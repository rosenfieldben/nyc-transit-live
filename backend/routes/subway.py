"""Subway endpoints: cached placements, static routes/stops, per-station arrivals."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from cache import _require_filled_cache, _serve_cached, _static_endpoint_ready
from models import StationArrivals, SubwayFeed, SubwayRoute, SubwayStop

router = APIRouter()

# Station ids index the in-memory arrivals dict; validate the path parameter
# to reject malformed input (and any traversal-shaped surprises) up front.
_STATION_ID_RE = re.compile(r"^[A-Za-z0-9]{1,6}$")


@router.get("/api/subways", response_model=SubwayFeed)
async def get_subways(request: Request) -> dict:
    """Cached train placements: {fetched_at, data: [{trip_id, route_id,
    latitude, longitude, stop_id, stop_name, direction}, ...]}."""
    return _serve_cached(request.app, "subways")


@router.get("/api/subway-routes", response_model=list[SubwayRoute])
async def get_subway_routes(request: Request, response: Response) -> list[dict]:
    """Static subway route geometry for drawing: one entry per route with its
    polylines as [lat, lon] point lists. Loaded in the background, so clients can
    cache it between page loads once ready; 503 while the static GTFS is still
    loading (do not cache a warming empty)."""
    app = request.app
    status = getattr(app.state, "subway_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static subway GTFS is still loading."):
        return []
    return getattr(app.state, "subway_routes", None) or []


@router.get("/api/subway-stops", response_model=list[SubwayStop])
async def get_subway_stops(request: Request, response: Response) -> list[dict]:
    """Subway station markers ({id, name, lat, lon}) from the static GTFS.
    Cacheable for the session once ready; 503 while the static GTFS is still
    loading (do not cache a warming empty)."""
    app = request.app
    status = getattr(app.state, "subway_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static subway GTFS is still loading."):
        return []
    stations = getattr(app.state, "subway_stations", None) or {}
    station_routes = getattr(app.state, "subway_station_routes", None) or {}
    return [
        {
            "id": sid,
            "name": s["name"],
            "lat": s["lat"],
            "lon": s["lon"],
            # Routes serving this station (H5), so a station popup can join
            # route-scoped alerts for every route here, not only routes with an
            # imminent train. Empty when the derive found none or was skipped.
            "routes": station_routes.get(sid, []),
        }
        for sid, s in stations.items()
    ]


@router.get("/api/subway-arrivals/{station_id}", response_model=StationArrivals)
async def get_subway_arrivals(request: Request, station_id: str) -> dict:
    """Upcoming trains at a station, grouped by direction, from the in-memory
    index refreshed each subway poll. 503 until the first successful poll
    fills it (consistent with _serve_cached); 404 for an unknown or malformed
    station id."""
    app = request.app
    entry = app.state.feed_cache["subways"]
    _require_filled_cache(entry)
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
