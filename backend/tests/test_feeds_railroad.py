"""Golden + unit tests for the railroad (LIRR / MNR) decode: GPS + placement.

Like test_feeds_golden.py, the riskiest part is decoding the true shape of the
feed, so these lock _decode_railroad_vehicles (GPS) and _decode_railroad_placements
(station placement of the position-less trains) against real captured payloads
(the bytes carry no PII) with `now` frozen to each feed's header timestamp.
Synthetic feeds cover the route_id-join layouts, the position filter, and the
placement edges. The placement golden also uses the committed per-system stops
(railroad_{lirr,mnr}_stops.json), so no test touches the network.

To regenerate the GPS golden after an INTENTIONAL decode change, from backend/. It
passes the committed stops since contract 6.3: the position ladder asks the placement
pass whether a stale vehicle's trip can be estimated instead, so the GPS pass decodes
with the stops the placement golden uses, as fetch_railroad_trains hands both passes
the same ones.

    python - <<'PY'
    import json
    from pathlib import Path
    from google.transit import gtfs_realtime_pb2 as pb
    import feeds
    FIX = Path("tests/fixtures")
    for system in ("LIRR", "MNR"):
        key = system.lower()
        raw = (FIX / f"railroad_{key}.pb").read_bytes()
        stops = json.loads((FIX / f"railroad_{key}_stops.json").read_text())
        feed = pb.FeedMessage(); feed.ParseFromString(raw)
        now = float(feed.header.timestamp)
        trains, _ = feeds._decode_railroad_vehicles(raw, system, now, stops)
        (FIX / f"railroad_{key}_expected.json").write_text(
            json.dumps({"now": now, "system": system, "trains": trains}, indent=0))
    PY

To regenerate the PLACEMENT golden (network-free, using the committed stops.json):

    python - <<'PY'
    import json
    from pathlib import Path
    from google.transit import gtfs_realtime_pb2 as pb
    import feeds
    FIX = Path("tests/fixtures")
    for system in ("LIRR", "MNR"):
        key = system.lower()
        raw = (FIX / f"railroad_{key}.pb").read_bytes()
        stops = json.loads((FIX / f"railroad_{key}_stops.json").read_text())
        feed = pb.FeedMessage(); feed.ParseFromString(raw)
        now = float(feed.header.timestamp)
        placed = feeds._decode_railroad_placements(raw, system, stops, now)
        (FIX / f"railroad_{key}_placed_expected.json").write_text(
            json.dumps({"now": now, "system": system, "trains": placed}, indent=0))
    PY

The stops.json fixtures themselves are regenerated from the static GTFS via
railroad_static.load_railroad_static() only when the static parsing changes.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

import cache
import feeds

FIXTURES = Path(__file__).parent / "fixtures"
SYSTEMS = ["LIRR", "MNR"]


def _load(system: str):
    key = system.lower()
    raw = (FIXTURES / f"railroad_{key}.pb").read_bytes()
    expected = json.loads((FIXTURES / f"railroad_{key}_expected.json").read_text())
    return raw, expected


# ---------------- golden ----------------


@pytest.mark.parametrize("system", SYSTEMS)
def test_real_feed_decodes_to_golden_output(system):
    raw, expected = _load(system)
    # With the committed stops, as the recipe above decodes it (contract 6.3).
    stops = json.loads((FIXTURES / f"railroad_{system.lower()}_stops.json").read_text())
    trains, feed_ts = feeds._decode_railroad_vehicles(
        raw, expected["system"], expected["now"], stops
    )
    assert trains == expected["trains"]
    # The decoder reads the header timestamp the fixture was frozen to.
    assert feed_ts == expected["now"]


@pytest.mark.parametrize("system", SYSTEMS)
def test_golden_output_is_nontrivial(system):
    # Guard the guard: an empty fixture would make the equality test vacuous.
    _, expected = _load(system)
    assert len(expected["trains"]) > 10


@pytest.mark.parametrize("system", SYSTEMS)
def test_every_golden_train_is_well_formed(system):
    _, expected = _load(system)
    for train in expected["trains"]:
        assert train["system"] == system
        assert feeds.RAILROAD_LAT_MIN <= train["latitude"] <= feeds.RAILROAD_LAT_MAX
        assert feeds.RAILROAD_LON_MIN <= train["longitude"] <= feeds.RAILROAD_LON_MAX
        # GPS trains are positions only: every anchor + direction field is null,
        # and they carry no station id/name (they are not placed at a stop).
        for field in (
            "stop_id",
            "stop_name",
            "direction",
            "prev_lat",
            "prev_lon",
            "prev_time",
            "next_time",
        ):
            assert train[field] is None


# ---------------- the cancellation invariant (F02) ----------------
#
# THE GOLDEN ABOVE IS A SNAPSHOT AND THIS IS A LAW. Equality against a committed
# list catches a decode change today, but a future recapture regenerates that list
# from whatever the decoder then does, so a canceled train creeping back into the
# GPS output would simply be blessed into the new golden and the equality test would
# go green over it. That is exactly how F02 survived: the canceled 5-train (508) sat
# FIRST in the committed golden, matching byte for byte, for as long as the fixture
# existed. The invariant below is computed from the CAPTURE rather than from the
# golden, so it cannot be regenerated into agreement.


# The audit's own witness: the one LIRR trip that is both canceled by a TripUpdate and
# carrying a positioned vehicle. Named once so the tests below read as claims about it
# rather than about a string.
CANCELED_TRIP = "6004XX_2026-06-20"


def _canceled_trip_ids(raw: bytes) -> set[str]:
    """Trips this feed itself marks canceled or deleted, read straight off the wire.

    NOT THROUGH THE DECODER, deliberately. Computing the denominator with the code
    whose output is the numerator would make a decoder that emitted nothing look
    like a feed with nothing canceled in it, which is the failure being watched for.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    return {
        entity.trip_update.trip.trip_id
        for entity in feed.entity
        if entity.HasField("trip_update")
        and entity.trip_update.trip.trip_id
        and entity.trip_update.trip.schedule_relationship in feeds._DROP_TRIP_RELATIONSHIPS
    }


def _positioned_vehicle_trip_ids(raw: bytes) -> set[str]:
    """Trip ids of every vehicle in this feed that carries a position, off the wire.

    Keyed the way _vehicle_is_canceled joins the SEPARATE-entity layout, which is
    LIRR's and the one F02 was found in. MNR is combined, so its cancellations are
    read from the entity itself and never reach this set; that arm is covered by
    test_a_canceled_combined_entity_is_not_emitted below.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    return {
        entity.vehicle.trip.trip_id
        for entity in feed.entity
        if entity.HasField("vehicle")
        and entity.vehicle.HasField("position")
        and entity.vehicle.trip.trip_id
    }


def _aged_past_obs_max(raw: bytes) -> set[str]:
    """Trip ids of the positioned vehicles whose own fix is older than OBS_MAX_S against the
    header, off the wire. With no stops passed nothing is placeable, so these are exactly
    the vehicles F01's age gate withholds from the GPS pass (contract 6.3, step 5)."""
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    header = float(feed.header.timestamp)
    return {
        entity.vehicle.trip.trip_id
        for entity in feed.entity
        if entity.HasField("vehicle")
        and entity.vehicle.HasField("position")
        and entity.vehicle.trip.trip_id
        and header - entity.vehicle.timestamp > cache.OBS_MAX_S
    }


