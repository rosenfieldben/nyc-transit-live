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

import pytest
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


def test_evaluate_subway_no_header_timestamp_is_warn():
    now = 1000.0
    # A live feed that decoded and carries entities but omitted its header
    # timestamp: freshness cannot be judged, so it is a WARN, not a pass.
    obs = [_obs("a", 5, now), _obs("b", 5, None)]
    result = cm._evaluate_subway(obs, now, cm.REALTIME_STALE_S)
    assert result.status == cm.WARN
    assert "no header timestamp: b" in result.detail


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


def test_railroad_realtime_stale_lirr_header_is_warn():
    # LIRR's header DOES track publish time, so a stale one is a real signal.
    old = _rt_feed(entities=[_trip_update("e", "t")], header_ts=1000.0)
    fetch = FakeFetcher({"lirr": old})
    result = cm.check_railroad_realtime(
        fetch,
        NO_SLEEP,
        1000.0 + cm.REALTIME_STALE_S + 60,
        {"LIRR": {}},
        feed_urls={"LIRR": "lirr"},
    )
    assert result.status == cm.WARN
    assert "older than" in result.detail


def test_railroad_realtime_lirr_missing_header_is_warn():
    # A LIRR feed that omits its header timestamp: freshness cannot be judged.
    headerless = _rt_feed(entities=[_trip_update("e", "t")])  # no header_ts
    fetch = FakeFetcher({"lirr": headerless})
    result = cm.check_railroad_realtime(
        fetch, NO_SLEEP, 10_000.0, {"LIRR": {}}, feed_urls={"LIRR": "lirr"}
    )
    assert result.status == cm.WARN
    assert "omitted its header timestamp" in result.detail


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
    # A NON-empty but mismatched parent table (real stations, wrong ids): the
    # golden feed's stop ids resolve against none of them, so the resolution rate
    # collapses and the check FAILs. (Empty stops is a different case, below: it
    # means the static load failed and resolution is skipped, not FAILed.)
    raw = (FIX / "path_rt_gen_a.pb").read_bytes()
    mismatched = {"99999": {"id": "99999", "name": "Nowhere", "lat": 40.7, "lon": -74.0}}
    fetch = FakeFetcher({"u": raw})
    result = cm.check_path_realtime(fetch, NO_SLEEP, PATH_GOLDEN_TS + 30, mismatched, url="u")
    assert result.status == cm.FAIL
    assert "resolved" in result.detail


def test_path_realtime_skips_resolution_when_static_unavailable():
    # An empty stops table means the PATH static load failed (its own check
    # reports why). The resolution band must be skipped with a note, not FAILed,
    # so the operator is not misdirected to a realtime id mismatch.
    raw = (FIX / "path_rt_gen_a.pb").read_bytes()
    fetch = FakeFetcher({"u": raw})
    result = cm.check_path_realtime(fetch, NO_SLEEP, PATH_GOLDEN_TS + 30, {}, url="u")
    assert result.status == cm.PASS
    assert "static parent table unavailable" in result.detail


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
    # 8 real trips (all join) plus one empty-trip-id deadhead. The counts are
    # chosen to DISTINGUISH exclusion from inclusion: excluding the deadhead is
    # 8/8 = 1.0 = PASS, but including it would be 8/9 = 0.89 < 0.90 = FAIL. So a
    # green here can only mean the deadhead was excluded, as intended.
    trips = {f"t{i}": {"route_id": "ER"} for i in range(8)}
    entities = [_trip_update(f"e{i}", f"t{i}") for i in range(8)]
    entities.append(_trip_update("dead", ""))  # deadhead: empty trip id
    fetch = FakeFetcher({"a": _rt_feed(), "t": _rt_feed(entities=entities)})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), trips, urls=_ferry_rt_urls()
    )
    assert result.status == cm.PASS


