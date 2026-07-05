"""FastAPI app exposing decoded MTA realtime data (buses + subways + commuter
rail / railroads) as JSON."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from google.protobuf.message import DecodeError

import airtrain_static
import bus_static
import path_static
import railroad_static
import static_data
from feeds import (
    RAILROAD_FEED_URLS,
    SUBWAY_FEED_URLS,
    carry_forward_prev,
    fetch_path_trains,
    fetch_railroad_trains,
    fetch_service_alerts,
    fetch_subway_trains,
    fetch_vehicle_positions,
)
from models import (
    AirTrainData,
    AlertFeed,
    BusFeed,
    PathFeed,
    PathRoute,
    PathStationArrivals,
    PathStop,
    RailroadFeed,
    RailroadRoute,
    RailroadStationArrivals,
    RailroadStop,
    RouteGeometry,
    StationArrivals,
    StatusResponse,
    SubwayFeed,
    SubwayRoute,
    SubwayStop,
)
from static_data import (
    load_subway_route_shapes,
    load_subway_stations,
    load_subway_stops,
)

logger = logging.getLogger(__name__)

# Uvicorn configures its own loggers but leaves the root logger bare, so
# module loggers (feeds, bus_static, static_data) would be invisible. Give
# root a handler if nothing else has; keep root at WARNING so third-party
# INFO noise (e.g. httpx per-request lines) stays out, and opt our modules in.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
for _mod in (__name__, "feeds", "bus_static", "static_data"):
    logging.getLogger(_mod).setLevel(logging.INFO)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# The backend polls the MTA once and serves every browser client from this
# cache, so N clients never means N upstream fetches.
POLL_INTERVAL_S = 20

# Service alerts poll on their OWN slower loop: alerts change far more slowly than
# vehicle positions, and the subway alerts feed alone is ~400 KB, so re-pulling all
# four every 20s would be wasteful. A separate lifespan task on this cadence keeps
# the position poll lean and independent (an alert-feed outage never stalls it).
ALERT_POLL_INTERVAL_S = 60

# Upstream-staleness threshold: how far the feed's CONTENT time (MTA's clock)
# may lag the poll time (this server's clock) before the data is considered
# stale — used by /healthz and reported via /api/status. Computed from two
# server-captured timestamps (fetched_at - feed_timestamp), so the browser
# clock is never involved; the frontend mirrors this in helpers.js.
FEED_STALE_AFTER_S = 90


def _feed_age(entry: dict) -> float | None:
    """Seconds the feed content lagged the poll, or None if not computable.
    Both inputs are server-captured at poll time, so this is clock-skew free."""
    if entry["fetched_at"] is None or entry["feed_timestamp"] is None:
        return None
    return entry["fetched_at"] - entry["feed_timestamp"]


# Station ids index the in-memory arrivals dict; validate the path parameter
# to reject malformed input (and any traversal-shaped surprises) up front.
_STATION_ID_RE = re.compile(r"^[A-Za-z0-9]{1,6}$")

# Railroad stop_ids are purely numeric in both fixtures (LIRR and MNR are each
# 1 to 3 digit opaque ids, e.g. "1", "12", "237"); allow up to 4 digits for
# headroom. This is only a cheap malformed-input pre-filter: membership in the
# system's static stops (belt-and-suspenders, like _STATION_ID_RE) is the real
# gate. The two systems' namespaces are independent, so the endpoint is keyed by
# (system, stop_id).
_RAILROAD_STATION_ID_RE = re.compile(r"^[0-9]{1,4}$")
_RAILROAD_SYSTEMS = frozenset({"LIRR", "MNR"})

# PATH parent station ids are 5-digit numerics (26733 Newark, 26734 WTC);
# allow up to 6 digits for headroom. Like the other station-id regexes this is
# only a cheap malformed-input pre-filter: membership in app.state.path_stops
# is the real gate. PATH ids live in their own namespace (they collide
# numerically with MTA ids), so this never mixes with the MTA regexes.
_PATH_STATION_ID_RE = re.compile(r"^[0-9]{1,6}$")


def _fresh_entry() -> dict:
    # fetched_at = this server's poll time; feed_timestamp = the feed's content
    # time (MTA's clock). Both are stored so freshness can be judged without the
    # browser clock — see _feed_age and FEED_STALE_AFTER_S.
    return {"data": None, "fetched_at": None, "feed_timestamp": None, "error": None}


def _fresh_alerts_entry() -> dict:
    # alerts = the active-alert index (None until the first successful poll, [] once
    # a poll decoded zero active alerts); active/suppressed are the counts /api/status
    # reports. Same last-known-on-failure rule as the feed cache: a failed poll keeps
    # the last index and its fetched_at, replacing them only on a poll that decoded.
    return {"alerts": None, "fetched_at": None, "error": None, "active": 0, "suppressed": 0}


def _note_failure(entry: dict, status: int, detail: str, log: bool = True) -> None:
    """Record why the latest poll failed. Last-known data keeps being served;
    the error only surfaces to clients while the cache has never been filled.
    log=False suppresses the warning for an EXPECTED, recurring condition (the
    subway warming path notes a 503 every poll while static loads, but the single
    transition warning belongs to _set_static_status, not every 20s poll)."""
    entry["error"] = {"status": status, "detail": detail}
    if log:
        logger.warning("feed poll failed (%d): %s", status, detail)


_URL_RE = re.compile(r"https?://\S+")


def _sanitize_upstream(exc: BaseException) -> str:
    """Strip URLs from upstream error text before recording it: httpx error
    strings embed the full request URL, which for the bus feed includes the
    API key query parameter, and recorded details are served by /api/status
    and the never-filled error paths."""
    return _URL_RE.sub("<feed url>", str(exc))


async def _refresh_buses(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["buses"]
    try:
        data, feed_timestamp = await fetch_vehicle_positions(client)
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
        trains, arrivals, feed_timestamp, failed_feeds = await fetch_subway_trains(stops, client)
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
        trains, arrivals_by_system, feed_timestamp, failed_feeds = await fetch_railroad_trains(
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
    decoded. Deliberately NO carry_forward_prev here: the anchor memory keys
    on trip ids, and PATH bridge trip ids do not survive an upstream refresh
    (see path_static's module docstring), so every poll decodes independently
    and prev_* stays null until 13d introduces a synthetic identity.
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
        trains, arrivals, feed_timestamp = await fetch_path_trains(client, stops)
    except httpx.HTTPError as exc:
        app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
        _note_failure(entry, 502, f"Upstream PATH bridge feed error: {_sanitize_upstream(exc)}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (bridge error page, proxy HTML).
        app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
        _note_failure(entry, 502, "Upstream PATH bridge feed returned undecodable data")
        return
    app.state.path_feed_health = {"total": 1, "ok": 1, "failed": []}
    # feed_timestamp is the bridge's write time; it advances even when the
    # content is a re-served identical generation, which is NORMAL for PATH
    # (the bridge regenerates faster than the upstream refreshes), so content
    # sameness across polls is never treated as staleness.
    entry.update(data=trains, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)
    # Replace the arrivals index only on success, so a failed poll keeps the
    # last-known arrivals on the same fetched_at, consistent with the cache.
    app.state.path_arrivals = arrivals


async def _poll_feeds(app: FastAPI) -> None:
    """Refresh the feeds every POLL_INTERVAL_S for the app's lifetime.

    One shared client for the task's lifetime; per-feed errors are recorded
    in the cache, and anything unexpected is logged rather than allowed to
    kill the loop.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                await asyncio.gather(
                    _refresh_buses(app, client),
                    _refresh_subways(app, client),
                    _refresh_railroads(app, client),
                    _refresh_path(app, client),
                )
            except Exception:
                logger.exception("feed poll cycle failed unexpectedly")
            await asyncio.sleep(POLL_INTERVAL_S)


