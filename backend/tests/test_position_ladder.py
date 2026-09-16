"""Contract 6.3: the position ladder, measured before anything reads it.

Section 3.4 of docs/design/freshness-contract.md orders what the map may draw for a
vehicle whose own observation has aged: its reported position while that is fresh, an
estimate from a fresh prediction, its reported position qualified while it is under ten
minutes old, a placement from a prediction under ten minutes old, and otherwise nothing.
The design measured that order on the committed LIRR capture at 27, 6, 11, 0 and 24.
feeds.railroad._position_ladder computes it, and since the gate commit both passes read
it through _accepted_as_gps and feeds.railroad._position_steps counts it for the
systems blocks. These tests ask the numbers of the ladder itself; test_feeds_railroad.py
asks the same numbers of what the two passes emit.

THE NUMBERS ARE ASKED OF THE DECODER, never of a golden, for the reason
test_feeds_railroad.py gives for F02's law: a golden is regenerated from whatever the
decoder does, so a count asked only of it would be regenerated into agreement.

BUILT WORLDS WHERE THE CAPTURE CANNOT REACH. Step 4 never fires on the capture, and the
design forbids an acceptance test that needs it to; Metro-North stamps every vehicle
with its own header, so the capture cannot show its exemption doing anything; no
LIRR trip carries two vehicles or two trip_updates, so the capture cannot show whether
feed order decides a step; the only undated LIRR trip_updates belong to canceled trips,
which are not placeable at all, so no vehicle on it rides a prediction with no clock; and
every positioned vehicle names its trip, so none is keyed by its entity id. Each is shown
on an in-memory copy of a committed capture with one rewrite (a field changed, cleared or
blanked, an entity repeated, or one entity added), named where it is made. The rest of the
capture rides along untouched, so every world still carries vehicles at many different ages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from google.transit import gtfs_realtime_pb2 as pb

import cache
import feeds
from feeds import railroad

FIXTURES = Path(__file__).parent / "fixtures"
BACKEND = Path(__file__).resolve().parent.parent

# The design's six (3.4, step 2): GPS 91 to 203 s old, and a prediction 4 or 5 s old
# that the placement pass places.
ESTIMATED = {
    "GO201_26_6187_1",
    "GO201_26_6468_2932_METS",
    "GO201_26_6188",
    "GO201_26_8768",
    "6029_2026-06-20",
    "GO201_26_6665",
}
# Its eleven (step 3): GPS 139 to 593 s old, and no placeable prediction within 90 s.
QUALIFIED = {
    "GO201_26_7987",
    "GO201_26_6186_1",
    "GO201_26_7990_2",
    "6017_2026-06-20",
    "6027_2026-06-20",
    "GO201_26_7766",
    "GO201_26_8960",
    "GO201_26_6040",
    "GO201_26_8971",
    "GO201_26_6570",
    "GO201_26_6369_2932_METS",
}
# The capture's oldest fix, train 521 at 53676 s (14h 54m 36s), the audit's witness.
OLDEST = "6006_2026-06-20"


def _capture(system: str):
    """A committed capture, parsed fresh so a test may rewrite it, and its stops."""
    key = system.lower()
    feed = pb.FeedMessage()
    feed.ParseFromString((FIXTURES / f"railroad_{key}.pb").read_bytes())
    stops = json.loads((FIXTURES / f"railroad_{key}_stops.json").read_text())
    return feed, stops


def _ladder(feed, system: str, stops, now: float | None = None) -> railroad.PositionLadder:
    """The ladder at `now`, which is the capture's own header unless a test moves it."""
    at = float(feed.header.timestamp) if now is None else now
    return railroad._position_ladder(feed, system, stops, at, railroad._canceled_trip_ids(feed))


def _counts(ladder: dict[str, int]) -> tuple[int, ...]:
    return tuple(sum(1 for step in ladder.values() if step == n) for n in range(1, 6))


def _at(ladder: dict[str, int], step: int) -> set[str]:
    return {trip for trip, s in ladder.items() if s == step}


def _vehicle(feed, trip_id: str):
    return next(
        e
        for e in feed.entity
        if e.HasField("vehicle")
        and e.vehicle.HasField("position")
        and e.vehicle.trip.trip_id == trip_id
    )


def _trip_update(feed, trip_id: str):
    return next(
        e
        for e in feed.entity
        if e.HasField("trip_update") and e.trip_update.trip.trip_id == trip_id
    )