def test_ferry_realtime_all_empty_trip_ids_during_service_is_warn():
    # Every in-service trip update carries an empty trip_id (namespace drift, or
    # an all-deadhead snapshot): the route join is impossible for all of them, so
    # the check must surface it rather than pass silently.
    trips = {"t0": {"route_id": "ER"}}
    entities = [_trip_update(f"e{i}", "") for i in range(5)]
    fetch = FakeFetcher({"a": _rt_feed(), "t": _rt_feed(entities=entities)})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), trips, urls=_ferry_rt_urls()
    )
    assert result.status == cm.WARN
    assert "none carry a trip_id" in result.detail


def test_ferry_realtime_skips_join_when_static_trips_unavailable():
    # A failed ferry static load hands an empty trips table to the realtime
    # check. With no table the join cannot be assessed, so it must NOT emit a
    # 0%-joined FAIL that would misattribute a static blip to a realtime break.
    entities = [_trip_update(f"e{i}", f"t{i}") for i in range(5)]
    fetch = FakeFetcher({"a": _rt_feed(), "t": _rt_feed(entities=entities)})
    result = cm.check_ferry_realtime(
        fetch, NO_SLEEP, _et(2026, 7, 10, 12, 0), {}, urls=_ferry_rt_urls()
    )
    assert result.status == cm.PASS
    assert "static trips table unavailable" in result.detail


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


def test_alerts_realtime_default_feeds_include_ferry_and_count_five():
    # The alerts-realtime check ITERATES ALERT_FEED_URLS rather than hardcoding four,
    # so adding "ferry" makes it check the fifth feed and the count in the detail moves
    # to 5. Run with the default (real) feed set, every feed decodable.
    assert "ferry" in cm.feeds.ALERT_FEED_URLS
    valid = _rt_feed()
    fetch = FakeFetcher({url: valid for url in cm.feeds.ALERT_FEED_URLS.values()})
    result = cm.check_alerts_realtime(fetch, NO_SLEEP, 1.0)  # default feed_urls
    assert result.status == cm.PASS
    assert "5 alert feeds decodable" in result.detail


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


def test_bus_realtime_stale_header_is_warn():
    # Vehicles present but the header is older than the freshness window.
    feed = _rt_feed(entities=[_vehicle("bus1")], header_ts=1000.0)
    fetch = FakeFetcher({"u": feed})
    result = cm.check_bus_realtime(fetch, NO_SLEEP, 1000.0 + cm.REALTIME_STALE_S + 60, "k", url="u")
    assert result.status == cm.WARN
    assert "older than" in result.detail


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


def _end_date_status(end_date, now, key="unittest"):
    # key defaults to a feed that is never in ACKNOWLEDGED_EXPIRED_FEEDS, so the
    # banding tests below see the plain (un-acknowledged) FAIL/WARN/PASS behavior.
    tz = feeds.NYC_TZ
    with zipfile.ZipFile(io.BytesIO(_zip_with_feed_info(end_date))) as zf:
        return cm._feed_end_date_status(key, zf, set(zf.namelist()), now, tz)


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
        assert cm._feed_end_date_status("unittest", zf, set(zf.namelist()), 1000.0, tz) is None


def test_feed_end_date_far_future_sentinel_does_not_crash():
    # A "never expires" sentinel (99991231) overflows datetime.max when the
    # one-day grace is added. That must be swallowed to None (healthy), not raised
    # as an OverflowError that would abort the whole monitor run.
    now = _et(2026, 7, 10, 12, 0)
    assert _end_date_status("99991231", now) is None


# ---------------------------------------------------------------------------
# ACKNOWLEDGED_EXPIRED_FEEDS: an acknowledged expired feed downgrades to WARN
# ---------------------------------------------------------------------------