def _with_the_witness_made_fresh(raw: bytes, running: bool = False) -> bytes:
    """The capture with every canceled trip's positioned vehicle stamped 10 s behind the
    header and, with `running`, its canceled TripUpdates flipped to SCHEDULED.

    WHY F02'S LAW NEEDS THIS REWRITE SINCE CONTRACT 6.3. The capture's canceled-and-
    positioned vehicle (train 508) is 50517 s old against its header, so F01's age gate
    withholds it from the GPS pass whatever its TripUpdate says, and a decoder with the
    cancellation guard removed would still emit nothing canceled from the capture as
    committed: the law would pass over the very defect it exists to catch. At 10 s the
    same vehicle earns step 1, which is drawn, so cancellation is the only rule left that
    can drop it. `running` is the control that proves the rewrite leaves it drawable.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    canceled = _canceled_trip_ids(raw)
    header = int(feed.header.timestamp)
    for entity in feed.entity:
        if (
            entity.HasField("vehicle")
            and entity.vehicle.HasField("position")
            and entity.vehicle.trip.trip_id in canceled
        ):
            entity.vehicle.timestamp = header - 10
        if (
            running
            and entity.HasField("trip_update")
            and entity.trip_update.trip.trip_id in canceled
        ):
            entity.trip_update.trip.schedule_relationship = pb.TripDescriptor.SCHEDULED
    return feed.SerializeToString()


@pytest.mark.parametrize("system", SYSTEMS)
def test_no_emitted_train_has_a_canceled_trip_update(system):
    """F02's law: a trip this feed says is canceled is not on the map as a train.

    A canceled trip keeps a live GPS entity that keeps moving, because the train
    physically exists and is deadheading, repositioning or running empty. It is not
    service, both of its station boards already omit it, and drawing it as a
    boardable train is the falsehood F02 named.

    ASKED OF THE DECODER FIRST, AND OF THE GOLDEN SECOND, and the order is the whole
    point. The committed golden is regenerated from whatever the decoder does, so a
    law asked only of the golden would be regenerated into agreement the moment the
    guard came out: it would go green over the very defect it exists to catch, which
    is how F02 survived in the first place. Decoding here means removing the guard
    fails THIS test, not merely the equality snapshot beside it. The decode reads the
    capture with its witness's fix made fresh, because since contract 6.3 the witness as
    committed is past OBS_MAX_S and the age gate alone would keep it off the map
    (_with_the_witness_made_fresh says why that would make this law vacuous).
    """
    raw, expected = _load(system)
    canceled = _canceled_trip_ids(raw)

    decoded, _ts = feeds._decode_railroad_vehicles(
        _with_the_witness_made_fresh(raw), system, expected["now"]
    )
    live = {train["trip_id"] for train in decoded}
    assert canceled & live == set(), (
        f"{system}: the decoder emitted canceled trips: {sorted(canceled & live)}"
    )

    committed = {train["trip_id"] for train in expected["trains"]}
    assert canceled & committed == set(), (
        f"{system}: canceled trips are recorded in the golden: {sorted(canceled & committed)}"
    )


def test_the_lirr_capture_still_witnesses_the_cancellation_it_was_kept_for():
    """THE WITNESS, ASSERTED SO THE LAW ABOVE CANNOT GO VACUOUS.

    A recapture whose feed happens to carry no canceled-and-positioned trip would
    satisfy the invariant trivially, and F02's evidence would quietly retire without
    anyone deciding to retire it. This is the fixture's own statement that it still
    contains the shape: exactly one trip both canceled by a TripUpdate and carrying a
    positioned vehicle, which is the audit's 6004XX_2026-06-20, train 508 on route 5,
    and which the committed golden used to list FIRST of 69.

    If a recapture loses it, replace the capture with one that has it rather than
    deleting this test: the law above is only worth anything while something in the
    corpus can break it.
    """
    raw, _ = _load("LIRR")
    both = _canceled_trip_ids(raw) & _positioned_vehicle_trip_ids(raw)
    assert both == {CANCELED_TRIP}, f"the canceled-and-positioned witness moved: {sorted(both)}"

    # AND THE DECODER DROPS IT, THOUGH SINCE CONTRACT 6.3 NOT IT ALONE. The difference was
    # 1 (69 positioned on the wire, 68 served) and is now 25: F01's age gate also
    # withholds every vehicle whose own fix is past OBS_MAX_S against the header. No stops
    # are passed, so nothing is placeable and the six the ladder would estimate stay here,
    # qualified; `now` 0.0 moves nothing, because the gate reads the header. The witness
    # is past OBS_MAX_S itself (50517 s), so it is one of those 25 for two reasons, and
    # the count alone cannot say which rule dropped it.
    positioned = _positioned_vehicle_trip_ids(raw)
    aged = _aged_past_obs_max(raw)
    assert CANCELED_TRIP in aged and len(aged) == 25
    served = {t["trip_id"] for t in feeds._decode_railroad_vehicles(raw, "LIRR", 0.0)[0]}
    assert len(positioned) - len(served) == 25
    assert positioned - served == aged

    # SO THE WITNESS IS MADE FRESH, and cancellation is the only rule left that can drop
    # it: the same 25 are dropped, the witness now for its cancellation alone. Its
    # TripUpdate flipped to SCHEDULED puts it back on the map, drawn at step 1.
    fresh = _with_the_witness_made_fresh(raw)
    served = {t["trip_id"] for t in feeds._decode_railroad_vehicles(fresh, "LIRR", 0.0)[0]}
    assert CANCELED_TRIP not in served and positioned - served == aged
    running = _with_the_witness_made_fresh(raw, running=True)
    served = {t["trip_id"] for t in feeds._decode_railroad_vehicles(running, "LIRR", 0.0)[0]}
    assert positioned - served == aged - {CANCELED_TRIP}


def test_the_vehicles_own_relationship_is_not_the_signal():
    """WHY THE JOIN IS LOAD-BEARING, pinned rather than only commented.

    On the committed capture the canceled train's VEHICLE entity reports SCHEDULED
    while its TripUpdate reports CANCELED. A decoder that read the vehicle's own
    schedule_relationship would therefore emit it exactly as before the fix, and
    every other test here would still pass.
    """
    raw, _ = _load("LIRR")
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    vehicle = next(
        e for e in feed.entity if e.HasField("vehicle") and e.vehicle.trip.trip_id == CANCELED_TRIP
    )
    sr = pb.TripDescriptor.ScheduleRelationship
    assert sr.Name(vehicle.vehicle.trip.schedule_relationship) == "SCHEDULED"
    assert vehicle.vehicle.trip.schedule_relationship not in feeds._DROP_TRIP_RELATIONSHIPS


def test_a_canceled_combined_entity_is_not_emitted():
    """The MNR arm, which the trip_id join cannot reach.

    MNR combines the trip_update and the vehicle in one entity and its
    vehicle.trip.trip_id is the TRAIN NUMBER, not the trip_update's internal id, so
    a cancellation there is only visible on the entity itself. Synthetic because the
    committed MNR capture carries no canceled trip: 119 combined entities, none
    dropped, which is why this arm needs a feed of its own rather than a fixture.
    """
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1000

    def add(entity_id, *, canceled):
        entity = feed.entity.add()
        entity.id = entity_id
        entity.trip_update.trip.trip_id = f"internal-{entity_id}"
        entity.trip_update.trip.route_id = "1"
        if canceled:
            entity.trip_update.trip.schedule_relationship = pb.TripDescriptor.CANCELED
        entity.vehicle.trip.trip_id = entity_id  # MNR: the train number, not the id
        entity.vehicle.position.latitude = 40.9
        entity.vehicle.position.longitude = -73.8
        entity.vehicle.vehicle.label = entity_id

    add("1797", canceled=False)
    add("1799", canceled=True)

    trains, _ts = feeds._decode_railroad_vehicles(feed.SerializeToString(), "MNR", 1000.0)
    assert [t["trip_id"] for t in trains] == ["1797"]


# ---------------- the age gate (F01, contract 6.3) ----------------
#
# THE GOLDENS ARE SNAPSHOTS AND THESE ARE LAWS, for the reason F02's are: a golden is
# regenerated from whatever the decoder does, so the numbers design 3.4 measured are asked
# here of what the two passes EMIT, off the committed capture at its header with its
# stops, and never of a golden. tests/test_position_ladder.py asks them of the ladder
# itself.

# The design's six (3.4, step 2): a fix 91 to 203 s old, and a prediction 4 or 5 s old
# that the placement pass places. tests/test_position_ladder.py names the same six.
ESTIMATED = {
    "GO201_26_6187_1",
    "GO201_26_6468_2932_METS",
    "GO201_26_6188",
    "GO201_26_8768",
    "6029_2026-06-20",
    "GO201_26_6665",
}


def _stops(system: str) -> dict:
    return json.loads((FIXTURES / f"railroad_{system.lower()}_stops.json").read_text())


def _both_passes(system: str, raw: bytes):
    """Both surfaces at the capture's own header, with its stops, as the live path
    decodes them: (header, GPS rows, placement rows)."""
    header = float(pb.FeedMessage.FromString(raw).header.timestamp)
    stops = _stops(system)
    gps, _ts = feeds._decode_railroad_vehicles(raw, system, header, stops)
    placed = feeds._decode_railroad_placements(raw, system, stops, header)
    return header, gps, placed


def _own_fix_ages(raw: bytes) -> dict[str, float]:
    """Each positioned vehicle's own fix age against the header, by trip id, off the
    wire. No LIRR trip id repeats on the capture, so the mapping loses nothing."""
    feed = pb.FeedMessage.FromString(raw)
    header = float(feed.header.timestamp)
    return {
        e.vehicle.trip.trip_id: header - e.vehicle.timestamp
        for e in feed.entity
        if e.HasField("vehicle") and e.vehicle.HasField("position")
    }


def test_the_lirr_capture_splits_27_6_11_0_24_on_the_served_surfaces():
    """Design 3.4's table, asked of what the two passes emit. Of the 68 vehicles the base
    rule accepts (69 on the wire less F02's canceled one; none is outside the box): 27 are
    GPS rows whose own fix is within OBS_FRESH_S, 11 GPS rows past it and within
    OBS_MAX_S, 6 placement rows labeled `estimated`, 0 placement rows of a vehicle's trip
    labeled `placed`, and 24 on no surface, every one past OBS_MAX_S. Nothing is drawn
    twice, and _position_steps, which the systems blocks serve, counts the same split."""
    raw = _raw("LIRR")
    header, gps, placed = _both_passes("LIRR", raw)
    ages = _own_fix_ages(raw)
    vehicles = set(ages) - _canceled_trip_ids(raw)
    assert len(vehicles) == 68
    served = {t["trip_id"] for t in gps}
    reported = {t for t in served if ages[t] <= cache.OBS_FRESH_S}
    qualified = {t for t in served if ages[t] > cache.OBS_FRESH_S}
    estimated = {t["trip_id"] for t in placed if t["provenance"] == "estimated"}
    placed_vehicles = {t["trip_id"] for t in placed if t["provenance"] == "placed"} & vehicles
    nowhere = vehicles - served - estimated - placed_vehicles
    split = (len(reported), len(estimated), len(qualified), len(placed_vehicles), len(nowhere))
    assert split == (27, 6, 11, 0, 24)
    assert served & {t["trip_id"] for t in placed} == set(), "no train drawn twice"
    assert all(ages[t] > cache.OBS_MAX_S for t in nowhere)
    assert feeds.railroad._position_steps(raw, "LIRR", _stops("LIRR"), header) == {
        "reported": 27,
        "estimated": 6,
        "qualified": 11,
        "placed": 0,
        "suppressed": 24,
    }


def test_no_served_lirr_gps_row_is_older_than_obs_max_s():
    """F01's acceptance, the half the GPS pass owns: a fresh header carrying an old vehicle
    observation never produces a GPS row older than OBS_MAX_S, with the stops or without
    them. The oldest served fix was 53676 s (14h 54m 36s, train 521) before the gate and
    is 593 s now; every row past OBS_FRESH_S is one the rider's client qualifies."""
    raw = _raw("LIRR")
    header, gps, _placed = _both_passes("LIRR", raw)
    oldest = max(header - t["observed_at"] for t in gps)
    assert oldest == 593.0 and oldest <= cache.OBS_MAX_S
    bare, _ts = feeds._decode_railroad_vehicles(raw, "LIRR", header)
    assert max(header - t["observed_at"] for t in bare) <= cache.OBS_MAX_S


def test_the_six_estimated_trains_are_placement_rows_labeled_estimated():
    """The design's six, by trip id: each vehicle's own fix is stale (91 to 203 s), its
    trip's prediction is fresh (4 or 5 s), and it is drawn once, by the placement pass,
    as a placement row with provenance `estimated` (memo D5): station coordinates, a
    timed next stop and a previous-stop anchor to glide from."""
    raw = _raw("LIRR")
    header, gps, placed = _both_passes("LIRR", raw)
    ages = _own_fix_ages(raw)
    coords = {(s["lat"], s["lon"]) for s in _stops("LIRR").values()}
    rows = {t["trip_id"]: t for t in placed if t["provenance"] == "estimated"}
    assert set(rows) == ESTIMATED
    assert not ESTIMATED & {t["trip_id"] for t in gps}
    for trip, row in rows.items():
        assert cache.OBS_FRESH_S < ages[trip] <= 203.0, trip
        assert header - row["observed_at"] <= cache.OBS_FRESH_S, trip
        assert (row["latitude"], row["longitude"]) in coords, trip
        assert row["next_time"] is not None and row["prev_time"] is not None, trip


def test_metro_north_is_exempt_at_the_decoder(monkeypatch):
    """Metro-North's position row is not age-gated (design 3.3): its stamps copy a header
    that lags two to four minutes. The capture cannot show the exemption doing anything,
    since every stamp IS the header, so the world moves every stamp 700 s behind it, past
    OBS_MAX_S. The GPS pass still emits all 49 rows exactly as the golden holds them, and
    the counts are 33 reported. Admit Metro-North to the policy set and the same world
    emits none, so the exemption is the set's and not a branch on the name."""
    _raw, expected = _load("MNR")
    aged = _mnr_aged(700)
    stops = _stops("MNR")
    gps, _ts = feeds._decode_railroad_vehicles(aged, "MNR", expected["now"], stops)
    assert gps == expected["trains"]
    assert feeds.railroad._position_steps(aged, "MNR", stops, expected["now"]) == {
        "reported": 33,
        "estimated": 0,
        "qualified": 0,
        "placed": 0,
        "suppressed": 0,
    }
    monkeypatch.setattr(feeds.railroad, "RAILROAD_FRESHNESS_SYSTEMS", frozenset({"LIRR", "MNR"}))
    gated, _ts = feeds._decode_railroad_vehicles(aged, "MNR", expected["now"], stops)
    assert gated == []


def _mnr_aged(seconds: int) -> bytes:
    """The committed Metro-North capture with every positioned vehicle's stamp moved
    `seconds` behind its header: the world the capture cannot show, since all 49 of its
    stamps ARE the header, so a gate on them would pass everything and prove nothing."""
    raw, _expected = _load("MNR")
    feed = pb.FeedMessage.FromString(raw)
    header = int(feed.header.timestamp)
    for entity in feed.entity:
        if entity.HasField("vehicle") and entity.vehicle.HasField("position"):
            entity.vehicle.timestamp = header - seconds
    return feed.SerializeToString()


def test_a_gated_combined_layout_places_exactly_what_its_ladder_estimates(monkeypatch):
    """N2 under the gate, on the combined layout (memo D13, mutation 7). No committed world
    gates a combined entity, because Metro-North is exempt; the aged world above admitted
    to the policy set is the one that does. There the ladder estimates 23 trains (their
    undated predictions fall back to the header, 0 s old) and withholds 10, the GPS pass
    emits nothing, and the placement pass must draw exactly the 23 as `estimated`. Its
    per-entity skip asks the same _accepted_as_gps the GPS pass emits by; one that asked
    the base rule instead skips every one of them, and the block would serve 23 estimates
    drawn nowhere. So the rows are asked for by trip, and the count the block serves is
    asked to be the rows drawn."""
    _raw, expected = _load("MNR")
    aged = _mnr_aged(700)
    stops = _stops("MNR")
    now = expected["now"]
    monkeypatch.setattr(feeds.railroad, "RAILROAD_FRESHNESS_SYSTEMS", frozenset({"LIRR", "MNR"}))
    feed = pb.FeedMessage.FromString(aged)
    ladder = feeds.railroad._position_ladder(
        feed, "MNR", stops, now, feeds.railroad._canceled_trip_ids(feed)
    )
    estimated_trains = sorted(train for train, step in ladder.items() if step == 2)
    assert len(estimated_trains) == 23
    # A combined entity is judged under its vehicle's trip id (the train number) and placed
    # under its trip_update's, so each row is joined back to the ladder through its entity.
    train_of = {
        e.trip_update.trip.trip_id: e.vehicle.trip.trip_id
        for e in feed.entity
        if e.HasField("vehicle") and e.vehicle.HasField("position") and e.HasField("trip_update")
    }
    placed = feeds._decode_railroad_placements(aged, "MNR", stops, now)
    estimated = [t for t in placed if t["provenance"] == "estimated"]
    assert sorted(train_of[t["trip_id"]] for t in estimated) == estimated_trains
    steps = feeds.railroad._position_steps(aged, "MNR", stops, now)
    assert steps["estimated"] == len(estimated)
    assert steps == {
        "reported": 0,
        "estimated": 23,
        "qualified": 0,
        "placed": 0,
        "suppressed": 10,
    }
    gps, _ts = feeds._decode_railroad_vehicles(aged, "MNR", now, stops)
    assert gps == []


def _repeated_vehicle(raw: bytes, trip_id: str, ages: tuple[int, ...]) -> bytes:
    """The capture with `trip_id`'s vehicle entity replaced, where it stood, by one copy per
    age in the order given, each under an entity id of its own and `age` seconds behind the
    header. tests/test_position_ladder.py builds the same world for the ladder."""
    feed = pb.FeedMessage.FromString(raw)
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
    return world.SerializeToString()


# The capture's own fresh prediction about a finished trip: no stop is left to place it
# at, so a copy of its vehicle is judged by its own fix alone. One of the 24 as captured.
REPEATED = "GO201_26_8945"


@pytest.mark.parametrize(
    ("ages", "served_age"), [((1, 700), 1), ((700, 1), 1), ((300, 700), 300), ((700, 300), 300)]
)
def test_a_repeated_vehicle_is_emitted_only_as_the_copy_that_earns_its_step(ages, served_age):
    """A trip the feed reports through two vehicle entities, at two ages. The trip takes
    the best step either copy earns (step 1 with a 1 s copy, step 3 with a 300 s one), and
    the GPS pass emits only the copy whose own fix earns it. Accepting both would leave the
    live path's first-wins dedupe to choose, and with the 700 s copy first it would serve a
    fix past OBS_MAX_S under the fresh copy's step. Both orders, because feed order is
    exactly what must not decide it; everything else decodes as the golden does."""
    raw, expected = _load("LIRR")
    world = _repeated_vehicle(raw, REPEATED, ages)
    gps, _ts = feeds._decode_railroad_vehicles(world, "LIRR", expected["now"], _stops("LIRR"))
    rows = [t for t in gps if t["trip_id"] == REPEATED]
    assert [expected["now"] - t["observed_at"] for t in rows] == [served_age]
    assert [t for t in gps if t["trip_id"] != REPEATED] == expected["trains"]


# ---------------- placement golden ----------------


def _load_placed(system: str):
    key = system.lower()
    raw = (FIXTURES / f"railroad_{key}.pb").read_bytes()
    stops = json.loads((FIXTURES / f"railroad_{key}_stops.json").read_text())
    expected = json.loads((FIXTURES / f"railroad_{key}_placed_expected.json").read_text())
    return raw, stops, expected


@pytest.mark.parametrize("system", SYSTEMS)
def test_placed_feed_decodes_to_golden_output(system):
    raw, stops, expected = _load_placed(system)
    placed = feeds._decode_railroad_placements(raw, expected["system"], stops, expected["now"])
    assert placed == expected["trains"]


def test_placed_golden_is_nontrivial():
    # LIRR has many position-less running trains (its feed prunes passed stops, so
    # most omitted trains are placeable); MNR's omitted trains are mostly GPS-
    # covered or future-scheduled, so its placed count is small but nonzero.
    assert len(_load_placed("LIRR")[2]["trains"]) > 10
    assert len(_load_placed("MNR")[2]["trains"]) >= 1


@pytest.mark.parametrize("system", SYSTEMS)
def test_every_placed_train_is_well_formed(system):
    raw, stops, expected = _load_placed(system)
    coords = {(s["lat"], s["lon"]) for s in stops.values()}
    for t in expected["trains"]:
        assert t["system"] == system
        assert t["bearing"] is None  # placed from schedule, no GPS heading
        assert (t["latitude"], t["longitude"]) in coords  # placed AT a static stop
        assert feeds.RAILROAD_LAT_MIN <= t["latitude"] <= feeds.RAILROAD_LAT_MAX
        assert feeds.RAILROAD_LON_MIN <= t["longitude"] <= feeds.RAILROAD_LON_MAX
        # Anchors are filled wherever the feed carries times: these placements all
        # have a timed next stop (the no-times fallback would leave next_time null).
        assert t["next_time"] is not None
        # Placed AT a known station, with its name (the carry-forward keys on stop_id).
        assert t["stop_id"] in stops
        assert t["stop_name"] is not None


# ---------------- arrivals golden ----------------

# Regenerate the arrivals golden after an INTENTIONAL decode change, from backend/
# (network-free, using the committed stops.json):
#
#     python - <<'PY'
#     import json
#     from pathlib import Path
#     from google.transit import gtfs_realtime_pb2 as pb
#     import feeds
#     FIX = Path("tests/fixtures")
#     for system in ("LIRR", "MNR"):
#         key = system.lower()
#         raw = (FIX / f"railroad_{key}.pb").read_bytes()
#         stops = json.loads((FIX / f"railroad_{key}_stops.json").read_text())
#         feed = pb.FeedMessage(); feed.ParseFromString(raw)
#         now = float(feed.header.timestamp)
#         _placed, arrivals = feeds._decode_railroad_feed(raw, system, stops, now)
#         (FIX / f"railroad_{key}_arrivals_expected.json").write_text(
#             json.dumps({"now": now, "system": system, "arrivals": arrivals},
#                        sort_keys=True, indent=0))
#     PY


def _load_arrivals(system: str):
    key = system.lower()
    raw = (FIXTURES / f"railroad_{key}.pb").read_bytes()
    stops = json.loads((FIXTURES / f"railroad_{key}_stops.json").read_text())
    expected = json.loads((FIXTURES / f"railroad_{key}_arrivals_expected.json").read_text())
    return raw, stops, expected


@pytest.mark.parametrize("system", SYSTEMS)
def test_arrivals_feed_decodes_to_golden_output(system):
    raw, stops, expected = _load_arrivals(system)
    _placed, arrivals = feeds._decode_railroad_feed(raw, expected["system"], stops, expected["now"])
    assert arrivals == expected["arrivals"]


def test_arrivals_golden_is_nontrivial():
    # Guard the guard: an empty index would make the equality test vacuous. Both
    # systems index arrivals at well over 50 stations in the captured feeds.
    assert len(_load_arrivals("LIRR")[2]["arrivals"]) > 50
    assert len(_load_arrivals("MNR")[2]["arrivals"]) > 50


@pytest.mark.parametrize("system", SYSTEMS)
def test_every_golden_arrival_is_well_formed(system):
    _, stops, expected = _load_arrivals(system)
    now = expected["now"]
    # Both systems can use all three buckets now: LIRR reads direction_id, MNR
    # infers Inbound/Outbound from the stop progression (Phase 11c), and "Trains"
    # remains the residual for trips whose direction is neither read nor inferred.
    valid_buckets = {"Outbound", "Inbound", "Trains"}
    for stop_id, buckets in expected["arrivals"].items():
        assert stop_id in stops  # resolvable station
        for bucket, arrs in buckets.items():
            assert bucket in valid_buckets
            assert arrs  # the decode never stores an empty bucket
            assert len(arrs) <= feeds.ARRIVALS_PER_DIRECTION  # capped
            times = [a["arrival"] for a in arrs]
            assert times == sorted(times)  # soonest first
            for a in arrs:
                assert set(a) == {
                    "route_id",
                    "trip_id",
                    "arrival",
                    "train_num",
                    # Contract 6.1: a prediction is an observation, so every golden
                    # arrival row carries when its trip update was published.
                    "observed_at",
                    "provenance",
                }
                assert a["arrival"] >= now - 60  # just-passed grace floor


# ---------------- synthetic: extraction rules ----------------


def _vehicle_entity(eid, trip_id="", route_id="", lat=40.8, lon=-73.5, label="", with_pos=True):
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1782006915
    ent = feed.entity.add()
    ent.id = eid
    v = ent.vehicle
    v.trip.trip_id = trip_id
    v.trip.route_id = route_id
    v.vehicle.label = label
    if with_pos:
        v.position.latitude = lat
        v.position.longitude = lon
    return feed, ent


def test_entity_without_vehicle_position_is_omitted():
    # A trip_update-only entity (no vehicle.position) must not appear in phase 1.
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    ent = feed.entity.add()
    ent.id = "tu-only"
    ent.trip_update.trip.trip_id = "T1"
    ent.trip_update.trip.route_id = "3"
    trains, _ = feeds._decode_railroad_vehicles(feed.SerializeToString(), "MNR", 0.0)
    assert trains == []


def test_position_outside_railroad_box_is_dropped():
    feed, _ = _vehicle_entity("v1", route_id="5", lat=0.0, lon=0.0)  # (0,0) is out of range
    trains, _ = feeds._decode_railroad_vehicles(feed.SerializeToString(), "LIRR", 0.0)
    assert trains == []


def test_route_id_join_by_trip_id_separate_entity():
    # LIRR layout: a vehicle entity with empty route_id, joined by trip_id to a
    # SEPARATE trip_update entity that carries the route.
    feed, _ = _vehicle_entity("v1", trip_id="TR_42", route_id="")
    tu = feed.entity.add()
    tu.id = "tu1"
    tu.trip_update.trip.trip_id = "TR_42"
    tu.trip_update.trip.route_id = "8"
    trains, _ = feeds._decode_railroad_vehicles(feed.SerializeToString(), "LIRR", 0.0)
    assert len(trains) == 1
    assert trains[0]["route_id"] == "8"
    assert trains[0]["trip_id"] == "TR_42"


def test_route_id_from_same_entity_trip_update():
    # MNR layout: one combined entity whose vehicle.trip has the train number and
    # empty route_id, while the route lives on the same entity's trip_update.
    feed, ent = _vehicle_entity("1797", trip_id="1797", route_id="", label="1797")
    ent.trip_update.trip.trip_id = "3114306"
    ent.trip_update.trip.route_id = "4"
    trains, _ = feeds._decode_railroad_vehicles(feed.SerializeToString(), "MNR", 0.0)
    assert len(trains) == 1
    assert trains[0]["route_id"] == "4"
    assert trains[0]["train_num"] == "1797"


def test_vehicle_own_route_id_preferred_and_train_num_falls_back_to_id():
    feed, ent = _vehicle_entity("v1", trip_id="T9", route_id="5", label="")
    ent.vehicle.vehicle.id = "veh-9"
    trains, _ = feeds._decode_railroad_vehicles(feed.SerializeToString(), "LIRR", 0.0)
    assert trains[0]["route_id"] == "5"  # vehicle's own route_id wins
    assert trains[0]["train_num"] == "veh-9"  # label empty -> vehicle.id


def test_decode_returns_header_timestamp():
    feed, _ = _vehicle_entity("v1", route_id="5")  # _vehicle_entity sets header.timestamp
    _, feed_ts = feeds._decode_railroad_vehicles(feed.SerializeToString(), "LIRR", 0.0)
    assert feed_ts == 1782006915.0


def test_decode_timestamp_none_when_feed_omits_it():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"  # timestamp left at its 0 default
    _, feed_ts = feeds._decode_railroad_vehicles(feed.SerializeToString(), "MNR", 0.0)
    assert feed_ts is None


# ---------------- fetch_railroad_trains: live path (fake client) ----------------


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeRailClient:
    """Dispatches by URL: returns bytes for a system, raises for a 'down' one."""

    def __init__(self, by_system, down=()):
        self._by_system = by_system
        self._down = set(down)

    async def get(self, url):
        for system in feeds.RAILROAD_FEED_URLS:
            if system.lower() in url.lower():
                if system in self._down:
                    raise httpx.HTTPError(f"{system} down")
                return _FakeResp(self._by_system[system])
        raise AssertionError(f"unexpected url {url}")


def _raw(system):
    return (FIXTURES / f"railroad_{system.lower()}.pb").read_bytes()


@pytest.mark.anyio
async def test_fetch_timestamp_uses_lirr_header_only():
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")})
    _, _, feed_ts, _, _, _ = await feeds.fetch_railroad_trains(client, {})
    lirr_ts = _load("LIRR")[1]["now"]
    mnr_ts = _load("MNR")[1]["now"]
    # Only LIRR (freshness-authoritative) drives feed_timestamp; MNR's header is
    # ignored even though it is the older of the two.
    assert feed_ts == lirr_ts == 1782006915.0
    assert feed_ts != mnr_ts  # 1782006692.0, MNR's older lagging header, is not used


@pytest.mark.anyio
async def test_fetch_timestamp_none_when_only_untrusted_feed_succeeds():
    # LIRR (the only trusted system) fails; MNR succeeds but contributes no
    # timestamp, so feed_timestamp falls back to None / the poll-age signal.
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")}, down=["LIRR"])
    trains, _, feed_ts, failed, _, _ = await feeds.fetch_railroad_trains(client, {})
    assert failed == ["LIRR"]
    assert trains and all(t["system"] == "MNR" for t in trains)
    assert feed_ts is None


@pytest.mark.anyio
async def test_fetch_dedups_duplicate_trip_ids_on_the_live_path():
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")})
    trains, _, _, failed, _, steps = await feeds.fetch_railroad_trains(client, {})
    assert failed == []
    # The MNR feed repeats trains across separate vehicle entities; the live path
    # collapses them to one marker per trip_id (49 decoded -> 33 unique), which
    # the golden decode (no de-dup) does not.
    mnr = [t for t in trains if t["system"] == "MNR"]
    assert len(mnr) == 33
    assert len({t["trip_id"] for t in mnr}) == 33
    # 44, not the 69 positioned vehicles on the wire. F02 drops the one whose TripUpdate
    # marks it canceled, before it is ever emitted (this was 68 until contract 6.3), and
    # F01's age gate withholds the 24 whose own fix is past OBS_MAX_S against the header.
    # No stops are loaded here, so nothing is placeable: the six the ladder would
    # estimate stay GPS, qualified, rather than withheld for an estimate nobody draws.
    assert len([t for t in trains if t["system"] == "LIRR"]) == 44
    assert len(trains) == 44 + 33
    # The counts each system's block serves, one per trip after this same dedupe:
    # Metro-North's 49 entities are its 33 markers, all step 1 on a row not age-gated.
    assert steps == {
        "LIRR": {"reported": 27, "estimated": 0, "qualified": 17, "placed": 0, "suppressed": 24},
        "MNR": {"reported": 33, "estimated": 0, "qualified": 0, "placed": 0, "suppressed": 0},
    }


@pytest.mark.anyio
async def test_fetch_serves_the_ladder_at_the_header_with_both_systems_stops(monkeypatch):
    """The live path at the LIRR capture's own header with the committed stops, the world
    design 3.4 measured: the counts, and the markers they describe. One count per trip
    after the (system, trip_id) dedupe, so every count but `suppressed` is a marker a
    vehicle entity produced, and the five sum to the 68 vehicles the base rule accepts."""
    header = _load("LIRR")[1]["now"]
    monkeypatch.setattr(feeds.railroad, "time", SimpleNamespace(time=lambda: header))
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")})
    stops = {system: _stops(system) for system in SYSTEMS}
    trains, _, _, failed, _, steps = await feeds.fetch_railroad_trains(client, stops)
    assert failed == []
    assert steps["LIRR"] == {
        "reported": 27,
        "estimated": 6,
        "qualified": 11,
        "placed": 0,
        "suppressed": 24,
    }
    assert steps["MNR"] == {
        "reported": 33,
        "estimated": 0,
        "qualified": 0,
        "placed": 0,
        "suppressed": 0,
    }
    lirr = [t for t in trains if t["system"] == "LIRR"]
    gps = [t for t in lirr if t["provenance"] == "reported"]
    estimated = {t["trip_id"] for t in lirr if t["provenance"] == "estimated"}
    placed = {t["trip_id"] for t in lirr if t["provenance"] == "placed"}
    assert len(gps) == 27 + 11 and estimated == ESTIMATED
    # The 56 placements of trips with no vehicle, untouched by the gate (memo D1), and not
    # one of a vehicle's trip: step 4 never fires on the capture.
    assert len(placed) == 56 and not placed & _positioned_vehicle_trip_ids(_raw("LIRR"))
    assert sum(steps["LIRR"].values()) - steps["LIRR"]["suppressed"] == len(gps) + len(estimated)
    assert sum(steps["LIRR"].values()) == 68
    mnr_gps = [t for t in trains if t["system"] == "MNR" and t["provenance"] == "reported"]
    assert len(mnr_gps) == sum(steps["MNR"].values()) == 33


@pytest.mark.anyio
@pytest.mark.parametrize("ages", [(1, 700), (700, 1)])
async def test_the_live_path_serves_the_fresh_copy_of_a_repeated_vehicle(ages):
    """The repeated-vehicle world through fetch_railroad_trains, whose first-wins (system,
    trip_id) dedupe is the reason the gate judges each copy by its own fix: the served row
    is the 1 s fix in either order, and the trip is counted once, as reported (it was
    one of the capture's 24, so 27/0/17/0/24 becomes 28/0/17/0/23 here, with no stops)."""
    world = _repeated_vehicle(_raw("LIRR"), REPEATED, ages)
    client = _FakeRailClient({"LIRR": world, "MNR": _raw("MNR")})
    trains, _, _, failed, _, steps = await feeds.fetch_railroad_trains(client, {})
    assert failed == []
    (row,) = [t for t in trains if t["trip_id"] == REPEATED]
    assert _load("LIRR")[1]["now"] - row["observed_at"] == 1
    assert steps["LIRR"] == {
        "reported": 28,
        "estimated": 0,
        "qualified": 17,
        "placed": 0,
        "suppressed": 23,
    }


@pytest.mark.anyio
async def test_fetch_skips_a_failed_feed_and_reports_it():
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")}, down=["MNR"])
    trains, _, _, failed, _, _ = await feeds.fetch_railroad_trains(client, {})
    assert failed == ["MNR"]
    assert trains and all(t["system"] == "LIRR" for t in trains)


@pytest.mark.anyio
async def test_fetch_skips_an_undecodable_feed():
    # MNR returns a truncated length-delimited field -> DecodeError, skipped.
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": b"\x0a\xff"})
    trains, _, _, failed, _, _ = await feeds.fetch_railroad_trains(client, {})
    assert failed == ["MNR"]
    assert trains and all(t["system"] == "LIRR" for t in trains)


@pytest.mark.anyio
async def test_fetch_raises_when_all_feeds_fail():
    client = _FakeRailClient({}, down=["LIRR", "MNR"])
    with pytest.raises(RuntimeError, match="All railroad feeds failed"):
        await feeds.fetch_railroad_trains(client, {})


# ---------------- synthetic: placement edges ----------------

NOW = 1000.0
SYN_STOPS = {
    "A": {"name": "Aville", "lat": 40.80, "lon": -73.50},
    "B": {"name": "Bville", "lat": 40.81, "lon": -73.51},
    "C": {"name": "Cville", "lat": 40.82, "lon": -73.52},
}

_SKIPPED = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.SKIPPED
_NO_DATA = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.NO_DATA
_CANCELED = pb.TripDescriptor.ScheduleRelationship.CANCELED


def _tu_entity(
    feed,
    trip_id,
    route_id="5",
    direction_id=None,
    start_time="",
    start_date="",
    stops=(),
    canceled=False,
):
    """Add a trip_update entity. stops = [(stop_id, time | None [, schedule_rel]), ...]."""
    ent = feed.entity.add()
    ent.id = trip_id
    tu = ent.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.route_id = route_id
    if direction_id is not None:
        tu.trip.direction_id = direction_id
    if start_time:
        tu.trip.start_time = start_time
    if start_date:
        tu.trip.start_date = start_date
    if canceled:
        tu.trip.schedule_relationship = _CANCELED
    for spec in stops:
        sid, t = spec[0], spec[1]
        stu = tu.stop_time_update.add()
        stu.stop_id = sid
        if t is not None:
            stu.arrival.time = int(t)
        if len(spec) > 2 and spec[2] is not None:
            stu.schedule_relationship = spec[2]
    return ent


def _placed(feed, system="LIRR", stops=SYN_STOPS, now=NOW):
    feed.header.gtfs_realtime_version = "2.0"  # required field for serialization
    return feeds._decode_railroad_placements(feed.SerializeToString(), system, stops, now)


def test_position_less_trip_placed_at_next_stop_with_prev_anchor():
    feed = pb.FeedMessage()
    # A is just-passed, B is next-upcoming, C is later. Placed at B; prev anchor A.
    _tu_entity(
        feed, "T1", direction_id=1, stops=[("A", NOW - 300), ("B", NOW + 120), ("C", NOW + 600)]
    )
    placed = _placed(feed)
    assert len(placed) == 1
    t = placed[0]
    assert (t["latitude"], t["longitude"]) == (40.81, -73.51)  # B
    assert t["next_time"] == NOW + 120
    assert (t["prev_lat"], t["prev_lon"], t["prev_time"]) == (40.80, -73.50, NOW - 300)  # A
    assert t["direction"] == "Inbound"
    assert t["bearing"] is None and t["route_id"] == "5"


def test_gps_trip_not_double_placed_combined_entity():
    # MNR layout: one entity carries BOTH a position and a trip_update (with a
    # different vehicle trip_id); the GPS slice owns it, so it is not placed.
    feed = pb.FeedMessage()
    ent = _tu_entity(feed, "3114306", stops=[("A", NOW + 120)])
    ent.vehicle.trip.trip_id = "1797"
    ent.vehicle.position.latitude = 40.8
    ent.vehicle.position.longitude = -73.5
    assert _placed(feed, system="MNR") == []


def test_gps_trip_not_double_placed_split_entity():
    # LIRR layout: a separate vehicle entity holds the position under the same
    # trip_id as the trip_update; the trip_update must not also be placed.
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", stops=[("A", NOW + 120)])
    veh = feed.entity.add()
    veh.id = "v1"
    veh.vehicle.trip.trip_id = "T1"
    veh.vehicle.position.latitude = 40.8
    veh.vehicle.position.longitude = -73.5
    assert _placed(feed, system="LIRR") == []


def test_canceled_trip_dropped_from_placement():
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", stops=[("A", NOW + 120)], canceled=True)
    assert _placed(feed) == []


def test_skipped_and_no_data_stops_skipped_in_placement():
    feed = pb.FeedMessage()
    _tu_entity(
        feed, "T1", stops=[("A", NOW + 60, _SKIPPED), ("B", NOW + 90, _NO_DATA), ("C", NOW + 120)]
    )
    placed = _placed(feed)
    assert len(placed) == 1
    assert (placed[0]["latitude"], placed[0]["longitude"]) == (40.82, -73.52)  # C
    assert placed[0]["next_time"] == NOW + 120


def test_no_times_fallback_to_first_resolvable_stop():
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", stops=[("A", None), ("B", None)])  # no stop carries a time
    placed = _placed(feed)
    assert len(placed) == 1
    assert (placed[0]["latitude"], placed[0]["longitude"]) == (40.80, -73.50)  # A
    assert placed[0]["next_time"] is None and placed[0]["prev_lat"] is None


def test_finished_trip_all_stops_past_dropped():
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", stops=[("A", NOW - 600), ("B", NOW - 300)])  # all past
    assert _placed(feed) == []


def test_far_future_first_stop_is_kept_no_subway_cap():
    # Railroad feeds prune passed stops, so a running train's first listed stop is
    # simply its next station, often far out. Unlike the subway path, that is NOT
    # dropped: the far-future-first-stop cap is intentionally not applied here. A
    # no-start_time (LIRR-style) trip whose only stop is 1h ahead must be placed.
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", stops=[("A", NOW + 3600)])
    placed = _placed(feed)
    assert len(placed) == 1
    assert (placed[0]["latitude"], placed[0]["longitude"]) == (40.80, -73.50)
    assert placed[0]["next_time"] == NOW + 3600


def test_direction_from_direction_id_and_null_when_absent():
    feed = pb.FeedMessage()
    _tu_entity(feed, "OUT", direction_id=0, stops=[("A", NOW + 120)])
    _tu_entity(feed, "IN", direction_id=1, stops=[("A", NOW + 120)])
    _tu_entity(feed, "NONE", stops=[("A", NOW + 120)])  # no direction_id (e.g. MNR)
    dirs = {t["trip_id"]: t["direction"] for t in _placed(feed)}
    assert dirs == {"OUT": "Outbound", "IN": "Inbound", "NONE": None}


def test_started_vs_not_yet_started_via_start_time():
    base = datetime(2026, 6, 20, 23, 0, 0, tzinfo=feeds.NYC_TZ)
    now = base.timestamp()
    feed = pb.FeedMessage()
    _tu_entity(
        feed, "RUNNING", start_time="22:25:00", start_date="20260620", stops=[("A", now + 120)]
    )
    _tu_entity(
        feed, "FUTURE", start_time="23:30:00", start_date="20260620", stops=[("A", now + 120)]
    )
    ids = {t["trip_id"] for t in _placed(feed, now=now)}
    assert ids == {"RUNNING"}  # the 23:30 start is > now + grace, so not-yet-started


# ---------------- synthetic: arrivals extraction ----------------


def _decode(feed, system="LIRR", stops=SYN_STOPS, now=NOW):
    """(placed, arrivals) from the combined decoder; sets the required header."""
    feed.header.gtfs_realtime_version = "2.0"
    return feeds._decode_railroad_feed(feed.SerializeToString(), system, stops, now)


def test_gps_train_in_arrivals_but_excluded_from_placement():
    # A positioned (GPS) trip: the placement half skips it, but its trip_update
    # stops must still be indexed as arrivals (a GPS train still stops at stations).
    feed = pb.FeedMessage()
    ent = _tu_entity(feed, "T1", stops=[("A", NOW + 120), ("B", NOW + 240)])
    ent.vehicle.trip.trip_id = "T1"
    ent.vehicle.position.latitude = 40.8
    ent.vehicle.position.longitude = -73.5
    placed, arrivals = _decode(feed, system="MNR")
    assert placed == []  # positioned -> not placed
    assert arrivals["A"]["Trains"] and arrivals["B"]["Trains"]  # but still in arrivals


def test_lirr_arrivals_bucketed_by_direction_id_with_trains_fallback():
    feed = pb.FeedMessage()
    _tu_entity(feed, "OUT", direction_id=0, stops=[("A", NOW + 120)])
    _tu_entity(feed, "IN", direction_id=1, stops=[("A", NOW + 180)])
    _tu_entity(feed, "NODIR", stops=[("A", NOW + 240)])  # LIRR trip missing direction_id
    _, arrivals = _decode(feed, system="LIRR")
    buckets = {k: [a["trip_id"] for a in v] for k, v in arrivals["A"].items()}
    assert buckets == {"Outbound": ["OUT"], "Inbound": ["IN"], "Trains": ["NODIR"]}


def test_mnr_arrivals_all_in_single_trains_bucket():
    # MNR omits direction_id, so every arrival lands in "Trains", time-sorted.
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("A", NOW + 180)])
    _tu_entity(feed, "M2", stops=[("A", NOW + 60)])
    _, arrivals = _decode(feed, system="MNR")
    assert set(arrivals["A"]) == {"Trains"}
    assert [a["trip_id"] for a in arrivals["A"]["Trains"]] == ["M2", "M1"]  # sorted by time


