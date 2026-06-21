"""Shape-drift guards for the response models.

The endpoint response_models make a dropped or mistyped field fail loudly
(500 + endpoint test). These tests add the other half: that real decode
output matches the model's field set EXACTLY, so a decode change that adds or
renames a field is caught here rather than silently dropped by serialization.
"""

import json
from pathlib import Path

import feeds
from models import (
    Arrival,
    BusFeed,
    BusIndexStatus,
    RailroadFeed,
    RailroadFeedHealth,
    RailroadTrain,
    StationArrivals,
    StatusResponse,
    SubwayFeed,
    SubwayFeedHealth,
    SubwayStop,
    Train,
    Vehicle,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Representative decode outputs, mirrored from feeds.py / the test_api fixtures.
VEHICLE = {
    "id": "MTA NYCT_1",
    "route_id": "M15",
    "latitude": 40.7,
    "longitude": -74.0,
    "bearing": 90.0,
}


def test_vehicle_model_field_set_is_locked():
    # A rename in the model (or the bus decode) breaks this; combined with the
    # endpoint response_model, a removed field also fails the /api/buses test.
    assert set(Vehicle.model_fields) == set(VEHICLE)
    Vehicle.model_validate(VEHICLE)


def test_train_model_matches_real_decode_output_exactly():
    expected = json.loads((FIXTURES / "subway_1_7_s_expected.json").read_text())
    fields = set(Train.model_fields)
    assert expected["trains"], "golden fixture is empty"
    for train in expected["trains"]:
        assert set(train) == fields  # no added / missing keys vs the model
        Train.model_validate(train)  # and the types validate


def test_feed_envelopes_validate():
    # feed_timestamp is a required field (may be None) alongside fetched_at.
    BusFeed.model_validate({"fetched_at": 1000.0, "feed_timestamp": 995.0, "data": [VEHICLE]})
    BusFeed.model_validate({"fetched_at": None, "feed_timestamp": None, "data": []})
    SubwayFeed.model_validate(
        {
            "fetched_at": 1000.0,
            "feed_timestamp": 996.0,
            "data": [
                {
                    "trip_id": "70000_1..N01R",
                    "route_id": "1",
                    "latitude": 40.7,
                    "longitude": -74.0,
                    "stop_id": "101N",
                    "stop_name": "Alpha",
                    "direction": "Northbound",
                    "prev_lat": 40.69,
                    "prev_lon": -74.01,
                    "prev_time": 999.0,
                    "next_time": 1002.0,
                }
            ],
        }
    )


def test_railroad_train_model_matches_real_decode_output_exactly():
    fields = set(RailroadTrain.model_fields)
    for system in ("lirr", "mnr"):
        expected = json.loads((FIXTURES / f"railroad_{system}_expected.json").read_text())
        assert expected["trains"], "golden fixture is empty"
        for train in expected["trains"]:
            assert set(train) == fields  # no added / missing keys vs the model
            RailroadTrain.model_validate(train)


def test_decoded_railroad_train_keys_cover_model():
    # Tie the model to the live decode path, not just the serialized fixture.
    raw = (FIXTURES / "railroad_mnr.pb").read_bytes()
    trains, _ = feeds._decode_railroad_vehicles(raw, "MNR", 0.0)
    assert trains, "decode produced no trains"
    assert all(set(t) == set(RailroadTrain.model_fields) for t in trains)


def test_railroad_feed_envelope_validates():
    sample = {
        "system": "MNR",
        "trip_id": "1797",
        "route_id": "4",
        "latitude": 41.0,
        "longitude": -73.5,
        "bearing": None,
        "train_num": "1797",
        "direction": None,
        "prev_lat": None,
        "prev_lon": None,
        "prev_time": None,
        "next_time": None,
    }
    RailroadFeed.model_validate({"fetched_at": 1000.0, "feed_timestamp": None, "data": [sample]})
    RailroadFeed.model_validate({"fetched_at": None, "feed_timestamp": None, "data": []})


SUBWAY_STOP = {"id": "A01", "name": "Alpha", "lat": 40.7, "lon": -74.0}
ARRIVAL = {"route_id": "1", "trip_id": "t1", "arrival": 1000.0}


def test_subway_stop_field_set_is_locked():
    assert set(SubwayStop.model_fields) == set(SUBWAY_STOP)
    SubwayStop.model_validate(SUBWAY_STOP)


def test_arrival_field_set_is_locked():
    assert set(Arrival.model_fields) == set(ARRIVAL)
    Arrival.model_validate(ARRIVAL)


def test_station_arrivals_validates_handler_shape():
    StationArrivals.model_validate(
        {
            "fetched_at": 1234.0,
            "station_id": "A01",
            "station_name": "Alpha",
            "directions": {"Northbound": [ARRIVAL], "Southbound": []},
        }
    )


def test_status_model_validates_handler_shape():
    # Mirrors what get_status builds, including a recorded error and null GTFS.
    StatusResponse.model_validate(
        {
            "feeds": {
                "buses": {
                    "fetched_at": 1000.0,
                    "age_s": 5.0,
                    "feed_age_s": 3.0,
                    "last_error": None,
                },
                "subways": {
                    "fetched_at": None,
                    "age_s": None,
                    "feed_age_s": None,
                    "last_error": {"status": 502, "detail": "boom"},
                },
            },
            "bus_route_index": {"status": "ready", "partial": False},
            "static_subway_gtfs": None,
            "subway_feeds": {"total": 8, "ok": 7, "failed": ["BDFM"]},
            "railroad_feeds": {"total": 2, "ok": 1, "failed": ["MNR"]},
        }
    )
    BusIndexStatus.model_validate({"status": "building", "partial": False})
    SubwayFeedHealth.model_validate({"total": 8, "ok": 8, "failed": []})
    RailroadFeedHealth.model_validate({"total": 2, "ok": 2, "failed": []})


def test_decoded_train_keys_cover_model():
    # Decode a fresh train from the golden bytes and confirm its keys are
    # exactly the model's — ties the model to the live code path, not just the
    # serialized fixture.
    raw = (FIXTURES / "subway_1_7_s.pb").read_bytes()
    stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
    expected = json.loads((FIXTURES / "subway_1_7_s_expected.json").read_text())
    trains = feeds._decode_trains(raw, stops, expected["feed_key"], expected["now"])
    assert trains, "decode produced no trains"
    assert all(set(t) == set(Train.model_fields) for t in trains)
