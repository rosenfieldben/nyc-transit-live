"""Synthetic + golden tests for the NYC Ferry realtime decode (14b).

Two layers, matching test_feeds_path.py's house discipline:
  - Synthetic feeds (built inline, no disk, no network) exercise every decode
    rule: the trip -> route join, a join miss keeping the boat with a null
    route, a deadhead (empty trip_id) dropped and counted, dock dwell (both
    arrival and departure), missing-time tolerance, past-stop dropping, route
    bucketing, and the bearing-omitted / speed-raw / status passthrough.
  - Goldens lock the decode against REAL captured feeds (ferry_vp_a.pb,
    ferry_tu_a.pb) decoded against the trip -> route snapshot captured with them
    (ferry_rt_static.json), written by backend/scripts/gen_ferry_rt_fixture.py.
    They skip loudly until the fixtures are captured (which needs egress to the
    Connexionz host during service hours); in CI a missing fixture fails.

The two rules flagged for reviewer attention live here: a join miss KEEPS the
positioned boat (route_id null), and a deadhead is DROPPED. Both are asserted
directly below so a regression in either surfaces as a test failure, not a
silently wrong map.
"""

import contextlib
import importlib
import json
import os
from pathlib import Path

import httpx
import pytest
from google.transit import gtfs_realtime_pb2 as pb

import feeds
from conftest import golden_fixture_guard

FIXTURES = Path(__file__).parent / "fixtures"

NOW = 1000.0

# The static trip -> route join inputs: trip_id -> {route_id, ...} and
# route_id -> {long_name, ...}, the shapes 14a's load_ferry_static produces.
TRIPS = {
    "T-ER-1": {"route_id": "ER", "direction_id": "0", "shape_id": "s1", "headsign": "Wall St"},
    "T-SB-1": {"route_id": "SB", "direction_id": "0", "shape_id": "s2", "headsign": "Bay Ridge"},
    "T-NOROUTE": {"route_id": None, "direction_id": None, "shape_id": None, "headsign": None},
}
ROUTES = {
    "ER": {
        "long_name": "East River",
        "short_name": "ER",
        "color": "00839C",
        "text_color": "FFFFFF",
    },
    "SB": {"long_name": "South Brooklyn", "short_name": "SB", "color": "FFD100", "text_color": "0"},
}

_STOPPED_AT = pb.VehiclePosition.VehicleStopStatus.STOPPED_AT
_IN_TRANSIT_TO = pb.VehiclePosition.VehicleStopStatus.IN_TRANSIT_TO
_CANCELED = pb.TripDescriptor.ScheduleRelationship.CANCELED
_SKIPPED = pb.TripUpdate.StopTimeUpdate.ScheduleRelationship.SKIPPED


def _feed(header_ts=NOW):
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(header_ts)
    return feed


def _add_vehicle(
    feed,
    *,
    vid="H1",
    label="H201",
    trip_id="T-ER-1",
    lat=40.703,
    lon=-74.011,
    speed=None,
    status=None,
    ts=None,
    with_position=True,
):
    """Add a VehiclePosition entity. lat/lon default inside the NYC box."""
    ent = feed.entity.add()
    ent.id = vid
    v = ent.vehicle
    v.vehicle.id = vid
    if label is not None:
        v.vehicle.label = label
    v.trip.trip_id = trip_id
    if with_position:
        v.position.latitude = lat
        v.position.longitude = lon
        if speed is not None:
            v.position.speed = speed
    if status is not None:
        v.current_status = status
    if ts is not None:
        v.timestamp = int(ts)
    return ent


def _add_trip_update(feed, *, trip_id="T-ER-1", canceled=False, stops=()):
    """Add a TripUpdate entity. stops = [(stop_id, arrival | None, departure |
    None [, schedule_relationship]), ...]."""
    ent = feed.entity.add()
    ent.id = trip_id
    tu = ent.trip_update
    tu.trip.trip_id = trip_id
    if canceled:
        tu.trip.schedule_relationship = _CANCELED
    for spec in stops:
        stop_id, arrival, departure = spec[0], spec[1], spec[2]
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        if arrival is not None:
            stu.arrival.time = int(arrival)
        if departure is not None:
            stu.departure.time = int(departure)
        if len(spec) > 3 and spec[3] is not None:
            stu.schedule_relationship = spec[3]
    return ent