def test_canceled_trip_dropped_from_arrivals():
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", direction_id=0, stops=[("A", NOW + 120)], canceled=True)
    _, arrivals = _decode(feed, system="LIRR")
    assert arrivals == {}  # canceled trip contributes to neither placement nor arrivals


def test_skipped_and_no_data_stops_dropped_from_arrivals():
    feed = pb.FeedMessage()
    _tu_entity(
        feed,
        "T1",
        direction_id=0,
        stops=[("A", NOW + 60, _SKIPPED), ("B", NOW + 90, _NO_DATA), ("C", NOW + 120)],
    )
    _, arrivals = _decode(feed, system="LIRR")
    assert set(arrivals) == {"C"}  # only the real-prediction stop is indexed
    assert arrivals["C"]["Outbound"][0]["arrival"] == NOW + 120


def test_arrivals_just_passed_grace_boundary():
    # Same now - 60 grace as placement: a stop at now-60 is kept, now-61 dropped.
    feed = pb.FeedMessage()
    _tu_entity(
        feed, "T1", direction_id=1, stops=[("A", NOW - 61), ("B", NOW - 60), ("C", NOW + 30)]
    )
    _, arrivals = _decode(feed, system="LIRR")
    assert set(arrivals) == {"B", "C"}  # A is past the grace floor


