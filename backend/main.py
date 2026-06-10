"""FastAPI app exposing decoded MTA realtime data (buses + subways) as JSON."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from google.protobuf.message import DecodeError

from feeds import fetch_subway_trains, fetch_vehicle_positions
from static_data import load_subway_stops

logger = logging.getLogger("uvicorn.error")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the static GTFS station lookup once at startup, not per request.
    # If it can't be loaded, keep serving buses; /api/subways returns 503.
    try:
        app.state.subway_stops = await load_subway_stops()
    except Exception as exc:
        logger.error("Could not load static subway GTFS (%s); /api/subways disabled", exc)
        app.state.subway_stops = None
    yield


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