def test_feed_end_date_acknowledged_expired_is_warn_with_reason(monkeypatch):
    # An expired feed listed in ACKNOWLEDGED_EXPIRED_FEEDS for its exact end date
    # downgrades FAIL -> WARN and carries the reason text, so the condition is still
    # surfaced every run rather than silenced. Uses a synthetic entry so the test
    # holds independently of whatever real acknowledgments the allowlist carries.
    now = _et(2026, 7, 10, 12, 0)
    end = _yyyymmdd(now - 10 * 86400, feeds.NYC_TZ)
    reason = "2026-07-10: upstream frozen, topology verified live"
    monkeypatch.setitem(cm.ACKNOWLEDGED_EXPIRED_FEEDS, ("acktest", end), reason)
    status, detail = _end_date_status(end, now, key="acktest")
    assert status == cm.WARN
    assert reason in detail
    assert end in detail  # still names the expired date, just downgraded


def test_feed_end_date_acknowledgment_pinned_to_exact_date(monkeypatch):
    # The acknowledgment is pinned to the EXACT expired date: a different past date
    # for the same feed still FAILs, so a future republish that later expires cannot
    # be silently covered by a stale acknowledgment.
    now = _et(2026, 7, 10, 12, 0)
    acked = _yyyymmdd(now - 10 * 86400, feeds.NYC_TZ)
    other = _yyyymmdd(now - 40 * 86400, feeds.NYC_TZ)
    monkeypatch.setitem(cm.ACKNOWLEDGED_EXPIRED_FEEDS, ("acktest", acked), "reason")
    status, detail = _end_date_status(other, now, key="acktest")
    assert status == cm.FAIL
    assert "acknowledged" not in detail


def test_feed_end_date_acknowledgment_scoped_to_feed_key(monkeypatch):
    # The acknowledgment is scoped to ITS feed: the same expired date on a DIFFERENT
    # feed key FAILs, so acknowledging one feed never downgrades another.
    now = _et(2026, 7, 10, 12, 0)
    end = _yyyymmdd(now - 10 * 86400, feeds.NYC_TZ)
    monkeypatch.setitem(cm.ACKNOWLEDGED_EXPIRED_FEEDS, ("acktest", end), "reason")
    status, _detail = _end_date_status(end, now, key="otherfeed")
    assert status == cm.FAIL


def test_feed_end_date_unacknowledged_past_is_fail():
    # Existing behavior pinned: a past date for a feed with NO acknowledgment FAILs
    # and carries no acknowledgment text.
    now = _et(2026, 7, 10, 12, 0)
    end = _yyyymmdd(now - 10 * 86400, feeds.NYC_TZ)
    status, detail = _end_date_status(end, now, key="unlisted")
    assert status == cm.FAIL
    assert "acknowledged" not in detail


def test_feed_end_date_acknowledgment_does_not_touch_soon_expiring_warn(monkeypatch):
    # Acknowledgment applies only to ALREADY-past dates. A feed whose date is still
    # in the future but within the warn window gets the plain "within N days" WARN,
    # never the acknowledgment reason (its exact past date is not the future one).
    now = _et(2026, 7, 10, 12, 0)
    soon = _yyyymmdd(now + 10 * 86400, feeds.NYC_TZ)
    monkeypatch.setitem(cm.ACKNOWLEDGED_EXPIRED_FEEDS, ("acktest", soon), "reason")
    status, detail = _end_date_status(soon, now, key="acktest")
    assert status == cm.WARN
    assert "within" in detail
    assert "acknowledged" not in detail


def test_path_expired_feed_is_acknowledged_as_warn():
    # Pins the live acknowledgment this change exists for: PATH's expired 20260601
    # feed_end_date downgrades to WARN carrying the reason, not FAIL. When Trillium
    # republishes and the entry is removed from ACKNOWLEDGED_EXPIRED_FEEDS, this test
    # fails on purpose, the reminder to re-verify and drop it.
    assert ("path", "20260601") in cm.ACKNOWLEDGED_EXPIRED_FEEDS
    now = _et(2026, 7, 12, 12, 0)  # after 20260601
    status, detail = _end_date_status("20260601", now, key="path")
    assert status == cm.WARN
    assert cm.ACKNOWLEDGED_EXPIRED_FEEDS[("path", "20260601")] in detail