def test_arrivals_sorted_and_capped_per_bucket():
    feed = pb.FeedMessage()
    # Eight inbound trains at A, out of order; the bucket keeps the six soonest.
    for i, dt in enumerate([300, 60, 500, 120, 240, 30, 420, 180]):
        _tu_entity(feed, f"T{i}", direction_id=1, stops=[("A", NOW + dt)])
    _, arrivals = _decode(feed, system="LIRR")
    inbound = arrivals["A"]["Inbound"]
    assert len(inbound) == feeds.ARRIVALS_PER_DIRECTION  # capped at 6
    times = [a["arrival"] for a in inbound]
    assert times == sorted(times)  # soonest first
    assert times[0] == NOW + 30 and times[-1] == NOW + 300  # kept the six soonest


def test_lirr_arrivals_train_num_joins_from_positioned_vehicle():
    # LIRR's trip_update-only entity has no vehicle; its train number is joined
    # from the separate positioned vehicle entity sharing the trip_id.
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", direction_id=1, stops=[("A", NOW + 120)])
    veh = feed.entity.add()
    veh.id = "v1"
    veh.vehicle.trip.trip_id = "T1"
    veh.vehicle.vehicle.label = "704"
    veh.vehicle.position.latitude = 40.8
    veh.vehicle.position.longitude = -73.5
    placed, arrivals = _decode(feed, system="LIRR")
    assert placed == []  # the GPS train is not placed
    assert arrivals["A"]["Inbound"][0]["train_num"] == "704"  # but its arrival carries the number


