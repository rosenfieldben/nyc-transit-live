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


class SystemFreshness(BaseModel):
    """One subsystem's own freshness inside an aggregate envelope (C2).

    THE ONE PER-SYSTEM CONTRACT, defined here and consumed everywhere. An
    aggregate endpoint fans out over several upstream systems (subway: 8 feed
    groups; railroad: LIRR + MNR; alerts: 5 systems) and a partial failure is
    still a SUCCESSFUL poll, so the envelope's top-level fetched_at only means
    "this poll ran". It says nothing about whether any particular system's data
    was refreshed. This block is what does: each system reports the age of ITS
    OWN data, and the two timestamps diverge exactly when something is wrong.

    NO ERROR TEXT LIVES HERE, deliberately. `ok` plus the age carry the whole
    rider-facing signal, and sanitized failure detail stays in /api/status, which
    keeps the leak surface exactly where it already was rather than widening it
    to every live envelope.
    """

    # This system's last poll that DECODED. Frozen while the system is failing,
    # which is the entire point: compare it with the envelope's fetched_at to see
    # how far behind this system has fallen.
    fetched_at: float | None
    # False while the system failed its most recent poll. NOTE the deliberate
    # difference from *FeedHealth.ok in this module, which is an integer COUNT of
    # healthy feeds; this is a per-system boolean. They are different models with
    # different audiences: those are operator counts on /api/status, this is the
    # rider-facing per-system flag on the live envelopes.
    ok: bool
    # Set while this system's data is being carried forward from its last good
    # poll. Null when the system is fresh AND null once the retention cap has
    # dropped the data, so it is not the complement of `ok`: a long-failed system
    # reports ok=False with retained_since=None and no data at all.
    retained_since: float | None
    # WHICH ROUTES THIS SYSTEM'S CURRENT DATA COVERS. Populated by the SUBWAY only,
    # and it is not a freshness field: it is the join key the client needs to point
    # this block at the markers it describes. A subway train carries a route_id and
    # nothing that names its feed group, so without this the client could know that
    # the ACE group is stale and still have no way to tell which trains were its.
    # The alternative was duplicating the backend's group table in JavaScript, which
    # would then drift from SUBWAY_FEED_URLS silently.
    #
    # Derived per poll from the by-group partition (the routes actually present in
    # this system's served data), so it needs no hand-maintained table and stays
    # true for retained data: a carried-forward group still lists its routes, and a
    # group the retention cap has emptied lists none, which is correct because it has
    # no markers left to describe. Null on the railroad and alerts blocks, whose
    # entities already carry their own system name.
    routes: list[str] | None = None


class SubwayFeed(BaseModel):
    fetched_at: float | None
    feed_timestamp: float | None  # oldest content time across subway feeds
    served_at: float  # this response's build time (see cache.py)
    data: list[Train]
    # Keyed by feed group ("ACE", "BDFM", ...). Optional so the field can be
    # added without breaking a client that predates it; absent only on an
    # envelope built before the first poll recorded any group.
    systems: dict[str, SystemFreshness] | None = None


class RailroadFeed(BaseModel):
    fetched_at: float | None
    # LIRR's feed-generation time; MNR's header is a lagging shared clock that
    # does not track publish time, so it is not used as a freshness signal (see
    # feeds.RAILROAD_FRESHNESS_SYSTEMS).
    feed_timestamp: float | None
    served_at: float  # this response's build time (see cache.py)
    data: list[RailroadTrain]
    systems: dict[str, SystemFreshness] | None = None  # keyed "LIRR" / "MNR"


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


# NJ Transit Rail static (15a): station markers from the credentialed RailData
# GTFS. NJT stop ids are small integers (1..176) that collide heavily with MTA,
# PATH and ferry ids (stop_id 112 names four different places across our feeds), so
# NJT data stays in its own namespace like every other system's.
class NjtStop(BaseModel):
    id: str
    name: str | None
    lat: float
    lon: float
    routes: list[str] = []  # route ids serving this station (H5)
    # NO wheelchair FIELD, unlike FerryStop, and the absence is deliberate: NJ
    # Transit's GTFS carries no accessibility data anywhere, and a hardcoded False
    # would read as an affirmative "not accessible" the feed never published.


