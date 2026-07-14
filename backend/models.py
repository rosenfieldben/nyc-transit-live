"""Pydantic response models — make API shape drift fail loudly.

These document the JSON each endpoint returns and validate it at the response
boundary, so a decode/cache change that drops or mistypes a field surfaces as
a loud 500 (and a test failure) instead of silently reshaping the API. They
are intentionally permissive about EXTRA keys at runtime — an added field is
dropped, not a 500 — so production stays resilient; the tests assert the field
sets match the decode output exactly, catching additions in CI instead.
"""

from __future__ import annotations

from pydantic import BaseModel


class Vehicle(BaseModel):
    id: str
    route_id: str | None
    latitude: float
    longitude: float
    bearing: float | None


class Train(BaseModel):
    trip_id: str
    route_id: str | None
    latitude: float  # next/current station — the static-fallback position
    longitude: float
    stop_id: str
    stop_name: str | None
    direction: str | None
    # Interpolation anchors (v2: route-polyline slice, straight-line fallback).
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None  # _stop_time at the previous station (epoch)
    next_time: float | None  # expected time at the next station (epoch)


class RailroadTrain(BaseModel):
    system: str  # "LIRR" or "MNR"
    trip_id: str
    route_id: str | None
    latitude: float  # real GPS position reported by the vehicle feed
    longitude: float
    bearing: float | None
    train_num: str | None  # vehicle label/id, the rider-facing train number
    # Placement fields. stop_id/stop_name are the next/current station (null for a
    # GPS train); the rest are filled for placed trains, with the anchors carried
    # forward across polls. The model mirrors models.Train.
    stop_id: str | None
    stop_name: str | None
    direction: str | None
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None
    next_time: float | None


class BusFeed(BaseModel):
    # The three freshness timestamps; see THE THREE TIMESTAMPS in cache.py for the
    # canonical contract. feed_timestamp = upstream generation, fetched_at = our
    # last successful poll, served_at = this response's build time (moves while
    # fetched_at holds, so a stuck poller is visible).
    fetched_at: float | None  # this server's poll time
    feed_timestamp: float | None  # the feed's content time (MTA's clock)
    served_at: float  # this response's build time (see cache.py)
    data: list[Vehicle]


class SubwayFeed(BaseModel):
    fetched_at: float | None
    feed_timestamp: float | None  # oldest content time across subway feeds
    served_at: float  # this response's build time (see cache.py)
    data: list[Train]


class RailroadFeed(BaseModel):
    fetched_at: float | None
    # LIRR's feed-generation time; MNR's header is a lagging shared clock that
    # does not track publish time, so it is not used as a freshness signal (see
    # feeds.RAILROAD_FRESHNESS_SYSTEMS).
    feed_timestamp: float | None
    served_at: float  # this response's build time (see cache.py)
    data: list[RailroadTrain]


class RouteGeometry(BaseModel):
    route: str
    directions: list[list[list[float]]]


class SubwayRoute(BaseModel):
    route: str
    polylines: list[list[list[float]]]


class RailroadRoute(BaseModel):
    system: str  # "LIRR" or "MNR" (route ids collide across systems)
    route: str
    name: str | None  # rider-facing route name from routes.txt, null when absent
    polylines: list[list[list[float]]]


class SubwayStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    # Route ids serving this station (H5), derived from stop_times -> trips; the
    # station popup joins route-scoped alerts for these. Defaults to [] so an
    # older client and a pre-index warmup both stay valid.
    routes: list[str] = []


class RailroadStop(BaseModel):
    system: str  # "LIRR" or "MNR" (stop_id namespaces are independent)
    id: str
    name: str | None
    lat: float
    lon: float
    routes: list[str] = []  # route ids serving this stop (H5)


class Arrival(BaseModel):
    route_id: str | None
    trip_id: str
    arrival: float  # absolute epoch seconds


class RailroadArrival(BaseModel):
    route_id: str | None
    trip_id: str
    arrival: float  # absolute epoch seconds
    train_num: str | None  # rider-facing train number, null when no vehicle joins


