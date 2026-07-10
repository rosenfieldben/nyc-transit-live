"""Hermetic tests for the live contract monitor (backend/scripts/contract_monitor.py).

No test here touches the network: the fetcher, clock, and environment the
monitor depends on are all injected. Each check function is exercised two ways,
matching the house discipline:

  - against the SAME committed goldens the decoders are pinned to (subway,
    railroad, PATH, alerts realtime; PATH and ferry static), so a check that
    passes here is decoding the exact bytes production decodes;
  - against synthetic degraded inputs (a feed that will not decode, a missing
    zip member, a stale header, a failed route join, empty-at-night vs
    empty-at-noon for the ferry), so every band and threshold is asserted at
    its edge.

The ferry service-hours boundary is tested at both edges with an injected clock,
since that boundary is what decides whether an empty ferry feed is a fault or
the normal closed state.

The monitor lives under scripts/ (not an importable package), so it is loaded
from its file path, the same way it would run.
"""

import importlib.util
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as pb

import feeds

_CM_PATH = Path(__file__).resolve().parent.parent / "scripts" / "contract_monitor.py"
_spec = importlib.util.spec_from_file_location("contract_monitor", _CM_PATH)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)

FIX = Path(__file__).parent / "fixtures"

# A no-op sleep: the retry delay is real time we never want to spend in tests.
NO_SLEEP = lambda _s: None  # noqa: E731

# Capture-time header timestamps of the realtime goldens (probed once): a
# healthy freshness check uses `now` just after these so the fixed, months-old
# fixtures still read as fresh.
SUBWAY_GOLDEN_TS = 1781380197.0
LIRR_GOLDEN_TS = 1782006915.0
PATH_GOLDEN_TS = 1783297522.0


# ---------------------------------------------------------------------------
# Fakes and builders
# ---------------------------------------------------------------------------


class FakeFetcher:
    """Injected fetcher. `mapping` is url -> response, where a response is:
    bytes (served as HTTP 200), an int (served as that status with an empty
    body), a BaseException (raised, simulating a transport error), or a list of
    any of those consumed one per call (to script a retry sequence). Every call
    is recorded so tests can assert call counts and that a secret rode in params,
    not in the url."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, headers=None, params=None):
        self.calls.append((url, headers, params))
        if url not in self.mapping:
            raise AssertionError(f"unexpected fetch of {url}")
        value = self.mapping[url]
        if isinstance(value, list):
            value = value.pop(0)
        return self._materialize(value)

    @staticmethod
    def _materialize(value):
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, int):
            return cm.FetchResult(value, b"")
        if isinstance(value, (bytes, bytearray)):
            return cm.FetchResult(200, bytes(value))
        raise AssertionError(f"bad fake response: {value!r}")


def _rt_feed(entities=(), header_ts=None):
    """Serialize a GTFS-Realtime FeedMessage. `entities` is a list of callables
    that each populate one feed.entity."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    if header_ts is not None:
        feed.header.timestamp = int(header_ts)
    for populate in entities:
        populate(feed.entity.add())
    return feed.SerializeToString()


def _trip_update(entity_id, trip_id, stops=()):
    def populate(ent):
        ent.id = entity_id
        ent.trip_update.trip.trip_id = trip_id
        for stop_id, when in stops:
            stu = ent.trip_update.stop_time_update.add()
            stu.stop_id = stop_id
            if when is not None:
                stu.arrival.time = int(when)

    return populate


def _vehicle(entity_id, lat=40.7, lon=-74.0):
    def populate(ent):
        ent.id = entity_id
        ent.vehicle.position.latitude = lat
        ent.vehicle.position.longitude = lon

    return populate


