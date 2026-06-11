"""Endpoint tests against the FastAPI app without running the lifespan.

httpx's ASGITransport never sends lifespan events, so app.state is primed
manually per test and no real MTA endpoint is ever contacted.
"""

import json

import httpx
import pytest

import bus_static
import main as app_module

pytestmark = pytest.mark.anyio

BUSES = [{"id": "MTA NYCT_1", "route_id": "M15", "latitude": 40.7, "longitude": -74.0, "bearing": 90.0}]
TRAINS = [{"trip_id": "70000_1..N01R", "route_id": "1", "latitude": 40.7, "longitude": -74.0,
           "stop_id": "101N", "stop_name": "Alpha", "direction": "Northbound"}]


@pytest.fixture
def cache():
    app_module.app.state.feed_cache = {
        "buses": app_module._fresh_entry(),
        "subways": app_module._fresh_entry(),
    }
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
    cache["buses"].update(data=BUSES, fetched_at=1000.0, error=None)
    cache["subways"].update(data=TRAINS, fetched_at=1001.0, error=None)
    res = await client.get("/api/buses")
    assert res.status_code == 200
    assert res.json() == {"fetched_at": 1000.0, "data": BUSES}
    res = await client.get("/api/subways")
    assert res.json() == {"fetched_at": 1001.0, "data": TRAINS}


async def test_stale_data_beats_subsequent_error(client, cache):
    # A successful refresh followed by a failed one: last-known data is
    # served, with the old fetched_at exposing the staleness.
    cache["buses"].update(data=BUSES, fetched_at=1000.0, error=None)
    app_module._note_failure(cache["buses"], 502, "Upstream MTA feed error: boom")
    res = await client.get("/api/buses")
    assert res.status_code == 200
    assert res.json() == {"fetched_at": 1000.0, "data": BUSES}


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
    assert body["feeds"]["buses"] == {"fetched_at": None, "age_s": None, "last_error": None}
    assert body["bus_route_index"] == {"status": "building", "partial": False}
    assert body["static_subway_gtfs"] is None


async def test_status_reports_ages_errors_and_gtfs_mtime(client, cache, status_env, tmp_path, monkeypatch):
    import time as time_mod

    import static_data

    _, set_state = status_env
    set_state("ready", partial=True)
    gtfs = tmp_path / "gtfs_subway.zip"
    gtfs.write_bytes(b"zip")
    monkeypatch.setattr(static_data, "SUBWAY_GTFS_ZIP", gtfs)

    cache["buses"].update(data=BUSES, fetched_at=time_mod.time() - 30, error=None)
    app_module._note_failure(cache["subways"], 502, "All subway feeds failed: timeout")

    res = await client.get("/api/status")
    body = res.json()
    assert 29 <= body["feeds"]["buses"]["age_s"] <= 40
    assert body["feeds"]["buses"]["last_error"] is None
    assert body["feeds"]["subways"]["last_error"] == {
        "status": 502, "detail": "All subway feeds failed: timeout",
    }
    assert body["bus_route_index"] == {"status": "ready", "partial": True}
    assert body["static_subway_gtfs"]["age_s"] >= 0
    # No secrets or filesystem paths in the payload.
    text = res.text
    assert "BUS_TIME_API_KEY=" not in text
    assert str(tmp_path) not in text and "/Users/" not in text and "/app/" not in text


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
