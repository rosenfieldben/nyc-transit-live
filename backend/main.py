"""FastAPI app exposing decoded MTA realtime data (buses + subways) as JSON."""

from __future__ import annotations

import asyncio
import logging
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
    # Bus route geometry indexes in the background — startup never waits on
    # the ~52 MB of borough GTFS zips; /api/bus-route reports until ready.
    app.state.bus_index_task = asyncio.create_task(bus_static.ensure_index())
    yield
    # Signal the build thread first: task.cancel() alone can't interrupt the
    # worker thread, and interpreter exit would block joining it otherwise.
    bus_static.stop()
    app.state.bus_index_task.cancel()


app = FastAPI(title="NYC Transit Live", version="0.2.0", lifespan=lifespan)


@app.get("/api/buses")
async def get_buses() -> list[dict]:
    """Return a JSON list of buses, each with id, route_id, latitude,
    longitude, and bearing."""
    try:
        return await fetch_vehicle_positions()
    except RuntimeError as exc:
        # Missing/placeholder API key — a configuration problem, not a 500.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Upstream MTA feed error: {exc}"
        ) from exc
    except DecodeError as exc:
        # HTTP 200 with a non-protobuf body (CDN error page, maintenance HTML).
        raise HTTPException(
            status_code=502, detail="Upstream bus feed returned undecodable data"
        ) from exc


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
async def get_subways() -> list[dict]:
    """Return a JSON list of active trains, each placed at its next stop:
    trip_id, route_id, latitude, longitude, stop_id, stop_name, direction."""
    stops = getattr(app.state, "subway_stops", None)
    if not stops:
        raise HTTPException(
            status_code=503,
            detail="Static subway GTFS could not be loaded at startup; "
            "restart the server to retry the download.",
        )
    try:
        return await fetch_subway_trains(stops)
    except RuntimeError as exc:
        # Every subway feed failed this poll.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# Mounted last so /api/* routes take priority; html=True serves index.html at /.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