def _vehicles(raw_feed):
    return feeds._decode_ferry_vehicles(raw_feed.SerializeToString(), TRIPS, ROUTES, NOW)


def _arrivals(raw_feed):
    return feeds._decode_ferry_arrivals(raw_feed.SerializeToString(), TRIPS, ROUTES, NOW)


# ---------------- VehiclePositions (boats) ----------------


def test_vehicle_basic_join_populates_all_fields():
    feed = _feed()
    _add_vehicle(
        feed, vid="H1", label="H201", trip_id="T-ER-1", speed=6.5, status=_IN_TRANSIT_TO, ts=NOW
    )
    boats, feed_ts, deadheads, misses = _vehicles(feed)
    assert (deadheads, misses) == (0, 0)
    assert feed_ts == NOW
    assert boats == [
        {
            "id": "H1",
            "label": "H201",
            "trip_id": "T-ER-1",
            "route_id": "ER",  # joined through TRIPS -> ROUTES
            "latitude": pytest.approx(40.703),
            "longitude": pytest.approx(-74.011),
            "speed": pytest.approx(6.5),
            "status": "IN_TRANSIT_TO",
            "updated_at": NOW,
        }
    ]


def test_vehicle_bearing_is_never_in_payload():
    # The feed reports bearing 0.0 always, so it must be omitted entirely.
    feed = _feed()
    _add_vehicle(feed)
    boats, *_ = _vehicles(feed)
    assert "bearing" not in boats[0]


def test_vehicle_join_miss_keeps_boat_with_null_route():
    # THE reviewer-flagged rule: a positioned vessel whose trip_id does not join
    # the static map stays on the map with route_id null, never dropped.
    feed = _feed()
    _add_vehicle(feed, vid="H9", trip_id="T-UNKNOWN-999")
    boats, _ts, deadheads, misses = _vehicles(feed)
    assert deadheads == 0 and misses == 1
    assert len(boats) == 1
    assert boats[0]["id"] == "H9" and boats[0]["route_id"] is None


def test_vehicle_trip_present_but_route_null_is_a_join_miss():
    # A trip that IS in the static but carries no route_id is a metadata miss too.
    feed = _feed()
    _add_vehicle(feed, vid="H8", trip_id="T-NOROUTE")
    boats, _ts, _dead, misses = _vehicles(feed)
    assert misses == 1 and boats[0]["route_id"] is None


def test_vehicle_deadhead_dropped_and_counted():
    # THE reviewer-flagged rule: an empty trip_id is a deadheading vessel; drop
    # it from boats with a count.
    feed = _feed()
    _add_vehicle(feed, vid="H1", trip_id="T-ER-1")  # in service
    _add_vehicle(feed, vid="H2", trip_id="")  # deadheading
    boats, _ts, deadheads, misses = _vehicles(feed)
    assert deadheads == 1 and misses == 0
    assert [b["id"] for b in boats] == ["H1"]


def test_vehicle_without_position_skipped():
    feed = _feed()
    _add_vehicle(feed, vid="H1", with_position=False)
    boats, *_ = _vehicles(feed)
    assert boats == []


def test_vehicle_out_of_box_coordinate_dropped():
    # A 0,0 depot/test coordinate is not a real NYC-harbor boat.
    feed = _feed()
    _add_vehicle(feed, vid="H1", lat=0.0, lon=0.0)
    boats, *_ = _vehicles(feed)
    assert boats == []


def test_vehicle_speed_and_status_optional():
    # No speed / no status in the feed -> both null, updated_at null when no ts.
    feed = _feed()
    _add_vehicle(feed, vid="H1", speed=None, status=None, ts=None)
    boats, *_ = _vehicles(feed)
    assert boats[0]["speed"] is None
    assert boats[0]["status"] is None
    assert boats[0]["updated_at"] is None


def test_vehicle_stopped_status_name():
    feed = _feed()
    _add_vehicle(feed, vid="H1", status=_STOPPED_AT)
    boats, *_ = _vehicles(feed)
    assert boats[0]["status"] == "STOPPED_AT"