def _placements(monkeypatch, feed, stops, now: float, system: str = "LIRR") -> list[dict]:
    """What the REAL placement pass places when the ladder judges no vehicle, in feed
    order: the row each trip's own prediction would produce at `now`, computed without
    the ladder. An empty ladder accepts nothing as GPS and withholds nothing, so the pass
    places every running trip, as it did for every trip with no accepted vehicle before
    the gate."""

    def judges_nothing(*_args) -> railroad.PositionLadder:
        return railroad.PositionLadder(system, False, now, 0.0, 0.0)

    with monkeypatch.context() as patched:
        patched.setattr(railroad, "_position_ladder", judges_nothing)
        return feeds._decode_railroad_placements(feed.SerializeToString(), system, stops, now)


def _placeable(monkeypatch, feed, stops, now: float) -> dict[str, dict]:
    """_placements by trip id, each trip's FIRST row, the one the live path's first-wins
    (system, trip_id) dedupe keeps."""
    rows: dict[str, dict] = {}
    for row in _placements(monkeypatch, feed, stops, now):
        rows.setdefault(row["trip_id"], row)
    return rows


def _repeated_vehicle(feed, trip_id: str, ages: tuple[int, ...]):
    """The capture with `trip_id`'s vehicle entity replaced, where it stood, by one copy
    per age in the order given, each under an entity id of its own and `age` seconds
    behind the header. The trip's trip_update and every other entity ride along."""
    header = int(feed.header.timestamp)
    world = pb.FeedMessage()
    world.header.CopyFrom(feed.header)
    for entity in feed.entity:
        if not (entity.HasField("vehicle") and entity.vehicle.trip.trip_id == trip_id):
            world.entity.add().CopyFrom(entity)
            continue
        for n, age in enumerate(ages):
            copy = world.entity.add()
            copy.CopyFrom(entity)
            copy.id = f"{entity.id}~{n}"
            copy.vehicle.timestamp = header - age
    return world


def _repeated_trip_update(feed, trip_id: str, updates: tuple[tuple[int, bool], ...]):
    """The capture with `trip_id`'s trip_update entity replaced, where it stood, by one
    copy per (age, placeable) in the order given: its clock `age` seconds behind the
    header, and its stop list emptied when it is not placeable, so _place_trip has no
    stop to choose (the shape of a fresh prediction about a finished trip)."""
    header = int(feed.header.timestamp)
    world = pb.FeedMessage()
    world.header.CopyFrom(feed.header)
    for entity in feed.entity:
        if not (entity.HasField("trip_update") and entity.trip_update.trip.trip_id == trip_id):
            world.entity.add().CopyFrom(entity)
            continue
        for n, (age, placeable) in enumerate(updates):
            copy = world.entity.add()
            copy.CopyFrom(entity)
            copy.id = f"{entity.id}~{n}"
            copy.trip_update.timestamp = header - age
            if not placeable:
                del copy.trip_update.stop_time_update[:]
    return world


# ---------------- the committed LIRR capture ----------------


def test_the_lirr_capture_orders_27_6_11_0_24():
    """Design 3.4's table, asked of the decoder at the capture's header."""
    feed, stops = _capture("LIRR")
    ladder = _ladder(feed, "LIRR", stops)
    assert _counts(ladder) == (27, 6, 11, 0, 24)
    assert _at(ladder, 2) == ESTIMATED
    assert _at(ladder, 3) == QUALIFIED
    # Step 4 fires for none of them. The design says why (every fresh prediction behind
    # the 24 is about a finished trip, and every placeable one is over ten minutes old)
    # and forbids a test that REQUIRES it to fire here; this one requires that it does
    # not, so a change that makes it fire on this capture is noticed and explained.
    assert _at(ladder, 4) == set()
    assert ladder[OLDEST] == 5


def test_the_ladder_covers_exactly_the_vehicles_the_base_rule_accepts():
    """Every vehicle the base rule accepts has a step and nothing else does, keyed as the
    GPS pass keys it: 68, the 69 positioned vehicles less F02's canceled one, all of which
    the GPS pass served before the gate. It now serves exactly their steps 1 and 3."""
    feed, stops = _capture("LIRR")
    header = float(feed.header.timestamp)
    canceled = railroad._canceled_trip_ids(feed)
    base = {
        e.vehicle.trip.trip_id or e.id
        for e in feed.entity
        if railroad._passes_base_rule(e, canceled)
    }
    assert len(base) == 68
    ladder = _ladder(feed, "LIRR", stops)
    assert set(ladder) == base
    gps, _ = feeds._decode_railroad_vehicles(feed.SerializeToString(), "LIRR", header, stops)
    assert {t["trip_id"] for t in gps} == _at(ladder, 1) | _at(ladder, 3)


