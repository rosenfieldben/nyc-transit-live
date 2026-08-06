"""Tests for the NJ Transit realtime decode (backend/feeds/njt.py).

EVERY RULE IN THE DECODER LAW HAS A TEST HERE, and every one of those tests has a
killing mutation recorded in the 15b handoff. The law is not a style guide: each
rule exists because the 2026-08-05 rush probe watched the failure happen, and the
comment on each test cites the observation it reproduces.

THE TRAP SHAPES ARE SYNTHESIZED, NOT CAPTURED, and deliberately so. A live
capture cannot be made to contain a cancellation on demand, so waiting for one
would mean the phantom rules ship untested; the builders below construct the
exact shapes the probe recorded, which is what makes "8% of peak Penn
stop_time_updates were phantoms" reproducible in 20 milliseconds. The captured
goldens (fixtures/njt_tu.pb) cover shape-truth against the real feed and are
gated separately.

The simulator has its own builders rather than importing these, matching the C6
precedent for `_publications`: the two tiers are supposed to be able to fail
independently, so neither imports the other's construction helpers.
"""

from __future__ import annotations

import pytest
from google.transit import gtfs_realtime_pb2 as pb

from feeds import njt

pytestmark = pytest.mark.anyio

# A fixed instant, so every window in this module is arithmetic rather than a
# race with the clock: 2026-08-06 18:15 EDT, the rush probe's own hour.
NOW = 1786061700.0

# 15a's indexes, trimmed to what these tests join against. Penn is 109 and the
# trap tests target it BY NAME, because "no phantom arrival at Penn" is the claim
# a rider would actually feel.
STOPS = {
    "109": {"id": "109", "name": "Penn Station New York", "lat": 40.750568, "lon": -73.993519},
    "112": {"id": "112", "name": "Newark Penn Station", "lat": 40.734924, "lon": -74.164581},
    "38": {"id": "38", "name": "Hoboken", "lat": 40.734984, "lon": -74.027683},
    "148": {"id": "148", "name": "Secaucus Upper Lvl", "lat": 40.761820, "lon": -74.074340},
}
TRIPS = {
    "T-3800": {"route_id": "9", "headsign": "New York", "short_name": "3800"},
    "T-1600": {"route_id": "13", "headsign": "Hoboken", "short_name": "1600"},
}

_TRIP_SR = pb.TripDescriptor.ScheduleRelationship
_STOP_SR = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship


def _feed(*trips, header_ts: float | None = NOW) -> bytes:
    """Serialize a FeedMessage from (populate) callables."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    if header_ts is not None:
        feed.header.timestamp = int(header_ts)
    for populate in trips:
        populate(feed.entity.add())
    return feed.SerializeToString()


def _trip(
    entity_id: str,
    trip_id: str,
    calls,
    *,
    route_id: str = "",
    trip_relationship=None,
):
    """One trip_update entity. `calls` is a list of
    (stop_id, arrival, departure, stop_relationship, delay); a None time is
    omitted from the wire entirely, which is how the bare SKIPPED variant is
    built."""

    def populate(entity):
        entity.id = entity_id
        tu = entity.trip_update
        tu.trip.trip_id = trip_id
        if route_id:
            tu.trip.route_id = route_id
        if trip_relationship is not None:
            tu.trip.schedule_relationship = trip_relationship
        for index, (stop_id, arrival, departure, rel, delay) in enumerate(calls, start=1):
            stu = tu.stop_time_update.add()
            stu.stop_id = stop_id
            stu.stop_sequence = index
            if rel is not None:
                stu.schedule_relationship = rel
            if arrival is not None:
                stu.arrival.time = int(arrival)
                if delay is not None:
                    stu.arrival.delay = delay
            elif delay is not None and departure is None:
                # A DELAY WITH NO TIME: the minimal legal StopTimeEvent, and one
                # field away from the bare timeless call this producer already
                # emits 35 times a peak poll. Written here so the timeless-call
                # tests build the real wire shape rather than an approximation.
                stu.arrival.delay = delay
            if departure is not None:
                stu.departure.time = int(departure)
                if delay is not None:
                    stu.departure.delay = delay

    return populate


def _decode(raw: bytes, now: float = NOW):
    return njt.decode_njt_trip_updates(raw, STOPS, TRIPS, now)


# ---------------------------------------------------------------------------
# Decoder law 1: trip-level CANCELED
# ---------------------------------------------------------------------------


def test_a_canceled_trip_never_reaches_arrivals_or_placement():
    """THE PENN PHANTOM, reproduced exactly as the rush probe found it.

    A trip-level CANCELED trip STAYS IN THE FEED, still joins the static, and
    marks every stop SKIPPED WHILE KEEPING FULL ARRIVAL AND DEPARTURE TIMES. 8% of
    peak Penn stop_time_updates were this shape. Nothing downstream can tell such
    a row from a running train, so if the filter is dropped a rider standing at
    Penn is told a canceled train is arriving in four minutes.

    MUTATION: delete the trip-level relationship check in
    decode_njt_trip_updates and this test fails on the arrivals assertion.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW + 60, NOW + 90, _STOP_SR.SKIPPED, 120),
                ("148", NOW + 400, NOW + 430, _STOP_SR.SKIPPED, 120),
                ("109", NOW + 900, NOW + 960, _STOP_SR.SKIPPED, 120),
            ],
            trip_relationship=_TRIP_SR.CANCELED,
        )
    )
    trains, arrivals, _ts, warnings = _decode(raw)
    assert trains == [], "a canceled trip must never be placed"
    assert arrivals == {}, "a canceled trip must never appear in arrivals"
    assert "109" not in arrivals, "THE PENN PHANTOM: no canceled arrival at Penn"
    assert warnings == []


def test_a_canceled_trip_is_dropped_even_when_its_stops_are_not_marked():
    """THE TRAP THE PROBES DID NOT SHOW, invented for the adversarial round and
    kept: a CANCELED trip whose stops carry NO stop-level relationship at all.

    Reading only the stop level would serve every one of its stops. The trip-level
    check is what makes this safe, and this test is what proves the two levels are
    read independently rather than one standing in for the other.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW + 60, NOW + 90, None, None),
                ("109", NOW + 900, NOW + 960, None, None),
            ],
            trip_relationship=_TRIP_SR.CANCELED,
        )
    )
    trains, arrivals, _ts, _w = _decode(raw)
    assert trains == []
    assert arrivals == {}


def test_a_partial_cancellation_keeps_its_surviving_stops():
    """The probe's partial cancellation: normal through Newark, then Secaucus and
    Penn dropped WITH A PLAUSIBLE DELAY STILL ATTACHED.

    The trip is NOT trip-level canceled, so it is a live train that stops serving
    some stations. Newark must still serve; Penn must not. A decoder that treated
    any SKIPPED stop as a canceled trip would lose the legitimate Newark row,
    which is the opposite error and just as wrong.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW + 60, NOW + 90, None, 60),
                ("148", NOW + 400, NOW + 430, _STOP_SR.SKIPPED, 300),
                ("109", NOW + 900, NOW + 960, _STOP_SR.SKIPPED, 300),
            ],
        )
    )
    _trains, arrivals, _ts, _w = _decode(raw)
    assert "112" in arrivals, "the surviving stop must still serve"
    assert "148" not in arrivals and "109" not in arrivals, "dropped stops must not serve"
    assert arrivals["112"][0]["train_num"] == "3800"