def test_check_path_static_acknowledged_expired_feed_is_warn_not_fail():
    # End to end through check_path_static (proving the "path" key is threaded to
    # _feed_end_date_status and the WARN folds into the Result): an otherwise-healthy
    # PATH zip carrying the expired 20260601 feed_end_date returns WARN, not FAIL,
    # and still yields the parsed tables the realtime check needs.
    members = _fixture_txt_members("path_gtfs")
    members["feed_info.txt"] = b"feed_end_date\n20260601\n"
    now = _et(2026, 7, 12, 12, 0)  # after 20260601
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    result, parsed = cm.check_path_static(fetch, NO_SLEEP, now, url="u")
    assert result.status == cm.WARN
    assert "20260601" in result.detail
    assert parsed is not None and parsed["stops"]["26733"]["name"].startswith("Newark")


def test_check_path_static_acknowledged_expired_plus_structural_fail_is_fail():
    # The acknowledgment must not weaken OTHER failure modes on the same feed. A PATH
    # zip that is BOTH acknowledged-expired (20260601) AND structurally broken (here
    # the 26733=Newark identity check fails) must still FAIL: the ack only appends a
    # WARN via _apply_end_status, and _worst folds it with the concurrent FAIL rather
    # than letting the downgrade mask a genuine break. Renaming 26733 keeps the zip
    # parseable (so this exercises the fold, not the early unparseable-return path).
    members = _fixture_txt_members("path_gtfs")
    members["feed_info.txt"] = b"feed_end_date\n20260601\n"
    members["stops.txt"] = members["stops.txt"].replace(b"26733,,,Newark,", b"26733,,,Elsewhere,")
    now = _et(2026, 7, 12, 12, 0)  # after 20260601
    fetch = FakeFetcher({"u": _zip_bytes(members)})
    result, _parsed = cm.check_path_static(fetch, NO_SLEEP, now, url="u")
    assert result.status == cm.FAIL
    assert "26733" in result.detail  # the structural failure, not silenced by the ack WARN


def _subway_zip_with_feed_info(end_date):
    return _zip_bytes(
        {
            "stops.txt": _subway_stops_csv(120),
            "shapes.txt": b"shape_id\n",
            "feed_info.txt": f"feed_end_date\n{end_date}\n".encode(),
        }
    )


def test_static_check_folds_expired_feed_info_into_fail():
    # End to end (not just the helper): a static zip whose feed_end_date is past
    # must make check_*_static return FAIL, proving _apply_end_status folds the
    # end-date result into the real check Result.
    now = _et(2026, 7, 10, 12, 0)
    past = _yyyymmdd(now - 10 * 86400, feeds.NYC_TZ)
    fetch = FakeFetcher({"u": _subway_zip_with_feed_info(past)})
    result, _parsed = cm.check_subway_static(fetch, NO_SLEEP, now, url="u")
    assert result.status == cm.FAIL
    assert "past" in result.detail


def test_static_check_folds_soon_expiring_feed_info_into_warn():
    now = _et(2026, 7, 10, 12, 0)
    soon = _yyyymmdd(now + 10 * 86400, feeds.NYC_TZ)
    fetch = FakeFetcher({"u": _subway_zip_with_feed_info(soon)})
    result, _parsed = cm.check_subway_static(fetch, NO_SLEEP, now, url="u")
    assert result.status == cm.WARN
    assert "within" in result.detail


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


def test_production_unset_url_is_fail_not_a_silent_skip():
    # R4 CHANGED THIS: an unset MONITOR_STATUS_URL used to WARN-skip the whole
    # production section, so a completely unmonitored deployment looked exactly like
    # a healthy one (green). Silence must be chosen, never defaulted.
    fetch = FakeFetcher({})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, None)
    assert len(results) == 1 and results[0].status == cm.FAIL
    assert not fetch.calls  # still no request attempted
    detail = results[0].detail
    # The message has to TEACH, since whoever sees it may not know the variable.
    assert "MONITOR_STATUS_URL" in detail  # names the variable
    assert "base URL" in detail  # says what it holds
    assert "Secrets and variables" in detail and "Variables" in detail  # says where
    assert "MONITOR_SKIP_PRODUCTION" in detail  # offers the legitimate way out


