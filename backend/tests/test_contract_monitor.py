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
from datetime import datetime, timedelta
from pathlib import Path

import httpx
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
    body), a FetchResult (served verbatim, for a non-200 that CARRIES a body), a
    BaseException (raised, simulating a transport error), or a list of any of
    those consumed one per call (to script a retry sequence). Every call is
    recorded so tests can assert call counts and that a secret rode in params,
    not in the url.

    THE FetchResult FORM ARRIVED WITH F1 and is not a convenience: /healthz
    answers 503 precisely when it has something to say, so status-and-body is a
    real upstream shape here, and until this existed a test could express the
    status or the body but never both."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []
        # POST form fields, recorded separately so `calls` stays the 3-tuples every
        # pre-15a assertion unpacks. NJ Transit is the only source that POSTs.
        self.forms = []

    def __call__(self, url, headers=None, params=None, files=None):
        self.calls.append((url, headers, params))
        self.forms.append(files)
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
        if isinstance(value, cm.FetchResult):
            return value
        # After FetchResult, because FetchResult is a NamedTuple and `bool` is an
        # int; neither should be caught by the int branch below.
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
    # The alerts-realtime check ITERATES its feed table rather than hardcoding four,
    # so adding "ferry" makes it check the fifth feed and the count in the detail moves
    # to 5. Run with the default (real) feed set, every feed decodable.
    assert "ferry" in cm.feeds.KEYLESS_ALERT_FEEDS
    valid = _rt_feed()
    fetch = FakeFetcher({url: valid for url in cm.feeds.KEYLESS_ALERT_FEEDS.values()})
    result = cm.check_alerts_realtime(fetch, NO_SLEEP, 1.0)  # default feed_urls
    assert result.status == cm.PASS
    assert "5 alert feeds decodable" in result.detail


def test_alerts_realtime_never_gets_the_njt_feed_which_only_answers_a_post():
    """The split named in check_alerts_realtime's docstring, asserted from both
    sides so it cannot become an accidental gap.

    NJ Transit publishes an alerts feed and this app polls it, so it IS in the
    production table. But it answers only a POST carrying a token, and this check's
    fetcher GETs. Pointed at the full table it would report a permanent FAIL
    against a feed it never spoke to, and (worse, in a pull request context with no
    credentials) it would reach a live NJ Transit endpoint from a fork.

    The other side of the split lives in check_njt_realtime, which holds the run's
    one minted token; test_njt_realtime_checks_the_alerts_feed_through_the_door
    pins that the feed is checked SOMEWHERE, so this exclusion cannot quietly
    become "nobody checks NJT alerts".
    """
    assert "njt" in cm.feeds.ALERT_FEED_URLS, "the app does poll this feed"
    assert "njt" not in cm.feeds.KEYLESS_ALERT_FEEDS, "but not with a GET"
    valid = _rt_feed()
    fetch = FakeFetcher({url: valid for url in cm.feeds.ALERT_FEED_URLS.values()})
    result = cm.check_alerts_realtime(fetch, NO_SLEEP, 1.0)  # default feed_urls
    assert result.status == cm.PASS
    njt_calls = [url for url, _h, _p in fetch.calls if "njtransit" in url]
    assert njt_calls == [], f"alerts-realtime must not touch an NJT endpoint, got {njt_calls}"


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
        # NJT's fourth state is acceptable here BY DESIGN (15a): a deployment given
        # no NJ Transit credentials makes no network call at all, and failing on
        # that would paint every non-NJT deployment permanently red. The
        # njt_static-specific bands are asserted separately below.
        "njt_static": "ready",
        "feeds": {"subway": {"age_s": 5.0}, "buses": {"age_s": 8.0}},
        # age_s is part of a healthy alerts payload: the poll-level age is what
        # catches a TOTAL outage, where the per-system map freezes and looks fine.
        "alerts": {"age_s": 30.0, "degraded_systems": []},
    }
    base.update(overrides)
    return json.dumps(base).encode()


_PROD_BASE = "https://app.example"
_PROD_STATUS = f"{_PROD_BASE}/api/status"
_PROD_HEALTH = f"{_PROD_BASE}/healthz"
_PROD_SERVED_AT = 1000.0

# NOTE what _status_json deliberately does NOT carry: served_at. Several tests
# below depend on its absence to reach the "cannot age this honestly" branches, so
# the happy path adds it explicitly rather than the builder defaulting it.


def _healthz_json(**overrides):
    """A healthy /healthz body. `degraded` present and empty is the healthy shape,
    not an omission: the monitor treats an ABSENT key as an unwatched deployment."""
    body = {"status": "pass", "degraded": []}
    body.update(overrides)
    return json.dumps(body).encode()


def _healthy_prod(*, health=None, advance=None, **status_overrides):
    """Both URLs check_production probes, wired for the full happy path.

    /api/status is a LIST, which FakeFetcher consumes one entry per call, so the
    two replay probes see served_at advance the way a live deployment's does. A
    single body would replay itself and fail the served_at witness, which is the
    point of that witness and would otherwise make every production test here
    look like a caching proxy.
    """
    gap = cm.PRODUCTION_REPLAY_PROBE_GAP_S if advance is None else advance
    return FakeFetcher(
        {
            _PROD_STATUS: [
                _status_json(served_at=_PROD_SERVED_AT, **status_overrides),
                _status_json(served_at=_PROD_SERVED_AT + gap, **status_overrides),
            ],
            _PROD_HEALTH: _healthz_json() if health is None else health,
        }
    )


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
    fetch = _healthy_prod()
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, configured)
    # BOTH paths resolve off the one variable, in every form. F1 added /healthz
    # without adding a second environment variable, so the form that used to be
    # only about /api/status is now also what proves the health probe is pointed
    # at the same deployment.
    assert [call[0] for call in fetch.calls] == [_PROD_STATUS, _PROD_STATUS, _PROD_HEALTH]
    assert all(r.status == cm.PASS for r in results)


def test_production_healthy_is_all_pass():
    """THE GREEN PATH. A monitor that cries wolf gets muted, so a deployment with
    nothing wrong with it has to come back clean across every line F1 added."""
    fetch = _healthy_prod()
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert all(r.status == cm.PASS for r in results), [r for r in results if r.status != cm.PASS]
    assert {r.name for r in results} >= {"production:healthz", "production:served_at"}


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
            "njt_static": "ready",
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


