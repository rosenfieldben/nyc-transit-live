"""NYC Ferry realtime (14b): the two GTFS-RT endpoints (VehiclePositions +
TripUpdates), the GPS boat decode, and the per-dock arrivals index.

Two feeds, one poll cycle (fetch_ferry_data): VehiclePositions gives the live
boats (real GPS, unlike the schedule-placed PATH trains), TripUpdates gives the
per-dock arrivals. Both feeds' trip descriptors carry an EMPTY route_id, so a
boat's or arrival's route is recovered by joining its trip_id through 14a's
static trip -> route map (ferry_static["trips"]); that makes the static a hard
warmup dependency of this decoder. Verified live 2026-07-09 (boats in service):

  - Vehicle ids are STABLE across polls and labels carry hull names (H201).
  - current_status is populated (STOPPED_AT when docked, IN_TRANSIT_TO under
    way): exposed raw, it is display gold for 14c.
  - speed is populated (0-13 observed): GTFS-RT Position.speed is defined as
    meters per second, and that 0-13 m/s range is 0-25 kn, matching NYC Ferry's
    hull speeds (~25 kn max), the sanity check that Connexionz follows the spec.
    Passed through RAW in m/s; the frontend converts to knots for display (H4).
  - bearing is ALWAYS 0.0 (unpopulated): omitted from the payload entirely
    rather than served as a lie.
  - Trip ids are real, stable schedule ids (25/25 assigned trips joined the
    static), so trip_id IS exposed, unlike PATH's unstable hashes.
  - TripUpdates carry NO direction_id, so arrivals bucket BY ROUTE (the static
    route name), which also reads better at a multi-route dock.

Two drop rules, deliberately different in severity:

  - A vessel with an EMPTY trip_id is DEADHEADING (repositioning, not in
    service): dropped from boats and arrivals with a debug count, the
    PATH-unresolved-stop precedent.
  - A vessel whose (present) trip_id does NOT join the static map keeps its
    position with route_id null and a debug count: a positioned vessel must
    never be dropped over a metadata miss (a stale static between refreshes),
    so it stays on the map, just uncolored, until the join heals.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from google.transit import gtfs_realtime_pb2

from feeds.shared import (
    _DROP_STOP_RELATIONSHIPS,
    _DROP_TRIP_RELATIONSHIPS,
    ARRIVALS_PER_DIRECTION,
    _header_timestamp,
    _in_nyc,
    _stop_time,
    logger,
)

# The two realtime endpoints share this host and path base with the 14a static
# utility URL. The 14b probe reached them over http, and 14a settled on https for
# the same host (its static loader is https-only), so this tries https first and
# falls back to http only if https fails, keeping both schemes working. Split out
# so a probe or a host move is a one-line change and so tests can point a fake
# transport at them.
FERRY_RT_HOST = "nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx"
FERRY_VEHICLE_ENDPOINT = "vehicleposition"
FERRY_TRIPUPDATE_ENDPOINT = "tripupdate"

# Residual arrivals bucket for a trip that carries no resolvable route (a join
# miss): the arrival is still shown, just ungrouped, the same "never drop over a
# metadata miss" rule the boats follow. Analogous to the railroad "Trains"
# residual. Real feeds join ~100%, so this is a rare safety net, not a norm.
_UNKNOWN_ROUTE_BUCKET = "Ferry"

# VehicleStopStatus enum, read for the boat's current_status (STOPPED_AT etc.).
_VEHICLE_STOP_STATUS = gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus


def _status_name(vehicle) -> str | None:
    """The vehicle's current_status enum name (STOPPED_AT / IN_TRANSIT_TO /
    INCOMING_AT), or None when the feed omits it. Exposed raw for 14c: STOPPED_AT
    means docked, the rest mean under way. An unknown future enum value passes
    through as its integer string rather than raising, matching the alerts
    decoder's tolerance for a value newer than the bundled binding."""
    if not vehicle.HasField("current_status"):
        return None
    try:
        return _VEHICLE_STOP_STATUS.Name(vehicle.current_status)
    except ValueError:
        return str(vehicle.current_status)


def _ferry_route_for_trip(
    trip_id: str, trips: dict[str, dict], routes: dict[str, dict]
) -> tuple[str | None, str | None]:
    """(route_id, route_name) for a realtime trip_id via the static trip -> route
    join, or (None, None) on a miss.

    The realtime trip descriptor carries an empty route_id, so the route is
    recovered by looking trip_id up in the 14a static trips table (route_id) and
    then that route in the routes table (its rider-facing long name). A miss (the
    trip_id is not in the static, or the static row has no route_id) returns
    (None, None); the caller keeps the boat/arrival and counts the miss rather
    than dropping it."""
    trip = trips.get(trip_id)
    if not trip:
        return None, None
    route_id = trip.get("route_id") or None
    if not route_id:
        return None, None
    info = routes.get(route_id) or {}
    route_name = info.get("long_name") or info.get("short_name") or route_id
    return route_id, route_name


