"""Unit tests for the pure GTFS-RT decoding logic in feeds.py."""

import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from google.transit import gtfs_realtime_pb2 as pb

from feeds import (
    ARRIVALS_PER_DIRECTION,
    NYC_TZ,
    SUBWAY_FEED_URLS,
    _aggregate_feeds,
    _decode_feed,
    _decode_trains,
    _stop_time,
    _trip_start_ts,
    carry_forward_prev,
    fetch_subway_trains,
    fetch_vehicle_positions,
)

# Fixed "now": 2026-06-10 12:00:00 New York time.
NOON = datetime(2026, 6, 10, 12, 0, 0, tzinfo=NYC_TZ)
NOW = NOON.timestamp()
TODAY = "20260610"

STOPS = {
    "A01N": {"name": "Alpha", "lat": 40.70, "lon": -74.00},
    "A02N": {"name": "Beta", "lat": 40.71, "lon": -74.01},
    "A03S": {"name": "Gamma", "lat": 40.72, "lon": -74.02},
}


def make_stu(stop_id=None, arrival=None, departure=None, relationship=None):
    stu = pb.TripUpdate.StopTimeUpdate()
    if stop_id is not None:
        stu.stop_id = stop_id
    if arrival is not None:
        stu.arrival.time = int(arrival)
    if departure is not None:
        stu.departure.time = int(departure)
    if relationship is not None:
        stu.schedule_relationship = relationship
    return stu