def test_the_six_estimates_are_the_placement_passs_own_answer(monkeypatch):
    """Memo D4, computed the long way round and WITHOUT the ladder: of the 41 vehicles the
    base rule accepts whose own fix is over 90 s old (all 41 were served before the gate),
    the six are exactly those whose trip the real placement pass places from a prediction
    within 90 s. So "placeable" means one thing."""
    feed, stops = _capture("LIRR")
    header = float(feed.header.timestamp)
    placeable = _placeable(monkeypatch, feed, stops, header)
    canceled = railroad._canceled_trip_ids(feed)
    stale = {
        e.vehicle.trip.trip_id
        for e in feed.entity
        if railroad._passes_base_rule(e, canceled)
        and header - e.vehicle.timestamp > cache.OBS_FRESH_S
    }
    assert len(stale) == 41
    estimable = {
        trip
        for trip in stale
        if trip in placeable and header - placeable[trip]["observed_at"] <= cache.OBS_FRESH_S
    }
    assert estimable == ESTIMATED == _at(_ladder(feed, "LIRR", stops), 2)


def test_the_clock_is_the_header_and_not_now():
    """Memo D2. A minute after the header every step is the same, because every age is
    read against the header. Move the HEADER a minute instead and the steps move, to the
    split measured for a clock a minute late (11/21/10/0/26), so the sameness is the
    clock and not a coincidence of this capture."""
    feed, stops = _capture("LIRR")
    header = float(feed.header.timestamp)
    at_header = _ladder(feed, "LIRR", stops)
    assert _ladder(feed, "LIRR", stops, now=header + 60) == at_header

    late = pb.FeedMessage()
    late.CopyFrom(feed)
    late.header.timestamp = int(header) + 60
    assert _counts(_ladder(late, "LIRR", stops, now=header + 60)) == (11, 21, 10, 0, 26)


def test_an_hour_later_the_ages_hold_and_placeability_is_the_placement_passs(monkeypatch):
    """An hour after the header a POLL clock would put all 68 at step 5. The header
    clock keeps every age, so steps 1 and 5 do not move. What does move is placeability,
    which the ladder asks at `now` because the placement pass places at `now` (memo D4):
    five of the six have passed their next stop by then, the placement pass would draw
    nothing for them, and the ladder qualifies them (step 3) rather than withholding them
    for an estimate nobody draws. The sixth, 6029_2026-06-20 (next stop 5298 s after the
    header), stays an estimate, from a prediction still 5 s old against the header."""
    feed, stops = _capture("LIRR")
    header = float(feed.header.timestamp)
    at_header = _ladder(feed, "LIRR", stops)
    later = _ladder(feed, "LIRR", stops, now=header + 3600)
    assert _counts(later) == (27, 1, 16, 0, 24)
    assert _at(later, 1) == _at(at_header, 1)
    assert _at(later, 5) == _at(at_header, 5)
    assert _at(later, 2) == {"6029_2026-06-20"}
    unplaceable = ESTIMATED - set(_placeable(monkeypatch, feed, stops, header + 3600))
    assert unplaceable == ESTIMATED - {"6029_2026-06-20"}
    assert _at(later, 3) == QUALIFIED | unplaceable


@pytest.mark.parametrize("stops", [None, {}], ids=["stops-none", "stops-empty"])
def test_without_stops_nothing_is_placeable(stops):
    """Memo D4. With no static stops the placement pass never runs (fetch_railroad_trains
    skips it), so no estimate can be drawn, and the ladder must not withhold a vehicle
    for one: a stale vehicle within 600 s is qualified (step 3), never estimated, and one
    past 600 s is nothing (step 5)."""
    feed, _ = _capture("LIRR")
    ladder = _ladder(feed, "LIRR", stops)
    assert _counts(ladder) == (27, 0, 17, 0, 24)
    assert _at(ladder, 3) == QUALIFIED | ESTIMATED
    assert ladder[OLDEST] == 5


# ---------------- built worlds: the boundaries, step 4, and a missing clock ----------------