def _decode_ferry_vehicles(
    raw: bytes, trips: dict[str, dict], routes: dict[str, dict], now: float
) -> tuple[list[dict], float | None, int, int]:
    """Decode the VehiclePositions feed into (boats, feed_timestamp,
    deadhead_count, join_miss_count).

    Each boat carries its REAL GPS position (no station projection, unlike the
    schedule-placed PATH trains) plus id, label (hull name), trip_id, route_id
    (or null on a join miss), speed, status, and updated_at. bearing is omitted
    (the feed always reports 0.0, so serving it would be a lie). now is accepted
    for signature parity with the other decoders and is currently unused here (a
    boat's position is not time-filtered); the golden freezes it regardless.

    Deadheads (empty trip_id) are dropped and counted; a present-but-unjoinable
    trip_id keeps the boat with route_id null and is counted separately, since a
    positioned vessel must never be dropped over a route-metadata miss."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    boats: list[dict] = []
    deadheads = 0
    join_misses = 0
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        if not vehicle.HasField("position"):
            continue  # no GPS to place; nothing to draw
        pos = vehicle.position
        if not _in_nyc(pos.latitude, pos.longitude):
            # Sanity guard, not a service-area definition (the same one the bus
            # and subway decoders apply): every NYC Ferry dock sits well inside
            # this box, so an out-of-range coordinate (e.g. a 0,0 depot/test
            # value) is not a real boat and would only scatter a marker. This is
            # about an invalid POSITION, distinct from the route-miss rule below,
            # which keeps a validly-positioned boat.
            continue
        trip_id = vehicle.trip.trip_id
        if not trip_id:
            deadheads += 1  # deadheading vessel (repositioning, not in service)
            continue
        route_id, _route_name = _ferry_route_for_trip(trip_id, trips, routes)
        if route_id is None:
            join_misses += 1  # keep the boat, just uncolored, until the join heals
        boats.append(
            {
                "id": vehicle.vehicle.id or entity.id,
                "label": vehicle.vehicle.label or None,
                "trip_id": trip_id,
                "route_id": route_id,
                "latitude": pos.latitude,
                "longitude": pos.longitude,
                # speed is passed through RAW in m/s (the GTFS-RT Position.speed
                # unit; the 0-13 m/s = 0-25 kn range matches NYC Ferry hull speeds,
                # confirming Connexionz follows the spec). The frontend converts to
                # knots for the boat popup (H4).
                "speed": pos.speed if pos.HasField("speed") else None,
                "status": _status_name(vehicle),
                # Per-vehicle content time (advances each poll); the boat's own
                # freshness, distinct from the feed header timestamp.
                "updated_at": float(vehicle.timestamp) or None,
            }
        )
    return boats, _header_timestamp(feed), deadheads, join_misses


def _decode_ferry_arrivals(
    raw: bytes, trips: dict[str, dict], routes: dict[str, dict], now: float
) -> tuple[dict[str, dict[str, list[dict]]], int, int]:
    """Decode the TripUpdates feed into (arrivals_by_stop, deadhead_count,
    join_miss_count).

    arrivals_by_stop is {stop_id: {route_name: [rows]}}: bucketed BY ROUTE
    (there is no direction_id in the feed, and route reads better at a
    multi-route dock), sorted soonest-first and capped at the shared arrivals
    cap. Each row carries route_id, trip_id, arrival, and departure; docks report
    BOTH times (a dwell), so both are kept, either nullable but never both null.

    Same conventions as the other arrivals decoders: canceled/deleted trips and
    skipped/no-data stops are dropped, and a stop whose latest time is already
    past (beyond a 60s just-passed grace) is not indexed. Deadheads (empty
    trip_id) and join misses (unresolvable route) are dropped/kept and counted
    exactly as in the vehicle decode; a join-missed arrival lands in the residual
    'Ferry' bucket rather than being dropped."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    arrivals: dict[str, dict[str, list[dict]]] = {}
    deadheads = 0
    join_misses = 0
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            continue  # canceled/deleted trip: no real prediction
        trip_id = tu.trip.trip_id
        if not trip_id:
            deadheads += 1
            continue
        route_id, route_name = _ferry_route_for_trip(trip_id, trips, routes)
        if route_id is None:
            join_misses += 1
        bucket = route_name or _UNKNOWN_ROUTE_BUCKET
        for stu in tu.stop_time_update:
            if not stu.stop_id:
                continue
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop: no real prediction
            # The "still upcoming" test uses the LATEST of arrival/departure (a
            # boat dwelling at a dock has arrival in the past but departure in the
            # future and must not be treated as gone), the shared _stop_time rule.
            if (latest := _stop_time(stu)) is None or latest < now - 60:
                continue
            arrival = stu.arrival.time if _has_time(stu, "arrival") else None
            departure = stu.departure.time if _has_time(stu, "departure") else None
            arrivals.setdefault(stu.stop_id, {}).setdefault(bucket, []).append(
                {
                    "route_id": route_id,
                    "trip_id": trip_id,
                    "arrival": float(arrival) if arrival is not None else None,
                    "departure": float(departure) if departure is not None else None,
                }
            )
    return _trim_ferry_arrivals(arrivals), deadheads, join_misses


