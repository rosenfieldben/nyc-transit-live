"""NYC Ferry endpoints (14a static foundation): stop markers and route geometry."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from cache import _static_endpoint_ready
from models import FerryRoute, FerryStop

router = APIRouter()


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
