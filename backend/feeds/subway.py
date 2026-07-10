"""Subway GTFS-RT trip feeds: the keyless per-line-group feed URLs and the
decode into placed trains plus a per-station arrivals index (_decode_feed /
_aggregate_feeds / fetch_subway_trains), with platform-direction parsing."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from feeds.shared import (
    _DROP_STOP_RELATIONSHIPS,
    _DROP_TRIP_RELATIONSHIPS,
    MAX_FUTURE_FIRST_STOP_S,
    TRIP_START_GRACE_S,
    _header_timestamp,
    _stop_time,
    _trim_arrivals,
    _trip_start_ts,
    logger,
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

    feed_timestamp = min(timestamps) if timestamps else None
    return trains, _trim_arrivals(arrivals), feed_timestamp, feed_errors


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