# NJ Transit Rail realtime (15b). SCHEDULE-DERIVED, never GPS: every position
# below is computed from the TripUpdates feed's own times against 15a's stop
# coordinates, because NJ Transit's vehicle positions feed is deliberately not
# fetched (the reasoning and its numbers are at the poller registry in
# pollers.py). A consumer must treat these as derived, which is what `status`
# makes checkable rather than implicit.
class NjtTrain(BaseModel):
    # The trip_id where there is one (measured stable at 100% across polls), and
    # "njt:<entity.id>" where there is not, which is every ADDED trip. Never empty:
    # the decoder's fallback chain is what a consumer keying a map by this relies on.
    id: str
    trip_id: str
    route_id: str | None
    # Static headsign when the trip joins 15a's index; for an ADDED trip (36 of them
    # in the first capture that caught a disrupted evening, all carrying an empty
    # trip_id) this is synthesized from route plus train number, which is what a
    # departure board would show.
    headsign: str | None
    train_num: str | None  # trip_short_name == entity.id == the train number
    latitude: float
    longitude: float
    # "at-station" while inside the dwell window (arrival <= now < departure),
    # "approaching" before the first listed stop, "in-transit" on the straight
    # segment between two stops. The straight segment is this phase's accepted
    # limit; shape-following is 15c's line-drawing decision.
    status: str
    stop_id: str | None  # where it is, or the stop it is heading for
    stop_name: str | None
    delay: int | None  # seconds, from the feed; absolute times remain authoritative
    # Interpolation anchors, so 15c can glide between polls exactly as it does for
    # every other system. Null while dwelling (there is nothing to glide along).
    prev_lat: float | None
    prev_lon: float | None
    prev_time: float | None
    next_time: float | None


class NjtFeed(BaseModel):
    fetched_at: float | None
    feed_timestamp: float | None  # the TripUpdates header time
    served_at: float  # this response's build time (see cache.py)
    trains: list[NjtTrain]
    # Keyed "njt", a single-entry block. A degenerate map for one system is
    # deliberate rather than a scalar: C2's contract is that a client reads the
    # same per-system shape from every envelope, and NJ Transit being one system
    # today is not a reason to make its client code special.
    systems: dict[str, SystemFreshness] | None = None


class NjtArrival(BaseModel):
    train_num: str | None
    route_id: str | None
    headsign: str | None
    # Both times, because a departure board shows both and the dwell window that
    # places the train is derived from the pair. Either may be null at an origin
    # or a terminal; a row where both are null is never emitted.
    arrival: float | None
    departure: float | None
    delay: int | None
    trip_id: str


class NjtStationArrivals(BaseModel):
    fetched_at: float | None
    stop_id: str
    stop_name: str | None
    # FLAT and chronological, not bucketed by direction or route: every row
    # carries its own route and headsign, and a board reads by time. CANCELED
    # trips and SKIPPED stops are already excluded upstream in the decoder, so no
    # consumer can reconstruct a phantom from this list.
    arrivals: list[NjtArrival]


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
    # Keyed by alert system ("subway", "bus", "LIRR", "MNR", "ferry"), projected
    # from the health map C1 made truthful. Carried HERE rather than left on
    # /api/status because the client never fetches /api/status: without this, a
    # partial alerts outage was invisible to the rider-facing freshness marker.
    systems: dict[str, SystemFreshness] | None = None


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


class StaticArchiveStatus(BaseModel):
    """One GTFS static archive's download honesty (C5).

    Answers "is what I am serving current, and if not why". last_promoted_at is
    when a download last passed validation and replaced the cache; a null with a
    nonzero failed_downloads means every publication seen this process has been
    rejected and the cache predates them all. last_download_error is sanitized at
    the source (static_shared.describe_failure): a validation failure names the
    file shape we rejected, anything else is a type name only, never raw upstream
    text that could carry a URL or a key.
    """

    last_promoted_at: float | None
    last_download_error: str | None
    failed_downloads: int


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
    # NJ Transit (15a). FOUR states here, not three: "loading" | "ready" |
    # "failed" | "not-configured". The fourth is what makes an unconfigured
    # deployment legible: no NJT credentials means no network attempt of any kind,
    # which is a deliberate configuration choice and must never look like a broken
    # upstream. Defaulted so pre-15a fixtures validate unchanged.
    njt_static: str | None = None
    subway_feeds: SubwayFeedHealth | None
    railroad_feeds: RailroadFeedHealth | None
    path_feeds: PathFeedHealth | None
    # Defaulted so pre-14b /api/status fixtures validate unchanged; the live
    # handler always populates it once the first ferry poll runs.
    ferry_feeds: FerryFeedHealth | None = None
    # Per-ARCHIVE download honesty (C5), keyed "subway" / "railroad_LIRR" /
    # "railroad_MNR" / "path" / "ferry". Deliberately a SIBLING of the
    # *_static warmup strings above rather than an expansion of them: those are
    # plain strings the contract monitor reads by name, and a group is not an
    # archive anyway (the railroad group covers two). Read together they answer
    # "ready, serving an archive from Tuesday, three failed publications since".
    # A key appears only once its archive has been downloaded at least once in
    # this process, so the map is empty on a cold boot with a warm cache.
    static_archives: dict[str, StaticArchiveStatus] = {}
    # Alert feed health (None only before the lifespan sets it, e.g. a bare test app).
    # Defaulted so pre-alerts /api/status callers and fixtures validate unchanged;
    # the live handler always populates it.
    alerts: AlertStatus | None = None