@pytest.mark.parametrize(("age", "step"), [(90, 1), (91, 3), (600, 3), (601, 5)])
def test_within_is_inclusive_on_the_vehicles_own_clock(age, step):
    """ "Within" is `<=`, the design's word. One vehicle's own clock is rewritten to sit on
    each threshold and one second past it. GO201_26_8945 is the capture's own case of a
    fresh prediction about a finished trip, so no prediction can place it and only its
    own clock decides its step."""
    feed, stops = _capture("LIRR")
    _vehicle(feed, "GO201_26_8945").vehicle.timestamp = int(feed.header.timestamp) - age
    assert _ladder(feed, "LIRR", stops)["GO201_26_8945"] == step


@pytest.mark.parametrize(("age", "step"), [(90, 2), (91, 4), (300, 4), (600, 4), (601, 5)])
def test_step_4_fires_in_a_built_world(age, step):
    """The world the capture cannot reach. GO201_26_7768 is one of the 24: GPS over 600 s
    old, and a future stop in a trip update 1114 s old, so its prediction places it and
    is too old to use. Rewrite only that trip update's clock. Inside OBS_MAX_S and older
    than OBS_FRESH_S it is placed (step 4); inside OBS_FRESH_S it is estimated (step 2,
    which the order asks first); past OBS_MAX_S it is nothing again."""
    feed, stops = _capture("LIRR")
    baseline = _ladder(feed, "LIRR", stops)
    assert baseline["GO201_26_7768"] == 5
    _trip_update(feed, "GO201_26_7768").trip_update.timestamp = int(feed.header.timestamp) - age
    ladder = _ladder(feed, "LIRR", stops)
    assert ladder["GO201_26_7768"] == step
    # The rewrite is the only difference between the two worlds.
    del ladder["GO201_26_7768"], baseline["GO201_26_7768"]
    assert ladder == baseline


def test_a_gated_vehicle_with_no_clock_is_drawn_and_qualified():
    """Design 3.2 clause (c): a null observed_at on an age-gated row is an anomaly, said
    at the observation ("age unknown"), so the vehicle is DRAWN at step 3 rather than
    dropped for an age nobody knows. Protobuf zero is absence (_railroad_observed_at).
    The oldest vehicle's prediction is placeable but 52538 s old, so nothing above step 3
    holds, with stops or without."""
    feed, stops = _capture("LIRR")
    _vehicle(feed, OLDEST).vehicle.timestamp = 0
    assert _ladder(feed, "LIRR", stops)[OLDEST] == 3
    assert _ladder(feed, "LIRR", None)[OLDEST] == 3


def test_a_fresh_estimate_outranks_an_undated_position():
    """The order applied to a vehicle with no clock. Step 2 is asked before step 3, and a
    position of unknown age is not within OBS_FRESH_S, so a placeable prediction that is
    (GO201_26_6187_1's, 4 s old) is what the vehicle becomes. Without stops, the same
    vehicle is drawn and qualified."""
    feed, stops = _capture("LIRR")
    _vehicle(feed, "GO201_26_6187_1").vehicle.timestamp = 0
    assert _ladder(feed, "LIRR", stops)["GO201_26_6187_1"] == 2
    assert _ladder(feed, "LIRR", None)["GO201_26_6187_1"] == 3


@pytest.mark.parametrize(
    ("trip", "step"),
    [(OLDEST, 5), ("GO201_26_7768", 5), ("GO201_26_6187_1", 3)],
    ids=["the-f01-witness", "a-prediction-past-600", "one-of-the-six"],
)
def test_an_undated_prediction_promotes_nobody(trip, step):
    """The undated rule's OTHER half, and the mirror of the test above. A trip_update with
    no clock of its own has an age nobody knows, and unknown is within neither limit, so it
    can carry no vehicle to step 2 or step 4: the witness stays at step 5, so does the one
    of the 24 whose prediction is 1114 s old, and one of the six falls back to its own
    position at step 3, qualified on a fix 91 s old.

    WHAT A ROW IS SERVED AND WHAT THE LADDER GATES ON DIFFER HERE, on purpose. The served
    clock keeps the feed header as its fallback, which design 3.3 blesses
    (_prediction_observed_at); gating on that value scored an undated prediction 0 s old
    whenever the ladder's clock IS the header, the freshest number on the feed, off a stamp
    the provider never sent. Clearing one trip_update's clock then moved 9 of the 68
    vehicles up to step 2, four of them from step 5, and the witness among them: train 521,
    its own fix 53676 s old, drawn as an estimate dated now. The ladder reads the
    prediction's own clock instead (_first_placeable_row's second element)."""
    feed, stops = _capture("LIRR")
    baseline = _ladder(feed, "LIRR", stops)
    _trip_update(feed, trip).trip_update.timestamp = 0
    ladder = _ladder(feed, "LIRR", stops)
    assert ladder[trip] == step
    # The cleared clock is the only difference between the two worlds.
    del ladder[trip], baseline[trip]
    assert ladder == baseline


