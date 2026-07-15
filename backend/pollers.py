"""Background poll loops and the per-feed refreshers.

The two _poll_* loops run for the app's lifetime (started by main's lifespan);
each _refresh_* decodes one system into the shared feed cache. A refresher takes
the app and an httpx client, records last-known-on-failure via the cache
helpers, and never raises out of the loop.

Depends on main for the feed fetchers (fetch_subway_trains, fetch_service_alerts,
...): those are the names the tests monkeypatch on the main module, so the
refreshers resolve them through `main.` at call time to keep
`monkeypatch.setattr(main, "fetch_subway_trains", ...)` effective after the
split. The non-swappable feed helpers (carry_forward_prev, match_path_identities,
merge_alert_generations, the feed-URL sets) are imported straight from feeds.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import FastAPI
from google.protobuf.message import DecodeError

import main
from cache import _note_failure, _sanitize_upstream
from feeds import (
    ALERT_RETENTION_MAX_S,
    RAILROAD_FEED_URLS,
    SUBWAY_FEED_URLS,
    carry_forward_prev,
    match_path_identities,
    merge_alert_generations,
)

# Log through the "main" logger (not __name__) so records and main.py's logging
# config are unchanged by the split.
logger = logging.getLogger("main")

# The backend polls the MTA once and serves every browser client from this
# cache, so N clients never means N upstream fetches.
POLL_INTERVAL_S = 20

# Service alerts poll on their OWN slower loop: alerts change far more slowly than
# vehicle positions, and the subway alerts feed alone is ~400 KB, so re-pulling them
# all every 20s would be wasteful. A separate lifespan task on this cadence keeps
# the position poll lean and independent (an alert-feed outage never stalls it).
ALERT_POLL_INTERVAL_S = 60

# A whole-task deadline for ONE system's refresh, applied per-coroutine INSIDE the
# gather (see _poll_feeds). The httpx client timeout=30 bounds the gap between bytes,
# not the whole exchange, so a trickling upstream that dribbles a byte every few
# seconds can keep a single refresh alive indefinitely; and because the cycle awaits
# all five refreshers together, that one wedged refresh freezes every system's
# fetched_at with it. This is the hard ceiling on a single refresh: when it fires,
# the timeout surfaces as a TimeoutError that _bounded_refresh routes to the same
# _note_failure path every other failure takes (last-known data kept, the error
# recorded for /api/status and the R1 stale surfaces), while the other four
# refreshers finish the cycle normally.
#
# WHY 45s when the poll cadence is 20s: the deadline is deliberately generous
# relative to the cadence. The loop sleeps AFTER the cycle, so a slow-but-finishing
# refresh merely stretches the next tick a little (harmless); only a truly wedged
# refresh should ever be aborted. 45s exceeds any healthy fetch (a full subway
# multi-feed pull is low single-digit seconds) by a wide margin, so it never aborts
# a healthy-but-slow cycle during an upstream slowdown, while still guaranteeing
# every cycle is finite. This does NOT replace the httpx per-op timeout=30: that
# guards a stalled SOCKET (no bytes for 30s) and this guards a whole request that
# keeps trickling under that floor; they catch different failure shapes, so both stay.
REFRESH_DEADLINE_S = 45


async def _bounded_refresh(entry: dict, coro) -> None:
    """Run one refresh coroutine under the whole-task REFRESH_DEADLINE_S. A timeout is
    converted here into the same last-known-on-failure record every other failure
    takes, so the gather sees a NORMAL return for this system and the other systems
    still finish the cycle. Only TimeoutError is caught: an unexpected error still
    propagates to the loop's cycle-level handler exactly as before.

    The refreshers' only await is the upstream fetch, and everything after it (the
    entry.update, the feed_health and arrivals writes) is synchronous, so a deadline
    can only cancel the fetch, never a half-applied update: last-known state is left
    intact for _note_failure to preserve.

    Only the cache entry's error is recorded here; the per-system feed_health dict
    (a secondary /api/status signal) is deliberately left at its last value, because
    this generic wrapper does not know each system's health shape and the recorded
    504 is the authoritative failure indicator either way."""
    try:
        async with asyncio.timeout(REFRESH_DEADLINE_S):
            await coro
    except TimeoutError:
        _note_failure(
            entry,
            504,
            f"Upstream did not complete within the {REFRESH_DEADLINE_S}s refresh "
            "deadline; keeping last-known data.",
        )


async def _refresh_buses(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["buses"]
    try:
        data, feed_timestamp = await main.fetch_vehicle_positions(client)
    except RuntimeError as exc:
        # Missing/placeholder API key — a configuration problem, not a 500.
        _note_failure(entry, 503, str(exc))
        return
    except httpx.HTTPError as exc:
        _note_failure(entry, 502, f"Upstream MTA feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (CDN error page, maintenance HTML).
        _note_failure(entry, 502, "Upstream bus feed returned undecodable data")
        return
    entry.update(data=data, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)


async def _refresh_subways(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["subways"]
    stops = app.state.subway_stops
    if not stops:
        # Static GTFS not ready yet (still loading, or a failed attempt retrying in
        # the background). No restart needed: the warmup retries automatically.
        # log=False: this recurs every poll during warmup, so the only log is the
        # single transition warning from _set_static_status (no per-poll spam).
        _note_failure(
            entry,
            503,
            "Static subway GTFS is still loading; it will retry automatically. Try again shortly.",
            log=False,
        )
        return
    total_feeds = len(SUBWAY_FEED_URLS)
    try:
        trains, arrivals, feed_timestamp, failed_feeds = await main.fetch_subway_trains(
            stops, client
        )
    except RuntimeError as exc:
        # Every subway feed failed this poll.
        app.state.subway_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(SUBWAY_FEED_URLS),
        }
        _note_failure(entry, 502, _sanitize_upstream(exc))
        return
    except httpx.HTTPError as exc:
        app.state.subway_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(SUBWAY_FEED_URLS),
        }
        _note_failure(entry, 502, f"Upstream MTA feed error: {_sanitize_upstream(exc)}")
        return
    # Partial failures still return data, so without this a vanished line group
    # would leave no trace (the entry error is cleared below, and feed_timestamp
    # is the min over only the surviving feeds). Record which groups dropped so
    # /api/status can surface the partial outage.
    app.state.subway_feed_health = {
        "total": total_feeds,
        "ok": total_feeds - len(failed_feeds),
        "failed": failed_feeds,
    }
    # Carry each trip's previous-poll stop forward as its prev interpolation anchor
    # when the feed pruned the departed stop (mutates trains in place), then remember
    # this poll's positions for the next one.
    app.state.subway_positions = carry_forward_prev(
        trains, getattr(app.state, "subway_positions", {})
    )
    entry.update(data=trains, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.subway_arrivals = arrivals


async def _refresh_railroads(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["railroads"]
    total_feeds = len(RAILROAD_FEED_URLS)
    try:
        trains, arrivals_by_system, feed_timestamp, failed_feeds = await main.fetch_railroad_trains(
            client, getattr(app.state, "railroad_stops", {})
        )
    except RuntimeError as exc:
        # Every railroad feed failed this poll.
        app.state.railroad_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(RAILROAD_FEED_URLS),
        }
        _note_failure(entry, 502, _sanitize_upstream(exc))
        return
    except httpx.HTTPError as exc:
        app.state.railroad_feed_health = {
            "total": total_feeds,
            "ok": 0,
            "failed": sorted(RAILROAD_FEED_URLS),
        }
        _note_failure(entry, 502, f"Upstream MTA feed error: {_sanitize_upstream(exc)}")
        return
    # Partial failures still return data; record which systems dropped so
    # /api/status surfaces the partial outage (parallel to _refresh_subways).
    app.state.railroad_feed_health = {
        "total": total_feeds,
        "ok": total_feeds - len(failed_feeds),
        "failed": failed_feeds,
    }
    # Carry each placed train's prev station forward across polls (the feeds prune
    # the just-departed stop, so the decode leaves prev_* null), giving the gliding
    # increment a previous-station anchor. GPS trains have next_time None, so the
    # forward-bracket guard skips them and they never synthesize a prev. Keyed by
    # (system, trip_id) since LIRR and MNR trip_ids are independent; mutates the
    # placed trains in place, then the memory is remembered for the next poll.
    app.state.railroad_positions = carry_forward_prev(
        trains,
        getattr(app.state, "railroad_positions", {}),
        key=lambda t: (t["system"], t["trip_id"]),
    )
    # feed_timestamp comes from LIRR's header only (MNR's lagging shared clock is
    # excluded; see feeds.RAILROAD_FRESHNESS_SYSTEMS); a failed poll keeps the
    # last-known timestamp, same as the subway cache.
    entry.update(data=trains, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace only the systems that decoded this poll (arrivals_by_system omits a
    # transiently-failed system), so its last-known arrivals survive while the
    # others refresh. Same "a failed poll never blanks a working index" rule as
    # the subway arrivals, applied per system since the two are independent.
    railroad_arrivals = getattr(app.state, "railroad_arrivals", None) or {}
    railroad_arrivals.update(arrivals_by_system)
    app.state.railroad_arrivals = railroad_arrivals


async def _refresh_path(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the PATH trains + arrivals from the community bridge feed.

    Same cache contract as the other systems: a failed poll keeps the
    last-known trains AND arrivals (the error only surfaces to clients while
    the cache has never filled), and both are replaced only on a poll that
    decoded. Deliberately NO carry_forward_prev here: that anchor memory keys
    on trip ids, and PATH bridge trip ids do not survive an upstream refresh
    (see path_static's module docstring). Identity and anchors come from
    match_path_identities instead (13d), which each successful poll threads
    its state through; a failed poll leaves that state untouched too, since a
    failure is not a generation and must not expire identities.
    """
    entry = app.state.feed_cache["path"]
    stops = getattr(app.state, "path_stops", None)
    if not stops:
        # The 13a static group is not ready yet: neither placement nor
        # arrivals can resolve parent station ids. Same quiet warming path as
        # the subway refresher: log=False because this recurs every poll
        # during warmup and the single transition log belongs to
        # _set_static_status, not the 20s poll loop.
        _note_failure(
            entry,
            503,
            "Static PATH GTFS is still loading; it will retry automatically. Try again shortly.",
            log=False,
        )
        return
    try:
        trains, arrivals, feed_timestamp, unresolved = await main.fetch_path_trains(client, stops)
    except httpx.HTTPError as exc:
        app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
        _note_failure(entry, 502, f"Upstream PATH bridge feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (bridge error page, proxy HTML).
        app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
        _note_failure(entry, 502, "Upstream PATH bridge feed returned undecodable data")
        return
    # A nonzero unresolved count means the bridge referenced station ids the
    # static stops table lacks (a renumber, or a lagging 13a snapshot): those
    # trains are silently absent from the map, so the condition must be
    # operator-visible. Logged only when it APPEARS or CLEARS (comparing
    # against the previous poll's health, so a persistent drift never spams
    # the 20s loop, matching _set_static_status's transition-only rule) and
    # carried on path_feed_health so /api/status shows it while it lasts. A
    # failed poll in between resets the memory (its health dict has no count),
    # so the warning refires after an outage: acceptable, it is still news.
    was_drifting = bool((getattr(app.state, "path_feed_health", None) or {}).get("unresolved"))
    if bool(unresolved) != was_drifting:
        if unresolved:
            logger.warning(
                "PATH decode is dropping %d entities whose station ids are missing "
                "from the static stops table (bridge and static GTFS may disagree)",
                unresolved,
            )
        else:
            logger.info("PATH unknown-station drops cleared")
    app.state.path_feed_health = {"total": 1, "ok": 1, "failed": [], "unresolved": unresolved}
    # feed_timestamp is the bridge's write time; it advances even when the
    # content is a re-served identical generation, which is NORMAL for PATH
    # (the bridge regenerates faster than the upstream refreshes), so content
    # sameness across polls is never treated as staleness.
    # Thread the decode through the synthetic identity matcher: the served
    # trains carry a stable `id` (and anchors on an advance) instead of the
    # bridge's unstable trip hash, which never leaves the backend.
    served, app.state.path_identity = match_path_identities(
        app.state.path_identity,
        trains,
        getattr(app.state, "path_station_order", None) or {},
    )
    entry.update(data=served, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.path_arrivals = arrivals


async def _refresh_ferry(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the NYC Ferry boats + arrivals from the two realtime endpoints.

    Same cache contract as the other systems with ONE deliberate divergence,
    flagged for reviewers: an EMPTY successful poll REPLACES the boats. NYC Ferry
    stops running roughly 22:30-06:00 ET, and the feeds then return zero entities
    with fresh headers. That empty decode is VALID DATA (the boats went home), so
    it replaces the cache like any other successful poll and the map correctly
    empties; only a FAILED poll (HTTP or decode error, below) keeps the last-known
    boats via _note_failure. This is the standard success-replaces /
    failure-retains split, but it matters more here than for a rail system, where
    an empty feed would be unusual: for ferries an empty feed is the nightly norm
    and must never linger as stale daytime boats.

    ferry_static is a hard dependency: the decode joins each realtime trip_id
    through 14a's static trip -> route map, so the poll waits for that warmup, the
    same quiet warming path the PATH refresher takes while its static loads.
    """
    entry = app.state.feed_cache["ferry"]
    if getattr(app.state, "ferry_static_status", None) != "ready":
        # 14a static not ready: the trip -> route join cannot run. Same log=False
        # warming path as the PATH/subway refreshers (the single transition log
        # belongs to _set_static_status, not the 20s poll loop).
        _note_failure(
            entry,
            503,
            "Static NYC Ferry GTFS is still loading; it will retry automatically. "
            "Try again shortly.",
            log=False,
        )
        return
    try:
        boats, arrivals, feed_timestamp = await main.fetch_ferry_data(
            client, getattr(app.state, "ferry_static", {})
        )
    except httpx.HTTPError as exc:
        app.state.ferry_feed_health = {"total": 1, "ok": 0, "failed": ["ferry"]}
        _note_failure(entry, 502, f"Upstream NYC Ferry feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (CDN error page, maintenance HTML).
        app.state.ferry_feed_health = {"total": 1, "ok": 0, "failed": ["ferry"]}
        _note_failure(entry, 502, "Upstream NYC Ferry feed returned undecodable data")
        return
    app.state.ferry_feed_health = {"total": 1, "ok": 1, "failed": []}
    # feed_timestamp is the VehiclePositions header time (the boats' feed); a
    # failed poll keeps the last-known timestamp, same as the other caches. An
    # empty boats list REPLACES the cache here on purpose (see the docstring).
    entry.update(data=boats, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.ferry_arrivals = arrivals


async def _poll_feeds(app: FastAPI) -> None:
    """Refresh the feeds every POLL_INTERVAL_S for the app's lifetime.

    One shared client for the task's lifetime; per-feed errors are recorded
    in the cache, and anything unexpected is logged rather than allowed to
    kill the loop.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                # Each refresh is wrapped in its OWN deadline (see _bounded_refresh)
                # so a wedged upstream bounds only its system: a timeout becomes a
                # last-known-on-failure record on that one entry, and the other four
                # complete this cycle. gather keeps NO return_exceptions, so an
                # unexpected (non-timeout) error still fails the whole cycle into the
                # handler below, unchanged.
                cache = app.state.feed_cache
                await asyncio.gather(
                    _bounded_refresh(cache["buses"], _refresh_buses(app, client)),
                    _bounded_refresh(cache["subways"], _refresh_subways(app, client)),
                    _bounded_refresh(cache["railroads"], _refresh_railroads(app, client)),
                    _bounded_refresh(cache["path"], _refresh_path(app, client)),
                    _bounded_refresh(cache["ferry"], _refresh_ferry(app, client)),
                )
            except Exception:
                logger.exception("feed poll cycle failed unexpectedly")
            await asyncio.sleep(POLL_INTERVAL_S)


async def _refresh_alerts(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the active-alerts index. Same cache contract as the feeds: a failed
    poll keeps the last-known index and its fetched_at (the error is recorded but
    only surfaces to clients while the index has never filled), and the index is
    replaced only on a poll that decoded.

    A partial failure (some feeds down, not all) is still a SUCCESSFUL poll, but it
    no longer silently drops the down systems' alerts. It USED TO: fetch_service_alerts
    returns only the systems that decoded, so replacing the index wholesale deleted a
    down system's alerts while recording success, an asymmetry with the railroad
    arrivals that already retain per system. Now the poll carries the down systems'
    alerts forward through merge_alert_generations (bounded by an activity re-filter
    and a retention cap), and records per-system health so the partial outage is
    visible in /api/status even though the poll succeeds.

    The all-feeds-failed path is unchanged: fetch_service_alerts raises RuntimeError,
    the last-known index is kept, and the poll-level error is recorded. Per-system
    health is left as its last partial-poll value there; the poll-level 502 is the
    authoritative total-outage signal (there is no per-system detail to record,
    since the all-failed RuntimeError carries no per-feed breakdown)."""
    entry = app.state.alerts_cache
    try:
        alerts, suppressed, failed = await main.fetch_service_alerts(client)
    except RuntimeError as exc:
        # Every alert feed failed this poll; keep the last-known index. Unlike the
        # single-fetch refreshers (buses/subways), there is no httpx.HTTPError to catch
        # here: fetch_service_alerts gathers every feed with return_exceptions=True,
        # so a per-feed HTTP or decode error is captured inside it and only the
        # all-failed RuntimeError ever propagates.
        _note_failure(entry, 502, _sanitize_upstream(exc))
        return

    now = time.time()
    failed_set = set(failed)
    health = entry["health"]
    # Thread the prior retention clock through the pure merge so the cap measures
    # total time down, not time-since-this-poll.
    prev_retained_since = {
        system: h["retained_since"]
        for system, h in health.items()
        if h["retained_since"] is not None
    }
    merged, retained_since = merge_alert_generations(
        entry["alerts"], alerts, failed_set, prev_retained_since, now, ALERT_RETENTION_MAX_S
    )
    for system, h in health.items():
        if system in failed_set:
            # No per-system upstream string exists to sanitize: fetch_service_alerts'
            # fixed signature returns only the failed feed KEYS, not their errors, so
            # the marker is generic (and URL-free by construction). fresh_at is kept
            # so an operator can see how long ago the system last decoded.
            h["last_error"] = {"status": 502, "detail": "alert feed unavailable this poll"}
            h["retained_since"] = retained_since.get(system)
        else:
            h["fresh_at"] = now
            h["retained_since"] = None
            h["last_error"] = None
    entry.update(
        alerts=merged,
        fetched_at=now,
        error=None,
        active=len(merged),
        suppressed=suppressed,
    )


async def _poll_alerts(app: FastAPI) -> None:
    """Refresh the alerts index every ALERT_POLL_INTERVAL_S for the app's lifetime.

    A separate task from _poll_feeds (own client, slower cadence): alerts change
    slowly and the feeds are large, and keeping it independent means an alert-feed
    outage never delays a position poll. Anything unexpected is logged rather than
    allowed to kill the loop, matching _poll_feeds."""
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                # Same whole-task deadline as the feed refreshers (REFRESH_DEADLINE_S
                # < the 60s alerts cadence): a trickling alerts feed can no longer
                # wedge this loop forever, and a timeout keeps the last-known index
                # via the existing _note_failure path.
                await _bounded_refresh(app.state.alerts_cache, _refresh_alerts(app, client))
            except Exception:
                logger.exception("alert poll cycle failed unexpectedly")
            await asyncio.sleep(ALERT_POLL_INTERVAL_S)
