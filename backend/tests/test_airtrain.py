"""Tests for the AirTrain JFK static layer.

Three concerns: the committed JSON IS the golden artifact (so a silent
regeneration that drifts the station or route count must fail loudly here), the
load_airtrain loader and its raise paths, and the /api/airtrain endpoint. Endpoint
tests prime app.state manually and never run the lifespan, matching test_api.py
(httpx's ASGITransport does not send lifespan events).
"""

import json

import httpx
import pytest

import airtrain_static
import main as app_module
from models import AirTrainData

# A minimal well-formed station / route, reused to build both the endpoint payload
# and the wrong-keyed loader cases by mutating exactly one thing.
VALID_STATION = {"id": "1", "name": "Alpha", "lat": 40.7, "lon": -73.8}
VALID_ROUTE = {
    "id": "r1",
    "name": "Loop",
    "polyline": [[40.7, -73.8], [40.71, -73.81]],
    "stations": ["1"],
    "headways": [{"start": "00:00", "end": "24:00", "headway_min": 15}],
}


# ---------------- golden fixture: the committed data/airtrain_jfk.json ----------------


def test_committed_fixture_has_expected_counts():
    # Load the ACTUAL committed artifact (not a synthetic fixture) so a silent
    # regeneration that changes the station or route count fails loudly right here.
    data = json.loads(airtrain_static.AIRTRAIN_FIXTURE.read_text(encoding="utf-8"))
    assert len(data["stations"]) == 10
    assert len(data["routes"]) == 3


def test_committed_fixture_validates_against_model():
    # The committed artifact must satisfy the response model exactly.
    AirTrainData.model_validate(airtrain_static.load_airtrain())


def test_load_airtrain_strips_provenance():
    # The committed file carries a _provenance block; load_airtrain must return only
    # the rider-facing payload so it never reaches clients.
    assert set(airtrain_static.load_airtrain()) == {"stations", "routes"}


# ---------------- load_airtrain raise paths ----------------


def test_load_airtrain_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        airtrain_static.load_airtrain(tmp_path / "does_not_exist.json")


def test_load_airtrain_raises_on_malformed_json(tmp_path):
    bad = tmp_path / "airtrain.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        airtrain_static.load_airtrain(bad)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"routes": [VALID_ROUTE]}, id="stations-key-missing"),
        pytest.param({"stations": [], "routes": [VALID_ROUTE]}, id="stations-empty"),
        pytest.param({"stations": [VALID_STATION]}, id="routes-key-missing"),
        pytest.param({"stations": [VALID_STATION], "routes": []}, id="routes-empty"),
        pytest.param(
            {"stations": [{"id": "1", "name": "A", "lat": 40.7}], "routes": [VALID_ROUTE]},
            id="station-missing-lon",
        ),
        pytest.param(
            {
                "stations": [VALID_STATION],
                "routes": [{"id": "r", "name": "R", "polyline": [], "stations": []}],
            },
            id="route-missing-headways",
        ),
        pytest.param(
            {"stations": [VALID_STATION], "routes": [{**VALID_ROUTE, "headways": {}}]},
            id="headways-not-a-list",
        ),
        pytest.param(
            {
                "stations": [VALID_STATION],
                "routes": [{**VALID_ROUTE, "headways": [{"start": "00:00", "end": "06:00"}]}],
            },
            id="band-missing-headway_min",
        ),
    ],
)
def test_load_airtrain_raises_on_wrong_keyed_structure(tmp_path, payload):
    bad = tmp_path / "airtrain.json"
    bad.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        airtrain_static.load_airtrain(bad)


# ---------------- /api/airtrain endpoint (app.state primed manually) ----------------


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_airtrain_endpoint_serves_fixture_and_is_cacheable(client):
    app_module.app.state.airtrain = airtrain_static.load_airtrain()
    res = await client.get("/api/airtrain")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "public, max-age=3600"
    body = res.json()
    assert len(body["stations"]) == 10
    assert len(body["routes"]) == 3


@pytest.mark.anyio
async def test_airtrain_endpoint_keys_are_exactly_stations_and_routes(client):
    # Lock the _provenance non-leak at the ENDPOINT boundary too: even if app.state
    # carried extra keys, response_model=AirTrainData strips all but stations/routes.
    app_module.app.state.airtrain = {
        "stations": [VALID_STATION],
        "routes": [VALID_ROUTE],
        "_provenance": {"leak": "must not appear in the response"},
    }
    res = await client.get("/api/airtrain")
    assert res.status_code == 200
    assert set(res.json()) == {"stations", "routes"}
