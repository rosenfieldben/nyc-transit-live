"""A03's backend half: a station board names the groups it EXPECTS (Amendment 1, Q1 and Q2).

The acceptance, in the auditor's words (A1.6 Step 2): "Maintain station-to-feed coverage
independently of displayed predictions; preserve expected failed contributors after expiry
or retention limits." Concretely, in A03's world: after the T0+200 s expiry and at the
T0+620 s cap, 219's `systems` names 1-7+S with ok false; in world B it names 1-7+S with
fetched_at null; and no served board is identical to fresh empty service's while a
contributor fails.

A03'S WORLD IS F03'S (test_f03_boards.f03_world, unwrapped onto this module's monkeypatch):
the committed 1-7+S capture and its stops, the frozen clock T0, station 219 Prospect Av
with its real static routes (2 and 5, so it expects 1-7+S), a poll every 20 s, 1-7+S
answering HTTP 503 beside seven healthy groups. The expiry road needs a station whose last
call falls inside the retention window, so that world serves the capture with every trip
calling at 219 after T0+200 s removed, as the A03 witness does; the cap road serves it
whole. Every board below is read as SERVED JSON through the ASGI app.
"""

from __future__ import annotations

import inspect
import json

import httpx
import pytest
import test_f03_boards
from google.transit import gtfs_realtime_pb2 as pb
from test_f03_boards import NOW, STATION_ID, _quiet_feed, _restamp, _Upstream

import cache
import main as app_module
import pollers
from feeds.subway import SUBWAY_FEED_URLS

pytestmark = pytest.mark.anyio

POLL_S = 20.0
FAILED = "1-7+S"
PLATFORMS = {"219N", "219S"}


@pytest.fixture
def world(monkeypatch):
    """F03's world: the app.state a lifespan builds, isolated by monkeypatch, and the
    patched clock, handed back as a one-element list."""
    return inspect.unwrap(test_f03_boards.f03_world)(monkeypatch)


class _Failing(_Upstream):
    """The world's upstream with 1-7+S answering HTTP 503 when `failing`."""

    def __init__(self, bodies: dict[str, bytes], failing: bool) -> None:
        super().__init__(bodies)
        self.failing = failing

    async def get(self, url: str) -> httpx.Response:
        if self.failing and url == SUBWAY_FEED_URLS[FAILED]:
            return httpx.Response(503, content=b"unavailable", request=httpx.Request("GET", url))
        return await super().get(url)


def _last_trains(raw: bytes, cutoff: float) -> bytes:
    """The feed with every trip calling at 219 after `cutoff` removed, with its vehicle."""
    feed = pb.FeedMessage()
    feed.ParseFromString(raw)
    out = pb.FeedMessage()
    out.header.CopyFrom(feed.header)
    dropped = set()
    for entity in feed.entity:
        if entity.HasField("trip_update"):
            calls = [
                max(
                    getattr(stu, field).time
                    for field in ("arrival", "departure")
                    if stu.HasField(field)
                )
                for stu in entity.trip_update.stop_time_update
                if stu.stop_id in PLATFORMS
            ]
            if any(t > cutoff for t in calls):
                dropped.add(entity.trip_update.trip.trip_id)
                continue
        out.entity.add().CopyFrom(entity)
    kept = pb.FeedMessage()
    kept.header.CopyFrom(out.header)
    for entity in out.entity:
        if not (entity.HasField("vehicle") and entity.vehicle.trip.trip_id in dropped):
            kept.entity.add().CopyFrom(entity)
    return kept.SerializeToString()


async def _poll(clock: list[float], at: float, body: bytes | None, failing: bool) -> dict:
    """One real refresh at `at`, every group a fresh, valid, empty feed stamped `at` and
    1-7+S `body` (or 503), then 219's board as served."""
    clock[0] = at
    bodies = {url: _quiet_feed(at) for url in SUBWAY_FEED_URLS.values()}
    if body is not None:
        bodies[SUBWAY_FEED_URLS[FAILED]] = body
    await pollers._refresh_subways(app_module.app, _Failing(bodies, failing))
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://a03") as http:
        response = await http.get(f"/api/subway-arrivals/{STATION_ID}")
    assert response.status_code == 200, response.text
    return response.json()


def _rows(board: dict) -> int:
    return sum(len(rows) for rows in board["directions"].values())


def _relative(board: dict) -> dict:
    """The board with every clock in it read relative to the instant it was served, the
    form in which A03 compared it to fresh empty service's."""
    at = board["served_at"]

    def rel(value):
        return None if value is None else value - at

    return {
        **board,
        "served_at": 0.0,
        "fetched_at": rel(board["fetched_at"]),
        "feed_timestamp": rel(board["feed_timestamp"]),
        "systems": None
        if board["systems"] is None
        else {
            name: {
                **block,
                "fetched_at": rel(block["fetched_at"]),
                "feed_timestamp": rel(block["feed_timestamp"]),
                "retained_since": rel(block["retained_since"]),
            }
            for name, block in board["systems"].items()
        },
    }


