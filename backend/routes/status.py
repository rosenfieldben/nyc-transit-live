"""Alerts, operational status, and the readiness probe."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

import bus_static
import njt_auth
import static_data
import static_shared
from cache import FEED_STALE_AFTER_S, _feed_age
from models import (
    HEALTH_BUS_INDEX_FAILED,
    HEALTH_FEED_CONTENT_STALE,
    HEALTH_GATING_CODES,
    HEALTH_NJT_MINT_QUOTA,
    HEALTH_NO_FEED_FRESH,
    HEALTH_SUBWAY_GROUPS_DOWN,
    HEALTH_SUBWAY_STATIC_FAILED,
    AlertFeed,
    HealthzResponse,
    StatusResponse,
)

router = APIRouter()


@router.get("/api/alerts", response_model=AlertFeed)
async def get_alerts(request: Request, response: Response) -> dict:
    """Active service alerts from the in-memory index: {fetched_at, served_at,
    alerts: [...]}, one entry per alert active now across the subway/bus/LIRR/MNR
    and NYC Ferry feeds.

    served_at is stamped here at response build (see THE THREE TIMESTAMPS in
    cache.py). THE FRESHNESS HEDGE KEYS ON fetched_at, NOT served_at: the frontend
    ages the banner/popup "alerts may be out of date" marker against fetched_at,
    because that only advances on a poll that decoded, while served_at is stamped per
    response and so is fresh by construction even when this index is one the poller
    could not refresh. The docstring used to say served_at here, which was the bug.
    Do not drop fetched_at from this payload; the client has no second request to get
    it from. no-store
    for the same reason as the live feeds: a cached copy would freeze served_at and
    lie about freshness. An index that decoded zero active alerts serves an empty
    list, NOT an error; a 503 surfaces only until the first successful poll fills
    the index (mirrors _serve_cached's warming path)."""
    entry = request.app.state.alerts_cache
    if entry["alerts"] is not None:
        response.headers["Cache-Control"] = "no-store"
        return {
            "fetched_at": entry["fetched_at"],
            "served_at": time.time(),
            "alerts": entry["alerts"],
            # C2: the per-system block, projected from the health map C1 made
            # truthful. It rides HERE rather than only on /api/status because the
            # client never fetches /api/status: without it a partial alerts outage
            # (one feed down, four healthy) is a successful poll that advances the
            # top-level fetched_at, so the rider-facing freshness marker could not
            # see it. fresh_at is this system's last decode, which is exactly the
            # per-system fetched_at the shared contract asks for.
            "systems": {
                system: {
                    "fetched_at": health["fresh_at"],
                    "ok": health["last_error"] is None,
                    "retained_since": health["retained_since"],
                }
                for system, health in (entry.get("health") or {}).items()
            },
        }
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Alerts cache is warming up; try again in a few seconds."
    )


