"""Q10 of the freshness contract's Amendment 1: a board answers a failed first poll with
its block, never with the upstream's error text.

A single-system board (PATH, NJ Transit, the ferry), a railroad board with both
railroads failing, and (by the ruling of 2026-11-08) a subway board whose every group
failed, answered a failed FIRST poll with HTTP 502 and `{"detail": ...}`, the
upstream exception's own text with only its URLs scrubbed, and the popup and the panel
printed it and the panel spoke it (H6 on the 2026-10-12 record's live list, measured with a
stub whose reason phrase and body each carried a marker: the reason phrase reached the rider
on PATH, the ferry and both railroads, the body on NJ Transit). Q10 answered yes: each now
answers 200 with a block reading fetched_at null and ok false, and PATH's and the ferry's
boards carry their one system's block on every answer. The 503 before any poll stays, and
the vehicle envelopes do not change.

Each family here is a FRESH process's first poll through its real refresher and its real
fetch, against H6's stub: an httpx.Response 503 whose reason phrase is REASON and whose
body is BODY. NJ Transit runs the real njt_auth.njt_post with a fake rider and secret and a
fresh token cache, its two POSTs answered in-process, so no request leaves and no mint is
spent. The boards are pinned to tests/e2e/fixtures/q10_first_poll_boards.json with the
operator's copy of the failure beside each, which frontend/boards.test.js renders through
the real popup and panel: the RIDER half of H6 is proved rendered, not read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from test_f03_boards import NOW

import main as app_module
import njt_auth
import pollers
from conftest import configure_njt

pytestmark = pytest.mark.anyio

REASON = "Q10-REASON-PHRASE-MARKER"
BODY = "Q10-RESPONSE-BODY-MARKER"
GOLDEN = Path(__file__).parents[2] / "tests" / "e2e" / "fixtures" / "q10_first_poll_boards.json"
REGENERATE_VAR = "Q10_BOARDS_REGENERATE"
# The never-decoded block every family's first failure writes (pollers._system_freshness).
NEVER_DECODED = {
    "fetched_at": None,
    "feed_timestamp": None,
    "ok": False,
    "retained_since": None,
    "routes": None,
    "positions": None,
}
# Each family: its feed-cache entry, its board (the browser suite's fixture stop, so the
# rendered half can call the board the way its loader does), and which marker its 502 used
# to hand the rider (H6).
FAMILIES = {
    "subway": ("subways", "/api/subway-arrivals/127", "/api/subways", REASON),
    "path": ("path", "/api/path-arrivals/26734", "/api/path", REASON),
    "ferry": ("ferry", "/api/ferry-arrivals/18", "/api/ferry", REASON),
    "njt": ("njt", "/api/njt-arrivals/109", "/api/njt-trains", BODY),
    "lirr": ("railroads", "/api/railroad-arrivals/LIRR/12", "/api/railroads", REASON),
}


class _H6Upstream:
    """H6's stub for the GET families: every URL answers HTTP 503 with REASON as its
    reason phrase and BODY as its body, as a real httpx.Response."""

    async def get(self, url: str, **_kwargs) -> httpx.Response:
        return httpx.Response(
            503,
            content=BODY.encode(),
            extensions={"reason_phrase": REASON.encode()},
            request=httpx.Request("GET", url),
        )


async def _h6_njt_post(url: str, form: dict, timeout_s: float) -> tuple[int, bytes]:
    """njt_auth's transport for H6's NJ Transit arm: getToken mints, the feed answers 503
    with BODY. The status line's reason phrase never reaches this layer (njt_post keeps the
    status and the bytes), which is why H6 measured the body here and the reason elsewhere."""
    if url.rstrip("/").endswith("getToken"):
        return 200, json.dumps({"UserToken": "q10-token"}).encode()
    return 503, BODY.encode()


@pytest.fixture
def fresh(monkeypatch):
    """A process before its first poll: empty caches, every static group ready, no health,
    NJ Transit configured with fake credentials over an in-process transport, and the
    browser suite's frozen clock."""
    state = app_module.app.state
    primed = {
        "feed_cache": {
            name: app_module._fresh_entry()
            for name in ("buses", "subways", "railroads", "path", "ferry", "njt")
        },
        "path_stops": {"26734": {"id": "26734", "name": "World Trade Center", "lat": 0, "lon": 0}},
        "path_arrivals": {},
        "path_identity": app_module.new_path_identity_state("t"),
        "path_station_order": {},
        "path_feed_health": None,
        "ferry_static_status": "ready",
        "ferry_static": {},
        "ferry_stops": {"18": {"id": "18", "name": "Wall St/Pier 11", "lat": 0, "lon": 0}},
        "ferry_arrivals": {},
        "ferry_feed_health": None,
        "njt_static_status": "ready",
        "njt_stops": {"109": {"name": "New York Penn Station", "lat": 0, "lon": 0}},
        "njt_trips": {},
        "njt_arrivals": {},
        "njt_feed_health": None,
        "railroad_stops": {
            "LIRR": {"12": {"name": "Jamaica", "lat": 0, "lon": 0}},
            "MNR": {"1": {"name": "Grand Central", "lat": 0, "lon": 0}},
        },
        "railroad_arrivals": {},
        "railroad_positions": {},
        "railroad_feed_health": None,
        "subway_static_status": "ready",
        "subway_stops": {"127N": {"name": "Times Sq-42 St", "lat": 0, "lon": 0}},
        "subway_stations": {"127": {"name": "Times Sq-42 St", "lat": 0, "lon": 0}},
        "subway_station_routes": {"127": ["1", "2", "3"]},
        "subway_arrivals": {},
        "subway_arrivals_by_system": {},
        "subway_positions": {},
        "subway_feed_health": None,
        "subway_unmapped_routes": {},
    }
    for name, value in primed.items():
        monkeypatch.setattr(state, name, value, raising=False)
    configure_njt(monkeypatch)
    monkeypatch.setattr(njt_auth, "TOKEN_CACHE", njt_auth.TokenCache())
    monkeypatch.setattr(njt_auth, "_httpx_post", _h6_njt_post)
    clock = [NOW]
    monkeypatch.setattr(app_module.time, "time", lambda: clock[0])
    return clock


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://q10") as http:
        return await http.get(path)