def test_production_never_polled_feed_is_warn_not_a_permanent_fail():
    # REVIEW FIX (was a critical false FAIL): age_s is null exactly when fetched_at is
    # None, i.e. the feed has NEVER had a successful poll. A deployment carrying no
    # bus API key serves buses.age_s = null FOREVER, and the app supports that
    # explicitly (the README promises a missing key does not take down the map, and
    # /healthz stays healthy). Failing on it painted a healthy deployment red on
    # every 6-hourly run, indefinitely. It is also boot-order dependent: the same
    # upstream outage walks PASS -> WARN -> FAIL if it starts AFTER boot.
    feeds_map = {"subway": {"age_s": 5.0}, "buses": {"age_s": None}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.WARN
    assert "never polled" in feedline.detail and "buses" in feedline.detail


def test_production_no_feed_has_ever_polled_is_fail():
    # The other side of that line: EVERY feed unpolled is not a tolerated subset, it
    # means the cache never populated at all, which really is a broken startup.
    feeds_map = {"subway": {"age_s": None}, "buses": {"age_s": None}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.FAIL
    assert "ever polled" in feedline.detail


@pytest.mark.parametrize("age", ["recent", True, [], {}], ids=["string", "bool", "list", "dict"])
def test_production_feed_with_a_nonnumeric_age_is_fail(age):
    # Present but not a number: the field changed shape, a real payload regression
    # (distinct from the null above). True is excluded explicitly because bool is a
    # subclass of int and would otherwise read as age 1.
    feeds_map = {"subway": {"age_s": 5.0}, "buses": {"age_s": age}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.FAIL
    assert "buses" in feedline.detail


def test_production_negative_feed_age_is_fail_not_silently_fresh():
    # REVIEW FIX: a negative age means fetched_at is AHEAD of served_at (a clock step
    # or a restored backup). Without an explicit check it passes every band and reads
    # as fresh, which is the monitor going BLIND rather than loud.
    feeds_map = {"subway": {"age_s": 5.0}, "buses": {"age_s": -4000.0}}
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(feeds=feeds_map)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    feedline = next(r for r in results if r.name == "production:feeds")
    assert feedline.status == cm.FAIL
    assert "buses" in feedline.detail


def test_production_degraded_alerts_is_warn_while_alerts_are_retained():
    # Still a WARN: the backend carries a down system's last-known alerts forward,
    # so riders keep seeing them and the outage is covered.
    alerts = {
        "age_s": 30.0,  # the poll itself is healthy; the per-system fields are what vary
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
        "age_s": 30.0,  # the poll itself is healthy; the per-system fields are what vary
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
        "age_s": 30.0,  # the poll itself is healthy; the per-system fields are what vary
        "degraded_systems": ["MNR"],
        "systems": {"MNR": {"fresh_at": 100.0, "retained_since": None, "last_error": {}}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=2000.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL and "MNR" in alertline.detail


def test_production_total_alerts_outage_is_fail_not_pass():
    # C1b REWROTE THIS TEST, which had stopped guarding anything real. It used to build
    # degraded_systems: [] with every per-system last_error null, on the premise that
    # the app froze its health map during a total outage. C1 removed that premise: the
    # app now marks every system failed, so this test was pinning a payload no
    # deployment can emit any more, while reading like the total-outage regression test.
    #
    # The invariant that still matters is the ordering: a poll that has not succeeded
    # in a day is a FAIL on the POLL AGE, before and regardless of anything the
    # per-system fields say. So the payload here is the one the app really produces.
    alerts = {
        "age_s": 99000.0,  # the poll itself has not succeeded in a day
        "last_error": {"status": 502, "detail": "alert feed unavailable"},
        "degraded_systems": ["LIRR", "MNR", "bus", "ferry", "subway"],
        "systems": {
            s: {"fresh_at": 1000.0, "retained_since": 1000.0, "last_error": {"status": 502}}
            for s in ("subway", "bus", "LIRR", "MNR", "ferry")
        },
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=100000.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL
    assert "not being refreshed" in alertline.detail  # names the real problem
    # And it POINTS AT the per-system detail rather than telling the operator to
    # distrust it: naming which feeds are down is the fastest route to a diagnosis.
    assert "degraded: LIRR, MNR, bus, ferry, subway" in alertline.detail


def test_production_stale_poll_still_fails_when_no_system_is_degraded():
    # The poll-age check must not become conditional on the per-system data. A poller
    # that has stopped running leaves BOTH frozen, so a stale poll age with an empty
    # degraded list is exactly the shape that needs catching, and it must still FAIL
    # (just without a degraded list to name).
    alerts = {
        "age_s": 99000.0,
        "degraded_systems": [],
        "systems": {"subway": {"fresh_at": 1000.0, "retained_since": None, "last_error": None}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=100000.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL
    assert "not being refreshed" in alertline.detail
    assert "degraded:" not in alertline.detail  # nothing to name, so nothing claimed


def test_production_never_polled_with_every_system_degraded_is_fail():
    # C1b ADDITION. A never-polled index is normally a warming deployment (WARN). But
    # never-polled AND every system degraded is a deployment that has never once
    # reached an alert feed (revoked key, DNS failure, rotated URLs). That cannot heal
    # on its own, so it must not sit at WARN indefinitely. This shape only became
    # observable once C1 made the app write health on the never-filled path.
    alerts = {
        "age_s": None,  # never polled
        "degraded_systems": ["LIRR", "MNR", "bus", "ferry", "subway"],
        "systems": {
            s: {"fresh_at": None, "retained_since": None, "last_error": {"status": 502}}
            for s in ("subway", "bus", "LIRR", "MNR", "ferry")
        },
    }
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(alerts=alerts)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL
    assert "never reached an alert feed" in alertline.detail


def test_production_never_polled_while_still_warming_is_only_warn():
    # The other side of that line, and the reason the escalation is conditional: a
    # genuinely warming deployment reports no per-system errors yet. It must stay WARN,
    # or every cold start would go red. An older deployment predating C1 reports this
    # same shape during a total outage, so a rollout cannot raise a false alarm either.
    alerts = {
        "age_s": None,
        "degraded_systems": [],
        "systems": {
            s: {"fresh_at": None, "retained_since": None, "last_error": None}
            for s in ("subway", "bus", "LIRR", "MNR", "ferry")
        },
    }
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(alerts=alerts)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN
    assert "never polled" in alertline.detail


def test_production_degraded_system_that_never_decoded_is_fail():
    # REVIEW FIX (high): a system broken since process start has fresh_at = null, so
    # the isinstance guard skipped exactly that case and left it permanently WARN,
    # unable to ever escalate. There are no retained alerts protecting riders here
    # and no retention clock either, so it is the worst state to be lenient about.
    alerts = {
        "age_s": 30.0,
        "degraded_systems": ["ferry"],
        "systems": {"ferry": {"fresh_at": None, "retained_since": None, "last_error": {}}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=2000.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL
    assert "never decoded" in alertline.detail


def test_production_missing_alerts_object_is_fail():
    # The app always emits this object; PASS here would report health never checked.
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(alerts=None)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.FAIL


def test_production_alerts_without_served_at_do_not_fail_on_age():
    # No served_at means no skew-free way to age the deployment's timestamps, and a
    # monitor must not FAIL on a number it cannot compute honestly. It degrades to
    # the WARN the degraded list alone justifies (an older backend, or a proxy that
    # stripped the field, must not turn the run red on arithmetic it cannot do).
    alerts = {
        "age_s": 30.0,  # healthy poll age, so the check reaches the served_at logic
        "degraded_systems": ["LIRR"],
        "systems": {"LIRR": {"fresh_at": 1.0, "retained_since": 1.0}},  # ancient
    }
    fetch = FakeFetcher({"https://app.example/api/status": _status_json(alerts=alerts)})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN
    # Pin the BRANCH, not just the status: this must be the "could not check" WARN,
    # naming the missing field, and not the ordinary retained-grace WARN. Otherwise a
    # regression that silently aged against the runner clock would still read WARN
    # here and the test would not notice.
    assert "served_at missing" in alertline.detail
    assert "LIRR" in alertline.detail


def test_production_alert_ages_use_served_at_not_the_runner_clock():
    # fresh_at and retained_since are stamped by the DEPLOYMENT, so they must be
    # compared against the payload's own served_at. Same-clock pairs only, matching
    # the app's own freshness discipline.
    #
    # The runner clock here is deliberately FAR AHEAD of the deployment's, which is
    # what makes this test non-vacuous: any substitution of `now` for served_at
    # computes an age of ~5e6s, blows past the 1800s horizon, and FAILs. (An
    # earlier version of this test put the deployment stamps in the runner's future,
    # so mixing clocks produced a NEGATIVE age that sailed under the horizon and the
    # test passed either way.)
    alerts = {
        "age_s": 30.0,  # healthy poll age, so the check reaches the served_at logic
        "degraded_systems": ["LIRR"],
        "systems": {"LIRR": {"fresh_at": 100.0, "retained_since": 100.0}},
    }
    fetch = FakeFetcher(
        {"https://app.example/api/status": _status_json(alerts=alerts, served_at=200.0)}
    )
    results = cm.check_production(fetch, NO_SLEEP, 5_000_000.0, "https://app.example")
    alertline = next(r for r in results if r.name == "production:alerts")
    assert alertline.status == cm.WARN  # 100s of retention, nowhere near the horizon
    assert "still retained" in alertline.detail  # the grace message, not an expiry
    assert "coverage lost" not in alertline.detail


def test_production_non_200_is_fail():
    fetch = FakeFetcher({"https://app.example/api/status": 502})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert results[0].status == cm.FAIL


def test_production_non_json_is_fail():
    fetch = FakeFetcher({"https://app.example/api/status": b"<html>oops</html>"})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    assert results[0].status == cm.FAIL


# ---------------------------------------------------------------------------
# NJ Transit static (credentialed, mint-conserving)
# ---------------------------------------------------------------------------
#
# The check has one property no other check here has: it SPENDS something. NJ
# Transit issues ten tokens per account per Eastern day (njt_auth.DAILY_MINT_LIMIT,
# observed 2026-09-02) and this job shares the account with production, so "exactly
# one mint per run" is asserted directly rather than trusted, and the WARN-skip with
# no credentials is asserted to make no request at all.

NJT_TOKEN = "https://njt.example/getToken"
NJT_DATA = "https://njt.example/getGTFS"

# The probe's exact invalid-token response (2026-08-05). Written out here rather
# than imported from the simulator: this is the hermetic tier, and the two are
# supposed to be able to fail independently.
NJT_INVALID_TOKEN = b'{"errorMessage":"Invalid token."}'

_NJT_ROUTES = (
    "route_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n"
    + "".join(f"{i},R{i},Line {i},113,EF3E42,\n" for i in range(1, 13))
)
_NJT_STOPS = "stop_id,stop_code,stop_name,stop_lat,stop_lon\n" + "".join(
    f"{i},C{i},Station {i},40.7{i:03d},-74.0{i:03d}\n"
    for i in range(1, 200)
    if i != 109 and i != 112
)
_NJT_IDENTITY = (
    "109,NY,New York Penn Station,40.750568,-73.993519\n"
    "112,NP,Newark Penn Station,40.734924,-74.164581\n"
)
_NJT_TRIPS = (
    "route_id,service_id,trip_id,trip_headsign,direction_id,trip_short_name\n1,S,T1,NY,0,3800\n"
)
_NJT_STOP_TIMES = (
    "trip_id,arrival_time,departure_time,stop_id,stop_sequence\nT1,07:00:00,07:00:00,109,1\n"
)
_NJT_AGENCY = (
    "agency_id,agency_name,agency_url,agency_timezone\nNJT,NJ TRANSIT,https://x,America/New_York\n"
)
_NJT_SHAPES = "shape_id,shape_pt_sequence,shape_pt_lat,shape_pt_lon\ns1,1,40.7,-74.0\n"

NJT_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=feeds.NYC_TZ).timestamp()


def _njt_zip(*, last_service="20270501", **overrides):
    """An NJT-shaped archive: no calendar.txt, no feed_info.txt, route_type 113."""
    members = {
        "agency.txt": _NJT_AGENCY,
        "routes.txt": _NJT_ROUTES,
        "stops.txt": _NJT_STOPS + _NJT_IDENTITY,
        "trips.txt": _NJT_TRIPS,
        "stop_times.txt": _NJT_STOP_TIMES,
        "calendar_dates.txt": f"service_id,date,exception_type\nS,{last_service},1\n",
        "shapes.txt": _NJT_SHAPES,
    }
    members.update(overrides)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            if body is not None:
                zf.writestr(name, body)
    return buf.getvalue()


def _njt_token(token="tok"):
    return json.dumps({"UserToken": token}).encode()


def _njt_fetch(**overrides):
    mapping = {NJT_TOKEN: _njt_token(), NJT_DATA: _njt_zip()}
    mapping.update(overrides)
    return FakeFetcher(mapping)


def _check_njt(fetch, now=NJT_NOW, user="rider", password="secret"):
    return cm.check_njt_static(
        fetch, NO_SLEEP, now, user, password, token_url=NJT_TOKEN, url=NJT_DATA
    )


# ---------------------------------------------------------------------------
# NJ Transit realtime (15b): the shared mint, the bands, and the WATCHED ratio
# ---------------------------------------------------------------------------

NJT_TU = "https://njt.example/getTripUpdates"
NJT_ALERTS = "https://njt.example/getAlerts"

# The static tables check_njt_realtime joins against, in the shape check_njt_static
# returns. Three stops so a trip can straddle a middle one.
NJT_RT_STOPS = {
    "109": {"id": "109", "name": "New York Penn Station", "lat": 40.750568, "lon": -73.993519},
    "112": {"id": "112", "name": "Newark Penn Station", "lat": 40.734924, "lon": -74.164581},
    "38": {"id": "38", "name": "Hoboken", "lat": 40.734984, "lon": -74.027683},
}
NJT_RT_TRIPS = {
    f"T{i}": {"route_id": "1", "headsign": "New York", "short_name": f"{3800 + i}"}
    for i in range(60)
}
NJT_RT_PARSED = {"stops": NJT_RT_STOPS, "trips": NJT_RT_TRIPS}

# 2026-08-06 18:15 EDT, the rush probe's own hour and comfortably inside service.
NJT_RT_NOW = datetime(2026, 8, 6, 18, 15, tzinfo=feeds.NYC_TZ).timestamp()
# 03:00 EDT, outside the 05:00-01:30 window.
NJT_RT_CLOSED = datetime(2026, 8, 6, 3, 0, tzinfo=feeds.NYC_TZ).timestamp()


def _njt_tu(
    count=30,
    *,
    header_ts=None,
    straddling=True,
    entity_ids=True,
    prefix="T",
    canceled=0,
    canceled_skips_stops=True,
):
    """A TripUpdates feed of `count` trips, each with a passed call and a future one.

    `straddling=False` PRUNES the passed calls, which is exactly what "NJ Transit
    stopped retaining passed stops" looks like on the wire and is the failure the
    watched ratio exists to catch.
    """
    now = NJT_RT_NOW
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(header_ts if header_ts is not None else now)
    for i in range(count):
        entity = feed.entity.add()
        entity.id = f"{3800 + i}" if entity_ids else f"x{i}"
        tu = entity.trip_update
        tu.trip.trip_id = f"{prefix}{i}"
        tu.trip.route_id = "1"
        if i < canceled:
            # The probe's phantom shape: canceled at the trip level, every stop
            # marked SKIPPED and still carrying full plausible times.
            tu.trip.schedule_relationship = pb.TripDescriptor.CANCELED
        calls = [("112", 300)] if not straddling else [("38", -300), ("112", 300)]
        for seq, (stop_id, offset) in enumerate(calls):
            stu = tu.stop_time_update.add()
            stu.stop_sequence = (seq + 1) * 10
            stu.stop_id = stop_id
            if i < canceled and canceled_skips_stops:
                stu.schedule_relationship = pb.TripUpdate.StopTimeUpdate.SKIPPED
            stu.arrival.time = int(now + offset)
            stu.departure.time = int(now + offset + 30)
    return feed.SerializeToString()


def _njt_rt_fetch(**overrides):
    mapping = {
        NJT_TOKEN: _njt_token(),
        NJT_TU: _njt_tu(),
        NJT_ALERTS: _rt_feed(),
    }
    mapping.update(overrides)
    return FakeFetcher(mapping)


def _check_njt_rt(fetch, now=NJT_RT_NOW, parsed=NJT_RT_PARSED, user="rider", password="secret"):
    return cm.check_njt_realtime(
        fetch,
        NO_SLEEP,
        now,
        parsed,
        user,
        password,
        token_url=NJT_TOKEN,
        tu_url=NJT_TU,
        alerts_url=NJT_ALERTS,
    )


def test_njt_realtime_skipped_without_credentials():
    """The same WARN-skip check_njt_static and check_bus_realtime take, and the
    same reason it must reach NO endpoint: every fork and pull request context
    arrives here with the secrets resolved to empty strings."""
    fetch = FakeFetcher({})
    result = _check_njt_rt(fetch, user="", password="")
    assert result.status == cm.WARN
    assert "not set" in result.detail
    assert not fetch.calls, "credentials-absent must reach no NJ Transit endpoint at all"


def test_njt_realtime_placeholder_credentials_are_treated_as_absent():
    """The .env.example values copied verbatim. Re-deriving "configured" as a
    truthiness test would send a doomed mint to the live endpoint on every run."""
    result = _check_njt_rt(FakeFetcher({}), user="your-njt-username", password="your-njt-password")
    assert result.status == cm.WARN


def test_njt_realtime_healthy_passes_with_exactly_one_mint():
    fetch = _njt_rt_fetch()
    result = _check_njt_rt(fetch)
    assert result.status == cm.PASS, result.detail
    assert "30 trip updates" in result.detail
    assert "in service" in result.detail
    mints = [url for url, _h, _p in fetch.calls if url == NJT_TOKEN]
    assert len(mints) == 1, f"one mint per check, got {len(mints)}"
    # The token rode as a FORM FIELD on both realtime POSTs, which is the only
    # shape RailData answers.
    posted = [f for f in fetch.forms if f and "token" in f]
    assert len(posted) == 2, "both trip updates and alerts POST behind the token"


def test_njt_realtime_a_down_feed_fails():
    assert _check_njt_rt(_njt_rt_fetch(**{NJT_TU: 503})).status == cm.FAIL
    assert _check_njt_rt(_njt_rt_fetch(**{NJT_ALERTS: 503})).status == cm.FAIL


def test_njt_realtime_undecodable_trip_updates_fail():
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_TU: b"not-a-protobuf"}))
    assert result.status == cm.FAIL
    assert "undecodable" in result.detail


def test_njt_realtime_checks_the_alerts_feed_through_the_door():
    """THE OTHER HALF of check_alerts_realtime's deliberate exclusion.

    That check cannot reach this feed (its fetcher GETs; this endpoint answers
    only a POST carrying a token), so the exclusion is only safe if SOMEBODY
    checks it. This is that somebody, and this test is what stops the split from
    decaying into a gap: an undecodable alerts body has to fail HERE.
    """
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_ALERTS: b"not-a-protobuf-\xff"}))
    assert result.status == cm.FAIL
    assert "alerts undecodable" in result.detail
    assert "njt" not in cm.feeds.KEYLESS_ALERT_FEEDS, "and the GET check still excludes it"


def test_njt_realtime_empty_feed_is_a_warn_in_service_and_fine_when_closed():
    """Decoder law 6's 13-byte valid feed, judged against the clock.

    Zero trains at 03:00 is the correct answer, so an empty feed outside service
    hours is a PASS. Inside them it means trains that should be running are not
    being published, which is worth a look and deliberately not a page.
    """
    empty = _rt_feed(header_ts=NJT_RT_NOW)
    assert _check_njt_rt(_njt_rt_fetch(**{NJT_TU: empty})).status == cm.WARN

    closed = _rt_feed(header_ts=NJT_RT_CLOSED)
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_TU: closed}), now=NJT_RT_CLOSED)
    assert result.status == cm.PASS
    assert "closed" in result.detail