def test_mnr_arrivals_train_num_from_combined_entity():
    # MNR's combined entity carries the label inline (read the same way placement
    # reads it), so its arrivals carry the train number without a join.
    feed = pb.FeedMessage()
    ent = _tu_entity(feed, "3114306", stops=[("A", NOW + 120)])
    ent.vehicle.trip.trip_id = "1797"
    ent.vehicle.vehicle.label = "1797"
    ent.vehicle.position.latitude = 40.8
    ent.vehicle.position.longitude = -73.5
    placed, arrivals = _decode(feed, system="MNR")
    assert placed == []  # positioned -> not placed
    assert arrivals["A"]["Trains"][0]["train_num"] == "1797"


def test_arrivals_train_num_none_when_no_vehicle_entity():
    # A placed (position-less) LIRR trip with no vehicle entity anywhere: arrivals
    # carry a null train number.
    feed = pb.FeedMessage()
    _tu_entity(feed, "T1", direction_id=0, stops=[("A", NOW + 120)])
    _, arrivals = _decode(feed, system="LIRR")
    assert arrivals["A"]["Outbound"][0]["train_num"] is None


# ---------------- synthetic: MNR direction inference (arrivals bucketing) ----------------

# Stops on a radial toward the NYC anchor (Grand Central). FAR is far out, NEAR is
# close to the city, MID sits a hair inside FAR (under the epsilon from it). Deltas
# verified against feeds._DIRECTION_EPSILON: FAR->NEAR ~0.61 (Inbound), FAR->MID
# ~0.009 (< epsilon, Trains).
INF_STOPS = {
    "FAR": {"name": "Far", "lat": 41.30, "lon": -73.60},
    "MID": {"name": "Mid", "lat": 41.29, "lon": -73.60},
    "NEAR": {"name": "Near", "lat": 40.76, "lon": -73.97},
}


def test_direction_from_progression_is_pure_and_terminal_agnostic():
    # Toward the anchor -> Inbound, away -> Outbound, under-epsilon net move -> None.
    assert feeds._direction_from_progression(41.30, -73.60, 40.76, -73.97) == "Inbound"
    assert feeds._direction_from_progression(40.76, -73.97, 41.30, -73.60) == "Outbound"
    assert feeds._direction_from_progression(41.30, -73.60, 41.29, -73.60) is None