# ---------------- TripUpdates (arrivals) ----------------


def test_arrivals_bucketed_by_route_name():
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", NOW + 120, NOW + 180)])
    _add_trip_update(feed, trip_id="T-SB-1", stops=[("18", NOW + 240, NOW + 300)])
    arrivals, _dead, _miss = _arrivals(feed)
    assert set(arrivals["18"]) == {"East River", "South Brooklyn"}
    assert arrivals["18"]["East River"][0]["route_id"] == "ER"


def test_arrivals_dwell_keeps_both_times():
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", NOW + 120, NOW + 180)])
    arrivals, *_ = _arrivals(feed)
    row = arrivals["18"]["East River"][0]
    assert row == {
        "route_id": "ER",
        "trip_id": "T-ER-1",
        "arrival": NOW + 120,
        "departure": NOW + 180,
    }


def test_arrivals_missing_departure_tolerated():
    # A terminal dock may report only an arrival.
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", NOW + 120, None)])
    arrivals, *_ = _arrivals(feed)
    row = arrivals["18"]["East River"][0]
    assert row["arrival"] == NOW + 120 and row["departure"] is None


def test_arrivals_missing_arrival_uses_departure_for_sort():
    # An origin dock may report only a departure; the row is still kept and sorts
    # by its departure time.
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", None, NOW + 90)])
    arrivals, *_ = _arrivals(feed)
    row = arrivals["18"]["East River"][0]
    assert row["arrival"] is None and row["departure"] == NOW + 90


def test_arrivals_mixed_null_and_present_arrivals_sort_without_raising():
    # Two boats on the SAME dock and route where one row has arrival=None (an
    # origin dock, departure only) and the other has an arrival. The trim sorts
    # the bucket, so the sort key must fall back to departure for the None-arrival
    # row instead of comparing None < a float (a TypeError that would blank the
    # whole dock). This directly exercises the crash-prevention branch in
    # _trim_ferry_arrivals; an arrival-only sort key would raise here.
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", None, NOW + 90)])  # departure only
    _add_trip_update(feed, trip_id="T-SB-1", stops=[("18", NOW + 40, NOW + 60)])  # different route
    # Put both on route ER via a trips map so they share ONE bucket and the sort
    # actually compares the two rows.
    trips = {"T-ER-1": {"route_id": "ER"}, "T-SB-1": {"route_id": "ER"}}
    arrivals, *_ = feeds._decode_ferry_arrivals(feed.SerializeToString(), trips, ROUTES, NOW)
    rows = arrivals["18"]["East River"]
    assert len(rows) == 2
    # Sorted soonest-first by the effective time (arrival when present, else
    # departure): the arrival-present row (NOW+40) precedes the departure-only
    # row (NOW+90).
    assert rows[0]["arrival"] == NOW + 40
    assert rows[1]["arrival"] is None and rows[1]["departure"] == NOW + 90


def test_arrivals_past_stop_dropped():
    # A stop whose latest time is well past (beyond the 60s grace) is not indexed.
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", NOW - 200, NOW - 100)])
    arrivals, *_ = _arrivals(feed)
    assert arrivals == {}


def test_arrivals_dwelling_boat_kept_via_departure():
    # Arrival is PAST the 60s just-passed grace (NOW-100 < NOW-60) but departure
    # is still upcoming (a boat dwelling at the dock). Only the LATEST-time rule
    # (_stop_time = max of arrival/departure) keeps it: an arrival-only check
    # would drop this row, so this pins the dwell rule, not just the grace.
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", NOW - 100, NOW + 90)])
    arrivals, *_ = _arrivals(feed)
    row = arrivals["18"]["East River"][0]
    assert row["arrival"] == NOW - 100 and row["departure"] == NOW + 90


def test_arrivals_deadhead_dropped_and_counted():
    feed = _feed()
    _add_trip_update(feed, trip_id="", stops=[("18", NOW + 120, NOW + 180)])
    arrivals, deadheads, misses = _arrivals(feed)
    assert arrivals == {} and deadheads == 1 and misses == 0