async def _first_failure() -> dict[str, dict]:
    """Each family's first poll, failing; then each board, its vehicle envelope and the
    operator's copy of the failure, as served."""
    upstream = _H6Upstream()
    await pollers._refresh_subways(app_module.app, upstream)
    await pollers._refresh_path(app_module.app, upstream)
    await pollers._refresh_ferry(app_module.app, upstream)
    await pollers._refresh_njt(app_module.app, upstream)
    await pollers._refresh_railroads(app_module.app, upstream)
    status = (await _get("/api/status")).json()
    served = {}
    for family, (entry, board_path, vehicles_path, _) in FAMILIES.items():
        served[family] = {
            "board": await _get(board_path),
            "vehicles": await _get(vehicles_path),
            "detail": status["feeds"][entry]["last_error"]["detail"],
        }
    return served


async def test_every_board_answers_a_failed_first_poll_with_its_never_decoded_block(fresh):
    """PATH, the ferry, NJ Transit and a railroad board with both railroads failing: HTTP
    200, no rows, and the block reading fetched_at null, ok false, where each answered 502.
    The vehicle envelopes still answer the recorded 502, which Q10 does not change."""
    served = await _first_failure()
    bodies = {}
    for family, got in served.items():
        assert got["board"].status_code == 200, (family, got["board"].text)
        assert got["vehicles"].status_code == 502, (family, got["vehicles"].text)
        board = got["board"].json()
        bodies[family] = board
        assert (board["fetched_at"], board["feed_timestamp"], board["served_at"]) == (
            None,
            None,
            NOW,
        ), family
        assert list(board["systems"].values()) == [
            {**NEVER_DECODED, "routes": []} if family == "subway" else NEVER_DECODED
        ], family
    assert set(bodies["subway"]["systems"]) == {"1-7+S"}
    assert bodies["subway"]["directions"] == {"Northbound": [], "Southbound": []}
    assert set(bodies["path"]["systems"]) == {"PATH"}
    assert set(bodies["ferry"]["systems"]) == {"ferry"}
    assert set(bodies["njt"]["systems"]) == {"njt"}
    assert set(bodies["lirr"]["systems"]) == {"LIRR"}
    assert (bodies["path"]["directions"], bodies["ferry"]["routes"]) == ({}, {})
    assert (bodies["njt"]["arrivals"], bodies["lirr"]["directions"]) == ([], {})