def test_the_captures_undated_trip_updates_are_all_canceled_trips():
    """Why the committed counts do not move when the ladder stops reading the header as a
    prediction's clock, stated as a fact about the capture rather than left to the
    goldens: its 5 undated trip_updates are its canceled trips, which are not placeable at
    all (_trip_update_is_canceled), so no vehicle was ever promoted by one. That is what
    makes the worlds above built ones."""
    feed, stops = _capture("LIRR")
    canceled = railroad._canceled_trip_ids(feed)
    undated = [
        e.trip_update
        for e in feed.entity
        if e.HasField("trip_update") and not e.trip_update.timestamp
    ]
    assert len(undated) == 5
    assert all(railroad._trip_update_is_canceled(tu, canceled) for tu in undated)
    assert _counts(_ladder(feed, "LIRR", stops)) == (27, 6, 11, 0, 24)


def test_a_vehicle_with_no_trip_id_is_keyed_where_no_trip_update_can_reach_it():
    """The key such a vehicle takes, and the set that records it. Blank GO201_26_7768's
    vehicle trip id and leave its trip_update alone: the ladder judges the same vehicle
    under its ENTITY id, at the step its own fix earns (5, its fix past OBS_MAX_S, and
    nothing placeable behind it, because a vehicle with no trip id has no trip_update to be
    placed from). The key goes into `unjoinable`, because the placement pass reads a
    verdict back by trip id alone (_ladder_step_behind, positioned_ids) and will place that
    trip as though it had no vehicle at all. _position_steps reads the set before it calls
    such a vehicle suppressed; test_feeds_railroad.py asks what it counts instead."""
    feed, stops = _capture("LIRR")
    baseline = _ladder(feed, "LIRR", stops)
    assert baseline.unjoinable == set(), "every vehicle on the capture names its trip"
    entity = _vehicle(feed, "GO201_26_7768")
    entity.vehicle.trip.trip_id = ""
    ladder = _ladder(feed, "LIRR", stops)
    assert "GO201_26_7768" not in ladder
    assert ladder[entity.id] == 5 == baseline["GO201_26_7768"]
    assert ladder.unjoinable == {entity.id}
    # One key renamed, nothing else moved: the counts are the capture's own.
    assert _counts(ladder) == _counts(baseline) == (27, 6, 11, 0, 24)


# ---------------- built worlds: a trip the feed reports twice ----------------

# GO201_26_8945 has no placeable prediction (see the boundary test above), so a copy of
# its vehicle is decided by its own fix alone: each age's step with no other copy.
ALONE = {700: 5, 300: 3, 1: 1}


@pytest.mark.parametrize(
    ("ages", "step"), [((700, 1), 1), ((1, 700), 1), ((700, 300), 3), ((300, 700), 3)]
)
def test_a_repeated_vehicle_takes_its_best_step_in_either_order(ages, step):
    """Two vehicle entities for one trip, at two ages. The trip takes the best step its
    vehicles earn, whichever stands first in the feed. Taking the first entity's step,
    the rule this replaced, made the 700-then-1 world step 5, a train withheld and
    counted while its own feed carried a fix one second old, and made the same two
    copies step 1 the other way round."""
    feed, stops = _capture("LIRR")
    baseline = _ladder(feed, "LIRR", stops)
    for age in ages:
        alone = _ladder(_repeated_vehicle(feed, "GO201_26_8945", (age,)), "LIRR", stops)
        assert alone["GO201_26_8945"] == ALONE[age], age
    ladder = _ladder(_repeated_vehicle(feed, "GO201_26_8945", ages), "LIRR", stops)
    assert ladder["GO201_26_8945"] == step
    # One trip, one step: the repeat adds no key, and nothing else moves.
    del ladder["GO201_26_8945"], baseline["GO201_26_8945"]
    assert ladder == baseline