# ---------------------------------------------------------------------------
# Decoder law 2: both SKIPPED variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arrival", "departure", "variant"),
    [
        (NOW + 900, NOW + 960, "with times (238 seen at peak)"),
        (None, None, "bare, no times at all (35 seen at peak)"),
    ],
)
def test_both_skipped_variants_drop_the_stop(arrival, departure, variant):
    """THE NAMED VICTIM: a train skipping Penn while headsigned FOR Penn.

    Both observed variants must drop, and they drop on the same line, because the
    relationship is read before any time is. That ordering is the reason the bare
    variant needs no special case; a decoder that checked times first would keep
    the timed variant.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW + 60, NOW + 90, None, None),
                ("109", arrival, departure, _STOP_SR.SKIPPED, None),
            ],
        )
    )
    _trains, arrivals, _ts, _w = _decode(raw)
    assert "109" not in arrivals, f"SKIPPED {variant} must drop the stop"
    assert "112" in arrivals


def test_a_skipped_last_stop_leaves_the_trip_placeable():
    """THE ADVERSARIAL SIBLING: the SKIPPED stop is the trip's LAST.

    The trip still runs, it just terminates earlier than scheduled. Dropping the
    final call must not drop the train, and the placement must fall back to the
    calls that remain rather than reading past the end of the list.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 570, None, None),
                ("148", NOW + 300, NOW + 330, None, None),
                ("109", NOW + 900, NOW + 960, _STOP_SR.SKIPPED, None),
            ],
        )
    )
    trains, arrivals, _ts, _w = _decode(raw)
    assert len(trains) == 1, "a trip whose last stop is skipped still runs"
    assert "109" not in arrivals
    assert trains[0]["stop_id"] == "148", "it is heading for the last surviving stop"


def test_a_no_data_stop_drops_like_a_skipped_one():
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW + 60, NOW + 90, None, None),
                ("109", NOW + 900, NOW + 960, _STOP_SR.NO_DATA, None),
            ],
        )
    )
    _trains, arrivals, _ts, _w = _decode(raw)
    assert "109" not in arrivals


# ---------------------------------------------------------------------------
# Decoder law 3: ADDED
# ---------------------------------------------------------------------------


def test_an_added_trip_renders_with_a_synthesized_name():
    """ADDED WAS NEVER OBSERVED in either probe, which is exactly why it is
    handled rather than assumed away: the one shape we have never seen is the one
    that will arrive unannounced.

    It joins nothing in the static, so route and train number come off the wire
    and the display name is synthesized from them. The join miss must NOT produce
    a cross-check warning: a miss is the documented shape for ADDED, and warning
    on it would train an operator to ignore the signal that matters.
    """
    raw = _feed(
        _trip(
            "9999",
            "T-UNKNOWN",
            [
                ("112", NOW - 60, NOW - 30, None, None),
                ("109", NOW + 600, NOW + 660, None, None),
            ],
            route_id="9",
            trip_relationship=_TRIP_SR.ADDED,
        )
    )
    trains, arrivals, _ts, warnings = _decode(raw)
    assert len(trains) == 1, "an ADDED trip must not be dropped for missing the static"
    train = trains[0]
    assert train["train_num"] == "9999", "the train number falls back to entity.id"
    assert train["route_id"] == "9", "the realtime route_id is the only route there is"
    assert train["headsign"] == "9 9999", "synthesized from route plus train number"
    assert warnings == [], "a join miss is the documented ADDED shape, not a warning"
    assert arrivals["109"][0]["headsign"] == "9 9999"


def test_an_added_trip_with_no_route_still_renders():
    """The join-optional path must not choke on the thinnest possible ADDED trip."""
    raw = _feed(
        _trip(
            "7777",
            "T-BARE",
            [
                ("112", NOW - 600, NOW - 570, None, None),
                ("109", NOW + 600, NOW + 660, None, None),
            ],
            trip_relationship=_TRIP_SR.ADDED,
        )
    )
    trains, _arrivals, _ts, _w = _decode(raw)
    assert len(trains) == 1
    assert trains[0]["headsign"] == "7777"


# ---------------------------------------------------------------------------
# Decoder law 4/5: times and ordering
# ---------------------------------------------------------------------------