async def _refresh_alerts(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Refresh the active-alerts index. Same cache contract as the feeds: a failed
    poll keeps the last-known index and its fetched_at (the error is recorded but
    only surfaces to clients while the index has never filled), and the index is
    replaced only on a poll that decoded. A partial failure (some feeds down, not
    all) is a SUCCESS: fetch_service_alerts already dropped the failed systems'
    alerts and returned, so the poll succeeds and the error clears."""
    entry = app.state.alerts_cache
    try:
        alerts, suppressed, _failed = await fetch_service_alerts(client)
    except RuntimeError as exc:
        # Every alert feed failed this poll; keep the last-known index. Unlike the
        # single-fetch refreshers (buses/subways), there is no httpx.HTTPError to catch
        # here: fetch_service_alerts gathers the four feeds with return_exceptions=True,
        # so a per-feed HTTP or decode error is captured inside it and only the
        # all-failed RuntimeError ever propagates.
        _note_failure(entry, 502, _sanitize_upstream(exc))
        return
    entry.update(
        alerts=alerts,
        fetched_at=time.time(),
        error=None,
        active=len(alerts),
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
                await _refresh_alerts(app, client)
            except Exception:
                logger.exception("alert poll cycle failed unexpectedly")
            await asyncio.sleep(ALERT_POLL_INTERVAL_S)


# Static GTFS loads in the background, off the startup critical path (the durable
# version of the old _DOWNLOAD_DEADLINE_S stopgap). Each group runs its own warmup
# task with a "loading" -> "ready" | "failed" state machine: on failure it sleeps
# STATIC_RETRY_S and retries until it succeeds or the app shuts down, so a degraded
# network at boot self-heals instead of stranding the map until the next deploy.
STATIC_RETRY_S = 300  # module-level so tests can shorten it


def _set_static_status(
    app: FastAPI, field: str, status: str, exc: BaseException | None = None
) -> None:
    """Set a static group's status, logging only on a TRANSITION so a long retry
    loop (or the per-poll checks that read this) never spams the log. `field` is
    one of "subway_static_status", "railroad_static_status", or
    "path_static_status"."""
    if getattr(app.state, field, None) == status:
        return
    setattr(app.state, field, status)
    if status == "failed":
        logger.warning("%s failed to load (%s); retrying in %ds", field, exc, STATIC_RETRY_S)
    elif status == "ready":
        logger.info("%s ready", field)


async def _warm_subway_static(app: FastAPI) -> None:
    """Load the subway static GTFS (stops, route lines, station markers) in the
    background, retrying every STATIC_RETRY_S until it succeeds. Fills the same
    app.state fields the handlers read, then flips the group to ready. A failed
    attempt leaves stops None, so the poller keeps noting a warming 503 and
    /healthz reports the failure until a retry succeeds."""
    while True:
        try:
            stops = await load_subway_stops()
            routes = load_subway_route_shapes()  # reuse the zip the stops load ensured
            stations = load_subway_stations()
        except Exception as exc:
            _set_static_status(app, "subway_static_status", "failed", exc)
            await asyncio.sleep(STATIC_RETRY_S)
            continue
        app.state.subway_stops = stops
        app.state.subway_routes = routes
        app.state.subway_stations = stations
        _set_static_status(app, "subway_static_status", "ready")
        return


async def _warm_railroad_static(app: FastAPI) -> None:
    """Load the railroad static GTFS in the background, retrying every
    STATIC_RETRY_S. load_railroad_static is lenient per system (a download or
    parse failure for one system yields None for it, GPS-only, without raising),
    so the group reaches ready once the attempt COMPLETES even if a system is
    None; only an unexpected raise (never per-system None) drives the failed and
    retry path. Fills the derived stops and route geometry the handlers read."""
    while True:
        try:
            data = await railroad_static.load_railroad_static()
        except Exception as exc:
            _set_static_status(app, "railroad_static_status", "failed", exc)
            await asyncio.sleep(STATIC_RETRY_S)
            continue
        app.state.railroad_static = data
        # Derived so the placement path (_refresh_railroads) reads stops unchanged;
        # a None system stays None (GPS-only), never a crash.
        app.state.railroad_stops = {
            system: (d["stops"] if d else None) for system, d in data.items()
        }
        app.state.railroad_routes = {
            system: (
                railroad_static.build_railroad_route_shapes(d["trips"], d["shapes"], d["routes"])
                if d
                else []
            )
            for system, d in data.items()
        }
        _set_static_status(app, "railroad_static_status", "ready")
        return


async def _warm_path_static(app: FastAPI) -> None:
    """Load the PATH static GTFS in the background, retrying every
    STATIC_RETRY_S. load_path_static is lenient (a download or parse failure
    yields {} without raising), and PATH is a SINGLE system: unlike the
    railroad group, which reaches ready with one system None because the other
    still deserves serving, an empty PATH result means the only system failed,
    so the group stays failed and retries. That keeps the endpoint contract
    honest: while failed they serve [] under no-cache ("ask again later"), and
    ready-with-cache-headers is only ever reached with real data."""
    while True:
        try:
            data = await path_static.load_path_static()
        except Exception as exc:
            _set_static_status(app, "path_static_status", "failed", exc)
            await asyncio.sleep(STATIC_RETRY_S)
            continue
        if not data.get("stops"):
            _set_static_status(
                app,
                "path_static_status",
                "failed",
                RuntimeError("PATH static GTFS unavailable or empty"),
            )
            await asyncio.sleep(STATIC_RETRY_S)
            continue
        app.state.path_static = data
        app.state.path_stops = data["stops"]
        app.state.path_routes = path_static.build_path_route_shapes(
            data["trips"], data["shapes"], data["routes"]
        )
        _set_static_status(app, "path_static_status", "ready")
        return


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Static GTFS loads in the BACKGROUND (see _warm_subway_static /
    # _warm_railroad_static), so startup returns immediately and never spends
    # Railway's healthcheck window on a download. State starts empty and each
    # group starts "loading"; the warmup tasks fill these fields and flip to
    # "ready" (or "failed" and retry). Until subway static is ready the poller
    # notes a warming 503 and the static endpoints report loading; railroad
    # serves GPS-only trains until its stops arrive.
    app.state.subway_stops = None
    app.state.subway_routes = []
    app.state.subway_stations = {}
    app.state.subway_static_status = "loading"
    app.state.railroad_static = {}  # {system: {stops, trips, shapes, routes} | None}
    app.state.railroad_stops = {}
    app.state.railroad_routes = {}
    app.state.railroad_static_status = "loading"
    # PATH static foundation (phase 13a; realtime is a later phase). Own
    # app.state fields, never merged into a shared namespace: numeric PATH stop
    # ids collide with MTA numeric ids across systems, the same reason the
    # alerts join is system-scoped.
    app.state.path_static = {}  # {stops, child_to_parent, trips, shapes, routes} or {}
    app.state.path_stops = {}
    app.state.path_routes = []
    app.state.path_static_status = "loading"
    # AirTrain JFK is a committed static fixture (data/airtrain_jfk.json), not a
    # network download, so it loads SYNCHRONOUSLY here and is ready the instant the
    # server accepts requests (no warmup task, no "loading" state, no 503). Loading
    # it may raise and abort boot, which is intended: unlike the graceful network
    # loaders above, a bad committed fixture is a build bug that must fail loudly.
    app.state.airtrain = airtrain_static.load_airtrain()
    app.state.feed_cache = {
        "buses": _fresh_entry(),
        "subways": _fresh_entry(),
        "railroads": _fresh_entry(),
        "path": _fresh_entry(),
    }
    # Active service-alerts index, refreshed by its own slower poll (_poll_alerts).
    app.state.alerts_cache = _fresh_alerts_entry()
    # Per-station arrivals index, rebuilt by each successful subway poll.
    app.state.subway_arrivals = {}
    # Same, per railroad system: {"LIRR": {...}, "MNR": {...}}, each system
    # replaced only on a poll where it decoded (a transient failure keeps its
    # last-known arrivals). Empty until the first successful railroad poll.
    app.state.railroad_arrivals = {}
    # Per-station PATH arrivals, rebuilt by each successful PATH poll (empty
    # until the first one; a failed poll keeps the last-known index). Its own
    # field, never merged with the MTA indexes: PATH ids collide numerically.
    app.state.path_arrivals = {}
    # Per-trip previous-poll position, used to carry a prev interpolation anchor
    # forward when the feed pruned the just-departed stop (see carry_forward_prev).
    app.state.subway_positions = {}
    # Same, for the railroad placements, keyed by (system, trip_id).
    app.state.railroad_positions = {}
    # Per-feed-group health of the most recent subway poll (None until the first
    # poll), surfaced by /api/status so a partial feed outage is visible.
    app.state.subway_feed_health = None
    # Same, for the railroad feeds (LIRR + MNR).
    app.state.railroad_feed_health = None
    # Same, for the single PATH bridge feed.
    app.state.path_feed_health = None
    # Background warmups: static GTFS (each group retries on failure) and the bus
    # route index. Startup never waits on any of them.
    app.state.subway_static_task = asyncio.create_task(_warm_subway_static(app))
    app.state.railroad_static_task = asyncio.create_task(_warm_railroad_static(app))
    app.state.path_static_task = asyncio.create_task(_warm_path_static(app))
    app.state.feed_poll_task = asyncio.create_task(_poll_feeds(app))
    # Service alerts poll on their own slower loop, independent of the position poll.
    app.state.alert_poll_task = asyncio.create_task(_poll_alerts(app))
    # Bus route geometry indexes in the background — startup never waits on
    # the ~52 MB of borough GTFS zips; /api/bus-route reports until ready.
    app.state.bus_index_task = asyncio.create_task(bus_static.ensure_index())
    yield
    # Signal the build thread first: task.cancel() alone can't interrupt the
    # worker thread, and interpreter exit would block joining it otherwise.
    bus_static.stop()
    app.state.bus_index_task.cancel()
    app.state.feed_poll_task.cancel()
    app.state.alert_poll_task.cancel()
    # cancel() during a warmup's retry sleep raises CancelledError inside the
    # sleep, so shutdown never waits out a mid-retry STATIC_RETRY_S.
    app.state.subway_static_task.cancel()
    app.state.railroad_static_task.cancel()
    app.state.path_static_task.cancel()
    # Await all so cleanup (e.g. the poller's client close) finishes before
    # shutdown proceeds; the stop event bounds how long the build task runs.
    for task in (
        app.state.bus_index_task,
        app.state.feed_poll_task,
        app.state.alert_poll_task,
        app.state.subway_static_task,
        app.state.railroad_static_task,
        app.state.path_static_task,
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="NYC Transit Live", version="0.3.0", lifespan=lifespan)

# Feed payloads (thousands of buses, ~450 KB of route geometry) are JSON that
# compresses ~5-10x; only bodies over ~1 KB are worth the CPU.
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _serve_cached(name: str) -> dict:
    """Serve {fetched_at, feed_timestamp, data} from the cache. Stale-but-present
    data is still served; the frontend judges staleness from the fetched_at /
    feed_timestamp pair (upstream lag) plus its own skew-corrected poll age
    (now - fetched_at), so a stuck poller serving frozen data still surfaces.
    Errors only reach clients while the cache has never successfully filled."""
    entry = app.state.feed_cache[name]
    if entry["data"] is not None:
        return {
            "fetched_at": entry["fetched_at"],
            "feed_timestamp": entry["feed_timestamp"],
            "data": entry["data"],
        }
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Feed cache is warming up; try again in a few seconds."
    )


def _static_endpoint_ready(status: str, response: Response, warming_detail: str) -> bool:
    """Shared warming behavior for the static-derived (decorative) endpoints.

    - loading: raise a 503 (the data is coming; do not cache anything).
    - ready: set the long cache header and return True so the caller serves data.
    - failed (retrying): set no-cache and return False so the caller serves [] that
      a browser will NOT cache, so a later retry success is not masked for an hour.
    Returning [] under a max-age here (the old behavior) was the cold-start bug:
    a browser could cache an empty payload for the whole warmup.
    """
    if status == "loading":
        raise HTTPException(status_code=503, detail=warming_detail)
    if status == "ready":
        response.headers["Cache-Control"] = "public, max-age=3600"
        return True
    response.headers["Cache-Control"] = "no-cache"  # failed: never cache the empty
    return False


@app.get("/api/buses", response_model=BusFeed)
async def get_buses() -> dict:
    """Cached bus positions: {fetched_at, data: [{id, route_id, latitude,
    longitude, bearing}, ...]}. Refreshed by the background poller."""
    return _serve_cached("buses")


@app.get("/api/bus-route/{route_id}", response_model=RouteGeometry)
async def get_bus_route(route_id: str) -> dict:
    """One bus route's representative geometry (one polyline per direction),
    read from the on-disk index built in the background at startup."""
    state = bus_static.status()
    if state in ("missing", "building"):
        raise HTTPException(
            status_code=503,
            detail="Bus route shapes are still indexing; try again in a minute.",
        )
    if state == "failed":
        raise HTTPException(
            status_code=503,
            detail="Bus route index could not be built; restart the server to retry.",
        )
    geometry = await asyncio.to_thread(bus_static.get_route_geometry, route_id)
    if geometry is None:
        if bus_static.is_partial():
            raise HTTPException(
                status_code=404,
                detail=f"No shape found for route {route_id} (route index is "
                "incomplete; some boroughs failed to download).",
            )
        raise HTTPException(status_code=404, detail=f"No shape found for route {route_id}.")
    return geometry


@app.get("/api/subway-routes", response_model=list[SubwayRoute])
async def get_subway_routes(response: Response) -> list[dict]:
    """Static subway route geometry for drawing: one entry per route with its
    polylines as [lat, lon] point lists. Loaded in the background, so clients can
    cache it between page loads once ready; 503 while the static GTFS is still
    loading (do not cache a warming empty)."""
    status = getattr(app.state, "subway_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static subway GTFS is still loading."):
        return []
    return getattr(app.state, "subway_routes", None) or []


@app.get("/api/railroad-routes", response_model=list[RailroadRoute])
async def get_railroad_routes(response: Response) -> list[dict]:
    """Static LIRR + Metro-North route geometry for drawing and gliding: one entry
    per (system, route) with its rider-facing `name` (from routes.txt, null when
    the route has no name) and polylines as [lat, lon] point lists. Built once at
    startup, so clients can cache it between loads. Keyed by system because LIRR
    and MNR route ids collide (both have a "1").

    KNOWN GAP: the builder drops a route with no usable geometry, so a
    geometry-less route's name never reaches the frontend. That is acceptable:
    such a route has no line to draw and no trains to place, so it is equally
    invisible whether or not its name is known.

    503 while the railroad static GTFS is still loading; once ready, cacheable
    (even if a system's static failed and its entries are absent, GPS-only)."""
    status = getattr(app.state, "railroad_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static railroad GTFS is still loading."):
        return []
    by_system = getattr(app.state, "railroad_routes", None) or {}
    return [
        {
            "system": system,
            "route": entry["route"],
            "name": entry["name"],
            "polylines": entry["polylines"],
        }
        for system, entries in by_system.items()
        for entry in entries
    ]


@app.get("/api/path-stops", response_model=list[PathStop])
async def get_path_stops(response: Response) -> list[dict]:
    """PATH parent-station markers ({id, name, lat, lon}) from the static GTFS.
    Parents only: the child platforms exist in the loaded tables (and the
    child_to_parent map) for later phases but are never served as markers.
    Cacheable for the session once ready; 503 while the static GTFS is still
    loading; a failed (retrying) load serves [] under no-cache, so an empty
    200 means "ask again later", never success."""
    status = getattr(app.state, "path_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static PATH GTFS is still loading."):
        return []
    stops = getattr(app.state, "path_stops", None) or {}
    return list(stops.values())


@app.get("/api/path-routes", response_model=list[PathRoute])
async def get_path_routes(response: Response) -> list[dict]:
    """Static PATH route geometry and branding for drawing: one entry per route
    with its rider-facing name, route_color/route_text_color from routes.txt,
    and the modal polyline(s) as [lat, lon] point lists (variant shapes are
    short-turn or track-work patterns; see build_path_route_shapes). Built once
    at warmup, so clients can cache it between loads. Same warming semantics as
    /api/path-stops: 503 while loading, [] under no-cache while failed.

    One extra guard beyond path-stops: the warmup gates "ready" on parent stops,
    not on the built geometry, so a degraded feed whose stops parse but whose
    shapes do not can reach "ready" with an empty routes list. An empty list is
    then served with no-cache (not the ready max-age), keeping the "empty 200
    means ask again later" contract so a browser does not pin empty geometry for
    an hour. path-stops needs no such guard: an empty-stops load marks the group
    failed instead of ready, so a ready path-stops response is never empty."""
    status = getattr(app.state, "path_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static PATH GTFS is still loading."):
        return []
    routes = getattr(app.state, "path_routes", None) or []
    if not routes:
        response.headers["Cache-Control"] = "no-cache"
    return routes


@app.get("/api/path", response_model=PathFeed)
async def get_path() -> dict:
    """Cached PATH trains from the community bridge feed: {fetched_at,
    feed_timestamp, trains}. Every train is schedule-placed at its next
    station (the bridge carries no vehicle positions) with null prev_*
    anchors: PATH bridge trip ids do not survive an upstream refresh, so no
    cross-poll identity or gliding exists yet (13d). feed_timestamp is the
    bridge's write time, which advances even when the content is a re-served
    identical generation (normal for PATH, not staleness).

    The envelope key is `trains` (not the `data` the MTA feeds use), so this
    reads its cache entry directly instead of through _serve_cached; the
    warming / never-filled-error semantics are the same.
    """
    entry = app.state.feed_cache["path"]
    if entry["data"] is not None:
        return {
            "fetched_at": entry["fetched_at"],
            "feed_timestamp": entry["feed_timestamp"],
            "trains": entry["data"],
        }
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Feed cache is warming up; try again in a few seconds."
    )


@app.get("/api/path-arrivals/{stop_id}", response_model=PathStationArrivals)
async def get_path_arrivals(stop_id: str) -> dict:
    """Upcoming PATH trains at a parent station, grouped by direction bucket,
    from the in-memory index refreshed each PATH poll.

    Modeled on /api/railroad-arrivals minus the system segment (PATH is a
    single system). Bucket keys are "To New York" / "To New Jersey" (from the
    realtime direction_id) with "Trains" as the direction-less residual,
    present only when populated; an empty {} means nothing upcoming. Rows
    carry a trip_id for shape parity only: PATH trip ids are unstable across
    upstream refreshes and display-poor, so clients must never key on or show
    them. 503 while the PATH cache has never filled (consistent with the
    other arrivals endpoints); 404 for a malformed or unknown stop id (regex
    plus membership in the static parent stops)."""
    entry = app.state.feed_cache["path"]
    if entry["data"] is None:  # no successful PATH poll yet
        if entry["error"]:
            raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
        raise HTTPException(
            status_code=503, detail="Feed cache is warming up; try again in a few seconds."
        )
    stops = getattr(app.state, "path_stops", None) or {}
    if not _PATH_STATION_ID_RE.match(stop_id) or stop_id not in stops:
        raise HTTPException(status_code=404, detail=f"Unknown PATH station {stop_id}.")
    return {
        "fetched_at": entry["fetched_at"],
        "stop_id": stop_id,
        "stop_name": stops[stop_id]["name"],
        "directions": (getattr(app.state, "path_arrivals", None) or {}).get(stop_id, {}),
    }


@app.get("/api/subways", response_model=SubwayFeed)
async def get_subways() -> dict:
    """Cached train placements: {fetched_at, data: [{trip_id, route_id,
    latitude, longitude, stop_id, stop_name, direction}, ...]}."""
    return _serve_cached("subways")


@app.get("/api/railroads", response_model=RailroadFeed)
async def get_railroads() -> dict:
    """Cached LIRR + Metro-North trains: {fetched_at, feed_timestamp, data:
    [{system, trip_id, route_id, latitude, longitude, bearing, train_num, ...},
    ...]}. Includes both GPS-positioned trains and schedule-placed trains
    positioned at their next station (the latter only when static railroad stops
    are loaded for that system); a placed train carries null bearing and filled
    direction/next_time/prev_* anchors."""
    return _serve_cached("railroads")


@app.get("/api/alerts", response_model=AlertFeed)
async def get_alerts() -> dict:
    """Active service alerts from the in-memory index: {fetched_at, alerts: [...]},
    one entry per alert active now across the subway/bus/LIRR/MNR feeds.

    Same envelope treatment as the other live feeds (no explicit Cache-Control; the
    frontend polls it, and _serve_cached sets none either). An index that decoded
    zero active alerts serves an empty list, NOT an error; a 503 surfaces only until
    the first successful poll fills the index (mirrors _serve_cached's warming path)."""
    entry = app.state.alerts_cache
    if entry["alerts"] is not None:
        return {"fetched_at": entry["fetched_at"], "alerts": entry["alerts"]}
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Alerts cache is warming up; try again in a few seconds."
    )