def test_production_explicit_skip_is_warn():
    # The escape hatch for the legitimate cases (a fork, a local dispatch run with
    # no deployment). It is a WARN, and it says out loud that it was deliberate, so
    # the summary never implies production was checked when it was not.
    fetch = FakeFetcher({})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, None, skip=True)
    assert len(results) == 1 and results[0].status == cm.WARN
    assert "MONITOR_SKIP_PRODUCTION" in results[0].detail
    assert not fetch.calls


def test_production_explicit_skip_wins_even_with_a_url_set():
    # Opting out is unconditional: it must not silently start probing just because
    # a stale variable is still lying around in the environment.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json()})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example", skip=True)
    assert len(results) == 1 and results[0].status == cm.WARN
    assert not fetch.calls


@pytest.mark.parametrize(
    "configured",
    [
        "https://app.example",  # the documented base form
        "https://app.example/",  # base with a trailing slash
        "https://app.example/api/status",  # the full status URL an operator pastes
        "https://app.example/api/status/",  # ...with a trailing slash
    ],
    ids=["base", "base-slash", "full-status", "full-status-slash"],
)
def test_production_accepts_both_url_forms(configured):
    # THE 2026-07-24 INCIDENT: the variable holds a base and the monitor appends
    # /api/status, but the instinct is to paste the status URL you were just
    # looking at, which produced /api/status/api/status and a baffling 404 FAIL.
    # Every form must resolve to the same single request.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json()})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, configured)
    assert [call[0] for call in fetch.calls] == ["https://app.example/api/status"]
    assert all(r.status == cm.PASS for r in results)


def test_production_healthy_is_all_pass():
    fetch = FakeFetcher({"https://app.example/api/status": _status_json()})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert all(r.status == cm.PASS for r in results)


def test_production_failed_static_is_fail():
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(path_static="failed")})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example/")
    statics = next(r for r in results if r.name == "production:statics")
    assert statics.status == cm.FAIL
    assert "path_static" in statics.detail


@pytest.mark.parametrize("state", ["loading", "failed", None, "unexpected"])
def test_production_any_not_ready_static_is_fail(state):
    # R4 CHANGED THIS: "loading" used to be a tolerated WARN on the grounds that a
    # probe must not flap red mid-warmup. But a static group that is not ready means
    # a whole mode is dark for riders (no stops, no lines, and for subway/PATH/ferry
    # no vehicles, since those pollers gate on the static), and a probe cannot know
    # whether the not-ready it is seeing is transient. Any state but "ready" fails.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(subway_static=state)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    statics = next(r for r in results if r.name == "production:statics")
    assert statics.status == cm.FAIL
    assert "subway_static" in statics.detail


def test_production_non_object_json_is_fail():
    # Valid JSON but not an object (a bare null/list/number) must FAIL only its
    # own line, not raise an AttributeError that aborts the whole run.
    for body in (b"null", b"[]", b"42", b'"a string"'):
        fetch = FakeFetcher({"https://app.example/api/status": body})
        results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
        assert len(results) == 1 and results[0].status == cm.FAIL


def test_production_empty_feeds_map_is_fail():
    # R4 CHANGED THIS (WARN -> FAIL): a healthy deployment always reports its live
    # feeds, so an empty map is a deploy regression (broken startup or a payload
    # shape change), not an upstream mood. It does not age into a threshold.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds={})})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.FAIL
    assert "no feeds" in feedline.detail


