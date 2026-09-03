"""NJ Transit Rail endpoints: station markers (15a) plus live trains and the
realtime arrivals index (15b).

Route lines arrived in 15c: /api/njt-routes serves the per-route polylines built
from shapes.txt, on the same warming and caching semantics as every other
static-derived endpoint here.

WHY AN ARRIVALS ENDPOINT EXISTS HERE when the 15b deliverables name only
/api/njt-trains: the trap matrix's central claim is "no phantom arrival at stop
109", and at the contract tier that claim is only expressible against a served
endpoint. Every sibling system already has one (/api/path-arrivals,
/api/ferry-arrivals, /api/railroad-arrivals), so this is the consistent shape
rather than a new surface, and it is what 15c's panel will read.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request, Response

from cache import _require_filled_cache, _serve_cached, _static_endpoint_ready
from models import NjtFeed, NjtRoute, NjtStationArrivals, NjtStop

router = APIRouter()

# NJT stop ids are small integers (1..176 as of the 2026-08-05 probe); allow up to
# six digits for headroom. Like the other station-id regexes this is only a cheap
# malformed-input pre-filter: membership in app.state.njt_stops is the real gate.
# NJT ids live in their own namespace (84.9% of them collide with an id in one of
# our other feeds), so this never mixes with those regexes.
_NJT_STOP_ID_RE = re.compile(r"^[0-9]{1,6}$")


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


@router.get("/api/njt-routes", response_model=list[NjtRoute])
async def get_njt_routes(request: Request, response: Response) -> list[dict]:
    """Static NJ Transit rail route geometry for drawing and gliding: one entry per
    route with its rider-facing `name`, the feed's own `color` and `text_color`,
    and `polylines` as [lat, lon] point lists. Built once at startup from
    shapes.txt, so clients can cache it between loads.

    NOT KEYED BY SYSTEM, unlike /api/railroad-routes: LIRR and Metro-North route
    ids collide with each other, NJ Transit's do not collide with themselves.

    text_color IS NULL ON EVERY ROUTE as this feed publishes it, so a client that
    prints text on `color` has to compute its own readable ink. Said here rather
    than left to be discovered, because the failure mode is dark text on a dark
    line, which is invisible rather than obviously broken.

    AN EMPTY LIST IS A VALID ANSWER, and it means the publication carried no
    drawable geometry: shapes.txt is optional (route lines are additive, so a
    publication without it still serves stations and trains and still reports
    ready). A route with no geometry is likewise absent rather than present with an
    empty polyline list, the same known gap /api/railroad-routes documents: such a
    route has no line to draw and no trains to place, so it is invisible either
    way. NJ Transit has a live example rather than a hypothetical, the
    event-only Meadowlands Rail Line, which a publication without Meadowlands
    service references no shape for.

    503 while the NJT static GTFS is still loading; once ready, cacheable. A failed
    (and retrying) load, and a deployment with no NJT credentials, both serve []
    under no-cache, so an empty 200 never means "there is definitively nothing
    here"; /api/status carries the reason.
    """
    app = request.app
    status = getattr(app.state, "njt_static_status", "loading")
    if not _static_endpoint_ready(status, response, "Static NJ Transit GTFS is still loading."):
        return []
    return getattr(app.state, "njt_routes", None) or []


@router.get("/api/njt-trains", response_model=NjtFeed)
async def get_njt_trains(request: Request, response: Response) -> dict:
    """Cached live NJ Transit trains: {fetched_at, feed_timestamp, served_at,
    trains, systems}.

    SCHEDULE-DERIVED POSITIONS, not GPS. Each train is placed from the
    TripUpdates feed's own arrival and departure times against 15a's stop
    coordinates: standing at a platform inside its dwell window, or interpolated
    along the straight segment between the stop it left and the stop it is
    heading for. NJ Transit's vehicle positions feed is deliberately never
    fetched; the numbers behind that decision are at the poller registry in
    pollers.py.

    An empty trains list is a VALID served state, not a warming 503: the probe
    recorded a 13-byte valid-but-empty feed overnight, and zero trains at 03:00 is
    the correct answer rather than a failure. 503 only until the FIRST successful
    poll fills the cache. served_at is stamped per response (see THE THREE
    TIMESTAMPS in cache.py).

    The `systems` block is the C2 per-system freshness, keyed "njt". It is a
    single-entry map on purpose: a client reads the same shape here as from the
    subway and railroad envelopes, so NJ Transit needs no special case on the
    frontend.
    """
    return _serve_cached(request.app, "njt", response, data_key="trains", with_systems=True)


@router.get("/api/njt-arrivals/{stop_id}", response_model=NjtStationArrivals)
async def get_njt_arrivals(request: Request, stop_id: str) -> dict:
    """Upcoming NJ Transit departures at one stop, from the in-memory index each
    poll rebuilds.

    FLAT AND CHRONOLOGICAL, unlike the bucketed subway/railroad/ferry arrivals:
    every row carries its own route and headsign, and a departure board reads by
    time. Penn Station New York is stop 109.

    THE PHANTOM CANNOT BE HERE. A trip-level CANCELED trip keeps full arrival and
    departure times on every stop it marks SKIPPED (8% of peak Penn
    stop_time_updates were that shape), so it is filtered in the decoder before
    either product is built, which is what makes it impossible to reconstruct one
    from this endpoint rather than merely unlikely.

    503 while the NJT cache has never filled (consistent with the other arrivals
    endpoints); 404 for a malformed or unknown stop id (regex plus membership in
    the static NJT stops).
    """
    app = request.app
    entry = app.state.feed_cache["njt"]
    _require_filled_cache(entry)
    stops = getattr(app.state, "njt_stops", None) or {}
    if not _NJT_STOP_ID_RE.match(stop_id) or stop_id not in stops:
        raise HTTPException(status_code=404, detail=f"Unknown NJ Transit stop {stop_id}.")
    return {
        "fetched_at": entry["fetched_at"],
        "stop_id": stop_id,
        "stop_name": stops[stop_id]["name"],
        "arrivals": (getattr(app.state, "njt_arrivals", None) or {}).get(stop_id, []),
    }