def test_njt_realtime_header_lag_bands():
    """The two edges derived from the PEAK probe (9s to 23s observed), never the
    overnight sample the probe called optimistic by roughly 2x."""
    fresh = _njt_tu(header_ts=NJT_RT_NOW - 20)  # inside the worst observed peak lag
    assert _check_njt_rt(_njt_rt_fetch(**{NJT_TU: fresh})).status == cm.PASS

    lagging = _njt_tu(header_ts=NJT_RT_NOW - 200)
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_TU: lagging}))
    assert result.status == cm.WARN
    assert "behind" in result.detail

    dead = _njt_tu(header_ts=NJT_RT_NOW - 900)
    assert _check_njt_rt(_njt_rt_fetch(**{NJT_TU: dead})).status == cm.FAIL


def test_njt_realtime_warns_when_the_feed_stops_retaining_passed_stops():
    """DIRECTIVE 1, AND THE POINT OF THIS WHOLE CHECK.

    feeds.njt._place assumes this feed RETAINS ALREADY-PASSED STOPS; placement is
    built on it and neither probe measured it directly. If NJ Transit ever starts
    pruning them, every running train arrives at the not-yet-departed branch where
    MAX_FUTURE_FIRST_STOP_S (180s) drops anything further out than three minutes,
    and the map thins out while the boards stay full.

    THE SIGNATURE IS ZERO STRADDLING TRIPS in a busy feed, and that needs no
    invented percentage: a trip with a call behind and a call ahead is the feed's
    own statement that the train is running this minute, so a feed full of trips
    where NONE straddles has stopped saying where trains have been.

    A detectable failure signature belongs to the monitor, not to a future rider's
    confusion.
    """
    pruned = _njt_tu(count=30, straddling=False)
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_TU: pruned}))
    assert result.status == cm.WARN, result.detail
    assert "retaining passed stops" in result.detail
    assert "_place" in result.detail or "njt._place" in result.detail