@pytest.mark.parametrize(
    ("updates", "step", "kept_age"),
    [
        (((4, True), (300, True)), 2, 4),
        (((300, True), (4, True)), 3, 300),
        (((4, False), (4, True)), 2, 4),
        (((4, True), (4, False)), 2, 4),
    ],
    ids=["fresh-then-old", "old-then-fresh", "finished-then-fresh", "fresh-then-finished"],
)
def test_the_first_placeable_trip_update_answers(monkeypatch, updates, step, kept_age):
    """Two trip_updates for one of the six. GO201_26_6187_1's own fix is 91 s old, so a
    placeable prediction within 90 s makes it an estimate (step 2) and one 300 s old
    leaves it qualified (step 3). The FIRST placeable trip_update in feed order answers,
    because its row is the one the placement pass emits first and the live path's
    first-wins dedupe keeps: the ladder judges the prediction the map would draw, not a
    better one behind it. One that cannot be placed (its stops emptied) is passed over,
    on either side."""
    feed, stops = _capture("LIRR")
    header = float(feed.header.timestamp)
    world = _repeated_trip_update(feed, "GO201_26_6187_1", updates)
    assert _ladder(world, "LIRR", stops)["GO201_26_6187_1"] == step
    rows = [
        t
        for t in _placements(monkeypatch, world, stops, header)
        if t["trip_id"] == "GO201_26_6187_1"
    ]
    assert header - rows[0]["observed_at"] == kept_age


# ---------------- Metro-North: exempt by policy, not by name ----------------


def _mnr_aged(seconds: int):
    """The committed Metro-North capture with every positioned vehicle's stamp moved
    `seconds` behind its header, and every trip_update stamped AT the header. The capture
    itself cannot show the exemption: all 49 stamps ARE the header, so a gate on them
    would pass everything and prove nothing.

    THE PREDICTION CLOCKS ARE SUPPLIED FOR THE SAME REASON. Metro-North dates none of its
    119 trip_updates, and the ladder gates on a prediction's OWN clock, never on the
    header standing in for it (_first_placeable_row), so a gated world that left them
    undated could not reach step 2 or step 4 at all and would be a world about a provider
    nobody ships: a system whose positions are worth gating dates its predictions, as
    LIRR dates 127 of its 132. Stamping them at the header makes each prediction 0 s old
    against the ladder's clock, which is what the 23 estimates below are."""
    feed, stops = _capture("MNR")
    header = int(feed.header.timestamp)
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            entity.trip_update.timestamp = header
        if entity.HasField("vehicle") and entity.vehicle.HasField("position"):
            entity.vehicle.timestamp = header - seconds
    return feed, stops


def test_the_position_policy_is_the_freshness_set():
    assert railroad._position_age_gated("LIRR") is True
    assert railroad._position_age_gated("MNR") is False
    for system in feeds.RAILROAD_FEED_URLS:
        gated = railroad._position_age_gated(system)
        assert gated == (system in railroad.RAILROAD_FRESHNESS_SYSTEMS), system


def test_metro_north_is_exempt_by_policy(monkeypatch):
    """Metro-North's position row is not age-gated (3.3), because its stamps copy a
    header that lags two to four minutes. In a world where every one of its vehicles is
    700 s behind the header, past OBS_MAX_S, every one is still step 1. Admit it to the
    policy set and the SAME world is gated, through the combined layout's own
    trip_updates, which the world stamps at the header (_mnr_aged says why): each
    prediction is 0 s old, the 23 trains whose own entity places them are estimated, and
    the 10 it cannot place are not drawn at all. So the exemption is the set's, where
    design 4.2 says it lives, and not a branch on the name."""
    committed, stops = _capture("MNR")
    assert set(_ladder(committed, "MNR", stops).values()) == {1}

    feed, stops = _mnr_aged(700)
    exempt = _ladder(feed, "MNR", stops)
    assert len(exempt) == 33, "the 33 markers the live path draws from 49 entities"
    assert set(exempt.values()) == {1}

    monkeypatch.setattr(railroad, "RAILROAD_FRESHNESS_SYSTEMS", frozenset({"LIRR", "MNR"}))
    assert railroad._position_age_gated("MNR")
    gated = _ladder(feed, "MNR", stops)
    assert set(gated) == set(exempt)
    assert _counts(gated) == (0, 23, 0, 0, 10)


