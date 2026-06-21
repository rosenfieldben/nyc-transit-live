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

import bus_static
import railroad_static
import static_data
from feeds import (
    RAILROAD_FEED_URLS,
    SUBWAY_FEED_URLS,
    carry_forward_prev,
    fetch_railroad_trains,
    fetch_subway_trains,
    fetch_vehicle_positions,
)
from models import (
    BusFeed,
    RailroadFeed,
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


def _fresh_entry() -> dict:
    # fetched_at = this server's poll time; feed_timestamp = the feed's content
    # time (MTA's clock). Both are stored so freshness can be judged without the
    # browser clock — see _feed_age and FEED_STALE_AFTER_S.
    return {"data": None, "fetched_at": None, "feed_timestamp": None, "error": None}


def _note_failure(entry: dict, status: int, detail: str) -> None:
    """Record why the latest poll failed. Last-known data keeps being served;
    the error only surfaces to clients while the cache has never been filled."""
    entry["error"] = {"status": status, "detail": detail}
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
        _note_failure(
            entry,
            503,
            "Static subway GTFS could not be loaded at startup; "
            "restart the server to retry the download.",
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
        trains, feed_timestamp, failed_feeds = await fetch_railroad_trains(
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
    # feed_timestamp comes from LIRR's header only (MNR's lagging shared clock is
    # excluded; see feeds.RAILROAD_FRESHNESS_SYSTEMS); a failed poll keeps the
    # last-known timestamp, same as the subway cache.
    entry.update(data=trains, fetched_at=time.time(), feed_timestamp=feed_timestamp, error=None)


async def _poll_feeds(app: FastAPI) -> None:
    """Refresh both feeds every POLL_INTERVAL_S for the app's lifetime.

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
                )
            except Exception:
                logger.exception("feed poll cycle failed unexpectedly")
            await asyncio.sleep(POLL_INTERVAL_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the static GTFS station lookup once at startup, not per request.
    # If it can't be loaded, keep serving buses; /api/subways returns 503.
    try:
        app.state.subway_stops = await load_subway_stops()
        # Route lines and station markers reuse the zip the stops loader just
        # ensured exists.
        app.state.subway_routes = load_subway_route_shapes()
        app.state.subway_stations = load_subway_stations()
    except Exception as exc:
        logger.error("Could not load static subway GTFS (%s); /api/subways disabled", exc)
        app.state.subway_stops = None
        app.state.subway_routes = []
        app.state.subway_stations = {}
    # Railroad static GTFS, per system, for station placement of position-less
    # trains. Each system loads independently and leniently: a None for a system
    # (download/parse failed) just means that system gets GPS trains only, never
    # a crash. Store the per-system stops; trips/shapes are for a later gliding
    # increment.
    try:
        railroad_static_data = await railroad_static.load_railroad_static()
    except Exception as exc:
        logger.error("Could not load railroad static GTFS (%s); placement disabled", exc)
        railroad_static_data = {}
    app.state.railroad_stops = {
        system: (data["stops"] if data else None) for system, data in railroad_static_data.items()
    }
    app.state.feed_cache = {
        "buses": _fresh_entry(),
        "subways": _fresh_entry(),
        "railroads": _fresh_entry(),
    }
    # Per-station arrivals index, rebuilt by each successful subway poll.
    app.state.subway_arrivals = {}
    # Per-trip previous-poll position, used to carry a prev interpolation anchor
    # forward when the feed pruned the just-departed stop (see carry_forward_prev).
    app.state.subway_positions = {}
    # Per-feed-group health of the most recent subway poll (None until the first
    # poll), surfaced by /api/status so a partial feed outage is visible.
    app.state.subway_feed_health = None
    # Same, for the railroad feeds (LIRR + MNR).
    app.state.railroad_feed_health = None
    app.state.feed_poll_task = asyncio.create_task(_poll_feeds(app))
    # Bus route geometry indexes in the background — startup never waits on
    # the ~52 MB of borough GTFS zips; /api/bus-route reports until ready.
    app.state.bus_index_task = asyncio.create_task(bus_static.ensure_index())
    yield
    # Signal the build thread first: task.cancel() alone can't interrupt the
    # worker thread, and interpreter exit would block joining it otherwise.
    bus_static.stop()
    app.state.bus_index_task.cancel()
    app.state.feed_poll_task.cancel()
    # Await both so cleanup (e.g. the poller's client close) finishes before
    # shutdown proceeds; the stop event bounds how long the build task runs.
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.bus_index_task
    with contextlib.suppress(asyncio.CancelledError):
        await app.state.feed_poll_task


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
    polylines as [lat, lon] point lists. Loaded once at startup, so it's safe
    for clients to cache between page loads."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return getattr(app.state, "subway_routes", None) or []


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


@app.get("/api/subway-stops", response_model=list[SubwayStop])
async def get_subway_stops(response: Response) -> list[dict]:
    """Subway station markers ({id, name, lat, lon}) from the static GTFS.
    Static for the session, so clients can cache it like the route lines."""
    response.headers["Cache-Control"] = "public, max-age=3600"
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


@app.get("/api/status", response_model=StatusResponse)
async def get_status() -> dict:
    """Operational snapshot: per-feed cache freshness and last recorded
    error, bus route index state, and static subway GTFS age. No secrets,
    no filesystem paths."""
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
    return {
        "feeds": feeds,
        "bus_route_index": {
            "status": bus_static.status(),
            "partial": bus_static.is_partial(),
        },
        "static_subway_gtfs": static_gtfs,
        "subway_feeds": getattr(app.state, "subway_feed_health", None),
        "railroad_feeds": getattr(app.state, "railroad_feed_health", None),
    }


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Readiness probe for the platform (Railway points its healthcheck here).
    Unlike the always-200 /api/status snapshot, this returns 503 when the app
    can't serve fresh data.

    Lenient by design: ready as long as AT LEAST ONE feed has fresh data, so a
    misconfigured key (which only stops the bus feed) doesn't take down an
    otherwise-working subway map. Degraded when no feed is fresh, or the bus
    route index build has failed. A still-building/missing index is NOT
    degraded, so a cold-start deploy stays healthy through the index warmup
    (within Railway's healthcheckTimeout) instead of flapping."""
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