def test_njt_realtime_warns_when_running_trips_stop_being_placed(monkeypatch):
    """The second half of the watched ratio: trips the feed says are running that
    the decoder did not place.

    THIS IS DRIVEN BY MONKEYPATCHING THE DECODER, and that is the finding rather
    than a shortcut. Once the denominator drops exactly what the decoder drops (see
    the test below), there is NO well-formed feed that straddles `now` and fails to
    place: a call behind and a call ahead, both at known stops, both carrying
    times, lands in _place's dwelling case or its interpolate-between case, always.
    That unreachability IS NJT_PLACEMENT_FLOOR's derivation, stated as a test
    rather than only as a comment.

    So what this branch guards is not an input, it is a REGRESSION: a future change
    to _place that starts dropping running trains. Simulating that directly is the
    only honest way to exercise it, and the 95% floor is the headroom that keeps an
    ordinary poll from tripping it.
    """
    real = cm.njt_feed.decode_njt_trip_updates

    def half(*args, **kwargs):
        trains, arrivals, ts, warnings = real(*args, **kwargs)
        return trains[: len(trains) // 2], arrivals, ts, warnings

    monkeypatch.setattr(cm.njt_feed, "decode_njt_trip_updates", half)
    result = _check_njt_rt(_njt_rt_fetch())
    assert result.status == cm.WARN, result.detail
    assert "running trips placed" in result.detail
    assert "15/30" in result.detail, result.detail


def test_the_straddling_denominator_drops_exactly_what_the_decoder_drops(monkeypatch):
    """THE RATIO'S TWO SIDES MUST FILTER IDENTICALLY, or the WARN is arithmetic
    rather than news.

    NJT_PLACEMENT_FLOOR's derivation claims the healthy placement rate is 1.0 "by
    construction, not an estimate". That is only true if the denominator drops the
    same calls feeds.njt._ordered_calls does, and it drops a call for EITHER an
    unknown stop_id or a relationship in _DROP_STOP_RELATIONSHIPS, which is SKIPPED
    AND NO_DATA.

    Both mismatches are reachable on real bytes. njt_static drops a stops.txt row
    whose coordinates will not parse, so the realtime feed can name a stop the
    static table lacks; and this producer's habit is relationships that still carry
    times (238 SKIPPED-with-times a peak poll), so a NO_DATA with times is its
    shape, not a hypothetical. Either one used to be counted as running here and
    dropped by the decoder, pushing the ratio under the floor and blaming an
    assumption that had not changed.
    """
    now = NJT_RT_NOW
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(now)

    def add(trip_id, stop_behind, rel=None):
        entity = feed.entity.add()
        entity.id = trip_id
        tu = entity.trip_update
        tu.trip.trip_id = trip_id
        for stop_id, offset in ((stop_behind, -300), ("109", 300)):
            stu = tu.stop_time_update.add()
            stu.stop_id = stop_id
            if stop_id == stop_behind and rel is not None:
                stu.schedule_relationship = rel
            stu.arrival.time = int(now + offset)
            stu.departure.time = int(now + offset + 30)

    add("RUNNING", "112")  # the control: a real straddle
    add("UNKNOWN_STOP", "99999")  # behind-call at a stop the static does not have
    add("NO_DATA", "112", pb.TripUpdate.StopTimeUpdate.NO_DATA)
    add("SKIPPED", "112", pb.TripUpdate.StopTimeUpdate.SKIPPED)

    straddling = cm._njt_straddling_trips(feed, now, NJT_RT_STOPS)
    assert straddling == {"RUNNING"}, (
        f"only the trip with a usable call behind it is running; got {sorted(straddling)}"
    )
    # And the decoder agrees, which is the whole claim: same input, same drops.
    trains, _arrivals, _ts, _w = cm.njt_feed.decode_njt_trip_updates(
        feed.SerializeToString(), NJT_RT_STOPS, NJT_RT_TRIPS, now
    )
    placed = {t["trip_id"] for t in trains} & straddling
    assert placed == {"RUNNING"}, (
        "every trip the monitor calls running must be one the decoder places, or the "
        f"ratio warns about its own arithmetic; placed {sorted(placed)}"
    )


def test_njt_realtime_does_not_judge_placement_on_a_thin_feed():
    """Below NJT_MIN_TRIPS_FOR_RATIO the ratio is not computed at all: a quiet
    stretch must not produce a percentage derived from three trips."""
    thin = _njt_tu(count=3, straddling=False)
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_TU: thin}))
    assert result.status == cm.PASS, result.detail


def test_njt_realtime_does_not_judge_placement_without_the_static_tables():
    """A failed static check must not surface as a realtime placement WARN. Its
    own result already says why the archive is unavailable, and blaming the
    realtime feed for it would point the reader at the wrong upstream."""
    result = _check_njt_rt(_njt_rt_fetch(), parsed=None)
    assert result.status == cm.PASS
    assert "placement not checked" in result.detail


def test_njt_realtime_warns_on_entity_id_cross_check_drift():
    """The 745-of-745 agreement feeds.njt._identity is built on. Nothing is served
    wrong when it drifts, so this is a WARN; but a drift that STARTS is worth a
    human deciding about rather than a log line nobody reads."""
    drifted = _njt_tu(count=30, entity_ids=False)
    result = _check_njt_rt(_njt_rt_fetch(**{NJT_TU: drifted}))
    assert result.status == cm.WARN
    assert "entity.id" in result.detail


@pytest.mark.parametrize(
    "hour,minute,expected",
    [
        (5, 0, True),  # first trains, the window opens
        (12, 0, True),
        (18, 15, True),  # the rush probe's hour
        (1, 30, True),  # last trains still reaching terminals, past midnight
        (3, 0, False),  # the quiet middle
        (4, 59, False),
    ],
)
def test_njt_service_hours_wrap_midnight(hour, minute, expected):
    """The window is 05:00 to 01:30 and therefore WRAPS, unlike the ferry's. A
    simple start <= t <= end test would call 01:30 closed while the last trains
    are still running and 03:00 in service while nothing is."""
    when = datetime(2026, 8, 6, hour, minute, tzinfo=feeds.NYC_TZ).timestamp()
    assert cm._in_njt_service_hours(when, feeds.NYC_TZ) is expected


def test_run_all_mints_exactly_one_token_for_both_njt_checks():
    """THE CONSERVATION CLAIM, made where it actually has to hold: run_all.

    By 15b three RailData consumers run in one pass (the static archive, the
    realtime trip updates, and the realtime alerts). Minting is rate-limited below
    the data cap at ten a day per account (njt_auth.DAILY_MINT_LIMIT, observed
    2026-09-02), and tokens are product-scoped so we cannot read our own usage.
    Production shares that account, so this job overspending takes NJ Transit dark
    for RIDERS, not just for the run. A wiring mistake that let each check mint its
    own would leave every check PASSING and quietly triple what the 6-hourly
    schedule spends, which is exactly the kind of defect no per-check test can see.

    Asserted against run_all rather than either check, because run_all is the only
    place the sharing is expressed.
    """
    calls = []

    def fetch(url, headers=None, params=None, files=None):
        calls.append(url)
        if url == cm.njt_auth.NJT_TOKEN_URL:
            return cm.FetchResult(200, _njt_token())
        if url == cm.njt_static.NJT_STATIC_URL:
            return cm.FetchResult(200, _njt_zip())
        if url == cm.njt_feed.NJT_TU_URL:
            # A PREFIX THAT CANNOT COLLIDE with the 15a archive fixture, which knows
            # exactly one trip (T1, short name 3800). A colliding id carrying a
            # different short name would raise the entity.id cross-check WARN, which
            # would be the check working correctly and would say nothing about the
            # mint arithmetic this test is here for.
            return cm.FetchResult(200, _njt_tu(prefix="X"))
        if url == cm.njt_feed.NJT_ALERTS_URL:
            return cm.FetchResult(200, _rt_feed())
        # Every other upstream fails; this test is only about the mint count.
        return cm.FetchResult(500, b"")

    results = cm.run_all(
        fetch,
        NO_SLEEP,
        NJT_RT_NOW,
        env={
            "NJT_USERNAME": "rider",
            "NJT_PASSWORD": "secret",
            "MONITOR_SKIP_PRODUCTION": "1",
        },
    )
    mints = [url for url in calls if url == cm.njt_auth.NJT_TOKEN_URL]
    assert len(mints) == 1, (
        f"one token for the whole run, got {len(mints)}. Each NJ Transit check "
        "minting its own would pass every other test here and triple what the "
        "schedule spends out of the account's ten a day."
    )
    # Non-vacuous: all three consumers really did fetch behind that one token.
    assert cm.njt_static.NJT_STATIC_URL in calls
    assert cm.njt_feed.NJT_TU_URL in calls
    assert cm.njt_feed.NJT_ALERTS_URL in calls
    by_name = {r.name: r for r in results}
    assert by_name["njt-static"].status == cm.PASS, by_name["njt-static"].detail
    assert by_name["njt-realtime"].status == cm.PASS, by_name["njt-realtime"].detail


def test_run_all_a_failed_mint_fails_both_njt_checks_with_the_same_reason():
    """THE OTHER HALF OF THE CONSERVATION CLAIM, and the half a mint counter cannot
    make. One token per run is only correct if the FAILURE is shared too: a run
    whose mint is refused must fail both NJ Transit checks off that one refusal,
    rather than the second check quietly trying again on its own.

    The assertion is therefore the count AND the two details together. Each check
    minting for itself would produce two getToken POSTs here and could produce two
    different-looking failures, which is the shape that turns one refused mint into
    two spent attempts against a budget of ten a day.

    A REFUSED BUDGET IS THE RIGHT REFUSAL TO TEST IT WITH, because it is the one
    where a second attempt is worst: it is charged to the same ten it is waiting on.
    _NjtToken remembers the failure rather than re-minting, and both checks read
    that memory.
    """
    calls = []

    def fetch(url, headers=None, params=None, files=None):
        calls.append(url)
        if url == cm.njt_auth.NJT_TOKEN_URL:
            return cm.FetchResult(500, NJT_QUOTA_REFUSAL)
        return cm.FetchResult(500, b"")

    results = cm.run_all(
        fetch,
        NO_SLEEP,
        NJT_RT_NOW,
        env={
            "NJT_USERNAME": "rider",
            "NJT_PASSWORD": "secret",
            "MONITOR_SKIP_PRODUCTION": "1",
        },
    )
    mints = [url for url in calls if url == cm.njt_auth.NJT_TOKEN_URL]
    assert len(mints) == 1, (
        f"one refused mint must be remembered, not repeated, got {len(mints)} getToken POSTs"
    )
    by_name = {r.name: r for r in results}
    expected = f"mint failed ({cm.njt_auth.MINT_QUOTA_MESSAGE})"
    assert by_name["njt-static"].status == cm.FAIL
    assert by_name["njt-realtime"].status == cm.FAIL
    assert by_name["njt-static"].detail == expected
    assert by_name["njt-realtime"].detail == expected, (
        "both checks must fail off the SAME refusal; a second, differently worded "
        "failure means the realtime check minted for itself"
    )
    # Non-vacuous: neither check went on to fetch behind a token it never got.
    assert cm.njt_static.NJT_STATIC_URL not in calls
    assert cm.njt_feed.NJT_TU_URL not in calls
    assert cm.njt_feed.NJT_ALERTS_URL not in calls
    # And NJ Transit alone is dark. Every other upstream in this run is answering
    # 500 too, so the useful claim is the reverse one: nothing about the shared
    # token stopped the other checks from running and reporting for themselves.
    assert {"subway-static", "railroad-static", "alerts-realtime"} <= set(by_name)