def _has_time(stu, field: str) -> bool:
    """True when a stop_time_update carries a nonzero time for `field`
    ('arrival' or 'departure'), the same presence test _stop_time applies."""
    if not stu.HasField(field):
        return False
    event = getattr(stu, field)
    return event.HasField("time") and bool(event.time)


def _trim_ferry_arrivals(
    arrivals: dict[str, dict[str, list[dict]]],
) -> dict[str, dict[str, list[dict]]]:
    """Sort each dock's per-route bucket soonest-first and cap at
    ARRIVALS_PER_DIRECTION (the shared arrivals cap). Ferry-local rather than the
    shared _trim_arrivals because ferry rows carry BOTH arrival and departure (a
    dock dwell), so the sort key is the arrival when present else the departure,
    where _trim_arrivals assumes a single 'arrival' field. Every kept row has at
    least one of the two, so the key is always a number."""
    trimmed: dict[str, dict[str, list[dict]]] = {}
    for stop_id, buckets in arrivals.items():
        trimmed[stop_id] = {}
        for route_name, rows in buckets.items():
            rows.sort(key=lambda r: r["arrival"] if r["arrival"] is not None else r["departure"])
            trimmed[stop_id][route_name] = rows[:ARRIVALS_PER_DIRECTION]
    return trimmed


async def _fetch_ferry_endpoint(client: httpx.AsyncClient, endpoint: str) -> bytes:
    """GET one ferry realtime endpoint and return the raw protobuf bytes. https
    first, then http on failure (the 14b probe used http and 14a used https for
    the same host; the http fallback only fires if https fails). Redirects are
    followed (the sibling static utility URL 302s; the realtime endpoints may
    too). Raises the https error if both attempts fail; a DecodeError on the body
    surfaces later, at the decode, exactly like the other single-fetch feeds."""
    last_exc: httpx.HTTPError | None = None
    for scheme in ("https", "http"):
        url = f"{scheme}://{FERRY_RT_HOST}/{endpoint}"
        try:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            last_exc = last_exc or exc  # keep the https error, the more informative one
    assert last_exc is not None  # unreachable: the loop returns on success or sets last_exc
    raise last_exc


async def fetch_ferry_data(
    client: httpx.AsyncClient, ferry_static: dict
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None]:
    """Fetch both NYC Ferry realtime endpoints in one poll cycle and return
    (boats, arrivals_by_stop, feed_timestamp).

    Both endpoints are fetched (VehiclePositions for the boats, TripUpdates for
    the arrivals) and decoded against 14a's static trip -> route map. An HTTP
    error on either endpoint, or an undecodable body, propagates for the caller
    (pollers._refresh_ferry) to record, the same all-or-nothing single-poll
    contract as the bus and PATH fetches. feed_timestamp is the VehiclePositions
    header time (the boats are the primary payload and their feed is the "is the
    system alive" signal). The caller must only call this once ferry_static is
    ready (the trip -> route join needs it)."""
    now = time.time()
    trips = (ferry_static or {}).get("trips") or {}
    routes = (ferry_static or {}).get("routes") or {}
    # Fetch both endpoints concurrently (they share one host), so the poll pays
    # the slower round trip, not their sum, matching fetch_railroad_trains'
    # gather. The contract is all-or-nothing, so the default gather (no
    # return_exceptions) is exactly right: the first leg to fail propagates and
    # the other is cancelled, and the caller retains last-known.
    vehicle_raw, tripupdate_raw = await asyncio.gather(
        _fetch_ferry_endpoint(client, FERRY_VEHICLE_ENDPOINT),
        _fetch_ferry_endpoint(client, FERRY_TRIPUPDATE_ENDPOINT),
    )
    boats, feed_timestamp, boat_deadheads, boat_join_misses = _decode_ferry_vehicles(
        vehicle_raw, trips, routes, now
    )
    arrivals, arr_deadheads, arr_join_misses = _decode_ferry_arrivals(
        tripupdate_raw, trips, routes, now
    )
    # Debug-level only: a deadheading vessel and a briefly-unjoinable trip are
    # normal churn, not an outage. Unlike PATH's unresolved-station count (which
    # is warning-level and surfaced on /api/status because those trains VANISH),
    # a ferry join miss keeps the boat on the map, so debug is the right volume
    # and it never touches the served payload or the health block.
    if boat_deadheads or arr_deadheads:
        logger.debug(
            "ferry: dropped %d deadheading boats and %d deadhead trip updates (empty trip_id)",
            boat_deadheads,
            arr_deadheads,
        )
    if boat_join_misses or arr_join_misses:
        logger.debug(
            "ferry: %d boats and %d arrival trips did not join the static route map "
            "(kept, uncolored)",
            boat_join_misses,
            arr_join_misses,
        )
    return boats, arrivals, feed_timestamp