@router.get("/api/status", response_model=StatusResponse)
async def get_status(request: Request, response: Response) -> dict:
    """Operational snapshot: per-feed cache freshness and last recorded error,
    bus route index state, static subway GTFS age, each static group's warmup
    state (loading / ready / failed), and each static ARCHIVE's download honesty
    (when it was last promoted, why the last download was rejected, how many have
    been rejected since). No secrets, no filesystem paths. A top-level
    served_at (this response's build time; see THE THREE TIMESTAMPS in cache.py) and
    no-store, matching the live feeds: status is a live operational read."""
    app = request.app
    response.headers["Cache-Control"] = "no-store"
    now = time.time()
    feeds = {}
    for name, entry in getattr(app.state, "feed_cache", {}).items():
        feed_age = _feed_age(entry)
        feeds[name] = {
            "fetched_at": entry["fetched_at"],
            "age_s": round(now - entry["fetched_at"], 1)
            if entry["fetched_at"] is not None
            else None,
            "feed_age_s": round(feed_age, 1) if feed_age is not None else None,
            "last_error": entry["error"],
        }
    static_gtfs = None
    try:
        mtime = static_data.SUBWAY_GTFS_ZIP.stat().st_mtime
        static_gtfs = {"mtime": mtime, "age_s": round(now - mtime, 1)}
    except OSError:
        pass  # not downloaded (yet); reported as null
    # Alert feed health: poll age, last recorded error, and the active vs held-back
    # planned counts. suppressed_planned is the not-yet-active work the last poll
    # excluded from the index, so an operator can see there is upcoming service work.
    alerts_entry = getattr(app.state, "alerts_cache", None)
    alerts = None
    if alerts_entry is not None:
        fetched_at = alerts_entry["fetched_at"]
        # Per-system health (14a-style visibility): `systems` exposes each alert
        # feed's last-decode time, whether its alerts are currently retained from a
        # down feed, and any current failure; `degraded_systems` is the sorted set
        # of systems failing right now, so a partial outage the poll-level fields
        # (which stay green on a partial failure) would hide is still surfaced.
        health = alerts_entry.get("health", {})
        alerts = {
            "fetched_at": fetched_at,
            "age_s": round(now - fetched_at, 1) if fetched_at is not None else None,
            "last_error": alerts_entry["error"],
            "active": alerts_entry["active"],
            "suppressed_planned": alerts_entry["suppressed"],
            "systems": health,
            "degraded_systems": sorted(
                system for system, h in health.items() if h["last_error"] is not None
            ),
        }
    return {
        # served_at = when this snapshot was built (this server's clock), so a
        # client can tell a live status read from a replayed cached one and can
        # skew-correct the ages below. The per-feed fetched_at/age_s/feed_age_s
        # remain server-derived (no browser clock involved).
        "served_at": now,
        "feeds": feeds,
        "bus_route_index": {
            "status": bus_static.status(),
            "partial": bus_static.is_partial(),
        },
        "static_subway_gtfs": static_gtfs,
        "subway_static": getattr(app.state, "subway_static_status", None),
        "railroad_static": getattr(app.state, "railroad_static_status", None),
        # PATH stays "failed" until a retry succeeds (single system, so an empty
        # load is a full failure, not a lenient GPS-only degradation), so its
        # warmup state must be visible in the operational snapshot the way every
        # other group's is. Railroad reaches that same failed-and-retrying state
        # only when EVERY system came back empty (R3); a partial load still
        # settles as ready, which is the lenient degradation described above.
        "path_static": getattr(app.state, "path_static_status", None),
        # Same single-system rationale as PATH: an empty ferry load is a full
        # failure, so the warmup state must be visible in the snapshot.
        "ferry_static": getattr(app.state, "ferry_static_status", None),
        # NJ Transit (15a), the only group with a FOURTH state. Besides loading /
        # ready / failed it can be "not-configured", which is what a deployment
        # without NJT_USERNAME and NJT_PASSWORD reports: no credentials means no
        # network attempt of any kind, so there is nothing failing and nothing
        # retrying. Publishing it distinctly is the whole point. An operator
        # reading "failed" would go looking for a broken upstream; one reading
        # "not-configured" knows the answer is a secret nobody set, and a
        # deployment that MEANT to run NJT can see at a glance that it is not.
        "njt_static": getattr(app.state, "njt_static_status", None),
        # Per-ARCHIVE download honesty (C5), beside the group states above rather
        # than inside them: a group state answers "can I serve this system", these
        # answer "how old is the archive I am serving it from, and why". Read
        # together they make the deliberate ready-but-stale state legible, the one
        # a loader enters when a fresh download fails validation and the cached
        # archive keeps serving past MAX_AGE_DAYS. The contract monitor needs none
        # of this: it watches the same publications from the upstream side, so the
        # two vantage points stay independent on purpose.
        "static_archives": static_shared.archive_status(),
        "subway_feeds": getattr(app.state, "subway_feed_health", None),
        "railroad_feeds": getattr(app.state, "railroad_feed_health", None),
        "path_feeds": getattr(app.state, "path_feed_health", None),
        "ferry_feeds": getattr(app.state, "ferry_feed_health", None),
        "alerts": alerts,
    }


# The prose each gating code contributes to `reasons`. Verbatim from before F1:
# these strings are what a deploy log has said for two years, and the codes exist
# so nothing has to parse them.
_HEALTH_REASONS = {
    HEALTH_NO_FEED_FRESH: "no feed has fresh data",
    HEALTH_BUS_INDEX_FAILED: "bus route index failed to build",
    HEALTH_SUBWAY_STATIC_FAILED: "subway static GTFS failed to load",
}


