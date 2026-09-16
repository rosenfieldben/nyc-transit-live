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


def _position_age_gated(system: str) -> bool:
    """Is this railroad system's GPS POSITION row age-gated (section 3.3's table)?

    THE SAME SET _railroad_observed_at READS, AND FOR THE SAME REASON. The table's two
    railroad position rows are LIRR's (gated: its vehicles date themselves) and
    Metro-North's (not gated: its stamp is a copy of a lagging header), and both follow
    from the one probe recorded above RAILROAD_FRESHNESS_SYSTEMS. So a system that set
    admits is dated and gated in the same edit, and design 4.2's rule holds: the policy
    table does not become a fourth place where Metro-North's exclusion is restated.
    """
    return system in RAILROAD_FRESHNESS_SYSTEMS


def _passes_base_rule(entity, canceled_trips: set[str]) -> bool:
    """The half of _accepted_as_gps that is not about age: a vehicle entity whose trip is
    not canceled, carrying a position inside the railroad box.

    ITS OWN FUNCTION SO THAT THE POSITION LADDER CAN ASK IT (contract 6.3). The ladder
    judges every vehicle this accepts, and _accepted_as_gps reads the ladder's judgement,
    so a ladder that asked _accepted_as_gps which vehicles to judge would be asking a
    question whose answer needs the ladder. NOTHING ELSE MAY ASK IT IN PLACE OF
    _accepted_as_gps: a pass that emitted what this accepts would draw a fix fifteen hours
    old as a live train, which is F01, and a pass that placed around it would recreate N2
    one condition later.

    THE BOX IS HERE, AND THE GAP IT CLOSED WAS REAL AND MEASURED (Audit 5, N2). The
    placement pass once built its positioned set without the bounding box, so a positioned
    vehicle reporting an out-of-range coordinate was dropped from the GPS output for being
    out of range AND suppressed from placement for being GPS-equipped: it appeared on no
    surface at all, rather than falling back to the estimate its trip_update could support.
    Reproduced on the committed LIRR capture, before the age gate, by moving one vehicle to
    lat 0 lon 0: GPS 68 to 67, placements unchanged at 56, and the train on neither list. A
    vehicle the box rejects never reaches the ladder, and its trip is placed exactly as it
    was then, whatever the age of the prediction behind it (memo D1).
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


class PositionLadder(dict[str, int]):
    """_position_ladder's answer, {trip id: step}, with the terms it was judged on.

    A MAPPING FIRST, so whatever reads steps reads a plain dict. The attributes are what
    _accepted_as_gps needs for the one question the mapping cannot answer, whether a single
    vehicle entity's own observation earns its trip's step: the system (for its policy row
    and its observation clock), whether that row is age-gated, the clock every age is read
    against, and the two limits as this decode read them.
    """

    def __init__(
        self, system: str, gated: bool, clock: float, fresh_s: float, max_s: float
    ) -> None:
        super().__init__()
        self.system = system
        self.gated = gated
        self.clock = clock
        self.fresh_s = fresh_s
        self.max_s = max_s


def _accepted_as_gps(entity, canceled_trips: set[str], ladder: PositionLadder) -> bool:
    """Is this feed entity a vehicle the map will draw at its OWN reported position?

    THE ONE ACCEPTANCE RULE, AND THE WHOLE POINT IS THAT IT HAS ONE HOME (Audit 5, N2).
    Two passes ask this question about the same feed: _decode_railroad_vehicles emits
    exactly the entities it accepts, and _decode_railroad_feed places every running trip
    it does NOT accept, as far as the position ladder allows. Those are complements of
    one predicate, so a train reaches at most one surface. When they were two predicates
    they were not complements, and a train could fall through the gap between them
    (_passes_base_rule records the measurement).

    THE AGE RULE IS APPLIED HERE, SINCE CONTRACT 6.3 (F01), and it reaches both passes at
    once because both ask this function: widening one pass and not the other would have
    recreated N2 exactly, one condition later. Accepted means three things. The base rule
    holds (_passes_base_rule). The ladder puts this vehicle's trip at step 1 (a fix within
    OBS_FRESH_S) or step 3 (a fix within OBS_MAX_S, or none on an age-gated row), the two
    steps drawn at a vehicle's own position. And THIS ENTITY'S OWN OBSERVATION EARNS THAT
    STEP. Steps 2 and 4 are drawn by the placement pass from a prediction and step 5 by
    nobody; _position_ladder has the order and its numbers.

    WHY ITS OWN OBSERVATION AND NOT ONLY ITS TRIP'S STEP. A trip the feed reports through
    two vehicle entities takes the best step either earns, so a trip with copies 1 s and
    700 s old is step 1. Accepting both would hand the GPS pass both rows and
    fetch_railroad_trains' first-wins (system, trip_id) dedupe the choice between them, and
    with the stale copy first it would serve the 700 s fix under the 1 s fix's step: an
    unqualified live marker older than OBS_MAX_S, F01 again through a side door. So at step
    1 only a copy within OBS_FRESH_S is accepted, at step 3 only one within OBS_MAX_S or
    undated, and the other copies are the same train, already drawn. No committed capture
    repeats an LIRR trip id; tests/test_feeds_railroad.py builds one, in both orders. A
    system whose position row is not age-gated (Metro-North) has no age to judge, so every
    copy of its step-1 trips is accepted, as before the gate: its 49 positioned entities
    are 33 trips, and the live dedupe draws each once.
    """
    if not _passes_base_rule(entity, canceled_trips):
        return False
    step = ladder.get(entity.vehicle.trip.trip_id or entity.id)
    if step not in (1, 3):
        return False
    if not ladder.gated:
        return True
    observed = _railroad_observed_at(ladder.system, entity.vehicle.timestamp)
    if observed is None:
        # An undated fix on a gated row is drawn and said "age unknown" (design 3.2 clause
        # (c)), which is step 3 and never step 1.
        return step == 3
    return ladder.clock - observed <= (ladder.fresh_s if step == 1 else ladder.max_s)


def _observation_limits() -> tuple[float, float]:
    """(OBS_FRESH_S, OBS_MAX_S) out of cache, read when a decode runs.

    A FUNCTION-LEVEL IMPORT, AND THE IMPORT CYCLE IS THE REASON. cache imports feeds
    (active_alert_feeds, iter_rows) before it defines a single constant, and
    feeds/__init__ imports this module before it binds iter_rows, so a module-level
    `from cache import` here fails whichever of the two is imported first. Measured with
    one such line added: importing feeds or main first fails on iter_rows, and importing
    cache first fails on the constant. (Importing pollers first proves nothing either
    way: it already fails without that line, on the older cycle between pollers and
    main.) By the time anything decodes both are loaded, so this is an attribute
    lookup, and neither number is written twice.
    """
    import cache

    return float(cache.OBS_FRESH_S), float(cache.OBS_MAX_S)


def _updates_by_trip(feed) -> dict[str, list]:
    """The feed's trip_update entities by trip id, the join the split layout (LIRR's) makes
    between a vehicle and its prediction, the way the placement pass joins them."""
    updates: dict[str, list] = defaultdict(list)
    for entity in feed.entity:
        if entity.HasField("trip_update") and entity.trip_update.trip.trip_id:
            updates[entity.trip_update.trip.trip_id].append(entity)
    return updates


def _first_placeable_row(
    entity,
    system: str,
    stops: dict[str, dict] | None,
    now: float,
    header: float | None,
    canceled_trips: set[str],
    updates_by_trip: dict[str, list],
) -> dict | None:
    """The first row the placement pass would draw for this vehicle's trip at `now`, or None.

    "PLACEABLE" IS THE PLACEMENT PASS'S OWN ANSWER: the trip_update is not canceled
    (_trip_update_is_canceled) and _place_trip returns a row, whose observed_at is the
    prediction's clock (_prediction_observed_at). A combined entity (Metro-North's layout)
    is placed from its own trip_update; a split-layout vehicle (LIRR's) from the
    trip_updates sharing its trip id, and where there are several the first placeable one
    in feed order answers, which is the row the live path's dedupe keeps. With no stops
    nothing is placeable, as fetch_railroad_trains skips the placement pass then.
    """
    if not stops:
        return None
    if entity.HasField("trip_update"):
        candidates = [entity]
    elif entity.vehicle.trip.trip_id:
        candidates = updates_by_trip.get(entity.vehicle.trip.trip_id, [])
    else:
        candidates = []
    for candidate in candidates:
        tu = candidate.trip_update
        if _trip_update_is_canceled(tu, canceled_trips):
            continue
        row = _place_trip(
            candidate, system, stops, now, None, _prediction_observed_at(system, tu, header)
        )
        if row is not None:
            return row
    return None


def _position_ladder(
    feed, system: str, stops: dict[str, dict] | None, now: float, canceled_trips: set[str]
) -> PositionLadder:
    """Section 3.4's order for every vehicle the base rule accepts, as {trip id: step}.

    THE STEPS, the first that holds, each age read against `clock`, the feed header
    (else `now`):

      1. reported, unqualified: its own observation is within OBS_FRESH_S.
      2. estimated: its trip's prediction is placeable and within OBS_FRESH_S.
      3. reported, qualified: its own observation is within OBS_MAX_S, or it has none on
         an age-gated row (drawn, and said "age unknown": design 3.2 clause (c)).
      4. placed: its trip's prediction is placeable and within OBS_MAX_S.
      5. nothing: no marker, and the vehicle is counted instead (design Q7).

    "Within" is `<=`, the design's word; no age on either committed capture sits on 90
    or 600. A null prediction clock is within nothing. A system whose position row is
    not age-gated (_position_age_gated) is step 1 throughout, because there is no age to
    order it by.

    AN UNKNOWN AGE IS NOT A FRESH ONE, AND THAT IS A DECISION RATHER THAN A CONSEQUENCE
    OF THE ORDER. A gated row whose vehicle carries no timestamp has an age nobody knows,
    and unknown is not within OBS_FRESH_S, so such a vehicle can never be step 1. It is
    asked step 2 first, and a prediction inside OBS_FRESH_S answers "where is this train
    now" better than a position whose age is a blank, so a fresh estimate takes it. Only
    when nothing fresh is placeable does it fall to step 3, drawn at its own position and
    said "age unknown" (design 3.2 clause (c)). Steps 4 and 5 are therefore out of its
    reach, which is the point rather than an accident: step 5's status line says a train
    was last seen over OBS_MAX_S ago, and that is a claim nobody can make about a fix
    with no time on it (models.PositionSteps, and _position_steps holds the same rule for
    a box-rejected copy). Both worlds are pinned at the ladder in
    tests/test_position_ladder.py and at both passes in tests/test_feeds_railroad.py.

    WHO IS COVERED: every vehicle _passes_base_rule accepts (a vehicle, not canceled,
    positioned, inside the box), keyed by the trip id the GPS pass emits for it. A trip
    with no vehicle is not a vehicle and stays out, placed as it always was whatever its
    prediction's age: 49 of the 56 LIRR placements on the capture ride a prediction over
    600 s, and gating them would take that surface to 13. A vehicle the box rejects stays
    out too, and falls to placement as N2 left it (memo D1).

    A REPEATED TRIP ID TAKES THE BEST STEP ANY OF ITS VEHICLES EARNS, whatever their
    order in the feed. Taking the first entity's step, as fetch_railroad_trains keeps
    the first row, made the step a fact about feed order: a trip whose first copy is
    700 s old and whose second is 1 s old was step 5, withheld and counted while the
    same feed carried a fix one second old, and step 1 with the copies swapped. Neither
    committed capture shows it (no LIRR trip id repeats, and Metro-North's 49
    positioned entities are 33 trips on a row that is not gated), so a built world pins
    it in both orders. The gate keeps its half of that bargain: of a repeated trip's
    entities _accepted_as_gps accepts only those whose own observation earns the trip's
    step, so the live path's first-wins dedupe cannot draw the 700 s copy under the 1 s
    copy's step 1.

    THE CLOCK IS THE HEADER, NOT THE POLL. The design measured its counts there and they
    hold only there: read against a clock 5 s later they are already 26/7/11/0/24. And a
    poll clock ages every vehicle by however far the poll trails the capture, which on
    the live path under test is months, so all 68 LIRR vehicles would be step 5.

    IN PRODUCTION `now` IS THE POLL, LATER THAN THE HEADER BY THE POLL LAG, and only
    placeability reads it: every age is read against the header, so no age moves. What
    can move is a trip whose last timed stop falls inside that lag, at or after the
    header's 60 s just-passed grace and before now's. It is placeable at the header and
    not at `now`, so a step-2 vehicle on it is not estimated: it falls to step 3, drawn at
    its own position and qualified, while its own fix is within OBS_MAX_S (all six of the
    capture's step-2 vehicles are, at 91 to 203 s), and to step 5 only past that; a
    step-4 vehicle on such a trip falls to step 5. So a live poll's counts can differ
    slightly from the header-time 27/6/11/0/24 the tests pin, and on LIRR, whose trips
    carry no start time for the not-yet-started filter to read, every difference is a
    trip that has run out of stops. Measured on the capture (tests/test_position_ladder.py):
    `now` a minute after the header changes no step, and an hour after it five of the six
    have run out of stops and are qualified instead, 27/1/16/0/24.

    "PLACEABLE" IS THE PLACEMENT PASS'S OWN ANSWER, asked at its own `now`
    (_first_placeable_row). The cancellation check in it can only bite on the combined
    layout: a split-layout vehicle whose trip is canceled never passes the base rule, but
    a combined entity whose trip ANOTHER trip_update cancels does (_vehicle_is_canceled
    reads only the entity's own), and the placement pass drops that trip. So the ladder
    can never withhold a vehicle for an estimate the placement pass would not draw, which
    is how an age gate reopens N2 one condition later. With no stops nothing is
    placeable, and steps 2 and 4 cannot fire.

    WIRED SINCE CONTRACT 6.3, into both passes through _accepted_as_gps, and into
    _position_steps, which counts what it did. On the committed LIRR capture at its
    header it answers 27, 6, 11, 0 and 24 (tests/test_position_ladder.py), and the GPS
    and placement goldens hold its steps 1 and 3 (38 rows) and its six estimates.
    """
    fresh_s, max_s = _observation_limits()
    header = _header_timestamp(feed)
    clock = header if header is not None else now
    gated = _position_age_gated(system)
    ladder = PositionLadder(system, gated, clock, fresh_s, max_s)
    accepted = [entity for entity in feed.entity if _passes_base_rule(entity, canceled_trips)]
    if not gated:
        for entity in accepted:
            ladder.setdefault(entity.vehicle.trip.trip_id or entity.id, 1)
        return ladder

    updates_by_trip = _updates_by_trip(feed)
    for entity in accepted:
        vehicle = entity.vehicle
        key = vehicle.trip.trip_id or entity.id
        observed = _railroad_observed_at(system, vehicle.timestamp)
        age = None if observed is None else clock - observed

        row = _first_placeable_row(
            entity, system, stops, now, header, canceled_trips, updates_by_trip
        )
        prediction_age = None
        if row is not None and row["observed_at"] is not None:
            prediction_age = clock - row["observed_at"]

        if age is not None and age <= fresh_s:
            step = 1
        elif prediction_age is not None and prediction_age <= fresh_s:
            step = 2
        elif age is None or age <= max_s:
            step = 3
        elif prediction_age is not None and prediction_age <= max_s:
            step = 4
        else:
            step = 5
        # A repeated trip id keeps the best step any of its vehicles earns, never the
        # first or the last in feed order (the docstring says why).
        ladder[key] = min(step, ladder.get(key, step))
    return ladder


def _ladder_step_behind(entity, canceled_trips: set[str], ladder: PositionLadder) -> int | None:
    """The ladder step of the vehicle behind this trip_update entity, or None when no vehicle
    the ladder judged is behind it: a trip with no vehicle, or one whose vehicle the box
    rejected, both placed exactly as before contract 6.3 (memo D1).

    THE LADDER'S OWN JOIN, READ BACKWARDS. A combined entity (Metro-North's layout) is
    behind itself, keyed by its own vehicle's trip id. A trip_update entity of the split
    layout (LIRR's) is joined by its trip id to the separate vehicle keyed under the same
    id, the join positioned_ids already makes.
    """
    if _passes_base_rule(entity, canceled_trips):
        return ladder.get(entity.vehicle.trip.trip_id or entity.id)
    trip_id = entity.trip_update.trip.trip_id
    return ladder.get(trip_id) if trip_id else None


# models.PositionSteps' fields, by the ladder step each counts.
_STEP_FIELDS = {1: "reported", 2: "estimated", 3: "qualified", 4: "placed", 5: "suppressed"}


def _position_steps(
    raw: bytes, system: str, stops: dict[str, dict] | None, now: float
) -> dict[str, int]:
    """What the position ladder did with one railroad system's vehicles this decode, as the
    counts models.PositionSteps serves: reported (step 1), estimated (2), qualified (3),
    placed (4, plus a vehicle the box rejected whose trip N2's fallback placed) and
    suppressed (5, plus a vehicle the box rejected that nothing placed, whose own fix is
    past OBS_MAX_S).

    ONE COUNT PER TRIP, WHICH IS THE LIVE PATH'S DEDUPE. The ladder keys a vehicle by the
    trip id the GPS pass emits it under, and fetch_railroad_trains keeps one row per
    (system, trip id), so a repeated trip is counted once, at the step it is drawn at. On
    the committed captures at their headers: LIRR 27, 6, 11, 0 and 24 over its 68
    vehicles; Metro-North 33, 0, 0, 0 and 0 over its 49 positioned entities, which are 33
    trips (its golden keeps all 49 rows because the golden does not dedupe). Every count
    but `suppressed` is a marker a vehicle entity produces; `suppressed` is the one design
    Q7 puts on the status line and nowhere on the map.

    N2'S FALLBACK IS COUNTED FROM THE ANSWER THE PLACEMENT PASS DRAWS FROM. A vehicle the
    box rejects never reaches the ladder (memo D1), and its trip is placed as it was
    before 6.3 when the placement pass can place it, which _first_placeable_row asks
    exactly as the ladder does: that trip is `placed`. One whose trip nothing places
    reaches no surface, and WHERE IT IS COUNTED IS DECIDED BY ITS OWN FIX, because the
    words the status line prints for `suppressed` are "last seen over 10m ago":
      * Past OBS_MAX_S they are true of it, and the box changed nothing about why it is
        not drawn: inside the box the ladder would have withheld it at step 5 as well (no
        fix within OBS_MAX_S, no prediction to place it). So it is `suppressed`, counted
        with the rest of design Q7's population. Measured by moving GO201_26_8945 (its fix
        8370 s old, its trip update naming no stop still ahead) to lat 0 lon 0 on the LIRR
        capture: `suppressed` stays 24, where it read 23 before this rule.
      * Within OBS_MAX_S, or undated on a gated row, they would be false of it: inside the
        box it would have been drawn at its own position (step 1 or 3), so it was not
        withheld for age, and it is no marker either. It is the ONE positioned, non-canceled
        vehicle the five counts leave out. Measured the same way with GO201_26_7987, a 139 s
        fix drawn qualified whose trip cannot be placed: `qualified` goes 11 to 10 and
        nothing else moves, so the counts sum to 67 of the 68.
    A trip the feed repeats is judged by its freshest box-rejected copy, as the ladder
    judges one by its best step, and a system whose position row is not age-gated
    (Metro-North) has no age to judge, so none of its vehicles is counted this way.
    Neither committed capture carries either kind as captured; tests/test_feeds_railroad.py
    builds both.

    Parsed from the same bytes, stops and `now` both passes decode, so it describes what
    they drew rather than offering a second opinion. fetch_railroad_trains asks it inside
    the GPS pass's DecodeError guard, right after the same bytes decoded there, so it
    cannot be the first to reject a system.
    """
    feed = parse_feed(raw)
    canceled_trips = _canceled_trip_ids(feed)
    ladder = _position_ladder(feed, system, stops, now, canceled_trips)
    steps = dict.fromkeys(_STEP_FIELDS.values(), 0)
    for step in ladder.values():
        steps[_STEP_FIELDS[step]] += 1

    header = _header_timestamp(feed)
    updates_by_trip = _updates_by_trip(feed)
    fallback: set[str] = set()
    # Each box-rejected trip's own fix ages, read as the ladder reads them (against its
    # clock), None for an undated copy: what decides whether an unplaced one is withheld.
    rejected_ages: dict[str, list[float | None]] = defaultdict(list)
    for entity in feed.entity:
        if not (entity.HasField("vehicle") and entity.vehicle.HasField("position")):
            continue
        if _vehicle_is_canceled(entity, canceled_trips) or _passes_base_rule(
            entity, canceled_trips
        ):
            continue  # canceled (F02: counted nowhere), or judged by the ladder above
        key = entity.vehicle.trip.trip_id or entity.id
        if key in ladder:
            continue  # the same trip, judged and counted by the ladder
        if key not in fallback and _first_placeable_row(
            entity, system, stops, now, header, canceled_trips, updates_by_trip
        ):
            fallback.add(key)
        observed = _railroad_observed_at(system, entity.vehicle.timestamp)
        rejected_ages[key].append(None if observed is None else ladder.clock - observed)
    steps["placed"] += len(fallback)
    if ladder.gated:
        # Withheld only when EVERY copy's own fix is past OBS_MAX_S: one fresher copy, or
        # an undated one, means the train was not last seen over ten minutes ago.
        steps["suppressed"] += sum(
            1
            for key, ages in rejected_ages.items()
            if key not in fallback and all(age is not None and age > ladder.max_s for age in ages)
        )
    return steps


def _decode_railroad_vehicles(
    raw: bytes, system: str, now: float, stops: dict[str, dict] | None = None
) -> tuple[list[dict], float | None]:
    """Decode one railroad feed; return (trains, feed_timestamp).

    feed_timestamp is the feed's content time (FeedHeader.timestamp, MTA's
    clock), or None when the feed omits it. Phase 1 keeps only entities whose
    vehicle carries a position AND whose trip is still running AND whose own
    observation the position ladder draws as it stands (contract 6.3), covering both
    feed layouts: LIRR puts the vehicle
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

    THE AGE GATE IS APPLIED HERE, AND ITS CLOCK IS NOT `now` (contract 6.3, F01). This
    pass emits what _accepted_as_gps accepts, which is what the position ladder draws at
    a vehicle's own position: steps 1 and 3, a fix within OBS_FRESH_S, or one within
    OBS_MAX_S (or undated on a gated row) when no fresh prediction can stand in for it.
    Every age is read against the feed HEADER, and against `now` only when the header
    is absent, because the design measured its counts at the header and a poll clock
    would age the whole fleet by the poll's lag. `now` reaches one thing, placeability:
    the ladder asks _place_trip, at `now`, whether a stale vehicle's trip can be
    estimated instead, exactly as the placement pass will draw it. So the sentence in
    section 4.2 of docs/design/freshness-contract.md that this paragraph used to defer,
    that `now` is unused here, is false from this commit; the golden still freezes
    `now` to the header, and still holds. `stops` is the placement pass's own static
    stops for this system, which fetch_railroad_trains passes to both passes: without
    them nothing is placeable, steps 2 and 4 cannot fire, and a stale vehicle within
    OBS_MAX_S is drawn here, qualified, rather than withheld for an estimate nobody will
    draw. On the committed LIRR capture at its header, with its stops, this emits 38 of
    the 68 vehicles the base rule accepts (27 fresh and 11 qualified); the placement
    pass estimates 6 and the other 24 are counted rather than drawn (_position_steps).

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
    # exactly the accepted entities; that pass places the running trips among the rest,
    # as far as the position ladder allows.
    canceled_trips = _canceled_trip_ids(feed)
    # The ladder the placement pass builds too, from the same inputs, so the two passes
    # stay complements under the age gate exactly as they are under the base rule.
    ladder = _position_ladder(feed, system, stops, now, canceled_trips)

    trains: list[dict] = []
    for entity in feed.entity:
        if not _accepted_as_gps(entity, canceled_trips, ladder):
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


def _prediction_observed_at(system: str, trip_update, feed_header: float | None) -> float | None:
    """THE PREDICTION CLOCK, per trip, with the feed header as its only fallback.

    LIRR dates 127 of the 132 trip_updates on the committed capture and the other 5 are
    its canceled trips, which the placement pass drops anyway; the header is what any
    future gap falls back to, and it is a real number this provider sent rather than one
    computed here. Metro-North dates none of its 119 and is excluded by
    _railroad_observed_at, so every MNR row reports null whichever branch it takes. See
    section 3.3 of docs/design/freshness-contract.md for the table.

    A FUNCTION OF ITS OWN SINCE CONTRACT 6.3, because the position ladder reads the same
    clock when it asks how old the prediction behind a vehicle is. It was a closure
    inside _decode_railroad_feed, and a second copy for the ladder would be a second
    clock.
    """
    return _railroad_observed_at(system, trip_update.timestamp) or _railroad_observed_at(
        system, feed_header or 0
    )


def _trip_update_is_canceled(tu, canceled_trips: set[str]) -> bool:
    """Is this trip_update's trip canceled or deleted, by itself or by ANY trip_update in
    this feed? The placement pass drops such a trip from placement and arrivals alike
    (the comment where it asks says why the feed-wide half is load-bearing), and the
    position ladder asks the same question through this function before it counts a
    trip as placeable, so the two cannot disagree about a feed that contradicts itself.
    """
    return (
        tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS
        or tu.trip.trip_id in canceled_trips
    )


def _place_trip(
    entity,
    system: str,
    stops: dict[str, dict],
    now: float,
    direction: str | None,
    observed_at: float | None,
    provenance: str = "placed",
) -> dict | None:
    """The row the placement pass draws for this trip_update entity at `now`, or None when
    the trip cannot be placed: not yet started, finished, or with no stop these static
    stops resolve.

    ONE ANSWER TO "CAN THIS TRIP BE PLACED", which is why it is a function (contract 6.3).
    The placement pass draws what this returns, and the position ladder asks it before
    counting an estimate: two versions of the answer would let the ladder withhold a
    vehicle for a row the pass never draws, which is N2 one condition later. Moved here
    unchanged from _decode_railroad_feed; the placement and arrivals goldens are
    byte-identical across the move. `direction`, `observed_at` and `provenance` are
    carried into the row as given and play no part in whether there is one: the caller
    computes the direction once per trip because the arrivals bucket reads it too,
    passes _prediction_observed_at of this trip_update as the clock, and passes
    `estimated` for a trip the position ladder puts at step 2 (memo D5).
    """
    tu = entity.trip_update
    # Not-yet-started filter. MNR carries the start; LIRR has no start_time, so start_ts
    # is None and nothing here filters an LIRR trip (the NOTE below says why the subway's
    # far-future-first-stop cap does not stand in for it).
    start_ts = _railroad_trip_start_ts(tu.trip)
    if start_ts is not None and start_ts > now + TRIP_START_GRACE_S:
        return None

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
        return None  # trip finished, or nothing resolvable
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
    # `direction` is the caller's, computed once per trip: direction_id (LIRR) or the
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
    # inline read arrivals uses in _decode_railroad_feed, kept separate so placement
    # output stays byte-identical to the pre-arrivals decoder.)
    placed_train_num = None
    if entity.HasField("vehicle"):
        placed_train_num = (entity.vehicle.vehicle.label or entity.vehicle.vehicle.id) or None
    return {
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
        # A PLACED TRAIN IS AS CURRENT AS THE PREDICTION THAT PLACED IT: the caller
        # passes _prediction_observed_at of this `tu`, the same trip_update whose
        # `chosen` stop set the latitude and longitude above. Not the header, not the
        # poll clock, and not the GPS reading it does not have.
        "observed_at": observed_at,
        # `placed` unless the caller says `estimated`, and the row sits AT a station's own
        # coordinates either way: the anchors beside it let a client glide between two
        # stations, but the position this decoder emits is the stop's. The placement pass
        # says `estimated` for a trip the position ladder puts at step 2, a vehicle whose
        # own fix has aged past OBS_FRESH_S while its trip's prediction has not (memo D5:
        # the same row, labeled for what drew it). Every other placed row stays `placed`.
        "provenance": provenance,
    }


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
    every other running trip is placed as far as the position ladder allows. Before
    that, the placement pass decided "has GPS" for itself, without the bounding box, and
    a positioned vehicle with an out-of-range coordinate reached neither surface.

    THE POSITION LADDER DECIDES WHAT BECOMES OF A VEHICLE'S TRIP (contract 6.3), and this
    pass builds it from the same inputs the GPS pass does. A trip drawn at its vehicle's
    own position (steps 1 and 3) is skipped, as it always was. A stale vehicle's trip
    whose prediction is within OBS_FRESH_S (step 2) is placed and labeled `estimated`,
    and one whose prediction is within OBS_MAX_S (step 4) is placed as `placed`. A
    vehicle with neither (step 5) is not placed at all: it is counted, and never drawn
    from a prediction older than OBS_MAX_S (on the committed LIRR capture 4 of the 24
    are placeable from predictions 1114 to 52538 s old). A trip with no vehicle behind
    it, and one whose vehicle the box rejected, is placed exactly as before, whatever
    its prediction's age (memo D1). On the committed LIRR capture at its header that is
    the 56 placements of trips with no accepted vehicle, unchanged, plus the 6
    estimates: 62.

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
    # THE SAME LADDER THE GPS PASS BUILT, from the same bytes, stops and `now`, so the
    # passes are complements under the age gate exactly as under the base rule.
    ladder = _position_ladder(feed, system, stops, now, canceled_trips)
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
                if _accepted_as_gps(entity, canceled_trips, ladder):
                    positioned_ids.add(v.trip.trip_id)

    # The prediction clock's only fallback (_prediction_observed_at, which says why).
    feed_header = _header_timestamp(feed)

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
        # shape, so no golden would have caught it. (_trip_update_is_canceled is the one
        # statement of it, which the position ladder asks too.)
        if _trip_update_is_canceled(tu, canceled_trips):
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
                    "observed_at": _prediction_observed_at(system, tu, feed_header),
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
        if _accepted_as_gps(entity, canceled_trips, ladder):
            continue
        if tu.trip.trip_id and tu.trip.trip_id in positioned_ids:
            continue
        # THE LADDER'S OTHER ANSWERS (contract 6.3). Step 5 is the suppression: nothing
        # honest is left to draw, so the trip is counted (_position_steps) and never
        # placed from a prediction older than OBS_MAX_S, which is what keeps the 4 of the
        # capture's 24 whose predictions are 1114 to 52538 s old off the map (memo D1's
        # 62, not 66). Steps 1 and 3 reach here only as one of a repeated trip's other
        # copies on the combined layout, whose own fix did not earn the step another copy
        # is already drawn at (_accepted_as_gps says why): placing its trip_update would
        # draw that train twice. Step 2 is placed and labeled `estimated`, step 4 placed,
        # and a trip no judged vehicle is behind (None) is placed exactly as it always was.
        step = _ladder_step_behind(entity, canceled_trips, ladder)
        if step in (1, 3, 5):
            continue

        # Everything else about placing a trip (the not-yet-started filter, the stop, the
        # anchors and the row) is _place_trip, the one answer the position ladder asks
        # too. `direction` is the one computed above for the arrivals bucket.
        placed = _place_trip(
            entity,
            system,
            stops,
            now,
            direction,
            _prediction_observed_at(system, tu, feed_header),
            "estimated" if step == 2 else "placed",
        )
        if placed is not None:
            trains.append(placed)

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
    dict[str, dict[str, int]],
]:
    """Fetch the LIRR and MNR feeds concurrently; return
    (trains, arrivals_by_system, feed_timestamp, failed_feeds, feed_ts_by_system,
    steps_by_system), where feed_ts_by_system carries each freshness-authoritative
    system's OWN header (contract 6.1) so a per-system block can name which contributor
    is behind. A system RAILROAD_FRESHNESS_SYSTEMS does not admit is simply absent from
    it. steps_by_system carries, for each system that decoded, what the position ladder
    did with its vehicles (_position_steps, contract 6.3): the counts models.PositionSteps
    serves, one per trip, which is the (system, trip_id) dedupe below. A failed system is
    absent from it, so its block keeps its last-known counts.

    Each feed contributes the GPS-positioned trains (_decode_railroad_vehicles)
    plus the position-less trains placed at their next station and a per-station
    arrivals index (both from _decode_railroad_feed, using railroad_stops[system]
    for coordinates; placement and arrivals are skipped for a system whose static
    stops are None, since neither can resolve stop_ids). BOTH PASSES GET THE SAME STOPS:
    the position ladder asks the placement pass's own answer to "can this stale
    vehicle's trip be estimated", and a GPS pass that answered without them would draw
    the six estimated LIRR trains at their own positions while the placement pass drew
    them at a station, one train twice. Trains are deduped by
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
    steps_by_system: dict[str, dict[str, int]] = {}
    # GPS pass first, so a positioned train wins its (system, trip_id) key.
    for system, result in zip(systems, results):
        if isinstance(result, BaseException):
            feed_errors[system] = str(result)
            continue
        stops = (railroad_stops or {}).get(system)
        try:
            gps, feed_ts = _decode_railroad_vehicles(result, system, now, stops)
            steps = _position_steps(result, system, stops, now)
        except DecodeError as exc:
            feed_errors[system] = f"undecodable protobuf ({exc})"
            continue
        raw_by_system[system] = result
        steps_by_system[system] = steps
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
    # A system the placement guard above failed keeps its last-known counts, as its
    # trains are retained rather than published (_refresh_railroads).
    position_steps = {s: c for s, c in steps_by_system.items() if s not in feed_errors}
    return (
        trains,
        arrivals_by_system,
        feed_timestamp,
        sorted(feed_errors),
        feed_ts_by_system,
        position_steps,
    )
