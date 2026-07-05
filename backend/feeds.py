"""Fetch and decode the MTA GTFS-Realtime feeds (bus positions, subway trips,
commuter-rail / railroad GPS positions)."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

logger = logging.getLogger(__name__)

# The .env file lives in the project root, one level up from backend/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

VEHICLE_POSITIONS_URL = "https://gtfsrt.prod.obanyc.com/vehiclePositions"

# NYC bounding box. The bus feed occasionally emits out-of-range coordinates
# (e.g. (0, 0) from a depot/test vehicle) that would scatter markers across the
# globe; this is the same invariant the subway golden test asserts.
NYC_LAT_MIN, NYC_LAT_MAX = 40.4, 41.1
NYC_LON_MIN, NYC_LON_MAX = -74.3, -73.6


def _in_nyc(lat: float, lon: float) -> bool:
    return NYC_LAT_MIN <= lat <= NYC_LAT_MAX and NYC_LON_MIN <= lon <= NYC_LON_MAX


# Railroad bounding box: much wider than the bus/subway NYC box because the LIRR
# runs out to Montauk and the MNR to Poughkeepsie / Wassaic / New Haven. Used to
# drop any stray out-of-range vehicle coordinate; every captured sample falls
# inside, so this is a sanity guard, not a service-area definition.
RAILROAD_LAT_MIN, RAILROAD_LAT_MAX = 40.3, 42.1
RAILROAD_LON_MIN, RAILROAD_LON_MAX = -74.5, -71.7


def _in_railroad_box(lat: float, lon: float) -> bool:
    return (
        RAILROAD_LAT_MIN <= lat <= RAILROAD_LAT_MAX and RAILROAD_LON_MIN <= lon <= RAILROAD_LON_MAX
    )


# Keyless subway GTFS-RT feeds, one per line group.
_SUBWAY_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2Fgtfs"
SUBWAY_FEED_URLS = {
    "1-7+S": _SUBWAY_BASE,
    "ACE": _SUBWAY_BASE + "-ace",
    "BDFM": _SUBWAY_BASE + "-bdfm",
    "G": _SUBWAY_BASE + "-g",
    "JZ": _SUBWAY_BASE + "-jz",
    "NQRW": _SUBWAY_BASE + "-nqrw",
    "L": _SUBWAY_BASE + "-l",
    "SIR": _SUBWAY_BASE + "-si",
}

# Keyless commuter-rail GTFS-RT feeds, same %2F-encoded base as the subway feeds
# (the literal-slash form is an unmatched API Gateway route that 403s with a
# misleading "Missing Authentication Token"; the encoded form needs no key).
_RAILROAD_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"
RAILROAD_FEED_URLS = {
    "LIRR": _RAILROAD_BASE + "/lirr%2Fgtfs-lirr",
    "MNR": _RAILROAD_BASE + "/mnr%2Fgtfs-mnr",
}

# Per-feed content time (FeedHeader.timestamp) is only a usable freshness
# signal for a system whose header tracks publish time. A
# per-vehicle-vs-header probe found MNR stamps a bursty clock that lags
# ~2-4 min onto its header AND copies it onto every vehicle.timestamp (no
# independent signal), while its GPS positions are live; LIRR's header is the
# true feed-generation time. So only LIRR's header drives the railroad
# feed_timestamp. An MNR upstream freeze can't be timestamp-detected from this
# feed and falls to the poll-age signal instead.
RAILROAD_FRESHNESS_SYSTEMS = frozenset({"LIRR"})

# Keyless GTFS-RT Service Alerts feeds, camsys-published on the same %2F-encoded
# base as the railroad feeds. Keyed by the system this app serves so each decoded
# alert can be tagged with its system. Deliberately NOT camsys%2Fall-alerts: that
# bundle mixes in agencies this app does not map (Access-A-Ride, bridges/tunnels,
# outer systems), which would surface alerts with no marker or route to attach to.
ALERT_FEED_URLS = {
    "subway": _RAILROAD_BASE + "/camsys%2Fsubway-alerts",
    "bus": _RAILROAD_BASE + "/camsys%2Fbus-alerts",
    "LIRR": _RAILROAD_BASE + "/camsys%2Flirr-alerts",
    "MNR": _RAILROAD_BASE + "/camsys%2Fmnr-alerts",
}


def _api_key() -> str:
    key = os.getenv("BUS_TIME_API_KEY")
    if not key or key == "your-key-here":
        raise RuntimeError(
            "BUS_TIME_API_KEY is not set. Copy .env.example to .env in the "
            "project root and add your MTA Bus Time key."
        )
    return key


def _header_timestamp(feed) -> float | None:
    """The feed's content time (FeedHeader.timestamp, MTA's clock) as a float,
    or None when the feed omits it (the field is 0). This is distinct from the
    app server's poll time — see the freshness handling in main.py."""
    return float(feed.header.timestamp) or None


async def fetch_vehicle_positions(client: httpx.AsyncClient) -> tuple[list[dict], float | None]:
    """Fetch the feed, decode the protobuf, and return (vehicles, feed_timestamp).

    Each vehicle dict has: id, route_id, latitude, longitude, bearing. Entities
    without a position are skipped; bearing is None when the feed doesn't report
    it. feed_timestamp is the feed's content time (MTA's clock). The caller owns
    the client (the polling task holds one for its lifetime).
    """
    resp = await client.get(VEHICLE_POSITIONS_URL, params={"key": _api_key()})
    resp.raise_for_status()
    raw = resp.content

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    vehicles: list[dict] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        if not v.HasField("position"):
            continue
        pos = v.position
        if not _in_nyc(pos.latitude, pos.longitude):
            continue  # out-of-range coordinate (e.g. 0,0); not a real NYC bus
        vehicles.append(
            {
                "id": v.vehicle.id or entity.id,
                "route_id": v.trip.route_id or None,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "bearing": pos.bearing if pos.HasField("bearing") else None,
            }
        )
    return vehicles, _header_timestamp(feed)


NYC_TZ = ZoneInfo("America/New_York")

# Show a trip slightly before its scheduled start; anything further out is a
# not-yet-departed phantom that would pile up at terminals.
TRIP_START_GRACE_S = 120

# Fallback when a trip's scheduled start can't be derived: don't trust a trip
# still sitting at its first listed stop more than this far in the future.
MAX_FUTURE_FIRST_STOP_S = 180


def _stop_time(stu) -> int | None:
    """Latest available POSIX time for a stop_time_update, or None.

    Latest (departure when both are set) matters for the "still upcoming"
    test: a train dwelling or held at a station has arrival in the past but
    departure in the future, and must not be treated as past that stop —
    otherwise held trains get plotted one station ahead.
    """
    times = []
    for field in ("arrival", "departure"):
        if stu.HasField(field):
            event = getattr(stu, field)
            if event.HasField("time") and event.time:
                times.append(event.time)
    return max(times) if times else None