def test_mnr_arrivals_inbound_inferred_from_progression():
    feed = pb.FeedMessage()
    # No direction_id (MNR); stops progress FAR -> NEAR (toward the city) -> Inbound.
    _tu_entity(feed, "M1", stops=[("FAR", NOW + 60), ("NEAR", NOW + 300)])
    _, arrivals = _decode(feed, system="MNR", stops=INF_STOPS)
    assert set(arrivals["FAR"]) == {"Inbound"}
    assert set(arrivals["NEAR"]) == {"Inbound"}


def test_mnr_arrivals_outbound_inferred_from_progression():
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("NEAR", NOW + 60), ("FAR", NOW + 300)])  # away from the city
    _, arrivals = _decode(feed, system="MNR", stops=INF_STOPS)
    assert set(arrivals["NEAR"]) == {"Outbound"}
    assert set(arrivals["FAR"]) == {"Outbound"}


def test_arrivals_single_resolvable_stop_falls_back_to_trains():
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("NEAR", NOW + 60)])  # only one resolvable stop
    _, arrivals = _decode(feed, system="MNR", stops=INF_STOPS)
    assert set(arrivals["NEAR"]) == {"Trains"}


def test_arrivals_under_epsilon_delta_falls_back_to_trains():
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("FAR", NOW + 60), ("MID", NOW + 120)])  # net move < epsilon
    _, arrivals = _decode(feed, system="MNR", stops=INF_STOPS)
    assert set(arrivals["FAR"]) == {"Trains"}


def test_direction_id_wins_over_inference():
    feed = pb.FeedMessage()
    # LIRR with direction_id=1 (Inbound) but a NEAR->FAR progression that WOULD
    # infer Outbound: the feed's direction_id must win; inference is a fallback.
    _tu_entity(feed, "L1", direction_id=1, stops=[("NEAR", NOW + 60), ("FAR", NOW + 300)])
    _, arrivals = _decode(feed, system="LIRR", stops=INF_STOPS)
    assert set(arrivals["NEAR"]) == {"Inbound"}


def test_inference_applies_to_direction_less_lirr_trip():
    feed = pb.FeedMessage()
    _tu_entity(feed, "L1", stops=[("FAR", NOW + 60), ("NEAR", NOW + 300)])  # LIRR, no direction_id
    _, arrivals = _decode(feed, system="LIRR", stops=INF_STOPS)
    assert set(arrivals["FAR"]) == {"Inbound"}


# ---------------- Phase 11d: placed direction from the inference ----------------


def test_placed_mnr_train_carries_inferred_direction():
    # A placeable MNR trip (no direction_id) whose stops progress FAR -> NEAR is
    # now PLACED with the inferred direction (11d), not a null.
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("FAR", NOW + 60), ("NEAR", NOW + 300)])
    placed, _ = _decode(feed, system="MNR", stops=INF_STOPS)
    assert len(placed) == 1 and placed[0]["direction"] == "Inbound"


def test_placed_and_arrivals_direction_agree_for_the_same_trip():
    # The single per-trip direction feeds BOTH: the placed train's direction and
    # the arrivals bucket its stops land in must be the same inferred value.
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("NEAR", NOW + 60), ("FAR", NOW + 300)])  # away -> Outbound
    placed, arrivals = _decode(feed, system="MNR", stops=INF_STOPS)
    assert len(placed) == 1 and placed[0]["direction"] == "Outbound"
    assert set(arrivals["NEAR"]) == {"Outbound"} and set(arrivals["FAR"]) == {"Outbound"}


def test_placed_direction_null_when_inference_is_ambiguous():
    # Under-epsilon net move (FAR -> MID): the arrivals bucket is the "Trains"
    # residual, but the placed train's direction stays null (the residual differs
    # by half). A single-resolvable-stop trip behaves the same.
    feed = pb.FeedMessage()
    _tu_entity(feed, "M1", stops=[("FAR", NOW + 60), ("MID", NOW + 120)])
    placed, arrivals = _decode(feed, system="MNR", stops=INF_STOPS)
    assert set(arrivals["FAR"]) == {"Trains"}
    assert len(placed) == 1 and placed[0]["direction"] is None

    single = pb.FeedMessage()
    _tu_entity(single, "M2", stops=[("NEAR", NOW + 60)])
    placed2, arrivals2 = _decode(single, system="MNR", stops=INF_STOPS)
    assert set(arrivals2["NEAR"]) == {"Trains"}
    assert len(placed2) == 1 and placed2[0]["direction"] is None


def test_placed_lirr_direction_id_wins_over_inference():
    # direction_id=1 (Inbound) but a NEAR -> FAR progression that WOULD infer
    # Outbound: the placed train keeps the reported direction, not the inference.
    feed = pb.FeedMessage()
    _tu_entity(feed, "L1", direction_id=1, stops=[("NEAR", NOW + 60), ("FAR", NOW + 300)])
    placed, arrivals = _decode(feed, system="LIRR", stops=INF_STOPS)
    assert len(placed) == 1 and placed[0]["direction"] == "Inbound"
    assert set(arrivals["NEAR"]) == {"Inbound"}  # bucket agrees, both from direction_id


# ---------------- fetch_railroad_trains: merge + composite-key dedup ----------------


def _gps_feed(trip_id, route_id="5"):
    f = pb.FeedMessage()
    f.header.gtfs_realtime_version = "2.0"
    f.header.timestamp = 1782006915
    e = f.entity.add()
    e.id = trip_id
    e.vehicle.trip.trip_id = trip_id
    e.vehicle.trip.route_id = route_id
    e.vehicle.position.latitude = 40.8
    e.vehicle.position.longitude = -73.5
    return f


@pytest.mark.anyio
async def test_fetch_merges_gps_and_placed_trains():
    # One feed with a GPS train and a separate position-less trip_update; with
    # static stops supplied, both appear (GPS slice + placement).
    # fetch_railroad_trains uses time.time() internally, so the placed stop must
    # be in the real future to be the next-upcoming stop.
    f = _gps_feed("GPS1")
    _tu_entity(f, "PLACED1", route_id="6", stops=[("A", time.time() + 600)])
    client = _FakeRailClient({"LIRR": f.SerializeToString(), "MNR": _raw("MNR")}, down=["MNR"])
    stops = {"LIRR": {"A": {"name": "A", "lat": 40.81, "lon": -73.51}}, "MNR": None}
    trains, _, _, failed, _, _ = await feeds.fetch_railroad_trains(client, stops)
    assert failed == ["MNR"]
    by_id = {t["trip_id"]: t for t in trains}
    # GPS coords come through the protobuf float32 position, so compare approx.
    assert by_id["GPS1"]["latitude"] == pytest.approx(40.8)
    assert by_id["GPS1"]["bearing"] is None and by_id["GPS1"]["route_id"] == "5"
    # Placed coords are the static stop's (a Python float), so they are exact.
    assert (by_id["PLACED1"]["latitude"], by_id["PLACED1"]["longitude"]) == (40.81, -73.51)
    assert by_id["PLACED1"]["next_time"] is not None and by_id["PLACED1"]["route_id"] == "6"


@pytest.mark.anyio
async def test_fetch_dedups_by_system_trip_id_composite_key():
    # The same trip_id in both feeds: (system, trip_id) keeps both, where a
    # trip_id-alone dedup would have dropped one.
    client = _FakeRailClient(
        {
            "LIRR": _gps_feed("SHARED").SerializeToString(),
            "MNR": _gps_feed("SHARED").SerializeToString(),
        }
    )
    trains, _, _, failed, _, _ = await feeds.fetch_railroad_trains(client, {})
    assert failed == []
    assert {(t["system"], t["trip_id"]) for t in trains} == {("LIRR", "SHARED"), ("MNR", "SHARED")}


# ---------------- railroad carry-forward (keyed by (system, trip_id)) ----------------

# Three stations along one segment chain.
RS1 = (40.70, -74.00)
RS2 = (40.71, -74.01)
RS3 = (40.72, -74.02)


def _rt(system, trip_id, stop_id, lat, lon, next_time, prev_lat=None):
    """A railroad placed-train dict (the fields carry_forward_prev reads/writes)."""
    return {
        "system": system,
        "trip_id": trip_id,
        "route_id": "5",
        "latitude": lat,
        "longitude": lon,
        "bearing": None,
        "train_num": None,
        "stop_id": stop_id,
        "stop_name": stop_id,
        "direction": None,
        "prev_lat": prev_lat,
        "prev_lon": None,
        "prev_time": None,
        "next_time": next_time,
    }


def _robs(stop_id, lat, lon, next_time, anchor=None):
    return {"stop_id": stop_id, "lat": lat, "lon": lon, "next_time": next_time, "anchor": anchor}


def _ranchor(stop_id, lat, lon, time):
    return {"stop_id": stop_id, "lat": lat, "lon": lon, "time": time}


def _rcf(trains, mem):
    # The railroad path keys memory by (system, trip_id).
    return feeds.carry_forward_prev(trains, mem, key=lambda t: (t["system"], t["trip_id"]))


def test_railroad_carry_forward_first_sighting_records_anchor_none():
    t = _rt("LIRR", "t1", "B", *RS2, next_time=1000.0)
    mem = _rcf([t], {})
    assert t["prev_lat"] is None  # nothing behind it yet
    assert mem[("LIRR", "t1")] == _robs("B", *RS2, 1000.0, anchor=None)


def test_railroad_carry_forward_transition_synthesizes_prev():
    # Last poll approaching A (next_time 940); now at B -> prev is A, the departed station.
    t = _rt("LIRR", "t1", "B", *RS2, next_time=1000.0)
    mem = _rcf([t], {("LIRR", "t1"): _robs("A", *RS1, 940.0)})
    assert (t["prev_lat"], t["prev_lon"], t["prev_time"]) == (*RS1, 940.0)
    assert mem[("LIRR", "t1")]["anchor"] == _ranchor("A", *RS1, 940.0)


def test_railroad_carry_forward_stable_segment_holds_anchor():
    carried = _ranchor("A", *RS1, 940.0)
    t = _rt("LIRR", "t1", "B", *RS2, next_time=1010.0)
    mem = _rcf([t], {("LIRR", "t1"): _robs("B", *RS2, 1000.0, anchor=carried)})
    assert (t["prev_lat"], t["prev_lon"], t["prev_time"]) == (*RS1, 940.0)  # still synthesized
    assert mem[("LIRR", "t1")]["anchor"] == carried  # held fixed across the segment


def test_railroad_carry_forward_guards_refuse_synthesis():
    # next_time None (no forward bracket)
    t = _rt("MNR", "t1", "B", *RS2, next_time=None)
    _rcf([t], {("MNR", "t1"): _robs("A", *RS1, 940.0)})
    assert t["prev_lat"] is None
    # anchor time None
    t = _rt("MNR", "t1", "B", *RS2, next_time=1000.0)
    _rcf([t], {("MNR", "t1"): _robs("B", *RS2, 1000.0, anchor=_ranchor("A", *RS1, None))})
    assert t["prev_lat"] is None
    # non-monotonic (anchor time >= next_time)
    t = _rt("MNR", "t1", "B", *RS2, next_time=1000.0)
    _rcf([t], {("MNR", "t1"): _robs("B", *RS2, 1000.0, anchor=_ranchor("A", *RS1, 1100.0))})
    assert t["prev_lat"] is None
    # anchor on the current stop (degenerate zero-length bracket)
    t = _rt("MNR", "t1", "A", *RS1, next_time=1100.0)
    _rcf([t], {("MNR", "t1"): _robs("A", *RS1, 1000.0, anchor=_ranchor("A", *RS1, 940.0))})
    assert t["prev_lat"] is None