def _health_codes(
    *,
    cache: dict,
    bus_index_status: str,
    subway_static_status: str | None,
    subway_feed_health: dict | None,
    njt_mint_quota: bool,
    now: float,
) -> list[str]:
    """Every degraded classification true of this instance right now, as codes.

    Pure and injected so the bands are testable without a client or a clock, the
    same shape the contract monitor's checks use. Returns codes in a fixed order
    so two probes of an unchanged instance compare equal.

    A feed is fresh if it has data AND neither (a) the upstream content was stale
    at the last poll (feed_age; unknown is tolerated, having data beats penalizing
    a missing timestamp) nor (b) the poll loop has stalled (now - fetched_at).
    The poll-age term catches a stuck poller that keeps serving frozen last-good
    data, which feed_age alone cannot see. Both use server-recorded times, so no
    clock skew. The `<` boundary matches the frontend (helpers.js flags at
    age >= FEED_STALE_AFTER_S).
    """
    codes: list[str] = []
    fresh, content_stale = [], []
    for name, entry in cache.items():
        if entry["data"] is None:
            continue
        feed_age = _feed_age(entry)
        upstream_ok = feed_age is None or feed_age < FEED_STALE_AFTER_S
        if not upstream_ok:
            content_stale.append(name)
        poll_ok = (now - entry["fetched_at"]) < FEED_STALE_AFTER_S
        if upstream_ok and poll_ok:
            fresh.append(name)
    if not fresh:
        codes.append(HEALTH_NO_FEED_FRESH)
    if bus_index_status == "failed":
        codes.append(HEALTH_BUS_INDEX_FAILED)
    # A failed subway static load is degraded (symmetric with the bus index), but
    # it retries in the background, so this clears once a retry succeeds. "loading"
    # is not degraded (cold-start warmup). Railroad static is intentionally omitted
    # (its failure is a lenient GPS-only degradation, per the handler docstring).
    if subway_static_status == "failed":
        codes.append(HEALTH_SUBWAY_STATIC_FAILED)

    # NEW WITH F1, AND NOT A REASON TO 503. One endpoint serving content that is
    # lagging is a real degradation a human should see, and a terrible trigger for
    # a container restart: the upstream is what is late, and a fresh process would
    # be exactly as late. Note the granularity this reports at, because it is not
    # the obvious one: feed_cache is keyed per ENDPOINT (subways, railroads, path,
    # ferry, buses), so this names an endpoint and never a subway line group. One
    # frozen group still reaches here, because feeds/subway.py folds the eight
    # group headers with min() and hands the cache the OLDEST of them.
    if content_stale:
        codes.append(HEALTH_FEED_CONTENT_STALE)

    # ALSO NEW, ALSO NOT A REASON TO 503, and the one place a threshold had to be
    # chosen rather than reused. The app carries no notion of "most groups": the
    # contract monitor's own _evaluate_subway bands on all-vs-some, which is the
    # house precedent and is deliberately not what this does. All-vs-some is right
    # for the monitor, which is reading the eight upstreams directly and can call a
    # single dead group a WARN. It is wrong here, because this is the only signal
    # that survives to something which watches: below a majority the map still
    # draws most lines and a single flapping group every six hours is how a monitor
    # gets muted, while above it a rider sees a mostly empty map and the probe
    # still answers 200.
    if _most_subway_groups_down(subway_feed_health):
        codes.append(HEALTH_SUBWAY_GROUPS_DOWN)

    # THE BUDGET, NOT AN OUTAGE, and the only code here that describes something
    # about US rather than about an upstream. NJ Transit issues ten tokens per
    # account per Eastern day (njt_auth.DAILY_MINT_LIMIT, observed 2026-09-02) and
    # this instance shares that account with the contract monitor and with every
    # fixture pull. When the eleventh is refused, the NJ Transit layer goes dark
    # while NJ Transit itself is perfectly healthy, and every other signal the app
    # publishes says exactly what a real outage says: njt_static "failed", the njt
    # feed erroring, the archive's last_download_error set. This code is the one
    # place the difference is written down.
    #
    # NOT GATING, and see HEALTH_GATING_CODES for the reason, which is stronger
    # here than for any other non-gating code: a restart would spend another mint.
    #
    # Read off the token cache rather than app.state because that cache is what
    # every mint in this process goes through, so the answer cannot be stale in the
    # way a flag somebody remembered to set would be. It clears itself on the next
    # mint that answers anything else, which after Eastern midnight is the first
    # one the warmup's retry schedule makes.
    if njt_mint_quota:
        codes.append(HEALTH_NJT_MINT_QUOTA)
    return codes