def test_arrivals_join_miss_lands_in_residual_bucket():
    feed = _feed()
    _add_trip_update(feed, trip_id="T-UNKNOWN-999", stops=[("18", NOW + 120, NOW + 180)])
    arrivals, _dead, misses = _arrivals(feed)
    assert misses == 1
    assert set(arrivals["18"]) == {"Ferry"}  # the _UNKNOWN_ROUTE_BUCKET residual
    assert arrivals["18"]["Ferry"][0]["route_id"] is None


def test_arrivals_canceled_trip_dropped():
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", canceled=True, stops=[("18", NOW + 120, NOW + 180)])
    arrivals, *_ = _arrivals(feed)
    assert arrivals == {}


def test_arrivals_skipped_stop_dropped():
    feed = _feed()
    _add_trip_update(feed, trip_id="T-ER-1", stops=[("18", NOW + 120, NOW + 180, _SKIPPED)])
    arrivals, *_ = _arrivals(feed)
    assert arrivals == {}


def test_arrivals_sorted_and_capped_per_route():
    # More upcoming boats than the shared cap: sorted soonest-first, capped.
    feed = _feed()
    cap = feeds.ARRIVALS_PER_DIRECTION
    # Add them out of order so the sort is exercised.
    for i in reversed(range(cap + 3)):
        _add_trip_update(feed, trip_id=f"T-ER-{i}", stops=[("18", NOW + 60 + i, NOW + 90 + i)])
    # Only T-ER-1 joins ROUTES (all share route ER via TRIPS? no: only T-ER-1 is
    # in TRIPS). Use a trips map where each joins ER for this test.
    trips = {f"T-ER-{i}": {"route_id": "ER"} for i in range(cap + 3)}
    arrivals, *_ = feeds._decode_ferry_arrivals(feed.SerializeToString(), trips, ROUTES, NOW)
    rows = arrivals["18"]["East River"]
    assert len(rows) == cap
    arrivals_times = [r["arrival"] for r in rows]
    assert arrivals_times == sorted(arrivals_times)  # soonest-first
    assert arrivals_times[0] == NOW + 60  # the earliest survived the cap


# ---------------- fetch transport: https is mandatory (R3) ----------------
#
# The helper used to try https and then RETRY the same endpoint over http on any
# httpx.HTTPError. Because that exception class covers certificate and connect
# failures as well as 4xx/5xx, the fallback downgraded transport on exactly the
# failures that most deserve surfacing. These tests pin the replacement: exactly
# ONE request per endpoint, on the configured (https) scheme, and a failure that
# propagates untouched to the caller.


class _FakeFerryResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeFerryClient:
    """Records every request so a test can assert the COUNT and the SCHEME. The
    ferry helper passes follow_redirects, so the signature accepts it."""

    def __init__(self, content=b"", error=None):
        self._content = content
        self._error = error
        self.urls: list[str] = []

    async def get(self, url, follow_redirects=False):
        self.urls.append(url)
        if self._error is not None:
            raise self._error
        return _FakeFerryResp(self._content)


# Every fetch test pins the base rather than reading the ambient one. FERRY_RT_BASE
# is an env override by design, so a developer or CI box that legitimately sets it
# (to a mirror, or to the plain http the docstring says an operator may choose) must
# not turn these into failures: they assert what the CODE does with a base, not what
# this machine happens to be configured for. The env wiring itself is covered
# separately, below, by reloading the module under a controlled environment.
_PINNED_BASE = "https://ferry.test/rt"


@pytest.fixture
def pinned_base(monkeypatch):
    monkeypatch.setattr(feeds.ferry, "FERRY_RT_BASE", _PINNED_BASE)
    return _PINNED_BASE


