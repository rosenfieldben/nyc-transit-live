"""Shape-drift guards for the response models.

The endpoint response_models make a dropped or mistyped field fail loudly
(500 + endpoint test). These tests add the other half: that real decode
output matches the model's field set EXACTLY, so a decode change that adds or
renames a field is caught here rather than silently dropped by serialization.
"""

import json
from pathlib import Path
from typing import get_args

import pytest

import feeds
import models as models_module
import railroad_static
from models import (
    Arrival,
    BusFeed,
    BusIndexStatus,
    FerryStationArrivals,
    NjtStationArrivals,
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

# THE CONTRACT PAIR, as a row that never went through a decoder reports it.
#
# Every field-set lock below is a strict equality again. 6.1 landed in three steps
# and the middle two left the model wider than the thing each assertion compared it
# against, which two named constants tolerated for exactly as long as it was true;
# both are gone, deleted by the commits that closed them, and that is the whole of
# the evidence that the decoders and the goldens caught up.
#
# The defaults below are what a row seeded straight into a cache entry carries,
# which is the honest thing for a row no decoder produced to say about itself.
_CONTRACT_PAIR = {"observed_at": None, "provenance": "unknown"}

# Representative decode outputs, mirrored from feeds.py / the test_api fixtures.
VEHICLE = {
    "id": "MTA NYCT_1",
    "route_id": "M15",
    "latitude": 40.7,
    "longitude": -74.0,
    "bearing": 90.0,
    **_CONTRACT_PAIR,
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
        assert set(train) == fields  # no added / missing keys
        Train.model_validate(train)  # and the types validate


def test_feed_envelopes_validate():
    # feed_timestamp is a required field (may be None) alongside fetched_at, and
    # served_at (R1) is required and non-null (stamped at every response build).
    BusFeed.model_validate(
        {"fetched_at": 1000.0, "feed_timestamp": 995.0, "served_at": 1001.0, "data": [VEHICLE]}
    )
    BusFeed.model_validate(
        {"fetched_at": None, "feed_timestamp": None, "served_at": 1001.0, "data": []}
    )
    SubwayFeed.model_validate(
        {
            "fetched_at": 1000.0,
            "feed_timestamp": 996.0,
            "served_at": 1001.0,
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
            assert set(train) == fields  # no added / missing keys
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
    RailroadFeed.model_validate(
        {"fetched_at": 1000.0, "feed_timestamp": None, "served_at": 1001.0, "data": [sample]}
    )
    RailroadFeed.model_validate(
        {"fetched_at": None, "feed_timestamp": None, "served_at": 1001.0, "data": []}
    )


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
ARRIVAL = {"route_id": "1", "trip_id": "t1", "arrival": 1000.0, **_CONTRACT_PAIR}
RAILROAD_STOP = {
    "system": "LIRR",
    "id": "12",
    "name": "Jamaica",
    "lat": 40.7,
    "lon": -73.8,
    "routes": ["5"],
}
RAILROAD_ARRIVAL = {
    "route_id": "5",
    "trip_id": "t1",
    "arrival": 1000.0,
    "train_num": "704",
    **_CONTRACT_PAIR,
}


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


# THE FIVE ARRIVALS ENVELOPES HAD NO FIELD-SET LOCK, WHICH IS PROBABLY HOW THEY CAME
# TO BE MISSING served_at. Every vehicle envelope in this module is pinned by one of
# the locks above; these five were only ever `model_validate`d against a handler-shaped
# payload, and pydantic ignores fields it is not given, so an added optional field
# passed silently and a MISSING one was invisible until a rider saw it. 6.1 gives each
# of them the same guard the row models have had since the beginning.
#
# The literals are the SERVED shape, not the handler's dict: the response model fills
# the contract defaults on the way out, and what a client receives is what a lock
# should describe.
_ARRIVALS_CLOCKS = {"fetched_at", "feed_timestamp", "served_at", "systems"}
ARRIVALS_ENVELOPES = {
    StationArrivals: {"station_id", "station_name", "directions"} | _ARRIVALS_CLOCKS,
    RailroadStationArrivals: {"system", "stop_id", "stop_name", "directions"} | _ARRIVALS_CLOCKS,
    PathStationArrivals: {"stop_id", "stop_name", "directions"} | _ARRIVALS_CLOCKS,
    NjtStationArrivals: {"stop_id", "stop_name", "arrivals"} | _ARRIVALS_CLOCKS,
    FerryStationArrivals: {"stop_id", "stop_name", "routes"} | _ARRIVALS_CLOCKS,
}


@pytest.mark.parametrize(
    "model,expected",
    list(ARRIVALS_ENVELOPES.items()),
    ids=lambda v: getattr(v, "__name__", ""),
)
def test_arrivals_envelope_field_set_is_locked(model, expected):
    assert set(model.model_fields) == expected


def test_every_arrivals_envelope_is_locked():
    """The lock above is per-model, so a SIXTH arrivals envelope added later would
    simply not be covered by it. This asserts the roster itself: every model in this
    module whose name ends in StationArrivals is in the table above."""
    import models as models_module

    declared = {
        getattr(models_module, name)
        for name in dir(models_module)
        if name.endswith("StationArrivals")
    }
    assert declared == set(ARRIVALS_ENVELOPES), "an arrivals envelope is missing its lock"


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
    PathFeed.model_validate(
        {"fetched_at": 1000.0, "feed_timestamp": 996.0, "served_at": 1001.0, "trains": [train]}
    )
    PathFeed.model_validate(
        {"fetched_at": None, "feed_timestamp": None, "served_at": 1001.0, "trains": []}
    )
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
            "served_at": 1000.0,  # R1: top-level snapshot build time
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
    # exactly the model's. This ties the model to the live code path, not just
    # the serialized fixture.
    raw = (FIXTURES / "subway_1_7_s.pb").read_bytes()
    stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
    expected = json.loads((FIXTURES / "subway_1_7_s_expected.json").read_text())
    trains = feeds._decode_trains(raw, stops, expected["feed_key"], expected["now"])
    assert trains, "decode produced no trains"
    assert all(set(t) == set(Train.model_fields) for t in trains)


# ---------------------------------------------------------------------------
# THE OBSERVATION CLOCKS, PER DECODER (contract 6.1, section 3.3)
#
# The per-provider age policy in the design is DATA: one row per system and
# observation kind, saying which clock that row's rule reads and whether it is
# age-gated. These tests are that table, asserted against the real decoders over the
# committed captures. A policy that lives only in a document drifts from the code it
# describes; a policy asserted here cannot.
# ---------------------------------------------------------------------------

# Every value models.Provenance admits. Derived from the type rather than retyped, so
# adding a sixth value to the enumeration cannot leave this list behind.
PROVENANCE_VALUES = set(get_args(models_module.Provenance))


def _rows(value):
    """Every dict at the leaves of a decoder's output, whatever its nesting."""
    if isinstance(value, list):
        return [row for item in value for row in _rows(item)]
    if isinstance(value, dict):
        if "provenance" in value:
            return [value]
        return [row for item in value.values() for row in _rows(item)]
    return []


def _bus_rows(monkeypatch):
    """The bus decode over the committed OneBusAway capture (section 6.0's probe).

    fetch_vehicle_positions is an async fetch rather than a pure decode, so the client
    is stubbed exactly as backend/tests/test_feeds.py stubs it. No network: the bytes
    are the committed fixture, and the key is a fake so this can never reach the real
    endpoint even if the stub were removed.
    """
    import asyncio

    monkeypatch.setenv("BUS_TIME_API_KEY", "test-key")

    class _Resp:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, content):
            self._content = content

        async def get(self, url, params=None):
            return _Resp(self._content)

    raw = (FIXTURES / "bus_vehicle_positions.pb").read_bytes()
    vehicles, _ts = asyncio.run(feeds.fetch_vehicle_positions(_Client(raw)))
    assert vehicles, "the committed bus capture decoded to nothing"
    return vehicles


def test_bus_rows_carry_the_vehicles_own_clock(monkeypatch):
    # 3.3, buses: vehicle.timestamp, age-gated, written from section 6.0's probe.
    # 2136 of 2136 vehicles on the committed capture date themselves.
    rows = _bus_rows(monkeypatch)
    assert len(rows) == 2136
    assert all(r["observed_at"] is not None for r in rows)
    assert all(r["provenance"] == "reported" for r in rows)
    assert len({r["observed_at"] for r in rows}) == 47  # per observation, not per message


def _njt_decode(now):
    """The NJT decode the golden test drives, built from the committed GTFS members."""
    import io
    import zipfile

    import njt_static
    from feeds import njt as njt_feed

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted((FIXTURES / "njt_gtfs").iterdir()):
            if path.suffix == ".txt":
                zf.writestr(path.name, path.read_text(encoding="utf-8"))
    parsed = njt_static._parse_zip(buffer)
    return njt_feed.decode_njt_trip_updates(
        (FIXTURES / "njt_tu.pb").read_bytes(),
        parsed["stops"],
        njt_static.build_njt_trip_index(parsed["trips"]),
        now,
    )


def _lirr():
    raw = (FIXTURES / "railroad_lirr.pb").read_bytes()
    stops = json.loads((FIXTURES / "railroad_lirr_stops.json").read_text())
    header = 1782006915.0
    gps, _ = feeds._decode_railroad_vehicles(raw, "LIRR", header)
    placed, arrivals = feeds._decode_railroad_feed(raw, "LIRR", stops, header)
    return header, gps, placed, arrivals


def _mnr():
    raw = (FIXTURES / "railroad_mnr.pb").read_bytes()
    stops = json.loads((FIXTURES / "railroad_mnr_stops.json").read_text())
    header = 1782006692.0
    gps, _ = feeds._decode_railroad_vehicles(raw, "MNR", header)
    placed, arrivals = feeds._decode_railroad_feed(raw, "MNR", stops, header)
    return header, gps, placed, arrivals


def test_lirr_positions_carry_the_vehicles_own_clock():
    # 3.3, LIRR GPS position: vehicle.timestamp, age-gated. None of the 69 positioned
    # vehicles on this capture stamps the header, which is what makes it an
    # INDEPENDENT clock rather than a restatement of one.
    header, gps, _placed, _arrivals = _lirr()
    assert gps, "decode produced no GPS trains"
    assert all(t["observed_at"] is not None for t in gps)
    assert all(t["provenance"] == "reported" for t in gps)
    ages = sorted(header - t["observed_at"] for t in gps)
    assert ages[0] == 4.0 and ages[-1] == 53676.0  # the design's freshest and oldest
    assert sum(1 for a in ages if a > 90) == 41  # 42 less F02's canceled trip
    assert not any(t["observed_at"] == header for t in gps)


def test_metro_north_positions_report_no_observation_clock():
    # 3.3, Metro-North: no clock, NOT age-gated. Its stamp exists on all 49 positioned
    # vehicles and is the header on all 49, so reading it would hand the fleet a
    # number that looks like an observation time and restates a lagging header.
    _header, gps, placed, arrivals = _mnr()
    assert gps, "decode produced no GPS trains"
    assert all(t["observed_at"] is None for t in gps)
    assert all(r["observed_at"] is None for r in _rows(placed) + _rows(arrivals))


def test_lirr_predictions_and_placements_carry_the_prediction_clock():
    # 3.3, LIRR prediction: trip_update.timestamp, age-gated. A placed train is as
    # current as the prediction that placed it, not as the header.
    header, _gps, placed, arrivals = _lirr()
    assert placed and _rows(arrivals)
    assert all(t["observed_at"] is not None for t in placed)
    assert all(t["provenance"] == "placed" for t in placed)
    assert all(r["observed_at"] is not None for r in _rows(arrivals))
    # Not one flat value: these rows are dated per trip, not per message.
    assert len({t["observed_at"] for t in placed}) > 1
    assert any(t["observed_at"] != header for t in placed)


def test_subway_positions_take_the_vehicle_clock_where_one_joins():
    # 3.3, subway: two rows, both age-gated. 98 VehiclePositions join by trip_id and
    # carry a real per-observation clock; the trips with none fall back to the group
    # header, which is the same clock their predictions use.
    header = 1781380197.0
    stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
    trains, arrivals, feed_ts = feeds._decode_feed(
        (FIXTURES / "subway_1_7_s.pb").read_bytes(), stops, "1-7+S", header
    )
    assert feed_ts == header
    assert all(t["observed_at"] is not None for t in trains)
    assert all(t["provenance"] == "placed" for t in trains)  # no subway coordinate ships
    joined = [t for t in trains if t["observed_at"] != header]
    assert len(joined) == 84 and len(trains) == 95
    # MEASURED WHILE REGENERATING THE GOLDENS, AND WORTH PINNING BECAUSE IT SIZES
    # F01's subway half. The FEED carries 16 observations older than 90s, but 14 of
    # them belong to trips the placement pass never draws (not yet started, or no
    # resolvable upcoming stop), so only TWO stale observations reach a served train.
    # Anyone sizing the age gate from the feed's 16 would overestimate by eight.
    assert sum(1 for t in trains if header - t["observed_at"] > 90) == 2
    # Every prediction takes the group header: no subway trip_update dates itself.
    rows = _rows(arrivals)
    assert rows and all(r["observed_at"] == header for r in rows)


def test_njt_positions_are_placed_or_estimated_on_the_header():
    # 3.3, NJ Transit: the header for both rows, age-gated. provenance splits on how
    # the position was derived, which is a different question from `status`.
    expected = json.loads((FIXTURES / "njt_tu_expected.json").read_text())
    trains, arrivals, feed_ts, _w = _njt_decode(expected["now"])
    assert trains and feed_ts == expected["feed_timestamp"]
    assert all(t["observed_at"] == feed_ts for t in trains)
    assert {t["provenance"] for t in trains} <= {"placed", "estimated"}
    # The split follows the motion state, and both halves are present on this capture.
    for train in trains:
        expect = "estimated" if train["status"] == "in-transit" else "placed"
        assert train["provenance"] == expect, train["status"]
    assert {t["provenance"] for t in trains} == {"placed", "estimated"}
    assert all(r["observed_at"] == feed_ts for r in _rows(arrivals))


def test_path_rows_take_the_entity_clock_not_the_bridge_write_time():
    # 3.3, PATH: trip_update.timestamp per entity, age-gated. The envelope's clock is
    # the bridge's WRITE time and advances on every regeneration, so a row aged
    # against it could never be stale; these are aged against the entity.
    stops = json.loads((FIXTURES / "path_stops.json").read_text())
    trains, arrivals, feed_ts, _u = feeds._decode_path_feed(
        (FIXTURES / "path_rt_gen_a.pb").read_bytes(), stops, 1783297522.0
    )
    assert trains
    assert all(t["observed_at"] is not None for t in trains)
    assert all(t["provenance"] == "placed" for t in trains)  # the bridge sends no position
    assert len({t["observed_at"] for t in trains}) > 1  # per entity, not per message
    assert all(t["observed_at"] < feed_ts for t in trains)
    assert all(r["observed_at"] is not None for r in _rows(arrivals))


def test_ferry_boat_serves_both_clock_keys_with_one_value():
    # Q1: observed_at is the contract's name for what has shipped as updated_at since
    # 14b. Both keys ride for one release and must never diverge.
    static = json.loads((FIXTURES / "ferry_rt_static.json").read_text())
    boats, feed_ts, _d, _m = feeds._decode_ferry_vehicles(
        (FIXTURES / "ferry_vp_a.pb").read_bytes(), static["trips"], static["routes"], 1783812024.0
    )
    assert boats
    assert all(b["updated_at"] == b["observed_at"] for b in boats)
    assert all(b["observed_at"] is not None for b in boats)
    assert all(b["provenance"] == "reported" for b in boats)
    assert all(b["observed_at"] <= feed_ts for b in boats)


def test_ferry_dock_rows_take_the_tripupdates_clock_not_the_boat_clock():
    # 3.3, ferry dock arrival: the TripUpdates header. The audit's F03 remedy names
    # this one: the boats and the docks are two feeds with two clocks.
    #
    # THE COMMITTED CAPTURES CANNOT TELL THE TWO APART, which is why this test builds
    # a feed. On ferry_tu_a.pb the TripUpdates header, the VehiclePositions header and
    # the `now` argument are all 1783812024, so an implementation reading any of the
    # three satisfies an assertion against that number and the test's own name goes
    # unchecked. Moving the TripUpdates header alone is the only way to ask the
    # question the name asks.
    from google.transit import gtfs_realtime_pb2 as pb

    static = json.loads((FIXTURES / "ferry_rt_static.json").read_text())
    boat_clock = 1783812024.0
    dock_clock = boat_clock - 137.0  # a number no other source in this test carries

    feed = pb.FeedMessage()
    feed.ParseFromString((FIXTURES / "ferry_tu_a.pb").read_bytes())
    feed.header.timestamp = int(dock_clock)
    arrivals, _d, _m = feeds._decode_ferry_arrivals(
        feed.SerializeToString(), static["trips"], static["routes"], boat_clock
    )
    rows = _rows(arrivals)
    assert rows
    assert all(r["observed_at"] == dock_clock for r in rows), "dock rows took the wrong clock"
    assert not any(r["observed_at"] == boat_clock for r in rows)
    assert all(r["provenance"] == "reported" for r in rows)


def test_path_rows_disagree_and_the_oldest_wins():
    # The "worst of the parts" rule in cache._oldest_row_observed_at, pinned where it
    # can actually fail: PATH rows at one station really do carry different clocks, so
    # min and max are different answers and a swap is catchable.
    import cache

    stops = json.loads((FIXTURES / "path_stops.json").read_text())
    _t, arrivals, _fts, _u = feeds._decode_path_feed(
        (FIXTURES / "path_rt_gen_a.pb").read_bytes(), stops, 1783297522.0
    )
    clocks = sorted({r["observed_at"] for r in _rows(arrivals)})
    assert len(clocks) > 1, "the capture must carry disagreeing clocks for this to mean anything"
    assert cache._oldest_row_observed_at(arrivals) == clocks[0]
    assert cache._oldest_row_observed_at(arrivals) != clocks[-1]


def test_an_undated_row_forces_a_null_board_clock():
    # The rule the docstring states and the first implementation could not reach: a row
    # that LACKS observed_at must make the whole board undatable rather than letting its
    # neighbours date it. A row is told from a container structurally, not by the key.
    import cache

    dated = {"route_id": "862", "arrival": 1500.0, "observed_at": 1000.0}
    undated = {"route_id": "862", "arrival": 1500.0}
    assert cache._oldest_row_observed_at({"To New Jersey": [dated]}) == 1000.0
    assert cache._oldest_row_observed_at({"To New Jersey": [dated, undated]}) is None
    assert cache._oldest_row_observed_at({"To New Jersey": [undated]}) is None
    # An empty board is "nothing here to date", which the payload distinguishes from
    # "cannot be dated" by the rows beside it.
    assert cache._oldest_row_observed_at({}) is None


def test_no_decoder_emits_a_provenance_outside_the_closed_set(monkeypatch):
    """THE GUARD MYPY CANNOT GIVE US.

    Every decoder returns list[dict] with untyped values, so `"provenance": "palced"`
    type-checks cleanly and surfaces only as a pydantic ValidationError at the
    response boundary, which is a 500 in front of a rider rather than a red test. This
    walks every row every decoder produces over the committed captures and asserts the
    value is one the enumeration admits.
    """
    seen: set[str] = set()
    for _header, gps, placed, arrivals in (_lirr(), _mnr()):
        seen |= {r["provenance"] for r in _rows(gps) + _rows(placed) + _rows(arrivals)}

    stops = json.loads((FIXTURES / "subway_1_7_s_stops.json").read_text())
    trains, sub_arrivals, _ts = feeds._decode_feed(
        (FIXTURES / "subway_1_7_s.pb").read_bytes(), stops, "1-7+S", 1781380197.0
    )
    seen |= {r["provenance"] for r in _rows(trains) + _rows(sub_arrivals)}

    path_stops = json.loads((FIXTURES / "path_stops.json").read_text())
    p_trains, p_arrivals, _fts, _u = feeds._decode_path_feed(
        (FIXTURES / "path_rt_gen_a.pb").read_bytes(), path_stops, 1783297522.0
    )
    seen |= {r["provenance"] for r in _rows(p_trains) + _rows(p_arrivals)}

    static = json.loads((FIXTURES / "ferry_rt_static.json").read_text())
    boats, _fts2, _d, _m = feeds._decode_ferry_vehicles(
        (FIXTURES / "ferry_vp_a.pb").read_bytes(), static["trips"], static["routes"], 1783812024.0
    )
    f_arrivals, _d2, _m2 = feeds._decode_ferry_arrivals(
        (FIXTURES / "ferry_tu_a.pb").read_bytes(), static["trips"], static["routes"], 1783812024.0
    )
    seen |= {r["provenance"] for r in _rows(boats) + _rows(f_arrivals)}

    n_trains, n_arrivals, _nts, _nw = _njt_decode(
        json.loads((FIXTURES / "njt_tu_expected.json").read_text())["now"]
    )
    seen |= {r["provenance"] for r in _rows(n_trains) + _rows(n_arrivals)}

    alerts, _sup, _ = feeds._decode_alerts((FIXTURES / "alerts_mnr.pb").read_bytes(), "MNR", 0.0)
    seen |= {r["provenance"] for r in _rows(alerts)}

    # BUSES ARE HERE BECAUSE A MUTATION SURVIVED WITHOUT THEM. The first version of
    # this test walked six decoders and not the seventh, so changing the bus decoder's
    # provenance to "live-gps" (the exact value Q8 rejected) left it green. The bus
    # decode is reached through an async fetch rather than a pure function, which is
    # why it was the one left out and is no reason to leave it out.
    seen |= {r["provenance"] for r in _rows(_bus_rows(monkeypatch))}

    assert seen, "no decoder produced a row"
    assert seen <= PROVENANCE_VALUES, sorted(seen - PROVENANCE_VALUES)
    # And the values actually in use are the four a decoder can emit: `retained` is
    # stamped by the retention merge, `unknown` only by the model default.
    assert seen == {"reported", "placed", "estimated"}