def make_feed(*trips):
    """trips: dicts with trip_id, route_id, start_date, optional relationship
    (trip-level ScheduleRelationship), and stus=[(stop, arr, dep[, relationship])]."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(NOW)
    for i, t in enumerate(trips):
        entity = feed.entity.add()
        entity.id = f"ent{i}"
        tu = entity.trip_update
        tu.trip.trip_id = t.get("trip_id", "")
        tu.trip.route_id = t.get("route_id", "")
        tu.trip.start_date = t.get("start_date", TODAY)
        if "relationship" in t:
            tu.trip.schedule_relationship = t["relationship"]
        for stu_spec in t.get("stus", []):
            tu.stop_time_update.append(make_stu(*stu_spec))
    return feed.SerializeToString()


def decode(*trips):
    return _decode_trains(make_feed(*trips), STOPS, "TEST", NOW)


# ---------------- _stop_time ----------------


def test_stop_time_arrival_only():
    assert _stop_time(make_stu("A01N", arrival=100)) == 100


def test_stop_time_departure_only():
    assert _stop_time(make_stu("A01N", departure=200)) == 200


def test_stop_time_returns_latest_of_both():
    # Dwelling train: arrival in the past, departure in the future — the
    # LATEST event time must win or held trains get plotted a stop ahead.
    assert _stop_time(make_stu("A01N", arrival=100, departure=200)) == 200
    assert _stop_time(make_stu("A01N", arrival=200, departure=100)) == 200


def test_stop_time_no_events():
    assert _stop_time(make_stu("A01N")) is None


def test_stop_time_zero_treated_as_absent():
    assert _stop_time(make_stu("A01N", arrival=0)) is None


# ---------------- _trip_start_ts ----------------


def trip(trip_id="", start_time="", start_date=""):
    return SimpleNamespace(trip_id=trip_id, start_time=start_time, start_date=start_date)


def test_trip_start_explicit_start_time():
    ts = _trip_start_ts(trip("123600_SI.S03R", "20:43:30", "20260609"))
    assert ts == datetime(2026, 6, 9, 20, 43, 30, tzinfo=NYC_TZ).timestamp()


def test_trip_start_from_centiminute_prefix():
    # 71000 centiminutes = 710 minutes = 11:50.
    ts = _trip_start_ts(trip("71000_1..N15R", "", TODAY))
    assert ts == datetime(2026, 6, 10, 11, 50, 0, tzinfo=NYC_TZ).timestamp()


def test_trip_start_past_midnight_rolls_to_next_day():
    # 147000 centiminutes = 24h30m after midnight of the service day.
    ts = _trip_start_ts(trip("147000_A..S04R", "", "20260609"))
    assert ts == datetime(2026, 6, 10, 0, 30, 0, tzinfo=NYC_TZ).timestamp()


def test_trip_start_unparseable_returns_none():
    assert _trip_start_ts(trip("LIRR-weird-id", "", "20260609")) is None
    assert _trip_start_ts(trip()) is None


def test_trip_start_malformed_start_time_falls_back_to_prefix():
    # "1:2" doesn't unpack to h:m:s; the 60000 prefix (10:00) should be used.
    ts = _trip_start_ts(trip("60000_G..N12R", "1:2", TODAY))
    assert ts == datetime(2026, 6, 10, 10, 0, 0, tzinfo=NYC_TZ).timestamp()


# ---------------- _decode_trains: chosen-stop selection ----------------

# Prefixes relative to NOW (noon): 70000 = 11:40 (started), 73000 = 12:10
# (not yet departed), 72100 = 12:01 (within the 120s start grace).
STARTED = "70000_1..N01R"
UNSTARTED = "73000_1..N01R"
BARELY_FUTURE = "72100_1..N01R"


def test_dwelling_train_stays_at_current_stop():
    trains = decode(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", NOW - 180, NOW + 120), ("A02N", NOW + 600, None)],
        }
    )
    assert len(trains) == 1
    assert trains[0]["stop_id"] == "A01N"
    assert trains[0]["stop_name"] == "Alpha"
    assert trains[0]["direction"] == "Northbound"


def test_past_stop_skipped_for_next_upcoming():
    trains = decode(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", NOW - 300, NOW - 240), ("A02N", NOW + 120, None)],
        }
    )
    assert [t["stop_id"] for t in trains] == ["A02N"]


def test_finished_trip_dropped():
    trains = decode({"trip_id": STARTED, "route_id": "1", "stus": [("A01N", NOW - 300, NOW - 240)]})
    assert trains == []


def test_unknown_stop_skipped_to_next_resolvable():
    trains = decode(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("ZZ9N", NOW + 60, None), ("A02N", NOW + 300, None)],
        }
    )
    assert [t["stop_id"] for t in trains] == ["A02N"]


def test_no_times_falls_back_to_first_resolvable():
    trains = decode(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A01N", None, None), ("A02N", None, None)]}
    )
    assert [t["stop_id"] for t in trains] == ["A01N"]


def test_unstarted_trip_excluded():
    trains = decode({"trip_id": UNSTARTED, "route_id": "1", "stus": [("A01N", NOW + 660, None)]})
    assert trains == []


def test_trip_within_start_grace_included():
    trains = decode({"trip_id": BARELY_FUTURE, "route_id": "1", "stus": [("A01N", NOW + 90, None)]})
    assert len(trains) == 1


def test_unparseable_trip_id_uses_first_stop_time_cap():
    far = {
        "trip_id": "WEIRD-ID-1",
        "route_id": "1",
        "start_date": "",
        "stus": [("A01N", NOW + 600, None)],
    }
    near = {
        "trip_id": "WEIRD-ID-2",
        "route_id": "1",
        "start_date": "",
        "stus": [("A02N", NOW + 60, None)],
    }
    trains = decode(far, near)
    assert [t["trip_id"] for t in trains] == ["WEIRD-ID-2"]


def test_placement_cap_uses_first_resolvable_not_index_zero():
    # Start-less trip whose first LISTED stop is an unknown station and whose
    # first RESOLVABLE stop is ~10 min out. The old `chosen is stop_time_update[0]`
    # check let it slip through (chosen was the 2nd stu); keyed on first_resolvable
    # it is correctly dropped by the far-future cap.
    trains = decode(
        {
            "trip_id": "WEIRD-ID",
            "route_id": "1",
            "start_date": "",
            "stus": [("ZZ9N", NOW + 30, None), ("A01N", NOW + 600, None)],
        }
    )
    assert trains == []


def test_southbound_direction_from_stop_suffix():
    trains = decode({"trip_id": STARTED, "route_id": "1", "stus": [("A03S", NOW + 60, None)]})
    assert trains[0]["direction"] == "Southbound"


def test_missing_trip_id_falls_back_to_entity_id():
    trains = decode({"trip_id": "", "route_id": "1", "stus": [("A01N", NOW + 60, None)]})
    assert trains[0]["trip_id"] == "TEST:ent0"


def test_missing_route_id_is_none():
    trains = decode({"trip_id": STARTED, "route_id": "", "stus": [("A01N", NOW + 60, None)]})
    assert trains[0]["route_id"] is None


def test_entity_without_trip_update_skipped():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    entity = feed.entity.add()
    entity.id = "vehicle-only"
    assert _decode_trains(feed.SerializeToString(), STOPS, "TEST", NOW) == []


# ---------------- _decode_feed: per-station arrivals ----------------


def decode_feed(*trips):
    return _decode_feed(make_feed(*trips), STOPS, "TEST", NOW)


def test_arrivals_include_every_upcoming_stop():
    # Placement keeps only the next stop; arrivals keep them all, keyed by the
    # station id (platform id with the N/S suffix stripped).
    _, arrivals, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", NOW + 60, None), ("A02N", NOW + 300, None)],
        }
    )
    assert sorted(arrivals) == ["A01", "A02"]
    assert arrivals["A01"]["Northbound"][0] == {
        "route_id": "1",
        "trip_id": STARTED,
        "arrival": NOW + 60,
    }
    assert arrivals["A02"]["Northbound"][0]["arrival"] == NOW + 300


def test_arrivals_drop_past_stops():
    _, arrivals, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", NOW - 300, NOW - 240), ("A02N", NOW + 120, None)],
        }
    )
    assert "A01" not in arrivals  # already passed (same now-60 grace as placement)
    assert arrivals["A02"]["Northbound"]


def test_arrivals_dwelling_train_kept_at_current_station():
    # Arrival in the past but departure in the future -> _stop_time future -> kept.
    _, arrivals, _ = decode_feed(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A01N", NOW - 30, NOW + 90)]}
    )
    assert arrivals["A01"]["Northbound"][0]["arrival"] == NOW + 90


def test_arrivals_populate_both_directions():
    _, arrivals, _ = decode_feed(
        {"trip_id": "70000_1..N01R", "route_id": "1", "stus": [("A01N", NOW + 60, None)]},
        {"trip_id": "70010_1..S01R", "route_id": "1", "stus": [("A03S", NOW + 90, None)]},
    )
    assert arrivals["A01"]["Northbound"]
    assert arrivals["A03"]["Southbound"]


def test_arrivals_include_unstarted_trip_as_downstream_arrival():
    # The deliberate divergence: an unstarted trip (departs its origin in ~10
    # min) is EXCLUDED from placement, but its stop is a real future arrival.
    trains, arrivals, _ = decode_feed(
        {"trip_id": UNSTARTED, "route_id": "1", "stus": [("A01N", NOW + 660, None)]}
    )
    assert trains == []  # placement filter excludes the unstarted trip
    assert arrivals["A01"]["Northbound"][0]["trip_id"] == UNSTARTED


def _pad(*feeds_bytes):
    """Pad a few feed results out to the full SUBWAY_FEED_URLS count with empty
    (but valid) feeds, so _aggregate_feeds' zip lines up."""
    empty = make_feed()  # a header-only feed (FeedMessage.header is required)
    return list(feeds_bytes) + [empty] * (len(SUBWAY_FEED_URLS) - len(feeds_bytes))


