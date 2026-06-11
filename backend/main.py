"""FastAPI app exposing decoded MTA realtime data (buses + subways) as JSON."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from google.protobuf.message import DecodeError

import bus_static
from feeds import fetch_subway_trains, fetch_vehicle_positions
from static_data import load_subway_route_shapes, load_subway_stops

logger = logging.getLogger("uvicorn.error")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# The backend polls the MTA once and serves every browser client from this
# cache, so N clients never means N upstream fetches.
POLL_INTERVAL_S = 20


def _fresh_entry() -> dict:
    return {"data": None, "fetched_at": None, "error": None}


def _note_failure(entry: dict, status: int, detail: str) -> None:
    """Record why the latest poll failed. Last-known data keeps being served;
    the error only surfaces to clients while the cache has never been filled."""
    entry["error"] = {"status": status, "detail": detail}
    logger.warning("feed poll failed (%d): %s", status, detail)


async def _refresh_buses(app: FastAPI, client: httpx.AsyncClient) -> None:
    entry = app.state.feed_cache["buses"]
    try:
        data = await fetch_vehicle_positions(client)
    except RuntimeError as exc:
        # Missing/placeholder API key — a configuration problem, not a 500.
        _note_failure(entry, 503, str(exc))
        return
    except httpx.HTTPError as exc:
        _note_failure(entry, 502, f"Upstream MTA feed error: {exc}")
        return
    except DecodeError:
        # HTTP 200 with a non-protobuf body (CDN error page, maintenance HTML).
        _note_failure(entry, 502, "Upstream bus feed returned undecodable data")
        return
    entry.update(data=data, fetched_at=time.time(), error=None)


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
    try:
        data = await fetch_subway_trains(stops, client)
    except RuntimeError as exc:
        # Every subway feed failed this poll.
        _note_failure(entry, 502, str(exc))
        return
    except httpx.HTTPError as exc:
        _note_failure(entry, 502, f"Upstream MTA feed error: {exc}")
        return
    entry.update(data=data, fetched_at=time.time(), error=None)


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
                    _refresh_buses(app, client), _refresh_subways(app, client)
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
        # Route lines reuse the zip the stops loader just ensured exists.
        app.state.subway_routes = load_subway_route_shapes()
    except Exception as exc:
        logger.error("Could not load static subway GTFS (%s); /api/subways disabled", exc)
        app.state.subway_stops = None
        app.state.subway_routes = []
    app.state.feed_cache = {"buses": _fresh_entry(), "subways": _fresh_entry()}
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


app = FastAPI(title="NYC Transit Live", version="0.2.0", lifespan=lifespan)


def _serve_cached(name: str) -> dict:
    """Serve {fetched_at, data} from the cache. Stale-but-present data is
    still served (fetched_at lets the frontend show staleness); errors only
    reach clients while the cache has never successfully filled."""
    entry = app.state.feed_cache[name]
    if entry["data"] is not None:
        return {"fetched_at": entry["fetched_at"], "data": entry["data"]}
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Feed cache is warming up; try again in a few seconds."
    )


@app.get("/api/buses")
async def get_buses() -> dict:
    """Cached bus positions: {fetched_at, data: [{id, route_id, latitude,
    longitude, bearing}, ...]}. Refreshed by the background poller."""
    return _serve_cached("buses")


@app.get("/api/bus-route/{route_id}")
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
        raise HTTPException(status_code=404, detail=f"No shape found for route {route_id}.")
    return geometry


@app.get("/api/subway-routes")
async def get_subway_routes() -> list[dict]:
    """Static subway route geometry for drawing: one entry per route with its
    polylines as [lat, lon] point lists. Loaded once at startup."""
    return getattr(app.state, "subway_routes", None) or []


@app.get("/api/subways")
async def get_subways() -> dict:
    """Cached train placements: {fetched_at, data: [{trip_id, route_id,
    latitude, longitude, stop_id, stop_name, direction}, ...]}."""
    return _serve_cached("subways")


# Mounted last so /api/* routes take priority; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
