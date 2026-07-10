"""Commuter-rail (LIRR + Metro-North) GTFS-RT feeds: the feed URLs and the
per-system freshness rule, the GPS vehicle decode, the direction-inference
cluster (the anchor-progression heuristic), and the trip placement / arrivals
decode."""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from feeds.shared import (
    _DROP_STOP_RELATIONSHIPS,
    _DROP_TRIP_RELATIONSHIPS,
    _RAILROAD_BASE,
    NYC_TZ,
    TRIP_START_GRACE_S,
    _header_timestamp,
    _in_railroad_box,
    _stop_time,
    _trim_arrivals,
    logger,
)

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

    return trains, _trim_arrivals(arrivals)


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