# ---------------------------------------------------------------------------
# Readiness probe (/healthz)
# ---------------------------------------------------------------------------

# THE DEGRADED CODES, defined here beside the model that carries them because
# they ARE the contract. The contract monitor imports this tuple and matches the
# probe's response against it, so a code renamed on one side without the other
# fails at import rather than going quietly unwatched in production, which is the
# exact class of blindness the F1 audit finding was about.
#
# Stable strings rather than an enum: they cross a JSON boundary to a monitor
# that may be running a different revision than the deployment it probes, so the
# wire value has to be the identity.
HEALTH_NO_FEED_FRESH = "no-feed-fresh"
HEALTH_BUS_INDEX_FAILED = "bus-route-index-failed"
HEALTH_SUBWAY_STATIC_FAILED = "subway-static-failed"
HEALTH_FEED_CONTENT_STALE = "feed-content-stale"
HEALTH_SUBWAY_GROUPS_DOWN = "subway-groups-down"
# THE ONE CODE THAT IS NOT ABOUT AN UPSTREAM BEING UNWELL. NJ Transit allows ten
# getToken calls per account per Eastern day (observed 2026-09-02; the budget and
# what spends it are set out at njt_auth.DAILY_MINT_LIMIT) and refuses the
# eleventh. When that happens the NJ Transit layer is dark until Eastern midnight
# and there is nothing wrong with NJ Transit at all, so reporting it as an ordinary
# failure would send whoever is on call hunting an outage that does not exist. It
# gets its own code so the answer reads "the budget is spent", not "something
# broke", and so the fix reads "wait, or stop spending mints" rather than "restart".
HEALTH_NJT_MINT_QUOTA = "njt-mint-quota"

HEALTH_DEGRADED_CODES = (
    HEALTH_NO_FEED_FRESH,
    HEALTH_BUS_INDEX_FAILED,
    HEALTH_SUBWAY_STATIC_FAILED,
    HEALTH_FEED_CONTENT_STALE,
    HEALTH_SUBWAY_GROUPS_DOWN,
    HEALTH_NJT_MINT_QUOTA,
)

# The subset that makes the probe answer 503. READINESS AND SICKNESS ARE TWO
# DIFFERENT QUESTIONS and this tuple is the seam between them: Railway restarts a
# container on a failing healthcheck, so "one feed's upstream content is lagging"
# must not reach the status code, while it must still reach a human. The gating
# set is therefore exactly the three reasons the probe already had before F1, and
# the non-gating codes are new information rather than new behavior.
#
# HEALTH_NJT_MINT_QUOTA IS THE SHARPEST CASE FOR THE SPLIT YET. Restarting on it
# would not merely fail to help: a fresh process mints on its first NJ Transit
# request, spending another of the ten the account has already run out of.
HEALTH_GATING_CODES = (
    HEALTH_NO_FEED_FRESH,
    HEALTH_BUS_INDEX_FAILED,
    HEALTH_SUBWAY_STATIC_FAILED,
)


class HealthzResponse(BaseModel):
    """The readiness probe's body.

    `status` and `reasons` are unchanged from before F1: prose for a human
    reading a deploy log, and the thing that decides the status code. `degraded`
    is the machine-readable classification the contract monitor reads, and it is
    a SUPERSET of what drove the status code, so a degraded state that is
    deliberately not worth a restart is still visible to something that watches.

    ALWAYS PRESENT, EVEN EMPTY, unlike `reasons`. An absent list and an empty one
    have to be distinguishable: empty means this deployment classified itself and
    found nothing wrong, absent means it is running code that predates the
    classification and is therefore unwatched. The monitor treats those
    differently and cannot do so if a healthy deployment omits the key.
    """

    status: str  # "pass" | "fail"
    # Omitted when empty, matching the pre-F1 body exactly.
    reasons: list[str] = []
    degraded: list[str] = []