def test_run_all_does_not_mint_at_all_without_credentials():
    """The other side: a run with no credentials must not POST getToken even once.

    _NjtToken is built lazily precisely so this holds. Both checks WARN-skip before
    reaching it, which is what keeps a fork or pull request context, where GitHub
    resolves an unavailable secret to an empty string, from touching NJ Transit.
    """
    fetch = FakeFetcher({})
    fetch.mapping = {}

    def permissive(url, headers=None, params=None, files=None):
        fetch.calls.append((url, headers, params))
        fetch.forms.append(files)
        return cm.FetchResult(500, b"")

    results = cm.run_all(permissive, NO_SLEEP, NJT_RT_NOW, env={"MONITOR_SKIP_PRODUCTION": "1"})
    njt_calls = [url for url, _h, _p in fetch.calls if "njt" in url.lower()]
    assert njt_calls == [], f"no credentials must mean no NJ Transit traffic, got {njt_calls}"
    by_name = {r.name: r for r in results}
    assert by_name["njt-static"].status == cm.WARN
    assert by_name["njt-realtime"].status == cm.WARN


def test_production_reports_a_dark_njt_layer():
    """THE ONLY PLACE A DARK NJ TRANSIT LAYER IS VISIBLE, so it gets its own test.

    check_njt_static probes the RailData API from the RUNNER and says nothing about
    the deployment; when the runner has no credentials it WARN-skips, and a WARN
    never fails a run. So a production instance whose NJT credentials were revoked,
    or whose publication is stuck, is seen by exactly one line: the static-group map
    in check_production. It was still the pre-15a four-tuple, which reported "all
    static groups ready" over njt_static="failed".
    """
    for state in ("failed", "loading", None):
        body = _status_json(njt_static=state)
        fetch = FakeFetcher({"https://app.example/api/status": body})
        results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
        statics = [r for r in results if r.name == "production:statics"]
        assert len(statics) == 1
        assert statics[0].status == cm.FAIL, f"njt_static={state!r} must not read as ready"
        assert "njt_static" in statics[0].detail


def test_production_accepts_a_deliberately_unconfigured_njt():
    """The other side, and the reason the map is a map rather than a longer tuple.

    "not-configured" means the deployment was given no NJ Transit credentials: it
    makes no network call at all, nothing is failing, and nothing is retrying.
    Failing on it would paint every deployment that does not run NJT permanently
    red, which is how a monitor teaches its operator to ignore it.
    """
    body = _status_json(njt_static="not-configured")
    fetch = FakeFetcher({"https://app.example/api/status": body})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
    statics = [r for r in results if r.name == "production:statics"]
    assert statics[0].status == cm.PASS, statics[0].detail


def test_production_still_fails_a_dark_layer_in_any_other_group():
    """The map must not have loosened anything else on its way in: only NJT gained
    a second acceptable state."""
    for field in ("subway_static", "railroad_static", "path_static", "ferry_static"):
        body = _status_json(**{field: "not-configured"})
        fetch = FakeFetcher({"https://app.example/api/status": body})
        results = cm.check_production(fetch, NO_SLEEP, 1000.0, "https://app.example")
        statics = [r for r in results if r.name == "production:statics"]
        assert statics[0].status == cm.FAIL, f"{field} has no not-configured state"


def test_njt_static_skipped_without_credentials():
    """The bus precedent: a credentialed feed the monitor cannot reach is a config
    choice, not an outage. WARN, and provably no request."""
    fetch = FakeFetcher({})
    cases = [
        (None, "secret"),
        ("rider", None),
        (None, None),
        ("", ""),
        ("   ", "secret"),  # whitespace only
        # THE PLACEHOLDERS .env.example SHIPS. README step 2 says copy that file, and
        # contract_monitor imports env_seams (which runs load_dotenv), so an operator
        # who copied it and edited only the bus key hands these straight through. A
        # bare `if not username or not password` reads them as configured and POSTs
        # a doomed mint to the live getToken endpoint, out of the ten a day the
        # account gets, then reports the rejection as an NJ Transit outage rather
        # than the promised WARN-skip. Routing the guard through njt_auth.credentials
        # is what keeps the monitor's idea of "configured" identical to the app's,
        # by construction.
        ("your-njt-username", "your-njt-password"),
        ("realuser", "your-njt-password"),  # the realistic half-edit
    ]
    for user, password in cases:
        result, parsed = _check_njt(fetch, user=user, password=password)
        assert result.status == cm.WARN, f"{user!r}/{password!r} must WARN-skip"
        assert "not set" in result.detail
        assert parsed is None
    assert not fetch.calls, "an unconfigured monitor must never reach the network"


def test_njt_static_healthy_passes_with_exactly_one_mint():
    fetch = _njt_fetch()
    result, parsed = _check_njt(fetch)
    assert result.status == cm.PASS, result.detail
    assert parsed is not None and len(parsed["routes"]) == 12
    mints = [url for url, _h, _p in fetch.calls if url == NJT_TOKEN]
    assert len(mints) == 1, f"the monitor must mint exactly once per run, got {len(mints)}"
    # The credentials rode in the multipart form, never in the URL or the query.
    assert fetch.forms[0] == {"username": "rider", "password": "secret"}
    assert "secret" not in fetch.calls[0][0]
    # And the data request carried the minted token as a form field.
    assert fetch.forms[1] == {"token": "tok"}


class _StatusFetcher:
    """A fetcher that answers one url with a chosen (status, body) pair.

    FakeFetcher cannot express "a non-200 WITH a body" (an int means an empty
    body), and the body is the whole point here: it is the only place NJ Transit
    says why a mint was refused.
    """

    def __init__(self, url, status, body):
        self.url, self.status, self.body = url, status, body
        self.calls = []
        self.forms = []

    def __call__(self, url, headers=None, params=None, files=None):
        self.calls.append((url, headers, params))
        self.forms.append(files)
        if url != self.url:
            raise AssertionError(f"unexpected fetch of {url}")
        return cm.FetchResult(self.status, self.body)


# A canary of the token's own shape (~21 characters) riding in a getToken body.
# The F3 tests assert it appears NOWHERE in a detail, because the detail is written
# to the job summary and the Actions log, and on these paths the body may be the
# live token.
CANARY = "canary-tok-0123456789"

# Where a hostile redirect would send a body: a different origin from every URL in
# this file, so a followed redirect is a recorded request naming this host.
ELSEWHERE = "https://elsewhere.example/collect"

# The daily-cap refusal, observed 2026-09-02: HTTP 500 with an errorMessage that
# BEGINS "Daily usage limit". The canary rides in the tail, which is the part the
# monitor must never repeat into a job summary.
NJT_QUOTA_REFUSAL = json.dumps(
    {"errorMessage": f"Daily usage limit of 10 reached. Token {CANARY} was the last."}
).encode()


def test_njt_static_mint_failure_fails_and_never_quotes_the_body():
    """INVERTED by Audit 4 (F3). This test used to assert the 401 body reached the
    operator, on the reasoning that it is the only place NJ Transit says why. For
    getToken that reasoning loses: the body may be the token, at any status, so the
    detail names the status and nothing else. And a failed mint is still not
    retried: that would be two of the day's ten mints."""
    fetch = _StatusFetcher(NJT_TOKEN, 401, json.dumps({"errorMessage": CANARY}).encode())
    result, parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert "mint failed (HTTP 401)" in result.detail
    assert CANARY not in result.detail, "the getToken body must never reach a detail"
    assert parsed is None
    assert len(fetch.calls) == 1, "a failed mint must not be retried"


@pytest.mark.parametrize(
    ("status", "body", "why"),
    [
        (
            200,
            json.dumps({"accessToken": CANARY}).encode(),
            "the token under a key extract_token does not recognize",
        ),
        (200, CANARY.encode(), "the token as a bare string that is not JSON"),
        (503, json.dumps({"errorMessage": CANARY}).encode(), "a non-200 carrying the token"),
    ],
)
def test_njt_static_the_gettoken_body_never_reaches_the_detail(status, body, why):
    """The same three shapes test_njt_auth pins at the mint boundary, driven through
    the monitor's own mint path, because the monitor builds its detail string
    itself for the non-200 case and via _sanitize for the other two."""
    fetch = _StatusFetcher(NJT_TOKEN, status, body)
    result, parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert "mint failed" in result.detail
    assert CANARY not in result.detail, why
    assert parsed is None
    assert len(fetch.calls) == 1, "a failed mint must not be retried"


