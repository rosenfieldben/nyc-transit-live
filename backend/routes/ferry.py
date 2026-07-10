"""NYC Ferry endpoints: stop markers and route geometry (14a static foundation),
plus live boats and per-dock arrivals (14b realtime)."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from cache import _require_filled_cache, _serve_cached, _static_endpoint_ready
from models import FerryFeed, FerryRoute, FerryStationArrivals, FerryStop

router = APIRouter()

# NYC Ferry stop ids are short numerics (e.g. "18"); allow up to 6 digits for
# headroom. Like the other station-id regexes this is only a cheap malformed-input
# pre-filter: membership in app.state.ferry_stops is the real gate. Ferry ids live
# in their own namespace (they collide numerically with MTA and PATH ids), so this
# never mixes with those regexes.
_FERRY_STOP_ID_RE = re.compile(r"^[0-9]{1,6}$")


@router.get("/api/ferry-stops", response_model=list[FerryStop])
async def get_ferry_stops(request: Request, response: Response) -> list[dict]:
    """NYC Ferry stop markers ({id, name, lat, lon, wheelchair}) from the
    static GTFS. Flat (no parent/child split like PATH), so every parsed stop
    is a marker. Cacheable for the session once ready; 503 while the static
    GTFS is still loading; a failed (retrying) load serves [] under no-cache,
    so an empty 200 means "ask again later", never success."""
    app = request.app
    status = getattr(app.state, "ferry_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static NYC Ferry GTFS is still loading."):
        return []
    stops = getattr(app.state, "ferry_stops", None) or {}
    return list(stops.values())


@router.get("/api/ferry-routes", response_model=list[FerryRoute])
async def get_ferry_routes(request: Request, response: Response) -> list[dict]:
    """Static NYC Ferry route geometry and branding for drawing: one entry per
    route with its rider-facing name, route_color/route_text_color, and the
    modal polyline(s) as [lat, lon] point lists (variant shapes are short-run
    or reroute patterns; see build_ferry_route_shapes). Built once at warmup,
    so clients can cache it between loads. Same warming semantics as
    /api/ferry-stops: 503 while loading, [] under no-cache while failed.

    The same guard as /api/path-routes (13a's review): the warmup gates "ready"
    on stops, not on built geometry, so a degraded feed whose stops parse but
    whose shapes do not can reach "ready" with an empty routes list. An empty
    list is then served with no-cache (not the ready max-age), keeping the
    "empty 200 means ask again later" contract so a browser does not pin empty
    geometry for an hour."""
    app = request.app
    status = getattr(app.state, "ferry_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static NYC Ferry GTFS is still loading."):
        return []
    routes = getattr(app.state, "ferry_routes", None) or []
    if not routes:
        response.headers["Cache-Control"] = "no-cache"
    return routes


@router.get("/api/ferry", response_model=FerryFeed)
async def get_ferry(request: Request) -> dict:
    """Cached live NYC Ferry boats from the VehiclePositions feed: {fetched_at,
    feed_timestamp, boats}. Each boat carries its real GPS position, hull label,
    trip_id, route_id (from the static trip -> route join, null on a miss), speed,
    current_status, and updated_at; bearing is omitted (the feed reports only
    0.0).

    An empty boats list is a VALID served state (overnight the boats go home), not
    a warming 503: a successful poll that decoded zero boats fills the cache with
    [], which serves normally. 503 only until the FIRST successful poll fills the
    cache. The envelope key is `boats` via _serve_cached's data_key, so the
    warming / never-filled contract stays shared with the other feed endpoints."""
    return _serve_cached(request.app, "ferry", data_key="boats")


@router.get("/api/ferry-arrivals/{stop_id}", response_model=FerryStationArrivals)
async def get_ferry_arrivals(request: Request, stop_id: str) -> dict:
    """Upcoming boats at a NYC Ferry dock, grouped by route, from the in-memory
    index refreshed each ferry poll.

    Modeled on /api/path-arrivals (single system, no direction segment). Bucket
    keys are route long names (the feed carries no direction_id, and route reads
    better at a multi-route dock), present only when populated; an empty {} means
    nothing upcoming, and a join-missed trip lands in a "Ferry" residual bucket.
    Rows carry route_id, trip_id, arrival, and departure (docks report both as a
    dwell). 503 while the ferry cache has never filled (consistent with the other
    arrivals endpoints); 404 for a malformed or unknown stop id (regex plus
    membership in the static ferry stops)."""
    app = request.app
    entry = app.state.feed_cache["ferry"]
    _require_filled_cache(entry)
    stops = getattr(app.state, "ferry_stops", None) or {}
    if not _FERRY_STOP_ID_RE.match(stop_id) or stop_id not in stops:
        raise HTTPException(status_code=404, detail=f"Unknown NYC Ferry stop {stop_id}.")
    return {
        "fetched_at": entry["fetched_at"],
        "stop_id": stop_id,
        "stop_name": stops[stop_id]["name"],
        "routes": (getattr(app.state, "ferry_arrivals", None) or {}).get(stop_id, {}),
    }