def test_arrivals_dedup_same_trip_across_feeds():
    feed = make_feed(
        {"trip_id": "70000_1..N01R", "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    # Same trip present in two feed results -> deduped to one placement and one
    # arrival (covers both the train seen_trips and the arrival_trips guards).
    trains, arrivals, _, feed_errors = _aggregate_feeds(_pad(feed, feed), STOPS, NOW)
    assert not feed_errors
    assert len(trains) == 1
    assert len(arrivals["A01"]["Northbound"]) == 1


def test_arrivals_sorted_and_capped_per_direction():
    trips = [
        {
            "trip_id": f"{70000 + i}_1..N01R",
            "route_id": "1",
            "stus": [("A01N", NOW + 600 - i * 10, None)],
        }
        for i in range(ARRIVALS_PER_DIRECTION + 2)
    ]
    _, arrivals, _, _ = _aggregate_feeds(_pad(make_feed(*trips)), STOPS, NOW)
    northbound = arrivals["A01"]["Northbound"]
    assert len(northbound) == ARRIVALS_PER_DIRECTION  # capped to the soonest
    times = [a["arrival"] for a in northbound]
    assert times == sorted(times)  # ascending
    assert times[0] == NOW + 600 - (ARRIVALS_PER_DIRECTION + 1) * 10  # soonest kept


def test_arrivals_skip_stop_without_clean_direction():
    # A resolvable stop whose id has no N/S suffix has no platform direction,
    # so it is not recorded as a station arrival.
    stops = {**STOPS, "A04": {"name": "Delta", "lat": 40.7, "lon": -74.0}}
    _, arrivals, _ = _decode_feed(
        make_feed({"trip_id": STARTED, "route_id": "1", "stus": [("A04", NOW + 60, None)]}),
        stops,
        "TEST",
        NOW,
    )
    assert "A04" not in arrivals


# ---------------- interpolation anchors (prev_*/next_time) ----------------

# STOPS coords, for asserting prev/next anchors.
A01 = (40.70, -74.00)
A02 = (40.71, -74.01)


def test_interp_anchors_prev_passed_next_upcoming():
    # A01N already passed, A02N upcoming -> placed at A02N (static fallback),
    # prev anchored to A01N with both timestamps for interpolation.
    trains, _, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", NOW - 300, None), ("A02N", NOW + 120, None)],
        }
    )
    t = trains[0]
    assert (t["latitude"], t["longitude"]) == A02 and t["stop_id"] == "A02N"
    assert (t["prev_lat"], t["prev_lon"]) == A01
    assert t["prev_time"] == NOW - 300
    assert t["next_time"] == NOW + 120