def _zip_bytes(members):
    """A zip archive from {member_name: bytes}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _fixture_txt_members(dirname):
    """Every .txt file under a committed GTFS fixture dir, as {name: bytes}."""
    return {p.name: p.read_bytes() for p in (FIX / dirname).glob("*.txt")}


def _subway_stops_csv(n):
    """A minimal subway stops.txt with n coordinate-bearing rows."""
    rows = ["stop_id,stop_name,stop_lat,stop_lon,location_type"]
    for i in range(n):
        rows.append(f"S{i},Station {i},40.7{i:03d},-73.9{i:03d},0")
    return ("\n".join(rows) + "\n").encode()


def _railroad_stops_csv(n):
    rows = ["stop_id,stop_name,stop_lat,stop_lon"]
    for i in range(n):
        rows.append(f"R{i},Halt {i},40.7{i:03d},-73.8{i:03d}")
    return ("\n".join(rows) + "\n").encode()


def _yyyymmdd(ts, tz):
    return datetime.fromtimestamp(ts, tz).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# _fetch_retrying: one retry, then give up
# ---------------------------------------------------------------------------


def test_fetch_retrying_retries_once_then_succeeds():
    fetch = FakeFetcher({"u": [500, b"ok"]})
    res, detail = cm._fetch_retrying(fetch, "u", NO_SLEEP)
    assert res is not None and res.content == b"ok"
    assert detail == ""
    assert len(fetch.calls) == 2  # one miss, one retry that succeeded


def test_fetch_retrying_gives_up_after_two_misses():
    fetch = FakeFetcher({"u": [500, 503]})
    res, detail = cm._fetch_retrying(fetch, "u", NO_SLEEP)
    assert res is None
    assert "HTTP 503" in detail
    assert len(fetch.calls) == 2  # exactly two attempts, never a third


def test_fetch_retrying_sanitizes_transport_error_urls():
    fetch = FakeFetcher({"u": RuntimeError("boom https://secret.example/feed?key=abc")})
    res, detail = cm._fetch_retrying(fetch, "u", NO_SLEEP)
    assert res is None
    assert "secret.example" not in detail and "abc" not in detail
    assert "<feed url>" in detail


# ---------------------------------------------------------------------------
# Subway realtime
# ---------------------------------------------------------------------------


def test_subway_realtime_healthy_golden_passes():
    raw = (FIX / "subway_1_7_s.pb").read_bytes()
    stops = json.loads((FIX / "subway_1_7_s_stops.json").read_text())
    fetch = FakeFetcher({"u": raw})
    result = cm.check_subway_realtime(
        fetch, NO_SLEEP, SUBWAY_GOLDEN_TS + 30, stops, feed_urls={"1-7+S": "u"}
    )
    assert result.status == cm.PASS


def test_subway_realtime_feed_down_is_fail():
    fetch = FakeFetcher({"u": 500})
    result = cm.check_subway_realtime(
        fetch, NO_SLEEP, SUBWAY_GOLDEN_TS + 30, {}, feed_urls={"1-7+S": "u"}
    )
    assert result.status == cm.FAIL
    assert "down" in result.detail


def test_subway_realtime_undecodable_is_fail():
    fetch = FakeFetcher({"u": b"not a protobuf"})
    result = cm.check_subway_realtime(
        fetch, NO_SLEEP, SUBWAY_GOLDEN_TS + 30, {}, feed_urls={"1-7+S": "u"}
    )
    assert result.status == cm.FAIL


def _obs(key, count, header_ts, ok=True):
    return cm._FeedObs(key, ok, "", header_ts, count)


def test_evaluate_subway_zero_on_all_is_fail():
    now = 1000.0
    obs = [_obs("a", 0, now), _obs("b", 0, now)]
    assert cm._evaluate_subway(obs, now, cm.REALTIME_STALE_S).status == cm.FAIL


def test_evaluate_subway_zero_on_one_is_warn():
    now = 1000.0
    obs = [_obs("a", 5, now), _obs("b", 0, now)]
    result = cm._evaluate_subway(obs, now, cm.REALTIME_STALE_S)
    assert result.status == cm.WARN
    assert "no entities: b" in result.detail


def test_evaluate_subway_all_stale_is_fail():
    now = 10_000.0
    old = now - cm.REALTIME_STALE_S - 1
    obs = [_obs("a", 5, old), _obs("b", 5, old)]
    assert cm._evaluate_subway(obs, now, cm.REALTIME_STALE_S).status == cm.FAIL


def test_evaluate_subway_one_stale_is_warn():
    now = 10_000.0
    old = now - cm.REALTIME_STALE_S - 1
    obs = [_obs("a", 5, now), _obs("b", 5, old)]
    result = cm._evaluate_subway(obs, now, cm.REALTIME_STALE_S)
    assert result.status == cm.WARN
    assert "stale header: b" in result.detail


def test_evaluate_subway_healthy_is_pass():
    now = 1000.0
    obs = [_obs("a", 5, now), _obs("b", 9, now)]
    assert cm._evaluate_subway(obs, now, cm.REALTIME_STALE_S).status == cm.PASS


# ---------------------------------------------------------------------------
# Railroad realtime
# ---------------------------------------------------------------------------


def test_railroad_realtime_healthy_goldens_pass():
    lirr = (FIX / "railroad_lirr.pb").read_bytes()
    mnr = (FIX / "railroad_mnr.pb").read_bytes()
    stops = {
        "LIRR": json.loads((FIX / "railroad_lirr_stops.json").read_text()),
        "MNR": json.loads((FIX / "railroad_mnr_stops.json").read_text()),
    }
    fetch = FakeFetcher({"lirr": lirr, "mnr": mnr})
    result = cm.check_railroad_realtime(
        fetch, NO_SLEEP, LIRR_GOLDEN_TS + 30, stops, feed_urls={"LIRR": "lirr", "MNR": "mnr"}
    )
    assert result.status == cm.PASS


def test_railroad_realtime_empty_feed_has_no_floor():
    # An empty railroad feed is normal overnight, so it must NOT fault.
    fetch = FakeFetcher({"lirr": _rt_feed(header_ts=LIRR_GOLDEN_TS + 30)})
    result = cm.check_railroad_realtime(
        fetch, NO_SLEEP, LIRR_GOLDEN_TS + 30, {"LIRR": {}}, feed_urls={"LIRR": "lirr"}
    )
    assert result.status == cm.PASS


def test_railroad_realtime_undecodable_is_fail():
    fetch = FakeFetcher({"lirr": b"garbage"})
    result = cm.check_railroad_realtime(
        fetch, NO_SLEEP, LIRR_GOLDEN_TS + 30, {"LIRR": {}}, feed_urls={"LIRR": "lirr"}
    )
    assert result.status == cm.FAIL


def test_railroad_realtime_mnr_header_not_used_for_freshness():
    # MNR's header is a lagging shared clock the app ignores, so even a very old
    # MNR header must not raise a staleness WARN.
    old = _rt_feed(entities=[_trip_update("e", "t")], header_ts=1.0)
    fetch = FakeFetcher({"mnr": old})
    result = cm.check_railroad_realtime(
        fetch, NO_SLEEP, 10_000_000.0, {"MNR": {}}, feed_urls={"MNR": "mnr"}
    )
    assert result.status == cm.PASS


# ---------------------------------------------------------------------------
# PATH realtime
# ---------------------------------------------------------------------------


def _path_stops():
    return json.loads((FIX / "path_stops.json").read_text())


def test_path_realtime_healthy_golden_passes():
    raw = (FIX / "path_rt_gen_a.pb").read_bytes()
    fetch = FakeFetcher({"u": raw})
    result = cm.check_path_realtime(fetch, NO_SLEEP, PATH_GOLDEN_TS + 30, _path_stops(), url="u")
    assert result.status == cm.PASS


def test_path_realtime_sends_courteous_user_agent():
    raw = (FIX / "path_rt_gen_a.pb").read_bytes()
    fetch = FakeFetcher({"u": raw})
    cm.check_path_realtime(fetch, NO_SLEEP, PATH_GOLDEN_TS + 30, _path_stops(), url="u")
    _url, headers, _params = fetch.calls[0]
    assert headers and headers.get("User-Agent") == feeds.PATH_USER_AGENT


def test_path_realtime_stale_bridge_is_fail():
    raw = (FIX / "path_rt_gen_a.pb").read_bytes()
    fetch = FakeFetcher({"u": raw})
    result = cm.check_path_realtime(
        fetch, NO_SLEEP, PATH_GOLDEN_TS + cm.PATH_STALE_S + 60, _path_stops(), url="u"
    )
    assert result.status == cm.FAIL
    assert "write time older" in result.detail


def test_path_realtime_unresolved_stops_is_fail():
    # No stops resolve against an empty table, so the resolution rate collapses.
    raw = (FIX / "path_rt_gen_a.pb").read_bytes()
    fetch = FakeFetcher({"u": raw})
    result = cm.check_path_realtime(fetch, NO_SLEEP, PATH_GOLDEN_TS + 30, {}, url="u")
    assert result.status == cm.FAIL
    assert "resolved" in result.detail


def test_path_realtime_vehicle_entity_warns_on_shape_change():
    feed = _rt_feed(
        entities=[_trip_update("t", "trip", stops=[("26733", PATH_GOLDEN_TS)]), _vehicle("v")],
        header_ts=PATH_GOLDEN_TS,
    )
    fetch = FakeFetcher({"u": feed})
    result = cm.check_path_realtime(fetch, NO_SLEEP, PATH_GOLDEN_TS + 30, _path_stops(), url="u")
    assert result.status == cm.WARN
    assert "VehiclePositions" in result.detail


# ---------------------------------------------------------------------------
# Ferry service-hours boundary (both edges, injected clock)
# ---------------------------------------------------------------------------


def _et(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=feeds.NYC_TZ).timestamp()


def test_ferry_service_hours_start_edge():
    tz = feeds.NYC_TZ
    assert cm._in_ferry_service_hours(_et(2026, 7, 10, 5, 59), tz) is False
    assert cm._in_ferry_service_hours(_et(2026, 7, 10, 6, 0), tz) is True


def test_ferry_service_hours_end_edge():
    tz = feeds.NYC_TZ
    assert cm._in_ferry_service_hours(_et(2026, 7, 10, 22, 30), tz) is True
    assert cm._in_ferry_service_hours(_et(2026, 7, 10, 22, 31), tz) is False


# ---------------------------------------------------------------------------
# Ferry realtime
# ---------------------------------------------------------------------------


def _ferry_rt_urls():
    return {"alert": "a", "tripupdate": "t"}


def test_ferry_realtime_empty_at_night_is_pass():
    empty = _rt_feed()
    fetch = FakeFetcher({"a": empty, "t": empty})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 3, 0), {}, urls=_ferry_rt_urls()
    )
    assert result.status == cm.PASS
    assert "closed" in result.detail


def test_ferry_realtime_empty_at_noon_is_warn():
    empty = _rt_feed()
    fetch = FakeFetcher({"a": empty, "t": empty})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), {}, urls=_ferry_rt_urls()
    )
    assert result.status == cm.WARN
    assert "service hours" in result.detail


def test_ferry_realtime_join_above_floor_passes():
    trips = {f"t{i}": {"route_id": "ER"} for i in range(10)}
    tu = _rt_feed(entities=[_trip_update(f"e{i}", f"t{i}") for i in range(10)])
    fetch = FakeFetcher({"a": _rt_feed(), "t": tu})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), trips, urls=_ferry_rt_urls()
    )
    assert result.status == cm.PASS


def test_ferry_realtime_join_below_floor_is_fail():
    trips = {f"t{i}": {"route_id": "ER"} for i in range(8)}  # t8, t9 will not join
    tu = _rt_feed(entities=[_trip_update(f"e{i}", f"t{i}") for i in range(10)])
    fetch = FakeFetcher({"a": _rt_feed(), "t": tu})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), trips, urls=_ferry_rt_urls()
    )
    assert result.status == cm.FAIL
    assert "joined to a route" in result.detail


def test_ferry_realtime_deadheads_excluded_from_join():
    # 9 real trips (all join) plus one empty-trip-id deadhead. The deadhead must
    # not count against the join rate, so this stays a PASS at 9/9.
    trips = {f"t{i}": {"route_id": "ER"} for i in range(9)}
    entities = [_trip_update(f"e{i}", f"t{i}") for i in range(9)]
    entities.append(_trip_update("dead", ""))  # deadhead: empty trip id
    fetch = FakeFetcher({"a": _rt_feed(), "t": _rt_feed(entities=entities)})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), trips, urls=_ferry_rt_urls()
    )
    assert result.status == cm.PASS


def test_ferry_realtime_endpoint_down_is_fail():
    fetch = FakeFetcher({"a": 500, "t": _rt_feed()})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 3, 0), {}, urls=_ferry_rt_urls()
    )
    assert result.status == cm.FAIL


# ---------------------------------------------------------------------------
# Alerts realtime
# ---------------------------------------------------------------------------


def test_alerts_realtime_healthy_golden_passes():
    raw = (FIX / "alerts_mnr.pb").read_bytes()
    fetch = FakeFetcher({"u": raw})
    result = cm.check_alerts_realtime(fetch, NO_SLEEP, 1.0, feed_urls={"MNR": "u"})
    assert result.status == cm.PASS


def test_alerts_realtime_empty_is_pass():
    fetch = FakeFetcher({"u": _rt_feed()})
    result = cm.check_alerts_realtime(fetch, NO_SLEEP, 1.0, feed_urls={"MNR": "u"})
    assert result.status == cm.PASS


def test_alerts_realtime_undecodable_is_fail():
    fetch = FakeFetcher({"u": b"nope"})
    result = cm.check_alerts_realtime(fetch, NO_SLEEP, 1.0, feed_urls={"MNR": "u"})
    assert result.status == cm.FAIL


# ---------------------------------------------------------------------------
# Bus realtime (key-gated, secret-safe)
# ---------------------------------------------------------------------------


def test_bus_realtime_skipped_without_key():
    fetch = FakeFetcher({})
    result = cm.check_bus_realtime(fetch, NO_SLEEP, 1000.0, None)
    assert result.status == cm.WARN
    assert not fetch.calls  # never even attempted


def test_bus_realtime_healthy_passes_and_hides_key():
    feed = _rt_feed(entities=[_vehicle("bus1")], header_ts=1000.0)
    fetch = FakeFetcher({"u": feed})
    result = cm.check_bus_realtime(fetch, NO_SLEEP, 1030.0, "secretkey", url="u")
    assert result.status == cm.PASS
    url, _headers, params = fetch.calls[0]
    assert params == {"key": "secretkey"}  # key rides as a param
    assert "secretkey" not in url  # never baked into the url


def test_bus_realtime_empty_feed_is_warn():
    fetch = FakeFetcher({"u": _rt_feed(header_ts=1000.0)})
    result = cm.check_bus_realtime(fetch, NO_SLEEP, 1030.0, "k", url="u")
    assert result.status == cm.WARN


# ---------------------------------------------------------------------------
# Static: PATH (committed golden)
# ---------------------------------------------------------------------------


def test_path_static_golden_passes_and_returns_tables():
    zbytes = _zip_bytes(_fixture_txt_members("path_gtfs"))
    fetch = FakeFetcher({"u": zbytes})
    result, parsed = cm.check_path_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.PASS
    assert parsed is not None and parsed["stops"]["26733"]["name"].startswith("Newark")


def test_path_static_missing_stop_times_member_is_fail():
    members = _fixture_txt_members("path_gtfs")
    members.pop("stop_times.txt")
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    result, _parsed = cm.check_path_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.FAIL
    assert "stop_times.txt" in result.detail


def test_path_static_bad_zip_is_fail():
    fetch = FakeFetcher({"u": b"not a zip"})
    result, parsed = cm.check_path_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.FAIL
    assert parsed is None


def test_path_static_unreachable_is_fail():
    fetch = FakeFetcher({"u": 500})
    result, _parsed = cm.check_path_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.FAIL


# ---------------------------------------------------------------------------
# Static: ferry (committed golden + synthetic stop_times for the member check)
# ---------------------------------------------------------------------------


def _ferry_members_with_stop_times():
    # The trimmed ferry fixture omits stop_times.txt for size, but the real feed
    # ships it, so a faithful member check needs one present. A header-only stub
    # satisfies the structural check (ferry_static._parse_zip never reads it).
    members = _fixture_txt_members("ferry_gtfs")
    members["stop_times.txt"] = b"trip_id,stop_id,stop_sequence\n"
    return members


def test_ferry_static_golden_passes_and_returns_tables():
    fetch = FakeFetcher({"u": _zip_bytes(_ferry_members_with_stop_times())})
    result, parsed = cm.check_ferry_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.PASS
    assert parsed is not None and "ER" in parsed["routes"]


def test_ferry_static_missing_stop_times_member_is_fail():
    fetch = FakeFetcher({"u": _zip_bytes(_fixture_txt_members("ferry_gtfs"))})
    result, _parsed = cm.check_ferry_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.FAIL
    assert "stop_times.txt" in result.detail


def test_ferry_static_sends_courteous_user_agent():
    fetch = FakeFetcher({"u": _zip_bytes(_ferry_members_with_stop_times())})
    cm.check_ferry_static(fetch, NO_SLEEP, 1000.0, url="u")
    _url, headers, _params = fetch.calls[0]
    assert headers and headers.get("User-Agent") == feeds.PATH_USER_AGENT


# ---------------------------------------------------------------------------
# Static: subway (synthetic zip; no committed subway static golden exists)
# ---------------------------------------------------------------------------


def test_subway_static_healthy_synthetic_passes():
    members = {"stops.txt": _subway_stops_csv(120), "shapes.txt": b"shape_id\n"}
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    result, parsed = cm.check_subway_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.PASS
    assert parsed is not None and len(parsed["stops"]) == 120


def test_subway_static_too_few_stops_is_fail():
    members = {"stops.txt": _subway_stops_csv(5), "shapes.txt": b"shape_id\n"}
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    result, _parsed = cm.check_subway_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.FAIL


def test_subway_static_missing_shapes_member_is_fail():
    members = {"stops.txt": _subway_stops_csv(120)}
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    result, _parsed = cm.check_subway_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert result.status == cm.FAIL
    assert "shapes.txt" in result.detail


def test_subway_static_parse_does_not_leak_module_path():
    # _parse_subway_bytes swaps a module constant during the parse; it must be
    # restored afterward so nothing else in the process sees the temp path.
    original = cm.static_data.SUBWAY_GTFS_ZIP
    members = {"stops.txt": _subway_stops_csv(120), "shapes.txt": b"shape_id\n"}
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    cm.check_subway_static(fetch, NO_SLEEP, 1000.0, url="u")
    assert cm.static_data.SUBWAY_GTFS_ZIP == original


# ---------------------------------------------------------------------------
# Static: railroad (synthetic zips for both systems)
# ---------------------------------------------------------------------------


def _railroad_zip(n_stops):
    return _zip_bytes(
        {
            "stops.txt": _railroad_stops_csv(n_stops),
            "trips.txt": b"trip_id,route_id\n",
            "shapes.txt": b"shape_id\n",
        }
    )


def test_railroad_static_healthy_synthetic_passes():
    fetch = FakeFetcher({"lirr": _railroad_zip(240), "mnr": _railroad_zip(120)})
    result, parsed = cm.check_railroad_static(
        fetch, NO_SLEEP, 1000.0, urls={"LIRR": "lirr", "MNR": "mnr"}
    )
    assert result.status == cm.PASS
    assert set(parsed) == {"LIRR", "MNR"}


def test_railroad_static_too_few_stops_is_fail():
    fetch = FakeFetcher({"lirr": _railroad_zip(3), "mnr": _railroad_zip(120)})
    result, _parsed = cm.check_railroad_static(
        fetch, NO_SLEEP, 1000.0, urls={"LIRR": "lirr", "MNR": "mnr"}
    )
    assert result.status == cm.FAIL
    assert "LIRR" in result.detail


# ---------------------------------------------------------------------------
# feed_info end-date banding
# ---------------------------------------------------------------------------


def _zip_with_feed_info(end_date):
    body = f"feed_end_date\n{end_date}\n".encode()
    return _zip_bytes({"stops.txt": b"stop_id\n", "feed_info.txt": body})


def _end_date_status(end_date, now):
    tz = feeds.NYC_TZ
    with zipfile.ZipFile(io.BytesIO(_zip_with_feed_info(end_date))) as zf:
        return cm._feed_end_date_status(zf, set(zf.namelist()), now, tz)


def test_feed_end_date_future_is_pass():
    now = _et(2026, 7, 10, 12, 0)
    end = _yyyymmdd(now + 200 * 86400, feeds.NYC_TZ)
    status, _detail = _end_date_status(end, now)
    assert status == cm.PASS


def test_feed_end_date_within_window_is_warn():
    now = _et(2026, 7, 10, 12, 0)
    end = _yyyymmdd(now + 10 * 86400, feeds.NYC_TZ)
    status, _detail = _end_date_status(end, now)
    assert status == cm.WARN


def test_feed_end_date_past_is_fail():
    now = _et(2026, 7, 10, 12, 0)
    end = _yyyymmdd(now - 10 * 86400, feeds.NYC_TZ)
    status, _detail = _end_date_status(end, now)
    assert status == cm.FAIL


def test_feed_end_date_absent_is_none():
    tz = feeds.NYC_TZ
    with zipfile.ZipFile(io.BytesIO(_zip_bytes({"stops.txt": b"stop_id\n"}))) as zf:
        assert cm._feed_end_date_status(zf, set(zf.namelist()), 1000.0, tz) is None


# ---------------------------------------------------------------------------
# Production /api/status
# ---------------------------------------------------------------------------


def _status_json(**overrides):
    base = {
        "subway_static": "ready",
        "railroad_static": "ready",
        "path_static": "ready",
        "ferry_static": "ready",
        "feeds": {"subway": {"age_s": 5.0}, "buses": {"age_s": 8.0}},
        "alerts": {"degraded_systems": []},
    }
    base.update(overrides)
    return json.dumps(base).encode()


def test_production_skipped_when_url_unset():
    fetch = FakeFetcher({})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, None)
    assert len(results) == 1 and results[0].status == cm.WARN
    assert not fetch.calls


def test_production_healthy_is_all_pass():
    fetch = FakeFetcher({"https://app.example/api/status": _status_json()})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert all(r.status == cm.PASS for r in results)


def test_production_static_not_ready_is_fail():
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(path_static="failed")})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example/")
    statics = next(r for r in results if r.name == "production:statics")
    assert statics.status == cm.FAIL
    assert "path_static" in statics.detail


def test_production_stale_feed_is_warn():
    feeds_map = {"subway": {"age_s": 5.0}, "buses": {"age_s": 9999.0}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.WARN


def test_production_degraded_alerts_is_warn():
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts={"degraded_systems": ["LIRR"]})}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN and "LIRR" in alertline.detail


def test_production_non_200_is_fail():
    fetch = FakeFetcher({"https://app.example/api/status": 502})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert results[0].status == cm.FAIL


def test_production_non_json_is_fail():
    fetch = FakeFetcher({"https://app.example/api/status": b"<html>oops</html>"})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert results[0].status == cm.FAIL


# ---------------------------------------------------------------------------
# Runner wiring / hermeticity
# ---------------------------------------------------------------------------


def test_run_all_is_hermetic_and_names_every_check():
    # Every fetch fails (500); no test double reaches the network. The run should
    # still produce one result per check without raising, exercising the wiring.
    class AllFail:
        def __init__(self):
            self.calls = 0

        def __call__(self, url, headers=None, params=None):
            self.calls += 1
            return cm.FetchResult(500, b"")

    fetch = AllFail()
    results = cm.run_all(fetch, NO_SLEEP, 1000.0, env={})
    names = {r.name for r in results}
    for expected in (
        "subway-static",
        "railroad-static",
        "path-static",
        "ferry-static",
        "subway-realtime",
        "railroad-realtime",
        "path-realtime",
        "ferry-realtime",
        "alerts-realtime",
        "bus-realtime",
        "production",
    ):
        assert expected in names
    assert fetch.calls > 0  # it did try to fetch, via the injected fake only


def test_format_summary_table_escapes_pipes():
    rows = [cm.Result("x", cm.WARN, "a | b")]
    table = cm.format_summary_table(rows)
    assert "a \\| b" in table
    assert table.startswith("| Check | Status | Detail |")