def _trip_start_ts(trip) -> float | None:
    """Scheduled start of a subway trip as a POSIX timestamp, if derivable.

    The feeds include trips up to ~30 minutes before they depart; the NYCT
    extension flag that marks them (is_assigned) isn't readable with the
    standard bindings, so the schedule start is the discriminator. Prefers
    explicit start_time/start_date (SIR sets them); falls back to the NYCT
    trip_id convention where the prefix is the origin departure in
    centiminutes after midnight of the service day (may exceed 24h for
    post-midnight trips). Returns None if neither source parses.
    """
    # KNOWN CAVEAT (DST): we add a timedelta to service-day midnight and rely on
    # .timestamp() below. Adding a timedelta to a zoneinfo-aware datetime shifts
    # wall-clock fields without re-normalizing the UTC offset, so on the ~2
    # days/year that cross a DST boundary the derived instant can be off by an
    # hour. The schedule start is only used as a coarse "has this trip departed"
    # discriminator (TRIP_START_GRACE_S = 120s), so a twice-yearly hour skew
    # doesn't meaningfully affect placement; a precise fix would need wall-clock
    # reconstruction with fold handling and isn't worth the regression risk.
    try:
        d = trip.start_date  # YYYYMMDD
        base = datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), tzinfo=NYC_TZ)
    except (ValueError, IndexError):
        base = datetime.now(NYC_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    if trip.start_time:
        try:
            h, m, s = (int(p) for p in trip.start_time.split(":"))
            return (base + timedelta(hours=h, minutes=m, seconds=s)).timestamp()
        except ValueError:
            pass  # malformed; try the trip_id prefix

    prefix = trip.trip_id.split("_", 1)[0]
    if prefix.isdigit():
        return (base + timedelta(minutes=int(prefix) / 100)).timestamp()
    return None


# Next this many upcoming trains kept per station and direction for arrivals.
ARRIVALS_PER_DIRECTION = 6

# GTFS-RT schedule relationships that mean "ignore this": a CANCELED trip isn't
# running; a DELETED trip has been removed and must not render as a ghost train;
# a SKIPPED/NO_DATA stop carries no real prediction. We drop them from both
# placement and arrivals.
#
# DELETED is filtered as of gtfs-realtime-bindings 2.1.0 (the pin in
# requirements.lock), whose trip enum carries DELETED=7. Under the old 2.0.0
# binding it was not: proto2's closed-enum decoding coerced an unknown wire
# value to the field default, so a real DELETED=7 read as SCHEDULED=0 and
# slipped past this set. The getattr below was written for exactly this
# upgrade: it resolved to a collision-safe -1 sentinel under 2.0.0 and now
# resolves the real value, activating the filter with no logic change (getattr
# is kept, rather than a direct attribute read, so an older binding degrades to
# the sentinel instead of raising at import). NEW (2.1.0's other addition)
# marks a trip new relative to the static schedule, the same family as ADDED;
# ADDED trips run and are not dropped, so NEW is deliberately not dropped
# either, and unfiltered NEW is not the bug this pin fixed.
_TRIP_SR = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship
_STOP_SR = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship
_DROP_TRIP_RELATIONSHIPS = frozenset({_TRIP_SR.CANCELED, getattr(_TRIP_SR, "DELETED", -1)})
_DROP_STOP_RELATIONSHIPS = frozenset({_STOP_SR.SKIPPED, _STOP_SR.NO_DATA})


def _platform_direction(stop_id: str) -> tuple[str | None, str]:
    """(direction, station_id) for a platform stop_id via its N/S suffix.

    NYC platform ids are the parent-station id plus a trailing N or S — the
    same convention used for train direction — so the station id is the
    platform id with that suffix stripped.
    """
    if stop_id.endswith("N"):
        return "Northbound", stop_id[:-1]
    if stop_id.endswith("S"):
        return "Southbound", stop_id[:-1]
    return None, stop_id


def _decode_feed(
    raw: bytes, stops: dict[str, dict], feed_key: str, now: float
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None]:
    """Decode one subway feed into (train placements, per-station arrivals,
    feed_timestamp).

    Parses the protobuf once and walks each trip's stop_time_updates for both
    outputs. The subway feeds carry NYC-specific protobuf extensions; the
    standard bindings keep those as unknown fields, so parsing tolerates them.

    Placement keeps only the next stop and SKIPS not-yet-started trips
    (TRIP_START_GRACE_S) so phantom trains don't pile up at terminals.
    Arrivals deliberately do the OPPOSITE: every still-upcoming resolvable stop
    is recorded with NO unstarted-trip filter, because a train departing its
    origin in 20 minutes is a legitimate future arrival at the stations
    downstream of it — exactly what a rider clicking a station wants to see.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    trains: list[dict] = []
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            continue  # canceled/deleted trip: drop from both placement and arrivals
        trip_id = tu.trip.trip_id or f"{feed_key}:{entity.id}"
        route_id = tu.trip.route_id or None

        # Arrivals: every resolvable, still-upcoming stop (no unstarted filter).
        for stu in tu.stop_time_update:
            if not stu.stop_id or stu.stop_id not in stops:
                continue
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop: no real prediction
            t = _stop_time(stu)
            if t is None or t < now - 60:  # same just-passed grace as placement
                continue
            direction, station_id = _platform_direction(stu.stop_id)
            if direction is None:
                continue  # no clean platform direction; not a station arrival
            arrivals[station_id][direction].append(
                {"route_id": route_id, "trip_id": trip_id, "arrival": float(t)}
            )

        # Placement: skip not-yet-started trips, then pick the first stop that
        # exists in the static GTFS and is still upcoming. Track the first
        # resolvable stop as a fallback for trips whose updates carry no times.
        start_ts = _trip_start_ts(tu.trip)
        if start_ts is not None and start_ts > now + TRIP_START_GRACE_S:
            continue
        chosen = None
        chosen_time = None
        first_resolvable = None
        prev_resolvable = None  # the resolvable stop immediately before `chosen`
        last_resolvable = None  # most recent resolvable stop seen while scanning
        saw_timed = False
        for stu in tu.stop_time_update:
            if not stu.stop_id or stu.stop_id not in stops:
                continue  # unknown station (e.g. closed); try the next one
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop: not a real placement target
            if first_resolvable is None:
                first_resolvable = stu
            t = _stop_time(stu)
            if t is None:
                last_resolvable = stu
                continue
            saw_timed = True
            if t >= now - 60:  # small grace for clock skew / just-passed stops
                chosen = stu
                chosen_time = t
                prev_resolvable = last_resolvable  # the station just behind it
                break
            last_resolvable = stu
        if chosen is None and not saw_timed:
            chosen = first_resolvable  # no-times fallback: prev_resolvable stays None
        if chosen is None:
            continue  # trip finished, or nothing resolvable

        # No derivable schedule start: fall back to distrusting a trip still at
        # its first RESOLVABLE stop with that stop far in the future. (Using
        # the first resolvable stop, not stop_time_update[0]: a leading unknown
        # station must not let a far-future trip bypass this cap.)
        if (
            start_ts is None
            and chosen_time is not None
            and chosen is first_resolvable
            and chosen_time > now + MAX_FUTURE_FIRST_STOP_S
        ):
            continue

        stop = stops[chosen.stop_id]
        direction, _ = _platform_direction(chosen.stop_id)
        # Interpolation anchors (v2: route-polyline slice, straight-line fallback). The
        # next/current station stays in latitude/longitude as the static
        # fallback; prev_* describe the most-recently-passed station, null when
        # none precedes the chosen stop or its time is unknown. next_time is the
        # expected time at the chosen station, null on the no-times fallback.
        prev_lat = prev_lon = prev_time = None
        if prev_resolvable is not None:
            prev_stop = stops[prev_resolvable.stop_id]
            prev_lat, prev_lon = prev_stop["lat"], prev_stop["lon"]
            pt = _stop_time(prev_resolvable)
            prev_time = float(pt) if pt is not None else None
        trains.append(
            {
                "trip_id": trip_id,
                "route_id": route_id,
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "stop_id": chosen.stop_id,
                "stop_name": stop["name"],
                "direction": direction,
                "prev_lat": prev_lat,
                "prev_lon": prev_lon,
                "prev_time": prev_time,
                "next_time": float(chosen_time) if chosen_time is not None else None,
            }
        )
    return trains, arrivals, _header_timestamp(feed)


def _decode_trains(raw: bytes, stops: dict[str, dict], feed_key: str, now: float) -> list[dict]:
    """Train placements for one feed — the placement half of _decode_feed.
    Kept as a thin wrapper so the placement logic stays directly testable."""
    return _decode_feed(raw, stops, feed_key, now)[0]


def _aggregate_feeds(
    results: list, stops: dict[str, dict], now: float
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None, dict[str, str]]:
    """Decode every feed result, dedup trips across feeds, and merge arrivals.

    `results` is aligned with SUBWAY_FEED_URLS; each item is decoded protobuf
    bytes or an exception from the fetch. Returns
    (trains, arrivals, feed_timestamp, feed_errors), where feed_timestamp is the
    OLDEST content time across successfully decoded feeds and feed_errors maps
    each failed feed-group key to its raw failure reason (empty when every feed
    decoded).
    """
    trains: list[dict] = []
    seen_trips: set[str] = set()
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    arrival_trips: set[str] = set()
    timestamps: list[float] = []
    feed_errors: dict[str, str] = {}
    for feed_key, result in zip(SUBWAY_FEED_URLS, results):
        if isinstance(result, BaseException):
            feed_errors[feed_key] = str(result)
            continue
        try:
            feed_trains, feed_arrivals, feed_ts = _decode_feed(result, stops, feed_key, now)
        except DecodeError as exc:
            feed_errors[feed_key] = f"undecodable protobuf ({exc})"
            continue
        if feed_ts is not None:
            timestamps.append(feed_ts)
        for train in feed_trains:
            if train["trip_id"] in seen_trips:
                continue
            seen_trips.add(train["trip_id"])
            trains.append(train)
        # Merge arrivals, skipping any trip already contributed by an earlier
        # feed (insurance against a trip appearing in two feeds). Within one
        # feed a trip's arrivals at different stations are all kept, so trip
        # ids are marked seen only after the whole feed is merged.
        feed_trip_ids: set[str] = set()
        for station_id, dirs in feed_arrivals.items():
            for direction, arrs in dirs.items():
                for arr in arrs:
                    if arr["trip_id"] in arrival_trips:
                        continue
                    arrivals[station_id][direction].append(arr)
                    feed_trip_ids.add(arr["trip_id"])
        arrival_trips |= feed_trip_ids

    # Keep the soonest arrivals per direction; the rest are noise on a popup.
    trimmed: dict[str, dict[str, list[dict]]] = {}
    for station_id, dirs in arrivals.items():
        trimmed[station_id] = {}
        for direction, arrs in dirs.items():
            arrs.sort(key=lambda a: a["arrival"])
            trimmed[station_id][direction] = arrs[:ARRIVALS_PER_DIRECTION]
    feed_timestamp = min(timestamps) if timestamps else None
    return trains, trimmed, feed_timestamp, feed_errors


def carry_forward_prev(
    trains: list[dict],
    last_positions: dict,
    key: Callable[[dict], object] | None = None,
) -> dict:
    """Fill missing prev anchors from a persisted previous-station anchor, and return the
    position memory for the next poll.

    `key` maps a train to its memory key; it defaults to the trip_id, which is what the subway
    path passes. The railroad path passes (system, trip_id) because LIRR and MNR trip_id
    namespaces are independent and can collide. The anchor-holding behavior and the limitations
    below are identical for both; only the key differs.

    The feeds usually prune the just-departed stop, so the decode leaves prev_* null
    for most trains. We recover a prev by remembering, per trip, the station it most recently
    departed (the anchor) and holding that anchor fixed for as long as the train is approaching
    the same next stop, advancing it only when the next stop changes. Holding it fixed is the
    point: if the anchor were recomputed only on the transition poll, prev would be supplied on
    just that one poll and the train would snap to its next station on every other poll of the
    segment.

    Each poll, the anchor is: None on first sighting; the previous observation (the now-departed
    station, timed by its last predicted arrival) when the next stop changed; or the carried
    anchor when the next stop is unchanged. prev is synthesized from the anchor only when the
    feed gave no real prev (prev_lat is None), the anchor is a different, time-stamped station,
    the current placement has a next_time, and the bracket is forward (anchor time < next_time).
    A real feed prev is never overwritten. The returned dict is rebuilt from this poll's trains,
    so finished or absent trips are pruned.

    KNOWN LIMITATIONS (straight-line interpolation; both resolved by v2's route-geometry
    slicing, which is along-route and forward-only):
      * Backward slide on stop regression. The anchor advances on ANY next-stop change,
        including a backward one. If the feed revises a held or mis-predicted trip's next stop
        to a station earlier on the line while the new ETA is further out, the forward-time
        guard (anchor time < next_time) still passes and the train is drawn sliding backward
        for one poll. The guard compares ETAs, not station positions, so it cannot catch this;
        a real fix needs the static route stop order (v2).
      * Multi-poll / multi-station gap. The anchor is normally one station back, but after one
        or more missed polls (memory is preserved across a failed poll) or a 2+-station jump in
        a single interval it can be further back, so the straight line cuts across the skipped
        station(s). Self-corrects on the next good poll.
    Both are rare, self-correcting, and we accept them here rather than pull route-order logic
    forward out of v2.
    """
    new_positions: dict = {}
    for train in trains:
        mem_key = train["trip_id"] if key is None else key(train)
        next_time = train["next_time"]
        prev_obs = last_positions.get(mem_key)

        # The departed-station anchor for this poll (held fixed across a segment).
        if prev_obs is None:
            anchor = None  # first sighting: nothing behind it yet
        elif prev_obs["stop_id"] != train["stop_id"]:
            # Next stop advanced: the station approached last poll is the one just
            # departed, timed by its last predicted arrival.
            anchor = {
                "stop_id": prev_obs["stop_id"],
                "lat": prev_obs["lat"],
                "lon": prev_obs["lon"],
                "time": prev_obs["next_time"],
            }
        else:
            anchor = prev_obs["anchor"]  # same segment: keep the anchor fixed

        if (
            train["prev_lat"] is None
            and next_time is not None
            and anchor is not None
            and anchor["time"] is not None
            and anchor["stop_id"] != train["stop_id"]
            and anchor["time"] < next_time
        ):
            train["prev_lat"] = anchor["lat"]
            train["prev_lon"] = anchor["lon"]
            train["prev_time"] = anchor["time"]

        new_positions[mem_key] = {
            "stop_id": train["stop_id"],
            "lat": train["latitude"],
            "lon": train["longitude"],
            "next_time": next_time,
            "anchor": anchor,
        }
    return new_positions


async def fetch_subway_trains(
    stops: dict[str, dict], client: httpx.AsyncClient
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None, list[str]]:
    """Fetch all subway feeds concurrently; return (train placements,
    per-station arrivals index, feed_timestamp, failed_feeds).

    failed_feeds is the sorted list of feed-group keys that failed this poll (a
    fetch error or an undecodable protobuf), empty on a fully successful poll.
    It lets the caller report a partial outage instead of silently dropping a
    whole line group. Individual feed failures are logged and skipped so one bad
    feed doesn't take out the endpoint; this raises only when every feed fails.
    The caller owns the client (the polling task holds one for its lifetime).
    """
    now = time.time()

    async def fetch(url: str) -> bytes:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    results = await asyncio.gather(
        *(fetch(url) for url in SUBWAY_FEED_URLS.values()),
        return_exceptions=True,
    )

    trains, arrivals, feed_timestamp, feed_errors = _aggregate_feeds(results, stops, now)
    if feed_errors:
        logger.warning(
            "%d of %d subway feeds failed: %s",
            len(feed_errors),
            len(SUBWAY_FEED_URLS),
            "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items()),
        )
    if len(feed_errors) == len(SUBWAY_FEED_URLS):
        joined = "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items())
        raise RuntimeError(f"All subway feeds failed: {joined}")
    return trains, arrivals, feed_timestamp, sorted(feed_errors)


def _decode_railroad_vehicles(
    raw: bytes, system: str, now: float
) -> tuple[list[dict], float | None]:
    """Decode one railroad feed; return (trains, feed_timestamp).

    feed_timestamp is the feed's content time (FeedHeader.timestamp, MTA's
    clock), or None when the feed omits it. Phase 1 keeps only entities whose
    vehicle carries a position, covering both feed layouts: LIRR puts the vehicle
    in its own entity, MNR combines the trip_update and vehicle in one. Each kept
    train carries its real lat/lon (no station projection needed). An empty
    vehicle route_id is filled from the
    trip_update: MNR's combined entity carries the route on its own trip_update
    (MNR's vehicle.trip holds the train number, not the trip_update's internal
    trip id, so the same-entity read is what fills MNR), while LIRR's separate
    vehicle entity is joined by trip_id to this feed's trip_updates. Coordinates
    are filtered to the railroad box as a sanity guard. The direction and
    interpolation-anchor fields are emitted as None: phase 2 (placing
    position-less trains at their next station) fills them, so the RailroadTrain
    model needs no change then. `now` is unused in phase 1 (no schedule join
    yet); it is kept for parity with the subway decoders and frozen by the golden
    test.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    # trip_id -> route_id from this feed's trip_updates, to fill an empty vehicle
    # route_id in the separate-entity (LIRR) layout. The combined-entity (MNR)
    # layout is handled inline below via the entity's own trip_update.
    route_by_trip: dict[str, str] = {}
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            trip = entity.trip_update.trip
            if trip.trip_id and trip.route_id:
                route_by_trip.setdefault(trip.trip_id, trip.route_id)

    trains: list[dict] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        if not v.HasField("position"):
            continue
        pos = v.position
        if not _in_railroad_box(pos.latitude, pos.longitude):
            continue  # stray out-of-range coordinate; not a real train
        route_id = v.trip.route_id
        if not route_id and entity.HasField("trip_update"):
            route_id = entity.trip_update.trip.route_id  # combined entity (MNR)
        if not route_id:
            route_id = route_by_trip.get(v.trip.trip_id, "")  # by trip_id (LIRR)
        route_id = route_id or None
        trains.append(
            {
                "system": system,
                "trip_id": v.trip.trip_id or entity.id,
                "route_id": route_id,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "bearing": pos.bearing if pos.HasField("bearing") else None,
                "train_num": (v.vehicle.label or v.vehicle.id) or None,
                "stop_id": None,  # GPS trains carry a real position, not a station
                "stop_name": None,
                "direction": None,
                "prev_lat": None,
                "prev_lon": None,
                "prev_time": None,
                "next_time": None,
            }
        )
    return trains, _header_timestamp(feed)


