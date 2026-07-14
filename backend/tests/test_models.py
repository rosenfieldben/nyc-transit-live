"""Shape-drift guards for the response models.

The endpoint response_models make a dropped or mistyped field fail loudly
(500 + endpoint test). These tests add the other half: that real decode
output matches the model's field set EXACTLY, so a decode change that adds or
renames a field is caught here rather than silently dropped by serialization.
"""

import json
from pathlib import Path

import feeds
import railroad_static
from models import (
    Arrival,
    BusFeed,
    BusIndexStatus,
    PathArrival,
    PathFeed,
    PathStationArrivals,
    PathTrain,
    RailroadArrival,
    RailroadFeed,
    RailroadFeedHealth,
    RailroadRoute,
    RailroadStationArrivals,
    RailroadStop,
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


def test_placed_railroad_train_keys_cover_model():
    # The station-placement path emits the SAME RailroadTrain shape as the GPS
    # path, so both feed the /api/railroads RailroadFeed without a model change.
    raw = (FIXTURES / "railroad_lirr.pb").read_bytes()
    stops = json.loads((FIXTURES / "railroad_lirr_stops.json").read_text())
    placed = feeds._decode_railroad_placements(raw, "LIRR", stops, 0.0)
    assert placed, "placement produced no trains"
    assert all(set(t) == set(RailroadTrain.model_fields) for t in placed)


def test_railroad_feed_envelope_validates():
    sample = {
        "system": "MNR",
        "trip_id": "1797",
        "route_id": "4",
        "latitude": 41.0,
        "longitude": -73.5,
        "bearing": None,
        "train_num": "1797",
        "stop_id": None,
        "stop_name": None,
        "direction": None,
        "prev_lat": None,
        "prev_lon": None,
        "prev_time": None,
        "next_time": None,
    }
    RailroadFeed.model_validate({"fetched_at": 1000.0, "feed_timestamp": None, "data": [sample]})
    RailroadFeed.model_validate({"fetched_at": None, "feed_timestamp": None, "data": []})


def test_railroad_route_model_validates_sample():
    RailroadRoute.model_validate(
        {"system": "MNR", "route": "3", "name": "New Haven", "polylines": [[[41.0, -73.0]]]}
    )
    # name is nullable (a route with no routes.txt entry).
    RailroadRoute.model_validate(
        {"system": "MNR", "route": "3", "name": None, "polylines": [[[41.0, -73.0], [41.1, -73.1]]]}
    )


def test_railroad_route_builder_output_covers_model():
    # The builder emits {route, name, polylines}; the endpoint adds system. Tie the
    # two together so a field added to the builder or the model can't drift apart:
    # each builder entry plus "system" must be exactly the model's field set. Also
    # confirm the name is filled from the routes table (long_name, else short_name).
    shapes = {"a": [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]}
    trips = {"t1": {"route_id": "5", "shape_id": "a"}}
    route_names = {"5": {"long_name": "Montauk Branch", "short_name": None}}
    entries = railroad_static.build_railroad_route_shapes(trips, shapes, route_names)
    assert entries  # guard against a vacuous pass
    for entry in entries:
        assert set(entry) | {"system"} == set(RailroadRoute.model_fields)
    assert entries[0]["name"] == "Montauk Branch"
    # Omitting the routes table leaves name null (geometry-only build).
    assert railroad_static.build_railroad_route_shapes(trips, shapes)[0]["name"] is None


SUBWAY_STOP = {"id": "A01", "name": "Alpha", "lat": 40.7, "lon": -74.0, "routes": ["1", "2"]}
ARRIVAL = {"route_id": "1", "trip_id": "t1", "arrival": 1000.0}
RAILROAD_STOP = {
    "system": "LIRR",
    "id": "12",
    "name": "Jamaica",
    "lat": 40.7,
    "lon": -73.8,
    "routes": ["5"],
}
RAILROAD_ARRIVAL = {"route_id": "5", "trip_id": "t1", "arrival": 1000.0, "train_num": "704"}


def test_subway_stop_field_set_is_locked():
    assert set(SubwayStop.model_fields) == set(SUBWAY_STOP)
    SubwayStop.model_validate(SUBWAY_STOP)


def test_railroad_stop_field_set_is_locked():
    assert set(RailroadStop.model_fields) == set(RAILROAD_STOP)
    RailroadStop.model_validate(RAILROAD_STOP)


def test_arrival_field_set_is_locked():
    assert set(Arrival.model_fields) == set(ARRIVAL)
    Arrival.model_validate(ARRIVAL)


def test_railroad_arrival_field_set_is_locked():
    # A railroad arrival adds train_num over the subway Arrival; lock it so a
    # decode change that drops or renames the number fails in CI.
    assert set(RailroadArrival.model_fields) == set(RAILROAD_ARRIVAL)
    RailroadArrival.model_validate(RAILROAD_ARRIVAL)
    RailroadArrival.model_validate({**RAILROAD_ARRIVAL, "route_id": None, "train_num": None})


def test_station_arrivals_validates_handler_shape():
    StationArrivals.model_validate(
        {
            "fetched_at": 1234.0,
            "station_id": "A01",
            "station_name": "Alpha",
            "directions": {"Northbound": [ARRIVAL], "Southbound": []},
        }
    )


def test_railroad_station_arrivals_validates_handler_shape():
    # LIRR shape (Outbound/Inbound buckets) and the empty-directions case both
    # validate; the bucket keys are whatever the station carries, not fixed.
    RailroadStationArrivals.model_validate(
        {
            "fetched_at": 1234.0,
            "system": "LIRR",
            "stop_id": "12",
            "stop_name": "Jamaica",
            "directions": {"Outbound": [RAILROAD_ARRIVAL], "Inbound": []},
        }
    )
    RailroadStationArrivals.model_validate(
        {
            "fetched_at": None,
            "system": "MNR",
            "stop_id": "1",
            "stop_name": "Grand Central",
            "directions": {},
        }
    )


def test_matched_path_train_keys_cover_model():
    # Tie PathTrain to the live serving path: decode a synthetic bridge-style
    # entity, thread it through the 13d identity matcher (what /api/path
    # actually serves), and the result must emit exactly the model's field
    # set. In particular the bridge's unstable trip hash must NOT survive to
    # the payload, and the minted `id` must.
    from google.transit import gtfs_realtime_pb2 as pb

    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    ent = feed.entity.add()
    ent.id = "e1"
    ent.trip_update.trip.trip_id = "uuid-1"
    ent.trip_update.trip.route_id = "862"
    ent.trip_update.trip.direction_id = 1
    stu = ent.trip_update.stop_time_update.add()
    stu.stop_id = "26733"
    stu.arrival.time = 1500
    stops = {"26733": {"id": "26733", "name": "Newark", "lat": 40.73454, "lon": -74.16375}}
    trains, arrivals, _, _ = feeds._decode_path_feed(feed.SerializeToString(), stops, 1000.0)
    assert trains, "decode produced no trains"
    served, _state = feeds.match_path_identities(feeds.new_path_identity_state("t"), trains, {})
    assert all(set(t) == set(PathTrain.model_fields) for t in served)
    for t in served:
        PathTrain.model_validate(t)
        assert "uuid-1" not in str(t)  # the bridge hash never reaches the payload
    for buckets in arrivals.values():
        for rows in buckets.values():
            for row in rows:
                assert set(row) == set(PathArrival.model_fields)
                PathArrival.model_validate(row)


def test_path_feed_and_arrivals_envelopes_validate():
    train = {
        "id": "t-1",
        "route_id": "862",
        "latitude": 40.73454,
        "longitude": -74.16375,
        "stop_id": "26733",
        "stop_name": "Newark",
        "direction": "To New Jersey",
        "prev_lat": None,
        "prev_lon": None,
        "prev_time": None,
        "next_time": 1500.0,
    }
    # The PATH envelope key is `trains` (not the MTA feeds' `data`).
    PathFeed.model_validate({"fetched_at": 1000.0, "feed_timestamp": 996.0, "trains": [train]})
    PathFeed.model_validate({"fetched_at": None, "feed_timestamp": None, "trains": []})
    PathStationArrivals.model_validate(
        {
            "fetched_at": 1234.0,
            "stop_id": "26733",
            "stop_name": "Newark",
            "directions": {
                "To New York": [{"route_id": "862", "trip_id": "uuid-1", "arrival": 1500.0}]
            },
        }
    )
    PathStationArrivals.model_validate(
        {"fetched_at": None, "stop_id": "26734", "stop_name": None, "directions": {}}
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
            "subway_static": "ready",
            "railroad_static": "loading",
            "path_static": "failed",
            "subway_feeds": {"total": 8, "ok": 7, "failed": ["BDFM"]},
            "railroad_feeds": {"total": 2, "ok": 1, "failed": ["MNR"]},
            "path_feeds": {"total": 1, "ok": 1, "failed": [], "unresolved": 0},
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
