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
    BusFeed,
    BusIndexStatus,
    StatusResponse,
    SubwayFeed,
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
    BusFeed.model_validate({"fetched_at": 1000.0, "data": [VEHICLE]})
    BusFeed.model_validate({"fetched_at": None, "data": []})
    SubwayFeed.model_validate(
        {
            "fetched_at": 1000.0,
            "data": [
                {
                    "trip_id": "70000_1..N01R",
                    "route_id": "1",
                    "latitude": 40.7,
                    "longitude": -74.0,
                    "stop_id": "101N",
                    "stop_name": "Alpha",
                    "direction": "Northbound",
                }
            ],
        }
    )


def test_status_model_validates_handler_shape():
    # Mirrors what get_status builds, including a recorded error and null GTFS.
    StatusResponse.model_validate(
        {
            "feeds": {
                "buses": {"fetched_at": 1000.0, "age_s": 5.0, "last_error": None},
                "subways": {
                    "fetched_at": None,
                    "age_s": None,
                    "last_error": {"status": 502, "detail": "boom"},
                },
            },
            "bus_route_index": {"status": "ready", "partial": False},
            "static_subway_gtfs": None,
        }
    )
    BusIndexStatus.model_validate({"status": "building", "partial": False})


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