# Railroad direction_id is agency-defined; for the LIRR and MNR the 0/1 binary
# maps to outbound (away from the NYC terminal) and inbound (toward it). MNR omits
# direction_id from its realtime trip_update, so its placed trains get a null
# direction; LIRR populates it.
_RAILROAD_DIRECTION = {0: "Outbound", 1: "Inbound"}

# A generic "toward the city" anchor for direction INFERENCE (see
# _infer_railroad_direction), at Grand Central Terminal (MNR stop_id 1).
# At the metro scale it also stands in for the other NYC rail terminals: LIRR's
# Penn Station and Atlantic Terminal and the west-of-Hudson lines' Hoboken end all
# sit within a few km of it, so distance-to-this-point is a fine proxy for "how
# close to the city" whichever terminal a trip actually runs to.
_NYC_ANCHOR_LAT, _NYC_ANCHOR_LON = 40.752998, -73.977056
# Longitude is compressed by latitude; scale lon deltas so the distance is roughly
# isotropic (the same idea as the frontend's _COS_LAT). Only internally consistent
# radial distance matters here, not true meters.
_ANCHOR_COS_LAT = math.cos(math.radians(40.7))
# Minimum net change in distance-to-anchor (scaled degrees) to commit to an
# inferred direction. ~0.01 degree is roughly 1 km of net radial movement: large
# enough that a near-tie (a cross-radial hop) or a single-resolvable-stop stub
# falls back to "Trains" rather than flapping, and far below the net radial span
# of any real multi-stop inbound/outbound trip on these lines (whose smallest
# inter-station gaps already run ~1 to 2 km, and a directional trip nets many).
_DIRECTION_EPSILON = 0.01