async def _fresh_empty(clock: list[float]) -> dict:
    """Genuinely fresh empty service: every group a fresh, valid, empty feed."""
    await _poll(clock, NOW, None, False)
    return await _poll(clock, NOW + POLL_S, None, False)


async def test_fresh_empty_service_names_its_expected_group_healthy(world):
    """The baseline the four states are told from: 219 expects 1-7+S, so even with nothing
    running there its board names 1-7+S, ok and current, where it used to serve systems
    null and the aggregate's clock."""
    board = await _fresh_empty(world)
    assert _rows(board) == 0
    assert board["fetched_at"] == NOW + POLL_S
    assert board["feed_timestamp"] == NOW + POLL_S
    assert board["systems"] == {
        FAILED: {
            "fetched_at": NOW + POLL_S,
            "feed_timestamp": NOW + POLL_S,
            "ok": True,
            "retained_since": None,
            "routes": [],
            "positions": None,
        }
    }


async def test_after_the_last_retained_row_expires_the_board_still_names_the_failed_group(world):
    """The expiry road. 219's last retained prediction is due at T0+194 s, so the T0+200 s
    poll is the first with nothing left at 219. Before Amendment 1 that board served
    systems null and the aggregate's 0 s old clock, identical to fresh empty service's; now
    it names 1-7+S, ok false, its clocks frozen at its last decode, and so does its own
    envelope: 200 s old by poll, content T0-5."""
    fresh = _relative(await _fresh_empty(world))
    last = _last_trains(_restamp(test_f03_boards._CAPTURE.read_bytes(), NOW, 5.0), NOW + 200)
    board = await _poll(world, NOW, last, False)
    assert (_rows(board), sorted(board["systems"])) == (2, [FAILED])
    for k in range(1, 10):
        board = await _poll(world, NOW + k * POLL_S, None, True)
        assert _rows(board) > 0, f"the last row left early, at T0+{k * POLL_S:.0f} s"
    board = await _poll(world, NOW + 200, None, True)
    assert _rows(board) == 0
    assert (board["fetched_at"], board["feed_timestamp"]) == (NOW, NOW - 5)
    block = board["systems"][FAILED]
    assert sorted(board["systems"]) == [FAILED]
    assert (block["ok"], block["fetched_at"], block["feed_timestamp"]) == (False, NOW, NOW - 5)
    assert block["retained_since"] == NOW + POLL_S
    assert _relative(board) != fresh


async def test_at_the_retention_cap_the_board_still_names_the_failed_group(world):
    """The cap road, on the untrimmed capture. At T0+620 s the cap drops 1-7+S's twelve
    retained rows at 219; before Amendment 1 the board then served systems null and a 0 s
    old clock. Now it names 1-7+S, ok false, 620 s old. Its retained_since reads null at
    the cap poll, because the capped branch records no clock (H4 on the 2026-10-12 record's
    live list): the block's fetched_at, not retained_since, says nothing is held."""
    fresh = _relative(await _fresh_empty(world))
    whole = _restamp(test_f03_boards._CAPTURE.read_bytes(), NOW, 5.0)
    await _poll(world, NOW, whole, False)
    for k in range(1, 31):
        board = await _poll(world, NOW + k * POLL_S, None, True)
    assert _rows(board) == 12
    board = await _poll(world, NOW + 620, None, True)
    assert _rows(board) == 0
    assert (board["fetched_at"], board["feed_timestamp"]) == (NOW, NOW - 5)
    block = board["systems"][FAILED]
    assert sorted(board["systems"]) == [FAILED]
    assert (block["ok"], block["fetched_at"], block["retained_since"]) == (False, NOW, None)
    assert _relative(board) != fresh


async def test_a_group_that_never_decoded_is_named_with_no_clock(world):
    """World B (Q2): 1-7+S answers 503 from the process's first poll. Its block reads
    fetched_at null, feed_timestamp null and ok false, which is the field that tells never
    decoded from failed, and the board's own two clocks are null by rule 3 (no contributor
    that cannot be dated is spoken for): before Amendment 1 the group had no row here, so it
    was not a contributor, and the board read exactly as fresh empty service's."""
    fresh = _relative(await _fresh_empty(world))
    world_b = app_module.app.state
    world_b.feed_cache = {"subways": app_module._fresh_entry()}
    world_b.subway_arrivals = {}
    world_b.subway_arrivals_by_system = {}
    await _poll(world, NOW, None, True)
    board = await _poll(world, NOW + POLL_S, None, True)
    assert _rows(board) == 0
    assert (board["fetched_at"], board["feed_timestamp"]) == (None, None)
    assert board["systems"] == {
        FAILED: {
            "fetched_at": None,
            "feed_timestamp": None,
            "ok": False,
            "retained_since": NOW,
            "routes": [],
            "positions": None,
        }
    }
    assert _relative(board) != fresh