def test_a_combined_entity_canceled_by_another_trip_update_is_not_placeable(monkeypatch):
    """The ladder's cancellation check, on the one layout where it can bite. A split
    (LIRR) vehicle whose trip is canceled never passes the base rule. A COMBINED entity
    carries its own trip_update and _vehicle_is_canceled reads only that one, so a second
    trip_update canceling the same trip leaves the vehicle accepted while the placement
    pass drops the trip; the ladder must not count that prediction either. The world is
    the aged Metro-North one admitted to the policy set, where train 6361's own entity
    places it (step 2), plus one trip_update canceling that entity's trip."""
    feed, stops = _mnr_aged(700)
    monkeypatch.setattr(railroad, "RAILROAD_FRESHNESS_SYSTEMS", frozenset({"LIRR", "MNR"}))
    header = float(feed.header.timestamp)
    (own,) = [
        e
        for e in feed.entity
        if e.HasField("vehicle")
        and e.vehicle.HasField("position")
        and e.vehicle.trip.trip_id == "6361"
    ]
    trip = own.trip_update.trip.trip_id
    assert _ladder(feed, "MNR", stops)["6361"] == 2
    assert trip in {t["trip_id"] for t in _placements(monkeypatch, feed, stops, header, "MNR")}

    cancel = feed.entity.add()
    cancel.id = "cancels-6361"
    cancel.trip_update.trip.trip_id = trip
    cancel.trip_update.trip.schedule_relationship = pb.TripDescriptor.CANCELED
    assert railroad._passes_base_rule(own, railroad._canceled_trip_ids(feed)), "still accepted"
    assert trip not in {t["trip_id"] for t in _placements(monkeypatch, feed, stops, header, "MNR")}
    assert _ladder(feed, "MNR", stops)["6361"] == 5


# ---------------- the two numbers ----------------


def test_obs_fresh_s_is_the_riders_threshold():
    """Design 3.3: one number, not two."""
    assert cache.OBS_FRESH_S == cache.FEED_STALE_AFTER_S == 90


def test_obs_max_s_is_the_retention_caps_number_and_is_read_at_decode_time(monkeypatch):
    """Q2's 600, equal to FEED_RETENTION_MAX_S where its seam is unset, which is here: the
    hermetic suite sets no seam. Then compress the cap in-process, the way the contract
    tier compresses it, and neither OBS_MAX_S nor the ladder moves, because the decoder
    reads OBS_MAX_S and never the cap."""
    assert "FEED_RETENTION_MAX_S" not in os.environ, "this suite runs with no seam set"
    assert cache.OBS_MAX_S == 600 == cache.FEED_RETENTION_MAX_S
    monkeypatch.setattr(cache, "FEED_RETENTION_MAX_S", 20)
    assert cache.OBS_MAX_S == 600
    assert railroad._observation_limits() == (90.0, 600.0)
    feed, stops = _capture("LIRR")
    assert _counts(_ladder(feed, "LIRR", stops)) == (27, 6, 11, 0, 24)


def test_the_retention_seam_does_not_move_obs_max_s():
    """The seam the contract tier really sets, in a fresh interpreter (the way
    test_env_seams proves every seam): FEED_RETENTION_MAX_S=20 moves the cap and leaves
    OBS_MAX_S at 600. An alias, OBS_MAX_S = FEED_RETENTION_MAX_S, would print 20 twice."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, '.'); import cache; "
            "print(cache.FEED_RETENTION_MAX_S, cache.OBS_MAX_S)",
        ],
        cwd=BACKEND,
        env={**os.environ, "FEED_RETENTION_MAX_S": "20"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["20.0", "600.0"]


@pytest.mark.parametrize(
    ("max_s", "expected"),
    [(600.0, (27, 6, 11, 0, 24)), (1800.0, (27, 6, 26, 0, 9)), (3600.0, (27, 6, 31, 0, 4))],
)
def test_obs_max_s_is_the_number_that_decides_whether_the_24_exist(monkeypatch, max_s, expected):
    """Q2's sensitivity through the real ladder, which is also the proof that the decoder
    reads cache.OBS_MAX_S when it runs rather than a copy: at 1800 s 15 of the 24 stay,
    drawn and qualified, and 9 go; at 3600 s 20 stay and 4 go."""
    monkeypatch.setattr(cache, "OBS_MAX_S", max_s)
    feed, stops = _capture("LIRR")
    assert _counts(_ladder(feed, "LIRR", stops)) == expected