@app.get("/api/subway-stops", response_model=list[SubwayStop])
async def get_subway_stops(response: Response) -> list[dict]:
    """Subway station markers ({id, name, lat, lon}) from the static GTFS.
    Cacheable for the session once ready; 503 while the static GTFS is still
    loading (do not cache a warming empty)."""
    status = getattr(app.state, "subway_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static subway GTFS is still loading."):
        return []
    stations = getattr(app.state, "subway_stations", None) or {}
    return [
        {"id": sid, "name": s["name"], "lat": s["lat"], "lon": s["lon"]}
        for sid, s in stations.items()
    ]


@app.get("/api/subway-arrivals/{station_id}", response_model=StationArrivals)
async def get_subway_arrivals(station_id: str) -> dict:
    """Upcoming trains at a station, grouped by direction, from the in-memory
    index refreshed each subway poll. 503 until the first successful poll
    fills it (consistent with _serve_cached); 404 for an unknown or malformed
    station id."""
    entry = app.state.feed_cache["subways"]
    if entry["data"] is None:  # no successful subway poll yet
        if entry["error"]:
            raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
        raise HTTPException(
            status_code=503, detail="Feed cache is warming up; try again in a few seconds."
        )
    stations = getattr(app.state, "subway_stations", None) or {}
    if not _STATION_ID_RE.match(station_id) or station_id not in stations:
        raise HTTPException(status_code=404, detail=f"Unknown station {station_id}.")
    station_arrivals = (getattr(app.state, "subway_arrivals", None) or {}).get(station_id, {})
    return {
        "fetched_at": entry["fetched_at"],
        "station_id": station_id,
        "station_name": stations[station_id]["name"],
        "directions": {
            "Northbound": station_arrivals.get("Northbound", []),
            "Southbound": station_arrivals.get("Southbound", []),
        },
    }