def _dist_to_anchor(lat: float, lon: float) -> float:
    """Isotropic planar distance from (lat, lon) to the NYC anchor, in scaled
    degrees (lon compressed by cos(latitude))."""
    return math.hypot(lat - _NYC_ANCHOR_LAT, (lon - _NYC_ANCHOR_LON) * _ANCHOR_COS_LAT)


def _direction_from_progression(
    first_lat: float, first_lon: float, last_lat: float, last_lon: float
) -> str | None:
    """Infer "Inbound"/"Outbound" from whether a trip's first-to-last resolvable
    stops move toward or away from the NYC anchor, or None when the net radial
    change is under _DIRECTION_EPSILON (near-ties and stubs).

    Pure and terminal-agnostic: it reads only the endpoints' distance to the
    anchor, so it serves MNR (no direction_id) and any direction-less trip without
    knowing which terminal the line runs to.
    """
    delta = _dist_to_anchor(first_lat, first_lon) - _dist_to_anchor(last_lat, last_lon)
    if delta > _DIRECTION_EPSILON:
        return "Inbound"  # ending closer to the city than it started
    if delta < -_DIRECTION_EPSILON:
        return "Outbound"  # ending farther from the city
    return None  # ambiguous: arrivals fall back to the "Trains" bucket, placement stays null


def _infer_railroad_direction(tu, stops: dict[str, dict]) -> str | None:
    """Inferred direction ("Inbound"/"Outbound") for a direction-less trip from
    its stop progression, or None. Uses the first and last RESOLVABLE stops (the
    same stop_id-in-stops and non-dropped-relationship filters the arrivals scan
    applies, but NOT the just-passed grace: the whole trip's endpoints set its
    direction regardless of which stops are still upcoming).

    The caller computes this once per trip and feeds it to BOTH the arrivals
    bucket and the placed train's direction field. A None result maps to the
    "Trains" arrivals bucket but to a null placement direction (the residual
    differs by half). It is a heuristic, not feed data.
    """
    resolvable = [
        stops[stu.stop_id]
        for stu in tu.stop_time_update
        if stu.stop_id
        and stu.stop_id in stops
        and stu.schedule_relationship not in _DROP_STOP_RELATIONSHIPS
    ]
    if len(resolvable) < 2:
        return None  # single resolvable stop (or none): nothing to compare
    first, last = resolvable[0], resolvable[-1]
    return _direction_from_progression(first["lat"], first["lon"], last["lat"], last["lon"])


