"""Bus endpoints: cached vehicle positions and per-route geometry."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

import bus_static
from cache import _serve_cached
from models import BusFeed, RouteGeometry

router = APIRouter()


@router.get("/api/buses", response_model=BusFeed)
async def get_buses(request: Request, response: Response) -> dict:
    """Cached bus positions: {fetched_at, feed_timestamp, served_at, data: [{id,
    route_id, latitude, longitude, bearing}, ...]}. Refreshed by the background
    poller; served_at is stamped per response (see THE THREE TIMESTAMPS in cache.py)."""
    return _serve_cached(request.app, "buses", response)


@router.get("/api/bus-route/{route_id}", response_model=RouteGeometry)
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