def _most_subway_groups_down(health: dict | None) -> bool:
    """True when a strict majority of the subway feed groups failed their last poll.

    Typed defensively because this reads app.state, which is None before the first
    poll and could carry a partly-built shape during one; a health block that
    cannot be read is NOT reported as an outage, since "I do not know" and "most of
    the subway is down" are different answers and only one of them is alarming.
    """
    if not isinstance(health, dict):
        return False
    total, ok = health.get("total"), health.get("ok")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        return False
    if not isinstance(ok, int) or isinstance(ok, bool) or ok < 0:
        return False
    return (total - ok) * 2 > total


@router.get("/healthz", include_in_schema=False)
async def healthz(request: Request) -> JSONResponse:
    """Readiness probe for the platform (Railway points its healthcheck here).
    Unlike the always-200 /api/status snapshot, this returns 503 when the app
    can't serve fresh data.

    Lenient by design: ready as long as AT LEAST ONE feed has fresh data, so a
    misconfigured key (which only stops the bus feed) doesn't take down an
    otherwise-working subway map. Degraded when no feed is fresh, the bus route
    index build has failed, or the subway static load has failed (and is
    retrying). A still-LOADING static group or bus index is NOT degraded, so a
    cold-start deploy stays healthy through the warmup (within Railway's
    healthcheckTimeout) instead of flapping; the failed states, which retry,
    surface until a retry succeeds. Railroad static failure is deliberately NOT a
    reason: a system whose static did not load degrades to GPS-only (still useful)
    rather than taking the probe down, matching its lenient per-system loading.

    THE STATUS CODE AND THE CLASSIFICATION ARE TWO DIFFERENT ANSWERS since F1.
    `status`/`reasons`/503 mean what they always meant, "should traffic come
    here", because Railway restarts a container on a failing healthcheck and a
    lagging upstream is not something a fresh process fixes. `degraded` means "is
    this instance sick", is a superset of the gating reasons, and is what the
    contract monitor reads: before F1 the monitor probed only /api/status and
    could tell that production was dead but never that it was ill.

    ONE OF THE CODES IS NOT A SICKNESS AT ALL. `njt-mint-quota` says this instance
    spent the NJ Transit account's ten mints for the Eastern day, so that layer is
    dark until midnight without anything being broken. It is published here because
    every other surface reports it exactly as it reports a real NJ Transit outage,
    and telling the two apart is otherwise a matter of finding the right log line."""
    app = request.app
    now = time.time()
    codes = _health_codes(
        cache=getattr(app.state, "feed_cache", {}),
        bus_index_status=bus_static.status(),
        subway_static_status=getattr(app.state, "subway_static_status", None),
        subway_feed_health=getattr(app.state, "subway_feed_health", None),
        njt_mint_quota=njt_auth.TOKEN_CACHE.mint_quota_refused,
        now=now,
    )
    reasons = [_HEALTH_REASONS[code] for code in codes if code in HEALTH_GATING_CODES]
    # The service-alerts feed is deliberately NOT a health input. Alerts are a
    # decorative overlay (like railroad static): an alert-feed outage degrades only
    # the alerts layer and must not fail the readiness probe that gates the whole
    # app, so alerts_cache is not consulted here.
    # The PATH bridge feed, by contrast, IS a health input: it rides feed_cache
    # like the MTA feeds, so a fresh PATH poll counts toward the "at least one
    # fresh feed" test above. That is intentional (PATH trains are a real served
    # layer, not a decorative overlay), with one caveat worth knowing: the bridge
    # is an unofficial community service, so under a total MTA-upstream outage a
    # still-fresh PATH bridge alone keeps the probe green. That is acceptable
    # here (the app genuinely can serve PATH data, and a total MTA outage 503s
    # every instance identically, so there is no healthier instance to fail over
    # to); per-feed detail stays visible in /api/status regardless.

    body = HealthzResponse(
        status="fail" if reasons else "pass", reasons=reasons, degraded=codes
    ).model_dump()
    # `reasons` omitted when empty, byte-identical to the pre-F1 healthy body.
    # `degraded` is NOT omitted when empty: a watcher has to be able to tell "this
    # deployment classified itself and found nothing" from "this deployment is too
    # old to classify itself", and an absent key is the only way the second one can
    # announce itself.
    if not reasons:
        body.pop("reasons")
    return JSONResponse(body, status_code=503 if reasons else 200)
