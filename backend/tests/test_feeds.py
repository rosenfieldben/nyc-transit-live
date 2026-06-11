"""Unit tests for the pure GTFS-RT decoding logic in feeds.py."""

from datetime import datetime
from types import SimpleNamespace

from google.transit import gtfs_realtime_pb2 as pb

from feeds import NYC_TZ, _decode_trains, _stop_time, _trip_start_ts

# Fixed "now": 2026-06-10 12:00:00 New York time.
NOON = datetime(2026, 6, 10, 12, 0, 0, tzinfo=NYC_TZ)
NOW = NOON.timestamp()
TODAY = "20260610"

STOPS = {
    "A01N": {"name": "Alpha", "lat": 40.70, "lon": -74.00},
    "A02N": {"name": "Beta", "lat": 40.71, "lon": -74.01},
    "A03S": {"name": "Gamma", "lat": 40.72, "lon": -74.02},
}


def make_stu(stop_id=None, arrival=None, departure=None):
    stu = pb.TripUpdate.StopTimeUpdate()
    if stop_id is not None:
        stu.stop_id = stop_id
    if arrival is not None:
        stu.arrival.time = int(arrival)
    if departure is not None:
        stu.departure.time = int(departure)
    return stu


def make_feed(*trips):
    """trips: dicts with trip_id, route_id, start_date, stus=[(stop, arr, dep)]."""
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
        for stop_id, arr, dep in t.get("stus", []):
            tu.stop_time_update.append(make_stu(stop_id, arr, dep))
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
    trains = decode(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A01N", NOW - 300, NOW - 240)]}
    )
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
    trains = decode(
        {"trip_id": UNSTARTED, "route_id": "1", "stus": [("A01N", NOW + 660, None)]}
    )
    assert trains == []


def test_trip_within_start_grace_included():
    trains = decode(
        {"trip_id": BARELY_FUTURE, "route_id": "1", "stus": [("A01N", NOW + 90, None)]}
    )
    assert len(trains) == 1


def test_unparseable_trip_id_uses_first_stop_time_cap():
    far = {"trip_id": "WEIRD-ID-1", "route_id": "1", "start_date": "", "stus": [("A01N", NOW + 600, None)]}
    near = {"trip_id": "WEIRD-ID-2", "route_id": "1", "start_date": "", "stus": [("A02N", NOW + 60, None)]}
    trains = decode(far, near)
    assert [t["trip_id"] for t in trains] == ["WEIRD-ID-2"]


def test_southbound_direction_from_stop_suffix():
    trains = decode(
        {"trip_id": STARTED, "route_id": "1", "stus": [("A03S", NOW + 60, None)]}
    )
    assert trains[0]["direction"] == "Southbound"


def test_missing_trip_id_falls_back_to_entity_id():
    trains = decode(
        {"trip_id": "", "route_id": "1", "stus": [("A01N", NOW + 60, None)]}
    )
    assert trains[0]["trip_id"] == "TEST:ent0"


def test_missing_route_id_is_none():
    trains = decode(
        {"trip_id": STARTED, "route_id": "", "stus": [("A01N", NOW + 60, None)]}
    )
    assert trains[0]["route_id"] is None


def test_entity_without_trip_update_skipped():
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    entity = feed.entity.add()
    entity.id = "vehicle-only"
    assert _decode_trains(feed.SerializeToString(), STOPS, "TEST", NOW) == []
