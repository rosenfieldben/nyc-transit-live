"""Golden + unit tests for the railroad (LIRR / MNR) decode: GPS + placement.

Like test_feeds_golden.py, the riskiest part is decoding the true shape of the
feed, so these lock _decode_railroad_vehicles (GPS) and _decode_railroad_placements
(station placement of the position-less trains) against real captured payloads
(the bytes carry no PII) with `now` frozen to each feed's header timestamp.
Synthetic feeds cover the route_id-join layouts, the position filter, and the
placement edges. The placement golden also uses the committed per-system stops
(railroad_{lirr,mnr}_stops.json), so no test touches the network.

To regenerate the GPS golden after an INTENTIONAL decode change, from backend/:

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
    # The bucket asymmetry at scale: LIRR carries direction_id so it can use all
    # three buckets; MNR omits it, so every MNR arrival is in "Trains".
    valid_buckets = {"Outbound", "Inbound", "Trains"} if system == "LIRR" else {"Trains"}
    for stop_id, buckets in expected["arrivals"].items():
        assert stop_id in stops  # resolvable station
        for bucket, arrs in buckets.items():
            assert bucket in valid_buckets
            assert arrs  # the decode never stores an empty bucket
            assert len(arrs) <= feeds.ARRIVALS_PER_DIRECTION  # capped
            times = [a["arrival"] for a in arrs]
            assert times == sorted(times)  # soonest first
            for a in arrs:
                assert set(a) == {"route_id", "trip_id", "arrival", "train_num"}
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
    _, _, feed_ts, _ = await feeds.fetch_railroad_trains(client, {})
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
    trains, _, feed_ts, failed = await feeds.fetch_railroad_trains(client, {})
    assert failed == ["LIRR"]
    assert trains and all(t["system"] == "MNR" for t in trains)
    assert feed_ts is None


@pytest.mark.anyio
async def test_fetch_dedups_duplicate_trip_ids_on_the_live_path():
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": _raw("MNR")})
    trains, _, _, failed = await feeds.fetch_railroad_trains(client, {})
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
    trains, _, _, failed = await feeds.fetch_railroad_trains(client, {})
    assert failed == ["MNR"]
    assert trains and all(t["system"] == "LIRR" for t in trains)


@pytest.mark.anyio
async def test_fetch_skips_an_undecodable_feed():
    # MNR returns a truncated length-delimited field -> DecodeError, skipped.
    client = _FakeRailClient({"LIRR": _raw("LIRR"), "MNR": b"\x0a\xff"})
    trains, _, _, failed = await feeds.fetch_railroad_trains(client, {})
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
    trains, _, _, failed = await feeds.fetch_railroad_trains(client, stops)
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
    trains, _, _, failed = await feeds.fetch_railroad_trains(client, {})
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