def _redirecting_client():
    """A factory for a real httpx.Client over a mock transport that answers the
    original URL with a 307 to ELSEWHERE, answers ELSEWHERE with a 200, and records
    every request. A fetcher arm that follows shows up as a second recorded request
    whose URL names ELSEWHERE; one that does not shows exactly one."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "elsewhere.example":
            return httpx.Response(200, content=b"followed")
        return httpx.Response(307, headers={"Location": ELSEWHERE})

    return (lambda: httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_njt_static_a_spent_daily_budget_is_named_and_never_quoted():
    """THE REFUSAL THAT USED TO LOOK LIKE AN OUTAGE. NJ Transit issues ten tokens
    per account per Eastern day (observed 2026-09-02) and refuses the eleventh with
    an HTTP 500, which reached the job summary as "mint failed (HTTP 500)": exactly
    what a dead getToken endpoint says. A run at 06:17 therefore sent whoever read
    it hunting an NJ Transit outage when the real answer was that the account's
    mints were gone until midnight, and that the monitor's own four are a large
    share of them.

    The detail is njt_auth's constant, matched against njt_auth's literal prefix, so
    F3 is intact on this arm too: reading the body is not quoting it, and the canary
    in the refusal's tail must appear nowhere. And it is STILL not retried, which
    matters more here than anywhere else: a retry would be charged to the very
    budget the refusal is about."""
    fetch = _StatusFetcher(NJT_TOKEN, 500, NJT_QUOTA_REFUSAL)
    result, parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert result.detail == f"mint failed ({cm.njt_auth.MINT_QUOTA_MESSAGE})"
    assert "HTTP 500" not in result.detail, "a spent budget is not an anonymous 500"
    assert CANARY not in result.detail, "the getToken body must never reach a detail"
    assert parsed is None
    assert len(fetch.calls) == 1, "a refused mint must not be retried"


def test_njt_static_a_real_getToken_500_is_still_reported_by_status_alone():
    """THE CONTROL. Same status, a body that is not the refusal: getToken really is
    down, and the detail must say so rather than blaming a budget that has not been
    spent. Loosening the sniff to "any 500 from getToken" passes the test above and
    fails here, which is what makes that mutation detectable."""
    fetch = _StatusFetcher(NJT_TOKEN, 500, json.dumps({"errorMessage": CANARY}).encode())
    result, _parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert result.detail == "mint failed (HTTP 500)"
    assert cm.njt_auth.MINT_QUOTA_MESSAGE not in result.detail
    assert CANARY not in result.detail


def test_the_post_arm_never_follows_a_redirect():
    """Audit 4, F2, at the production fetcher: a POST whose body carries the
    credentials sends exactly ONE request when answered with a 307, at the original
    URL, and the 307 comes back as a status. Then the same fetcher under _NjtToken
    fails the mint on that status, with no second request anywhere."""
    factory, seen = _redirecting_client()
    fetch = cm.make_httpx_fetcher(client_factory=factory)
    res = fetch(NJT_TOKEN, files={"username": "rider", "password": CANARY})
    assert res.status == 307
    assert [str(request.url) for request in seen] == [NJT_TOKEN], (
        "exactly one request, at the original URL: a second would be the credentials "
        "delivered to whatever host the Location named"
    )
    assert CANARY.encode() in seen[0].content, "the request under test really carried the secret"

    minted, detail = cm._NjtToken(fetch, NJT_TOKEN, "rider", CANARY).get()
    assert minted is None
    assert "mint failed (HTTP 307)" in detail
    assert CANARY not in detail and ELSEWHERE not in detail
    assert [str(request.url) for request in seen] == [NJT_TOKEN, NJT_TOKEN]


def test_the_get_arm_still_follows_a_redirect():
    """THE CONTROL for the test above: the GET arm keeps following, because two
    static sources 30x to their zip and a GET carries nothing a redirect could
    deliver. Both arms through one seam, so the asymmetry is proved rather than
    assumed."""
    factory, seen = _redirecting_client()
    fetch = cm.make_httpx_fetcher(client_factory=factory)
    res = fetch("https://ferry.example/utility")
    assert res.status == 200
    assert res.content == b"followed"
    assert [str(request.url) for request in seen] == ["https://ferry.example/utility", ELSEWHERE]


def test_njt_static_mint_transport_failure_is_a_fail():
    def explodes(url, headers=None, params=None, files=None):
        raise ConnectionResetError("connection reset")

    result, parsed = _check_njt(explodes)
    assert result.status == cm.FAIL
    assert "mint failed" in result.detail
    assert parsed is None


def test_njt_static_a_token_response_with_no_token_fails():
    fetch = _njt_fetch(**{NJT_TOKEN: b'{"expires":3600}'})
    result, _parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert "mint failed" in result.detail


def test_njt_static_an_invalid_token_500_is_reported_as_unreachable():
    """THE PROBE'S EXACT BODY, not a bare 500, and the distinction is the test.

    An earlier version served a 500 with an EMPTY body, which is an ordinary outage
    and therefore could not tell "the monitor does not re-mint on an auth 500" from
    "the monitor does not re-mint on anything". Serving the real invalid-token body
    is what makes the assertion mean what it says.

    The monitor deliberately does NOT re-mint here: it has already spent its one
    mint, and a token that died between minting and fetching (seconds apart) is a
    real fault worth a human look rather than another of the day's ten mints.
    _fetch_retrying reports it as an unreachable upstream, which is a FAIL.
    """

    class _MintThenReject:
        """Mints normally, then answers every getGTFS with the probe's exact
        invalid-token 500. FakeFetcher cannot express a non-200 WITH a body."""

        def __init__(self):
            self.calls = []
            self.forms = []

        def __call__(self, url, headers=None, params=None, files=None):
            self.calls.append((url, headers, params))
            self.forms.append(files)
            if url == NJT_TOKEN:
                return cm.FetchResult(200, _njt_token())
            return cm.FetchResult(500, NJT_INVALID_TOKEN)

    fetch = _MintThenReject()
    result, parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert parsed is None
    mints = [url for url, _h, _p in fetch.calls if url == NJT_TOKEN]
    assert len(mints) == 1, "a rejected data fetch must never provoke a second mint"
    # The data POST was retried once (the house one-retry for a transient blip),
    # reusing the same token, so it cost no additional mint.
    assert [url for url, _h, _p in fetch.calls].count(NJT_DATA) == 2


def test_njt_static_missing_members_fail():
    """DROPS agency.txt, and the choice of member is the whole test.

    An earlier version dropped calendar_dates.txt, which njt_static._parse_zip
    OPENS: the check never reached _check_members at all, it short-circuited in the
    earlier "unparseable" arm on a KeyError, and NJT_REQUIRED_MEMBERS was wholly
    untested while the assertion appeared to pass. agency.txt is required by the
    monitor and opened by nothing, so it is the one member that can only be caught
    by the presence check.
    """
    fetch = _njt_fetch(**{NJT_DATA: _njt_zip(**{"agency.txt": None})})
    result, _parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert "missing members: agency.txt" in result.detail


def test_njt_static_a_missing_shapes_is_a_warn_not_a_fail():
    """shapes.txt is WATCHED, not required: njt_static deliberately does not parse
    it (15a defers geometry) and a hermetic test pins that a publication without it
    still serves. So upstream dropping it is worth a human look and must NOT exit
    the run non-zero, which is what listing it as required did: the 6-hourly
    schedule went red over a member the app was designed to survive losing."""
    fetch = _njt_fetch(**{NJT_DATA: _njt_zip(**{"shapes.txt": None})})
    result, parsed = _check_njt(fetch)
    assert result.status == cm.WARN, result.detail
    assert "shapes.txt" in result.detail
    assert parsed is not None, "a WARN must still return the parsed tables"


def test_njt_static_warns_when_the_feed_stops_being_flat():
    """The shape-change watch. njt_static treats this feed as FLAT on the strength
    of the probe; if parent stations or entrances appear, the parser correctly drops
    the non-boardable rows but "should the marker set become the PARENTS" is a
    design decision a human owes an answer to. Nothing else can see it: the stop
    floor is a lower bound, so 172 becoming 400 sails past, and the identity check
    still passes because a parent keeps the station's name."""
    grown = (
        "stop_id,stop_code,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
        + "".join(
            f"{i},C{i},Station {i},40.7{i:03d},-74.0{i:03d},0,\n"
            for i in range(1, 200)
            if i not in (109, 112)
        )
        + "109,NY,New York Penn Station,40.750568,-73.993519,0,\n"
        + "112,NP,Newark Penn Station,40.734924,-74.164581,0,\n"
    )
    fetch = _njt_fetch(**{NJT_DATA: _njt_zip(**{"stops.txt": grown})})
    result, _parsed = _check_njt(fetch)
    assert result.status == cm.WARN, result.detail
    assert "location_type" in result.detail and "parent_station" in result.detail


def test_njt_static_does_not_require_calendar_or_feed_info():
    """THE ACCEPTANCE CASE. Neither member exists in the real feed, and requiring
    either would FAIL this check on every valid publication forever."""
    body = _njt_zip()
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        assert "calendar.txt" not in zf.namelist()
        assert "feed_info.txt" not in zf.namelist()
    result, _parsed = _check_njt(_njt_fetch(**{NJT_DATA: body}))
    assert result.status == cm.PASS, result.detail


def test_njt_static_identity_stops_are_checked_by_name():
    """Presence alone proves nothing in a feed of small integer ids: stop 112 names
    four different places across our feeds, so an id that survived a renumbering
    pointing at the wrong station is exactly the drift worth catching."""
    renamed = _NJT_STOPS + (
        "109,NY,Somewhere Else,40.750568,-73.993519\n"
        "112,NP,Newark Penn Station,40.734924,-74.164581\n"
    )
    result, _parsed = _check_njt(_njt_fetch(**{NJT_DATA: _njt_zip(**{"stops.txt": renamed})}))
    assert result.status == cm.FAIL
    assert "109" in result.detail


def test_njt_static_thin_feeds_fail_their_floors():
    thin_routes = _NJT_ROUTES.splitlines()[0] + "\n1,R1,Line 1,113,EF3E42,\n"
    result, _parsed = _check_njt(_njt_fetch(**{NJT_DATA: _njt_zip(**{"routes.txt": thin_routes})}))
    assert result.status == cm.FAIL
    assert "routes" in result.detail


# --- the service-date band, at every edge -----------------------------------