@pytest.mark.anyio
async def test_fetch_endpoint_makes_one_https_request(pinned_base):
    client = _FakeFerryClient(b"payload")
    raw = await feeds.ferry._fetch_ferry_endpoint(client, feeds.FERRY_VEHICLE_ENDPOINT)
    assert raw == b"payload"
    assert client.urls == [f"{pinned_base}/{feeds.FERRY_VEHICLE_ENDPOINT}"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        # The three shapes the old fallback treated identically, all httpx.HTTPError:
        # a server error, a transport/connect failure, and the certificate failure an
        # attacker could induce to force the downgrade.
        httpx.HTTPStatusError("500", request=httpx.Request("GET", "https://x/y"), response=None),
        httpx.ConnectError("connection refused"),
        httpx.ConnectError("certificate verify failed"),
    ],
    ids=["status_5xx", "connect_error", "certificate_error"],
)
async def test_fetch_endpoint_never_retries_on_another_scheme(error, pinned_base):
    # THE R3 INVARIANT: an https failure raises, and NO second attempt is made on
    # any scheme. Before R3 each of these silently produced a plaintext retry.
    client = _FakeFerryClient(error=error)
    with pytest.raises(httpx.HTTPError):
        await feeds.ferry._fetch_ferry_endpoint(client, feeds.FERRY_VEHICLE_ENDPOINT)
    assert len(client.urls) == 1  # exactly one attempt, no fallback
    assert not any(url.startswith("http://") for url in client.urls)


@pytest.mark.anyio
async def test_fetch_endpoint_turns_a_malformed_base_into_a_recordable_error(monkeypatch):
    # A malformed override is now reachable because the base is operator config, and
    # httpx.InvalidURL is NOT an httpx.HTTPError, so it would sail past every ferry
    # handler and land in the poll loop's generic "cycle failed unexpectedly" while
    # the cache recorded nothing. It must arrive as an HTTPError the poller records.
    monkeypatch.setattr(feeds.ferry, "FERRY_RT_BASE", "https://ferry.test:not-a-port/rt")

    class _RealUrlClient:
        async def get(self, url, follow_redirects=False):
            raise httpx.InvalidURL("Invalid port: 'not-a-port'")

    with pytest.raises(httpx.HTTPError) as caught:  # NOT a bare InvalidURL
        await feeds.ferry._fetch_ferry_endpoint(_RealUrlClient(), feeds.FERRY_VEHICLE_ENDPOINT)
    assert "FERRY_RT_BASE" in str(caught.value)  # the recorded detail names the cause


@pytest.mark.anyio
async def test_fetch_ferry_data_propagates_http_error_for_the_caller_to_record(pinned_base):
    # The whole-poll contract (matching the PATH fetch precedent): the error
    # reaches pollers._refresh_ferry, which records it and keeps last-known.
    client = _FakeFerryClient(error=httpx.ConnectError("ferry down"))
    with pytest.raises(httpx.HTTPError):
        await feeds.fetch_ferry_data(client, {"trips": TRIPS, "routes": ROUTES})
    # Both endpoints are gathered and each makes exactly one attempt, so a poll that
    # fails outright costs 2 requests. Under the deleted fallback it cost 4.
    assert len(client.urls) == 2
    assert not any(url.startswith("http://") for url in client.urls)


@pytest.mark.anyio
async def test_fetch_uses_the_configured_base_for_both_endpoints(monkeypatch):
    # The helper reads the module global at call time, so an override (env or
    # otherwise) reaches every endpoint rather than only one of them.
    monkeypatch.setattr(feeds.ferry, "FERRY_RT_BASE", "https://ferry.example/base")
    client = _FakeFerryClient(_feed().SerializeToString())
    await feeds.fetch_ferry_data(client, {"trips": TRIPS, "routes": ROUTES})
    assert sorted(client.urls) == [
        "https://ferry.example/base/tripupdate",
        "https://ferry.example/base/vehicleposition",
    ]


