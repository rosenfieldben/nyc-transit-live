"""Railroad (LIRR + Metro-North) endpoints: cached trains, static routes/stops,
per-station arrivals."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from cache import _require_filled_cache, _serve_cached, _static_endpoint_ready
from models import RailroadFeed, RailroadRoute, RailroadStationArrivals, RailroadStop

router = APIRouter()

# Railroad stop_ids are purely numeric in both fixtures (LIRR and MNR are each
# 1 to 3 digit opaque ids, e.g. "1", "12", "237"); allow up to 4 digits for
# headroom. This is only a cheap malformed-input pre-filter: membership in the
# system's static stops (belt-and-suspenders, like _STATION_ID_RE) is the real
# gate. The two systems' namespaces are independent, so the endpoint is keyed by
# (system, stop_id).
_RAILROAD_STATION_ID_RE = re.compile(r"^[0-9]{1,4}$")
_RAILROAD_SYSTEMS = frozenset({"LIRR", "MNR"})


@router.get("/api/railroads", response_model=RailroadFeed)
async def get_railroads(request: Request, response: Response) -> dict:
    """Cached LIRR + Metro-North trains: {fetched_at, feed_timestamp, served_at,
    data: [{system, trip_id, route_id, latitude, longitude, bearing, train_num,
    ...}, ...]}. Includes both GPS-positioned trains and schedule-placed trains
    positioned at their next station (the latter only when static railroad stops
    are loaded for that system); a placed train carries null bearing and filled
    direction/next_time/prev_* anchors. served_at is stamped per response (see THE
    THREE TIMESTAMPS in cache.py)."""
    return _serve_cached(request.app, "railroads", response)


@router.get("/api/railroad-routes", response_model=list[RailroadRoute])
async def get_railroad_routes(request: Request, response: Response) -> list[dict]:
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
    app = request.app
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


@router.get("/api/railroad-stops", response_model=list[RailroadStop])
async def get_railroad_stops(request: Request, response: Response) -> list[dict]:
    """LIRR + Metro-North station markers ({system, id, name, lat, lon}) from the
    static GTFS, keyed by system because the two stop_id namespaces are
    independent. Cacheable for the session once ready; 503 while the railroad
    static GTFS is still loading. A system whose static failed to load (None
    stops) contributes nothing, GPS-only."""
    app = request.app
    status = getattr(app.state, "railroad_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static railroad GTFS is still loading."):
        return []
    by_system = getattr(app.state, "railroad_stops", None) or {}
    routes_by_system = getattr(app.state, "railroad_station_routes", None) or {}
    return [
        {
            "system": system,
            "id": sid,
            "name": s["name"],
            "lat": s["lat"],
            "lon": s["lon"],
            # Routes serving this stop (H5), scoped to the system (LIRR/MNR ids
            # are independent namespaces). Empty when the derive found none.
            "routes": (routes_by_system.get(system) or {}).get(sid, []),
        }
        for system, stops in by_system.items()
        if stops
        for sid, s in stops.items()
    ]


@router.get("/api/railroad-arrivals/{system}/{stop_id}", response_model=RailroadStationArrivals)
async def get_railroad_arrivals(request: Request, system: str, stop_id: str) -> dict:
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
    app = request.app
    if system not in _RAILROAD_SYSTEMS:
        raise HTTPException(status_code=404, detail=f"Unknown system {system}.")
    entry = app.state.feed_cache["railroads"]
    _require_filled_cache(entry)
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