@pytest.mark.parametrize(
    ("offset_days", "expected"),
    [
        (365, cm.PASS),
        (cm.NJT_SERVICE_WARN_DAYS, cm.PASS),  # exactly at the edge: not yet a warning
        (cm.NJT_SERVICE_WARN_DAYS - 1, cm.WARN),
        (1, cm.WARN),
        (0, cm.WARN),  # last service day is TODAY: still servable, but say so loudly
        (-1, cm.FAIL),  # expired yesterday
        (-400, cm.FAIL),
    ],
)
def test_njt_service_date_bands(offset_days, expected):
    """THE BAND THE VALIDATOR DELEGATES TO THIS CHECK. njt_static's guard draws the
    hard line (it refuses to promote a feed that leads today by nothing); this warns
    while the edge approaches, so a human sees it a month out rather than the
    morning it lands. Offset 0 is deliberately a WARN and not a FAIL: a feed whose
    last service day is today is running trains right now."""
    last = (datetime.fromtimestamp(NJT_NOW, feeds.NYC_TZ) + timedelta(days=offset_days)).strftime(
        "%Y%m%d"
    )
    status, detail = cm._njt_service_status(last, NJT_NOW, feeds.NYC_TZ)
    assert status == expected, detail


def test_njt_service_date_of_nothing_fails():
    status, detail = cm._njt_service_status(None, NJT_NOW, feeds.NYC_TZ)
    assert status == cm.FAIL
    assert "no service days" in detail


def test_njt_service_date_that_is_not_a_date_warns_rather_than_aborting():
    """Shaped right, not a date. Not this check's to police, and certainly not
    something to abort the whole monitor run over."""
    status, _detail = cm._njt_service_status("20261332", NJT_NOW, feeds.NYC_TZ)
    assert status == cm.WARN


def test_njt_static_expired_service_fails_the_whole_check():
    fetch = _njt_fetch(**{NJT_DATA: _njt_zip(last_service="20250101")})
    result, _parsed = _check_njt(fetch)
    assert result.status == cm.FAIL
    assert "service ended" in result.detail


# ---------------------------------------------------------------------------
# The workflow's own wiring for the credentialed check
# ---------------------------------------------------------------------------


def test_the_monitor_workflow_declares_the_environment_its_njt_secrets_live_in():
    """NJT_USERNAME and NJT_PASSWORD are ENVIRONMENT secrets on an environment named
    "monitor", not repository secrets, and that distinction has exactly one failure
    mode: environment secrets resolve only for a job that DECLARES the environment.
    Drop `environment: monitor` and the two `secrets.NJT_*` mappings quietly become
    empty strings, the njt-static check WARN-skips forever, and the run stays green
    while checking nothing.

    That is indistinguishable from a deliberate opt-out by design (the WARN-skip is
    the same either way), which is exactly why it needs a test rather than a
    reader's attention. The assertion is the COUPLING, not the presence of one line:
    a job that references NJT secrets must declare the environment, so removing
    either half fails here.

    Read from the workflow file rather than hardcoded, the same way
    test_static_warmup_retries_land_inside_the_healthcheck_window reads
    railway.json: a test that restates the config cannot notice the config changing.
    """
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/contract-monitor.yml").read_text()
    )
    job = workflow["jobs"]["monitor"]
    env_block = job["steps"][-1]["env"]

    njt_from_secrets = {
        name: value
        for name, value in env_block.items()
        if name.startswith("NJT_") and "secrets." in str(value)
    }
    assert set(njt_from_secrets) == {"NJT_USERNAME", "NJT_PASSWORD"}, (
        "the monitor job must map both NJ Transit credentials from secrets, or the "
        f"njt-static check can never run: got {sorted(njt_from_secrets)}"
    )
    assert job.get("environment") == "monitor", (
        "the monitor job maps NJT_* ENVIRONMENT secrets, so it must declare "
        "`environment: monitor`. Without the declaration those secrets resolve to "
        "empty strings and njt-static WARN-skips forever while the run stays green."
    )


def test_the_check_warn_skips_on_the_empty_strings_a_missing_secret_resolves_to():
    """The other half of the pair above, at the code boundary.

    GitHub renders an unavailable secret as an EMPTY STRING rather than leaving the
    variable unset, so "credentials absent" reaches run_all as "" and not as None. A
    guard written with `is None` would pass every test that hands it None and then
    run the real check with empty credentials in every fork and pull request
    context, which is the one place this must not happen.

    Driven through run_all rather than check_njt_static directly, because the
    empty-string value has to survive the env plumbing too: an `env.get(name) or
    default` anywhere on that path would turn "" into something else.
    """
    fetch = FakeFetcher({})
    results = cm.run_all(
        fetch,
        NO_SLEEP,
        1000.0,
        env={"NJT_USERNAME": "", "NJT_PASSWORD": "", "MONITOR_SKIP_PRODUCTION": "1"},
    )
    njt = [r for r in results if r.name == "njt-static"]
    assert len(njt) == 1
    assert njt[0].status == cm.WARN
    assert "not set" in njt[0].detail
    # Scoped to NJT: the other checks in run_all fetch their own upstreams through
    # this same fake, so a blanket "no calls" assertion would be about them.
    njt_calls = [url for url, _h, _p in fetch.calls if "njtransit" in url or "/njt/" in url]
    assert njt_calls == [], f"empty credentials must reach no NJT endpoint, got {njt_calls}"
    assert all(form is None for form in fetch.forms), (
        "no POST should have been attempted at all: NJ Transit is the only source "
        "that posts, and it was skipped"
    )


# ---------------------------------------------------------------------------
# Runner wiring / hermeticity
# ---------------------------------------------------------------------------


def test_run_all_is_hermetic_and_names_every_check():
    # Every fetch fails (500); no test double reaches the network. The run should
    # still produce one result per check without raising, exercising the wiring.
    class AllFail:
        def __init__(self):
            self.calls = 0

        def __call__(self, url, headers=None, params=None, files=None):
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
        "njt-static",
        "subway-realtime",
        "railroad-realtime",
        "path-realtime",
        "ferry-realtime",
        "alerts-realtime",
        "njt-realtime",
        "bus-realtime",
        "production",
    ):
        assert expected in names
    assert fetch.calls > 0  # it did try to fetch, via the injected fake only


def test_run_all_unset_status_url_produces_a_production_fail():
    # The wiring end of the change: run_all must pass the unset variable through so
    # the section fails, rather than the old silent WARN-skip.
    class AllFail:
        def __call__(self, url, headers=None, params=None, files=None):
            return cm.FetchResult(500, b"")

    results = cm.run_all(AllFail(), NO_SLEEP, 1000.0, env={})
    production = [r for r in results if r.name.startswith("production")]
    assert len(production) == 1
    assert production[0].status == cm.FAIL
    assert "MONITOR_STATUS_URL" in production[0].detail


def test_run_all_honors_the_explicit_skip_variable():
    # Any non-empty value opts out, the usual shell-variable convention.
    class AllFail:
        def __call__(self, url, headers=None, params=None, files=None):
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
        def _fetch(url, headers=None, params=None, files=None):
            return cm.FetchResult(500, b"")

        return _fetch

    monkeypatch.setattr(cm, "make_httpx_fetcher", fake_fetcher)
    monkeypatch.setattr(cm.time, "sleep", lambda _s: None)
    # Unset before the FIRST main() call, not just before step 3: this suite runs
    # inside Actions, where GITHUB_STEP_SUMMARY points at the real job summary file,
    # and every main() below would otherwise append three synthetic result tables to
    # the CI run's own summary.
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

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


# ---------------------------------------------------------------------------
# F1: the /healthz classification and the served_at replay witness
# ---------------------------------------------------------------------------


def test_the_monitor_watches_exactly_the_codes_the_probe_publishes():
    """THE COUPLING TEST that stands in for an import.

    PRODUCTION_HEALTH_CODES is mirrored rather than imported, matching what this
    file already does with PRODUCTION_ALERT_RETENTION_MAX_S and for the same
    reason: what the monitor DEMANDS of production must be its own statement, or
    editing the app quietly edits the monitor's expectations. Mirroring without
    this test would be strictly worse than importing, though, because teaching
    /healthz a new degraded state would leave the monitor silently not watching
    it, which is F1 happening again one level up. So the drift is caught here.
    """
    import models

    assert cm.PRODUCTION_HEALTH_CODES == models.HEALTH_DEGRADED_CODES


@pytest.mark.parametrize(
    "configured",
    ["https://app.example", "https://app.example/", "https://app.example/api/status"],
)
def test_the_health_url_comes_off_the_same_variable_in_every_form(configured):
    assert cm._resolve_health_url(configured) == _PROD_HEALTH


# ---- the four degraded states, each seen to fire ----


def test_the_quota_code_is_explained_in_words_and_the_others_are_not():
    """A CODE THAT READS LIKE AN OUTAGE AND IS NOT ONE NEEDS A SENTENCE. Every other
    code names something broken, so its name is the whole instruction. "njt-mint-quota"
    names a budget that is spent: the layer really is dark, nothing upstream is
    wrong, and the two obvious reactions (redeploy, or dispatch this workflow to
    check again) each spend one of the mints that ran out. So the summary says that,
    out of the monitor's OWN literal, keyed by a code from its own tuple. Nothing
    from the wire is echoed, which is the rule _check_production_health is built on.
    """
    fetch = _healthy_prod(health=_healthz_json(status="pass", degraded=["njt-mint-quota"]))
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    assert "njt-mint-quota" in health.detail
    assert "Eastern day" in health.detail and "not an NJ Transit outage" in health.detail

    # The others stay bare: a note per code would be a glossary nobody reads.
    other = _healthy_prod(health=_healthz_json(status="pass", degraded=["feed-content-stale"]))
    detail = next(
        r
        for r in cm.check_production(other, NO_SLEEP, 1000.0, _PROD_BASE)
        if r.name == "production:healthz"
    ).detail
    assert detail == "degraded: feed-content-stale"


@pytest.mark.parametrize("code", cm.PRODUCTION_HEALTH_CODES)
def test_every_degraded_code_fails_the_run(code):
    """Each state the probe can report has to reach a nonzero exit. Parametrized
    over the tuple itself so a code added to both sides without a witness cannot
    slip through: the new code gets a test the moment it is listed."""
    fetch = _healthy_prod(health=_healthz_json(status="fail", degraded=[code]))
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    assert code in health.detail