def _railroad_trip_start_ts(trip) -> float | None:
    """Scheduled start of a railroad trip from start_date + start_time, or None.

    Unlike the subway _trip_start_ts this deliberately does NOT fall back to a
    trip_id prefix: railroad trip_ids are not centiminute-encoded (LIRR
    'GO201_26_6006_2', MNR '3116189'), so that heuristic would derive a wildly
    wrong start and wrongly drop the train as not-yet-started. MNR carries
    start_time so it gets the not-yet-started filter; LIRR omits start_time, so
    this returns None there (no filter). A missing or malformed start_date also
    returns None rather than substituting the wall clock: the subway helper can
    do that because it has a trip_id-prefix fallback, but this is the sole start
    source, and a now()-based start would make placement nondeterministic (the
    golden freezes `now`) and could wrongly drop or keep a train by calendar date.
    The DST caveat noted on _trip_start_ts applies equally here.
    """
    if not trip.start_time:
        return None
    try:
        d = trip.start_date  # YYYYMMDD
        base = datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), tzinfo=NYC_TZ)
    except (ValueError, IndexError):
        return None  # no usable service date: no not-yet-started filter
    try:
        h, m, s = (int(p) for p in trip.start_time.split(":"))
        return (base + timedelta(hours=h, minutes=m, seconds=s)).timestamp()
    except ValueError:
        return None