@app.get("/api/railroad-stops", response_model=list[RailroadStop])
async def get_railroad_stops(response: Response) -> list[dict]:
    """LIRR + Metro-North station markers ({system, id, name, lat, lon}) from the
    static GTFS, keyed by system because the two stop_id namespaces are
    independent. Cacheable for the session once ready; 503 while the railroad
    static GTFS is still loading. A system whose static failed to load (None
    stops) contributes nothing, GPS-only."""
    status = getattr(app.state, "railroad_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static railroad GTFS is still loading."):
        return []
    by_system = getattr(app.state, "railroad_stops", None) or {}
    return [
        {"system": system, "id": sid, "name": s["name"], "lat": s["lat"], "lon": s["lon"]}
        for system, stops in by_system.items()
        if stops
        for sid, s in stops.items()
    ]


@app.get("/api/airtrain", response_model=AirTrainData)
async def get_airtrain(response: Response) -> dict:
    """AirTrain JFK static geometry, stations, and SCHEDULED headways.

    Static-only: AirTrain JFK has no realtime feed, so this endpoint never carries
    live positions or countdowns; the headways are scheduled reference bands (see
    the _provenance block in data/airtrain_jfk.json). Loaded once at startup from a
    committed fixture, so it is always ready while the server is up (no warming
    503) and is cacheable for the session like the other static endpoints.
    """
    response.headers["Cache-Control"] = "public, max-age=3600"
    return app.state.airtrain