def test_a_degraded_probe_answering_503_is_read_not_called_unreachable():
    """/healthz replies 503 EXACTLY WHEN IT IS DEGRADED, so the one probe with
    something to say arrives as a non-200. Routed through _fetch_retrying it would
    come back as (None, "HTTP 503"), the body would be discarded, and the richest
    signal this check has would be reported as the poorest failure it can emit."""
    fetch = FakeFetcher(
        {
            _PROD_STATUS: [
                _status_json(served_at=_PROD_SERVED_AT),
                _status_json(served_at=_PROD_SERVED_AT + cm.PRODUCTION_REPLAY_PROBE_GAP_S),
            ],
            _PROD_HEALTH: cm.FetchResult(
                503, _healthz_json(status="fail", degraded=["bus-route-index-failed"])
            ),
        }
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    assert "bus-route-index-failed" in health.detail
    assert "unreachable" not in health.detail


def test_several_degraded_states_are_all_named():
    codes = ["bus-route-index-failed", "feed-content-stale", "subway-groups-down"]
    fetch = _healthy_prod(health=_healthz_json(status="pass", degraded=codes))
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    for code in codes:
        assert code in health.detail


def test_a_degraded_state_fails_the_run_even_while_the_probe_says_pass():
    """The non-gating codes ride a 200 with status "pass", because a lagging
    upstream must not make Railway restart the container. The monitor is the
    stricter reader on purpose: readiness and sickness are different questions and
    this one is asking the second."""
    fetch = _healthy_prod(health=_healthz_json(status="pass", degraded=["feed-content-stale"]))
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL


# ---- the probe body itself ----


def test_a_probe_that_publishes_no_degraded_key_is_unwatched_not_healthy():
    """SILENCE MUST BE CHOSEN, NEVER DEFAULTED, the rule an unset
    MONITOR_STATUS_URL is already held to. A deployment running code from before
    the classification reports nothing, and reading that as "nothing wrong" would
    make an unwatched deployment indistinguishable from a healthy one."""
    fetch = _healthy_prod(health=json.dumps({"status": "pass"}).encode())
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    assert "UNWATCHED" in health.detail


def test_unrecognized_codes_are_counted_and_never_quoted():
    """`degraded` is JSON from a URL pasted into a repository variable, and this
    detail is written to $GITHUB_STEP_SUMMARY, which GitHub RENDERS AS MARKDOWN.
    Echoing an unknown string there would let whatever answers that URL write into
    the job summary. The count still says "this deployment knows something I do
    not" without repeating it."""
    hostile = "[click me](https://evil.example) <img src=x onerror=alert(1)>"
    fetch = _healthy_prod(health=_healthz_json(status="fail", degraded=[hostile, "not-a-code"]))
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    assert "2 unrecognized" in health.detail
    assert "evil.example" not in health.detail
    assert "onerror" not in health.detail
    # And nothing from the body survives into the rendered summary either.
    assert "evil.example" not in cm.format_summary_table(results)


@pytest.mark.parametrize(
    "body",
    [
        b"not json at all",
        b"null",
        b"[]",
        b'{"status": "pass", "degraded": "feed-content-stale"}',  # a string, not a list
        b'{"status": "pass", "degraded": [1, 2]}',  # a list of the wrong type
    ],
)
def test_a_malformed_probe_body_fails_its_own_line_only(body):
    fetch = _healthy_prod(health=body)
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    # Every other line still reported, so one bad probe cannot blind the rest.
    assert next(r for r in results if r.name == "production:statics").status == cm.PASS


@pytest.mark.parametrize("response", [500, 404, ConnectionError("boom")])
def test_an_unreachable_probe_is_a_fail(response):
    fetch = _healthy_prod(health=response)
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    health = next(r for r in results if r.name == "production:healthz")
    assert health.status == cm.FAIL
    assert "unreachable" in health.detail


# ---- the replayed served_at ----


def _replay_prod(first, second, *, health=None):
    """Two /api/status probes with served_at set explicitly on each."""
    return FakeFetcher(
        {
            _PROD_STATUS: [_status_json(served_at=first), _status_json(served_at=second)],
            _PROD_HEALTH: _healthz_json() if health is None else health,
        }
    )


def test_a_replayed_served_at_is_caught_by_the_second_probe():
    """THE STATE THAT CANNOT BE SEEN IN ONE READ. A cached copy of /api/status is
    well formed, internally consistent, and passes every other check in this file
    while the deployment behind it could be gone. Only comparison tells them
    apart."""
    fetch = _replay_prod(_PROD_SERVED_AT, _PROD_SERVED_AT)
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    replay = next(r for r in results if r.name == "production:served_at")
    assert replay.status == cm.FAIL
    assert "replaying" in replay.detail
    # Non-vacuity: everything else about this payload looked perfectly healthy,
    # which is exactly why the single-read version of this check could not exist.
    assert next(r for r in results if r.name == "production:statics").status == cm.PASS
    assert next(r for r in results if r.name == "production:feeds").status == cm.PASS


@pytest.mark.parametrize(
    ("advance", "expected"),
    [
        (0.0, "FAIL"),  # frozen: the replay signature
        (-30.0, "FAIL"),  # the deployment's clock stepped backwards
        (0.4, "FAIL"),  # a short-TTL cache, advancing but far under the gap
        (1.0, "PASS"),  # exactly the floor (gap 2.0 x ratio 0.5)
        (2.0, "PASS"),  # a live deployment: advances by the whole gap
        (2.6, "PASS"),  # ...or a little more, the probes not being instant
    ],
)
def test_the_served_at_advance_bands(advance, expected):
    fetch = _replay_prod(_PROD_SERVED_AT, _PROD_SERVED_AT + advance)
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    replay = next(r for r in results if r.name == "production:served_at")
    assert replay.status == getattr(cm, expected)


def test_the_replay_probe_waits_the_gap_before_asking_again():
    """The judgement is made against the gap we ASKED for, so the gap has to
    actually be requested; without the sleep a live deployment would be judged on
    two responses built microseconds apart and would read as a cache."""
    slept = []
    fetch = _healthy_prod()
    cm.check_production(fetch, slept.append, 1000.0, _PROD_BASE)
    assert cm.PRODUCTION_REPLAY_PROBE_GAP_S in slept


@pytest.mark.parametrize("missing", ["first", "second"])
def test_a_probe_without_a_usable_served_at_cannot_be_judged_and_fails(missing):
    first = _status_json() if missing == "first" else _status_json(served_at=_PROD_SERVED_AT)
    second = _status_json() if missing == "second" else _status_json(served_at=_PROD_SERVED_AT + 5)
    fetch = FakeFetcher({_PROD_STATUS: [first, second], _PROD_HEALTH: _healthz_json()})
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    replay = next(r for r in results if r.name == "production:served_at")
    assert replay.status == cm.FAIL
    assert "served_at" in replay.detail


@pytest.mark.parametrize("value", ['"1000"', "true", "null", "{}"])
def test_a_nonnumeric_served_at_is_not_silently_compared(value):
    body = json.loads(_status_json())
    body["served_at"] = json.loads(value)
    fetch = FakeFetcher(
        {
            _PROD_STATUS: [json.dumps(body).encode(), _status_json(served_at=_PROD_SERVED_AT)],
            _PROD_HEALTH: _healthz_json(),
        }
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    replay = next(r for r in results if r.name == "production:served_at")
    assert replay.status == cm.FAIL


def test_a_second_probe_that_fails_is_a_fail_not_a_silent_pass():
    """The first probe already proved reachability, so a second one missing
    seconds later is not a cold deployment; it is one answering inconsistently."""
    fetch = FakeFetcher(
        {
            _PROD_STATUS: [_status_json(served_at=_PROD_SERVED_AT), 502, 502],
            _PROD_HEALTH: _healthz_json(),
        }
    )
    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    replay = next(r for r in results if r.name == "production:served_at")
    assert replay.status == cm.FAIL
    assert "second" in replay.detail


@pytest.mark.parametrize(
    ("health", "status_bodies", "expected_exit"),
    [
        (None, None, 0),
        (_healthz_json(status="fail", degraded=["subway-groups-down"]), None, 1),
        (None, "replay", 1),
    ],
    ids=["healthy-exits-0", "degraded-exits-1", "replayed-served-at-exits-1"],
)
def test_a_degraded_deployment_produces_the_fail_that_exits_nonzero(
    health, status_bodies, expected_exit, monkeypatch
):
    """THE EXIT SEMANTICS. main() exits 1 when any Result is a FAIL (pinned
    separately), so what is left to prove is that a degraded deployment actually
    PRODUCES one: a classification that reaches a Result but not the exit code
    would be a monitor reporting production sick in a job that stays green, which
    is the finding restated.

    Against check_production rather than run_all, deliberately. run_all with a
    stub fetcher fails every upstream check too, so `any FAIL` would hold whatever
    the production lines said and the assertion would be non-discriminating. The
    healthy row is here for the same reason: a check that always failed would
    satisfy the other two.
    """
    served = (
        [_status_json(served_at=_PROD_SERVED_AT), _status_json(served_at=_PROD_SERVED_AT)]
        if status_bodies == "replay"
        else [
            _status_json(served_at=_PROD_SERVED_AT),
            _status_json(served_at=_PROD_SERVED_AT + cm.PRODUCTION_REPLAY_PROBE_GAP_S),
        ]
    )
    mapping = {_PROD_STATUS: served, _PROD_HEALTH: health or _healthz_json()}

    def fetch(url, headers=None, params=None, files=None):
        if url in mapping:
            return FakeFetcher(mapping)(url, headers, params, files)
        # Every non-production upstream is out of scope here; a 500 keeps their
        # lines FAILing uniformly so only the production lines vary between rows.
        return cm.FetchResult(500, b"")

    results = cm.check_production(fetch, NO_SLEEP, 1000.0, _PROD_BASE)
    fails = [r for r in results if r.status == cm.FAIL]
    assert (1 if fails else 0) == expected_exit, fails