def test_absolute_time_is_authoritative_and_delay_rides_along():
    """delay, absolute time and scheduled_time are all present in this feed, so a
    decoder COULD derive times from schedule plus delay. It must not: the
    upstream's absolute time is what a departure board shows, and deriving would
    make us disagree with it on any row where the three drift."""
    raw = _feed(
        _trip("3800", "T-3800", [("109", NOW + 600, NOW + 660, None, 240)]),
    )
    _trains, arrivals, _ts, _w = _decode(raw)
    row = arrivals["109"][0]
    assert row["arrival"] == NOW + 600, "the absolute time, not schedule plus delay"
    assert row["delay"] == 240, "delay is carried for display"


def test_stop_sequence_orders_but_is_never_an_index():
    """stop_sequence is sparse and opaque (decoder law 5). Here it is present and
    DESCENDING relative to feed order, so a decoder that trusted feed order would
    interpolate along the wrong segment. Sequence 1 is Newark, 2 is Penn."""

    def populate(entity):
        entity.id = "3800"
        tu = entity.trip_update
        tu.trip.trip_id = "T-3800"
        for stop_id, seq, arrival, departure in (
            ("109", 2, NOW + 600, NOW + 660),
            ("112", 1, NOW - 600, NOW - 570),
        ):
            stu = tu.stop_time_update.add()
            stu.stop_id = stop_id
            stu.stop_sequence = seq
            stu.arrival.time = int(arrival)
            stu.departure.time = int(departure)

    trains, _arrivals, _ts, _w = _decode(_feed(populate))
    assert len(trains) == 1
    # Between Newark (seq 1, departed) and Penn (seq 2, upcoming), so it is
    # in-transit toward Penn rather than the reverse.
    assert trains[0]["status"] == "in-transit"
    assert trains[0]["stop_id"] == "109"


def test_sparse_stop_sequence_falls_back_to_feed_order():
    """When only SOME calls carry a sequence, sorting would interleave numbered
    and unnumbered rows arbitrarily. Feed order is the honest fallback (GTFS-RT
    requires stop order anyway), and this pins that choice."""

    def populate(entity):
        entity.id = "3800"
        tu = entity.trip_update
        tu.trip.trip_id = "T-3800"
        first = tu.stop_time_update.add()
        first.stop_id = "112"
        first.arrival.time = int(NOW - 600)
        first.departure.time = int(NOW - 570)
        second = tu.stop_time_update.add()
        second.stop_id = "109"
        second.stop_sequence = 7
        second.arrival.time = int(NOW + 600)
        second.departure.time = int(NOW + 660)

    trains, _arrivals, _ts, _w = _decode(_feed(populate))
    assert trains[0]["stop_id"] == "109"


# ---------------------------------------------------------------------------
# Decoder law 6: empty-success
# ---------------------------------------------------------------------------


def test_an_empty_but_valid_feed_serves_zero_trains_as_a_healthy_state():
    """The overnight 13-byte body: a valid header, no entities. parse_feed rule 4
    accepts it, and zero trains is a STATE, not a failure."""
    raw = _feed()
    assert len(raw) < 20, f"the overnight body is tiny, got {len(raw)} bytes"
    trains, arrivals, ts, warnings = _decode(raw)
    assert trains == [] and arrivals == {} and warnings == []
    assert ts == NOW, "the header timestamp still reports"


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_a_dwelling_train_is_at_its_station():
    """arrival <= now < departure. Both times were natively present on 103 of 103
    probed calls, so this is the common case."""
    raw = _feed(_trip("3800", "T-3800", [("109", NOW - 30, NOW + 30, None, None)]))
    trains, _arrivals, _ts, _w = _decode(raw)
    train = trains[0]
    assert train["status"] == "at-station"
    assert train["stop_id"] == "109"
    assert (train["latitude"], train["longitude"]) == (STOPS["109"]["lat"], STOPS["109"]["lon"])


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (NOW - 31, "in-transit"),  # one second before arrival: still moving
        (NOW - 30, "at-station"),  # arrival exactly: dwell begins, inclusive
        (NOW + 29, "at-station"),  # one second before departure: still dwelling
        (NOW + 30, "in-transit"),  # departure exactly: dwell ends, exclusive
    ],
)
def test_the_dwell_window_boundaries(now, expected):
    """THE BOUNDARY, both edges, because half-open is a choice and not an
    accident: arrival is inclusive and departure exclusive, so a train is never
    simultaneously dwelling and departed."""
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 570, None, None),
                ("109", NOW - 30, NOW + 30, None, None),
                ("38", NOW + 600, NOW + 660, None, None),
            ],
        )
    )
    trains, _arrivals, _ts, _w = _decode(raw, now=now)
    assert trains[0]["status"] == expected