@app.get("/api/railroad-arrivals/{system}/{stop_id}", response_model=RailroadStationArrivals)
async def get_railroad_arrivals(system: str, stop_id: str) -> dict:
    """Upcoming trains at a railroad station, grouped by direction bucket, from
    the in-memory index refreshed each railroad poll.

    The bucket keys are asymmetric by system: LIRR reads "Outbound"/"Inbound"
    straight from the realtime direction_id, while a trip with no usable
    direction_id (all of MNR, plus a rare LIRR trip missing it) has its direction
    INFERRED from the stop progression toward the NYC anchor (a heuristic, not
    feed data). "Trains" is the residual bucket for trips whose direction could be
    neither read nor inferred. `directions` carries only the buckets that actually
    have upcoming trains at this station, so a station shows some subset of
    {Outbound, Inbound, Trains} (unlike the subway endpoint, which always emits
    both platform directions); an empty {} means nothing is upcoming. GPS trains
    ARE included here (a positioned train still stops at stations), even though
    the marker layer draws them from their live position.

    404 for a system outside {LIRR, MNR}; 503 while the railroad cache has never
    filled (consistent with /api/subway-arrivals); 404 for a malformed or unknown
    stop_id (regex plus membership in that system's static stops)."""
    if system not in _RAILROAD_SYSTEMS:
        raise HTTPException(status_code=404, detail=f"Unknown system {system}.")
    entry = app.state.feed_cache["railroads"]
    if entry["data"] is None:  # no successful railroad poll yet
        if entry["error"]:
            raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
        raise HTTPException(
            status_code=503, detail="Feed cache is warming up; try again in a few seconds."
        )
    stops = (getattr(app.state, "railroad_stops", None) or {}).get(system) or {}
    if not _RAILROAD_STATION_ID_RE.match(stop_id) or stop_id not in stops:
        raise HTTPException(status_code=404, detail=f"Unknown {system} station {stop_id}.")
    station_arrivals = (
        (getattr(app.state, "railroad_arrivals", None) or {}).get(system, {}).get(stop_id, {})
    )
    return {
        "fetched_at": entry["fetched_at"],
        "system": system,
        "stop_id": stop_id,
        "stop_name": stops[stop_id]["name"],
        "directions": station_arrivals,
    }