async def test_the_upstreams_text_stays_on_the_operators_surface(fresh):
    """H6, split the way Q10 splits it. Each failure's detail still carries the marker H6
    measured (the reason phrase on the subway, PATH, the ferry and the railroads, the body on NJ
    Transit) on /api/status's last_error and on the vehicle envelope's 502, and neither
    marker is anywhere in a board. The served boards and the operator's details are the
    committed fixture frontend/boards.test.js renders: the rider half, rendered."""
    served = await _first_failure()
    record = {}
    for family, (_, _, _, marker) in FAMILIES.items():
        got = served[family]
        assert marker in got["detail"], (family, got["detail"])
        assert got["vehicles"].json()["detail"] == got["detail"], family
        assert REASON not in got["board"].text and BODY not in got["board"].text, family
        record[family] = {"board": got["board"].json(), "detail": got["detail"]}
    assert (
        served["njt"]["detail"]
        == f"Upstream NJ Transit feed error: NJ Transit returned HTTP 503: {BODY}"
    )
    assert served["path"]["detail"].startswith(
        f"Upstream PATH bridge feed error: Server error '503 {REASON}'"
    )
    if os.environ.get(REGENERATE_VAR) == "1":
        GOLDEN.write_text(json.dumps(record, indent=2) + "\n")
    assert record == json.loads(GOLDEN.read_text()), (
        f"the first-failure boards no longer match {GOLDEN.name}, which the browser half "
        f"renders. If the change is intentional, regenerate with {REGENERATE_VAR}=1 and "
        "review the diff; otherwise the backend has drifted."
    )


async def test_the_503_before_any_poll_stays(fresh, monkeypatch):
    """Nothing tried, nothing to say: before any poll every board answers the warming 503,
    and so do NJ Transit unconfigured, a PATH static group still loading and NJ Transit's
    static still loading, whose polls never run and so write no block."""
    for _, board_path, _, _ in FAMILIES.values():
        response = await _get(board_path)
        assert (response.status_code, response.json()["detail"]) == (
            503,
            "Feed cache is warming up; try again in a few seconds.",
        )
    # Assigned, not patched: the fixture patched these names, and pytest 9.2 restores a
    # patched app.state name from the instance __dict__, which Starlette's State keeps empty.
    app_module.app.state.njt_static_status = "not-configured"
    await pollers._refresh_njt(app_module.app, None)
    response = await _get("/api/njt-arrivals/109")
    assert response.status_code == 503
    assert response.json()["detail"].startswith("NJ Transit is not configured")
    app_module.app.state.path_stops = {}
    await pollers._refresh_path(app_module.app, _H6Upstream())
    assert app_module.app.state.feed_cache["path"].get("systems") is None
    assert app_module.app.state.feed_cache["path"]["error"]["status"] == 503
    app_module.app.state.njt_static_status = "loading"
    await pollers._refresh_njt(app_module.app, None)
    assert app_module.app.state.feed_cache["njt"].get("systems") is None
    response = await _get("/api/njt-arrivals/109")
    assert response.status_code == 503
    assert response.json()["detail"].startswith("Static NJ Transit GTFS is still loading")


@pytest.mark.parametrize("family", ["path", "ferry"])
async def test_an_undecodable_first_poll_writes_the_same_block(fresh, monkeypatch, family):
    """A 200 whose body is not a protobuf, on the process's first poll: PATH's and the
    ferry's own arm for it writes the never-decoded block too, so the board answers 200."""
    from google.protobuf.message import DecodeError

    async def html(*_args):
        raise DecodeError("not a protobuf")

    if family == "path":
        monkeypatch.setattr(app_module, "fetch_path_trains", html)
        await pollers._refresh_path(app_module.app, None)
        response, system = await _get("/api/path-arrivals/26734"), "PATH"
    else:
        monkeypatch.setattr(app_module, "fetch_ferry_data", html)
        await pollers._refresh_ferry(app_module.app, None)
        response, system = await _get("/api/ferry-arrivals/18"), "ferry"
    assert response.status_code == 200
    assert response.json()["systems"] == {system: NEVER_DECODED}


