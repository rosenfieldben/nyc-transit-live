"""Golden + unit tests for the railroad (LIRR / MNR) GPS decode.

Like test_feeds_golden.py, the riskiest part is decoding the true shape of the
feed, so these lock _decode_railroad_vehicles against real captured payloads
(the bytes carry no PII) with `now` frozen to each feed's header timestamp.
Synthetic feeds cover the two route_id-join layouts and the position filter.

To regenerate after an INTENTIONAL decode change, from backend/:

    python - <<'PY'
    import json
    from pathlib import Path
    from google.transit import gtfs_realtime_pb2 as pb
    import feeds
    FIX = Path("tests/fixtures")
    for system in ("LIRR", "MNR"):
        key = system.lower()
        raw = (FIX / f"railroad_{key}.pb").read_bytes()
        feed = pb.FeedMessage(); feed.ParseFromString(raw)
        now = float(feed.header.timestamp)
        trains, _ = feeds._decode_railroad_vehicles(raw, system, now)
        (FIX / f"railroad_{key}_expected.json").write_text(
            json.dumps({"now": now, "system": system, "trains": trains}, indent=0))
    PY
"""

import json
from pathlib import Path

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

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
    trains, feed_ts = feeds._decode_railroad_vehicles(raw, expected["system"], expected["now"])
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
        # Phase-1 trains are GPS only: every anchor + direction field is null.
        for field in ("direction", "prev_lat", "prev_lon", "prev_time", "next_time"):
            assert train[field] is None


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
    _, feed_ts, _ = await feeds.fetch_railroad_trains(client)
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
    trains, feed_ts, failed = await feeds.fetch_railroad_trains(client)
    assert failed == ["LIRR"]
    assert trains and all(t["system"] == "MNR" for t in trains)
    assert feed_ts is None


@pytest.mark.anyio
async def test_fetch_dedups_duplicate_trip_ids_on_the_live_path():
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")})
    trains, _, failed = await feeds.fetch_railroad_trains(client)
    assert failed == []
    # The MNR feed repeats trains across separate vehicle entities; the live path
    # collapses them to one marker per trip_id (49 decoded -> 33 unique), which
    # the golden decode (no de-dup) does not.
    mnr = [t for t in trains if t["system"] == "MNR"]
    assert len(mnr) == 33
    assert len({t["trip_id"] for t in mnr}) == 33
    assert len([t for t in trains if t["system"] == "LIRR"]) == 69
    assert len(trains) == 69 + 33


@pytest.mark.anyio
async def test_fetch_skips_a_failed_feed_and_reports_it():
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")}, down=["MNR"])
    trains, _, failed = await feeds.fetch_railroad_trains(client)
    assert failed == ["MNR"]
    assert trains and all(t["system"] == "LIRR" for t in trains)


@pytest.mark.anyio
async def test_fetch_skips_an_undecodable_feed():
    # MNR returns a truncated length-delimited field -> DecodeError, skipped.
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": b"\x0a\xff"})
    trains, _, failed = await feeds.fetch_railroad_trains(client)
    assert failed == ["MNR"]
    assert trains and all(t["system"] == "LIRR" for t in trains)


@pytest.mark.anyio
async def test_fetch_raises_when_all_feeds_fail():
    client = _FakeRailClient({}, down=["LIRR", "MNR"])
    with pytest.raises(RuntimeError, match="All railroad feeds failed"):
        await feeds.fetch_railroad_trains(client)
