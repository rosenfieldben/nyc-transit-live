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


def test_production_skipped_when_url_unset():
    fetch = FakeFetcher({})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, None)
    assert len(results) == 1 and results[0].status == cm.WARN
    assert not fetch.calls


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


def test_production_loading_static_is_warn_not_fail():
    # "loading" is the normal cold-start / redeploy transient the app's own
    # /healthz tolerates, so it must WARN, not FAIL: a 6-hourly probe must not
    # flap red just because it landed mid-warmup.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(subway_static="loading")})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    statics = next(r for r in results if r.name == "production:statics")
    assert statics.status == cm.WARN
    assert "subway_static" in statics.detail


def test_production_non_object_json_is_fail():
    # Valid JSON but not an object (a bare null/list/number) must FAIL only its
    # own line, not raise an AttributeError that aborts the whole run.
    for body in (b"null", b"[]", b"42", b'"a string"'):
        fetch = FakeFetcher({"https://app.example/api/status": body})
        results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
        assert len(results) == 1 and results[0].status == cm.FAIL


def test_production_empty_feeds_map_is_warn():
    # A healthy deployment always reports its live feeds; an empty map means a
    # broken startup, which the "0 feeds fresh" PASS would otherwise hide.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds={})})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.WARN
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
    assert feedline.status == cm.WARN  # coerced to empty, surfaced not crashed


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