def test_railroad_carry_forward_prunes_absent_trips():
    t = _rt("LIRR", "t1", "B", *RS2, next_time=1000.0)
    old = {("LIRR", "t1"): _robs("A", *RS1, 940.0), ("MNR", "gone"): _robs("X", 40.6, -73.9, 800.0)}
    mem = _rcf([t], old)
    assert ("LIRR", "t1") in mem and ("MNR", "gone") not in mem


def test_railroad_carry_forward_multi_poll_glide():
    p1 = _rt("LIRR", "t1", "A", *RS1, next_time=900.0)
    m1 = _rcf([p1], {})
    assert p1["prev_lat"] is None
    p2 = _rt("LIRR", "t1", "B", *RS2, next_time=1000.0)
    m2 = _rcf([p2], m1)
    assert (p2["prev_lat"], p2["prev_lon"], p2["prev_time"]) == (*RS1, 900.0)
    p3 = _rt("LIRR", "t1", "B", *RS2, next_time=1005.0)  # same segment
    m3 = _rcf([p3], m2)
    assert (p3["prev_lat"], p3["prev_lon"], p3["prev_time"]) == (*RS1, 900.0)  # anchor held
    p4 = _rt("LIRR", "t1", "C", *RS3, next_time=1100.0)  # advanced again
    _rcf([p4], m3)
    assert (p4["prev_lat"], p4["prev_lon"], p4["prev_time"]) == (*RS2, 1005.0)


def test_railroad_carry_forward_same_trip_id_independent_across_systems():
    # A trip_id present in BOTH systems must not cross-contaminate: each is keyed by
    # (system, trip_id). Both LIRR 99 and MNR 99 transition this poll, but from
    # DIFFERENT departed stations (LIRR A->B, MNR B->C), so each must synthesize its
    # OWN distinct prev from its own memory slot. Under a trip_id-alone key the two
    # would share one "99" slot: the read would cross the wrong anchor in (and the
    # write would collapse the memory to a single entry), so both the distinct-prev
    # assertions and the set(out) shape assertion below would fail.
    lirr = _rt("LIRR", "99", "B", *RS2, next_time=1000.0)
    mnr = _rt("MNR", "99", "C", *RS3, next_time=1000.0)
    mem = {
        ("LIRR", "99"): _robs("A", *RS1, 940.0),  # LIRR most recently departed A
        ("MNR", "99"): _robs("B", *RS2, 940.0),  # MNR most recently departed B
    }
    out = _rcf([lirr, mnr], mem)
    assert (lirr["prev_lat"], lirr["prev_lon"], lirr["prev_time"]) == (*RS1, 940.0)  # from A
    assert (mnr["prev_lat"], mnr["prev_lon"], mnr["prev_time"]) == (*RS2, 940.0)  # from B, not A
    assert set(out) == {("LIRR", "99"), ("MNR", "99")}


@pytest.mark.anyio
async def test_c3_an_empty_200_on_one_railroad_system_fails_that_system_only():
    # MNR serves a 200 with no body. Before C3 that decoded as MNR running zero
    # trains, so its markers vanished while the poll reported a clean success and
    # cleared the standing error. It now joins the per-system failed list, which is
    # what C2's retention and freshness block key on, so a rider sees MNR's last
    # known trains dimmed rather than an empty map half.
    # Real stops for LIRR, so the SECOND (placement) decode pass actually runs:
    # it is skipped entirely for a system with no static stops, which left the
    # placement decoder's own strict parse unexercised. REVIEW FIX.
    stops = {"LIRR": json.loads((FIXTURES / "railroad_lirr_stops.json").read_text())}
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": b""})
    trains, arrivals, _, failed, _, _ = await feeds.fetch_railroad_trains(client, stops)
    assert failed == ["MNR"]
    assert trains  # not vacuous: there ARE trains, and every one of them is LIRR
    assert all(t["system"] == "LIRR" for t in trains)
    # The KEY's presence is the proof the placement pass ran for LIRR (only that
    # loop sets it); the index itself is empty because the captured arrival times
    # are long past relative to the wall clock this live path uses.
    assert "LIRR" in arrivals
    assert "MNR" not in arrivals  # it never decoded, so it never reached that pass


@pytest.mark.anyio
async def test_c3_a_VALID_EMPTY_railroad_system_is_healthy_with_no_trains():
    # The other side of the line: a header-only feed is a system with nothing
    # running (overnight), which must stay a SUCCESS so the client shows honest
    # absence rather than retaining and dimming trains that genuinely are not out.
    header_only = pb.FeedMessage()
    header_only.header.gtfs_realtime_version = "2.0"
    stops = {"LIRR": json.loads((FIXTURES / "railroad_lirr_stops.json").read_text())}
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": header_only.SerializeToString()})
    trains, arrivals, _, failed, _, _ = await feeds.fetch_railroad_trains(client, stops)
    assert failed == []
    assert trains  # not vacuous: an all() over an empty list would pass either way
    assert all(t["system"] == "LIRR" for t in trains)
    # MNR decoded but has no static stops in this call, so the placement pass skips
    # it; LIRR has stops, so its key IS present. Either way MNR is not in the
    # failed list, which is the distinction under test.
    assert "LIRR" in arrivals


# ---------------- N2: one acceptance rule, two complementary surfaces ----------------
#
# THE FINDING. The GPS pass and the placement pass each decided, separately, whether a
# vehicle entity "has GPS". The placement pass's version omitted the geographic bounding
# box, so a positioned vehicle reporting an out-of-range coordinate was rejected by the
# GPS pass for being out of range AND suppressed by the placement pass for being
# GPS-equipped. It reached no surface at all.
#
# WHY THESE TESTS HAD TO BE WRITTEN AT ALL. Every positioned vehicle in both committed
# captures is inside the box, so the fix is byte-identical on all six railroad goldens
# and the whole backend suite passes unchanged either way. The defect is latent in the
# fixtures and only a capture carrying the fault can tell the fixed code from the broken
# code. Without these, the three call sites could silently drift apart again, which is
# how N2 arose in the first place.


def _capture(system: str):
    """The committed capture for one system, with its stops and its two frozen clocks."""
    key = system.lower()
    raw = (FIXTURES / f"railroad_{key}.pb").read_bytes()
    stops = json.loads((FIXTURES / f"railroad_{key}_stops.json").read_text())
    gps_now = json.loads((FIXTURES / f"railroad_{key}_expected.json").read_text())["now"]
    placed_now = json.loads((FIXTURES / f"railroad_{key}_placed_expected.json").read_text())["now"]
    return raw, stops, gps_now, placed_now


def _surfaces(raw: bytes, system: str, stops: dict, gps_now: float, placed_now: float):
    """The two surfaces a railroad train can reach, as trip_id sets plus their records.
    Both passes get the stops, as the live path gives them since contract 6.3: the GPS
    pass asks the position ladder, which asks the placement pass's own answer, and a GPS
    pass decoded without them would draw the six estimated trains on both surfaces."""
    gps, _ts = feeds._decode_railroad_vehicles(raw, system, gps_now, stops)
    placed = feeds._decode_railroad_placements(raw, system, stops, placed_now)
    return gps, placed, {t["trip_id"] for t in gps}, {t["trip_id"] for t in placed}


@pytest.mark.parametrize("system", SYSTEMS)
def test_no_railroad_train_is_drawn_on_both_surfaces(system):
    # The half of the invariant that the old code did get right, pinned so the fix
    # cannot buy its other half by double-drawing. A trip on both surfaces would be one
    # train painted twice: a live marker at its reported position and a hollow estimate
    # at a station, with no way for a rider to tell which is the real one.
    raw, stops, gps_now, placed_now = _capture(system)
    gps, placed, gps_ids, placed_ids = _surfaces(raw, system, stops, gps_now, placed_now)
    assert gps and placed, "not vacuous: both surfaces carry records on this capture"
    assert gps_ids & placed_ids == set(), f"{system}: trips drawn twice"


@pytest.mark.parametrize("system", SYSTEMS)
def test_every_accepted_vehicle_reaches_the_gps_surface(system):
    # The other half, on the unmodified capture: a vehicle the acceptance rule accepts is
    # emitted, exactly once. This is what makes the GPS pass "emits exactly the accepted
    # set" rather than "emits a subset of it".
    raw, stops, gps_now, placed_now = _capture(system)
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    canceled = feeds.railroad._canceled_trip_ids(feed)
    # The rule's third input since contract 6.3: the ladder, built as both passes build it.
    ladder = feeds.railroad._position_ladder(feed, system, stops, gps_now, canceled)
    accepted = [e for e in feed.entity if feeds.railroad._accepted_as_gps(e, canceled, ladder)]
    assert accepted, "not vacuous"
    gps, _placed, gps_ids, _placed_ids = _surfaces(raw, system, stops, gps_now, placed_now)
    assert len(gps) == len(accepted), f"{system}: GPS output must be exactly the accepted set"
    for entity in accepted:
        wanted = entity.vehicle.trip.trip_id or entity.id
        assert wanted in gps_ids, f"{system}: accepted vehicle {wanted} is not on the GPS surface"


def _freshest_positioned(feed):
    """The vehicle entity whose position was observed most recently: a step-1 vehicle, drawn
    at its own position, whose trip is placeable. Until contract 6.3 these tests displaced
    the OLDEST (6006_2026-06-20, 53676 s), the one the audit displaced; the age gate now
    withholds that vehicle before the box is ever asked, so displacing it would test the
    gate rather than N2."""
    positioned = [
        e for e in feed.entity if e.HasField("vehicle") and e.vehicle.HasField("position")
    ]
    return max(positioned, key=lambda e: e.vehicle.timestamp)


def _displace(raw: bytes, pick):
    """A copy of a capture with one chosen vehicle entity moved to lat 0 lon 0.

    THE AUDIT'S OWN EXPERIMENT, as a fixture. (0, 0) is in the Gulf of Guinea: it is the
    canonical "this coordinate is garbage" value and it is what the audit injected.
    """
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    entity = pick(feed)
    entity.vehicle.position.latitude = 0.0
    entity.vehicle.position.longitude = 0.0
    return feed.SerializeToString(), entity