def test_interp_prev_null_at_first_resolvable_stop():
    trains, _, _ = decode_feed(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    t = trains[0]
    assert t["prev_lat"] is None and t["prev_lon"] is None and t["prev_time"] is None
    assert t["next_time"] == NOW + 60


def test_interp_dwelling_prev_prior_next_is_departure():
    # Dwelling at A02N (arrival past, departure future) -> chosen via the
    # departure; prev is the prior station, next_time is that departure.
    trains, _, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", NOW - 300, None), ("A02N", NOW - 30, NOW + 90)],
        }
    )
    t = trains[0]
    assert t["stop_id"] == "A02N"
    assert (t["prev_lat"], t["prev_lon"]) == A01
    assert t["prev_time"] == NOW - 300
    assert t["next_time"] == NOW + 90  # the future departure


def test_interp_no_times_fallback_nulls_prev_and_next():
    trains, _, _ = decode_feed(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A01N", None, None), ("A02N", None, None)]}
    )
    t = trains[0]
    assert t["stop_id"] == "A01N"  # first resolvable, static fallback
    assert t["prev_lat"] is None and t["prev_lon"] is None and t["prev_time"] is None
    assert t["next_time"] is None


def test_interp_untimed_prev_keeps_coords_but_null_time():
    # The previous resolvable stop has coords but no time: prev coords are set
    # (so v2 could still use them) while prev_time is null, which makes the
    # frontend helper fall back to the static position. Locks that edge.
    trains, _, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [("A01N", None, None), ("A02N", NOW + 60, None)],
        }
    )
    t = trains[0]
    assert t["stop_id"] == "A02N"
    assert (t["prev_lat"], t["prev_lon"]) == A01  # untimed predecessor, coords kept
    assert t["prev_time"] is None
    assert t["next_time"] == NOW + 60


