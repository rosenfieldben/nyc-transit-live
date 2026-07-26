"""Endpoint tests against the FastAPI app without running the lifespan.

httpx's ASGITransport never sends lifespan events, so app.state is primed
manually per test and no real MTA endpoint is ever contacted.
"""

import asyncio
import copy
import itertools
import json
import logging
import time
import types
from pathlib import Path

import httpx
import pytest

import bus_static
import feeds
import main as app_module
import warmups

pytestmark = pytest.mark.anyio

BUSES = [
    {"id": "MTA NYCT_1", "route_id": "M15", "latitude": 40.7, "longitude": -74.0, "bearing": 90.0}
]
TRAINS = [
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
]
RAILROADS = [
    {
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
]


@pytest.fixture
def cache():
    app_module.app.state.feed_cache = {
        "buses": app_module._fresh_entry(),
        "subways": app_module._fresh_entry(),
        "railroads": app_module._fresh_entry(),
        "path": app_module._fresh_entry(),
        "ferry": app_module._fresh_entry(),
    }
    app_module.app.state.subway_feed_health = None
    app_module.app.state.railroad_feed_health = None
    app_module.app.state.path_feed_health = None
    app_module.app.state.ferry_feed_health = None
    return app_module.app.state.feed_cache


@pytest.fixture
async def client(cache):
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------- /api/buses and /api/subways cache states ----------------


@pytest.mark.parametrize("path", ["/api/buses", "/api/subways"])
async def test_empty_cache_returns_warming_up_503(client, path):
    res = await client.get(path)
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_successful_refresh_serves_envelope(client, cache):
    cache["buses"].update(data=BUSES, fetched_at=1000.0, feed_timestamp=995.0, error=None)
    cache["subways"].update(data=TRAINS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    res = await client.get("/api/buses")
    assert res.status_code == 200
    # served_at (R1) is stamped at response build, so it is present and at/after
    # the poll time; the rest of the envelope is exact. A live feed is no-store.
    body = res.json()
    assert body.pop("served_at") >= 1000.0
    # Buses are a SINGLE upstream, so their envelope gains no systems block: the
    # top-level fetched_at already means what a per-system one would (C2 opt-in).
    assert body == {"fetched_at": 1000.0, "feed_timestamp": 995.0, "data": BUSES}
    assert res.headers.get("cache-control") == "no-store"
    res = await client.get("/api/subways")
    body = res.json()
    assert body.pop("served_at") >= 1001.0
    # The subway envelope DOES carry one (8 feed groups). It is null here because
    # this entry was seeded directly rather than by a poll.
    assert body == {
        "fetched_at": 1001.0,
        "feed_timestamp": 996.0,
        "data": TRAINS,
        "systems": None,
    }
    assert res.headers.get("cache-control") == "no-store"


async def test_served_at_advances_while_fetched_at_holds(client, cache, monkeypatch):
    # The core served_at invariant (R1): served_at moves on every response even
    # though the cache entry (data + fetched_at) is frozen. This is precisely the
    # signature of a stuck poller serving frozen last-known data, which the old
    # model was blind to. Drive it with an advancing clock so two fast sequential
    # requests get strictly increasing served_at while fetched_at stays put.
    cache["buses"].update(data=BUSES, fetched_at=1000.0, feed_timestamp=995.0, error=None)
    # A monotonic clock: every time.time() call returns a strictly larger value
    # (unbounded because the request path calls it more than once). served_at is
    # whichever tick the handler grabbed, so assert it STRICTLY INCREASES across
    # the two requests rather than pinning exact values.
    clock = itertools.count(2000.0, 0.001)
    monkeypatch.setattr(app_module.time, "time", lambda: next(clock))
    first = (await client.get("/api/buses")).json()
    second = (await client.get("/api/buses")).json()
    assert first["fetched_at"] == second["fetched_at"] == 1000.0  # poll time frozen
    assert second["served_at"] > first["served_at"] > 1000.0  # response time advances
    assert second["served_at"] > second["fetched_at"]  # the stuck-poller gap widens


async def test_stale_data_beats_subsequent_error(client, cache):
    # A successful refresh followed by a failed one: last-known data is
    # served, with the old fetched_at/feed_timestamp exposing the staleness.
    cache["buses"].update(data=BUSES, fetched_at=1000.0, feed_timestamp=995.0, error=None)
    app_module._note_failure(cache["buses"], 502, "Upstream MTA feed error: boom")
    res = await client.get("/api/buses")
    assert res.status_code == 200
    body = res.json()
    # served_at keeps advancing even though the poll is now failing (fetched_at
    # frozen): that widening served_at - fetched_at gap is exactly the stuck-poller
    # staleness the frontend now reads.
    assert body.pop("served_at") >= 1000.0
    assert body == {"fetched_at": 1000.0, "feed_timestamp": 995.0, "data": BUSES}


async def test_never_filled_cache_serves_recorded_503(client, cache):
    app_module._note_failure(cache["buses"], 503, "BUS_TIME_API_KEY is not set.")
    res = await client.get("/api/buses")
    assert res.status_code == 503
    # Exact match on purpose: the contract under test is that the endpoint
    # serves the recorded detail verbatim, and the test primed that string.
    assert res.json()["detail"] == "BUS_TIME_API_KEY is not set."


async def test_never_filled_cache_serves_recorded_502(client, cache):
    app_module._note_failure(cache["subways"], 502, "All subway feeds failed: timeout")
    res = await client.get("/api/subways")
    assert res.status_code == 502
    # Exact match on purpose: verbatim pass-through of the recorded detail.
    assert res.json()["detail"] == "All subway feeds failed: timeout"


# ---------------- /api/railroads cache states + feed health ----------------


async def test_railroads_warming_up_503(client):
    res = await client.get("/api/railroads")
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_railroads_successful_envelope(client, cache):
    cache["railroads"].update(data=RAILROADS, fetched_at=1001.0, feed_timestamp=None, error=None)
    res = await client.get("/api/railroads")
    assert res.status_code == 200
    body = res.json()
    assert body.pop("served_at") >= 1001.0
    # systems is null on a directly-seeded entry (no poll wrote it); the railroad
    # envelope carries the key because it aggregates LIRR + MNR.
    assert body == {
        "fetched_at": 1001.0,
        "feed_timestamp": None,
        "data": RAILROADS,
        "systems": None,
    }
    assert res.headers.get("cache-control") == "no-store"


async def test_railroads_stale_data_beats_subsequent_error(client, cache):
    cache["railroads"].update(data=RAILROADS, fetched_at=1001.0, feed_timestamp=None, error=None)
    app_module._note_failure(cache["railroads"], 502, "Upstream MTA feed error: boom")
    res = await client.get("/api/railroads")
    assert res.status_code == 200
    assert res.json()["data"] == RAILROADS  # last-known data still served


async def test_railroad_refresh_records_partial_feed_health(client, cache, monkeypatch):
    # One system fails, one returns data: the entry error stays clear, but the
    # partial outage is recorded for /api/status (parallel to the subway case).
    #
    # C2 MADE THIS FAKE'S OLD FORM SELF-CONTRADICTORY: it declared MNR failed while
    # returning MNR trains, which the real fetch_railroad_trains cannot do (it only
    # ever returns trains from systems that decoded). The pre-C2 poller published
    # whatever came back, so the contradiction was invisible; now a failed system's
    # data comes from retention, not from the fetch, so a fake that lies about which
    # system decoded gets a different (and correct) answer. The fake is honest now:
    # LIRR decodes, MNR does not.
    lirr_train = {**RAILROADS[0], "system": "LIRR", "trip_id": "lirr-1"}

    async def partial(client_arg, stops_arg):
        return [lirr_train], {}, 996.0, ["MNR"]

    monkeypatch.setattr(app_module, "fetch_railroad_trains", partial)
    await app_module._refresh_railroads(app_module.app, client=None)
    assert cache["railroads"]["error"] is None
    # The surviving system is published; MNR contributes nothing because there is no
    # previous generation to retain (this is the first poll).
    assert cache["railroads"]["data"] == [lirr_train]
    assert cache["railroads"]["feed_timestamp"] == 996.0  # threaded through from the fetch
    # And the per-system block tells the truth about which one is behind.
    systems = cache["railroads"]["systems"]
    assert systems["LIRR"]["ok"] is True and systems["LIRR"]["fetched_at"] is not None
    assert systems["MNR"]["ok"] is False and systems["MNR"]["fetched_at"] is None
    total = len(feeds.RAILROAD_FEED_URLS)
    assert app_module.app.state.railroad_feed_health == {
        "total": total,
        "ok": total - 1,
        "failed": ["MNR"],
    }


async def test_railroad_refresh_total_failure_marks_all_feeds_failed(client, cache, monkeypatch):
    async def boom(client_arg, stops_arg):
        raise RuntimeError("All railroad feeds failed: every system timed out")

    monkeypatch.setattr(app_module, "fetch_railroad_trains", boom)
    await app_module._refresh_railroads(app_module.app, client=None)
    total = len(feeds.RAILROAD_FEED_URLS)
    health = app_module.app.state.railroad_feed_health
    assert health["total"] == total and health["ok"] == 0
    assert len(health["failed"]) == total
    assert cache["railroads"]["error"]["status"] == 502


async def test_railroad_refresh_replaces_only_decoded_systems_arrivals(client, cache, monkeypatch):
    # Decision 3: a poll where only LIRR decoded refreshes LIRR's arrivals while
    # leaving MNR's last-known arrivals intact (per-system leniency).
    app_module.app.state.railroad_arrivals = {
        "LIRR": {"12": {"Outbound": [{"route_id": "5", "trip_id": "old", "arrival": 1.0}]}},
        # FUTURE arrival times: C2 prunes a RETAINED system's already-departed rows
        # (an expired row sorts ahead of every live one and would fill the cap), so a
        # fixture using epoch-ish times would be deleted by that rule rather than by
        # the retention this test is about.
        "MNR": {"1": {"Trains": [{"route_id": "1", "trip_id": "keep", "arrival": 1e12}]}},
    }
    new_lirr = {"12": {"Inbound": [{"route_id": "5", "trip_id": "new", "arrival": 1e12}]}}

    async def only_lirr(client_arg, stops_arg):
        return RAILROADS, {"LIRR": new_lirr}, 996.0, ["MNR"]

    monkeypatch.setattr(app_module, "fetch_railroad_trains", only_lirr)
    await app_module._refresh_railroads(app_module.app, client=None)
    arrivals = app_module.app.state.railroad_arrivals
    assert arrivals["LIRR"] == new_lirr  # LIRR fully replaced (old dropped)
    assert arrivals["MNR"]["1"]["Trains"][0]["trip_id"] == "keep"  # MNR preserved


# ---------------- /api/bus-route/{id} index states ----------------


@pytest.fixture
def bus_index(tmp_path, monkeypatch):
    """Point the route cache at tmp and give tests status/partial knobs."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(bus_static, "BUS_CACHE_DIR", cache_dir)

    def set_state(status, partial=False):
        monkeypatch.setattr(bus_static, "_status", status)
        monkeypatch.setattr(bus_static, "_partial", partial)

    return cache_dir, set_state


@pytest.mark.parametrize("status", ["missing", "building"])
async def test_bus_route_503_while_indexing(client, bus_index, status):
    _, set_state = bus_index
    set_state(status)
    res = await client.get("/api/bus-route/M15")
    assert res.status_code == 503
    assert "indexing" in res.json()["detail"]


async def test_bus_route_503_when_build_failed(client, bus_index):
    _, set_state = bus_index
    set_state("failed")
    res = await client.get("/api/bus-route/M15")
    assert res.status_code == 503
    assert "could not be built" in res.json()["detail"]


async def test_bus_route_404_mentions_incomplete_index_when_partial(client, bus_index):
    _, set_state = bus_index
    set_state("ready", partial=True)
    res = await client.get("/api/bus-route/M15")
    assert res.status_code == 404
    assert "incomplete" in res.json()["detail"]


async def test_bus_route_plain_404_when_index_complete(client, bus_index):
    _, set_state = bus_index
    set_state("ready", partial=False)
    res = await client.get("/api/bus-route/M15")
    assert res.status_code == 404
    assert "incomplete" not in res.json()["detail"]


async def test_bus_route_serves_cached_file(client, bus_index):
    cache_dir, set_state = bus_index
    set_state("ready")
    geometry = {"route": "M15", "directions": [[[40.7, -74.0], [40.71, -74.01]]]}
    (cache_dir / "M15.json").write_text(json.dumps(geometry))
    res = await client.get("/api/bus-route/M15")
    assert res.status_code == 200
    assert res.json() == geometry


async def test_bus_route_traversal_never_reads_outside_cache(client, bus_index, tmp_path):
    cache_dir, set_state = bus_index
    set_state("ready")
    # Plant a file OUTSIDE the cache dir that a "../" traversal would reach.
    secret = {"route": "evil", "directions": []}
    (tmp_path / "evil.json").write_text(json.dumps(secret))
    res = await client.get("/api/bus-route/..%2Fevil")
    assert res.status_code == 404
    assert res.json() != secret
    # The geometry reader itself must also reject the id outright.
    assert bus_static.get_route_geometry("../evil") is None


# ---------------- /api/status ----------------


@pytest.fixture
def status_env(bus_index, tmp_path, monkeypatch):
    """No static GTFS on disk by default; index state via bus_index knobs."""
    import static_data

    monkeypatch.setattr(static_data, "SUBWAY_GTFS_ZIP", tmp_path / "absent.zip")
    return bus_index


async def test_status_warming_state(client, status_env):
    _, set_state = status_env
    set_state("building")
    res = await client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["feeds"]["buses"] == {
        "fetched_at": None,
        "age_s": None,
        "feed_age_s": None,
        "last_error": None,
    }
    assert body["bus_route_index"] == {"status": "building", "partial": False}
    assert body["static_subway_gtfs"] is None
    # R1: status carries a top-level served_at (this snapshot's build time) and is
    # no-store, since it is a live operational read like the feeds.
    assert isinstance(body["served_at"], float)
    assert res.headers.get("cache-control") == "no-store"


async def test_status_reports_ages_errors_and_gtfs_mtime(
    client, cache, status_env, tmp_path, monkeypatch
):
    import time as time_mod

    import static_data

    _, set_state = status_env
    set_state("ready", partial=True)
    gtfs = tmp_path / "gtfs_subway.zip"
    gtfs.write_bytes(b"zip")
    monkeypatch.setattr(static_data, "SUBWAY_GTFS_ZIP", gtfs)

    fetched = time_mod.time() - 30
    cache["buses"].update(data=BUSES, fetched_at=fetched, feed_timestamp=fetched - 5, error=None)
    app_module._note_failure(cache["subways"], 502, "All subway feeds failed: timeout")

    res = await client.get("/api/status")
    body = res.json()
    assert 29 <= body["feeds"]["buses"]["age_s"] <= 40
    assert body["feeds"]["buses"]["feed_age_s"] == 5.0  # fetched_at - feed_timestamp
    assert body["feeds"]["buses"]["last_error"] is None
    assert body["feeds"]["subways"]["feed_age_s"] is None  # never filled
    assert body["feeds"]["subways"]["last_error"] == {
        "status": 502,
        "detail": "All subway feeds failed: timeout",
    }
    assert body["bus_route_index"] == {"status": "ready", "partial": True}
    assert body["static_subway_gtfs"]["age_s"] >= 0
    # No secrets or filesystem paths in the payload.
    text = res.text
    assert "BUS_TIME_API_KEY=" not in text
    assert str(tmp_path) not in text and "/Users/" not in text and "/app/" not in text


async def test_status_reports_subway_feed_health(client, status_env):
    app_module.app.state.subway_feed_health = {"total": 8, "ok": 7, "failed": ["BDFM"]}
    res = await client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["subway_feeds"] == {"total": 8, "ok": 7, "failed": ["BDFM"]}


async def test_status_reports_railroad_feed_health(client, status_env):
    app_module.app.state.railroad_feed_health = {"total": 2, "ok": 1, "failed": ["MNR"]}
    res = await client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["railroad_feeds"] == {"total": 2, "ok": 1, "failed": ["MNR"]}


async def test_status_reports_path_feed_health_and_cache_entry(client, cache, status_env):
    # The PATH bridge is a polled feed like the others: its cache entry rides
    # the feeds map automatically and its single-feed health is reported
    # alongside subway_feeds/railroad_feeds.
    app_module.app.state.path_feed_health = {"total": 1, "ok": 0, "failed": ["PATH"]}
    res = await client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    # The failure-branch health dict carries no unresolved count (no decode
    # ran); the model default fills 0 at the boundary.
    assert body["path_feeds"] == {"total": 1, "ok": 0, "failed": ["PATH"], "unresolved": 0}
    assert "path" in body["feeds"]  # the cache entry surfaces with the other feeds


async def test_status_reports_path_static(client, status_env):
    # PATH can loop in "failed" indefinitely (single system, so an empty load is a
    # total failure), so its warmup state must be visible in the operational
    # snapshot alongside subway_static and railroad_static. Railroad joins it there
    # only when EVERY system came back empty (R3); a partial load still reaches ready.
    app_module.app.state.path_static_status = "failed"
    res = await client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["path_static"] == "failed"


async def test_status_reports_ferry_static(client, status_env):
    # NYC Ferry is a single-system static group like PATH, so its warmup state
    # is likewise reported in the snapshot.
    app_module.app.state.ferry_static_status = "ready"
    res = await client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["ferry_static"] == "ready"


async def test_status_reports_ferry_feed_health_and_cache_entry(client, cache, status_env):
    # The ferry realtime feed rides the feeds map like the others, and its
    # single-feed health surfaces alongside subway_feeds/railroad_feeds/path_feeds.
    app_module.app.state.ferry_feed_health = {"total": 1, "ok": 1, "failed": []}
    res = await client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["ferry_feeds"] == {"total": 1, "ok": 1, "failed": []}
    assert "ferry" in body["feeds"]  # the cache entry surfaces with the other feeds


# ---------------- upstream error sanitization (no key/URL leakage) ----------------


async def test_bus_refresh_error_never_records_url_or_key(client, cache, monkeypatch):
    # httpx error text embeds the request URL — for the bus feed that URL
    # carries the API key, and recorded details are served to clients.
    async def boom(client_arg):
        raise httpx.ConnectError(
            "Connect failed for url "
            "'https://gtfsrt.prod.obanyc.com/vehiclePositions?key=SECRETVALUE123'"
        )

    monkeypatch.setattr(app_module, "fetch_vehicle_positions", boom)
    await app_module._refresh_buses(app_module.app, client=None)

    detail = cache["buses"]["error"]["detail"]
    assert cache["buses"]["error"]["status"] == 502
    assert "SECRETVALUE123" not in detail
    assert "https://" not in detail and "obanyc.com" not in detail

    # End to end: neither surface that serves the detail leaks it.
    for path in ("/api/buses", "/api/status"):
        res = await client.get(path)
        assert "SECRETVALUE123" not in res.text
        assert "obanyc.com" not in res.text


async def test_subway_refresh_error_never_records_url(client, cache, monkeypatch):
    async def boom(stops, client_arg):
        raise RuntimeError(
            "All subway feeds failed: ACE: timeout at https://api-endpoint.mta.info/x"
        )

    monkeypatch.setattr(app_module, "fetch_subway_trains", boom)
    app_module.app.state.subway_stops = {"101N": {}}
    await app_module._refresh_subways(app_module.app, client=None)

    detail = cache["subways"]["error"]["detail"]
    assert "https://" not in detail and "mta.info" not in detail
    assert "All subway feeds failed" in detail  # the useful part survives


async def test_subway_refresh_records_partial_feed_health(client, cache, monkeypatch):
    # A poll where some feed groups failed still returns data; the entry error
    # stays clear, but the partial outage must be recorded for /api/status.
    async def partial(stops, client_arg):
        return TRAINS, {}, 996.0, ["BDFM"], {"ACE": TRAINS}, {"ACE": {}}

    monkeypatch.setattr(app_module, "fetch_subway_trains", partial)
    app_module.app.state.subway_stops = {"101N": {}}
    await app_module._refresh_subways(app_module.app, client=None)

    assert cache["subways"]["error"] is None
    assert cache["subways"]["data"] == TRAINS
    total = len(feeds.SUBWAY_FEED_URLS)
    assert app_module.app.state.subway_feed_health == {
        "total": total,
        "ok": total - 1,
        "failed": ["BDFM"],
    }


async def test_subway_refresh_records_full_feed_health(client, cache, monkeypatch):
    async def full(stops, client_arg):
        return TRAINS, {}, 996.0, [], {"ACE": TRAINS}, {"ACE": {}}

    monkeypatch.setattr(app_module, "fetch_subway_trains", full)
    app_module.app.state.subway_stops = {"101N": {}}
    await app_module._refresh_subways(app_module.app, client=None)

    total = len(feeds.SUBWAY_FEED_URLS)
    assert app_module.app.state.subway_feed_health == {
        "total": total,
        "ok": total,
        "failed": [],
    }


async def test_subway_refresh_total_failure_marks_all_feeds_failed(client, cache, monkeypatch):
    async def boom(stops, client_arg):
        raise RuntimeError("All subway feeds failed: every group timed out")

    monkeypatch.setattr(app_module, "fetch_subway_trains", boom)
    app_module.app.state.subway_stops = {"101N": {}}
    await app_module._refresh_subways(app_module.app, client=None)

    total = len(feeds.SUBWAY_FEED_URLS)
    health = app_module.app.state.subway_feed_health
    assert health["total"] == total and health["ok"] == 0
    assert len(health["failed"]) == total
    assert cache["subways"]["error"]["status"] == 502


# ---------------- /api/subway-stops and /api/subway-arrivals ----------------


@pytest.fixture
def subway_state(cache):
    """Prime the station list and arrivals index without running the lifespan.
    The static group is marked ready (the warmup task would have set it)."""
    app_module.app.state.subway_static_status = "ready"
    app_module.app.state.subway_stations = {
        "A01": {"name": "Alpha", "lat": 40.7, "lon": -74.0},
    }
    app_module.app.state.subway_station_routes = {"A01": ["1", "2"]}  # H5
    app_module.app.state.subway_arrivals = {
        "A01": {"Northbound": [{"route_id": "1", "trip_id": "t1", "arrival": 1000.0}]}
    }
    return cache


async def test_subway_stops_lists_stations(client, subway_state):
    res = await client.get("/api/subway-stops")
    assert res.status_code == 200
    # routes serving the station ride along (H5).
    assert res.json() == [
        {"id": "A01", "name": "Alpha", "lat": 40.7, "lon": -74.0, "routes": ["1", "2"]}
    ]
    assert "max-age" in res.headers.get("cache-control", "")


async def test_subway_stops_routes_default_empty_when_index_absent(client, subway_state):
    # A ready static group whose routes-per-station index failed to build still
    # serves markers; the routes field just comes back empty, never missing.
    app_module.app.state.subway_station_routes = {}
    res = await client.get("/api/subway-stops")
    assert res.json() == [{"id": "A01", "name": "Alpha", "lat": 40.7, "lon": -74.0, "routes": []}]


async def test_subway_arrivals_warming_up_503(client, subway_state):
    # No successful subway poll yet (the cache fixture leaves data=None).
    res = await client.get("/api/subway-arrivals/A01")
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_subway_arrivals_known_station(client, subway_state, cache):
    cache["subways"].update(data=[], fetched_at=1234.0, error=None)  # a poll succeeded
    res = await client.get("/api/subway-arrivals/A01")
    assert res.status_code == 200
    body = res.json()
    assert body["station_id"] == "A01"
    assert body["station_name"] == "Alpha"
    assert body["fetched_at"] == 1234.0
    assert body["directions"]["Northbound"][0] == {
        "route_id": "1",
        "trip_id": "t1",
        "arrival": 1000.0,
    }
    assert body["directions"]["Southbound"] == []  # both keys always present


async def test_subway_arrivals_known_station_without_upcoming_trains(client, subway_state, cache):
    cache["subways"].update(data=[], fetched_at=1.0, error=None)
    app_module.app.state.subway_arrivals = {}  # valid station, nothing upcoming
    res = await client.get("/api/subway-arrivals/A01")
    assert res.status_code == 200
    assert res.json()["directions"] == {"Northbound": [], "Southbound": []}


async def test_subway_arrivals_unknown_station_404(client, subway_state, cache):
    cache["subways"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/subway-arrivals/ZZ9")
    assert res.status_code == 404


async def test_subway_arrivals_rejects_malformed_station_id(client, subway_state, cache):
    cache["subways"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/subway-arrivals/..%2Fevil")
    assert res.status_code == 404


# ---------------- /api/railroad-stops and /api/railroad-arrivals ----------------


@pytest.fixture
def railroad_state(cache):
    """Prime the per-system station lists and arrivals index without the lifespan.
    The static group is marked ready (the warmup task would have set it)."""
    app_module.app.state.railroad_static_status = "ready"
    app_module.app.state.railroad_stops = {
        "LIRR": {"12": {"name": "Jamaica", "lat": 40.7, "lon": -73.8}},
        "MNR": {"1": {"name": "Grand Central", "lat": 40.75, "lon": -73.97}},
    }
    # Routes-per-station index per system (H5), scoped so LIRR/MNR ids never mix.
    app_module.app.state.railroad_station_routes = {
        "LIRR": {"12": ["5", "8"]},
        "MNR": {"1": ["1"]},
    }
    app_module.app.state.railroad_arrivals = {
        "LIRR": {
            "12": {
                "Outbound": [
                    {"route_id": "5", "trip_id": "t1", "arrival": 1000.0, "train_num": "704"}
                ]
            }
        },
        "MNR": {
            "1": {
                "Trains": [
                    {"route_id": "1", "trip_id": "m1", "arrival": 1000.0, "train_num": "8765"}
                ]
            }
        },
    }
    return cache


async def test_railroad_stops_lists_stations_per_system(client, railroad_state):
    res = await client.get("/api/railroad-stops")
    assert res.status_code == 200
    assert res.json() == [
        {
            "system": "LIRR",
            "id": "12",
            "name": "Jamaica",
            "lat": 40.7,
            "lon": -73.8,
            "routes": ["5", "8"],
        },
        {
            "system": "MNR",
            "id": "1",
            "name": "Grand Central",
            "lat": 40.75,
            "lon": -73.97,
            "routes": ["1"],
        },
    ]
    assert "max-age" in res.headers.get("cache-control", "")


async def test_railroad_stops_skips_systems_without_static(client, cache):
    # A system whose static failed to load (None) contributes nothing; no crash.
    app_module.app.state.railroad_static_status = "ready"
    app_module.app.state.railroad_stops = {
        "LIRR": {"12": {"name": "Jamaica", "lat": 40.7, "lon": -73.8}},
        "MNR": None,
    }
    res = await client.get("/api/railroad-stops")
    assert res.status_code == 200
    assert [s["system"] for s in res.json()] == ["LIRR"]


async def test_railroad_arrivals_warming_up_503(client, railroad_state):
    # No successful railroad poll yet (the cache fixture leaves data=None).
    res = await client.get("/api/railroad-arrivals/LIRR/12")
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_railroad_arrivals_unknown_system_404(client, railroad_state, cache):
    cache["railroads"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/railroad-arrivals/NJT/1")
    assert res.status_code == 404


async def test_railroad_arrivals_unknown_stop_404(client, railroad_state, cache):
    cache["railroads"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/railroad-arrivals/LIRR/999")  # valid format, not a station
    assert res.status_code == 404


async def test_railroad_arrivals_rejects_malformed_stop_id(client, railroad_state, cache):
    cache["railroads"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/railroad-arrivals/LIRR/abc")  # non-numeric, fails the regex
    assert res.status_code == 404


async def test_railroad_arrivals_lirr_known_station(client, railroad_state, cache):
    cache["railroads"].update(data=[], fetched_at=1234.0, error=None)  # a poll succeeded
    res = await client.get("/api/railroad-arrivals/LIRR/12")
    assert res.status_code == 200
    body = res.json()
    assert body["system"] == "LIRR"
    assert body["stop_id"] == "12"
    assert body["stop_name"] == "Jamaica"
    assert body["fetched_at"] == 1234.0
    assert body["directions"] == {
        "Outbound": [{"route_id": "5", "trip_id": "t1", "arrival": 1000.0, "train_num": "704"}]
    }


async def test_railroad_arrivals_mnr_single_trains_bucket(client, railroad_state, cache):
    cache["railroads"].update(data=[], fetched_at=1234.0, error=None)
    res = await client.get("/api/railroad-arrivals/MNR/1")
    assert res.status_code == 200
    body = res.json()
    assert body["system"] == "MNR" and body["stop_name"] == "Grand Central"
    # MNR uses only the "Trains" bucket; no empty Outbound/Inbound emitted.
    assert set(body["directions"]) == {"Trains"}
    assert body["directions"]["Trains"][0]["train_num"] == "8765"


async def test_railroad_arrivals_empty_when_nothing_upcoming(client, railroad_state, cache):
    cache["railroads"].update(data=[], fetched_at=1.0, error=None)
    # Valid station, nothing upcoming.
    app_module.app.state.railroad_arrivals = {"LIRR": {}, "MNR": {}}
    res = await client.get("/api/railroad-arrivals/LIRR/12")
    assert res.status_code == 200
    assert res.json()["directions"] == {}  # no buckets fabricated for symmetry


# ---------------- /healthz readiness probe ----------------


@pytest.fixture
def healthz_env(cache, monkeypatch):
    # Bus index "ready" by default so it doesn't add a degraded reason; tests
    # that care about the index override it.
    monkeypatch.setattr(bus_static, "_status", "ready")
    return cache


def _fresh(entry, age=5.0):
    # Polled just now; content was `age` seconds old at that poll.
    now = time.time()
    entry.update(data=[1], fetched_at=now, feed_timestamp=now - age, error=None)


def _stale(entry, age=300.0):
    # Recent poll, but upstream content `age` seconds old (upstream staleness).
    now = time.time()
    entry.update(data=[1], fetched_at=now, feed_timestamp=now - age, error=None)


async def test_healthz_warming_is_degraded(client, healthz_env):
    # No feed filled yet (cold start, before first poll).
    res = await client.get("/healthz")
    assert res.status_code == 503
    assert res.json()["status"] == "fail"
    assert any("fresh" in r for r in res.json()["reasons"])


async def test_healthz_passes_with_one_fresh_feed(client, healthz_env):
    _fresh(healthz_env["buses"])
    res = await client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "pass"}


async def test_healthz_lenient_one_fresh_other_stale(client, healthz_env):
    _fresh(healthz_env["buses"])  # fresh
    _stale(healthz_env["subways"])  # 300s stale
    res = await client.get("/healthz")
    assert res.status_code == 200  # >= 1 fresh feed -> healthy


async def test_healthz_degraded_when_all_feeds_stale(client, healthz_env):
    _stale(healthz_env["buses"])
    _stale(healthz_env["subways"])
    res = await client.get("/healthz")
    assert res.status_code == 503
    assert any("fresh" in r for r in res.json()["reasons"])


async def test_healthz_degraded_when_bus_index_failed(client, healthz_env, monkeypatch):
    _fresh(healthz_env["buses"])  # feed is fresh...
    monkeypatch.setattr(bus_static, "_status", "failed")  # ...but the index failed
    res = await client.get("/healthz")
    assert res.status_code == 503
    assert any("index" in r for r in res.json()["reasons"])


async def test_healthz_building_index_stays_healthy(client, healthz_env, monkeypatch):
    _fresh(healthz_env["buses"])
    monkeypatch.setattr(bus_static, "_status", "building")  # cold-start build in progress
    res = await client.get("/healthz")
    assert res.status_code == 200  # building != failed -> no flap during warmup


async def test_healthz_degraded_at_exactly_the_threshold(client, healthz_env):
    # age == FEED_STALE_AFTER_S is stale on both sides (< boundary, matching the
    # frontend's >= warn), so a feed exactly at the threshold is not fresh.
    _stale(healthz_env["buses"], age=float(app_module.FEED_STALE_AFTER_S))
    _stale(healthz_env["subways"], age=float(app_module.FEED_STALE_AFTER_S))
    res = await client.get("/healthz")
    assert res.status_code == 503


async def test_healthz_fresh_with_unknown_feed_timestamp(client, healthz_env):
    # A feed can have data but no feed_timestamp (the feed omitted its header
    # time); unknown upstream age is tolerated as long as the poll is current.
    healthz_env["buses"].update(data=[1], fetched_at=time.time(), feed_timestamp=None, error=None)
    res = await client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "pass"}


async def test_healthz_degraded_when_poll_loop_stalled(client, healthz_env):
    # Upstream content was fresh at the last poll, but that poll was long ago
    # (a stuck poller serving frozen data) — the poll-age term must catch it.
    old = time.time() - 600
    healthz_env["buses"].update(data=[1], fetched_at=old, feed_timestamp=old - 5, error=None)
    res = await client.get("/healthz")
    assert res.status_code == 503


async def test_healthz_never_leaks_error_details(client, healthz_env):
    app_module._note_failure(healthz_env["buses"], 502, "boom at https://feed/x?key=SECRET")
    res = await client.get("/healthz")
    assert "SECRET" not in res.text and "https://" not in res.text


# ---------------- static frontend assets (no-cache for deploys) ----------------


@pytest.mark.parametrize("path", ["/", "/index.html", "/helpers.js", "/map.js", "/style.css"])
async def test_static_assets_sent_with_no_cache(client, path):
    # Unhashed assets under stable names: a deploy must be picked up immediately,
    # so they carry Cache-Control: no-cache (browser revalidates via the ETag).
    res = await client.get(path)
    assert res.status_code == 200
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers.get("etag")  # the ETag that makes revalidation a cheap 304


async def test_static_revalidation_is_a_cheap_304(client):
    # no-cache means revalidate, not refetch: a matching ETag returns an empty
    # 304 that still carries the directive, so an unchanged asset costs no body.
    first = await client.get("/helpers.js")
    res = await client.get("/helpers.js", headers={"If-None-Match": first.headers["etag"]})
    assert res.status_code == 304
    assert res.headers["cache-control"] == "no-cache"
    assert res.content == b""


# ---------------- /api/railroad-routes ----------------


async def test_railroad_routes_endpoint_flattens_and_caches(client):
    app_module.app.state.railroad_static_status = "ready"
    app_module.app.state.railroad_routes = {
        "LIRR": [
            {
                "route": "5",
                "name": "Montauk Branch",
                "polylines": [[[40.7, -74.0], [40.71, -74.01]]],
            }
        ],
        "MNR": [{"route": "9", "name": None, "polylines": [[[41.0, -73.0], [41.1, -73.1]]]}],
    }
    res = await client.get("/api/railroad-routes")
    assert res.status_code == 200
    assert res.json() == [
        {
            "system": "LIRR",
            "route": "5",
            "name": "Montauk Branch",  # rider-facing name carried through
            "polylines": [[[40.7, -74.0], [40.71, -74.01]]],
        },
        {
            "system": "MNR",
            "route": "9",
            "name": None,  # a route with no routes.txt name is still served
            "polylines": [[[41.0, -73.0], [41.1, -73.1]]],
        },
    ]
    assert "max-age" in res.headers.get("cache-control", "")


# ---------------- /api/path-stops and /api/path-routes ----------------

PATH_STOPS = {
    "26733": {"id": "26733", "name": "Newark", "lat": 40.73454, "lon": -74.16375},
    "26734": {"id": "26734", "name": "World Trade Center", "lat": 40.71271, "lon": -74.01193},
}
PATH_ROUTES = [
    {
        "id": "862",
        "name": "Newark - World Trade Center",
        "color": "d93a30",
        "text_color": "ffffff",
        "shape": [[[40.73454, -74.16375], [40.71271, -74.01193]]],
    }
]

# One load_path_static() return shape (parent station, child platform, one
# route), shared by the lifespan smoke test and the warmup state-machine tests
# so a change to the loaded table shape is edited in exactly one place.
PATH_STATIC_DATA = {
    "stops": {
        "26733": {"id": "26733", "name": "Newark", "lat": 40.7, "lon": -74.2},
        "26734": {"id": "26734", "name": "World Trade Center", "lat": 40.71, "lon": -74.01},
    },
    "child_to_parent": {"781718": "26733", "781750": "26734"},
    "trips": {"p1": {"route_id": "862", "direction_id": "0", "shape_id": "s1"}},
    "shapes": {"s1": [[40.7, -74.2], [40.71, -74.1]]},
    "routes": {
        "862": {
            "long_name": "Newark - World Trade Center",
            "short_name": None,
            "color": "d93a30",
            "text_color": "ffffff",
        }
    },
    # Child platform ids, as the real stop_times.txt lists them; the warmup
    # must resolve them to parents when it builds the station order.
    "stop_times": {"p1": ["781750", "781718"]},
}


async def test_path_stops_503_while_loading(client):
    app_module.app.state.path_static_status = "loading"
    res = await client.get("/api/path-stops")
    assert res.status_code == 503
    assert "loading" in res.json()["detail"].lower()


async def test_path_stops_served_with_max_age_when_ready(client):
    app_module.app.state.path_static_status = "ready"
    app_module.app.state.path_stops = PATH_STOPS
    # Routes-per-station index (H5): Newark serves 862 + the Harrison shuttle.
    app_module.app.state.path_station_routes = {"26733": ["74320", "862"], "26734": ["862"]}
    res = await client.get("/api/path-stops")
    assert res.status_code == 200
    assert res.json() == [
        {**PATH_STOPS["26733"], "routes": ["74320", "862"]},
        {**PATH_STOPS["26734"], "routes": ["862"]},
    ]
    assert "max-age" in res.headers.get("cache-control", "")


async def test_path_stops_routes_default_empty_when_index_absent(client):
    app_module.app.state.path_static_status = "ready"
    app_module.app.state.path_stops = PATH_STOPS
    app_module.app.state.path_station_routes = {}
    res = await client.get("/api/path-stops")
    assert res.json() == [{**s, "routes": []} for s in PATH_STOPS.values()]


async def test_path_stops_no_cache_empty_when_failed(client):
    # A failed (retrying) load serves [] under no-cache even if stale state is
    # present, so an empty 200 always means "ask again later", never success.
    app_module.app.state.path_static_status = "failed"
    app_module.app.state.path_stops = PATH_STOPS
    res = await client.get("/api/path-stops")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


async def test_path_routes_503_while_loading(client):
    app_module.app.state.path_static_status = "loading"
    res = await client.get("/api/path-routes")
    assert res.status_code == 503
    assert "loading" in res.json()["detail"].lower()


async def test_path_routes_served_with_branding_when_ready(client):
    app_module.app.state.path_static_status = "ready"
    app_module.app.state.path_routes = PATH_ROUTES
    res = await client.get("/api/path-routes")
    assert res.status_code == 200
    assert res.json() == PATH_ROUTES  # id/name/color/text_color/shape verbatim
    assert "max-age" in res.headers.get("cache-control", "")


async def test_path_routes_no_cache_empty_when_failed(client):
    app_module.app.state.path_static_status = "failed"
    app_module.app.state.path_routes = PATH_ROUTES
    res = await client.get("/api/path-routes")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


async def test_path_routes_ready_but_empty_is_no_cache(client):
    # The warmup gates "ready" on parent stops, not on built geometry, so a
    # degraded feed (stops parse, shapes do not) can reach ready with empty
    # routes. That empty list must be served no-cache, not under the ready
    # max-age, so a browser does not pin empty geometry for an hour ("empty 200
    # means ask again later"). Distinct from the failed case above: here the
    # group really is ready, there is just nothing drawable this load.
    app_module.app.state.path_static_status = "ready"
    app_module.app.state.path_routes = []
    res = await client.get("/api/path-routes")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


# ---------------- /api/ferry-stops and /api/ferry-routes (14a) ----------------

FERRY_STOPS = {
    "18": {
        "id": "18",
        "name": "Wall St/Pier 11",
        "lat": 40.70355,
        "lon": -74.00512,
        "wheelchair": True,
    },
    "2": {
        "id": "2",
        "name": "South Williamsburg",
        "lat": 40.70951,
        "lon": -73.96769,
        "wheelchair": False,
    },
}
FERRY_ROUTES = [
    {
        "id": "ER",
        "name": "East River",
        "color": "00839C",
        "text_color": "FFFFFF",
        "shape": [[[40.70951, -73.96769], [40.70355, -74.00512]]],
    }
]

# One load_ferry_static() return shape (flat stops, one route + shape + trip),
# shared by the lifespan smoke test and the warmup tests.
FERRY_STATIC_DATA = {
    "stops": {
        "18": {
            "id": "18",
            "name": "Wall St/Pier 11",
            "lat": 40.70355,
            "lon": -74.00512,
            "wheelchair": True,
        },
    },
    "trips": {"f1": {"route_id": "ER", "direction_id": "0", "shape_id": "s1", "headsign": None}},
    "shapes": {"s1": [[40.70951, -73.96769], [40.70355, -74.00512]]},
    "routes": {
        "ER": {
            "long_name": "East River",
            "short_name": None,
            "color": "00839C",
            "text_color": "FFFFFF",
        }
    },
}


async def test_ferry_stops_503_while_loading(client):
    app_module.app.state.ferry_static_status = "loading"
    res = await client.get("/api/ferry-stops")
    assert res.status_code == 503
    assert "loading" in res.json()["detail"].lower()


async def test_ferry_stops_served_with_max_age_when_ready(client):
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_stops = FERRY_STOPS
    # Routes-per-station index (H5): dock 18 is served by ER + SB, dock 2 by ER.
    app_module.app.state.ferry_station_routes = {"18": ["ER", "SB"], "2": ["ER"]}
    res = await client.get("/api/ferry-stops")
    assert res.status_code == 200
    # Each dock carries its wheelchair flag AND the routes serving it (H5).
    assert res.json() == [
        {**FERRY_STOPS["18"], "routes": ["ER", "SB"]},
        {**FERRY_STOPS["2"], "routes": ["ER"]},
    ]
    assert "max-age" in res.headers.get("cache-control", "")


async def test_ferry_stops_routes_default_empty_when_index_absent(client):
    # The committed trim carries no stop_times.txt, so the derive comes up empty;
    # the dock still serves with an empty routes list, never a missing field.
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_stops = FERRY_STOPS
    app_module.app.state.ferry_station_routes = {}
    res = await client.get("/api/ferry-stops")
    assert res.json() == [{**s, "routes": []} for s in FERRY_STOPS.values()]


async def test_ferry_stops_no_cache_empty_when_failed(client):
    app_module.app.state.ferry_static_status = "failed"
    app_module.app.state.ferry_stops = FERRY_STOPS
    res = await client.get("/api/ferry-stops")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


async def test_ferry_routes_served_with_branding_when_ready(client):
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_routes = FERRY_ROUTES
    res = await client.get("/api/ferry-routes")
    assert res.status_code == 200
    assert res.json() == FERRY_ROUTES
    assert "max-age" in res.headers.get("cache-control", "")


async def test_ferry_routes_no_cache_empty_when_failed(client):
    app_module.app.state.ferry_static_status = "failed"
    app_module.app.state.ferry_routes = FERRY_ROUTES
    res = await client.get("/api/ferry-routes")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


async def test_ferry_routes_ready_but_empty_is_no_cache(client):
    # Same guard as /api/path-routes: ready is gated on stops, so a degraded
    # feed can be ready with empty geometry; that empty list is served no-cache.
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_routes = []
    res = await client.get("/api/ferry-routes")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


# ---------------- /api/ferry and /api/ferry-arrivals (14b realtime) ----------------

FERRY_BOATS = [
    {
        "id": "H1",
        "label": "H201",
        "trip_id": "T-ER-1",
        "route_id": "ER",
        "latitude": 40.703,
        "longitude": -74.011,
        "speed": 6.5,
        "status": "IN_TRANSIT_TO",
        "updated_at": 1000.0,
    }
]
FERRY_ARRIVALS_INDEX = {
    "18": {
        "East River": [
            {"route_id": "ER", "trip_id": "T-ER-1", "arrival": 1500.0, "departure": 1560.0}
        ]
    }
}


async def test_ferry_feed_warming_up_503(client):
    res = await client.get("/api/ferry")
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_ferry_feed_serves_boats_envelope(client, cache):
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    res = await client.get("/api/ferry")
    assert res.status_code == 200
    # The envelope key is `boats` (not the MTA feeds' `data`); bearing is absent.
    body = res.json()
    assert body.pop("served_at") >= 1001.0
    assert body == {"fetched_at": 1001.0, "feed_timestamp": 996.0, "boats": FERRY_BOATS}
    assert "bearing" not in body["boats"][0]
    assert res.headers.get("cache-control") == "no-store"


async def test_ferry_feed_empty_boats_is_served_not_503(client, cache):
    # Overnight the boats go home: an empty list is a VALID served state once the
    # cache has filled, not a warming 503.
    cache["ferry"].update(data=[], fetched_at=1001.0, feed_timestamp=996.0, error=None)
    res = await client.get("/api/ferry")
    assert res.status_code == 200
    assert res.json()["boats"] == []


async def test_ferry_arrivals_503_while_cache_never_filled(client):
    res = await client.get("/api/ferry-arrivals/18")
    assert res.status_code == 503


async def test_ferry_arrivals_404_unknown_stop(client, cache):
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    app_module.app.state.ferry_stops = FERRY_STOPS
    res = await client.get("/api/ferry-arrivals/999")  # well-formed but not a stop
    assert res.status_code == 404


async def test_ferry_arrivals_404_malformed_stop(client, cache):
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    app_module.app.state.ferry_stops = FERRY_STOPS
    res = await client.get("/api/ferry-arrivals/not-a-number")
    assert res.status_code == 404


async def test_ferry_arrivals_served_for_known_stop(client, cache):
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    app_module.app.state.ferry_stops = FERRY_STOPS
    app_module.app.state.ferry_arrivals = FERRY_ARRIVALS_INDEX
    res = await client.get("/api/ferry-arrivals/18")
    assert res.status_code == 200
    assert res.json() == {
        "fetched_at": 1001.0,
        "stop_id": "18",
        "stop_name": "Wall St/Pier 11",
        "routes": FERRY_ARRIVALS_INDEX["18"],
    }


async def test_ferry_arrivals_empty_when_nothing_upcoming(client, cache):
    # A known dock with no rows in the index returns an empty routes dict, not 404.
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    app_module.app.state.ferry_stops = FERRY_STOPS
    app_module.app.state.ferry_arrivals = {}
    res = await client.get("/api/ferry-arrivals/18")
    assert res.status_code == 200
    assert res.json()["routes"] == {}


async def test_ferry_refresh_empty_success_replaces_boats(client, cache, monkeypatch):
    # THE reviewer-flagged divergence: an empty successful poll REPLACES the
    # boats (they went home), unlike a failed poll which retains last-known.
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1.0, feed_timestamp=1.0, error=None)

    async def empty(client_arg, static_arg):
        return [], {}, 997.0

    monkeypatch.setattr(app_module, "fetch_ferry_data", empty)
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_static = FERRY_STATIC_DATA
    await app_module._refresh_ferry(app_module.app, client=None)
    assert cache["ferry"]["data"] == []  # replaced, not retained
    assert cache["ferry"]["error"] is None
    assert cache["ferry"]["feed_timestamp"] == 997.0
    assert app_module.app.state.ferry_feed_health == {"total": 1, "ok": 1, "failed": []}


async def test_ferry_refresh_failure_retains_last_known(client, cache, monkeypatch):
    cache["ferry"].update(data=FERRY_BOATS, fetched_at=1.0, feed_timestamp=1.0, error=None)
    app_module.app.state.ferry_arrivals = FERRY_ARRIVALS_INDEX

    async def boom(client_arg, static_arg):
        raise httpx.ConnectError("ferry host down")

    monkeypatch.setattr(app_module, "fetch_ferry_data", boom)
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_static = FERRY_STATIC_DATA
    await app_module._refresh_ferry(app_module.app, client=None)
    assert cache["ferry"]["data"] == FERRY_BOATS  # last-known kept on failure
    assert cache["ferry"]["error"]["status"] == 502
    assert app_module.app.state.ferry_arrivals == FERRY_ARRIVALS_INDEX  # index untouched
    assert app_module.app.state.ferry_feed_health == {"total": 1, "ok": 0, "failed": ["ferry"]}


async def test_ferry_refresh_replaces_arrivals_on_success(client, cache, monkeypatch):
    app_module.app.state.ferry_arrivals = {"18": {"East River": [{"trip_id": "old"}]}}

    async def ok(client_arg, static_arg):
        return FERRY_BOATS, FERRY_ARRIVALS_INDEX, 996.0

    monkeypatch.setattr(app_module, "fetch_ferry_data", ok)
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_static = FERRY_STATIC_DATA
    await app_module._refresh_ferry(app_module.app, client=None)
    assert cache["ferry"]["data"] == FERRY_BOATS
    assert app_module.app.state.ferry_arrivals == FERRY_ARRIVALS_INDEX  # fully replaced


async def test_ferry_refresh_warming_while_static_not_ready(client, cache, monkeypatch):
    called = False

    async def fake(client_arg, static_arg):
        nonlocal called
        called = True
        return [], {}, 1.0

    monkeypatch.setattr(app_module, "fetch_ferry_data", fake)
    app_module.app.state.ferry_static_status = "loading"
    await app_module._refresh_ferry(app_module.app, client=None)
    assert not called  # the trip -> route join needs the static, so no fetch yet
    assert cache["ferry"]["error"]["status"] == 503


async def test_ferry_refresh_undecodable_body_records_502(client, cache, monkeypatch):
    from google.protobuf.message import DecodeError

    async def undecodable(client_arg, static_arg):
        raise DecodeError("not a protobuf")

    monkeypatch.setattr(app_module, "fetch_ferry_data", undecodable)
    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_static = FERRY_STATIC_DATA
    await app_module._refresh_ferry(app_module.app, client=None)
    assert cache["ferry"]["error"]["status"] == 502
    assert app_module.app.state.ferry_feed_health == {"total": 1, "ok": 0, "failed": ["ferry"]}


# ---------------- /api/path and /api/path-arrivals (13b realtime) ----------------

PATH_TRAINS = [
    {
        "trip_id": "5c0e8a4d-uuid",  # bridge ids are unstable; carried, never keyed on
        "route_id": "862",
        "latitude": 40.73454,
        "longitude": -74.16375,
        "stop_id": "26733",
        "stop_name": "Newark",
        "direction": "To New Jersey",
        "prev_lat": None,  # 13b: no carry-forward, prev is null on every train
        "prev_lon": None,
        "prev_time": None,
        "next_time": 1500.0,
    }
]
PATH_ARRIVALS = {"26733": {"To New Jersey": [{"route_id": "862", "arrival": 1500.0}]}}
# The SERVED shape (13d): what _refresh_path stores after the identity
# matcher, i.e. what /api/path actually returns. A stable minted `id`, no
# bridge trip hash.
PATH_SERVED_TRAINS = [{**{k: v for k, v in PATH_TRAINS[0].items() if k != "trip_id"}, "id": "t-1"}]


async def test_path_feed_warming_up_503(client):
    res = await client.get("/api/path")
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_path_feed_envelope_uses_trains_key(client, cache):
    cache["path"].update(
        data=PATH_SERVED_TRAINS, fetched_at=1001.0, feed_timestamp=996.0, error=None
    )
    res = await client.get("/api/path")
    assert res.status_code == 200
    # The envelope key is `trains` (not the MTA feeds' `data`), and the served
    # trains carry the stable synthetic id, never the bridge hash.
    body = res.json()
    assert body.pop("served_at") >= 1001.0
    assert body == {
        "fetched_at": 1001.0,
        "feed_timestamp": 996.0,
        "trains": PATH_SERVED_TRAINS,
    }
    assert res.headers.get("cache-control") == "no-store"


async def test_path_feed_stale_data_beats_subsequent_error(client, cache):
    cache["path"].update(
        data=PATH_SERVED_TRAINS, fetched_at=1001.0, feed_timestamp=996.0, error=None
    )
    app_module._note_failure(cache["path"], 502, "Upstream PATH bridge feed error: boom")
    res = await client.get("/api/path")
    assert res.status_code == 200
    assert res.json()["trains"] == PATH_SERVED_TRAINS  # last-known data still served


async def test_path_feed_never_filled_serves_recorded_error(client, cache):
    app_module._note_failure(cache["path"], 502, "Upstream PATH bridge feed error: boom")
    res = await client.get("/api/path")
    assert res.status_code == 502
    assert res.json()["detail"] == "Upstream PATH bridge feed error: boom"


@pytest.fixture
def path_rt_state(cache):
    """Prime the PATH static stops + arrivals index + 13d identity state
    without running the lifespan (which is what normally seeds them)."""
    app_module.app.state.path_stops = {
        "26733": {"id": "26733", "name": "Newark", "lat": 40.73454, "lon": -74.16375},
        "26734": {"id": "26734", "name": "World Trade Center", "lat": 40.71271, "lon": -74.01193},
    }
    app_module.app.state.path_arrivals = PATH_ARRIVALS
    app_module.app.state.path_identity = app_module.new_path_identity_state("t")
    app_module.app.state.path_station_order = {}
    return cache


async def test_path_arrivals_warming_up_503(client, path_rt_state):
    # No successful PATH poll yet (the cache fixture leaves data=None).
    res = await client.get("/api/path-arrivals/26733")
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_path_arrivals_known_station(client, path_rt_state, cache):
    cache["path"].update(data=[], fetched_at=1234.0, error=None)  # a poll succeeded
    res = await client.get("/api/path-arrivals/26733")
    assert res.status_code == 200
    body = res.json()
    assert body["stop_id"] == "26733"
    assert body["stop_name"] == "Newark"
    assert body["fetched_at"] == 1234.0
    # Rows are {route_id, arrival} only: the bridge hash reaches no payload.
    assert body["directions"] == {"To New Jersey": [{"route_id": "862", "arrival": 1500.0}]}


async def test_path_arrivals_empty_when_nothing_upcoming(client, path_rt_state, cache):
    cache["path"].update(data=[], fetched_at=1.0, error=None)
    app_module.app.state.path_arrivals = {}  # valid station, nothing upcoming
    res = await client.get("/api/path-arrivals/26734")
    assert res.status_code == 200
    assert res.json()["directions"] == {}  # no buckets fabricated


async def test_path_arrivals_unknown_stop_404(client, path_rt_state, cache):
    cache["path"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/path-arrivals/99999")  # valid format, not a station
    assert res.status_code == 404


async def test_path_arrivals_rejects_malformed_stop_id(client, path_rt_state, cache):
    cache["path"].update(data=[], fetched_at=1.0, error=None)
    res = await client.get("/api/path-arrivals/..%2Fevil")
    assert res.status_code == 404


# ---------------- _refresh_path: warming, success, failure ----------------


async def test_path_refresh_warming_while_static_loading(client, cache, caplog):
    # The 13a static group is not ready: the refresher notes a quiet 503 (the
    # transition log belongs to _set_static_status, not the 20s poll loop) and
    # never calls the fetch.
    app_module.app.state.path_stops = {}
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        await app_module._refresh_path(app_module.app, client=None)
    err = cache["path"]["error"]
    assert err["status"] == 503
    assert "loading" in err["detail"].lower()
    assert not [r for r in caplog.records if "feed poll failed" in r.getMessage()]


async def test_path_refresh_success_fills_cache_arrivals_and_health(client, path_rt_state, cache):
    async def ok(client_arg, stops_arg):
        return PATH_TRAINS, PATH_ARRIVALS, 996.0, 0

    orig = app_module.fetch_path_trains
    app_module.fetch_path_trains = ok
    try:
        await app_module._refresh_path(app_module.app, client=None)
    finally:
        app_module.fetch_path_trains = orig
    # The cache holds the SERVED shape: the matcher's stable id in place of
    # the bridge trip hash, everything else straight from the decode.
    served = cache["path"]["data"]
    assert [{k: v for k, v in t.items() if k != "id"} for t in served] == [
        {k: v for k, v in t.items() if k != "trip_id"} for t in PATH_TRAINS
    ]
    assert all(t["id"] and "trip_id" not in t for t in served)
    assert cache["path"]["feed_timestamp"] == 996.0
    assert cache["path"]["error"] is None
    assert app_module.app.state.path_arrivals == PATH_ARRIVALS
    assert app_module.app.state.path_feed_health == {
        "total": 1,
        "ok": 1,
        "failed": [],
        "unresolved": 0,
    }


async def test_path_refresh_carries_identity_across_churned_polls(client, path_rt_state, cache):
    # The point of 13d: two polls whose bridge hashes are fully disjoint but
    # whose content is the same physical train must serve the SAME stable id,
    # because _refresh_path threads the matcher state across polls.
    hashes = iter(["hash-gen-1", "hash-gen-2"])

    async def churning(client_arg, stops_arg):
        train = dict(PATH_TRAINS[0], trip_id=next(hashes))
        return [train], PATH_ARRIVALS, 996.0, 0

    orig = app_module.fetch_path_trains
    app_module.fetch_path_trains = churning
    try:
        await app_module._refresh_path(app_module.app, client=None)
        first_id = cache["path"]["data"][0]["id"]
        await app_module._refresh_path(app_module.app, client=None)
        second_id = cache["path"]["data"][0]["id"]
    finally:
        app_module.fetch_path_trains = orig
    assert first_id == second_id
    assert first_id.startswith("t-")  # the fixture's epoch, not a bridge hash


async def test_path_refresh_unresolved_drift_logs_on_transition_only(
    client, path_rt_state, cache, monkeypatch, caplog
):
    # A static-vs-bridge station-id drift (unresolved > 0) must be
    # operator-visible without per-poll spam: one warning when it appears,
    # silence while it persists, one info when it clears. The count rides
    # path_feed_health so /api/status shows it in between.
    counts = iter([3, 3, 0])

    async def drifting(client_arg, stops_arg):
        return PATH_TRAINS, PATH_ARRIVALS, 996.0, next(counts)

    monkeypatch.setattr(app_module, "fetch_path_trains", drifting)

    def drift_logs():
        return [
            r for r in caplog.records if "missing" in r.getMessage() and "PATH" in r.getMessage()
        ]

    with caplog.at_level(logging.INFO, logger=app_module.logger.name):
        await app_module._refresh_path(app_module.app, client=None)  # 0 -> 3: warn
        assert len(drift_logs()) == 1
        assert app_module.app.state.path_feed_health["unresolved"] == 3
        await app_module._refresh_path(app_module.app, client=None)  # 3 -> 3: silent
        assert len(drift_logs()) == 1
        await app_module._refresh_path(app_module.app, client=None)  # 3 -> 0: cleared info
        assert app_module.app.state.path_feed_health["unresolved"] == 0
        cleared = [r for r in caplog.records if "cleared" in r.getMessage()]
        assert len(cleared) == 1


async def test_path_refresh_failure_keeps_last_known(client, path_rt_state, cache, monkeypatch):
    # A poll that fails after a success keeps the last-known trains AND
    # arrivals (consistent with the other systems) while recording the error.
    cache["path"].update(data=PATH_TRAINS, fetched_at=1001.0, feed_timestamp=996.0, error=None)
    app_module.app.state.path_arrivals = PATH_ARRIVALS

    async def boom(client_arg, stops_arg):
        raise httpx.ConnectError("bridge down at https://path.transitdata.nyc/gtfsrt")

    monkeypatch.setattr(app_module, "fetch_path_trains", boom)
    await app_module._refresh_path(app_module.app, client=None)
    assert cache["path"]["data"] == PATH_TRAINS  # kept
    assert app_module.app.state.path_arrivals == PATH_ARRIVALS  # kept
    assert app_module.app.state.path_feed_health == {"total": 1, "ok": 0, "failed": ["PATH"]}
    detail = cache["path"]["error"]["detail"]
    assert cache["path"]["error"]["status"] == 502
    assert "https://" not in detail  # sanitized, no bridge URL leaked


async def test_path_refresh_undecodable_body_records_502(client, path_rt_state, cache, monkeypatch):
    from google.protobuf.message import DecodeError

    async def bad(client_arg, stops_arg):
        raise DecodeError("truncated")

    monkeypatch.setattr(app_module, "fetch_path_trains", bad)
    await app_module._refresh_path(app_module.app, client=None)
    assert cache["path"]["error"]["status"] == 502
    assert "undecodable" in cache["path"]["error"]["detail"]
    assert app_module.app.state.path_feed_health == {"total": 1, "ok": 0, "failed": ["PATH"]}


# ---------------- lifespan startup/shutdown smoke ----------------


async def test_lifespan_starts_polls_and_shuts_down_cleanly(monkeypatch):
    # ASGITransport never runs lifespan, so drive the contextmanager directly
    # (no extra dependency). Fake the static loaders and the upstream fetchers
    # so startup needs no network and the poll fills the cache instantly.
    async def fake_stops():
        return {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}

    async def fake_fetch_buses(client):
        return BUSES, 1000.0

    async def fake_fetch_subways(stops, client):
        return TRAINS, {}, 1001.0, [], {"ACE": TRAINS}, {"ACE": {}}

    async def fake_fetch_railroads(client, stops):
        return RAILROADS, {}, 1002.0, []

    async def fake_fetch_path(client, stops):
        return PATH_TRAINS, PATH_ARRIVALS, 1003.0, 0

    async def fake_fetch_ferry(client, ferry_static):
        # Stubbed like every other fetcher: ferry static warms to "ready" below,
        # so without this the poll loop would call the real fetch_ferry_data and
        # hit the network, breaking this test's no-network contract.
        return [], {}, 1004.0

    async def fake_load_railroad_static():
        # No network. LIRR carries stops/trips/shapes/routes; MNR failed (None).
        return {
            "LIRR": {
                "stops": {"1": {"name": "Aville", "lat": 40.7, "lon": -74.0}},
                "trips": {"t1": {"route_id": "5", "shape_id": "s1"}},
                "shapes": {"s1": [[40.7, -74.0], [40.71, -74.01]]},
                "routes": {"5": {"long_name": "Montauk Branch", "short_name": None}},
            },
            "MNR": None,
        }

    async def fake_load_path_static():
        return PATH_STATIC_DATA  # no network; shared module-level fixture shape

    async def fake_load_ferry_static():
        return FERRY_STATIC_DATA  # no network; shared module-level fixture shape

    async def fake_ensure_index():
        return None

    monkeypatch.setattr(app_module, "load_subway_stops", fake_stops)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: [])
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: {})
    # Patched so the lifespan warmup stays hermetic (the real loader parses the
    # committed 36 MB subway zip); the routes-per-station wiring is unit-tested above.
    monkeypatch.setattr(app_module, "load_subway_station_routes", lambda: {})
    monkeypatch.setattr(app_module, "fetch_vehicle_positions", fake_fetch_buses)
    monkeypatch.setattr(app_module, "fetch_subway_trains", fake_fetch_subways)
    monkeypatch.setattr(app_module, "fetch_railroad_trains", fake_fetch_railroads)
    monkeypatch.setattr(app_module, "fetch_path_trains", fake_fetch_path)
    monkeypatch.setattr(app_module, "fetch_ferry_data", fake_fetch_ferry)
    monkeypatch.setattr(
        app_module.railroad_static, "load_railroad_static", fake_load_railroad_static
    )
    monkeypatch.setattr(app_module.path_static, "load_path_static", fake_load_path_static)
    monkeypatch.setattr(app_module.ferry_static, "load_ferry_static", fake_load_ferry_static)
    monkeypatch.setattr(bus_static, "ensure_index", fake_ensure_index)

    app = app_module.app
    async with app_module.lifespan(app):
        # Immediately after entering (no await yet), the warmup tasks are created
        # but have not run: static state is still the "loading" initial values,
        # and the carry-forward memory is empty (the poll would fill it).
        assert app.state.subway_static_status == "loading"
        assert app.state.railroad_static_status == "loading"
        assert app.state.path_static_status == "loading"
        assert app.state.ferry_static_status == "loading"
        assert app.state.subway_stops is None
        assert app.state.path_stops == {}
        assert app.state.railroad_positions == {}
        assert app.state.subway_positions == {}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # The static loads run in the background now; wait for both to be ready.
            for _ in range(200):
                if (
                    app.state.subway_static_status == "ready"
                    and app.state.railroad_static_status == "ready"
                    and app.state.path_static_status == "ready"
                    and app.state.ferry_static_status == "ready"
                ):
                    break
                await asyncio.sleep(0.01)
            assert app.state.subway_static_status == "ready"
            assert app.state.railroad_static_status == "ready"
            assert app.state.path_static_status == "ready"
            assert app.state.ferry_static_status == "ready"
            assert app.state.ferry_stops == FERRY_STATIC_DATA["stops"]
            # The warmup filled the same fields the old synchronous load did.
            assert app.state.subway_stops == {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}
            assert app.state.railroad_static["LIRR"]["trips"] == {
                "t1": {"route_id": "5", "shape_id": "s1"}
            }
            assert app.state.railroad_stops["LIRR"] == {
                "1": {"name": "Aville", "lat": 40.7, "lon": -74.0}
            }
            assert app.state.railroad_stops["MNR"] is None  # failed system -> None, GPS-only
            # Route geometry built from the kept trips/shapes with the routes.txt
            # name; the failed MNR system gets an empty list, not a crash.
            assert app.state.railroad_routes["LIRR"] == [
                {
                    "route": "5",
                    "name": "Montauk Branch",
                    "polylines": [[[40.7, -74.0], [40.71, -74.01]]],
                }
            ]
            assert app.state.railroad_routes["MNR"] == []
            # PATH warmup filled its own namespaced fields (never merged into
            # the MTA stop tables), built the modal route geometry, and built
            # the 13d station order (child platform ids resolved to parents).
            assert app.state.path_stops == PATH_STATIC_DATA["stops"]
            assert app.state.path_static["child_to_parent"] == {
                "781718": "26733",
                "781750": "26734",
            }
            assert app.state.path_station_order == {("862", "0"): ["26734", "26733"]}
            assert app.state.path_routes == [
                {
                    "id": "862",
                    "name": "Newark - World Trade Center",
                    "color": "d93a30",
                    "text_color": "ffffff",
                    "shape": [[[40.7, -74.2], [40.71, -74.1]]],
                }
            ]
            # Wait for the background poll task's first cycle to fill the caches
            # (PATH included: its refresher needs the path static warmup done,
            # which the ready-wait above guaranteed).
            for _ in range(200):
                if (
                    app.state.feed_cache["buses"]["data"] is not None
                    and app.state.feed_cache["path"]["data"] is not None
                ):
                    break
                await asyncio.sleep(0.01)
            assert app.state.feed_cache["buses"]["data"] == BUSES
            assert app.state.feed_cache["buses"]["feed_timestamp"] == 1000.0
            # The same poll cycle refreshed PATH: cache filled, arrivals index
            # replaced, single-feed health recorded (the PATH static warmup
            # already populated path_stops above, so the refresher was not on
            # its warming path).
            served = app.state.feed_cache["path"]["data"]
            assert [{k: v for k, v in t.items() if k != "id"} for t in served] == [
                {k: v for k, v in t.items() if k != "trip_id"} for t in PATH_TRAINS
            ]
            assert all(t["id"] for t in served)
            assert app.state.feed_cache["path"]["feed_timestamp"] == 1003.0
            assert app.state.path_arrivals == PATH_ARRIVALS
            assert app.state.path_feed_health == {
                "total": 1,
                "ok": 1,
                "failed": [],
                "unresolved": 0,
            }
            # The same poll cycle refreshed the ferry realtime feed via the
            # stubbed fetch (no network): it returned no boats, a valid empty
            # poll, so the cache filled with [] and health recorded ok.
            assert app.state.feed_cache["ferry"]["data"] == []
            assert app.state.feed_cache["ferry"]["feed_timestamp"] == 1004.0
            assert app.state.ferry_feed_health == {"total": 1, "ok": 1, "failed": []}
            res = await c.get("/api/status")
            assert res.status_code == 200
            assert res.json()["feeds"]["buses"]["fetched_at"] is not None
            # The static group states are reported.
            assert res.json()["subway_static"] == "ready"
            assert res.json()["railroad_static"] == "ready"
            assert res.json()["path_static"] == "ready"
            assert res.json()["ferry_static"] == "ready"
        tasks = (
            app.state.feed_poll_task,
            app.state.bus_index_task,
            app.state.subway_static_task,
            app.state.railroad_static_task,
            app.state.path_static_task,
        )

    # Shutdown cancelled/awaited every background task (poll, bus index, both
    # static warmups).
    for task in tasks:
        assert task.done()


# ---------------- whole-request deadlines (R2) ----------------


async def test_poll_cycle_deadline_bounds_a_wedged_refresh(monkeypatch, cache):
    # R2: a single upstream that never completes must not freeze the whole cycle.
    # httpx's per-read timeout can't stop a trickle, so each refresh runs under its
    # own REFRESH_DEADLINE_S inside the gather. Wedge the bus feed (it fills once,
    # then blocks forever) and assert: the healthy systems keep advancing across
    # cycles, and the wedged one records a sanitized 504 while KEEPING its last-known
    # data. Shrink the deadline + cadence so this resolves in well under a second.
    import pollers

    monkeypatch.setattr(pollers, "REFRESH_DEADLINE_S", 0.2)
    monkeypatch.setattr(pollers, "POLL_INTERVAL_S", 0.01)

    app = app_module.app
    app.state.subway_stops = {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}
    app.state.path_stops = {}  # PATH takes its warming shortcut (no path_identity needed)
    app.state.ferry_static_status = "loading"  # ferry takes its warming shortcut too

    hang = asyncio.Event()  # never set: the bus fetch blocks on it forever
    calls = {"bus": 0}

    async def bus_fetch(client):
        calls["bus"] += 1
        if calls["bus"] == 1:
            return BUSES, 1000.0  # first cycle fills the cache (the last-known data)
        await hang.wait()  # every later cycle wedges past the deadline

    async def sub_fetch(stops, client):
        return TRAINS, {}, 1001.0, [], {"ACE": TRAINS}, {"ACE": {}}

    async def rr_fetch(client, stops):
        return RAILROADS, {}, 1002.0, []

    monkeypatch.setattr(app_module, "fetch_vehicle_positions", bus_fetch)
    monkeypatch.setattr(app_module, "fetch_subway_trains", sub_fetch)
    monkeypatch.setattr(app_module, "fetch_railroad_trains", rr_fetch)

    task = asyncio.create_task(pollers._poll_feeds(app))
    try:
        for _ in range(300):  # wait for cycle 1 to fill the bus cache
            if cache["buses"]["data"] is not None:
                break
            await asyncio.sleep(0.005)
        assert cache["buses"]["data"] == BUSES
        assert cache["subways"]["data"] == TRAINS
        bus_fetched_at = cache["buses"]["fetched_at"]
        subways_fetched_at = cache["subways"]["fetched_at"]

        for _ in range(300):  # wait for a later cycle to time the bus refresh out
            if cache["buses"]["error"] and cache["subways"]["fetched_at"] > subways_fetched_at:
                break
            await asyncio.sleep(0.005)

        # The wedged system: a sanitized 504, last-known data kept, poll time held.
        assert cache["buses"]["error"]["status"] == 504
        assert "deadline" in cache["buses"]["error"]["detail"]
        assert "http" not in cache["buses"]["error"]["detail"].lower()  # no URL leak
        assert cache["buses"]["data"] == BUSES  # last-known kept
        assert cache["buses"]["fetched_at"] == bus_fetched_at  # a failed poll never advances it
        # The healthy systems finished their cycles and kept advancing (the whole
        # cycle was NOT frozen by the wedged bus feed).
        assert cache["subways"]["data"] == TRAINS
        assert cache["subways"]["fetched_at"] > subways_fetched_at
        assert cache["railroads"]["data"] == RAILROADS
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_alerts_loop_deadline_bounds_a_wedged_refresh(monkeypatch):
    # R2: the alerts loop gets the same whole-task deadline (REFRESH_DEADLINE_S is
    # under the 60s alerts cadence). Wedge the alerts fetch after it fills once and
    # assert the loop records a sanitized 504 while keeping the last-known index.
    import pollers

    monkeypatch.setattr(pollers, "REFRESH_DEADLINE_S", 0.2)
    monkeypatch.setattr(pollers, "ALERT_POLL_INTERVAL_S", 0.01)

    app = app_module.app
    app.state.alerts_cache = app_module._fresh_alerts_entry()

    hang = asyncio.Event()
    calls = {"n": 0}

    async def alerts_fetch(client):
        calls["n"] += 1
        if calls["n"] == 1:
            return [], 0, []  # a successful empty poll fills the index
        await hang.wait()  # every later poll wedges past the deadline

    monkeypatch.setattr(app_module, "fetch_service_alerts", alerts_fetch)

    task = asyncio.create_task(pollers._poll_alerts(app))
    try:
        for _ in range(300):  # wait for the first poll to fill the index
            if app.state.alerts_cache["fetched_at"] is not None:
                break
            await asyncio.sleep(0.005)
        assert app.state.alerts_cache["alerts"] == []
        fetched_at = app.state.alerts_cache["fetched_at"]

        for _ in range(300):  # wait for a later poll to time out
            if app.state.alerts_cache["error"]:
                break
            await asyncio.sleep(0.005)

        assert app.state.alerts_cache["error"]["status"] == 504
        assert "deadline" in app.state.alerts_cache["error"]["detail"]
        assert app.state.alerts_cache["alerts"] == []  # last-known index kept
        assert app.state.alerts_cache["fetched_at"] == fetched_at  # not advanced by the failed poll
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---------------- background static warmup state machine ----------------

SUBWAY_STOPS = {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}


def _fake_app(**state):
    return types.SimpleNamespace(state=types.SimpleNamespace(**state))


async def test_subway_static_warmup_loading_to_ready(monkeypatch):
    async def fake_stops():
        return SUBWAY_STOPS

    routes = [{"route": "1", "polylines": []}]
    monkeypatch.setattr(app_module, "load_subway_stops", fake_stops)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: routes)
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: SUBWAY_STOPS)
    # Patched to stay hermetic: the real loader parses the committed 36 MB zip.
    monkeypatch.setattr(app_module, "load_subway_station_routes", lambda: {"101": ["1"]})
    app = _fake_app(subway_static_status="loading")
    await app_module._warm_subway_static(app)
    assert app.state.subway_static_status == "ready"
    assert app.state.subway_stops == SUBWAY_STOPS
    assert app.state.subway_routes == [{"route": "1", "polylines": []}]
    assert app.state.subway_stations == SUBWAY_STOPS
    assert app.state.subway_station_routes == {"101": ["1"]}  # routes-per-station wired (H5)


async def test_subway_static_warmup_retries_after_failure(monkeypatch):
    # loading -> failed -> retry -> ready, driven with the retry interval shortened.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    gate = {"ok": False}

    async def gated_stops():
        if not gate["ok"]:
            raise RuntimeError("network blip")
        return SUBWAY_STOPS

    monkeypatch.setattr(app_module, "load_subway_stops", gated_stops)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: [])
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: {})
    monkeypatch.setattr(app_module, "load_subway_station_routes", lambda: {})  # hermetic
    app = _fake_app(subway_static_status="loading")
    task = asyncio.create_task(app_module._warm_subway_static(app))
    try:
        for _ in range(200):  # wait until the first attempt has failed
            if app.state.subway_static_status == "failed":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "failed"
        gate["ok"] = True  # let the next retry succeed
        for _ in range(200):
            if app.state.subway_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "ready"
        assert app.state.subway_stops == SUBWAY_STOPS
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_subway_static_warmup_cancels_cleanly_during_retry_sleep(monkeypatch):
    # Parked in the retry sleep (loader always fails, ceiling set huge): a cancel
    # must complete promptly rather than wait the delay out (the clean-shutdown-
    # during-retry invariant). With the backoff schedule the first rung is 15s,
    # well above the 5s assertion timeout below, so the test still fails if a
    # cancel during the sleep stopped working; the huge STATIC_RETRY_S keeps every
    # LATER rung long too, so this holds no matter which rung it parks on.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 3600)

    async def always_fails():
        raise RuntimeError("down")

    monkeypatch.setattr(app_module, "load_subway_stops", always_fails)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: [])
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: {})
    app = _fake_app(subway_static_status="loading")
    task = asyncio.create_task(app_module._warm_subway_static(app))
    for _ in range(200):  # wait until it parks in the retry sleep
        if app.state.subway_static_status == "failed":
            break
        await asyncio.sleep(0.005)
    assert app.state.subway_static_status == "failed"
    task.cancel()
    # Finishes well within the parked delay (15s at minimum); wait_for raises if it hung.
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=5)
    assert task.cancelled()


async def test_subway_static_warmup_attempt_deadline_then_recovers(monkeypatch):
    # R2: a warmup attempt that never completes must not stall the retry loop
    # forever. The load runs under STATIC_ATTEMPT_DEADLINE_S; a timeout raises
    # TimeoutError, which the existing `except Exception` catches and drives down the
    # same failed -> retry path as any other load failure. Shrink the attempt
    # deadline (and the retry interval) so the first attempt times out fast, then let
    # the retry succeed. Mirrors test_subway_static_warmup_retries_after_failure but
    # the failure is a DEADLINE, not a raised error.
    monkeypatch.setattr(app_module, "STATIC_ATTEMPT_DEADLINE_S", 0.05)
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    gate = {"ok": False}
    hang = asyncio.Event()  # never set: the first attempt blocks past the deadline

    async def gated_stops():
        if not gate["ok"]:
            await hang.wait()  # exceeds STATIC_ATTEMPT_DEADLINE_S -> TimeoutError
        return SUBWAY_STOPS

    monkeypatch.setattr(app_module, "load_subway_stops", gated_stops)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: [])
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: {})
    monkeypatch.setattr(app_module, "load_subway_station_routes", lambda: {})  # hermetic
    app = _fake_app(subway_static_status="loading")
    task = asyncio.create_task(app_module._warm_subway_static(app))
    try:
        for _ in range(200):  # wait until the first attempt has TIMED OUT into failed
            if app.state.subway_static_status == "failed":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "failed"
        gate["ok"] = True  # let the next retry complete within the deadline
        for _ in range(200):
            if app.state.subway_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "ready"
        assert app.state.subway_stops == SUBWAY_STOPS
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_subway_refresh_warming_does_not_log_per_poll(client, cache, caplog):
    # The stops-absent warming path sets the cache 503 but must NOT log a warning:
    # it recurs every poll, so the only log is the single _set_static_status
    # transition, not per-poll spam.
    app_module.app.state.subway_stops = None
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        await app_module._refresh_subways(app_module.app, client=None)
    assert cache["subways"]["error"]["status"] == 503
    assert not [r for r in caplog.records if "feed poll failed" in r.getMessage()]


async def test_railroad_static_warmup_loading_to_ready(monkeypatch):
    async def fake_load():
        return {
            "LIRR": {
                "stops": {"1": {"name": "Aville", "lat": 40.7, "lon": -74.0}},
                "trips": {"t1": {"route_id": "5", "shape_id": "s1"}},
                "shapes": {"s1": [[40.7, -74.0], [40.71, -74.01]]},
                "routes": {"5": {"long_name": "Montauk Branch", "short_name": None}},
            },
            "MNR": None,  # failed system -> None, GPS-only
        }

    monkeypatch.setattr(app_module.railroad_static, "load_railroad_static", fake_load)
    app = _fake_app(railroad_static_status="loading")
    await app_module._warm_railroad_static(app)
    # A PARTIAL result still reaches ready, and R3 deliberately did not change that:
    # one system down is a real degraded state worth serving, because the surviving
    # system's stops still place its trains. Only the ALL-systems-None case became a
    # failed attempt (see the test below); this pins the boundary between them.
    assert app.state.railroad_static_status == "ready"  # ready even with a None system
    assert app.state.railroad_stops["LIRR"] == {"1": {"name": "Aville", "lat": 40.7, "lon": -74.0}}
    assert app.state.railroad_stops["MNR"] is None
    assert app.state.railroad_routes["LIRR"][0]["name"] == "Montauk Branch"
    assert app.state.railroad_routes["MNR"] == []


async def test_railroad_static_warmup_all_systems_none_is_failed_then_recovers(monkeypatch, caplog):
    # R3: a load where EVERY system came back None used to be marked ready and never
    # retried, so a boot-time outage that cleared a minute later stayed broken until
    # the next deploy. It is now a failed attempt that retries like any other.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)  # caps every schedule rung
    gate = {"ok": False}
    healthy = {
        "LIRR": {
            "stops": {"1": {"name": "Aville", "lat": 40.7, "lon": -74.0}},
            "trips": {"t1": {"route_id": "5", "shape_id": "s1"}},
            "shapes": {"s1": [[40.7, -74.0], [40.71, -74.01]]},
            "routes": {"5": {"long_name": "Montauk Branch", "short_name": None}},
        },
        "MNR": None,
    }

    async def gated_load():
        return healthy if gate["ok"] else {"LIRR": None, "MNR": None}

    monkeypatch.setattr(app_module.railroad_static, "load_railroad_static", gated_load)
    app = _fake_app(railroad_static_status="loading")
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        task = asyncio.create_task(app_module._warm_railroad_static(app))
        try:
            for _ in range(200):  # wait until the all-None attempt is marked failed
                if app.state.railroad_static_status == "failed":
                    break
                await asyncio.sleep(0.005)
            assert app.state.railroad_static_status == "failed"
            # A total failure must not publish empty state: nothing was assigned, so
            # the handlers keep reporting the failed group rather than serving blanks.
            assert getattr(app.state, "railroad_static", None) is None
            gate["ok"] = True  # the next retry loads
            for _ in range(200):
                if app.state.railroad_static_status == "ready":
                    break
                await asyncio.sleep(0.005)
            assert app.state.railroad_static_status == "ready"
            assert app.state.railroad_stops["LIRR"] == {
                "1": {"name": "Aville", "lat": 40.7, "lon": -74.0}
            }
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    # NO DOUBLE-LOGGING: many attempts failed while gated, but the transition-only
    # guard in _set_static_status (which every failure path reaches through the one
    # shared _fail_and_wait) logged the failure exactly once.
    failures = [r for r in caplog.records if "railroad_static_status failed" in r.getMessage()]
    assert len(failures) == 1


async def test_railroad_static_warmup_all_systems_parsed_but_empty_is_also_failed(monkeypatch):
    # The aggregate test is EMPTINESS, not None-ness, matching the PATH and ferry
    # empty-result paths. A system that downloaded and parsed but yielded ZERO stops
    # places no trains and would serve [] under a one-hour cache header, so it must
    # not be the thing that rescues the group from retrying just by being non-None.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)

    async def parsed_but_empty():
        return {
            "LIRR": {"stops": {}, "trips": {}, "shapes": {}, "routes": {}},
            "MNR": None,
        }

    monkeypatch.setattr(app_module.railroad_static, "load_railroad_static", parsed_but_empty)
    app = _fake_app(railroad_static_status="loading")
    task = asyncio.create_task(app_module._warm_railroad_static(app))
    try:
        for _ in range(200):
            if app.state.railroad_static_status == "failed":
                break
            await asyncio.sleep(0.005)
        assert app.state.railroad_static_status == "failed"  # never settles as ready
        assert getattr(app.state, "railroad_static", None) is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---------------- warmup retry backoff schedule (R3) ----------------


def test_retry_delay_walks_the_schedule_then_holds_at_the_last_rung():
    # rand fixed at the midpoint, so jitter contributes exactly 1.0 and the rungs
    # themselves are what is asserted.
    def mid():
        return 0.5

    rungs = [warmups._retry_delay(attempt, rand=mid) for attempt in range(6)]
    assert rungs[:4] == list(app_module.STATIC_RETRY_SCHEDULE_S)
    assert rungs[0] == 15  # the first retry is 15s, not the old flat 300s
    # Past the end of the schedule it holds at the steady-state rung forever rather
    # than growing without bound.
    assert rungs[4] == rungs[5] == app_module.STATIC_RETRY_S


def test_retry_delay_jitter_stays_within_ten_percent():
    # The bounds are exact at the extremes of the injected source, so a stampede
    # across the four warmup tasks is broken up without the delay drifting far.
    assert warmups._retry_delay(0, rand=lambda: 0.0) == 15 * 0.9
    assert warmups._retry_delay(0, rand=lambda: 1.0) == pytest.approx(15 * 1.1)
    for attempt in range(6):
        nominal = min(
            app_module.STATIC_RETRY_SCHEDULE_S[min(attempt, 3)], app_module.STATIC_RETRY_S
        )
        sampled = warmups._retry_delay(attempt)  # the real random source
        assert 0.9 * nominal <= sampled <= 1.1 * nominal


def test_retry_delay_caps_every_rung_at_static_retry_s(monkeypatch):
    # STATIC_RETRY_S stays the single ceiling knob AND the documented monkeypatch
    # surface: shrinking it shrinks every rung, which is exactly what lets the
    # warmup state-machine tests above drive retries instantly.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    for attempt in range(6):
        assert warmups._retry_delay(attempt, rand=lambda: 0.5) == 0.01


async def test_fail_and_wait_sleeps_the_delay_it_reported(monkeypatch):
    # _fail_and_wait must actually WAIT the delay it computed, and hand that same
    # delay to the status log: a version that recorded the failure but skipped (or
    # shortened) the sleep would turn a backoff into a hot loop against a dead
    # upstream, and one that logged a different number than it slept would be a
    # quieter lie. Capture both sides and assert they agree.
    slept = []
    reported = {}

    async def fake_sleep(seconds):
        slept.append(seconds)

    def fake_status(app, field, status, exc=None, retry_in_s=None):
        reported["status"] = status
        reported["retry_in_s"] = retry_in_s
        setattr(app.state, field, status)

    monkeypatch.setattr(warmups.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(warmups, "_set_static_status", fake_status)
    app = _fake_app(subway_static_status="loading")

    nxt = await warmups._fail_and_wait(app, "subway_static_status", RuntimeError("down"), 0)

    assert nxt == 1  # the attempt index advances, or the schedule never progresses
    assert len(slept) == 1
    assert reported["status"] == "failed"
    assert slept[0] == reported["retry_in_s"]  # slept exactly what it announced
    assert 0.9 * 15 <= slept[0] <= 1.1 * 15  # and that is the first rung


@pytest.mark.parametrize(
    ("warm", "field", "patch"),
    [
        ("_warm_subway_static", "subway_static_status", "subway_raise"),
        ("_warm_railroad_static", "railroad_static_status", "railroad_raise"),
        ("_warm_railroad_static", "railroad_static_status", "railroad_empty"),
        ("_warm_path_static", "path_static_status", "path_raise"),
        ("_warm_path_static", "path_static_status", "path_empty"),
        ("_warm_ferry_static", "ferry_static_status", "ferry_raise"),
        ("_warm_ferry_static", "ferry_static_status", "ferry_empty"),
    ],
)
async def test_every_warmup_failure_path_advances_the_schedule(monkeypatch, warm, field, patch):
    # EVERY failure path must thread the attempt counter through _fail_and_wait, or
    # that path silently pins itself to the first rung and retries every 15s forever
    # against a dead upstream. This covers all seven: each of the four groups' raise
    # path, plus the empty-result path that PATH, ferry, and (since R3) railroad each
    # add. A path that forgot to reassign `attempt` shows up here as a repeated first
    # rung instead of a rising one.
    attempts = []
    real_delay = warmups._retry_delay

    def recording(attempt, rand=None):
        attempts.append(attempt)
        return 0.0 if len(attempts) < 4 else real_delay(attempt, rand=lambda: 0.5)

    monkeypatch.setattr(warmups, "_retry_delay", recording)

    async def raiser():
        raise RuntimeError("down")

    async def empty_dict():
        return {}

    async def railroad_empty():
        return {"LIRR": None, "MNR": None}

    if patch == "subway_raise":
        monkeypatch.setattr(app_module, "load_subway_stops", raiser)
    elif patch == "railroad_raise":
        monkeypatch.setattr(app_module.railroad_static, "load_railroad_static", raiser)
    elif patch == "railroad_empty":
        monkeypatch.setattr(app_module.railroad_static, "load_railroad_static", railroad_empty)
    elif patch == "path_raise":
        monkeypatch.setattr(app_module.path_static, "load_path_static", raiser)
    elif patch == "path_empty":
        monkeypatch.setattr(app_module.path_static, "load_path_static", empty_dict)
    elif patch == "ferry_raise":
        monkeypatch.setattr(app_module.ferry_static, "load_ferry_static", raiser)
    elif patch == "ferry_empty":
        monkeypatch.setattr(app_module.ferry_static, "load_ferry_static", empty_dict)

    app = _fake_app(**{field: "loading"})
    task = asyncio.create_task(getattr(app_module, warm)(app))
    try:
        for _ in range(400):  # let it fail several times over
            if len(attempts) >= 4:
                break
            await asyncio.sleep(0.005)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # Strictly increasing from zero: this path walks the schedule rather than
    # resetting to (or sticking on) the first rung.
    assert attempts[:4] == [0, 1, 2, 3]


async def test_escalating_to_a_longer_rung_logs_once_per_step(monkeypatch, caplog):
    # The transition-only status log fires once, naming the FIRST rung, so without
    # this the log would claim a 15s cadence for an outage that has settled onto the
    # 300s one. Each escalation logs exactly once (bounded by the schedule length),
    # and jitter alone never logs.
    # Sleep is stubbed out rather than the delay, so _retry_delay and _rung both run
    # for real and the NUMBER each line reports is exercised, not just how many lines
    # there are. Jitter is dropped (delay == the exact rung) so the reported cadence
    # can be asserted verbatim: a version that interpolated the previous rung, or the
    # uncapped schedule entry, would still emit the right COUNT of lines and only a
    # text assertion catches it. monkeypatch undoes both at teardown.
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(warmups.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(warmups, "_retry_delay", lambda attempt, rand=None: warmups._rung(attempt))
    app = _fake_app(subway_static_status="loading")
    with caplog.at_level(logging.WARNING, logger=app_module.logger.name):
        for attempt in range(6):
            await warmups._fail_and_wait(app, "subway_static_status", RuntimeError("down"), attempt)

    transitions = [r for r in caplog.records if "failed to load" in r.getMessage()]
    escalations = [r for r in caplog.records if "backing off" in r.getMessage()]
    assert len(transitions) == 1  # still exactly one transition line, as before
    # The transition names the FIRST rung, which is the whole reason the escalations
    # have to exist.
    first_rung = app_module.STATIC_RETRY_SCHEDULE_S[0]
    assert f"retrying in {first_rung:.0f}s" in transitions[0].getMessage()
    # One escalation per step up, each naming the rung it just moved TO; attempts 4
    # and 5 sit on the same final rung and stay silent, so this cannot become spam.
    assert [r.getMessage() for r in escalations] == [
        f"subway_static_status still failing (down); backing off, next retry in {rung:.0f}s"
        for rung in app_module.STATIC_RETRY_SCHEDULE_S[1:]
    ]


def test_failure_log_never_reports_an_empty_reason():
    # str(TimeoutError()) is "", and the R2 attempt deadline raises exactly that, so
    # a naive "%s" would log "failed to load ()" and name no cause for the single
    # most interesting failure. Every exception must describe itself.
    assert warmups._describe(TimeoutError()) == "TimeoutError"
    assert warmups._describe(RuntimeError("boom")) == "boom"
    assert warmups._describe(None) == "unknown"


async def test_static_warmup_retries_land_inside_the_healthcheck_window(monkeypatch):
    # THE COLD-START COLLISION THIS PINS: railway.json sets healthcheckTimeout, and
    # the retry interval used to be a FLAT STATIC_RETRY_S equal to it. A transient
    # first-attempt failure therefore got its next attempt only at the very edge of
    # the deployment window, i.e. no real second chance inside it. The schedule's
    # early rungs must fit two more attempts comfortably inside that window.
    #
    # The window is READ FROM railway.json rather than hardcoded, so this guards the
    # coupling in BOTH directions the README documents: lengthening the early rungs
    # fails here, and so does lowering healthcheckTimeout underneath them.
    railway = json.loads((Path(__file__).resolve().parents[2] / "railway.json").read_text())
    HEALTHCHECK_TIMEOUT_S = railway["deploy"]["healthcheckTimeout"]
    real_delay = warmups._retry_delay
    delays = []

    def recording(attempt, rand=None):
        # Compute the REAL scheduled delay (jitter pinned mid-range so the assertion
        # is exact), record it, then return 0 so the wait is simulated, not slept.
        delay = real_delay(attempt, rand=lambda: 0.5)
        delays.append(delay)
        return 0.0

    monkeypatch.setattr(warmups, "_retry_delay", recording)
    calls = {"n": 0}

    async def flaky_stops():
        calls["n"] += 1
        if calls["n"] <= 2:  # two transient cold-start failures, then healthy
            raise RuntimeError("cold-start blip")
        return SUBWAY_STOPS

    monkeypatch.setattr(app_module, "load_subway_stops", flaky_stops)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: [])
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: {})
    monkeypatch.setattr(app_module, "load_subway_station_routes", lambda: {})  # hermetic
    app = _fake_app(subway_static_status="loading")
    await app_module._warm_subway_static(app)  # terminates: the third attempt loads

    assert app.state.subway_static_status == "ready"
    assert delays == [15, 30]  # the first two rungs, not two flat 300s waits
    # Both retries started, and the group reached ready, well inside the window the
    # old flat interval only just reached with its FIRST retry.
    assert sum(delays) <= HEALTHCHECK_TIMEOUT_S / 2


async def test_path_static_warmup_loading_to_ready(monkeypatch):
    async def fake_load():
        return PATH_STATIC_DATA

    monkeypatch.setattr(app_module.path_static, "load_path_static", fake_load)
    app = _fake_app(path_static_status="loading")
    await app_module._warm_path_static(app)
    assert app.state.path_static_status == "ready"
    assert app.state.path_stops == PATH_STATIC_DATA["stops"]
    assert app.state.path_static["child_to_parent"] == {"781718": "26733", "781750": "26734"}
    assert app.state.path_routes[0]["id"] == "862"
    assert app.state.path_routes[0]["color"] == "d93a30"
    # The 13d successor relation, built from stop_times with the child ids
    # resolved to the parent stations the realtime bridge uses.
    assert app.state.path_station_order == {("862", "0"): ["26734", "26733"]}


async def test_path_static_warmup_empty_result_is_failed_then_recovers(monkeypatch):
    # load_path_static is lenient ({} on failure, no exception), and PATH is a
    # single system: an empty result must drive the FAILED state (unlike the
    # railroad group, which reaches ready with a None system) so the endpoints
    # keep serving the no-cache empty until a retry brings real data.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    gate = {"ok": False}

    async def gated_load():
        return PATH_STATIC_DATA if gate["ok"] else {}

    monkeypatch.setattr(app_module.path_static, "load_path_static", gated_load)
    app = _fake_app(path_static_status="loading")
    task = asyncio.create_task(app_module._warm_path_static(app))
    try:
        for _ in range(200):  # wait until the empty attempt has failed
            if app.state.path_static_status == "failed":
                break
            await asyncio.sleep(0.005)
        assert app.state.path_static_status == "failed"
        gate["ok"] = True  # let the next retry succeed
        for _ in range(200):
            if app.state.path_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        assert app.state.path_static_status == "ready"
        assert app.state.path_stops == PATH_STATIC_DATA["stops"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_ferry_static_warmup_loading_to_ready(monkeypatch):
    async def fake_load():
        return FERRY_STATIC_DATA

    monkeypatch.setattr(app_module.ferry_static, "load_ferry_static", fake_load)
    app = _fake_app(ferry_static_status="loading")
    await app_module._warm_ferry_static(app)
    assert app.state.ferry_static_status == "ready"
    assert app.state.ferry_stops == FERRY_STATIC_DATA["stops"]
    # The full tables (including the trip -> route map 14b joins against) stay
    # on app.state for the later phase to consume without re-parsing.
    assert app.state.ferry_static["trips"]["f1"]["route_id"] == "ER"
    assert app.state.ferry_routes[0]["id"] == "ER"
    assert app.state.ferry_routes[0]["color"] == "00839C"


async def test_ferry_static_warmup_empty_result_is_failed_then_recovers(monkeypatch):
    # Single-system, same as PATH: an empty load drives FAILED (endpoints serve
    # no-cache []), and a later retry with data recovers to READY.
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    gate = {"ok": False}

    async def gated_load():
        return FERRY_STATIC_DATA if gate["ok"] else {}

    monkeypatch.setattr(app_module.ferry_static, "load_ferry_static", gated_load)
    app = _fake_app(ferry_static_status="loading")
    task = asyncio.create_task(app_module._warm_ferry_static(app))
    try:
        for _ in range(200):
            if app.state.ferry_static_status == "failed":
                break
            await asyncio.sleep(0.005)
        assert app.state.ferry_static_status == "failed"
        gate["ok"] = True
        for _ in range(200):
            if app.state.ferry_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        assert app.state.ferry_static_status == "ready"
        assert app.state.ferry_stops == FERRY_STATIC_DATA["stops"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_set_static_status_logs_once_per_transition(caplog):
    app = _fake_app(subway_static_status="loading")
    with caplog.at_level(logging.INFO, logger=app_module.logger.name):
        app_module._set_static_status(app, "subway_static_status", "ready")
        app_module._set_static_status(app, "subway_static_status", "ready")  # no transition
    assert app.state.subway_static_status == "ready"
    ready_logs = [r for r in caplog.records if "ready" in r.getMessage()]
    assert len(ready_logs) == 1  # logged on the transition only, not the repeat


# ---------------- static endpoints while warming ----------------


async def test_subway_stops_503_while_loading(client):
    app_module.app.state.subway_static_status = "loading"
    res = await client.get("/api/subway-stops")
    assert res.status_code == 503
    assert "loading" in res.json()["detail"].lower()


async def test_subway_stops_no_cache_empty_when_failed(client):
    # A failed load serves [] but with no-cache, so a browser does not pin the
    # empty for an hour and a later retry success is picked up.
    app_module.app.state.subway_static_status = "failed"
    app_module.app.state.subway_stations = {"A01": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}
    res = await client.get("/api/subway-stops")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


async def test_railroad_routes_503_while_loading(client):
    app_module.app.state.railroad_static_status = "loading"
    res = await client.get("/api/railroad-routes")
    assert res.status_code == 503
    assert "loading" in res.json()["detail"].lower()


async def test_railroad_routes_no_cache_empty_when_failed(client):
    app_module.app.state.railroad_static_status = "failed"
    app_module.app.state.railroad_routes = {}
    res = await client.get("/api/railroad-routes")
    assert res.status_code == 200
    assert res.json() == []
    assert res.headers.get("cache-control") == "no-cache"


# ---------------- poller warming detail + healthz while warming ----------------


async def test_subway_refresh_notes_warming_503_while_static_loading(client, cache):
    # Static not ready: the poller notes a 503 whose detail no longer claims a
    # restart is needed (the warmup retries automatically).
    app_module.app.state.subway_stops = None
    await app_module._refresh_subways(app_module.app, client=None)
    err = cache["subways"]["error"]
    assert err["status"] == 503
    assert "loading" in err["detail"].lower()
    assert "restart" not in err["detail"].lower()


async def test_healthz_not_degraded_while_static_loading(client, healthz_env):
    _fresh(healthz_env["buses"])  # a fresh feed keeps it up
    app_module.app.state.subway_static_status = "loading"  # cold-start warmup
    res = await client.get("/healthz")
    assert res.status_code == 200  # loading is not a degraded reason


async def test_healthz_degraded_on_failed_subway_static_and_recovers(client, healthz_env):
    _fresh(healthz_env["buses"])
    app_module.app.state.subway_static_status = "failed"
    res = await client.get("/healthz")
    assert res.status_code == 503
    assert any("subway static" in r for r in res.json()["reasons"])
    # A retry succeeding clears the reason.
    app_module.app.state.subway_static_status = "ready"
    res = await client.get("/healthz")
    assert res.status_code == 200


async def test_healthz_lenient_on_failed_railroad_static(client, healthz_env):
    # Railroad static failure degrades to GPS-only, NOT a healthz reason.
    _fresh(healthz_env["buses"])
    app_module.app.state.railroad_static_status = "failed"
    res = await client.get("/healthz")
    assert res.status_code == 200


async def test_healthz_ignores_failed_alerts(client, healthz_env):
    # The alerts feed is decorative: a failed alert poll must NOT fail the probe.
    _fresh(healthz_env["buses"])
    app_module.app.state.alerts_cache = app_module._fresh_alerts_entry()
    app_module._note_failure(app_module.app.state.alerts_cache, 502, "all alert feeds down")
    res = await client.get("/healthz")
    assert res.status_code == 200


# ---------------- /api/alerts ----------------

ALERT = {
    "id": "lmm:alert:1",
    "system": "MNR",
    "header": "Delays on the Harlem Line",
    "description": None,
    "effect": "SIGNIFICANT_DELAYS",
    "cause": "MAINTENANCE",
    "routes": ["1"],
    "stops": ["16"],
    "starts_at": 1000.0,
    "ends_at": None,
}


@pytest.fixture
def alerts_cache():
    app_module.app.state.alerts_cache = app_module._fresh_alerts_entry()
    return app_module.app.state.alerts_cache


async def test_alerts_served_from_seeded_index(client, alerts_cache):
    alerts_cache.update(alerts=[ALERT], fetched_at=1000.0, active=1, suppressed=2)
    res = await client.get("/api/alerts")
    assert res.status_code == 200
    body = res.json()
    assert body.pop("served_at") >= 1000.0  # R1: stamped at response build
    # Every alert system is present from the seeded health map, even before a poll.
    assert body == {
        "fetched_at": 1000.0,
        "alerts": [ALERT],
        "systems": {
            # routes is null on the alerts blocks: route coverage is the SUBWAY's
            # join key (its trains name no system), and an alert already names its own.
            s: {"fetched_at": None, "ok": True, "retained_since": None, "routes": None}
            for s in feeds.ALERT_FEED_URLS
        },
    }
    assert res.headers.get("cache-control") == "no-store"


async def test_alerts_empty_index_is_empty_list_not_error(client, alerts_cache):
    # A poll that decoded zero active alerts serves an empty list, not a 503/500.
    alerts_cache.update(alerts=[], fetched_at=1000.0, active=0, suppressed=0)
    res = await client.get("/api/alerts")
    assert res.status_code == 200
    body = res.json()
    assert body.pop("served_at") >= 1000.0
    assert body == {
        "fetched_at": 1000.0,
        "alerts": [],
        "systems": {
            # routes is null on the alerts blocks: route coverage is the SUBWAY's
            # join key (its trains name no system), and an alert already names its own.
            s: {"fetched_at": None, "ok": True, "retained_since": None, "routes": None}
            for s in feeds.ALERT_FEED_URLS
        },
    }


async def test_alerts_warming_before_first_poll_returns_503(client, alerts_cache):
    res = await client.get("/api/alerts")  # index None, no error yet
    assert res.status_code == 503
    assert "warming up" in res.json()["detail"]


async def test_alerts_failed_poll_keeps_last_known(client, alerts_cache, monkeypatch):
    alerts_cache.update(alerts=[ALERT], fetched_at=1000.0, active=1, suppressed=2)

    async def boom(client_arg):
        raise RuntimeError("All alert feeds failed: every feed timed out")

    monkeypatch.setattr(app_module, "fetch_service_alerts", boom)
    await app_module._refresh_alerts(app_module.app, client=None)
    # Last-known index and fetched_at kept; the error is recorded but not served
    # while the index is filled.
    assert alerts_cache["alerts"] == [ALERT]
    assert alerts_cache["fetched_at"] == 1000.0
    assert alerts_cache["error"]["status"] == 502
    res = await client.get("/api/alerts")
    assert res.status_code == 200
    assert res.json()["alerts"] == [ALERT]


async def test_alerts_successful_poll_replaces_index(client, alerts_cache, monkeypatch):
    async def ok(client_arg):
        return [ALERT], 3, ["bus"]  # decoded alerts, suppressed count, one failed feed

    monkeypatch.setattr(app_module, "fetch_service_alerts", ok)
    await app_module._refresh_alerts(app_module.app, client=None)
    assert alerts_cache["alerts"] == [ALERT]
    assert alerts_cache["active"] == 1
    assert alerts_cache["suppressed"] == 3
    assert alerts_cache["error"] is None  # a partial failure is still a successful poll
    assert alerts_cache["fetched_at"] is not None


async def test_status_reports_alerts(client, status_env, alerts_cache):
    alerts_cache.update(alerts=[ALERT], fetched_at=time.time() - 10, active=1, suppressed=4)
    res = await client.get("/api/status")
    assert res.status_code == 200
    alerts_status = res.json()["alerts"]
    assert alerts_status["active"] == 1
    assert alerts_status["suppressed_planned"] == 4
    assert alerts_status["last_error"] is None
    assert 9 <= alerts_status["age_s"] <= 20


# ---------------- alert retention across a partial feed outage ----------------

SUBWAY_ALERT = {**ALERT, "id": "subway:1", "system": "subway"}


async def test_alerts_partial_failure_retains_down_system(client, alerts_cache, monkeypatch):
    # Prior poll carried a subway and an MNR alert, both systems fresh.
    alerts_cache.update(alerts=[SUBWAY_ALERT, ALERT], fetched_at=1000.0, active=2, suppressed=0)
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    fresh_subway = {**SUBWAY_ALERT, "id": "subway:2"}

    async def partial(client_arg):
        return [fresh_subway], 0, ["MNR"]  # subway decoded, MNR down this poll

    monkeypatch.setattr(app_module, "fetch_service_alerts", partial)
    await app_module._refresh_alerts(app_module.app, client=None)

    # The served index keeps the down system's alert (retained) alongside the fresh
    # subway one, instead of silently dropping MNR the way the old wholesale replace did.
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:2", ALERT["id"]}
    assert alerts_cache["active"] == 2
    assert alerts_cache["error"] is None  # a partial failure is still a successful poll
    assert alerts_cache["health"]["MNR"]["retained_since"] is not None
    assert alerts_cache["health"]["MNR"]["last_error"]["status"] == 502
    assert alerts_cache["health"]["subway"]["retained_since"] is None
    assert alerts_cache["health"]["subway"]["last_error"] is None

    res = await client.get("/api/status")
    alerts_status = res.json()["alerts"]
    assert alerts_status["degraded_systems"] == ["MNR"]
    assert alerts_status["systems"]["MNR"]["retained_since"] is not None
    assert alerts_status["systems"]["subway"]["last_error"] is None


async def test_alerts_ferry_feed_failure_marks_ferry_degraded(client, alerts_cache, monkeypatch):
    # The PR 51 per-system retention machinery, exercised with the FIFTH key: a ferry
    # alert-feed failure retains ferry's alert and marks ferry degraded, while the MTA
    # systems (here subway) stay fresh. Adding "ferry" to ALERT_FEED_URLS needed no
    # retention change; this proves the generic machinery extends to it, and is the
    # safety net named in the design (a ferry decode/fetch failure surfaces in
    # degraded_systems rather than breaking the poll).
    ferry_alert = {**ALERT, "id": "ferry:1", "system": "ferry"}
    alerts_cache.update(
        alerts=[SUBWAY_ALERT, ferry_alert], fetched_at=1000.0, active=2, suppressed=0
    )
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    fresh_subway = {**SUBWAY_ALERT, "id": "subway:2"}

    async def partial(client_arg):
        return [fresh_subway], 0, ["ferry"]  # MTA decoded, ferry down this poll

    monkeypatch.setattr(app_module, "fetch_service_alerts", partial)
    await app_module._refresh_alerts(app_module.app, client=None)

    # Ferry's alert is carried forward (not silently dropped); the MTA system is fresh.
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:2", "ferry:1"}
    assert alerts_cache["error"] is None  # a partial failure is still a successful poll
    assert alerts_cache["health"]["ferry"]["retained_since"] is not None
    assert alerts_cache["health"]["ferry"]["last_error"]["status"] == 502
    assert alerts_cache["health"]["subway"]["retained_since"] is None
    assert alerts_cache["health"]["subway"]["last_error"] is None

    res = await client.get("/api/status")
    alerts_status = res.json()["alerts"]
    assert alerts_status["degraded_systems"] == ["ferry"]
    assert alerts_status["systems"]["ferry"]["retained_since"] is not None


async def test_alerts_recovery_clears_retention(client, alerts_cache, monkeypatch):
    # MNR is currently retained from a prior outage.
    alerts_cache.update(alerts=[ALERT], fetched_at=1000.0, active=1, suppressed=0)
    alerts_cache["health"]["MNR"]["retained_since"] = 900.0
    alerts_cache["health"]["MNR"]["last_error"] = {"status": 502, "detail": "was down"}

    fresh_mnr = {**ALERT, "id": "lmm:alert:2"}

    async def recovered(client_arg):
        return [fresh_mnr], 0, []  # every feed decoded this poll

    monkeypatch.setattr(app_module, "fetch_service_alerts", recovered)
    await app_module._refresh_alerts(app_module.app, client=None)
    # Fresh MNR replaces the retained one, and the retention/error state clears.
    assert {a["id"] for a in alerts_cache["alerts"]} == {"lmm:alert:2"}
    assert alerts_cache["health"]["MNR"]["retained_since"] is None
    assert alerts_cache["health"]["MNR"]["last_error"] is None
    res = await client.get("/api/status")
    assert res.json()["alerts"]["degraded_systems"] == []


async def test_alerts_all_failed_keeps_a_live_alert_and_its_last_decode_time(
    client, alerts_cache, monkeypatch
):
    # C1 REWROTE THIS TEST. It used to assert the all-failed path left per-system
    # health UNTOUCHED, which was the finding: the frozen map kept reporting the last
    # happy value while every feed was down. Health is now rewritten. What must still
    # hold is the rest of the cache contract: a live (open-ended) alert survives, and
    # fresh_at keeps the LAST DECODE time rather than advancing, so an operator can
    # see how long the system has actually been dark.
    alerts_cache.update(alerts=[ALERT], fetched_at=1000.0, active=1, suppressed=7)
    alerts_cache["health"]["subway"]["fresh_at"] = 500.0

    async def boom(client_arg):
        raise RuntimeError("All alert feeds failed: every feed timed out")

    monkeypatch.setattr(app_module, "fetch_service_alerts", boom)
    await app_module._refresh_alerts(app_module.app, client=None)
    assert alerts_cache["alerts"] == [ALERT]  # open-ended: nothing to expire
    assert alerts_cache["error"]["status"] == 502
    assert alerts_cache["health"]["subway"]["fresh_at"] == 500.0  # NOT advanced
    assert alerts_cache["fetched_at"] == 1000.0  # last poll that decoded
    assert alerts_cache["suppressed"] == 7  # describes that same last decode


# ---------------- C1: a total outage no longer freezes the index ----------------
# The finding, verified end to end: the all-failed path used to return before any
# merge, so the index was BYTE-FROZEN. Alerts that expired mid-outage stayed visible,
# the retention cap never fired, and per-system health kept its last happy value while
# /api/alerts served all of it as a 200 with a fresh served_at. These pin each half.

# MNR ends at 2000; subway is open-ended. Both are live at now=1500, and only the MNR
# one has expired by now=2500.
MNR_ENDING_ALERT = {**ALERT, "id": "mnr:ending", "system": "MNR", "ends_at": 2000.0}
SUBWAY_OPEN_ALERT = {**ALERT, "id": "subway:open", "system": "subway", "ends_at": None}


async def _total_outage_poll(monkeypatch, now):
    async def boom(client_arg):
        raise RuntimeError("All alert feeds failed: every feed timed out")

    monkeypatch.setattr(app_module, "fetch_service_alerts", boom)
    monkeypatch.setattr(app_module.time, "time", lambda: now)
    await app_module._refresh_alerts(app_module.app, client=None)


async def test_alerts_expiring_during_a_total_outage_drops_from_the_index(
    client, alerts_cache, monkeypatch
):
    alerts_cache.update(
        alerts=[MNR_ENDING_ALERT, SUBWAY_OPEN_ALERT], fetched_at=1000.0, active=2, suppressed=0
    )
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    # Mid-outage but before the MNR alert's ends_at: both still served.
    await _total_outage_poll(monkeypatch, 1500.0)
    assert {a["id"] for a in alerts_cache["alerts"]} == {"mnr:ending", "subway:open"}

    # The outage PERSISTS across the alert's own end time. The expired one drops even
    # though no feed has decoded since; the open-ended one stays.
    await _total_outage_poll(monkeypatch, 2500.0)
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:open"}
    assert alerts_cache["active"] == 1
    # Still a failed poll throughout: the index shrank, it was not refreshed.
    assert alerts_cache["error"]["status"] == 502
    assert alerts_cache["fetched_at"] == 1000.0
    # And the rider-facing endpoint serves the SHRUNKEN index, not the frozen one.
    res = await client.get("/api/alerts")
    assert res.status_code == 200
    assert [a["id"] for a in res.json()["alerts"]] == ["subway:open"]


async def test_alerts_retention_cap_fires_during_a_total_outage(alerts_cache, monkeypatch):
    # The cap is the ONLY thing that can ever clear an open-ended alert, and it could
    # not fire at all while the all-failed path returned early: a systemwide alert
    # would have been served forever behind a permanently dead feed.
    cap = float(app_module.ALERT_RETENTION_MAX_S)
    alerts_cache.update(alerts=[SUBWAY_OPEN_ALERT], fetched_at=1000.0, active=1, suppressed=0)
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    await _total_outage_poll(monkeypatch, 1000.0)  # outage begins: retention starts
    assert alerts_cache["health"]["subway"]["retained_since"] == 1000.0
    await _total_outage_poll(monkeypatch, 1000.0 + cap - 1)  # just under: still served
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:open"}
    await _total_outage_poll(monkeypatch, 1000.0 + cap)  # at the cap: dropped
    assert alerts_cache["alerts"] == []
    assert alerts_cache["health"]["subway"]["retained_since"] is None
    assert alerts_cache["health"]["subway"]["last_error"]["status"] == 502  # still degraded


async def test_alerts_total_outage_reports_every_system_degraded(
    client, status_env, alerts_cache, monkeypatch
):
    # THE BLIND SPOT R4 HAD TO WORK AROUND, closed at the source. Cross-reference:
    # test_contract_monitor.test_production_total_alerts_outage_is_fail_not_pass
    # exists because this map used to read fully healthy during a total outage, which
    # forced the monitor to consult the POLL age first to notice anything was wrong.
    # The monitor keeps that check (defence in depth, and it catches a frozen poller
    # too), but degraded_systems is now truthful here on its own.
    alerts_cache.update(alerts=[SUBWAY_OPEN_ALERT], fetched_at=1000.0, active=1, suppressed=0)
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    await _total_outage_poll(monkeypatch, 1200.0)
    systems = set(alerts_cache["health"])
    assert {s for s, h in alerts_cache["health"].items() if h["retained_since"] == 1200.0} == {
        "subway"  # only the system with alerts to carry records a retention clock
    }
    res = await client.get("/api/status")
    alerts_status = res.json()["alerts"]
    assert set(alerts_status["degraded_systems"]) == systems  # every feed, not none
    assert alerts_status["last_error"]["status"] == 502


async def test_alerts_deadline_timeout_takes_the_same_outage_path_as_an_all_failed_poll(
    client, status_env, alerts_cache, monkeypatch
):
    # REVIEW FIX. A trickling feed that outruns REFRESH_DEADLINE_S is the shape that
    # deadline exists to catch, and it used to be handled OUTSIDE _refresh_alerts by
    # _bounded_refresh, which can only record the error. So the most likely total
    # outage in production skipped every bit of machinery above: the index stayed
    # byte-frozen, expired alerts kept being served, the retention cap never fired,
    # and degraded_systems still read empty. A timeout must be indistinguishable from
    # an all-feeds-failed poll to everything downstream.
    import pollers

    monkeypatch.setattr(pollers, "REFRESH_DEADLINE_S", 0.05)
    alerts_cache.update(
        alerts=[MNR_ENDING_ALERT, SUBWAY_OPEN_ALERT], fetched_at=1000.0, active=2, suppressed=0
    )
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    async def trickle(client_arg):
        await asyncio.sleep(10)  # never lands inside the deadline

    monkeypatch.setattr(app_module, "fetch_service_alerts", trickle)
    # Past the MNR alert's ends_at (2000), so the re-filter has something to drop.
    monkeypatch.setattr(app_module.time, "time", lambda: 2500.0)
    await app_module._refresh_alerts(app_module.app, client=None)

    assert alerts_cache["error"]["status"] == 504  # still reported as a deadline
    assert "deadline" in alerts_cache["error"]["detail"]
    assert alerts_cache["fetched_at"] == 1000.0  # last poll that decoded
    # ...and now the outage machinery actually ran:
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:open"}  # expired dropped
    assert alerts_cache["active"] == 1
    res = await client.get("/api/status")
    assert set(res.json()["alerts"]["degraded_systems"]) == set(alerts_cache["health"])


async def test_one_slow_alert_feed_does_not_fail_the_other_four(monkeypatch):
    # REVIEW FIX, and this was a REGRESSION the first review round introduced. Bounding
    # the whole fetch meant one trickling feed cancelled the four responses that had
    # already landed, so the caller saw only a timeout and marked every system failed:
    # four healthy feeds' alerts went into retention and were deleted half an hour
    # later, and /api/status blamed five phantom upstream outages instead of the one
    # slow feed. The deadline is now PER FEED, so a slow feed is an ordinary per-feed
    # failure and the rest of the poll succeeds. Exercises the real
    # fetch_service_alerts, not a stub, since the defect lived in its gather.
    from google.transit import gtfs_realtime_pb2 as pb

    monkeypatch.setattr(feeds.alerts, "ALERT_FEED_DEADLINE_S", 0.05)

    empty_feed = pb.FeedMessage()
    empty_feed.header.gtfs_realtime_version = "2.0"
    body = empty_feed.SerializeToString()

    class _Resp:
        content = body

        def raise_for_status(self):
            return None

    class SlowSubwayClient:
        async def get(self, url, **kwargs):
            if "subway" in url:
                await asyncio.sleep(10)  # never lands inside the per-feed deadline
            return _Resp()

    alerts, suppressed, failed = await feeds.fetch_service_alerts(SlowSubwayClient())
    # The four healthy feeds decoded; ONLY the slow one is reported failed.
    assert failed == ["subway"]
    assert suppressed == 0
    assert alerts == []


async def test_a_timed_out_alert_feed_records_a_readable_reason(monkeypatch):
    # str(TimeoutError()) is the EMPTY STRING, so without an explicit description a
    # timed-out feed would be logged and recorded with no cause at all (the same trap
    # R3 hit on the warmup path).
    detail = feeds.alerts._describe_feed_error(TimeoutError())
    assert detail and "no response within" in detail
    # And any other exception with an empty str() still names its type.
    assert feeds.alerts._describe_feed_error(RuntimeError()) == "RuntimeError"
    assert feeds.alerts._describe_feed_error(RuntimeError("boom")) == "boom"


async def test_alerts_never_filled_outage_still_reports_every_system_degraded(
    client, status_env, alerts_cache, monkeypatch
):
    # REVIEW FIX. The never-filled guard skipped the health write along with the index
    # write, so a process that STARTED while every feed was down reported five
    # perfectly healthy systems: degraded_systems [] and every last_error null, for as
    # long as the outage lasted. That is the same "looks healthy while nothing works"
    # this change exists to remove, just at boot instead of mid-life.
    assert alerts_cache["alerts"] is None

    await _total_outage_poll(monkeypatch, 1000.0)

    assert alerts_cache["alerts"] is None  # index still unfilled, as before
    res = await client.get("/api/status")
    alerts_status = res.json()["alerts"]
    assert set(alerts_status["degraded_systems"]) == set(alerts_cache["health"])
    # fresh_at stays null: nothing has ever decoded, and claiming otherwise would be
    # the opposite lie.
    assert all(h["fresh_at"] is None for h in alerts_cache["health"].values())


async def test_alerts_total_outage_before_the_first_success_stays_warming(
    client, alerts_cache, monkeypatch
):
    # The index has NEVER filled, so there is nothing to re-filter. Writing the
    # merge's empty result would flip /api/alerts from its warming 503 to a 200
    # serving "no active alerts", which is the same false-green this change removes.
    assert alerts_cache["alerts"] is None

    await _total_outage_poll(monkeypatch, 1000.0)
    assert alerts_cache["alerts"] is None  # still unfilled, NOT []
    assert alerts_cache["error"]["status"] == 502
    # The endpoint keeps its existing never-filled behavior: the recorded error
    # surfaces (502), and specifically NOT a 200 carrying an empty alerts list, which
    # is what writing the merge result here would have produced.
    res = await client.get("/api/alerts")
    assert res.status_code == 502
    assert res.status_code != 200


async def test_alerts_retention_clock_survives_consecutive_total_outages(alerts_cache, monkeypatch):
    # Aimed at the review concern: retained_since must be threaded, not restamped,
    # or every failing poll would restart the clock and the cap could never fire.
    alerts_cache.update(alerts=[SUBWAY_OPEN_ALERT], fetched_at=1000.0, active=1, suppressed=0)
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    for now in (1000.0, 1060.0, 1120.0, 1180.0):
        await _total_outage_poll(monkeypatch, now)
        assert alerts_cache["health"]["subway"]["retained_since"] == 1000.0  # never bumped


async def test_alerts_partial_failure_after_a_total_one_does_not_double_charge_retention(
    alerts_cache, monkeypatch
):
    # The other half of the review concern. The total-outage path now WRITES a merged
    # index, so a later partial failure re-runs the merge over already-shrunk content.
    # Both the expiry re-filter (absolute ends_at) and the cap (measured from the
    # retention START) are idempotent, so the second pass must reach the same answer
    # rather than compounding: subway recovers and is replaced by fresh data, while
    # MNR's clock still reads the original outage start.
    # The MNR alert is OPEN-ENDED on purpose. REVIEW FIX: this test used to carry the
    # ends_at=2000 alert, and its closing assertion at cap+1 (t=2801) was therefore
    # VACUOUS: the expiry re-filter drops that alert on its own ends_at at any t>2000,
    # so the assertion held no matter where the retention clock started. Only an alert
    # that can never self-expire makes the cap the sole possible explanation.
    cap = float(app_module.ALERT_RETENTION_MAX_S)
    mnr_open = {**ALERT, "id": "mnr:open", "system": "MNR", "ends_at": None}
    alerts_cache.update(
        alerts=[mnr_open, SUBWAY_OPEN_ALERT], fetched_at=1000.0, active=2, suppressed=0
    )
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 1000.0

    await _total_outage_poll(monkeypatch, 1000.0)
    assert alerts_cache["health"]["MNR"]["retained_since"] == 1000.0

    fresh_subway = {**SUBWAY_OPEN_ALERT, "id": "subway:fresh"}

    async def only_mnr_down(client_arg):
        return [fresh_subway], 0, ["MNR"]

    monkeypatch.setattr(app_module, "fetch_service_alerts", only_mnr_down)
    monkeypatch.setattr(app_module.time, "time", lambda: 1500.0)
    await app_module._refresh_alerts(app_module.app, client=None)

    # MNR still carried, and its retention clock still points at the ORIGINAL
    # total-outage start rather than being restamped to 1500.
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:fresh", "mnr:open"}
    assert alerts_cache["health"]["MNR"]["retained_since"] == 1000.0
    assert alerts_cache["health"]["subway"]["retained_since"] is None  # recovered
    assert alerts_cache["error"] is None  # a partial failure is a successful poll

    # The cap still measures from 1000.0, not from 1500.0. Both edges are asserted, so
    # a clock that HAD restarted at 1500 would fail the first of them: at 1000+cap-1
    # the alert is still carried, and at 1000+cap exactly it drops. A restarted clock
    # would still be 499s short of its cap at that second and keep the alert.
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0 + cap - 1)
    await app_module._refresh_alerts(app_module.app, client=None)
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:fresh", "mnr:open"}
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0 + cap)
    await app_module._refresh_alerts(app_module.app, client=None)
    assert {a["id"] for a in alerts_cache["alerts"]} == {"subway:fresh"}
    assert alerts_cache["health"]["MNR"]["retained_since"] is None


async def test_status_reports_alert_system_health(client, status_env, alerts_cache):
    # All systems fresh: the health map is exposed and nothing is degraded.
    now = time.time()
    alerts_cache.update(alerts=[ALERT], fetched_at=now - 5, active=1, suppressed=0)
    for health in alerts_cache["health"].values():
        health["fresh_at"] = now - 5
    res = await client.get("/api/status")
    alerts_status = res.json()["alerts"]
    assert set(alerts_status["systems"]) == {"subway", "bus", "LIRR", "MNR", "ferry"}
    assert alerts_status["degraded_systems"] == []
    assert alerts_status["systems"]["subway"]["retained_since"] is None
    assert alerts_status["systems"]["subway"]["last_error"] is None


# The pure merge's cap is unit-tested in test_feeds_alerts; these prove the
# main-level glue actually THREADS the prior retention clock (health's
# retained_since) into it, so the cap measures from the ORIGINAL down time rather
# than resetting every poll. Regression target: a `prev_retained_since = {}` or an
# is-not-None-to-truthiness slip would keep an open-ended (ends_at None) alert
# forever and still ship green without these. The clock is pinned so the boundary
# is exact; MNR fails while the other three decode (a partial, still-successful poll).
MNR_OPEN_ALERT = {**ALERT, "id": "mnr:open", "ends_at": None}


def _seed_retained_mnr(alerts_cache, retained_since):
    alerts_cache.update(alerts=[MNR_OPEN_ALERT], fetched_at=0.0, active=1, suppressed=0)
    for health in alerts_cache["health"].values():
        health["fresh_at"] = 0.0
    alerts_cache["health"]["MNR"]["retained_since"] = retained_since
    alerts_cache["health"]["MNR"]["last_error"] = {"status": 502, "detail": "down"}


async def _poll_mnr_still_down(monkeypatch, now):
    async def mnr_down(client_arg):
        return [], 0, ["MNR"]  # the other three decoded zero, MNR still failing

    monkeypatch.setattr(app_module, "fetch_service_alerts", mnr_down)
    monkeypatch.setattr(app_module.time, "time", lambda: now)
    await app_module._refresh_alerts(app_module.app, client=None)


async def test_alerts_retention_cap_drops_open_ended_alert_from_original_start(
    alerts_cache, monkeypatch
):
    now = 100_000.0
    cap = app_module.ALERT_RETENTION_MAX_S
    # Down since one second PAST the cap: threading the original start means this
    # poll caps and drops. Resetting the clock to `now` each poll would keep it.
    _seed_retained_mnr(alerts_cache, retained_since=now - cap - 1)
    await _poll_mnr_still_down(monkeypatch, now)
    assert alerts_cache["alerts"] == []  # open-ended alert dropped by the cap
    assert alerts_cache["active"] == 0
    assert alerts_cache["health"]["MNR"]["retained_since"] is None
    assert alerts_cache["health"]["MNR"]["last_error"]["status"] == 502  # still degraded
    assert alerts_cache["error"] is None  # a partial failure is still a successful poll


async def test_alerts_retention_just_under_cap_keeps_alert(alerts_cache, monkeypatch):
    now = 100_000.0
    cap = app_module.ALERT_RETENTION_MAX_S
    # Down since just under the cap: still retained, and the original start is
    # carried forward unchanged (not bumped to now).
    started = now - cap + 120
    _seed_retained_mnr(alerts_cache, retained_since=started)
    await _poll_mnr_still_down(monkeypatch, now)
    assert {a["id"] for a in alerts_cache["alerts"]} == {"mnr:open"}
    assert alerts_cache["health"]["MNR"]["retained_since"] == started


async def test_alerts_retention_epoch_zero_start_is_not_reset(alerts_cache, monkeypatch):
    # A 0.0 (epoch) start must be threaded through as-is: at now == cap it drops.
    # A truthiness slip (`started or now`) would treat 0.0 as now and never cap.
    cap = float(app_module.ALERT_RETENTION_MAX_S)
    _seed_retained_mnr(alerts_cache, retained_since=0.0)
    await _poll_mnr_still_down(monkeypatch, cap)
    assert alerts_cache["alerts"] == []
    assert alerts_cache["health"]["MNR"]["retained_since"] is None


# ---------------- security headers (H3) ----------------

_EXPECTED_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https://tile.openstreetmap.org; "
    "connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'"
)


async def test_security_headers_on_frontend_document(client):
    # The HTML/static surface carries the full security-header suite. Pins the exact
    # CSP string (the e2e serve.js mirrors it; the Playwright suite is the proof it
    # does not break the app). style-src 'unsafe-inline' is required for the popup
    # inline styles; script-src stays strict via default-src 'self'.
    res = await client.get("/")
    assert res.status_code == 200
    assert res.headers["content-security-policy"] == _EXPECTED_CSP
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert res.headers["permissions-policy"] == "geolocation=(), camera=(), microphone=()"
    # No HSTS: Railway terminates TLS, so the app does not assert transport policy.
    assert "strict-transport-security" not in res.headers


async def test_security_headers_scoped_off_non_frontend(client):
    # A document CSP is meaningless on JSON/docs responses and would break the
    # CDN-backed Swagger UI, so the middleware skips /api, /healthz, and the docs
    # paths. openapi.json is a stable, state-independent non-frontend path.
    res = await client.get("/openapi.json")
    assert res.status_code == 200
    assert "content-security-policy" not in res.headers
    assert "permissions-policy" not in res.headers


# ---------------- C2: per-system freshness and retention ----------------
# The central contract defect: an aggregate endpoint treated partial failure as
# success, published survivors only, and advanced ONE fetched_at, so a failed
# subway group's trains silently vanished and retained railroad arrivals wore the
# aggregate's fresh clock. These pin each half of the fix.

C2_GROUPS = ("ACE", "NQRW")


# PR1 shipped the retention flag OFF, so these tests used to switch it on through an
# autouse fixture. PR2 ships it ON (paired with the client-side stale rendering), so
# they now run against the SHIPPED default and the fixture is gone: the one test that
# needs the other setting turns it off itself, by name.


@pytest.fixture(autouse=True)
def _c2_subway_stops(request):
    """The subway refresher early-returns a 503 without static stops, so every C2
    subway test needs them seeded before it can reach the fetch at all."""
    if request.node.name.startswith("test_c2_"):
        app_module.app.state.subway_stops = {
            "127N": {"name": "Times Sq", "lat": 40.75, "lon": -73.98}
        }
        app_module.app.state.subway_stations = {
            "127": {"name": "Times Sq", "lat": 40.75, "lon": -73.98}
        }
    yield


def _subway_fetch(trains_by_group, failed, feed_ts=996.0):
    """A fake fetch_subway_trains honest about which groups decoded: only decoding
    groups appear in the by-group maps, which is what the real decoder does."""

    async def fetch(stops_arg, client_arg):
        arrivals_by_group = {
            group: {
                "127": {
                    "Northbound": [
                        {
                            "route_id": g_trains[0]["route_id"],
                            "trip_id": g_trains[0]["trip_id"],
                            "arrival": 1200.0,
                        }
                    ]
                }
            }
            for group, g_trains in trains_by_group.items()
            if g_trains
        }
        flat = [t for g in trains_by_group.values() for t in g]
        return flat, {}, feed_ts, failed, trains_by_group, arrivals_by_group

    return fetch


def _train(group, trip):
    return {**TRAINS[0], "trip_id": trip, "route_id": group[0]}


async def test_c2_partial_subway_outage_retains_the_failed_group_with_a_frozen_stamp(
    client, cache, monkeypatch
):
    # A healthy poll for both groups, then ACE fails. ACE's trains must SURVIVE in
    # the aggregate (pre-C2 they vanished with no explanation), stamped with ACE's
    # own frozen fetched_at while NQRW's advances.
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-1")], "NQRW": [_train("NQRW", "nqrw-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    assert {t["trip_id"] for t in cache["subways"]["data"]} == {"ace-1", "nqrw-1"}
    assert cache["subways"]["systems"]["ACE"] == {
        "fetched_at": 1000.0,
        "ok": True,
        "retained_since": None,
        "routes": ["A"],  # the coverage the client inverts into route -> group
    }

    # ACE goes down; NQRW keeps decoding with a NEW train.
    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"NQRW": [_train("NQRW", "nqrw-2")]}, ["ACE"]),
    )
    await app_module._refresh_subways(app_module.app, client=None)

    # ACE's train is still served, NQRW's has been replaced.
    assert {t["trip_id"] for t in cache["subways"]["data"]} == {"ace-1", "nqrw-2"}
    systems = cache["subways"]["systems"]
    # routes is the per-system coverage the client inverts into route -> group, so it
    # can point this block at the markers it describes. ACE keeps listing its route
    # while its train is retained; NQRW lists its own.
    assert systems["ACE"] == {
        "fetched_at": 1000.0,
        "ok": False,
        "retained_since": 1060.0,
        "routes": ["A"],
    }
    assert systems["NQRW"] == {
        "fetched_at": 1060.0,
        "ok": True,
        "retained_since": None,
        "routes": ["N"],
    }
    # The AGGREGATE advanced (the poll ran), which is exactly why the per-system
    # block has to exist: on its own the envelope timestamp says nothing is wrong.
    assert cache["subways"]["fetched_at"] == 1060.0
    res = await client.get("/api/subways")
    assert res.json()["systems"]["ACE"]["ok"] is False


async def test_c2_retention_cap_ages_the_data_out_while_freshness_keeps_reporting(
    client, cache, monkeypatch
):
    # Past FEED_RETENTION_MAX_S the retained data goes, because a ten-minute-old
    # position rendered as current is a ghost. The system's block must STILL report
    # the outage: absence plus an explanation, never silent absence.
    import cache as cache_module

    cap = float(cache_module.FEED_RETENTION_MAX_S)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-1")], "NQRW": [_train("NQRW", "nqrw-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)

    # ACE first FAILS at 1060, so that is when its retention clock starts: the cap
    # measures from the outage, not from the last healthy poll.
    ace_down = _subway_fetch({"NQRW": [_train("NQRW", "nqrw-2")]}, ["ACE"])
    monkeypatch.setattr(app_module, "fetch_subway_trains", ace_down)
    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0)
    await app_module._refresh_subways(app_module.app, client=None)
    assert cache["subways"]["systems"]["ACE"]["retained_since"] == 1060.0

    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0 + cap - 1)
    await app_module._refresh_subways(app_module.app, client=None)
    assert "ace-1" in {t["trip_id"] for t in cache["subways"]["data"]}  # just inside

    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0 + cap)
    await app_module._refresh_subways(app_module.app, client=None)
    data = {t["trip_id"] for t in cache["subways"]["data"]}
    assert "ace-1" not in data  # aged out
    assert data == {"nqrw-2"}
    systems = cache["subways"]["systems"]
    assert systems["ACE"]["ok"] is False  # still reporting
    assert systems["ACE"]["retained_since"] is None  # nothing left to retain
    assert systems["ACE"]["fetched_at"] == 1000.0  # and still dated honestly


async def test_c2_a_healthy_but_EMPTY_group_replaces_rather_than_retains(
    client, cache, monkeypatch
):
    # THE INVARIANT THE HANDOFF ASKED FOR. The decoders classify a feed as failed
    # only at the exception boundary, so an HTTP 200 with zero entities is a
    # SUCCESS: a group with nothing running (ordinary overnight) reports an empty
    # list and must REPLACE its previous trains. Retaining on emptiness would
    # resurrect last night's trains every night; the ferry precedent is the same
    # rule with the opposite default, which is why this needs pinning.
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    assert {t["trip_id"] for t in cache["subways"]["data"]} == {"ace-1"}

    # ACE decodes cleanly and reports NOTHING RUNNING: no failure, empty list.
    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0)
    monkeypatch.setattr(app_module, "fetch_subway_trains", _subway_fetch({"ACE": []}, []))
    await app_module._refresh_subways(app_module.app, client=None)
    assert cache["subways"]["data"] == []  # replaced, NOT retained
    assert cache["subways"]["systems"]["ACE"] == {
        "fetched_at": 1060.0,  # it decoded, so it is fresh
        "ok": True,
        "retained_since": None,
        # It covers no routes because it is genuinely running nothing, which is how a
        # rider sees absence rather than retained-stale markers.
        "routes": [],
    }


async def test_c2_recovery_replaces_retained_data_and_clears_retained_since(
    client, cache, monkeypatch
):
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0)
    monkeypatch.setattr(app_module, "fetch_subway_trains", _subway_fetch({}, ["ACE"]))
    await app_module._refresh_subways(app_module.app, client=None)
    assert cache["subways"]["systems"]["ACE"]["retained_since"] == 1060.0

    # ACE comes back with fresh data.
    monkeypatch.setattr(app_module.time, "time", lambda: 1120.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-9")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    assert {t["trip_id"] for t in cache["subways"]["data"]} == {"ace-9"}
    assert cache["subways"]["systems"]["ACE"] == {
        "fetched_at": 1120.0,
        "ok": True,
        "retained_since": None,
        "routes": ["A"],
    }


async def test_c2_total_subway_failure_keeps_data_and_reports_every_system_down(
    client, cache, monkeypatch
):
    # C2 changed the DATA handling of the partial path only: a total failure still
    # keeps last-known everything and records the poll error, exactly as before. The
    # one thing it does change is the per-system block, which used to keep saying
    # ok: True for every system while not one of them had decoded.
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    # DEEP copy. The blocks below are mutated in place on the failure path, so a
    # shallow dict() would alias them and every assertion would pass vacuously.
    before = copy.deepcopy(cache["subways"])

    async def boom(stops_arg, client_arg):
        raise RuntimeError("All subway feeds failed: everything is down")

    monkeypatch.setattr(app_module, "fetch_subway_trains", boom)
    monkeypatch.setattr(app_module.time, "time", lambda: 2000.0)
    await app_module._refresh_subways(app_module.app, client=None)
    assert cache["subways"]["data"] == before["data"]  # last-known kept
    assert cache["subways"]["fetched_at"] == before["fetched_at"]  # NOT advanced
    assert cache["subways"]["error"]["status"] == 502
    systems = cache["subways"]["systems"]
    assert all(block["ok"] is False for block in systems.values())
    # Each system's own fetched_at is untouched: a total outage does not change
    # when that system's data was last fetched, and the retention clock, which no
    # merge advanced on this path, must not restart either.
    for system, block in systems.items():
        assert block["fetched_at"] == before["systems"][system]["fetched_at"]
        assert block["retained_since"] == before["systems"][system]["retained_since"]


async def test_c2_total_railroad_failure_also_reports_every_system_down(client, cache, monkeypatch):
    # The same correction has to be wired into the OTHER refresher: the helper is
    # shared, the four early returns that call it are not, and missing one leaves
    # that feed claiming every system is fine during a total outage.
    app_module.app.state.railroad_stops = {"MNR": {"1": {"name": "G", "lat": 40.7, "lon": -73.9}}}
    mnr = {**RAILROADS[0], "system": "MNR", "trip_id": "m1"}

    async def healthy(client_arg, stops_arg):
        return [mnr], {"MNR": {"1": {"Trains": []}}}, 990.0, []

    monkeypatch.setattr(app_module, "fetch_railroad_trains", healthy)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    await app_module._refresh_railroads(app_module.app, client=None)
    assert cache["railroads"]["systems"]["MNR"]["ok"] is True

    async def boom(client_arg, stops_arg):
        raise RuntimeError("All railroad feeds failed: every system timed out")

    monkeypatch.setattr(app_module, "fetch_railroad_trains", boom)
    monkeypatch.setattr(app_module.time, "time", lambda: 2000.0)
    await app_module._refresh_railroads(app_module.app, client=None)
    systems = cache["railroads"]["systems"]
    assert all(block["ok"] is False for block in systems.values())
    assert systems["MNR"]["fetched_at"] == 1000.0  # still honestly dated


async def test_c2_railroad_arrivals_carry_their_own_systems_frozen_timestamp(
    client, cache, monkeypatch
):
    # THE AUDIT'S SHARPEST SUB-CLAIM: a retained MNR arrival used to be stamped
    # with the aggregate fetched_at, which a healthy LIRR keeps advancing, so the
    # popup presented frozen countdowns as current.
    app_module.app.state.railroad_stops = {
        "MNR": {"1": {"name": "Grand Central", "lat": 40.75, "lon": -73.97}},
        "LIRR": {"2": {"name": "Jamaica", "lat": 40.70, "lon": -73.80}},
    }
    mnr = {**RAILROADS[0], "system": "MNR", "trip_id": "mnr-1"}
    lirr = {**RAILROADS[0], "system": "LIRR", "trip_id": "lirr-1"}

    async def both(client_arg, stops_arg):
        return [mnr, lirr], {"MNR": {"1": {"Trains": []}}, "LIRR": {"2": {"Trains": []}}}, 990.0, []

    monkeypatch.setattr(app_module, "fetch_railroad_trains", both)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    await app_module._refresh_railroads(app_module.app, client=None)

    async def lirr_only(client_arg, stops_arg):
        return [lirr], {"LIRR": {"2": {"Trains": []}}}, 990.0, ["MNR"]

    monkeypatch.setattr(app_module, "fetch_railroad_trains", lirr_only)
    monkeypatch.setattr(app_module.time, "time", lambda: 1300.0)
    await app_module._refresh_railroads(app_module.app, client=None)

    # The aggregate advanced to 1300; MNR's own clock is still 1000.
    assert cache["railroads"]["fetched_at"] == 1300.0
    mnr_res = await client.get("/api/railroad-arrivals/MNR/1")
    assert mnr_res.json()["fetched_at"] == 1000.0  # frozen, and honest about it
    lirr_res = await client.get("/api/railroad-arrivals/LIRR/2")
    assert lirr_res.json()["fetched_at"] == 1300.0  # fresh
    # And MNR's train is retained rather than vanishing.
    assert {t["trip_id"] for t in cache["railroads"]["data"]} == {"mnr-1", "lirr-1"}


async def test_c2_alerts_envelope_mirrors_the_health_map(client, alerts_cache):
    # The client never fetches /api/status, so the per-system block has to ride on
    # /api/alerts. It is a projection of the health map C1 made truthful.
    alerts_cache.update(alerts=[ALERT], fetched_at=1000.0, active=1, suppressed=0)
    for system, health in alerts_cache["health"].items():
        health["fresh_at"] = 1000.0
    alerts_cache["health"]["MNR"].update(
        fresh_at=900.0, retained_since=950.0, last_error={"status": 502, "detail": "down"}
    )
    body = (await client.get("/api/alerts")).json()
    assert body["systems"]["subway"] == {
        "fetched_at": 1000.0,
        "ok": True,
        "retained_since": None,
        "routes": None,  # alerts name their own system; no route join needed
    }
    assert body["systems"]["MNR"] == {
        "fetched_at": 900.0,  # fresh_at maps to fetched_at
        "ok": False,  # derived from last_error
        "retained_since": 950.0,
        "routes": None,
    }
    # No error TEXT leaks into the public envelope; detail stays on /api/status.
    assert "detail" not in str(body["systems"]["MNR"])


async def test_c2_gate_off_publishes_survivors_only(client, cache, monkeypatch):
    # PR2 SHIPS THE RETENTION ON, paired with the client-side stale rendering in the
    # same commit (see the flag's comment in cache.py). This test keeps the OFF path
    # honest: the gate still exists, and with it off the data behaves exactly as it
    # did before C2, a failed group's trains simply absent. Without this the gate
    # would rot into a constant nothing reads.
    import pollers

    assert pollers.FEED_RETENTION_ENABLED is True  # the shipped default as of PR2
    monkeypatch.setattr(pollers, "FEED_RETENTION_ENABLED", False)
    app_module.app.state.subway_stops = {"127N": {"name": "T", "lat": 40.7, "lon": -73.9}}
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": [_train("ACE", "ace-1")], "NQRW": [_train("NQRW", "nqrw-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)

    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"NQRW": [_train("NQRW", "nqrw-2")]}, ["ACE"]),
    )
    await app_module._refresh_subways(app_module.app, client=None)

    # Survivors only, exactly as before C2.
    assert {t["trip_id"] for t in cache["subways"]["data"]} == {"nqrw-2"}
    # But the freshness block still tells the operator the truth.
    assert cache["subways"]["systems"]["ACE"]["ok"] is False
    assert cache["subways"]["systems"]["ACE"]["fetched_at"] == 1000.0


async def test_c2_retained_arrivals_never_evict_fresh_ones(client, cache, monkeypatch):
    # REVIEW FIX (high). A retained group's arrivals keep their ORIGINAL absolute
    # times, and _trim_arrivals sorts soonest-first before capping, so an expired
    # row is the smallest number in its bucket and filled the cap AHEAD of every
    # live one. Reproduced by the reviewer: one failed group served six departures
    # that had already happened and zero of the ones that had not.
    import pollers

    monkeypatch.setattr(pollers, "FEED_RETENTION_ENABLED", True)
    app_module.app.state.subway_stops = {"127N": {"name": "T", "lat": 40.7, "lon": -73.9}}
    now1, now2 = 1000.0, 1400.0

    async def poll1(stops_arg, client_arg):
        by_group = {
            "ACE": {
                "127": {"Northbound": [{"route_id": "A", "trip_id": "ace-0", "arrival": 1060.0}]}
            },
            "NQRW": {
                "127": {"Northbound": [{"route_id": "N", "trip_id": "nq-0", "arrival": 1080.0}]}
            },
        }
        return [], {}, 990.0, [], {"ACE": [], "NQRW": []}, by_group

    monkeypatch.setattr(app_module, "fetch_subway_trains", poll1)
    monkeypatch.setattr(app_module.time, "time", lambda: now1)
    await app_module._refresh_subways(app_module.app, client=None)

    async def poll2(stops_arg, client_arg):
        # ACE is down; NQRW serves FRESH arrivals, all still in the future.
        by_group = {
            "NQRW": {
                "127": {"Northbound": [{"route_id": "N", "trip_id": "nq-1", "arrival": 1460.0}]}
            }
        }
        return [], {}, 1390.0, ["ACE"], {"NQRW": []}, by_group

    monkeypatch.setattr(app_module, "fetch_subway_trains", poll2)
    monkeypatch.setattr(app_module.time, "time", lambda: now2)
    await app_module._refresh_subways(app_module.app, client=None)

    served = app_module.app.state.subway_arrivals["127"]["Northbound"]
    ids = [a["trip_id"] for a in served]
    assert "nq-1" in ids  # the LIVE arrival survived
    assert "ace-0" not in ids  # the retained one had already departed, so it went
    assert all(a["arrival"] >= now2 for a in served)  # nothing in the past is served


async def test_c2_retention_clock_is_not_reset_by_the_systems_write(client, cache, monkeypatch):
    # REVIEW FIX (high). _refresh_railroads wrote entry["systems"] BETWEEN the two
    # merges, so the arrivals merge read the just-reset retained_since=None,
    # restarted its clock at `now` every poll, and the cap never fired: reproduced
    # still serving t=1000 arrivals after 3.3 hours, 20x the cap.
    import pollers

    monkeypatch.setattr(pollers, "FEED_RETENTION_ENABLED", True)
    app_module.app.state.railroad_stops = {"MNR": {"1": {"name": "GCT", "lat": 40.7, "lon": -73.9}}}
    cap = float(pollers.FEED_RETENTION_MAX_S)
    mnr = {**RAILROADS[0], "system": "MNR", "trip_id": "m1"}

    async def healthy(client_arg, stops_arg):
        return [mnr], {"MNR": {"1": {"Trains": [{"trip_id": "m1", "arrival": 1e12}]}}}, 990.0, []

    monkeypatch.setattr(app_module, "fetch_railroad_trains", healthy)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    await app_module._refresh_railroads(app_module.app, client=None)

    async def mnr_down(client_arg, stops_arg):
        return [], {}, 990.0, ["MNR"]

    monkeypatch.setattr(app_module, "fetch_railroad_trains", mnr_down)
    for t in (1020.0, 1600.0):
        monkeypatch.setattr(app_module.time, "time", lambda t=t: t)
        await app_module._refresh_railroads(app_module.app, client=None)
    assert "MNR" in app_module.app.state.railroad_arrivals  # still inside the cap

    # Past the cap measured from the FIRST failure (1020), not from the last poll.
    monkeypatch.setattr(app_module.time, "time", lambda: 1020.0 + cap)
    await app_module._refresh_railroads(app_module.app, client=None)
    assert app_module.app.state.railroad_arrivals.get("MNR") in (None, {})
    assert cache["railroads"]["systems"]["MNR"]["retained_since"] is None
    assert cache["railroads"]["systems"]["MNR"]["ok"] is False  # still reported


async def test_c2_retention_clock_survives_an_empty_carried_list(client, cache, monkeypatch):
    # REVIEW FIX (high). retained_since was recorded only when something was
    # actually carried, so a system that went down holding an EMPTY list recorded no
    # clock, and every later poll restarted it from `now`: the cap could never fire
    # for the other kind of data merged under the same system.
    import pollers

    monkeypatch.setattr(pollers, "FEED_RETENTION_ENABLED", True)
    app_module.app.state.subway_stops = {"127N": {"name": "T", "lat": 40.7, "lon": -73.9}}

    async def poll1(stops_arg, client_arg):
        # ACE decodes with NO trains but a populated arrivals index, which the
        # decoders really do produce (placement skips not-yet-started trips while
        # arrivals keep every upcoming stop).
        return (
            [],
            {},
            990.0,
            [],
            {"ACE": []},
            {"ACE": {"127": {"Northbound": [{"trip_id": "a", "arrival": 1e12}]}}},
        )

    monkeypatch.setattr(app_module, "fetch_subway_trains", poll1)
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    await app_module._refresh_subways(app_module.app, client=None)

    async def ace_down(stops_arg, client_arg):
        return [], {}, 990.0, ["ACE"], {}, {}

    monkeypatch.setattr(app_module, "fetch_subway_trains", ace_down)
    monkeypatch.setattr(app_module.time, "time", lambda: 1020.0)
    await app_module._refresh_subways(app_module.app, client=None)
    # The clock is recorded even though nothing was carried for TRAINS.
    assert cache["subways"]["systems"]["ACE"]["retained_since"] == 1020.0


async def test_c2_route_coverage_is_derived_from_the_served_by_group_data(
    client, cache, monkeypatch
):
    # PR2's one permitted backend addition. The client needs route -> feed group to
    # know which markers a stale group's block describes (a subway train names no
    # group), and deriving it from the served by-group data means there is no
    # hand-maintained table to drift from SUBWAY_FEED_URLS.
    import pollers

    assert pollers._routes_by_system(
        {
            "ACE": [
                {"route_id": "C", "trip_id": "c1"},
                {"route_id": "A", "trip_id": "a1"},
                {"route_id": "A", "trip_id": "a2"},  # deduped
                {"trip_id": "no-route"},  # contributes nothing
            ],
            "G": [],  # decoded, nothing running: covers no routes
        }
        # Sorted, so the payload is stable poll to poll and a diff is meaningful.
    ) == {"ACE": ["A", "C"], "G": []}

    # End to end: a group serving TWO routes lists both, and the block a failed group
    # publishes still lists the routes of the trains it is retaining, which is what
    # makes those markers dimmable rather than silently un-attributable.
    monkeypatch.setattr(app_module.time, "time", lambda: 1000.0)
    two_routes = [_train("ACE", "ace-1"), {**TRAINS[0], "trip_id": "ace-2", "route_id": "E"}]
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"ACE": two_routes, "NQRW": [_train("NQRW", "nqrw-1")]}, []),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    assert cache["subways"]["systems"]["ACE"]["routes"] == ["A", "E"]

    monkeypatch.setattr(app_module.time, "time", lambda: 1060.0)
    monkeypatch.setattr(
        app_module,
        "fetch_subway_trains",
        _subway_fetch({"NQRW": [_train("NQRW", "nqrw-2")]}, ["ACE"]),
    )
    await app_module._refresh_subways(app_module.app, client=None)
    body = (await client.get("/api/subways")).json()
    assert body["systems"]["ACE"] == {
        "fetched_at": 1000.0,
        "ok": False,
        "retained_since": 1060.0,
        "routes": ["A", "E"],  # still named, because those trains are still served
    }
    # And the railroad envelope publishes null coverage: its trains carry `system`,
    # so there is nothing for the client to join on.
    app_module.app.state.railroad_stops = {"MNR": {"1": {"name": "G", "lat": 40.7, "lon": -73.9}}}

    async def railroads(client_arg, stops_arg):
        return [{**RAILROADS[0], "system": "MNR", "trip_id": "m1"}], {}, 990.0, []

    monkeypatch.setattr(app_module, "fetch_railroad_trains", railroads)
    await app_module._refresh_railroads(app_module.app, client=None)
    assert cache["railroads"]["systems"]["MNR"]["routes"] is None