class StationArrivals(BaseModel):
    fetched_at: float | None
    station_id: str
    station_name: str | None
    # Keyed by "Northbound" / "Southbound"; both keys always present.
    directions: dict[str, list[Arrival]]


class RailroadStationArrivals(BaseModel):
    fetched_at: float | None
    system: str
    stop_id: str
    stop_name: str | None
    # Bucket keys are asymmetric and only present when they have trains: LIRR uses
    # "Outbound"/"Inbound" (from direction_id), MNR and direction-less LIRR trips
    # use "Trains". An empty dict means nothing upcoming.
    directions: dict[str, list[RailroadArrival]]


# PATH realtime (13b placement + 13d identity): trains placed at their next
# station from the community bridge feed, plus a per-station arrivals index.
# PATH ids stay in their own namespace (numeric PATH stop ids collide with MTA
# numeric ids across systems). The bridge's own trip ids are UNSTABLE across
# upstream refreshes and display-poor (see path_static's module docstring), so
# 13d dropped them from this payload entirely: `id` is the backend-minted
# synthetic identity (feeds.match_path_identities), stable across polls, which
# the frontend keys its markers on. prev_* is populated only after an observed
# advance (the matcher's branch 2) and drives the same glide contract the
# subway v2 payload feeds trainLatLng; a freshly-minted identity carries null
# anchors and renders placed at its station.
class PathTrain(BaseModel):
    id: str
    route_id: str | None
    latitude: float  # next/current station, the static placement (no GPS in this feed)
    longitude: float
    stop_id: str
    stop_name: str | None
    direction: str | None  # "To New York" / "To New Jersey", null when the feed omits it
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None
    next_time: float | None


class PathFeed(BaseModel):
    fetched_at: float | None
    # The bridge's WRITE time: it advances every regeneration (~15s) even when
    # the entity content is unchanged, so it signals "bridge alive", not
    # "upstream refreshed". Unchanged content across polls is normal for PATH.
    feed_timestamp: float | None
    served_at: float  # this response's build time (see cache.py)
    trains: list[PathTrain]


class PathArrival(BaseModel):
    # Deliberately NO trip id, unlike RailroadArrival: the bridge's hashes are
    # unstable across upstream refreshes and display-poor, and since the 13d
    # cleanup they appear in no served payload anywhere.
    route_id: str | None
    arrival: float  # absolute epoch seconds


class PathStationArrivals(BaseModel):
    fetched_at: float | None
    stop_id: str
    stop_name: str | None
    # Keys are "To New York" / "To New Jersey" (from direction_id) with
    # "Trains" as the direction-less residual, present only when populated
    # (the railroad bucket discipline); {} means nothing upcoming.
    directions: dict[str, list[PathArrival]]


class PathFeedHealth(BaseModel):
    total: int  # 1: PATH is a single bridge feed
    ok: int
    failed: list[str]  # ["PATH"] when the last poll failed, else []
    # Entities the last successful poll dropped because NO stop id resolved to
    # a known parent station: nonzero means the bridge and the static stops
    # table disagree (a station renumber or a lagging 13a snapshot) and those
    # trains are silently missing from the map. Defaulted because the
    # failure-branch health dicts carry no count (no decode ran).
    unresolved: int = 0


# PATH static (13a): station markers and route geometry.
class PathStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    routes: list[str] = []  # route ids serving this station (H5)


class PathRoute(BaseModel):
    id: str
    name: str | None  # rider-facing route name from routes.txt, null when absent
    color: str | None  # route_color hex (no '#') verbatim from routes.txt
    text_color: str | None  # route_text_color hex, same treatment
    # The modal polyline(s) for the route: one per direction that survives the
    # reverse-direction dedup (usually one), as [[lat, lon], ...] lists.
    shape: list[list[list[float]]]


# NYC Ferry static (14a): station markers and route geometry. Flatter than
# PATH (no parent/child split), and the marker carries a wheelchair flag that
# is display-relevant to a later phase. Ferry stop ids are short numerics that
# collide with MTA and PATH ids, so ferry data stays in its own namespace.
class FerryStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    wheelchair: bool  # GTFS wheelchair_boarding == 1 (accessible), else False
    routes: list[str] = []  # route ids serving this dock (H5)