# ---------------- schedule-relationship filtering ----------------

_TRIP_CANCELED = pb.TripDescriptor.ScheduleRelationship.CANCELED
_STOP_SKIPPED = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.SKIPPED
_STOP_NO_DATA = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.NO_DATA


def test_canceled_trip_dropped_from_placement_and_arrivals():
    trains, arrivals, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "relationship": _TRIP_CANCELED,
            "stus": [("A01N", NOW + 60, None)],
        }
    )
    assert trains == []
    assert arrivals == {}


def test_deleted_trip_dropped_from_placement_and_arrivals():
    # Pins the gtfs-realtime-bindings >= 2.1 floor doing its job: under 2.0.0 a
    # wire DELETED=7 was coerced to SCHEDULED by proto2's closed-enum decoding
    # and slipped past the drop set, rendering a ghost train. With the enum
    # value present, the getattr in _DROP_TRIP_RELATIONSHIPS resolves it and the
    # trip vanishes from BOTH outputs, exactly like CANCELED.
    deleted = pb.TripDescriptor.ScheduleRelationship.DELETED
    trains, arrivals, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "relationship": deleted,
            "stus": [("A01N", NOW + 60, None)],
        }
    )
    assert trains == []
    assert arrivals == {}


def test_new_trip_is_not_dropped():
    # NEW (also added in bindings 2.1.0) marks a trip new relative to the static
    # schedule, the same family as ADDED. ADDED trips run and are not dropped,
    # so NEW must not be dropped either: unfiltered NEW is a decision, not the
    # coercion bug the DELETED test above guards against.
    new = pb.TripDescriptor.ScheduleRelationship.NEW
    trains, arrivals, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "relationship": new,
            "stus": [("A01N", NOW + 60, None)],
        }
    )
    assert len(trains) == 1
    assert trains[0]["stop_id"] == "A01N"
    assert arrivals["A01"]["Northbound"]  # its upcoming stop is indexed too


def test_skipped_and_no_data_stops_excluded():
    # A01N (skipped) and A02N (no-data) carry no real prediction; A03S does.
    trains, arrivals, _ = decode_feed(
        {
            "trip_id": STARTED,
            "route_id": "1",
            "stus": [
                ("A01N", NOW + 60, None, _STOP_SKIPPED),
                ("A02N", NOW + 120, None, _STOP_NO_DATA),
                ("A03S", NOW + 180, None),
            ],
        }
    )
    assert "A01" not in arrivals and "A02" not in arrivals
    assert arrivals["A03"]["Southbound"]
    assert [t["stop_id"] for t in trains] == ["A03S"]  # placement skips both too


# ---------------- feed_timestamp threading ----------------


def _feed_with_ts(ts, *trips):
    """A feed whose FeedHeader.timestamp is overridden to `ts`."""
    feed = pb.FeedMessage()
    feed.ParseFromString(make_feed(*trips))
    feed.header.timestamp = int(ts)
    return feed.SerializeToString()


