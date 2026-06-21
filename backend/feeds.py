"""Fetch and decode the MTA GTFS-Realtime feeds (bus positions, subway trips,
commuter-rail / railroad GPS positions)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
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
# running; a SKIPPED/NO_DATA stop carries no real prediction. We drop them from
# both placement and arrivals.
#
# DELETED is included for forward-compatibility but does NOT take effect with
# this binding: gtfs-realtime-bindings 2.0.0 predates DELETED in the trip enum,
# so getattr resolves it to the -1 sentinel (collision-safe) AND, more to the
# point, a real DELETED=7 on the wire is coerced to SCHEDULED=0 by proto2's
# closed-enum decoding — so a DELETED trip currently reads as SCHEDULED and is
# NOT filtered. Reliably dropping it would need a binding upgrade (after which
# getattr would resolve DELETED and this check would work) or raw unknown-field
# parsing; neither is worth it for a rare case. CANCELED (value 3, present in
# the binding) is filtered correctly.
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


def carry_forward_prev(trains: list[dict], last_positions: dict[str, dict]) -> dict[str, dict]:
    """Fill missing prev anchors from a persisted previous-station anchor, and return the
    position memory for the next poll.

    The NYCT feeds usually prune the just-departed stop, so _decode_feed leaves prev_* null
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
    new_positions: dict[str, dict] = {}
    for train in trains:
        trip_id = train["trip_id"]
        next_time = train["next_time"]
        prev_obs = last_positions.get(trip_id)

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

        new_positions[trip_id] = {
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
    vehicle carries a position. This covers
    both feed layouts: LIRR puts the vehicle in its own entity, MNR combines the
    trip_update and vehicle in one. Each kept train carries its real lat/lon (no
    station projection needed). An empty vehicle route_id is filled from the
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
                "direction": None,
                "prev_lat": None,
                "prev_lon": None,
                "prev_time": None,
                "next_time": None,
            }
        )
    return trains, _header_timestamp(feed)


async def fetch_railroad_trains(
    client: httpx.AsyncClient,
) -> tuple[list[dict], float | None, list[str]]:
    """Fetch the LIRR and MNR feeds concurrently; return
    (trains, feed_timestamp, failed_feeds).

    feed_timestamp is the OLDEST content time across successfully decoded feeds
    (the combined view is only as fresh as its stalest member), or None if none
    decoded. Mirrors fetch_subway_trains: per-feed failures (a fetch error or
    undecodable protobuf) are logged and skipped, trains are de-duped by trip_id
    across the two systems (a guard, since the namespaces shouldn't collide), and
    this raises only when every feed fails. failed_feeds is the sorted list of
    systems that dropped this poll, empty on a fully successful poll. The caller
    owns the client (the polling task holds one for its lifetime).
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
    seen_trips: set[str] = set()
    timestamps: list[float] = []
    feed_errors: dict[str, str] = {}
    for system, result in zip(systems, results):
        if isinstance(result, BaseException):
            feed_errors[system] = str(result)
            continue
        try:
            decoded, feed_ts = _decode_railroad_vehicles(result, system, now)
        except DecodeError as exc:
            feed_errors[system] = f"undecodable protobuf ({exc})"
            continue
        if feed_ts is not None:
            timestamps.append(feed_ts)
        for train in decoded:
            if train["trip_id"] in seen_trips:
                continue
            seen_trips.add(train["trip_id"])
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
    return trains, feed_timestamp, sorted(feed_errors)