def test_a_zero_second_dwell_never_places_at_the_station():
    """THE ADVERSARIAL SIBLING: arrival == departure, so the half-open window is
    empty. The train must be placed on a segment rather than at the platform, and
    nothing may divide by zero."""
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 600, None, None),
                ("109", NOW + 600, NOW + 600, None, None),
            ],
        )
    )
    trains, _arrivals, _ts, _w = _decode(raw)
    assert trains[0]["status"] == "in-transit"


def test_an_interpolated_train_sits_on_the_segment():
    """Halfway in time is halfway in space, on the straight segment. Straight is
    this phase's accepted limit; shape-following is 15c's decision."""
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 700, NOW - 600, None, None),
                ("109", NOW + 600, NOW + 700, None, None),
            ],
        )
    )
    trains, _arrivals, _ts, _w = _decode(raw)
    train = trains[0]
    assert train["status"] == "in-transit"
    mid_lat = (STOPS["112"]["lat"] + STOPS["109"]["lat"]) / 2
    mid_lon = (STOPS["112"]["lon"] + STOPS["109"]["lon"]) / 2
    assert train["latitude"] == pytest.approx(mid_lat, abs=1e-9)
    assert train["longitude"] == pytest.approx(mid_lon, abs=1e-9)
    # The anchors 15c glides between are emitted too, so the client can animate
    # exactly as it does for every other system.
    assert train["prev_lat"] == STOPS["112"]["lat"]
    assert train["next_time"] == NOW + 600


def test_a_trip_listed_far_ahead_of_its_origin_is_a_phantom():
    """A trip whose first stop is hours away is not a train parked at its origin
    all afternoon. Capped by the shared MAX_FUTURE_FIRST_STOP_S the subway uses
    for the same judgement."""
    raw = _feed(
        _trip("3800", "T-3800", [("112", NOW + 4000, NOW + 4060, None, None)]),
    )
    trains, arrivals, _ts, _w = _decode(raw)
    assert trains == [], "not yet running"
    assert "112" in arrivals, "but it IS a legitimate future arrival downstream"


def test_a_finished_trip_is_dropped_after_its_terminal_grace():
    raw = _feed(_trip("3800", "T-3800", [("109", NOW - 600, NOW - 500, None, None)]))
    assert _decode(raw)[0] == [], "well past its last departure"
    # Inside the grace it is still standing at the terminal.
    raw = _feed(_trip("3800", "T-3800", [("109", NOW - 40, NOW - 20, None, None)]))
    trains = _decode(raw)[0]
    assert len(trains) == 1 and trains[0]["status"] == "at-station"


def test_a_stop_the_static_does_not_carry_is_skipped_without_dropping_the_trip():
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 570, None, None),
                ("99999", NOW - 300, NOW - 270, None, None),
                ("109", NOW + 600, NOW + 660, None, None),
            ],
        )
    )
    trains, arrivals, _ts, _w = _decode(raw)
    assert len(trains) == 1
    assert "99999" not in arrivals
    # It interpolates across the unknown stop rather than being stranded by it.
    assert trains[0]["status"] == "in-transit"


# ---------------------------------------------------------------------------
# The join and its cross-check
# ---------------------------------------------------------------------------


def test_the_join_fills_route_headsign_and_train_number():
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 570, None, None),
                ("109", NOW + 600, NOW + 660, None, None),
            ],
        )
    )
    trains, _arrivals, _ts, warnings = _decode(raw)
    train = trains[0]
    assert (train["route_id"], train["headsign"], train["train_num"]) == ("9", "New York", "3800")
    assert warnings == []


