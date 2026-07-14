"""PATH endpoints: cached trains, static stops/routes, per-station arrivals."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from cache import _require_filled_cache, _serve_cached, _static_endpoint_ready
from models import PathFeed, PathRoute, PathStationArrivals, PathStop

router = APIRouter()

# PATH parent station ids are 5-digit numerics (26733 Newark, 26734 WTC);
# allow up to 6 digits for headroom. Like the other station-id regexes this is
# only a cheap malformed-input pre-filter: membership in app.state.path_stops
# is the real gate. PATH ids live in their own namespace (they collide
# numerically with MTA ids), so this never mixes with the MTA regexes.
_PATH_STATION_ID_RE = re.compile(r"^[0-9]{1,6}$")


@router.get("/api/path-stops", response_model=list[PathStop])
async def get_path_stops(request: Request, response: Response) -> list[dict]:
    """PATH parent-station markers ({id, name, lat, lon}) from the static GTFS.
    Parents only: the child platforms exist in the loaded tables (and the
    child_to_parent map) for later phases but are never served as markers.
    Cacheable for the session once ready; 503 while the static GTFS is still
    loading; a failed (retrying) load serves [] under no-cache, so an empty
    200 means "ask again later", never success."""
    app = request.app
    status = getattr(app.state, "path_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static PATH GTFS is still loading."):
        return []
    stops = getattr(app.state, "path_stops", None) or {}
    station_routes = getattr(app.state, "path_station_routes", None) or {}
    # Merge the routes-per-station index (H5) onto each parent-station dict
    # without mutating the cached app.state stops. Empty when the derive found
    # none (or a pre-H5 cached zip lacked stop_times).
    return [{**s, "routes": station_routes.get(sid, [])} for sid, s in stops.items()]


@router.get("/api/path-routes", response_model=list[PathRoute])
async def get_path_routes(request: Request, response: Response) -> list[dict]:
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
    app = request.app
    status = getattr(app.state, "path_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static PATH GTFS is still loading."):
        return []
    routes = getattr(app.state, "path_routes", None) or []
    if not routes:
        response.headers["Cache-Control"] = "no-cache"
    return routes


@router.get("/api/path", response_model=PathFeed)
async def get_path(request: Request) -> dict:
    """Cached PATH trains from the community bridge feed: {fetched_at,
    feed_timestamp, trains}. Every train is schedule-placed at its next
    station (the bridge carries no vehicle positions) and carries a stable
    synthetic `id` from match_path_identities (13d); prev_* anchors are
    populated only after an observed advance, exactly the subway v2 glide
    contract. The bridge's own trip hash is deliberately NOT served: it is
    unstable by construction and meaningless to riders. feed_timestamp is the
    bridge's write time, which advances even when the content is a re-served
    identical generation (normal for PATH, not staleness).

    The envelope key is `trains` (not the `data` the MTA feeds use), served via
    _serve_cached's data_key so the warming / never-filled contract stays in
    one place shared with the MTA feed endpoints.
    """
    return _serve_cached(request.app, "path", data_key="trains")


@router.get("/api/path-arrivals/{stop_id}", response_model=PathStationArrivals)
async def get_path_arrivals(request: Request, stop_id: str) -> dict:
    """Upcoming PATH trains at a parent station, grouped by direction bucket,
    from the in-memory index refreshed each PATH poll.

    Modeled on /api/railroad-arrivals minus the system segment (PATH is a
    single system). Bucket keys are "To New York" / "To New Jersey" (from the
    realtime direction_id) with "Trains" as the direction-less residual,
    present only when populated; an empty {} means nothing upcoming. Rows are
    {route_id, arrival} only: unlike RailroadArrival they carry NO trip id,
    since the 13d cleanup dropped the bridge's unstable, display-poor hash
    from every served payload (see PathArrival). 503 while the PATH cache has
    never filled (consistent with the other arrivals endpoints); 404 for a
    malformed or unknown stop id (regex plus membership in the static parent
    stops)."""
    app = request.app
    entry = app.state.feed_cache["path"]
    _require_filled_cache(entry)
    stops = getattr(app.state, "path_stops", None) or {}
    if not _PATH_STATION_ID_RE.match(stop_id) or stop_id not in stops:
        raise HTTPException(status_code=404, detail=f"Unknown PATH station {stop_id}.")
    return {
        "fetched_at": entry["fetched_at"],
        "stop_id": stop_id,
        "stop_name": stops[stop_id]["name"],
        "directions": (getattr(app.state, "path_arrivals", None) or {}).get(stop_id, {}),
    }