@app.get("/api/status", response_model=StatusResponse)
async def get_status() -> dict:
    """Operational snapshot: per-feed cache freshness and last recorded error,
    bus route index state, static subway GTFS age, and each static group's warmup
    state (loading / ready / failed). No secrets, no filesystem paths."""
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
        alerts = {
            "fetched_at": fetched_at,
            "age_s": round(now - fetched_at, 1) if fetched_at is not None else None,
            "last_error": alerts_entry["error"],
            "active": alerts_entry["active"],
            "suppressed_planned": alerts_entry["suppressed"],
        }
    return {
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
        "subway_feeds": getattr(app.state, "subway_feed_health", None),
        "railroad_feeds": getattr(app.state, "railroad_feed_health", None),
        "path_feeds": getattr(app.state, "path_feed_health", None),
        "alerts": alerts,
    }


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
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

    body: dict = {"status": "fail" if reasons else "pass"}
    if reasons:
        body["reasons"] = reasons
    return JSONResponse(body, status_code=503 if reasons else 200)


class RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that asks the browser to revalidate every load.

    The frontend assets are unhashed (no build step) and served under stable
    names (index.html, helpers.js, map.js, style.css), so a long-lived cache
    would serve a stale bundle after a deploy (the symptom this fixes). With
    no-cache the browser keeps the file but revalidates via the ETag and
    Last-Modified StaticFiles already sets, so an unchanged file is a cheap 304
    and a deployed change is picked up immediately.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Mounted last so /api/* routes take priority; html=True serves index.html at /.
app.mount("/", RevalidatingStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