def _decode_railroad_feed(
    raw: bytes, system: str, stops: dict[str, dict], now: float
) -> tuple[list[dict], dict[str, dict[str, list[dict]]]]:
    """Decode one railroad feed into (train placements, per-station arrivals).

    Mirrors the subway _decode_feed: one parse produces both outputs from the
    same walk of the trip_updates. Placement fills the position-less trains at
    their next station; arrivals index every still-upcoming stop for the station
    click popup.

    PLACEMENT reuses the subway placement rules (drop canceled trips, skip
    skipped/no-data stops, pick the first resolvable still-upcoming stop with a
    just-passed grace, fall back to the first resolvable stop when none carries a
    time, drop a not-yet-started trip). Two railroad differences from the subway
    path: railroad stop_ids have no N/S suffix, so direction comes from the
    realtime trip.direction_id (null when the feed omits it, e.g. MNR), and the
    start time is derived from start_date+start_time only (see
    _railroad_trip_start_ts). A GPS train is never also placed: a trip_update is
    skipped when its OWN entity carries a position (MNR's combined entity) or when
    its trip_id is one a positioned vehicle entity carries (LIRR's split layout).

    ARRIVALS deliberately do the OPPOSITE of placement on two points, matching
    the subway scan: (1) NO not-yet-started filter, because a train departing its
    origin in 20 minutes is a legitimate future arrival at the stations downstream
    of it, and (2) positioned (GPS) trains ARE included: a GPS train still stops
    at stations, and omitting it would hide exactly the best-tracked trains (the
    position-skip guards only the placement half). Arrivals are bucketed by
    direction: LIRR trips read trip.direction_id via _RAILROAD_DIRECTION, while a
    trip with no usable direction_id (all of MNR, plus any LIRR trip missing it)
    has its direction INFERRED from the stop progression toward the NYC anchor
    (_infer_railroad_direction, a heuristic, not feed data). "Trains" is the
    residual bucket for trips whose direction could be neither read nor inferred.
    Each bucket is sorted by arrival time and capped at ARRIVALS_PER_DIRECTION.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    # Trip ids a positioned vehicle entity carries. For LIRR the vehicle entity
    # shares its trip_id with the matching trip_update, so this set skips placing
    # an already-GPS train; for MNR the vehicle trip_id differs from the
    # trip_update's, so the per-entity check below is what catches it instead.
    # KNOWN GAP (not seen in any captured feed): a positioned LIRR-style vehicle
    # with an EMPTY trip_id cannot be joined to its separate trip_update, so that
    # train could be placed (hollow) on top of its GPS marker. Joining by entity
    # id would need a naming convention we cannot rely on, so it is left as-is.
    #
    # label_by_trip: the rider-facing train number from each positioned vehicle
    # entity, keyed by that vehicle's trip_id. LIRR's arrivals come from a
    # trip_update-only entity (no vehicle), so its train number is joined in from
    # the separate positioned vehicle by trip_id, the same shape as route_by_trip
    # in _decode_railroad_vehicles. MNR's combined entity carries the label on the
    # same entity and is read inline below, so its differing vehicle trip_id here
    # simply never matches a trip_update and is harmless.
    positioned_ids: set[str] = set()
    label_by_trip: dict[str, str] = {}
    for entity in feed.entity:
        if entity.HasField("vehicle") and entity.vehicle.HasField("position"):
            v = entity.vehicle
            if v.trip.trip_id:
                positioned_ids.add(v.trip.trip_id)
                label = (v.vehicle.label or v.vehicle.id) or None
                if label:
                    label_by_trip.setdefault(v.trip.trip_id, label)

    trains: list[dict] = []
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            continue  # canceled/deleted trip: drop from both placement and arrivals

        arr_trip_id = tu.trip.trip_id or f"{system}:{entity.id}"
        arr_route_id = tu.trip.route_id or None
        # Direction, computed ONCE per trip and used for BOTH the arrivals bucket
        # and the placed train's direction field below: direction_id if present
        # (LIRR), else the stop-progression inference (a heuristic, not feed data;
        # covers MNR and any direction-less LIRR trip), else None. The residual
        # differs by half: no direction means the "Trains" arrivals bucket but a
        # null placement direction. _infer_railroad_direction is called at most
        # once per trip (only when direction_id is absent).
        direction = (
            _RAILROAD_DIRECTION.get(tu.trip.direction_id)
            if tu.trip.HasField("direction_id")
            else None
        )
        if direction is None:
            direction = _infer_railroad_direction(tu, stops)
        bucket = direction or "Trains"
        # Train number: MNR's combined entity carries it inline (same read as the
        # placement path below); LIRR joins it from the positioned vehicle entity.
        train_num = None
        if entity.HasField("vehicle"):
            train_num = (entity.vehicle.vehicle.label or entity.vehicle.vehicle.id) or None
        if train_num is None and tu.trip.trip_id:
            train_num = label_by_trip.get(tu.trip.trip_id)

        # Arrivals: every resolvable, still-upcoming stop (no unstarted filter,
        # positioned trains included). Railroad stop_ids have no platform suffix,
        # so the stop_id IS the station id and direction comes from the bucket.
        for stu in tu.stop_time_update:
            if not stu.stop_id or stu.stop_id not in stops:
                continue
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop: no real prediction
            t = _stop_time(stu)
            if t is None or t < now - 60:  # same just-passed grace as placement
                continue
            arrivals[stu.stop_id][bucket].append(
                {
                    "route_id": arr_route_id,
                    "trip_id": arr_trip_id,
                    "arrival": float(t),
                    "train_num": train_num,
                }
            )

        # Placement: skip a GPS train (never place it twice). MNR combines
        # trip_update + vehicle in one entity, so a position here means it is
        # GPS-placed; LIRR splits them, so a separate vehicle entity holds this
        # train's position under the same trip_id.
        if entity.HasField("vehicle") and entity.vehicle.HasField("position"):
            continue
        if tu.trip.trip_id and tu.trip.trip_id in positioned_ids:
            continue

        # Not-yet-started filter (MNR carries the start; LIRR has no start_time so
        # start_ts is None and the far-future-first-stop cap applies below).
        start_ts = _railroad_trip_start_ts(tu.trip)
        if start_ts is not None and start_ts > now + TRIP_START_GRACE_S:
            continue

        # Pick the first resolvable, still-upcoming stop. Mirror _decode_feed:
        # track the first resolvable stop (no-times fallback) and the stop just
        # behind the chosen one (the prev anchor).
        chosen = None
        chosen_time = None
        first_resolvable = None
        prev_resolvable = None
        last_resolvable = None
        saw_timed = False
        for stu in tu.stop_time_update:
            if not stu.stop_id or stu.stop_id not in stops:
                continue  # unknown station; try the next one
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop
            if first_resolvable is None:
                first_resolvable = stu
            t = _stop_time(stu)
            if t is None:
                last_resolvable = stu
                continue
            saw_timed = True
            if t >= now - 60:  # small grace for clock skew / just-passed stops
                chosen = stu
                chosen_time = t
                prev_resolvable = last_resolvable
                break
            last_resolvable = stu
        if chosen is None and not saw_timed:
            chosen = first_resolvable  # no-times fallback: prev_resolvable stays None
        if chosen is None:
            continue  # trip finished, or nothing resolvable
        # NOTE: the subway far-future-first-stop cap (MAX_FUTURE_FIRST_STOP_S) is
        # deliberately NOT applied here. It treats "chosen is the first resolvable
        # stop and far in the future" as a not-yet-departed phantom, which holds
        # for subway feeds that list a trip from its origin. The railroad feeds
        # PRUNE already-passed stops, so a running train's first listed stop is
        # simply its next station (often many minutes out), and the cap would drop
        # most running trains. MNR's not-yet-started filter (start_time, above)
        # screens its future-scheduled trips; LIRR carries no start_time, so a
        # not-yet-departed LIRR train cannot be told from a running one and is
        # placed at its next/origin station, which is acceptable for static
        # placement (gliding comes in the next increment).

        stop = stops[chosen.stop_id]
        # `direction` was computed once above: direction_id (LIRR) or the
        # stop-progression inference (a heuristic, not feed data), null when
        # neither. For MNR the popup line this feeds is therefore inferred, not
        # reported. (Unlike the "Trains" arrivals residual, a null direction stays
        # null here rather than becoming a bucket label.)
        # The chosen station is the static-fallback position; prev_* describe the
        # most-recently-passed station (null when none precedes it or its time is
        # unknown); next_time is the predicted time at the chosen station.
        prev_lat = prev_lon = prev_time = None
        if prev_resolvable is not None:
            prev_stop = stops[prev_resolvable.stop_id]
            prev_lat, prev_lon = prev_stop["lat"], prev_stop["lon"]
            pt = _stop_time(prev_resolvable)
            prev_time = float(pt) if pt is not None else None
        # MNR's combined entity keeps a vehicle (just no position) carrying the
        # train number; LIRR's trip_update-only entity has none. (This is the same
        # inline read arrivals uses above, kept separate so placement output stays
        # byte-identical to the pre-arrivals decoder.)
        placed_train_num = None
        if entity.HasField("vehicle"):
            placed_train_num = (entity.vehicle.vehicle.label or entity.vehicle.vehicle.id) or None
        trains.append(
            {
                "system": system,
                "trip_id": tu.trip.trip_id or f"{system}:{entity.id}",
                "route_id": tu.trip.route_id or None,
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "bearing": None,  # placed from schedule, no GPS heading
                "train_num": placed_train_num,
                "stop_id": chosen.stop_id,  # the next/current station the carry-forward keys on
                "stop_name": stop["name"],
                "direction": direction,
                "prev_lat": prev_lat,
                "prev_lon": prev_lon,
                "prev_time": prev_time,
                "next_time": float(chosen_time) if chosen_time is not None else None,
            }
        )

    # Keep the soonest arrivals per bucket; the rest are noise on a popup.
    trimmed: dict[str, dict[str, list[dict]]] = {}
    for station_id, buckets in arrivals.items():
        trimmed[station_id] = {}
        for direction, arrs in buckets.items():
            arrs.sort(key=lambda a: a["arrival"])
            trimmed[station_id][direction] = arrs[:ARRIVALS_PER_DIRECTION]
    return trains, trimmed


def _decode_railroad_placements(
    raw: bytes, system: str, stops: dict[str, dict], now: float
) -> list[dict]:
    """Placed trains for one railroad feed, the placement half of
    _decode_railroad_feed. Kept as a thin wrapper so the placement logic stays
    directly testable and the placement golden calls what it always has."""
    return _decode_railroad_feed(raw, system, stops, now)[0]


async def fetch_railroad_trains(
    client: httpx.AsyncClient,
    railroad_stops: dict[str, dict | None],
) -> tuple[list[dict], dict[str, dict[str, dict[str, list[dict]]]], float | None, list[str]]:
    """Fetch the LIRR and MNR feeds concurrently; return
    (trains, arrivals_by_system, feed_timestamp, failed_feeds).

    Each feed contributes the GPS-positioned trains (_decode_railroad_vehicles)
    plus the position-less trains placed at their next station and a per-station
    arrivals index (both from _decode_railroad_feed, using railroad_stops[system]
    for coordinates; placement and arrivals are skipped for a system whose static
    stops are None, since neither can resolve stop_ids). Trains are deduped by
    (system, trip_id) with the GPS train winning any conflict (GPS is added
    first); the composite key matters because LIRR's and MNR's trip_id namespaces
    are independent. arrivals_by_system is {system: {stop_id: {bucket: [...]}}}
    for only the systems that decoded WITH static stops this poll, so the caller
    can replace those systems' arrivals while keeping a transiently-failed
    system's last-known index (mirrors fetch_subway_trains returning arrivals).

    feed_timestamp comes only from systems whose header is a trustworthy
    freshness signal (RAILROAD_FRESHNESS_SYSTEMS): today just LIRR, whose header
    is the true feed-generation time. MNR's header is a lagging shared clock and
    is deliberately excluded, so it never drives staleness. The value is the
    oldest such trusted header (only LIRR's today, but min-across-trusted stays
    correct if another trusted feed is added later), or None when no trusted feed
    decoded. Mirrors fetch_subway_trains: per-feed failures (a fetch error or
    undecodable protobuf) are logged and skipped, and this raises only when every
    feed fails. failed_feeds is the sorted list of systems that dropped this poll,
    empty on a fully successful poll. The caller owns the client (the polling task
    holds one for its lifetime).
    """
    now = time.time()

    async def fetch(url: str) -> bytes:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    systems = list(RAILROAD_FEED_URLS)
    results = await asyncio.gather(
        *(fetch(RAILROAD_FEED_URLS[s]) for s in systems),
        return_exceptions=True,
    )

    trains: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (system, trip_id)
    timestamps: list[float] = []
    feed_errors: dict[str, str] = {}
    raw_by_system: dict[str, bytes] = {}  # successfully decoded, kept for placement
    # GPS pass first, so a positioned train wins its (system, trip_id) key.
    for system, result in zip(systems, results):
        if isinstance(result, BaseException):
            feed_errors[system] = str(result)
            continue
        try:
            gps, feed_ts = _decode_railroad_vehicles(result, system, now)
        except DecodeError as exc:
            feed_errors[system] = f"undecodable protobuf ({exc})"
            continue
        raw_by_system[system] = result
        # Only trust a freshness-authoritative system's header (see
        # RAILROAD_FRESHNESS_SYSTEMS); MNR's lagging shared clock is ignored.
        if feed_ts is not None and system in RAILROAD_FRESHNESS_SYSTEMS:
            timestamps.append(feed_ts)
        for train in gps:
            key = (system, train["trip_id"])
            if key in seen:
                continue
            seen.add(key)
            trains.append(train)
    # Placement + arrivals pass: both need static stops to resolve stop_ids, so
    # both are skipped for a system whose stops are None. One combined decode per
    # system yields the placements (merged, GPS-wins) and its arrivals index.
    arrivals_by_system: dict[str, dict[str, dict[str, list[dict]]]] = {}
    for system, result in raw_by_system.items():
        stops = (railroad_stops or {}).get(system)
        if not stops:
            continue
        placed, arrivals = _decode_railroad_feed(result, system, stops, now)
        arrivals_by_system[system] = arrivals
        for train in placed:
            key = (system, train["trip_id"])
            if key in seen:
                continue
            seen.add(key)
            trains.append(train)

    if feed_errors:
        logger.warning(
            "%d of %d railroad feeds failed: %s",
            len(feed_errors),
            len(RAILROAD_FEED_URLS),
            "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items()),
        )
    if len(feed_errors) == len(RAILROAD_FEED_URLS):
        joined = "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items())
        raise RuntimeError(f"All railroad feeds failed: {joined}")
    feed_timestamp = min(timestamps) if timestamps else None
    return trains, arrivals_by_system, feed_timestamp, sorted(feed_errors)


# ---- PATH (Port Authority Trans-Hudson) ----

# Community GTFS-RT bridge (jamespfennell/path-train-gtfs-realtime, sourced
# from the PANYNJ API). Unofficial with no SLA, so the URL is an env override:
# pointing at a self-hosted bridge is a config change, not a code change.
PATH_RT_URL = os.getenv("PATH_RT_URL", "https://path.transitdata.nyc/gtfsrt")

# Sent on every bridge request. The bridge is a community service; a
# descriptive User-Agent lets its maintainer see who is polling and reach out,
# instead of an anonymous default UA.
PATH_USER_AGENT = "nyc-transit-live (+https://github.com/rosenfieldben/nyc-transit-live)"

# PATH direction_id semantics, verified against static trips.txt across all 7
# routes via a headsign-by-direction tally (2026-07-05): 0 runs toward the New
# Jersey terminal (Newark, Hoboken, Journal Square, Harrison, Grove St), 1
# toward the New York terminal (33rd Street, World Trade Center). These labels
# are the arrivals bucket keys AND the placed train's direction field.
_PATH_DIRECTION = {0: "To New Jersey", 1: "To New York"}


def _decode_path_feed(
    raw: bytes, stops: dict[str, dict], now: float
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None]:
    """Decode the PATH bridge feed into (train placements, per-station
    arrivals, feed_timestamp).

    The bridge serves TripUpdate entities only (no VehiclePositions), each
    observed carrying EXACTLY ONE stop_time_update: the next arrival. The scan
    below does not assume that: it takes the FIRST resolvable, still-upcoming
    stop_time_update and ignores any later ones, so a bridge that starts
    emitting full stop lists neither breaks the decode nor changes its output
    shape. One consequence is deliberate: arrivals index only that one chosen
    stop per trip (there is nothing downstream to index today).

    Stop ids are the PARENT station ids from the 13a static stops table, so
    `stops` is app.state.path_stops and the stop_id IS the station id (no
    platform suffix, no child folding needed). An entity whose stop ids do not
    resolve there is skipped; the skips are counted and logged at debug level
    (a persistent count would mean the static table and the bridge disagree).

    PLACEMENT mirrors the railroad conventions: drop canceled/deleted trips
    and skipped/no-data stops, keep a just-passed grace of 60s, fall back to
    the first resolvable stop when no stop carries a time (next_time null),
    and drop a trip whose only timed stops are all past. There is no
    not-yet-started filter: the bridge emits only live next-arrival
    predictions (no start_date/start_time to derive one from), so the subway
    phantom problem cannot arise. prev_* is ALWAYS null in this phase: the
    carry-forward anchor memory keys on trip ids, and PATH bridge trip ids do
    not survive an upstream refresh (see path_static's module docstring), so
    an anchor keyed on them would silently mismatch; 13d owns a synthetic
    identity before any anchor is trustworthy.

    ARRIVALS are bucketed by direction_id ("To New York" / "To New Jersey",
    see _PATH_DIRECTION), with "Trains" as the residual for a direction-less
    trip, matching the railroad bucket discipline (keys present only when
    populated, sorted soonest-first, capped at ARRIVALS_PER_DIRECTION). Rows
    carry trip_id for shape parity with the railroad arrivals, but PATH trip
    ids are display-poor AND unstable across upstream refreshes: the frontend
    must never key on them or show them.

    feed_timestamp is the bridge's WRITE time, a fair "is the bridge alive"
    signal. It advances even when the entity content is unchanged, because the
    bridge regenerates (~15s) faster than the upstream refreshes: consecutive
    polls with identical content are NORMAL for PATH, never a stuck-feed
    signal, so there is deliberately no content-unchanged staleness heuristic
    here or anywhere downstream.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    trains: list[dict] = []
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    unresolved_entities = 0
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            continue  # canceled/deleted trip: drop from both placement and arrivals
        trip_id = tu.trip.trip_id or f"PATH:{entity.id}"
        route_id = tu.trip.route_id or None
        direction = (
            _PATH_DIRECTION.get(tu.trip.direction_id) if tu.trip.HasField("direction_id") else None
        )

        # One scan picks the single stop both outputs use: the first
        # resolvable, still-upcoming stop_time_update (today the only one).
        chosen = None
        chosen_time = None
        first_resolvable = None
        saw_timed = False
        saw_any_stop = False
        for stu in tu.stop_time_update:
            saw_any_stop = True
            if not stu.stop_id or stu.stop_id not in stops:
                continue  # not a parent station id we know; try the next one
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop: no real prediction
            if first_resolvable is None:
                first_resolvable = stu
            t = _stop_time(stu)
            if t is None:
                continue
            saw_timed = True
            if t >= now - 60:  # same just-passed grace as the other systems
                chosen = stu
                chosen_time = t
                break
        if chosen is None and not saw_timed:
            chosen = first_resolvable  # no-times fallback: next_time stays null
        if chosen is None:
            if saw_any_stop and first_resolvable is None:
                unresolved_entities += 1  # every stop id failed to resolve
            continue  # unresolvable, or its only timed stops are all past

        stop = stops[chosen.stop_id]
        if chosen_time is not None:
            bucket = direction or "Trains"
            arrivals[chosen.stop_id][bucket].append(
                {"route_id": route_id, "trip_id": trip_id, "arrival": float(chosen_time)}
            )
        trains.append(
            {
                "trip_id": trip_id,
                "route_id": route_id,
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "stop_id": chosen.stop_id,
                "stop_name": stop["name"],
                "direction": direction,
                "prev_lat": None,  # no carry-forward in 13b (unstable trip ids)
                "prev_lon": None,
                "prev_time": None,
                "next_time": float(chosen_time) if chosen_time is not None else None,
            }
        )

    if unresolved_entities:
        logger.debug(
            "PATH decode skipped %d entities whose stop ids resolve to no parent station",
            unresolved_entities,
        )

    # Keep the soonest arrivals per bucket; the rest are noise on a popup.
    trimmed: dict[str, dict[str, list[dict]]] = {}
    for station_id, buckets in arrivals.items():
        trimmed[station_id] = {}
        for direction_key, arrs in buckets.items():
            arrs.sort(key=lambda a: a["arrival"])
            trimmed[station_id][direction_key] = arrs[:ARRIVALS_PER_DIRECTION]
    return trains, trimmed, _header_timestamp(feed)


async def fetch_path_trains(
    client: httpx.AsyncClient, path_stops: dict[str, dict]
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None]:
    """Fetch the PATH bridge feed; return (trains, arrivals_by_stop,
    feed_timestamp).

    Single feed, so unlike fetch_subway_trains / fetch_railroad_trains there
    is no partial-failure aggregation: an HTTP error or undecodable body
    propagates for the caller (main._refresh_path) to record, the same way the
    single-feed bus fetch behaves. The caller owns the client and must only
    call this once path_stops is populated (placement and arrivals both
    resolve parent station ids through it). See _decode_path_feed for the
    duplicate-generation and unstable-trip-id caveats.
    """
    now = time.time()
    resp = await client.get(PATH_RT_URL, headers={"User-Agent": PATH_USER_AGENT})
    resp.raise_for_status()
    return _decode_path_feed(resp.content, path_stops, now)


# ---- Service alerts ----

_ALERT_EFFECT = gtfs_realtime_pb2.Alert.Effect
_ALERT_CAUSE = gtfs_realtime_pb2.Alert.Cause


def _alert_window_status(
    periods: list[tuple[int | None, int | None]], now: float
) -> tuple[str, int | None, int | None]:
    """Classify an alert's active_period list against `now`, returning
    (status, starts_at, ends_at):

      "active": some period covers now; starts_at/ends_at are that period's bounds
      "future": no period covers now but at least one starts after now (planned work)
      "ended":  no period covers now and none is still upcoming (all elapsed)

    Open bounds follow the feed facts: an EMPTY period list means the alert is
    always active (no window constraint); a None start is open on the left; a None
    end (the decode maps an end of 0 or unset to None) is open-ended. A period
    covers now on the half-open interval [start, end), matching the GTFS-RT spec.
    "future" is split out from "ended" because only not-yet-active planned work is
    worth counting for /api/status; a fully elapsed alert is just gone.
    """
    if not periods:
        return "active", None, None
    covering: list[tuple[int | None, int | None]] = []
    has_future = False
    for start, end in periods:
        started = start is None or now >= start
        not_ended = end is None or now < end
        if started and not_ended:
            covering.append((start, end))
        elif start is not None and now < start:
            has_future = True  # begins later: planned, not yet active
    if covering:
        # When several periods cover now, report the one that started earliest (the
        # alert has been active longest); an open start sorts first.
        covering.sort(key=lambda p: float("-inf") if p[0] is None else p[0])
        start, end = covering[0]
        return "active", start, end
    return ("future", None, None) if has_future else ("ended", None, None)


def _translated(ts) -> str | None:
    """First English translation of a TranslatedString, else the first available,
    else None. The text is kept VERBATIM (subway alerts embed route tokens like
    [Q]); normalizing or stripping it is 12b's rendering concern, not the decode's."""
    translations = ts.translation
    if not translations:
        return None
    for tr in translations:
        if tr.language and tr.language.lower().startswith("en"):
            return tr.text
    return translations[0].text


def _enum_name(enum_wrapper, value: int) -> str:
    """GTFS-RT enum value to its name, falling back to the raw int as a string for
    a value newer than the bundled binding (rather than raising on an unknown)."""
    try:
        return enum_wrapper.Name(value)
    except ValueError:
        return str(value)


def _decode_alerts(raw: bytes, feed_key: str, now: float) -> tuple[list[dict], int]:
    """Decode one service-alerts feed into (active alerts, suppressed_count).

    Returns one plain dict per alert that is ACTIVE at `now`:
      {id, system, header, description, effect, cause, routes, stops,
       starts_at, ends_at}
    where routes/stops are the informed_entity selectors deduped in first-seen
    order (an alert's informed_entity list mixes route-only, stop-only, and
    both-carrying selectors, each with an agency_id we do not need to keep here),
    and starts_at/ends_at come from the period covering now (ends_at None when
    open-ended). Subway stop selectors are PARENT-STATION ids (e.g. "R20", "245"),
    the same id space as the static station index, so 12b can join them directly.

    Not-yet-active planned work (a "future" window) is excluded from the list but
    counted into suppressed_count, so /api/status can report how much upcoming work
    is being held back; fully elapsed alerts are dropped and not counted. `now` is
    frozen by the golden test for determinism.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    alerts: list[dict] = []
    suppressed = 0
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        # Map each TimeRange to (start, end); an end of 0 or unset is open-ended
        # (None), a missing start is open on the left (None).
        periods = [
            (
                tr.start if tr.HasField("start") else None,
                tr.end if (tr.HasField("end") and tr.end) else None,
            )
            for tr in alert.active_period
        ]
        status, starts_at, ends_at = _alert_window_status(periods, now)
        if status == "ended":
            continue
        if status == "future":
            suppressed += 1
            continue

        routes: list[str] = []
        stops: list[str] = []
        for sel in alert.informed_entity:
            if sel.route_id and sel.route_id not in routes:
                routes.append(sel.route_id)
            if sel.stop_id and sel.stop_id not in stops:
                stops.append(sel.stop_id)

        alerts.append(
            {
                "id": entity.id,
                "system": feed_key,
                "header": _translated(alert.header_text),
                "description": _translated(alert.description_text),
                "effect": _enum_name(_ALERT_EFFECT, alert.effect),
                "cause": _enum_name(_ALERT_CAUSE, alert.cause),
                "routes": routes,
                "stops": stops,
                "starts_at": starts_at,
                "ends_at": ends_at,
            }
        )
    return alerts, suppressed


async def fetch_service_alerts(client: httpx.AsyncClient) -> tuple[list[dict], int, list[str]]:
    """Fetch all four alert feeds concurrently; return
    (active alerts, suppressed_count, failed_feeds).

    Mirrors fetch_subway_trains: per-feed failures (a fetch error or undecodable
    protobuf) are logged and skipped so one bad feed does not drop every alert,
    and this raises only when EVERY feed fails. failed_feeds is the sorted list of
    feed keys that dropped this poll, empty on a fully successful poll. The caller
    owns the client. `now` is captured once so all four feeds filter against the
    same instant.
    """
    now = time.time()

    async def fetch(url: str) -> bytes:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content

    keys = list(ALERT_FEED_URLS)
    results = await asyncio.gather(
        *(fetch(ALERT_FEED_URLS[k]) for k in keys),
        return_exceptions=True,
    )

    alerts: list[dict] = []
    suppressed = 0
    feed_errors: dict[str, str] = {}
    for key, result in zip(keys, results):
        if isinstance(result, BaseException):
            feed_errors[key] = str(result)
            continue
        try:
            decoded, feed_suppressed = _decode_alerts(result, key, now)
        except DecodeError as exc:
            feed_errors[key] = f"undecodable protobuf ({exc})"
            continue
        alerts.extend(decoded)
        suppressed += feed_suppressed

    if feed_errors:
        logger.warning(
            "%d of %d alert feeds failed: %s",
            len(feed_errors),
            len(ALERT_FEED_URLS),
            "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items()),
        )
    if len(feed_errors) == len(ALERT_FEED_URLS):
        joined = "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items())
        raise RuntimeError(f"All alert feeds failed: {joined}")
    return alerts, suppressed, sorted(feed_errors)