def test_production_malformed_nested_shapes_do_not_crash():
    # A proxy/error page could return a status object whose feeds/alerts are the
    # wrong JSON type. The check must coerce and WARN, not raise .items()/.get()
    # and abort the run. A non-empty list for feeds is the only .items()-crashing
    # case (an empty list is falsy and handled by the empty-map branch).
    body = json.dumps(
        {
            "subway_static": "ready",
            "railroad_static": "ready",
            "path_static": "ready",
            "ferry_static": "ready",
            "feeds": [1, 2, 3],
            "alerts": [],
        }
    ).encode()
    fetch = FakeFetcher({"https://app.example/api/status": body})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert results[0].status == cm.PASS  # reachable
    feedline = next(r for r in results if r.name == "production:feeds")
    # R4: the coerced-to-empty case now fails with the same reasoning as an
    # explicitly empty map. Still surfaced, still not crashed, which is the point.
    assert feedline.status == cm.FAIL


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (599.0, "PASS"),  # just inside the fresh band
        (601.0, "WARN"),  # just past PRODUCTION_FEED_STALE_S: worth a look
        (1799.0, "WARN"),  # still only worth a look
        (1801.0, "FAIL"),  # past PRODUCTION_FEED_FAIL_S: an outage, not a blip
    ],
    ids=["599-pass", "601-warn", "1799-warn", "1801-fail"],
)
def test_production_feed_age_bands_at_the_exact_edges(age, expected):
    # The two edges exist so a flapping upstream cannot train the operator to ignore
    # FAILs: everything between 600s and 1800s is a WARN nobody has to act on.
    feeds_map = {"subway": {"age_s": 5.0}, "buses": {"age_s": age}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == getattr(cm, expected)


@pytest.mark.parametrize("age", [None, "recent", True], ids=["null", "string", "bool"])
def test_production_feed_without_a_usable_age_is_fail(age):
    # A feed present in the payload but carrying no numeric age has never polled, or
    # the field changed shape. Either way that is a deploy regression, so it fails at
    # once rather than being folded into the staleness bands. True is excluded
    # explicitly because bool is a subclass of int and would otherwise read as age 1.
    feeds_map = {"subway": {"age_s": 5.0}, "buses": {"age_s": age}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.FAIL
    assert "buses" in feedline.detail


def test_production_degraded_alerts_is_warn_while_alerts_are_retained():
    # Still a WARN: the backend carries a down system's last-known alerts forward,
    # so riders keep seeing them and the outage is covered.
    alerts = {
        "degraded_systems": ["LIRR"],
        "systems": {"LIRR": {"fresh_at": 900.0, "retained_since": 900.0, "last_error": {}}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=1000.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN and "LIRR" in alertline.detail


def test_production_alerts_retained_past_the_horizon_is_fail():
    # Past PRODUCTION_ALERT_RETENTION_MAX_S the backend has DROPPED the retained
    # alerts, so the coverage the WARN was predicated on is gone: riders now see
    # nothing for that system, which is a FAIL.
    alerts = {
        "degraded_systems": ["LIRR"],
        "systems": {"LIRR": {"fresh_at": 900.0, "retained_since": 900.0, "last_error": {}}},
    }
    # served_at - retained_since = 1801s, one second past the horizon.
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=2701.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL
    assert "LIRR" in alertline.detail and "retention horizon" in alertline.detail


def test_production_degraded_system_stale_without_retention_clock_is_fail():
    # A system failing without ever establishing a retention clock: retained_since is
    # null, so rule 1 cannot catch it; the fresh_at edge does.
    alerts = {
        "degraded_systems": ["MNR"],
        "systems": {"MNR": {"fresh_at": 100.0, "retained_since": None, "last_error": {}}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=2000.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL and "MNR" in alertline.detail


def test_production_alerts_without_served_at_do_not_fail_on_age():
    # No served_at means no skew-free way to age the deployment's timestamps, and a
    # monitor must not FAIL on a number it cannot compute honestly. It degrades to
    # the WARN the degraded list alone justifies (an older backend, or a proxy that
    # stripped the field, must not turn the run red on arithmetic it cannot do).
    alerts = {
        "degraded_systems": ["LIRR"],
        "systems": {"LIRR": {"fresh_at": 1.0, "retained_since": 1.0}},  # ancient
    }
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(alerts=alerts)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN


def test_production_alert_ages_use_served_at_not_the_runner_clock():
    # fresh_at and retained_since are stamped by the DEPLOYMENT, so they must be
    # compared against the payload's own served_at. Here the runner's clock is wildly
    # skewed from the deployment's; a check that mixed them would compute a ~1e6s age
    # and fail. Same-clock pairs only, matching the app's own freshness discipline.
    alerts = {
        "degraded_systems": ["LIRR"],
        "systems": {"LIRR": {"fresh_at": 999_000.0, "retained_since": 999_000.0}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=999_100.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")  # now=1000
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN  # 100s of retention, nowhere near the horizon


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


def test_run_all_unset_status_url_produces_a_production_fail():
    # The wiring end of the change: run_all must pass the unset variable through so
    # the section fails, rather than the old silent WARN-skip.
    class AllFail:
        def __call__(self, url, headers=None, params=None):
            return cm.FetchResult(500, b"")

    results = cm.run_all(AllFail(), NO_SLEEP, 1000.0, env={})
    production = [r for r in results if r.name.startswith("production")]
    assert len(production) == 1
    assert production[0].status == cm.FAIL
    assert "MONITOR_STATUS_URL" in production[0].detail


def test_run_all_honors_the_explicit_skip_variable():
    # Any non-empty value opts out, the usual shell-variable convention.
    class AllFail:
        def __call__(self, url, headers=None, params=None):
            return cm.FetchResult(500, b"")

    for value in ("1", "true", "yes"):
        results = cm.run_all(AllFail(), NO_SLEEP, 1000.0, env={"MONITOR_SKIP_PRODUCTION": value})
        production = [r for r in results if r.name.startswith("production")]
        assert len(production) == 1 and production[0].status == cm.WARN


def test_exit_code_is_zero_for_all_warn_and_one_for_any_fail(monkeypatch, tmp_path, capsys):
    # THE INVARIANT THIS PR RESTS ON: WARN never fails the run, FAIL always does.
    # Pinned at the main() level (not by re-deriving it from a Result list) so that
    # whoever next touches Result handling cannot regress the distinction silently.
    # The unset-variable case is included because that is precisely the run that used
    # to exit 0 while checking nothing at all.
    def fake_fetcher():
        def _fetch(url, headers=None, params=None):
            return cm.FetchResult(500, b"")

        return _fetch

    monkeypatch.setattr(cm, "make_httpx_fetcher", fake_fetcher)
    monkeypatch.setattr(cm.time, "sleep", lambda _s: None)

    # 1. An all-WARN run exits 0. Force one by stubbing run_all to a WARN-only list,
    #    which keeps the assertion about main()'s exit rule rather than about which
    #    checks happen to warn today.
    monkeypatch.setattr(cm, "run_all", lambda *a, **k: [cm.Result("x", cm.WARN, "w")])
    monkeypatch.delenv("MONITOR_STATUS_URL", raising=False)
    assert cm.main() == 0

    # 2. A run containing any FAIL exits 1, even alongside passes and warns.
    monkeypatch.setattr(
        cm,
        "run_all",
        lambda *a, **k: [
            cm.Result("a", cm.PASS, "p"),
            cm.Result("b", cm.WARN, "w"),
            cm.Result("c", cm.FAIL, "f"),
        ],
    )
    assert cm.main() == 1

    # 3. And the real unset-variable path exits 1 through the genuine run_all, which
    #    is the regression this PR exists to prevent.
    monkeypatch.undo()
    monkeypatch.setattr(cm, "make_httpx_fetcher", fake_fetcher)
    monkeypatch.setattr(cm.time, "sleep", lambda _s: None)
    monkeypatch.delenv("MONITOR_STATUS_URL", raising=False)
    monkeypatch.delenv("MONITOR_SKIP_PRODUCTION", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cm.main() == 1
    assert "MONITOR_STATUS_URL" in capsys.readouterr().out


def test_format_summary_table_escapes_pipes():
    rows = [cm.Result("x", cm.WARN, "a | b")]
    table = cm.format_summary_table(rows)
    assert "a \\| b" in table
    assert table.startswith("| Check | Status | Detail |")