async def test_a_deadline_on_the_subways_first_poll_names_its_expected_groups(fresh, monkeypatch):
    """The subway through the cycle's degrader: a first poll killed by the deadline writes a
    never-decoded block for every group, with the subway's route coverage ([]), so Times Sq's
    board answers 200 naming 1-7+S, the group its routes 1, 2 and 3 expect."""
    entry = app_module.app.state.feed_cache["subways"]
    monkeypatch.setattr(pollers, "REFRESH_DEADLINE_S", 0.01)

    async def wedged():
        await pollers.asyncio.Event().wait()

    await pollers._bounded_refresh(
        entry, wedged(), pollers._feed_degrader(app_module.app, "subways", entry)
    )
    assert sorted(entry["systems"]) == sorted(pollers.SUBWAY_FEED_URLS)
    response = await _get("/api/subway-arrivals/127")
    assert response.status_code == 200
    assert response.json()["systems"] == {"1-7+S": {**NEVER_DECODED, "routes": []}}


async def test_a_deadline_on_the_first_poll_writes_the_same_block(fresh, monkeypatch):
    """A first poll killed by the refresh deadline never reaches the refresher's own arms;
    the cycle's degrader writes the never-decoded block, so the board answers 200 too."""
    entry = app_module.app.state.feed_cache["ferry"]
    monkeypatch.setattr(pollers, "REFRESH_DEADLINE_S", 0.01)

    async def wedged():
        await pollers.asyncio.Event().wait()

    await pollers._bounded_refresh(
        entry, wedged(), pollers._feed_degrader(app_module.app, "ferry", entry)
    )
    response = await _get("/api/ferry-arrivals/18")
    assert response.status_code == 200
    assert response.json()["systems"] == {"ferry": NEVER_DECODED}


async def test_path_and_the_ferry_carry_their_block_on_every_answer(fresh, monkeypatch):
    """Healthy, then failing: PATH's and the ferry's boards name their one system with
    `ok`, where they served systems null always. The block's content clock is null by
    policy (the bridge's write time and the boats' header date nothing a board shows);
    the board's own feed_timestamp still comes from its rows."""
    row = {
        "route_id": "862",
        "arrival": NOW + 300,
        "observed_at": NOW - 7,
        "provenance": "reported",
    }

    async def path_ok(client, stops):
        return [], {"26734": {"To New York": [row]}}, NOW - 2, 0

    async def ferry_ok(client, static):
        return [], {}, NOW - 3

    monkeypatch.setattr(app_module, "fetch_path_trains", path_ok)
    monkeypatch.setattr(app_module, "fetch_ferry_data", ferry_ok)
    await pollers._refresh_path(app_module.app, None)
    await pollers._refresh_ferry(app_module.app, None)
    healthy = {"fetched_at": NOW, "feed_timestamp": None, "ok": True, "retained_since": None}
    path = (await _get("/api/path-arrivals/26734")).json()
    ferry = (await _get("/api/ferry-arrivals/18")).json()
    assert path["systems"] == {"PATH": {**healthy, "routes": None, "positions": None}}
    assert path["feed_timestamp"] == NOW - 7
    assert ferry["systems"] == {"ferry": {**healthy, "routes": None, "positions": None}}

    async def down(*_args):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(app_module, "fetch_path_trains", down)
    monkeypatch.setattr(app_module, "fetch_ferry_data", down)
    fresh[0] = NOW + 20
    await pollers._refresh_path(app_module.app, None)
    await pollers._refresh_ferry(app_module.app, None)
    failed = {**healthy, "ok": False, "routes": None, "positions": None}
    assert (await _get("/api/path-arrivals/26734")).json()["systems"] == {"PATH": failed}
    assert (await _get("/api/ferry-arrivals/18")).json()["systems"] == {"ferry": failed}


async def test_one_railroad_down_is_unchanged(fresh, monkeypatch):
    """LIRR failing on the first poll beside a healthy Metro-North already answered 200 with
    LIRR's never-decoded block before Q10, and still does."""

    async def mnr_up(client, stops):
        return [], {"MNR": {}}, None, ["LIRR"], {"MNR": None}, {}

    monkeypatch.setattr(app_module, "fetch_railroad_trains", mnr_up)
    await pollers._refresh_railroads(app_module.app, None)
    response = await _get("/api/railroad-arrivals/LIRR/12")
    assert response.status_code == 200
    assert response.json()["systems"]["LIRR"]["fetched_at"] is None
    assert response.json()["systems"]["LIRR"]["ok"] is False