def test_an_out_of_range_lirr_vehicle_falls_back_to_placement():
    # THE AUDIT'S REPRODUCTION, run against the fixed code, on a step-1 vehicle: the
    # freshest positioned LIRR vehicle (GO201_26_7588, train 7344, its fix 4 s old) is
    # moved out of range. Its trip_update is untouched and still carries 11 upcoming
    # stops at known stations, so a usable estimate exists.
    #
    # THE COUNTS ARE ASSERTED RELATIVE TO THIS CAPTURE'S OWN BASELINE, not as bare
    # literals, so a recapture cannot make this test quietly describe a different feed.
    # The absolute numbers today are GPS 38 -> 37 and placements 62 -> 63. (The audit
    # note says 69 -> 68: it was written before F02 dropped the capture's canceled trip
    # from the GPS output and before F01's age gate took that output from 68 to 38.)
    raw, stops, gps_now, placed_now = _capture("LIRR")
    _g, _p, base_gps_ids, base_placed_ids = _surfaces(raw, "LIRR", stops, gps_now, placed_now)
    feed = pb.FeedMessage.FromString(raw)
    ladder = feeds.railroad._position_ladder(
        feed, "LIRR", stops, gps_now, feeds.railroad._canceled_trip_ids(feed)
    )

    moved_raw, entity = _displace(raw, _freshest_positioned)
    trip_id = entity.vehicle.trip.trip_id
    assert ladder[trip_id] == 1, "the chosen train is drawn at its own, fresh position"
    assert trip_id in base_gps_ids, "the chosen train is on the GPS surface before the move"

    _g, placed, gps_ids, placed_ids = _surfaces(moved_raw, "LIRR", stops, gps_now, placed_now)
    assert len(gps_ids) == len(base_gps_ids) - 1, "it leaves the GPS surface"
    assert trip_id not in gps_ids
    assert len(placed_ids) == len(base_placed_ids) + 1, "and arrives on the placement surface"
    assert trip_id in placed_ids
    # N2'S FALLBACK, UNCHANGED BY THE GATE (memo D1): a vehicle the box rejects never
    # reaches the ladder, so its trip is placed as it always was, labeled `placed`.
    (row,) = [t for t in placed if t["trip_id"] == trip_id]
    assert row["provenance"] == "placed"
    # And it is counted where it is drawn: one fewer reported, one more placed (a marker
    # a vehicle produced), nothing more suppressed.
    assert feeds.railroad._position_steps(moved_raw, "LIRR", stops, gps_now) == {
        "reported": 26,
        "estimated": 6,
        "qualified": 11,
        "placed": 1,
        "suppressed": 24,
    }
    # THE POINT OF THE FINDING, stated as the invariant rather than as two counts: the
    # train is on exactly one surface. Before the fix it was on neither.
    assert (trip_id in gps_ids) + (trip_id in placed_ids) == 1
    assert gps_ids & placed_ids == set(), "and nothing else started being drawn twice"


def _vehicle_on(trip_id: str):
    """A _displace picker: the one positioned vehicle entity carrying `trip_id`."""

    def pick(feed):
        (entity,) = [
            e
            for e in feed.entity
            if e.HasField("vehicle")
            and e.vehicle.HasField("position")
            and e.vehicle.trip.trip_id == trip_id
        ]
        return entity

    return pick


def test_an_out_of_range_vehicle_withheld_for_age_stays_counted_withheld():
    # Q7'S COUNT IS ABOUT AGE, AND MOVING A COORDINATE OUT OF THE BOX DOES NOT MAKE A FIX
    # YOUNGER. GO201_26_8945 is one of the capture's 24: its own fix is 8370 s old and its
    # trip update, though 27 s old, names no stop still ahead, so nothing can place it.
    # Moved to lat 0 lon 0 it is judged by N2's fallback instead of the ladder, and the
    # fallback places nothing either; inside the box the ladder withholds it at step 5, and
    # "last seen over 10m ago" is exactly as true of it out of the box. So it stays in the
    # count, on neither surface. Before this rule the count read 23 while 24 were withheld.
    raw, stops, gps_now, placed_now = _capture("LIRR")
    trip_id = "GO201_26_8945"
    feed = pb.FeedMessage.FromString(raw)
    ladder = feeds.railroad._position_ladder(
        feed, "LIRR", stops, gps_now, feeds.railroad._canceled_trip_ids(feed)
    )
    assert ladder[trip_id] == 5, "withheld at the header, as captured"

    moved_raw, entity = _displace(raw, _vehicle_on(trip_id))
    assert gps_now - entity.vehicle.timestamp > cache.OBS_MAX_S
    _g, _p, gps_ids, placed_ids = _surfaces(moved_raw, "LIRR", stops, gps_now, placed_now)
    assert trip_id not in gps_ids | placed_ids, "drawn nowhere, in or out of the box"
    assert feeds.railroad._position_steps(moved_raw, "LIRR", stops, gps_now) == {
        "reported": 27,
        "estimated": 6,
        "qualified": 11,
        "placed": 0,
        "suppressed": 24,
    }


def test_an_out_of_range_vehicle_with_a_recent_fix_and_no_placeable_trip_is_counted_nowhere():
    # THE ONE POSITIONED, NON-CANCELED VEHICLE THE COUNTS LEAVE OUT, pinned so that it is a
    # decision rather than a gap. GO201_26_7987 is drawn qualified at the header: its own
    # fix is 139 s old and no prediction can place its trip. Moved out of the box it is on
    # neither surface, and no count is true of it: it is no marker, and it was not withheld
    # for age (inside the box the ladder draws it at its own position, step 3), so
    # `suppressed`'s "last seen over 10m ago" would be false. `qualified` goes 11 to 10 and
    # nothing else moves, so the five counts sum to 67 of the 68 vehicles.
    raw, stops, gps_now, placed_now = _capture("LIRR")
    trip_id = "GO201_26_7987"
    feed = pb.FeedMessage.FromString(raw)
    ladder = feeds.railroad._position_ladder(
        feed, "LIRR", stops, gps_now, feeds.railroad._canceled_trip_ids(feed)
    )
    assert ladder[trip_id] == 3, "drawn qualified at the header, as captured"

    moved_raw, entity = _displace(raw, _vehicle_on(trip_id))
    assert cache.OBS_FRESH_S < gps_now - entity.vehicle.timestamp <= cache.OBS_MAX_S
    _g, _p, gps_ids, placed_ids = _surfaces(moved_raw, "LIRR", stops, gps_now, placed_now)
    assert trip_id not in gps_ids | placed_ids, "its trip has nothing to place it with"
    steps = feeds.railroad._position_steps(moved_raw, "LIRR", stops, gps_now)
    assert steps == {
        "reported": 27,
        "estimated": 6,
        "qualified": 10,
        "placed": 0,
        "suppressed": 24,
    }
    assert sum(steps.values()) == 67


def test_an_out_of_range_mnr_combined_entity_falls_back_to_placement():
    # THE SAME DEFECT THROUGH THE OTHER LAYOUT, and it is a genuinely separate call site.
    # MNR's vehicle.trip.trip_id is the TRAIN NUMBER, which never matches a trip_update
    # id, so positioned_ids is inert for Metro-North: the per-entity acceptance test is
    # the only thing that decides. A fix that narrowed positioned_ids alone would leave
    # every MNR train exactly as lost as before, and this is the test that says so.
    raw, stops, gps_now, placed_now = _capture("MNR")
    _g, _p, base_gps_ids, base_placed_ids = _surfaces(raw, "MNR", stops, gps_now, placed_now)

    # A combined entity whose trip_update still has an upcoming stop, so placement has
    # something to place. Most of this capture's trip_updates are fully in the past.
    def placeable(feed):
        for e in feed.entity:
            if not (e.HasField("vehicle") and e.vehicle.HasField("position")):
                continue
            if not e.HasField("trip_update"):
                continue
            for stu in e.trip_update.stop_time_update:
                t = feeds.shared._stop_time(stu)
                if stu.stop_id in stops and t is not None and t >= placed_now - 60:
                    return e
        raise AssertionError("no MNR combined entity with an upcoming stop in the capture")

    moved_raw, entity = _displace(raw, placeable)
    trip_id = entity.trip_update.trip.trip_id
    assert entity.vehicle.trip.trip_id in base_gps_ids, "on the GPS surface before the move"

    _g, _p, gps_ids, placed_ids = _surfaces(moved_raw, "MNR", stops, gps_now, placed_now)
    assert len(gps_ids) == len(base_gps_ids) - 1, "it leaves the GPS surface"
    assert len(placed_ids) == len(base_placed_ids) + 1, "and arrives on the placement surface"
    assert trip_id in placed_ids
    assert gps_ids & placed_ids == set()


def test_a_canceled_trip_is_not_placed_when_a_second_trip_update_contradicts_it():
    # THE REGRESSION THIS FIX WOULD OTHERWISE HAVE INTRODUCED, and no committed capture
    # carries the shape, so nothing else would catch it.
    #
    # Narrowing positioned_ids by acceptance takes a CANCELED trip's id out of the set
    # that used to block its placement. If a feed carries two trip_updates for one
    # trip_id with different schedule_relationship, the non-canceled copy then reaches
    # the placement pass and F02's canceled train is back on the map, drawn as a
    # schedule estimate instead of a GPS marker. Measured before the cancellation drop
    # was widened to the feed's whole canceled set: placements 56 -> 57 with the
    # canceled trip among them.
    raw, stops, _gps_now, placed_now = _capture("LIRR")
    canceled_trip = "6004XX_2026-06-20"
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    source = next(
        e
        for e in feed.entity
        if e.HasField("trip_update") and e.trip_update.trip.trip_id == canceled_trip
    )
    assert source.trip_update.trip.schedule_relationship in feeds.shared._DROP_TRIP_RELATIONSHIPS
    duplicate = feed.entity.add()
    duplicate.CopyFrom(source)
    duplicate.id = source.id + "_CONTRADICTION"
    duplicate.trip_update.trip.schedule_relationship = 0  # SCHEDULED
    # AND IT HAS TO BE PLACEABLE, which the first draft of this test forgot. The canceled
    # trip's own stop_time_updates are both in the past at this capture's clock, so the
    # placement pass drops it for having no upcoming stop long before the cancellation
    # gate is consulted, and the test passed against every mutation: it proved nothing.
    # Giving the duplicate a future arrival at a known station is what makes the gate
    # observable. Measured with the gate narrowed back to this entity's own
    # schedule_relationship: placements 56 to 57 with the canceled trip among them.
    upcoming = duplicate.trip_update.stop_time_update[0]
    upcoming.stop_id = "83"  # Hampton Bays, in the committed static stops
    upcoming.arrival.time = int(placed_now) + 600

    placed = feeds._decode_railroad_placements(feed.SerializeToString(), "LIRR", stops, placed_now)
    assert canceled_trip not in {t["trip_id"] for t in placed}, (
        "a trip this feed cancels anywhere must not be placed from a contradicting copy"
    )
    baseline = feeds._decode_railroad_placements(raw, "LIRR", stops, placed_now)
    assert len(placed) == len(baseline), "and the contradiction adds no placement at all"


def test_the_train_number_survives_a_rejected_coordinate():
    # A LABEL IS NOT A POSITION CLAIM. positioned_ids and label_by_trip are built in one
    # loop, so narrowing both together reads natural; it would strip the rider-facing
    # train number off the arrivals board for exactly the train this fix recovers.
    # Measured on the step-1 vehicle the fallback test above displaces: all 11 of trip
    # GO201_26_7588's arrival rows (station 359 first) keep train_num "7344" after that
    # vehicle's coordinate is rejected. (Until contract 6.3 this displaced the oldest,
    # 6006_2026-06-20, whose station-141 row kept "521"; the age gate now withholds that
    # vehicle before the box is asked.)
    raw, stops, _gps_now, placed_now = _capture("LIRR")

    moved_raw, entity = _displace(raw, _freshest_positioned)
    trip_id = entity.vehicle.trip.trip_id
    label = entity.vehicle.vehicle.label or entity.vehicle.vehicle.id
    assert label, "the chosen vehicle carries a train number"

    _placed, arrivals = feeds._decode_railroad_feed(moved_raw, "LIRR", stops, placed_now)
    rows = [
        row
        for buckets in arrivals.values()
        for rows_ in buckets.values()
        for row in rows_
        if row["trip_id"] == trip_id
    ]
    assert len(rows) == 11, "the recovered trip still publishes its arrivals"
    assert all(row["train_num"] == label for row in rows), "with its train number intact"