class FerryRoute(BaseModel):
    id: str
    name: str | None  # route_long_name from routes.txt, null when absent
    color: str | None  # route_color hex (no '#') verbatim from routes.txt
    text_color: str | None  # route_text_color hex, same treatment
    # The modal polyline(s) for the route: one per direction that survives the
    # reverse-direction dedup, as [[lat, lon], ...] lists.
    shape: list[list[list[float]]]


# NYC Ferry realtime (14b): live GPS boats from the VehiclePositions feed and a
# per-dock arrivals index from the TripUpdates feed. Both feeds carry an empty
# route_id, so route_id is recovered by joining trip_id through 14a's static
# trip -> route map; a boat whose trip_id does not join keeps its position with
# route_id null (never dropped over a metadata miss). Ferry ids stay in their own
# namespace (short numerics collide with MTA and PATH ids).
class FerryBoat(BaseModel):
    id: str  # vehicle descriptor id, stable across polls
    label: str | None  # hull name (e.g. "H201"), null when absent
    trip_id: str  # a real, stable schedule id (unlike PATH's unstable hashes)
    route_id: str | None  # from the static trip -> route join, null on a miss
    latitude: float  # real GPS position (not a station projection)
    longitude: float
    # Raw feed speed, unit undocumented (0-13 observed, plausibly m/s): passed
    # through without conversion rather than served in a guessed unit. Null when
    # the feed omits it.
    speed: float | None
    # VehicleStopStatus enum name (STOPPED_AT when docked, IN_TRANSIT_TO /
    # INCOMING_AT under way), null when the feed omits it. bearing is deliberately
    # absent: the feed always reports 0.0, so serving it would be a lie.
    status: str | None
    updated_at: float | None  # per-vehicle content time, advances each poll


class FerryFeed(BaseModel):
    fetched_at: float | None  # this server's poll time
    feed_timestamp: float | None  # the VehiclePositions feed header time
    served_at: float  # this response's build time (see cache.py)
    boats: list[FerryBoat]


class FerryArrival(BaseModel):
    route_id: str | None  # from the static trip -> route join, null on a miss
    trip_id: str  # real schedule id, exposed (unlike PathArrival)
    # Docks report BOTH times (a dwell): arrival is when the boat reaches the
    # dock, departure when it leaves. Either may be null (an origin dock has no
    # arrival, a terminal no departure), but never both on a kept row.
    arrival: float | None
    departure: float | None


class FerryStationArrivals(BaseModel):
    fetched_at: float | None
    stop_id: str
    stop_name: str | None
    # Bucketed BY ROUTE NAME (the feed carries no direction_id, and route reads
    # better at a multi-route dock), present only when populated; an empty dict
    # means nothing upcoming. A join-missed trip lands in a "Ferry" residual bucket.
    routes: dict[str, list[FerryArrival]]


class FerryFeedHealth(BaseModel):
    total: int  # 1: the two ferry endpoints are polled as one all-or-nothing feed
    ok: int
    failed: list[str]  # ["ferry"] when the last poll failed, else []


# AirTrain JFK: a static-only mode (no realtime feed exists). The whole dataset
# ships as one committed fixture, so a single /api/airtrain endpoint returns
# AirTrainData. Headways are SCHEDULED reference bands, never live countdowns.
class AirTrainHeadwayBand(BaseModel):
    start: str  # "HH:MM" service-day local (America/New_York), band start inclusive
    end: str  # "HH:MM", band end exclusive ("24:00" == end of service day)
    headway_min: int  # scheduled minutes between trains in this band (reference, not live)


class AirTrainStation(BaseModel):
    id: str
    name: str
    lat: float
    lon: float


class AirTrainRoute(BaseModel):
    id: str
    name: str
    polyline: list[list[float]]  # ordered [[lat, lon], ...] guideway geometry
    stations: list[str]  # ordered station ids this branch serves
    headways: list[AirTrainHeadwayBand]  # non-overlapping bands covering the service day


class AirTrainData(BaseModel):
    stations: list[AirTrainStation]
    routes: list[AirTrainRoute]


