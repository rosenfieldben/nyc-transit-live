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

import env_seams
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
    parse_feed,
)

# Overridable base (C6). Its own variable rather than a shared one, even though
# the default is the same MTA Dataservice host the alert feeds use: a contract
# scenario has to be able to take the ALERT feeds down while the railroad feeds
# keep advancing, and one shared switch could not express that.
RAILROAD_RT_BASE = env_seams.url("RAILROAD_RT_BASE", _RAILROAD_BASE)
RAILROAD_FEED_URLS = {
    "LIRR": RAILROAD_RT_BASE + "/lirr%2Fgtfs-lirr",
    "MNR": RAILROAD_RT_BASE + "/mnr%2Fgtfs-mnr",
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


def _vehicle_is_canceled(entity, canceled_trips: set[str]) -> bool:
    """Is this vehicle entity's trip cancelled (or deleted) in this same feed?

    TWO LAYOUTS, TWO ANSWERS, and the reason both arms exist is that neither one
    covers the other:

      * COMBINED ENTITY (MNR): the trip_update sits on the vehicle's own entity, so
        it is read directly. The trip_id join CANNOT serve here, because MNR's
        vehicle.trip.trip_id is the TRAIN NUMBER ("1797") while its trip_update
        carries the internal id ("3114306"); measured on the committed capture, all
        119 entities are combined and the two ids never match.
      * SEPARATE ENTITIES (LIRR): the vehicle has no trip_update of its own, so it is
        joined by trip_id to the cancellations collected from this feed. Measured on
        the committed capture: 8 canceled trip_updates, 69 positioned vehicles, and
        exactly one trip in both sets.

    THE VEHICLE'S OWN schedule_relationship IS DELIBERATELY NOT CONSULTED, because it
    is not the signal. On that same LIRR capture the canceled train's vehicle entity
    (6004XX_2026-06-20_V) reports SCHEDULED while its TripUpdate reports CANCELED, so
    a decoder that trusted the vehicle would emit it exactly as before. The join is
    the whole check.
    """
    if entity.HasField("trip_update"):
        return entity.trip_update.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS
    return bool(entity.vehicle.trip.trip_id) and entity.vehicle.trip.trip_id in canceled_trips


def _canceled_trip_ids(feed) -> set[str]:
    """Trip ids this feed cancels or deletes, collected from its own trip_updates.

    Both railroad passes need this set, and they must not build it differently: it is
    half of what "accepted as GPS" means (see _accepted_as_gps) and, in the placement
    pass, it is also what keeps a canceled trip off the schedule-estimate surface.
    """
    canceled: set[str] = set()
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip = entity.trip_update.trip
        if trip.trip_id and trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            canceled.add(trip.trip_id)
    return canceled


def _railroad_observed_at(system: str, stamp: int | float) -> float | None:
    """The observation time a railroad row may report, or None where the provider
    sends none worth reporting.

    THE POLICY IS DATA AND IT ALREADY HAS A HOME, so this function reads
    RAILROAD_FRESHNESS_SYSTEMS rather than naming Metro-North a fourth time. That set
    answers "does this system's clock track reality", and the comment above it records
    the probe that settled it for both halves at once: MNR stamps a bursty clock that
    lags two to four minutes onto its header AND COPIES IT onto every
    vehicle.timestamp, while LIRR's header is the true generation time and its vehicles
    date themselves independently. One cause, one exclusion, one place to change it.

    MNR'S STAMP EXISTS AND IS WORTHLESS, which is the trap this function closes. All 49
    positioned vehicles on the committed Metro-North capture carry a timestamp and all
    49 of them are the header, one distinct value. A decoder that simply read the field
    would hand Metro-North a non-null observed_at that looks like an observation clock
    and is a restatement of a lagging header, which is worse than null: null says "this
    provider does not date its observations" and a copied header says something false.

    PROTOBUF ZERO IS NOT ABSENCE. uint64 fields default to 0 rather than going missing,
    and 0.0 survives every `is not None` check while meaning "unset", so the falsy
    guard here is the same idiom _header_timestamp uses on the header itself.
    """
    if system not in RAILROAD_FRESHNESS_SYSTEMS:
        return None
    return float(stamp) or None


def _accepted_as_gps(entity, canceled_trips: set[str]) -> bool:
    """Is this feed entity a vehicle the map will draw at its OWN reported position?

    THE ONE ACCEPTANCE RULE, AND THE WHOLE POINT IS THAT IT HAS ONE HOME (Audit 5, N2).
    Two passes ask this question about the same feed: _decode_railroad_vehicles emits
    exactly the entities it accepts, and _decode_railroad_feed places, at its next
    scheduled station, every running trip it does NOT accept. Those are complements of
    one predicate, so a train reaches exactly one surface. When they were two
    predicates they were not complements, and a train could fall through the gap.

    THAT GAP WAS REAL AND MEASURED. The placement pass built its positioned set without
    the bounding box, so a positioned vehicle reporting an out-of-range coordinate was
    dropped from the GPS output for being out of range AND suppressed from placement for
    being GPS-equipped: it appeared on no surface at all, rather than falling back to the
    estimate its trip_update could support. Reproduced on the committed LIRR capture by
    moving one vehicle to lat 0 lon 0: GPS 68 to 67, placements unchanged at 56, and the
    train on neither list.

    WHY IT HAS TO STAY ONE FUNCTION, not two that agree today. F01's remedy widens this
    rule by OBSERVATION AGE: a position old enough to be untrustworthy stops being
    accepted and its trip falls back to placement. Widening one pass and not the other
    recreates N2 exactly, one condition later. Widening this function widens both at
    once, which is the property worth keeping. (That change also needs the system and
    the current time, which this signature does not carry yet; adding them is F01's
    first line, not a parameter to leave unused here.)

    NO AGE RULE IS APPLIED HERE. F01 is a separate finding with a contract of its own,
    and this one is deliberately behaviour-preserving on every committed capture.
    """
    if not entity.HasField("vehicle"):
        return False
    if _vehicle_is_canceled(entity, canceled_trips):
        return False
    vehicle = entity.vehicle
    if not vehicle.HasField("position"):
        return False
    # A stray out-of-range coordinate is not a real train. Rejecting it here is what
    # routes the trip to the placement pass instead of off the map entirely.
    return _in_railroad_box(vehicle.position.latitude, vehicle.position.longitude)


def _decode_railroad_vehicles(
    raw: bytes, system: str, now: float
) -> tuple[list[dict], float | None]:
    """Decode one railroad feed; return (trains, feed_timestamp).

    feed_timestamp is the feed's content time (FeedHeader.timestamp, MTA's
    clock), or None when the feed omits it. Phase 1 keeps only entities whose
    vehicle carries a position AND whose trip is still running, covering both feed
    layouts: LIRR puts the vehicle
    in its own entity, MNR combines the trip_update and vehicle in one. Each kept
    train carries its real lat/lon (no station projection needed). An empty
    vehicle route_id is filled from the
    trip_update: MNR's combined entity carries the route on its own trip_update
    (MNR's vehicle.trip holds the train number, not the trip_update's internal
    trip id, so the same-entity read is what fills MNR), while LIRR's separate
    vehicle entity is joined by trip_id to this feed's trip_updates. WHICH ENTITIES
    ARE EMITTED IS NOT DECIDED HERE: _accepted_as_gps decides, and the placement
    pass reads the same function, so the two are complements rather than two
    rules that happen to agree (N2). The direction and
    interpolation-anchor fields are emitted as None: phase 2 (placing
    position-less trains at their next station) fills them, so the RailroadTrain
    model needs no change then.

    EACH TRAIN NOW CARRIES ITS OWN OBSERVATION TIME, or null where the provider
    sends none: _railroad_observed_at applies the per-system policy and this pass
    does not restate it. provenance is `reported` on every row here, because every
    row here is a coordinate the vehicle itself published.

    `now` IS STILL UNUSED, AND THAT IS NOT AN OVERSIGHT. Reading vehicle.timestamp
    needs no clock of ours: the age of an observation is a fact about the feed,
    computed later by whoever compares it to something. What WOULD need `now` is an
    age GATE, deciding that an old position stops being drawn, and that is F01's
    change rather than this one. The contract's models step is deliberately inert:
    it produces the values and nothing consumes them yet. So the parameter is still
    kept for parity with the subway decoders and still frozen by the golden test,
    and the line in section 4.2 of docs/design/freshness-contract.md that says this
    sentence stops being true is describing F01's commit, not this one.

    CANCELLATION IS RESOLVED BEFORE EMISSION, NOT AFTER (Audit 5, F02). A canceled
    trip stays in these feeds with a live GPS entity that keeps moving, because the
    train physically exists: it is deadheading to a yard, repositioning, or running
    empty to its next assignment. WHAT THE MAP DOES WITH IT IS NOTHING. It is not
    service, so it is not a train a rider can board, and drawing it as one is the
    falsehood F02 named: the audit found the capture's canceled 5-train (train 508)
    emitted FIRST of 69 GPS trains and served unmarked, while both of its station
    boards correctly omitted it. A rider watching it approach their platform would
    have been watching a train that was never going to stop for them.

    NOT DIMMED, NOT LABELLED, NOT EMITTED. A "canceled" marker was considered and
    rejected: this decoder has no way to tell a deadhead from a cancellation a rider
    might care about, the arrivals boards already say nothing about it, and a marker
    the boards disagree with is worse than no marker. The placement pass has dropped
    these trips since it was written (_decode_railroad_feed); this makes the GPS pass
    agree with it rather than contradict it.
    """
    # parse_feed rejects an empty or malformed body (C3); fetch_railroad_trains
    # catches it per SYSTEM, so a poisoned LIRR leaves MNR untouched.
    feed = parse_feed(raw)

    # trip_id -> route_id from this feed's trip_updates, to fill an empty vehicle
    # route_id in the separate-entity (LIRR) layout. The combined-entity (MNR)
    # layout is handled inline below via the entity's own trip_update.
    route_by_trip: dict[str, str] = {}
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            trip = entity.trip_update.trip
            if trip.trip_id and trip.route_id:
                route_by_trip.setdefault(trip.trip_id, trip.route_id)

    # THE CANCELED SET AND THE ACCEPTANCE RULE BOTH COME FROM SHARED FUNCTIONS, so this
    # pass and the placement pass in _decode_railroad_feed cannot disagree about which
    # vehicles are GPS-positioned or about what "running" means (N2). This loop emits
    # exactly the accepted entities; that pass places exactly the running trips among
    # the rest.
    canceled_trips = _canceled_trip_ids(feed)

    trains: list[dict] = []
    for entity in feed.entity:
        if not _accepted_as_gps(entity, canceled_trips):
            continue
        v = entity.vehicle
        pos = v.position
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
                # The vehicle's OWN clock where the provider keeps one, null where it
                # does not (_railroad_observed_at). LIRR dates all 69 of its positioned
                # vehicles independently of the header; Metro-North dates none of them.
                "observed_at": _railroad_observed_at(system, v.timestamp),
                "provenance": "reported",
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
    _railroad_trip_start_ts). A GPS train is never also placed, and a train the GPS
    pass REJECTED is never lost: both halves read _accepted_as_gps (N2), so a
    trip_update is skipped when its own entity is accepted (MNR's combined entity) or
    when its trip_id belongs to an accepted vehicle entity (LIRR's split layout), and
    every other running trip is placed. Before that, the placement pass decided
    "has GPS" for itself, without the bounding box, and a positioned vehicle with an
    out-of-range coordinate reached neither surface.

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
    # parse_feed for the same reason as the vehicle decode above. This pass only
    # ever sees bytes that ALREADY decoded in the GPS pass (raw_by_system holds
    # exactly those), so it cannot be the first to reject a system; the strict
    # parse here is belt and braces, and keeps the two decoders' contracts equal.
    feed = parse_feed(raw)

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
    canceled_trips = _canceled_trip_ids(feed)
    positioned_ids: set[str] = set()
    label_by_trip: dict[str, str] = {}
    for entity in feed.entity:
        if entity.HasField("vehicle") and entity.vehicle.HasField("position"):
            v = entity.vehicle
            # The trip_id truthiness test is a KEY-VALIDITY guard, not part of the
            # acceptance rule: an empty trip_id cannot key either map, and the GPS pass
            # has no such test because it falls back to entity.id for its own output.
            if v.trip.trip_id:
                # THE TRAIN NUMBER IS NOT A POSITION CLAIM, so it is joined from any
                # positioned vehicle, accepted as GPS or not. Narrowing it to the
                # accepted set alongside positioned_ids reads natural and is wrong:
                # measured, it strips train_num "521" off trip 6006_2026-06-20's
                # arrivals row at station 141 the moment that vehicle's COORDINATE goes
                # out of range. A label stays valid when a coordinate does not.
                label = (v.vehicle.label or v.vehicle.id) or None
                if label:
                    label_by_trip.setdefault(v.trip.trip_id, label)
                # POSITIONED_IDS IS THE ONE THAT NARROWS (N2). It answers "is this trip
                # already drawn at its own position", which is exactly _accepted_as_gps,
                # and before this it answered a broader question of its own.
                if _accepted_as_gps(entity, canceled_trips):
                    positioned_ids.add(v.trip.trip_id)

    # THE PREDICTION CLOCK, per trip, with the feed header as its only fallback.
    # LIRR dates 127 of the 132 trip_updates on the committed capture and the other 5
    # are its canceled trips, which this pass drops anyway; the header is what any
    # future gap falls back to, and it is a real number this provider sent rather than
    # one computed here. Metro-North dates none of its 119 and is excluded by
    # _railroad_observed_at, so every MNR row below reports null whichever branch it
    # takes. See section 3.3 of docs/design/freshness-contract.md for the table.
    feed_header = _header_timestamp(feed)

    def prediction_observed_at(trip_update) -> float | None:
        return _railroad_observed_at(system, trip_update.timestamp) or _railroad_observed_at(
            system, feed_header or 0
        )

    trains: list[dict] = []
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        # CANCELED BY ANY trip_update IN THIS FEED, not only by this one, and that
        # widening is load-bearing rather than tidy. Narrowing positioned_ids above
        # removes a canceled trip's id from the set that used to block its placement,
        # so a feed carrying two trip_updates for one trip_id with DIFFERENT
        # schedule_relationship would place the non-canceled copy: F02's canceled train
        # back on the map through the placement door. Measured on a capture built to
        # carry that contradiction: placements 56 to 57 with the canceled trip among
        # them, and 56 again with this test widened. No committed capture contains the
        # shape, so no golden would have caught it.
        if (
            tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS
            or tu.trip.trip_id in canceled_trips
        ):
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
                    # A PREDICTION IS AN OBSERVATION: this row's countdown is only as
                    # current as the trip_update that produced it, which is the whole
                    # of F03 on the railroad side.
                    "observed_at": prediction_observed_at(tu),
                    "provenance": "reported",
                }
            )

        # Placement: skip a train the GPS pass ACCEPTED (never place it twice), and
        # place every other running trip. MNR combines trip_update + vehicle in one
        # entity, so acceptance is read off this entity directly; LIRR splits them, so a
        # separate vehicle entity holds this train's position under the same trip_id and
        # positioned_ids carries the answer across.
        #
        # BOTH TESTS HAD TO MOVE, and the MNR one is the one that does the work there:
        # MNR's vehicle.trip.trip_id is the TRAIN NUMBER, which never matches a
        # trip_update id, so positioned_ids is empty of anything MNR consults and this
        # per-entity test is the only thing preventing a double draw. Narrowing
        # positioned_ids alone would have shipped as a fix with Metro-North exactly as
        # broken as before.
        if _accepted_as_gps(entity, canceled_trips):
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
                # A PLACED TRAIN IS AS CURRENT AS THE PREDICTION THAT PLACED IT, which
                # is `tu` in scope here: the same trip_update whose `chosen` stop set
                # the latitude and longitude two lines up. Not the header, not the poll
                # clock, and not the GPS reading it does not have.
                "observed_at": prediction_observed_at(tu),
                # `placed` and not `estimated`: this row sits AT a station's own
                # coordinates. The anchors beside it let a client glide between two
                # stations, but the position this decoder emits is the stop's.
                "provenance": "placed",
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
) -> tuple[
    list[dict],
    dict[str, dict[str, dict[str, list[dict]]]],
    float | None,
    list[str],
    dict[str, float | None],
]:
    """Fetch the LIRR and MNR feeds concurrently; return
    (trains, arrivals_by_system, feed_timestamp, failed_feeds, feed_ts_by_system),
    where feed_ts_by_system carries each freshness-authoritative system's OWN header
    (contract 6.1) so a per-system block can name which contributor is behind. A
    system RAILROAD_FRESHNESS_SYSTEMS does not admit is simply absent from it.

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
    feed_ts_by_system: dict[str, float | None] = {}
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
        # RAILROAD_FRESHNESS_SYSTEMS); MNR's lagging shared clock is ignored. The
        # per-system map keeps the SAME exclusion rather than restating it: a system
        # that is not admitted here is simply absent from the map, so its block
        # reports None without anything downstream knowing why.
        if feed_ts is not None and system in RAILROAD_FRESHNESS_SYSTEMS:
            timestamps.append(feed_ts)
            feed_ts_by_system[system] = feed_ts
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
        try:
            placed, arrivals = _decode_railroad_feed(result, system, stops, now)
        except DecodeError as exc:
            # UNREACHABLE AS WRITTEN, and guarded anyway. raw_by_system holds only
            # bytes that already decoded in the GPS pass above and parse_feed is
            # deterministic, so this cannot be the first pass to reject a system.
            # Without the guard, though, the reasoning that keeps it safe lives in a
            # comment rather than in the code, and a DecodeError escaping here would
            # leave fetch_railroad_trains entirely (its caller catches RuntimeError
            # and httpx.HTTPError, not this) and be recorded nowhere. It routes to
            # the same per-system entry the GPS pass uses, so a system still fails
            # at most once per poll.
            feed_errors[system] = f"undecodable protobuf ({exc})"
            continue
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
    return trains, arrivals_by_system, feed_timestamp, sorted(feed_errors), feed_ts_by_system
