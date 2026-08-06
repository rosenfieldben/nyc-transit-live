"""NJ Transit Rail endpoints: station markers from the 15a static foundation.

ONE endpoint in this phase, deliberately. 15a is the data foundation and the token
plumbing under it; realtime is 15b and the frontend is 15c, so nothing here serves
a train, a route line, or an arrival. The trips index and the scheduled stop
schedule the loader builds ride on app.state for those phases to consume without
re-parsing, exactly as ferry_static's trip -> route map waited for 14b.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from cache import _static_endpoint_ready
from models import NjtStop

router = APIRouter()


@router.get("/api/njt-stops", response_model=list[NjtStop])
async def get_njt_stops(request: Request, response: Response) -> list[dict]:
    """NJ Transit rail station markers ({id, name, lat, lon, routes}) from the
    static GTFS. Flat, like the ferry docks and unlike PATH's platforms: this feed
    ships no parent stations and no location_type, so every parsed stop is a
    marker.

    NO wheelchair FIELD, unlike /api/ferry-stops. The ferry feed publishes
    wheelchair_boarding and NJ Transit's publishes no accessibility data at all, so
    the marker carries none rather than a hardcoded False a client would read as
    "not accessible".

    Same warming semantics as the other static-derived endpoints: cacheable for the
    session once ready; 503 while the static GTFS is still loading; a failed (and
    retrying) load serves [] under no-cache, so an empty 200 means "ask again
    later", never success. A deployment with no NJT credentials reaches
    "not-configured" and takes that same [] under no-cache arm, because it is not
    loading (nothing is coming) and not ready (there is nothing to serve); the
    reason is published on /api/status rather than guessed at from an empty list.
    """
    app = request.app
    status = getattr(app.state, "njt_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static NJ Transit GTFS is still loading."):
        return []
    stops = getattr(app.state, "njt_stops", None) or {}
    station_routes = getattr(app.state, "njt_station_routes", None) or {}
    # Merge the routes-per-station index (H5) onto each stop dict without mutating
    # the cached app.state stops. Port Jervis stations come back carrying MAIN and
    # BERG, which is what the feed says: Port Jervis has no route id of its own and
    # its identity lives only in trip_headsign (see njt_static.derive_njt_stop_routes).
    return [{**stop, "routes": station_routes.get(sid, [])} for sid, stop in stops.items()]