def test_an_entity_id_mismatch_warns_without_dropping_the_train():
    """entity.id == train number == trip_short_name at 745 of 745 observations, so
    this cross-check should never fire. It warns rather than drops precisely
    BECAUSE the probe is confident: if the invariant starts breaking we want to
    know, not to silently lose trains while we guess which side moved."""
    raw = _feed(
        _trip(
            "XXXX",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 570, None, None),
                ("109", NOW + 600, NOW + 660, None, None),
            ],
        )
    )
    trains, _arrivals, _ts, warnings = _decode(raw)
    assert len(trains) == 1, "a mismatch must never drop the train"
    assert len(warnings) == 1
    assert "XXXX" in warnings[0] and "3800" in warnings[0]


def test_arrivals_are_sorted_and_capped():
    calls = [("109", NOW + 60 * i, NOW + 60 * i + 30, None, None) for i in range(12, 0, -1)]
    raw = _feed(*[_trip(f"{i}", f"T-{i}", [calls[i]]) for i in range(len(calls))])
    _trains, arrivals, _ts, _w = _decode(raw)
    rows = arrivals["109"]
    assert len(rows) == njt.ARRIVALS_PER_STOP
    assert rows == sorted(rows, key=lambda r: r["arrival"]), "soonest first"


def test_a_just_passed_stop_stays_briefly_then_goes():
    raw = _feed(_trip("3800", "T-3800", [("109", NOW - 30, NOW - 20, None, None)]))
    assert "109" in _decode(raw)[1], "just-passed stops keep the same grace as everywhere"
    raw = _feed(_trip("3800", "T-3800", [("109", NOW - 600, NOW - 590, None, None)]))
    assert _decode(raw)[1] == {}


# ---------------------------------------------------------------------------
# THE DWELL, and the timeless call: two shapes the adversarial round found
# ---------------------------------------------------------------------------


