"""Fetch and decode the MTA GTFS-Realtime feeds (bus positions, subway trips)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

logger = logging.getLogger("uvicorn.error")

# The .env file lives in the project root, one level up from backend/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

VEHICLE_POSITIONS_URL = "https://gtfsrt.prod.obanyc.com/vehiclePositions"

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


async def fetch_vehicle_positions() -> list[dict]:
    """Fetch the feed, decode the protobuf, and return one dict per vehicle.

    Each dict has: id, route_id, latitude, longitude, bearing. Entities without
    a position are skipped; bearing is None when the feed doesn't report it.
    """
    async with httpx.AsyncClient(timeout=30) as client:
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
        vehicles.append(
            {
                "id": v.vehicle.id or entity.id,
                "route_id": v.trip.route_id or None,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                "bearing": pos.bearing if pos.HasField("bearing") else None,
            }
        )
    return vehicles


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


def _decode_trains(raw: bytes, stops: dict[str, dict], feed_key: str, now: float) -> list[dict]:
    """Decode one subway feed's trip updates into train placements.

    Each train is placed at its next upcoming stop. The subway feeds carry
    NYC-specific protobuf extensions; the standard bindings keep those as
    unknown fields, so parsing tolerates them — we just can't read them.
    Direction comes from the stop_id suffix instead (NYC convention: trailing
    N/S on every platform id).
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    trains: list[dict] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update

        # Skip trips that haven't started yet (they appear in the feed well
        # before departure and would otherwise sit as phantoms at terminals).
        start_ts = _trip_start_ts(tu.trip)
        if start_ts is not None and start_ts > now + TRIP_START_GRACE_S:
            continue

        # Pick the first stop that exists in the static GTFS and is still
        # upcoming. Track the first resolvable stop as a fallback for trips
        # whose updates carry no times at all.
        chosen = None
        chosen_time = None
        first_resolvable = None
        saw_timed = False
        for stu in tu.stop_time_update:
            if not stu.stop_id or stu.stop_id not in stops:
                continue  # unknown station (e.g. closed); try the next one
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

        # No derivable schedule start: fall back to distrusting a trip still
        # at its first listed stop with that stop far in the future.
        if (
            start_ts is None
            and chosen_time is not None
            and len(tu.stop_time_update)
            and chosen is tu.stop_time_update[0]
            and chosen_time > now + MAX_FUTURE_FIRST_STOP_S
        ):
            continue

        stop = stops[chosen.stop_id]
        if chosen.stop_id.endswith("N"):
            direction = "Northbound"
        elif chosen.stop_id.endswith("S"):
            direction = "Southbound"
        else:
            direction = None

        trains.append(
            {
                "trip_id": tu.trip.trip_id or f"{feed_key}:{entity.id}",
                "route_id": tu.trip.route_id or None,
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "stop_id": chosen.stop_id,
                "stop_name": stop["name"],
                "direction": direction,
            }
        )
    return trains


async def fetch_subway_trains(stops: dict[str, dict]) -> list[dict]:
    """Fetch all subway feeds concurrently and place each active train.

    Individual feed failures are logged and skipped so one bad feed doesn't
    take out the endpoint; raises only when every feed fails.
    """
    now = time.time()
    async with httpx.AsyncClient(timeout=30) as client:

        async def fetch(url: str) -> bytes:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

        results = await asyncio.gather(
            *(fetch(url) for url in SUBWAY_FEED_URLS.values()),
            return_exceptions=True,
        )

    trains: list[dict] = []
    seen_trips: set[str] = set()
    errors: list[str] = []
    for feed_key, result in zip(SUBWAY_FEED_URLS, results):
        if isinstance(result, BaseException):
            errors.append(f"{feed_key}: {result}")
            continue
        try:
            decoded = _decode_trains(result, stops, feed_key, now)
        except DecodeError as exc:
            errors.append(f"{feed_key}: undecodable protobuf ({exc})")
            continue
        for train in decoded:
            if train["trip_id"] in seen_trips:
                continue
            seen_trips.add(train["trip_id"])
            trains.append(train)

    if errors:
        logger.warning(
            "%d of %d subway feeds failed: %s",
            len(errors), len(SUBWAY_FEED_URLS), "; ".join(errors),
        )
    if len(errors) == len(SUBWAY_FEED_URLS):
        raise RuntimeError(f"All subway feeds failed: {'; '.join(errors)}")
    return trains
