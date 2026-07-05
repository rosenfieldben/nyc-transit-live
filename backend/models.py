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
    fetched_at: float | None  # this server's poll time
    feed_timestamp: float | None  # the feed's content time (MTA's clock)
    data: list[Vehicle]


class SubwayFeed(BaseModel):
    fetched_at: float | None
    feed_timestamp: float | None  # oldest content time across subway feeds
    data: list[Train]


class RailroadFeed(BaseModel):
    fetched_at: float | None
    # LIRR's feed-generation time; MNR's header is a lagging shared clock that
    # does not track publish time, so it is not used as a freshness signal (see
    # feeds.RAILROAD_FRESHNESS_SYSTEMS).
    feed_timestamp: float | None
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


class RailroadStop(BaseModel):
    system: str  # "LIRR" or "MNR" (stop_id namespaces are independent)
    id: str
    name: str | None
    lat: float
    lon: float


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


# PATH realtime (13b): trains placed at their next station from the community
# bridge feed, plus a per-station arrivals index. PATH ids stay in their own
# namespace (numeric PATH stop ids collide with MTA numeric ids across
# systems). Bridge trip ids are UNSTABLE across upstream refreshes and
# display-poor (see path_static's module docstring): they are carried for
# shape parity with the other systems only, and the frontend must never key
# on or display them. prev_* is always null in 13b (no carry-forward until
# 13d's synthetic identity); the fields exist so 13d fills them without a
# model change, the same forward-compatibility the railroad phase 1 used.
class PathTrain(BaseModel):
    trip_id: str
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
    trains: list[PathTrain]


class PathArrival(BaseModel):
    route_id: str | None
    trip_id: str  # unstable + display-poor: parity with RailroadArrival only, never key on it
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


class PathRoute(BaseModel):
    id: str
    name: str | None  # rider-facing route name from routes.txt, null when absent
    color: str | None  # route_color hex (no '#') verbatim from routes.txt
    text_color: str | None  # route_text_color hex, same treatment
    # The modal polyline(s) for the route: one per direction that survives the
    # reverse-direction dedup (usually one), as [[lat, lon], ...] lists.
    shape: list[list[list[float]]]


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


# Service alerts. One polled feed per system (subway/bus/LIRR/MNR); the decode
# keeps only alerts active now and tags each with its system. Text is verbatim
# from the feed (route tokens like [Q] included); 12b owns rendering.
class Alert(BaseModel):
    id: str
    system: str  # feed this came from: subway | bus | LIRR | MNR
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


class AlertStatus(BaseModel):
    fetched_at: float | None
    age_s: float | None  # seconds since the alert poll last succeeded
    last_error: FeedError | None
    active: int  # active alerts currently in the index
    suppressed_planned: int  # not-yet-active planned alerts held back this poll


class StatusResponse(BaseModel):
    feeds: dict[str, FeedStatus]
    bus_route_index: BusIndexStatus
    static_subway_gtfs: StaticGtfsStatus | None
    # Background static-GTFS warmup state per group: "loading" | "ready" |
    # "failed" (None only before the lifespan sets it, e.g. a bare test app).
    subway_static: str | None
    railroad_static: str | None
    path_static: str | None
    subway_feeds: SubwayFeedHealth | None
    railroad_feeds: RailroadFeedHealth | None
    path_feeds: PathFeedHealth | None
    # Alert feed health (None only before the lifespan sets it, e.g. a bare test app).
    # Defaulted so pre-alerts /api/status callers and fixtures validate unchanged;
    # the live handler always populates it.
    alerts: AlertStatus | None = None
