"""Fetch and decode the MTA GTFS-Realtime feeds (bus positions, subway trips)."""

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
                continue
            saw_timed = True
            if t >= now - 60:  # small grace for clock skew / just-passed stops
                chosen = stu
                chosen_time = t
                break
        if chosen is None and not saw_timed:
            chosen = first_resolvable
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
        trains.append(
            {
                "trip_id": trip_id,
                "route_id": route_id,
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "stop_id": chosen.stop_id,
                "stop_name": stop["name"],
                "direction": direction,
            }
        )
    return trains, arrivals, _header_timestamp(feed)


def _decode_trains(raw: bytes, stops: dict[str, dict], feed_key: str, now: float) -> list[dict]:
    """Train placements for one feed — the placement half of _decode_feed.
    Kept as a thin wrapper so the placement logic stays directly testable."""
    return _decode_feed(raw, stops, feed_key, now)[0]


def _aggregate_feeds(
    results: list, stops: dict[str, dict], now: float
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None, list[str]]:
    """Decode every feed result, dedup trips across feeds, and merge arrivals.

    `results` is aligned with SUBWAY_FEED_URLS; each item is decoded protobuf
    bytes or an exception from the fetch. Returns
    (trains, arrivals, feed_timestamp, errors), where feed_timestamp is the
    OLDEST content time across successfully decoded feeds — the freshness of
    the combined view is bounded by its stalest member.
    """
    trains: list[dict] = []
    seen_trips: set[str] = set()
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    arrival_trips: set[str] = set()
    timestamps: list[float] = []
    errors: list[str] = []
    for feed_key, result in zip(SUBWAY_FEED_URLS, results):
        if isinstance(result, BaseException):
            errors.append(f"{feed_key}: {result}")
            continue
        try:
            feed_trains, feed_arrivals, feed_ts = _decode_feed(result, stops, feed_key, now)
        except DecodeError as exc:
            errors.append(f"{feed_key}: undecodable protobuf ({exc})")
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
    return trains, trimmed, feed_timestamp, errors


async def fetch_subway_trains(
    stops: dict[str, dict], client: httpx.AsyncClient
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None]:
    """Fetch all subway feeds concurrently; return (train placements,
    per-station arrivals index, feed_timestamp).

    feed_timestamp is the oldest content time across decoded feeds. Individual
    feed failures are logged and skipped so one bad feed doesn't take out the
    endpoint; raises only when every feed fails. The caller owns the client
    (the polling task holds one for its lifetime).
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

    trains, arrivals, feed_timestamp, errors = _aggregate_feeds(results, stops, now)
    if errors:
        logger.warning(
            "%d of %d subway feeds failed: %s",
            len(errors),
            len(SUBWAY_FEED_URLS),
            "; ".join(errors),
        )
    if len(errors) == len(SUBWAY_FEED_URLS):
        raise RuntimeError(f"All subway feeds failed: {'; '.join(errors)}")
    return trains, arrivals, feed_timestamp