async def test_stale_content_behind_a_current_poll_is_told_apart_by_its_clock(world):
    """World A: 1-7+S decodes on every poll and every call it carries at 219 is past the
    decoder's grace, its header 620 s old by the second poll. The board names 1-7+S ok and
    polled now, with its content clock: the field that tells stale from fresh."""
    fresh = _relative(await _fresh_empty(world))
    last = _last_trains(_restamp(test_f03_boards._CAPTURE.read_bytes(), NOW, 5.0), NOW + 200)
    stale = _restamp(last, NOW, 600.0)
    await _poll(world, NOW, stale, False)
    board = await _poll(world, NOW + POLL_S, stale, False)
    assert _rows(board) == 0
    block = board["systems"][FAILED]
    assert (block["ok"], block["fetched_at"], block["feed_timestamp"]) == (
        True,
        NOW + POLL_S,
        NOW - 600,
    )
    assert board["feed_timestamp"] == NOW - 600
    assert _relative(board) != fresh


def _block(fetched_at, feed_timestamp, ok=True):
    return {
        "fetched_at": fetched_at,
        "feed_timestamp": feed_timestamp,
        "ok": ok,
        "retained_since": None if ok else 900.0,
        "routes": [],
    }


def test_the_three_selectors_read_expected_united_with_seen():
    """The membership rule, on Forest Hills-71 Av's real static routes (E, F, FX, M, R, so
    it expects ACE, BDFM and NQRW), with literal clocks. Rows from ACE alone; BDFM has never
    decoded; NQRW is healthy with no row here; G has a row here and is not expected; SIR is
    neither, and does not take part."""
    entry = {
        "fetched_at": 1000.0,
        "systems": {
            "ACE": _block(990.0, 985.0),
            "BDFM": _block(None, None, ok=False),
            "NQRW": _block(995.0, 990.0),
            "G": _block(998.0, 980.0),
            "SIR": _block(None, None, ok=False),
        },
    }
    by_system = {
        "ACE": {"G08": {"Northbound": [{"route_id": "E"}]}},
        "G": {"G08": {"Southbound": [{"route_id": "G"}]}},
    }
    routes = ["E", "F", "FX", "M", "R"]
    assert cache._contributing_systems(entry, by_system, "G08", routes) == [
        "ACE",
        "BDFM",
        "G",
        "NQRW",
    ]
    assert list(cache._contributing_freshness(entry, by_system, "G08", routes)) == [
        "ACE",
        "BDFM",
        "G",
        "NQRW",
    ]
    # Rule 3 over the wider set: BDFM has never decoded, so neither board clock is spoken
    # for, although every row here is dated.
    assert cache._oldest_contributing_fetched_at(entry, by_system, "G08", routes) is None
    assert cache._oldest_contributing_content_at(entry, by_system, "G08", routes) is None
    # With BDFM dated, the worst contributor answers, NQRW included with no row here.
    entry["systems"]["BDFM"] = _block(970.0, 960.0)
    assert cache._oldest_contributing_fetched_at(entry, by_system, "G08", routes) == 970.0
    assert cache._oldest_contributing_content_at(entry, by_system, "G08", routes) == 960.0
    # Expected alone would drop G's rows from the vote; seen alone would drop BDFM and NQRW.
    assert cache._contributing_systems(entry, {}, "G08", routes) == ["ACE", "BDFM", "NQRW"]
    assert cache._contributing_systems(entry, by_system, "G08", []) == ["ACE", "G"]


def test_only_a_station_with_no_contributor_reads_the_aggregate_clock():
    """The aggregate fallback is kept for a station where nothing takes part, and since Q1
    no station of the live index is one: every one of the committed census's 496 stations
    expects at least one group, S01, S03 and S04 (served by FS alone) expecting BDFM. A
    station whose every contributor has never decoded reads null, not the aggregate."""
    census = json.loads(test_f03_boards._CENSUS.read_text())
    assert len(census) == 496
    assert all(cache._expected_groups(station["routes"]) for station in census)
    routes = {station["id"]: station["routes"] for station in census}
    assert [cache._expected_groups(routes[s]) for s in ("S01", "S03", "S04")] == [{"BDFM"}] * 3
    entry = {"fetched_at": 1000.0, "systems": {"1-7+S": _block(None, None, ok=False)}}
    assert cache._oldest_contributing_fetched_at(entry, {}, "X", []) == 1000.0
    assert cache._oldest_contributing_fetched_at(entry, {}, "219", ["2", "5"]) is None
    assert cache._contributing_freshness(entry, {}, "X", []) is None
