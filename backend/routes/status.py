"""Alerts, operational status, and the readiness probe."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

import bus_static
import static_data
from cache import FEED_STALE_AFTER_S, _feed_age
from models import AlertFeed, StatusResponse

router = APIRouter()


@router.get("/api/alerts", response_model=AlertFeed)
async def get_alerts(request: Request, response: Response) -> dict:
    """Active service alerts from the in-memory index: {fetched_at, served_at,
    alerts: [...]}, one entry per alert active now across the subway/bus/LIRR/MNR
    and NYC Ferry feeds.

    served_at is stamped here at response build (see THE THREE TIMESTAMPS in
    cache.py); the frontend tracks it to hedge the banner/popups when the alerts
    feed itself has gone stale, since the alerts poll swallows failures. no-store
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
        }
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Alerts cache is warming up; try again in a few seconds."
    )


@router.get("/api/status", response_model=StatusResponse)
async def get_status(request: Request, response: Response) -> dict:
    """Operational snapshot: per-feed cache freshness and last recorded error,
    bus route index state, static subway GTFS age, and each static group's warmup
    state (loading / ready / failed). No secrets, no filesystem paths. A top-level
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
        # PATH is the only static group that stays "failed" until a retry
        # succeeds (single system, so an empty load is a full failure, not a
        # lenient GPS-only degradation), so its warmup state must be visible in
        # the operational snapshot the way every other group's is.
        "path_static": getattr(app.state, "path_static_status", None),
        # Same single-system rationale as PATH: an empty ferry load is a full
        # failure, so the warmup state must be visible in the snapshot.
        "ferry_static": getattr(app.state, "ferry_static_status", None),
        "subway_feeds": getattr(app.state, "subway_feed_health", None),
        "railroad_feeds": getattr(app.state, "railroad_feed_health", None),
        "path_feeds": getattr(app.state, "path_feed_health", None),
        "ferry_feeds": getattr(app.state, "ferry_feed_health", None),
        "alerts": alerts,
    }


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
    rather than taking the probe down, matching its lenient per-system loading."""
    app = request.app
    reasons: list[str] = []
    now = time.time()
    cache = getattr(app.state, "feed_cache", {})
    # A feed is fresh if it has data AND neither (a) the upstream content was
    # stale at the last poll (feed_age; unknown is tolerated — having data beats
    # penalizing a missing timestamp) nor (b) the poll loop has stalled
    # (now - fetched_at). The poll-age term catches a stuck poller that keeps
    # serving frozen last-good data, which feed_age alone can't see. Both use
    # server-recorded times, so no clock skew. The `<` boundary matches the
    # frontend (helpers.js flags at age >= FEED_STALE_AFTER_S).
    fresh = []
    for name, entry in cache.items():
        if entry["data"] is None:
            continue
        feed_age = _feed_age(entry)
        upstream_ok = feed_age is None or feed_age < FEED_STALE_AFTER_S
        poll_ok = (now - entry["fetched_at"]) < FEED_STALE_AFTER_S
        if upstream_ok and poll_ok:
            fresh.append(name)
    if not fresh:
        reasons.append("no feed has fresh data")
    if bus_static.status() == "failed":
        reasons.append("bus route index failed to build")
    # A failed subway static load is degraded (symmetric with the bus index), but
    # it retries in the background, so this clears once a retry succeeds. "loading"
    # is not degraded (cold-start warmup). Railroad static is intentionally omitted
    # (its failure is a lenient GPS-only degradation, per the docstring).
    if getattr(app.state, "subway_static_status", None) == "failed":
        reasons.append("subway static GTFS failed to load")
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

    body: dict = {"status": "fail" if reasons else "pass"}
    if reasons:
        body["reasons"] = reasons
    return JSONResponse(body, status_code=503 if reasons else 200)