def test_a_train_standing_at_penn_is_on_pennsylvania_stations_own_board():
    """THE MAP AND THE BOARD MUST NOT DISAGREE ABOUT THE TRAIN A RIDER CAN CATCH.

    A train that pulled into Penn five minutes ago and leaves in ten is dwelling:
    arrival well past, departure well ahead. Reading arrival first put that call
    behind the just-passed grace, so the stop dropped out of the arrivals index
    entirely while _place simultaneously drew the train standing at that platform.
    The rider saw a train on the map at Penn and no such train on Penn's departure
    board, and it is the one departure they could still make.

    This is feeds.shared._stop_time's rule, whose docstring names this exact
    hazard, applied to this decoder's already-parsed calls (_still_upcoming). The
    ferry decoder had it right; this one did not.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 1800, NOW - 1790, None, None),
                ("109", NOW - 300, NOW + 600, None, None),
            ],
        )
    )
    trains, arrivals, _ts, _w = _decode(raw)
    assert [t["status"] for t in trains] == ["at-station"], "the map places it at the platform"
    assert trains[0]["stop_id"] == "109"
    assert len(arrivals.get("109", [])) == 1, (
        "and the board at that platform must list it: a dwelling call's DEPARTURE "
        "is what makes the stop still upcoming, not its arrival"
    )


def test_a_dwelling_train_does_not_sort_ahead_of_a_sooner_departure():
    """The same key, applied to ordering. A train that arrived thirty seconds ago
    and departs in eight minutes must sit BELOW one departing in one minute on a
    board a rider reads by time."""
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [("112", NOW - 900, NOW - 890, None, None), ("109", NOW - 30, NOW + 480, None, None)],
        ),
        _trip(
            "3802",
            "T-3802",
            [("112", NOW - 900, NOW - 890, None, None), ("109", NOW + 50, NOW + 60, None, None)],
        ),
    )
    _trains, arrivals, _ts, _w = _decode(raw)
    assert [row["train_num"] for row in arrivals["109"]] == ["3802", "3800"]


def test_a_timeless_first_call_does_not_park_a_train_at_a_terminal_it_has_not_reached():
    """THE PHANTOM THAT CAME IN THROUGH THE ONE BRANCH THAT NEVER CONSULTED THE CAP.

    A leading stop_time_update carrying a delay and no absolute time is the minimal
    legal StopTimeEvent, and one field away from the bare timeless call this
    producer already emits 35 times a peak poll. It made case 2's first_time None,
    so MAX_FUTURE_FIRST_STOP_S never ran, and the trip fell through to case 4,
    which had no "is the trip actually past this stop" test at all and placed it
    standing at its FINAL stop ninety minutes early, status at-station, next_time
    an hour and a half out.

    The control is the same trip WITHOUT the timeless call, which the cap has
    always rejected correctly. Both must now be dropped, for the same reason.
    """
    with_timeless = _feed(
        _trip(
            "3800",
            "T-3800",
            [("112", None, None, None, 120), ("109", NOW + 5400, NOW + 5430, None, None)],
        )
    )
    trains, arrivals, _ts, _w = _decode(with_timeless)
    assert trains == [], (
        "a train whose only timed call is ninety minutes out is not running; placing "
        "it at that stop is the phantom the cap exists to refuse"
    )
    # The BOARD still lists it, and that is right: it really does call at 109 in
    # ninety minutes. What must not happen is a marker on the map.
    assert len(arrivals.get("109", [])) == 1

    control = _feed(_trip("3800", "T-3800", [("109", NOW + 5400, NOW + 5430, None, None)]))
    assert _decode(control)[0] == [], "the control the cap already handled"


def test_a_timeless_middle_call_interpolates_across_the_gap_rather_than_teleporting():
    """The same defect one position along. A timeless call between two timed ones
    broke case 3's consecutive pairing on BOTH sides, so a train between Newark and
    Penn fell to case 4 and teleported onto Penn's platform.

    Pairing over the timed calls interpolates across the gap instead, which is the
    straight-line approximation _place already makes between any two stops.
    """
    raw = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 600, NOW - 590, None, None),
                ("38", None, None, None, 60),
                ("109", NOW + 900, NOW + 930, None, None),
            ],
        )
    )
    trains, _arrivals, _ts, _w = _decode(raw)
    assert len(trains) == 1
    train = trains[0]
    assert train["status"] == "in-transit", train
    # Strictly BETWEEN Newark (-74.1646) and Penn (-73.9935), never snapped onto
    # either endpoint, which is what the teleport looked like.
    assert -74.1646 < train["longitude"] < -73.9935, train
    assert train["latitude"] != STOPS["109"]["lat"]


def test_the_terminal_grace_is_a_window_not_an_open_ended_fallthrough():
    """Case 4's two edges, asserted together because only the pair pins the shape.

    A train thirty seconds past its last departure is still standing at its
    terminal and should be drawn there. One two hours past is done. One that has
    not got there yet was never case 4's business at all.
    """
    just_done = _feed(
        _trip(
            "3800",
            "T-3800",
            [("112", NOW - 1800, NOW - 1790, None, None), ("109", NOW - 30, NOW - 20, None, None)],
        )
    )
    assert [t["status"] for t in _decode(just_done)[0]] == ["at-station"]

    long_done = _feed(
        _trip(
            "3800",
            "T-3800",
            [
                ("112", NOW - 9000, NOW - 8990, None, None),
                ("109", NOW - 8000, NOW - 7990, None, None),
            ],
        )
    )
    assert _decode(long_done)[0] == []


def test_a_trip_whose_every_call_is_timeless_is_dropped_rather_than_guessed_at():
    """No time anywhere is no position anywhere. The train is not placed, and it
    does not raise on the way to not being placed."""
    raw = _feed(
        _trip("3800", "T-3800", [("112", None, None, None, 60), ("109", None, None, None, 120)])
    )
    trains, arrivals, _ts, _w = _decode(raw)
    assert trains == []
    assert arrivals == {}
