"""Endpoint tests against the FastAPI app without running the lifespan.

httpx's ASGITransport never sends lifespan events, so app.state is primed
manually per test and no real MTA endpoint is ever contacted.
"""

import asyncio
import json
import time

import httpx
import pytest

import bus_static
import feeds
import main as app_module

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
    }
    app_module.app.state.subway_feed_health = None
    app_module.app.state.railroad_feed_health = None
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
    assert res.json() == {"fetched_at": 1000.0, "feed_timestamp": 995.0, "data": BUSES}
    res = await client.get("/api/subways")
    assert res.json() == {"fetched_at": 1001.0, "feed_timestamp": 996.0, "data": TRAINS}


async def test_stale_data_beats_subsequent_error(client, cache):
    # A successful refresh followed by a failed one: last-known data is
    # served, with the old fetched_at/feed_timestamp exposing the staleness.
    cache["buses"].update(data=BUSES, fetched_at=1000.0, feed_timestamp=995.0, error=None)
    app_module._note_failure(cache["buses"], 502, "Upstream MTA feed error: boom")
    res = await client.get("/api/buses")
    assert res.status_code == 200
    assert res.json() == {"fetched_at": 1000.0, "feed_timestamp": 995.0, "data": BUSES}


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
    assert res.json() == {"fetched_at": 1001.0, "feed_timestamp": None, "data": RAILROADS}


async def test_railroads_stale_data_beats_subsequent_error(client, cache):
    cache["railroads"].update(data=RAILROADS, fetched_at=1001.0, feed_timestamp=None, error=None)
    app_module._note_failure(cache["railroads"], 502, "Upstream MTA feed error: boom")
    res = await client.get("/api/railroads")
    assert res.status_code == 200
    assert res.json()["data"] == RAILROADS  # last-known data still served


async def test_railroad_refresh_records_partial_feed_health(client, cache, monkeypatch):
    # One system fails, one returns data: the entry error stays clear, but the
    # partial outage is recorded for /api/status (parallel to the subway case).
    async def partial(client_arg, stops_arg):
        return RAILROADS, 996.0, ["MNR"]

    monkeypatch.setattr(app_module, "fetch_railroad_trains", partial)
    await app_module._refresh_railroads(app_module.app, client=None)
    assert cache["railroads"]["error"] is None
    assert cache["railroads"]["data"] == RAILROADS
    assert cache["railroads"]["feed_timestamp"] == 996.0  # threaded through from the fetch
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
        return TRAINS, {}, 996.0, ["BDFM"]

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
        return TRAINS, {}, 996.0, []

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
    """Prime the station list and arrivals index without running the lifespan."""
    app_module.app.state.subway_stations = {
        "A01": {"name": "Alpha", "lat": 40.7, "lon": -74.0},
    }
    app_module.app.state.subway_arrivals = {
        "A01": {"Northbound": [{"route_id": "1", "trip_id": "t1", "arrival": 1000.0}]}
    }
    return cache


async def test_subway_stops_lists_stations(client, subway_state):
    res = await client.get("/api/subway-stops")
    assert res.status_code == 200
    assert res.json() == [{"id": "A01", "name": "Alpha", "lat": 40.7, "lon": -74.0}]
    assert "max-age" in res.headers.get("cache-control", "")


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
        return TRAINS, {}, 1001.0, []

    async def fake_fetch_railroads(client, stops):
        return RAILROADS, 1002.0, []

    async def fake_load_railroad_static():
        # No network. LIRR carries stops/trips/shapes; MNR failed to load (None).
        return {
            "LIRR": {
                "stops": {"1": {"name": "Aville", "lat": 40.7, "lon": -74.0}},
                "trips": {"t1": {"route_id": "5", "shape_id": "s1"}},
                "shapes": {"s1": [[40.7, -74.0], [40.71, -74.01]]},
            },
            "MNR": None,
        }

    async def fake_ensure_index():
        return None

    monkeypatch.setattr(app_module, "load_subway_stops", fake_stops)
    monkeypatch.setattr(app_module, "load_subway_route_shapes", lambda: [])
    monkeypatch.setattr(app_module, "load_subway_stations", lambda: {})
    monkeypatch.setattr(app_module, "fetch_vehicle_positions", fake_fetch_buses)
    monkeypatch.setattr(app_module, "fetch_subway_trains", fake_fetch_subways)
    monkeypatch.setattr(app_module, "fetch_railroad_trains", fake_fetch_railroads)
    monkeypatch.setattr(
        app_module.railroad_static, "load_railroad_static", fake_load_railroad_static
    )
    monkeypatch.setattr(bus_static, "ensure_index", fake_ensure_index)

    app = app_module.app
    async with app_module.lifespan(app):
        # Static load ran; cache + tasks exist.
        assert app.state.subway_stops == {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}
        # The full per-system railroad static is kept (trips/shapes for gliding),
        # and railroad_stops is derived from it (None for the failed system).
        assert app.state.railroad_static["LIRR"]["trips"] == {
            "t1": {"route_id": "5", "shape_id": "s1"}
        }
        assert app.state.railroad_static["LIRR"]["shapes"]["s1"] == [[40.7, -74.0], [40.71, -74.01]]
        assert app.state.railroad_stops["LIRR"] == {
            "1": {"name": "Aville", "lat": 40.7, "lon": -74.0}
        }
        assert app.state.railroad_stops["MNR"] is None
        # The carry-forward memory is initialized empty. Asserted here, before the
        # block below waits on the first poll, so it is still the startup value
        # (the poll's carry_forward_prev would otherwise fill it).
        assert app.state.railroad_positions == {}
        assert app.state.subway_positions == {}
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # Wait for the background poll task's first cycle to fill the cache.
            for _ in range(100):
                if app.state.feed_cache["buses"]["data"] is not None:
                    break
                await asyncio.sleep(0.01)
            assert app.state.feed_cache["buses"]["data"] == BUSES
            assert app.state.feed_cache["buses"]["feed_timestamp"] == 1000.0
            res = await c.get("/api/status")
            assert res.status_code == 200
            assert res.json()["feeds"]["buses"]["fetched_at"] is not None
        poll_task = app.state.feed_poll_task
        index_task = app.state.bus_index_task

    # Shutdown cancelled/awaited both background tasks.
    assert poll_task.done()
    assert index_task.done()