def test_decode_feed_returns_header_timestamp():
    _, _, ts = decode_feed(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    assert ts == NOW  # make_feed sets header.timestamp = int(NOW)


def test_decode_feed_timestamp_none_when_feed_omits_it():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"  # timestamp left at its 0 default
    _, _, ts = _decode_feed(feed.SerializeToString(), STOPS, "TEST", NOW)
    assert ts is None


def test_aggregate_uses_oldest_feed_timestamp():
    # The combined view is only as fresh as its stalest member.
    older = _feed_with_ts(
        NOW - 100, {"trip_id": "70000_1..N01R", "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    newer = _feed_with_ts(
        NOW, {"trip_id": "70001_1..N01R", "route_id": "1", "stus": [("A02N", NOW + 60, None)]}
    )
    _, _, ts, _ = _aggregate_feeds(_pad(newer, older), STOPS, NOW)
    assert ts == NOW - 100  # min across decoded feeds (padding feeds carry NOW)


# ---------------- _aggregate_feeds: dedup + per-feed error handling ----------------


def test_aggregate_skips_a_feed_whose_fetch_raised():
    good = make_feed(
        {"trip_id": "70000_1..N01R", "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    results = _pad(RuntimeError("ACE down"), good)
    trains, arrivals, _, feed_errors = _aggregate_feeds(results, STOPS, NOW)
    assert len(feed_errors) == 1
    assert "ACE down" in next(iter(feed_errors.values()))
    assert len(trains) == 1  # the good feed still decoded
    assert arrivals["A01"]["Northbound"]


def test_aggregate_skips_a_corrupt_protobuf_feed():
    good = make_feed(
        {"trip_id": "70000_1..N01R", "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    results = _pad(b"\x0a\xff", good)  # truncated length-delimited field -> DecodeError
    trains, arrivals, _, feed_errors = _aggregate_feeds(results, STOPS, NOW)
    assert len(feed_errors) == 1
    assert "undecodable protobuf" in next(iter(feed_errors.values()))
    assert len(trains) == 1
    assert arrivals["A01"]["Northbound"]


def test_aggregate_all_feeds_failed_records_every_error():
    results = [RuntimeError("down")] * len(SUBWAY_FEED_URLS)
    trains, arrivals, _, feed_errors = _aggregate_feeds(results, STOPS, NOW)
    assert len(feed_errors) == len(SUBWAY_FEED_URLS)
    assert trains == [] and arrivals == {}


# ---------------- fetch_subway_trains: partial vs total failure ----------------


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeClient:
    """Returns the same feed bytes for every URL, or raises when content is None."""

    def __init__(self, content):
        self._content = content

    async def get(self, url):
        if self._content is None:
            raise RuntimeError("connect failed")
        return _FakeResp(self._content)


def _live_feed(trip_id, stop_id, arrival_offset):
    """A one-trip feed timed against the real wall clock (fetch_subway_trains
    uses time.time()), with a long-past start_date so the trip always counts
    as started regardless of when the test runs."""
    now = time.time()
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(now)
    entity = feed.entity.add()
    entity.id = "e0"
    tu = entity.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.route_id = "1"
    tu.trip.start_date = "20200101"
    stu = tu.stop_time_update.add()
    stu.stop_id = stop_id
    stu.arrival.time = int(now + arrival_offset)
    return feed.SerializeToString()


@pytest.mark.anyio
async def test_fetch_subway_trains_returns_on_partial_success():
    raw = _live_feed("100_1..N01R", "A01N", 60)
    trains, arrivals, _, failed = await fetch_subway_trains(STOPS, _FakeClient(raw))
    assert len(trains) == 1  # same feed for all URLs -> deduped to one
    assert arrivals["A01"]["Northbound"]
    assert failed == []  # every feed returned the same valid bytes


@pytest.mark.anyio
async def test_fetch_subway_trains_raises_when_all_feeds_fail():
    with pytest.raises(RuntimeError):
        await fetch_subway_trains(STOPS, _FakeClient(None))


# ---------------- fetch_vehicle_positions: NYC bounds + timestamp ----------------


class _FakeBusClient:
    """Bus client stub: fetch_vehicle_positions calls get(url, params=...)."""

    def __init__(self, content):
        self._content = content

    async def get(self, url, params=None):
        return _FakeResp(self._content)


def _bus_feed(*vehicles):
    """vehicles: (id, route_id, lat, lon) tuples."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(NOW)
    for i, (vid, route, lat, lon) in enumerate(vehicles):
        entity = feed.entity.add()
        entity.id = f"v{i}"
        v = entity.vehicle
        v.vehicle.id = vid
        v.trip.route_id = route
        v.position.latitude = lat
        v.position.longitude = lon
    return feed.SerializeToString()


@pytest.mark.anyio
async def test_fetch_vehicle_positions_skips_out_of_range(monkeypatch):
    monkeypatch.setenv("BUS_TIME_API_KEY", "test-key")
    raw = _bus_feed(("in", "M15", 40.75, -73.99), ("out", "X", 0.0, 0.0))
    vehicles, ts = await fetch_vehicle_positions(_FakeBusClient(raw))
    assert [v["id"] for v in vehicles] == ["in"]  # the (0, 0) vehicle is dropped
    assert ts == NOW


# ---------------- carry_forward_prev: cross-poll previous-station anchor ----------------


def mk_train(trip_id, stop_id, lat, lon, next_time, prev_lat=None, prev_lon=None, prev_time=None):
    """A minimal placement train dict (the shape carry_forward_prev reads/writes)."""
    return {
        "trip_id": trip_id,
        "route_id": "1",
        "latitude": lat,
        "longitude": lon,
        "stop_id": stop_id,
        "stop_name": stop_id,
        "direction": "Northbound",
        "prev_lat": prev_lat,
        "prev_lon": prev_lon,
        "prev_time": prev_time,
        "next_time": next_time,
    }


def _obs(stop_id, lat, lon, next_time, anchor=None):
    # A last_positions entry: last observed position plus the carried anchor.
    return {"stop_id": stop_id, "lat": lat, "lon": lon, "next_time": next_time, "anchor": anchor}


def _anchor(stop_id, lat, lon, time):
    return {"stop_id": stop_id, "lat": lat, "lon": lon, "time": time}


# Three stations along one segment chain.
S1 = (40.70, -74.00)
S2 = (40.71, -74.01)
S3 = (40.72, -74.02)


def test_carry_forward_transition_synthesizes_prev():
    # Last poll observed approaching S1 (next_time 940), no anchor yet; now at S2.
    train = mk_train("t1", "S2", *S2, next_time=1000.0)
    new_mem = carry_forward_prev([train], {"t1": _obs("S1", *S1, 940.0)})
    assert (train["prev_lat"], train["prev_lon"], train["prev_time"]) == (*S1, 940.0)
    # The just-departed station becomes the stored anchor for the next poll.
    assert new_mem["t1"]["anchor"] == _anchor("S1", *S1, 940.0)
    assert (new_mem["t1"]["stop_id"], new_mem["t1"]["next_time"]) == ("S2", 1000.0)


def test_carry_forward_stable_segment_persists_anchor():
    # The case the old design missed: same next stop as last poll, but a carried
    # anchor is present -> prev is STILL synthesized, and the anchor is held fixed.
    carried = _anchor("S1", *S1, 940.0)
    train = mk_train("t1", "S2", *S2, next_time=1010.0)
    new_mem = carry_forward_prev([train], {"t1": _obs("S2", *S2, 1000.0, anchor=carried)})
    assert (train["prev_lat"], train["prev_lon"], train["prev_time"]) == (*S1, 940.0)
    assert new_mem["t1"]["anchor"] == carried  # carried forward unchanged


def test_carry_forward_preserves_real_feed_prev_but_stores_anchor():
    train = mk_train(
        "t1", "S2", *S2, next_time=1000.0, prev_lat=40.65, prev_lon=-73.95, prev_time=900.0
    )
    new_mem = carry_forward_prev([train], {"t1": _obs("S1", *S1, 940.0)})
    assert (train["prev_lat"], train["prev_lon"], train["prev_time"]) == (40.65, -73.95, 900.0)
    # The anchor is still computed from the transition and stored for next poll.
    assert new_mem["t1"]["anchor"] == _anchor("S1", *S1, 940.0)


def test_carry_forward_first_sighting_records_anchor_none():
    train = mk_train("t1", "S2", *S2, next_time=1000.0)
    new_mem = carry_forward_prev([train], {})
    assert train["prev_lat"] is None
    assert new_mem["t1"] == _obs("S2", *S2, 1000.0, anchor=None)


def test_carry_forward_same_stop_no_anchor_does_not_synthesize():
    # First segment after sighting: same stop, but no anchor carried yet.
    train = mk_train("t1", "S2", *S2, next_time=1000.0)
    new_mem = carry_forward_prev([train], {"t1": _obs("S2", *S2, 980.0, anchor=None)})
    assert train["prev_lat"] is None
    assert new_mem["t1"]["anchor"] is None


def test_carry_forward_next_time_none_does_not_synthesize():
    train = mk_train("t1", "S2", *S2, next_time=None)
    new_mem = carry_forward_prev([train], {"t1": _obs("S1", *S1, 940.0)})
    assert train["prev_lat"] is None
    assert new_mem["t1"]["next_time"] is None  # still recorded


def test_carry_forward_anchor_time_none_does_not_synthesize():
    train = mk_train("t1", "S2", *S2, next_time=1000.0)
    carried = _anchor("S1", *S1, None)
    carry_forward_prev([train], {"t1": _obs("S2", *S2, 1000.0, anchor=carried)})
    assert train["prev_lat"] is None


def test_carry_forward_non_monotonic_anchor_does_not_synthesize():
    # Anchor time >= next_time gives no forward bracket.
    equal = mk_train("t1", "S2", *S2, next_time=1000.0)
    carry_forward_prev([equal], {"t1": _obs("S2", *S2, 1000.0, anchor=_anchor("S1", *S1, 1000.0))})
    assert equal["prev_lat"] is None
    later = mk_train("t1", "S2", *S2, next_time=1000.0)
    carry_forward_prev([later], {"t1": _obs("S2", *S2, 1000.0, anchor=_anchor("S1", *S1, 1100.0))})
    assert later["prev_lat"] is None


def test_carry_forward_anchor_on_current_stop_does_not_synthesize():
    # Degenerate stop-regression onto the held anchor: the carried anchor records
    # the very station the train is at now. With every OTHER guard satisfied, the
    # anchor-stop != current-stop guard alone must block a zero-length prev==next
    # bracket. (This is the anchor-station sub-case of the documented backward-slide
    # limitation; here it's correctly refused rather than drawn.)
    train = mk_train("t1", "S1", *S1, next_time=1100.0)
    carry_forward_prev([train], {"t1": _obs("S1", *S1, 1000.0, anchor=_anchor("S1", *S1, 940.0))})
    assert train["prev_lat"] is None


def test_carry_forward_prunes_trips_absent_this_poll():
    train = mk_train("t1", "S2", *S2, next_time=1000.0)
    old = {"t1": _obs("S1", *S1, 940.0), "gone": _obs("X1", 40.6, -73.9, 800.0)}
    new_mem = carry_forward_prev([train], old)
    assert "t1" in new_mem and "gone" not in new_mem


def test_carry_forward_multi_poll_continuous_glide():
    # Poll 1: first sighting at S1, no memory -> prev null.
    p1 = mk_train("t1", "S1", *S1, next_time=900.0)
    m1 = carry_forward_prev([p1], {})
    assert p1["prev_lat"] is None
    # Poll 2: advanced to S2 -> prev = S1, prev_time == poll 1's next_time.
    p2 = mk_train("t1", "S2", *S2, next_time=1000.0)
    m2 = carry_forward_prev([p2], m1)
    assert (p2["prev_lat"], p2["prev_lon"], p2["prev_time"]) == (*S1, 900.0)
    # Poll 3: STILL at S2 (same segment) -> prev STILL S1 (anchor held). This is the fix.
    p3 = mk_train("t1", "S2", *S2, next_time=1005.0)
    m3 = carry_forward_prev([p3], m2)
    assert (p3["prev_lat"], p3["prev_lon"], p3["prev_time"]) == (*S1, 900.0)
    # Poll 4: advanced to S3 -> prev = S2, prev_time == poll 3's next_time.
    p4 = mk_train("t1", "S3", *S3, next_time=1100.0)
    carry_forward_prev([p4], m3)
    assert (p4["prev_lat"], p4["prev_lon"], p4["prev_time"]) == (*S2, 1005.0)