@contextlib.contextmanager
def _reloaded_with_env(value):
    """Reload feeds.ferry with FERRY_RT_BASE set to `value` (None to unset it), then
    put every binding back exactly as it was.

    The constant is module-level (the PATH_RT_URL shape), so re-running its
    os.getenv means reloading the module. That is global mutable state shared with
    the rest of the session, and feeds/__init__.py holds a SEPARATE binding made at
    package import, so a naive restore ("delete the var and reload") would pin the
    module to the hardcoded default and silently desync it from both os.environ and
    the package attribute for every later test. Capture and restore all three."""
    before_env = os.environ.get("FERRY_RT_BASE")
    before_module = feeds.ferry.FERRY_RT_BASE
    before_package = feeds.FERRY_RT_BASE
    try:
        if value is None:
            os.environ.pop("FERRY_RT_BASE", None)
        else:
            os.environ["FERRY_RT_BASE"] = value
        importlib.reload(feeds.ferry)
        yield feeds.ferry.FERRY_RT_BASE
    finally:
        if before_env is None:
            os.environ.pop("FERRY_RT_BASE", None)
        else:
            os.environ["FERRY_RT_BASE"] = before_env
        importlib.reload(feeds.ferry)
        feeds.ferry.FERRY_RT_BASE = before_module
        feeds.FERRY_RT_BASE = before_package


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        ("https://mirror.example/rt", "https://mirror.example/rt"),
        # A trailing slash is stripped: the endpoint is joined onto this base, so
        # keeping it would build a double slash. PATH_RT_URL needs no such guard
        # because it is used whole, with nothing appended.
        ("https://mirror.example/rt/", "https://mirror.example/rt"),
    ],
    ids=["plain", "trailing_slash"],
)
def test_ferry_rt_base_env_override_is_read_at_import(env_value, expected):
    # The override is the honest replacement for the deleted fallback, so pin that
    # it is actually wired to the environment.
    with _reloaded_with_env(env_value) as base:
        assert base == expected


def test_ferry_rt_base_defaults_to_https_when_unset():
    # THE DEFAULT must never be plaintext: an operator who needs http has to set the
    # override explicitly and own that choice. Asserting this needs the var actually
    # UNSET, or the test would only be describing this machine's configuration.
    with _reloaded_with_env(None) as base:
        assert base.startswith("https://")
        assert base == "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx"


def test_ferry_rt_base_restore_leaves_module_and_package_in_sync():
    # Guards the helper above: a reload that leaked would desync feeds.ferry from
    # feeds (a separate binding), and nothing else in the suite would notice.
    before_module, before_package = feeds.ferry.FERRY_RT_BASE, feeds.FERRY_RT_BASE
    with _reloaded_with_env("https://elsewhere.example/rt"):
        pass
    assert feeds.ferry.FERRY_RT_BASE == before_module
    assert feeds.FERRY_RT_BASE == before_package


# ---------------- gated goldens over the real captured feeds ----------------

golden = golden_fixture_guard(FIXTURES / "ferry_vp_a.pb", "backend/scripts/gen_ferry_rt_fixture.py")


def _golden_static():
    return json.loads((FIXTURES / "ferry_rt_static.json").read_text())


def _golden_expected():
    return json.loads((FIXTURES / "ferry_rt_expected.json").read_text())


@golden
def test_golden_vehicles_match_expected():
    static = _golden_static()
    expected = _golden_expected()
    raw = (FIXTURES / "ferry_vp_a.pb").read_bytes()
    boats, feed_ts, _dead, _miss = feeds._decode_ferry_vehicles(
        raw, static["trips"], static["routes"], expected["now"]
    )
    assert boats == expected["boats"]
    assert feed_ts == expected["feed_timestamp"]


@golden
def test_golden_arrivals_match_expected():
    static = _golden_static()
    expected = _golden_expected()
    raw = (FIXTURES / "ferry_tu_a.pb").read_bytes()
    arrivals, _dead, _miss = feeds._decode_ferry_arrivals(
        raw, static["trips"], static["routes"], expected["now"]
    )
    assert arrivals == expected["arrivals"]


@golden
def test_golden_capture_joined_and_carried_no_deadheads_in_boats():
    # The live probe saw 25/25 trips join and one deadheading vessel; the decoded
    # boats must therefore all carry a route and none be the deadhead.
    static = _golden_static()
    expected = _golden_expected()
    raw = (FIXTURES / "ferry_vp_a.pb").read_bytes()
    boats, _ts, _dead, misses = feeds._decode_ferry_vehicles(
        raw, static["trips"], static["routes"], expected["now"]
    )
    assert boats, "expected at least one in-service boat in the capture"
    assert misses == 0, "captured boats should all join the static route map"
    assert all(b["route_id"] for b in boats)
