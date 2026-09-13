"""Alerts, operational status, and the readiness probe."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import NamedTuple

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

import bus_static
import njt_auth
import static_data
import static_shared
from cache import FEED_RETENTION_MAX_S, FEED_STALE_AFTER_S, _feed_age
from feeds import RAILROAD_FRESHNESS_SYSTEMS, iter_rows
from models import (
    HEALTH_BUS_INDEX_FAILED,
    HEALTH_FEED_CONTENT_STALE,
    HEALTH_GATING_CODES,
    HEALTH_NJT_MINT_QUOTA,
    HEALTH_NO_FEED_FRESH,
    HEALTH_OBSERVATIONS_QUALIFIED,
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
        health_map = entry.get("health") or {}
        # THE ENVELOPE'S CONTENT CLOCK, by the same union discipline the arrivals
        # boards use: the oldest content time among the systems actually contributing,
        # and None if any of them cannot be dated rather than letting the others speak
        # for it. Equal to the oldest served alert's own observed_at, which is what
        # test_alerts_envelope_content_clock_equals_the_alerts_observed_at pins.
        contributing = {(alert.get("system") or "") for alert in (entry["alerts"] or [])} & set(
            health_map
        )
        clocks = [health_map[system].get("content_at") for system in sorted(contributing)]
        envelope_content_at = None if not clocks or any(c is None for c in clocks) else min(clocks)
        return {
            "fetched_at": entry["fetched_at"],
            "feed_timestamp": envelope_content_at,
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
                    "feed_timestamp": health.get("content_at"),
                    "ok": health["last_error"] is None,
                    "retained_since": health["retained_since"],
                }
                for system, health in health_map.items()
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
        # down feed, any current failure AND WHY, and the one success worth a
        # sentence (`served_empty`, NJ Transit's zero-byte 200 meaning no active
        # alerts); `degraded_systems` is the sorted set of systems failing right
        # now, so a partial outage the poll-level fields (which stay green on a
        # partial failure) would hide is still surfaced. A served-empty system is
        # NOT degraded: it decoded, so it is absent from that list by construction.
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
        # NJ TRANSIT MINTING, beside the group state rather than inside it (Audit 5,
        # F05). The group state answers "can I serve NJ Transit"; this answers "why
        # am I not asking NJ Transit for a token right now", which since F05 is a
        # state the app can be in for half an hour at a time with no request going
        # out at all. Null whenever a mint may be attempted, so an operator reads
        # this key only when there is something to read.
        #
        # IT IS THE ONLY SURFACE THAT COVERS A COLD START. In a running process the
        # cooldown also reaches the njt feed's last_error through the poller's arm,
        # but during a cold start the feed poller is gated off behind a static group
        # that is still loading, so a cooldown holding the warmup back would appear
        # nowhere. Read off the token cache for the reason /healthz reads the quota
        # flag off it: that cache is what every mint in this process goes through,
        # so the answer cannot be stale the way a flag somebody remembered to set
        # could be.
        "njt_mint_cooldown": _njt_mint_cooldown(),
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


def _njt_mint_cooldown() -> dict | None:
    """The NJ Transit mint cooldown for the snapshot, or None when there is none.

    `seconds_remaining` is the machine half and `detail` the human one, and both
    come from a single read of the cache so the number and the sentence cannot
    disagree. The detail names the failure that started the window, which is
    already free of any getToken body by construction (Audit 4, F3).
    """
    running = njt_auth.TOKEN_CACHE.cooldown()
    if running is None:
        return None
    remaining, detail = running
    return {"seconds_remaining": round(remaining, 1), "detail": detail}


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
    observations_qualified: bool = False,
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
    # lagging is a real degradation a human should see, and no reason at all to
    # refuse a build: the status code is read only while a deployment is being
    # promoted (see models.HEALTH_GATING_CODES), and the upstream being late is a
    # property of the world rather than of the code being promoted. Gating on it
    # would block a good deploy for something no deploy can fix, and would keep
    # blocking for as long as the upstream lagged. A restart is not the alternative
    # being weighed here: the platform does not restart a live container on this
    # probe at all. Note the granularity this reports at, because it is not
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
    # here than for any other non-gating code because gating has a PRICE rather than
    # merely no benefit: a 503 refuses the promotion, a refused promotion is retried,
    # and each fresh process mints on its first NJ Transit request. The probe would
    # spend the budget it exists to report.
    #
    # Read off the token cache rather than app.state because that cache is what
    # every mint in this process goes through, so the answer cannot be stale in the
    # way a flag somebody remembered to set would be. It clears itself on the next
    # mint that answers anything else, which after Eastern midnight is the first
    # one the warmup's retry schedule makes.
    if njt_mint_quota:
        codes.append(HEALTH_NJT_MINT_QUOTA)

    # CONTRACT 6.2, AND ALSO NOT A REASON TO 503. The only code read off what riders
    # are SERVED rather than off a feed, a warmup or a budget: some system is serving
    # nothing current. The rule and its measured threshold live at
    # models.HEALTH_OBSERVATIONS_QUALIFIED; the handler computes it off the arrivals
    # indexes with _systems_serving_nothing_current below and hands in the answer, so
    # this function stays pure over its inputs like every code above.
    #
    # LAST, so every ordered list this probe published before it keeps its order. And
    # DEFAULTED, so a caller that predates it keeps working: the F10 reproduction
    # (docs/reviews/audit-2026-09-05/f10_deadline_health_disagreement.py) calls this
    # with exactly the six keywords above. The handler always passes it.
    if observations_qualified:
        codes.append(HEALTH_OBSERVATIONS_QUALIFIED)
    return codes


class _ServedSystem(NamedTuple):
    """One system's served arrival rows, as _systems_serving_nothing_current reads them.

    `rows` is whatever nesting that system's index uses (a flat list per stop for NJ
    Transit, {stop: {bucket: [row]}} for the rest), walked with feeds.iter_rows so this
    rule and the retention stamp cannot disagree about what a row is. None, as opposed
    to an empty container, means the system has NO ENTRY in its index at all, which is
    one of the facts _was_dropped turns on. `block` is the system's per-system
    freshness block, and only the systems whose data is RETAINED pass one: the subway
    groups and the railroads (see _served_arrival_systems).
    """

    rows: object
    age_gated: bool
    block: Mapping | None = None


def _row_is_qualified(row: Mapping, age_gated: bool, now: float) -> bool:
    """Whether a rider has to be told something about this row before trusting it.

    The design's 3.2 rule applied to one served row, in three clauses: its provenance
    is not "reported" (retained, unknown, absent, anything else); or its system dates
    its rows and this one is undated; or it is FEED_STALE_AFTER_S or more old. The `>=`
    matches the frontend, which flags at age >= FEED_STALE_AFTER_S, and the mirror of
    the `<` that makes a feed fresh in _health_codes.
    """
    if row.get("provenance") != "reported":
        return True
    observed_at = row.get("observed_at")
    if observed_at is None:
        return age_gated
    return now - observed_at >= FEED_STALE_AFTER_S


def _was_dropped(system: _ServedSystem, now: float) -> bool:
    """The ladder's last rung: failing past the retention cap, with nothing left to serve.

    DECIDED BY HOW LONG THE SYSTEM HAS BEEN FAILING, NEVER BY WHETHER ITS RETENTION
    CLOCK IS SET, because that clock does not stay stopped once the cap fires. The poll
    that passes the cap drops the rows and writes retained_since None. On the next
    failed poll pollers._merge_feed_systems rebuilds the previous clocks only from
    blocks whose retained_since is set, so feeds.merge_system_generations opens a NEW
    window at `now`, with nothing left to carry. A rule that read "retained_since None"
    as "the cap fired" was therefore true on the cap poll alone, then quiet until the
    new window capped in turn. Measured on that first version: ACE failing for 45
    minutes at a 22 s poll under the 600 s cap published the code on 3 of the 94 polls
    after the first cap poll. fetched_at is the clock that holds still for the whole
    outage (a failing system keeps its last decode time), so the rule ages that.

    FOUR FACTS, ALL REQUIRED:
      - ok is False: failing now. A healthy system serving nothing has nothing running.
      - fetched_at is set: it has decoded in this process. One that never has took
        nothing from anyone. That includes a group already failing when this process
        started, which the contract monitor's note for this code says out loud.
      - NO ENTRY in its index (rows None, not an empty container). A TOTAL outage
        returns before any merge runs: _mark_all_systems_failed flips every ok and the
        index keeps every entry, an empty one included, so an empty entry means nothing
        was taken away. The F10 reproduction
        (docs/reviews/audit-2026-09-05/f10_deadline_health_disagreement.py) drives
        exactly that over eight groups, and a version that read the block alone reported
        all eight as dropped. test_a_total_outage_leaves_an_empty_entry_undropped pins
        both sides.
      - now - fetched_at >= FEED_RETENTION_MAX_S: failing at least as long as the cap,
        the same env-seam value the merge reads and the same `>=`. A system failing
        inside its window with nothing to carry (it went down holding no rows) also has
        no entry, and this clause is what keeps it quiet.

    MEASURED FROM THE LAST DECODE, while the merge's window counts from the first failed
    poll, so the two edges sit one poll interval apart: a system that went down holding
    nothing can be named up to one interval before its window closes. A system still
    carrying rows never reaches this function, because its rows decide it.
    test_healthz_names_a_dropped_group_on_every_poll_of_its_outage in
    backend/tests/test_api.py drives the real refresher through the cap and 94 polls
    past it.
    """
    block = system.block
    if block is None or system.rows is not None:
        return False
    fetched_at = block.get("fetched_at")
    return (
        block.get("ok") is False
        and fetched_at is not None
        and now - fetched_at >= FEED_RETENTION_MAX_S
    )


def _systems_serving_nothing_current(systems: Mapping[str, _ServedSystem], now: float) -> list[str]:
    """The systems serving riders nothing current, sorted: the observations-qualified rule.

    A system counts when it serves at least one row and EVERY one of them is qualified
    (_row_is_qualified), or when it serves none because the retention cap dropped them
    (_was_dropped). One current row keeps a whole system quiet, and that threshold is
    measured rather than chosen: models.HEALTH_OBSERVATIONS_QUALIFIED has the LIRR
    numbers that rule out a version where any one row would do.

    A system serving no rows that was NOT dropped is quiet: a group with nothing running
    right now, one that has never decoded, one failing inside its retention window with
    nothing to carry, and one a total outage caught holding an empty entry all serve
    nothing without anything having been taken away. The window case becomes the
    dropped rung once the system has been failing for FEED_RETENTION_MAX_S, and it
    stays there on every poll until the system decodes again (_was_dropped says why that
    needed saying).

    Pure and clock-injected, like _health_codes; the healthz handler builds `systems`
    off app.state with _served_arrival_systems.
    """
    serving_nothing = []
    for name in sorted(systems):
        system = systems[name]
        rows = list(iter_rows(system.rows))
        if rows:
            if all(_row_is_qualified(row, system.age_gated, now) for row in rows):
                serving_nothing.append(name)
        elif _was_dropped(system, now):
            serving_nothing.append(name)
    return serving_nothing


def _served_arrival_systems(state: object) -> dict[str, _ServedSystem]:
    """Every arrivals index this instance serves, one entry per system, off app.state.

    Named by kind so a subway group and a railroad can never collide: "subway:ACE",
    "railroad:LIRR", "path", "ferry", "njt". ARRIVAL ROWS ONLY: vehicle positions get
    their own age gate in contract 6.3, and alerts are governed by retention rather than
    by age (design 3.3), so neither is read here.

    THE SUBWAY IS READ PER GROUP, from subway_arrivals_by_system, and not from the
    combined index a board is built from, because the combined index no longer says
    which group a row came from: a subway row names a route, not a feed group, and
    combine_group_arrivals has already deduplicated and trimmed across groups.

    EVERY SYSTEM IS AGE-GATED BUT A RAILROAD OUTSIDE feeds.RAILROAD_FRESHNESS_SYSTEMS,
    and the set is read rather than restated. It is the one place the railroad whose
    header is not a usable clock is named (design 4.2), and that railroad's rows carry
    no observed_at at all, so gating them would qualify a whole railroad forever.

    ONLY THE SUBWAY GROUPS AND THE RAILROADS PASS A BLOCK, because only they retain,
    and only their indexes are keyed by system, which is what lets an ABSENT entry mean
    the merge removed it (_was_dropped). NJ Transit's envelope carries a block too, but
    a failed NJ Transit poll keeps its last-known rows and never retains, so ok False
    with retained_since None there is one failed poll rather than a cap that fired, and
    its index is one flat map with no per-system entry that could go missing.

    Typed defensively, because this reads app.state, whose indexes are None or absent
    before the first poll.
    """
    cache = getattr(state, "feed_cache", None) or {}
    systems: dict[str, _ServedSystem] = {}
    subway_rows = getattr(state, "subway_arrivals_by_system", None) or {}
    subway_blocks = (cache.get("subways") or {}).get("systems") or {}
    for group in set(subway_rows) | set(subway_blocks):
        systems[f"subway:{group}"] = _ServedSystem(
            subway_rows.get(group), True, subway_blocks.get(group)
        )
    railroad_rows = getattr(state, "railroad_arrivals", None) or {}
    railroad_blocks = (cache.get("railroads") or {}).get("systems") or {}
    for system in set(railroad_rows) | set(railroad_blocks):
        systems[f"railroad:{system}"] = _ServedSystem(
            railroad_rows.get(system),
            system in RAILROAD_FRESHNESS_SYSTEMS,
            railroad_blocks.get(system),
        )
    for name, index in (
        ("path", "path_arrivals"),
        ("ferry", "ferry_arrivals"),
        ("njt", "njt_arrivals"),
    ):
        systems[name] = _ServedSystem(getattr(state, index, None), True)
    return systems


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
    here", and that question is asked exactly once: Railway reads this probe while
    a deployment is being promoted and stops reading it once the deployment is live
    (models.HEALTH_GATING_CODES carries the citation; a live container is restarted
    on a process exit under the separate restart policy, never on this probe). So
    the status code answers "may this build go live", and a lagging upstream is not
    a reason to refuse one. `degraded` means "is this instance sick", is a superset
    of the gating reasons, and is what the contract monitor reads: it is the only
    channel that says anything about a deployment that is ALREADY live, which is
    why the superset exists. Before F1 the monitor probed only /api/status and
    could tell that production was dead but never that it was ill.

    ONE OF THE CODES IS NOT A SICKNESS AT ALL. `njt-mint-quota` says this instance
    spent the NJ Transit account's ten mints for the Eastern day, so that layer is
    dark until midnight without anything being broken. It is published here because
    every other surface reports it exactly as it reports a real NJ Transit outage,
    and telling the two apart is otherwise a matter of finding the right log line.

    ONE OF THE CODES IS READ OFF WHAT RIDERS ARE SERVED. `observations-qualified` is
    computed from the arrivals indexes themselves (_served_arrival_systems), not from a
    feed header or a warmup state, and says some system is serving riders nothing
    current. models.HEALTH_OBSERVATIONS_QUALIFIED carries the rule, why
    feed-content-stale is not the same code, and why the threshold is a whole system
    rather than a single row."""
    app = request.app
    now = time.time()
    codes = _health_codes(
        cache=getattr(app.state, "feed_cache", {}),
        bus_index_status=bus_static.status(),
        subway_static_status=getattr(app.state, "subway_static_status", None),
        subway_feed_health=getattr(app.state, "subway_feed_health", None),
        njt_mint_quota=njt_auth.TOKEN_CACHE.mint_quota_refused,
        now=now,
        observations_qualified=bool(
            _systems_serving_nothing_current(_served_arrival_systems(app.state), now)
        ),
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