# Service alerts. One polled feed per system (subway/bus/LIRR/MNR/ferry); the
# decode keeps only alerts active now and tags each with its system. Text is verbatim
# from the feed (route tokens like [Q] included); 12b owns rendering.
class Alert(BaseModel):
    id: str
    system: str  # feed this came from: subway | bus | LIRR | MNR | ferry
    header: str | None
    description: str | None
    effect: str  # GTFS-RT Effect enum name (e.g. NO_SERVICE, DETOUR)
    cause: str  # GTFS-RT Cause enum name (e.g. MAINTENANCE)
    routes: list[str]  # deduped route selectors from the informed_entity list
    stops: list[str]  # deduped stop selectors (subway: parent-station ids)
    starts_at: float | None  # covering period start, null when open on the left
    ends_at: float | None  # covering period end, null when open-ended


class AlertFeed(BaseModel):
    fetched_at: float | None
    served_at: float  # this response's build time (see cache.py)
    alerts: list[Alert]


class FeedError(BaseModel):
    status: int
    detail: str


class FeedStatus(BaseModel):
    fetched_at: float | None
    age_s: float | None  # seconds since this server last polled
    feed_age_s: float | None  # how stale the feed CONTENT was at poll time
    last_error: FeedError | None


class BusIndexStatus(BaseModel):
    status: str
    partial: bool


class StaticGtfsStatus(BaseModel):
    mtime: float
    age_s: float


class SubwayFeedHealth(BaseModel):
    total: int  # number of subway feed groups polled
    ok: int  # how many returned usable data on the last poll
    failed: list[str]  # feed-group keys that failed the last poll (e.g. ["BDFM"])


class RailroadFeedHealth(BaseModel):
    total: int  # number of railroad feeds polled (LIRR + MNR)
    ok: int  # how many returned usable data on the last poll
    failed: list[str]  # systems that failed the last poll (e.g. ["MNR"])


class AlertSystemHealth(BaseModel):
    # Per-alert-feed freshness, so a partial outage (one of the alert feeds down)
    # is visible even though the poll as a whole still succeeds.
    fresh_at: float | None  # last poll this system decoded (null before its first)
    # Set while a down system's alerts are being carried forward from its last good
    # poll; null when the system is fresh or once the retention cap has dropped them.
    retained_since: float | None
    last_error: FeedError | None  # this system's failure this poll, null when fresh


class AlertStatus(BaseModel):
    fetched_at: float | None
    age_s: float | None  # seconds since the alert poll last succeeded
    last_error: FeedError | None
    active: int  # active alerts currently in the index
    suppressed_planned: int  # not-yet-active planned alerts held back this poll
    # Per-system alert-feed health and the systems failing right now. Defaulted so
    # pre-retention /api/status fixtures validate unchanged; the live handler always
    # populates them once the alerts cache exists.
    systems: dict[str, AlertSystemHealth] | None = None
    degraded_systems: list[str] = []


class StatusResponse(BaseModel):
    served_at: float  # this snapshot's build time (see cache.py)
    feeds: dict[str, FeedStatus]
    bus_route_index: BusIndexStatus
    static_subway_gtfs: StaticGtfsStatus | None
    # Background static-GTFS warmup state per group: "loading" | "ready" |
    # "failed" (None only before the lifespan sets it, e.g. a bare test app).
    subway_static: str | None
    railroad_static: str | None
    path_static: str | None
    # Defaulted so pre-14a /api/status fixtures validate unchanged; the live
    # handler always populates it.
    ferry_static: str | None = None
    subway_feeds: SubwayFeedHealth | None
    railroad_feeds: RailroadFeedHealth | None
    path_feeds: PathFeedHealth | None
    # Defaulted so pre-14b /api/status fixtures validate unchanged; the live
    # handler always populates it once the first ferry poll runs.
    ferry_feeds: FerryFeedHealth | None = None
    # Alert feed health (None only before the lifespan sets it, e.g. a bare test app).
    # Defaulted so pre-alerts /api/status callers and fixtures validate unchanged;
    # the live handler always populates it.
    alerts: AlertStatus | None = None
