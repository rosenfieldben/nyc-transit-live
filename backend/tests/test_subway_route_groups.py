"""The route-to-group table (Amendment 1 to the freshness contract, Q1), held to its evidence.

feeds.SUBWAY_ROUTE_GROUPS names which group's feed carries each subway route, so a station
board can name the groups it EXPECTS rather than only the ones with a row there now. The
table is written from Step 0's capture (fixtures/subway_groups/capture.json), and the first
tests here are what stop the two drifting apart: the table IS the capture's placement, row
for row, with the capture's one route in two groups (D) resolved where the capture's README
resolves it and its one route outside the archive (SS) marked.

The last tests drive the runtime check Q1 asks for, "every route a live group serves is in
the table", through the real subway refresher and the served /healthz and /api/status.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest
import test_f03_boards
from google.transit import gtfs_realtime_pb2 as pb
from test_f03_boards import NOW, _quiet_feed, _Upstream

import cache
import main as app_module
import models
import pollers
from feeds.subway import (
    SUBWAY_FEED_URLS,
    SUBWAY_ROUTE_GROUPS,
    SUBWAY_ROUTES_ABSENT_FROM_ARCHIVE,
    routes_outside_the_table,
)

pytestmark = pytest.mark.anyio

_CAPTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "subway_groups" / "capture.json").read_text()
)
# The /api/subway-stops payload the live archive's real loader derived (496 stations, each
# with its routes): the routes-per-station index the expected groups are read through.
_CENSUS = json.loads(
    (
        Path(__file__).parents[2] / "tests" / "e2e" / "fixtures" / "subway_stops_real.json"
    ).read_text()
)


def test_the_table_is_the_captures_placement_row_for_row():
    """Every route the capture saw, under the one group the capture saw it on, and D under
    BDFM, where the capture saw it in every BDFM sample and in one NQRW sample. 30 rows: the
    archive's 29 and SS."""
    routes = _CAPTURE["routes"]
    single = {
        route: next(iter(seen["groups"]))
        for route, seen in routes.items()
        if len(seen["groups"]) == 1
    }
    assert SUBWAY_ROUTE_GROUPS == {**single, "D": "BDFM"}
    assert len(SUBWAY_ROUTE_GROUPS) == 30
    # D, the exception: every BDFM sample, and one NQRW sample with one trip in it.
    d_groups = routes["D"]["groups"]
    assert sorted(d_groups) == ["BDFM", "NQRW"]
    assert (d_groups["BDFM"]["samples"], d_groups["NQRW"]["samples"]) == (246, 1)
    assert (d_groups["NQRW"]["trip_updates"], d_groups["NQRW"]["vehicles"]) == (1, 1)
    assert _CAPTURE["placement"]["in_more_than_one_group"] == {"D": ["BDFM", "NQRW"]}
    # The capture's own placement is the table less its two resolved rows.
    assert _CAPTURE["placement"]["placed"] == {
        route: group for route, group in SUBWAY_ROUTE_GROUPS.items() if route not in {"D", "SS"}
    }
    # SS is the one row the archive does not list, and it is marked.
    assert SUBWAY_ROUTES_ABSENT_FROM_ARCHIVE == frozenset({"SS"})
    assert {route for route, seen in routes.items() if not seen["in_archive"]} == {"SS"}
    assert _CAPTURE["placement"]["seen_not_in_archive"] == ["SS"]
    assert SUBWAY_ROUTE_GROUPS["SS"] == "SIR"


def test_every_route_in_the_live_archive_and_the_census_is_in_the_table():
    """The other direction: the live archive's routes.txt (its 29 ids, recorded in the
    capture with the file's sha256) and the routes-per-station index of the committed census
    each name only routes the table places, so no station expects a route the table cannot
    turn into a group. Every group the table names is a feed group, and every feed group
    carries a route."""
    archive = _CAPTURE["archive"]["route_ids"]
    assert len(archive) == 29
    assert set(archive) <= set(SUBWAY_ROUTE_GROUPS)
    assert set(SUBWAY_ROUTE_GROUPS) - set(archive) == {"SS"}
    census_routes = {route for station in _CENSUS for route in station["routes"]}
    assert len(_CENSUS) == 496
    assert census_routes == set(archive)
    assert set(SUBWAY_ROUTE_GROUPS.values()) == set(SUBWAY_FEED_URLS)


def test_the_check_is_route_in_table_not_route_under_this_group():
    """What the runtime check reports: a route id the table has never heard of, with the
    group that served it, from placed trains or from arrival rows alone. A D trip on the
    NQRW feed is not reported (D has a row), nor SS on SIR (in the table, absent from the
    archive), nor a row with no route id."""
    trains = {
        "NQRW": [{"route_id": "D"}, {"route_id": "X9"}, {"route_id": None}],
        "SIR": [{"route_id": "SS"}],
    }
    arrivals = {
        "ACE": {"A02": {"Northbound": [{"route_id": "Y1"}, {"route_id": "E"}]}},
        "NQRW": {"R01": {"Southbound": [{"route_id": "N"}]}},
    }
    # X9 only among the placed trains, Y1 only among the arrival rows: each input is read.
    assert routes_outside_the_table(trains, arrivals) == {("X9", "NQRW"), ("Y1", "ACE")}
    assert routes_outside_the_table({"NQRW": [{"route_id": "D"}]}, {}) == set()
    assert routes_outside_the_table({"SIR": [{"route_id": "SS"}]}, {}) == set()


@pytest.fixture
def world(monkeypatch):
    """F03's world (test_f03_boards.f03_world): the app.state a lifespan builds, isolated by
    monkeypatch, and the patched clock, handed back as a one-element list."""
    return inspect.unwrap(test_f03_boards.f03_world)(monkeypatch)


def _feed(at: float, trips: list[tuple[str, str, str]], start_time: str = "") -> bytes:
    """A dated group feed whose trips each call once at a 219 platform two minutes on, so
    each reaches the arrivals index with its route id. `start_time` (New York, on the
    world's day) makes every trip one not yet started, which the decoder indexes for
    arrivals and does not place."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "1.0"
    feed.header.timestamp = int(at)
    for trip_id, route_id, stop_id in trips:
        entity = feed.entity.add()
        entity.id = trip_id
        entity.trip_update.trip.trip_id = trip_id
        entity.trip_update.trip.route_id = route_id
        if start_time:
            entity.trip_update.trip.start_date = "20260702"
            entity.trip_update.trip.start_time = start_time
        call = entity.trip_update.stop_time_update.add()
        call.stop_id = stop_id
        call.arrival.time = int(at + 120)
    return feed.SerializeToString()


def _upstream(at: float, carried: dict[str, bytes]) -> _Upstream:
    bodies = {url: _quiet_feed(at) for url in SUBWAY_FEED_URLS.values()}
    for group, body in carried.items():
        bodies[SUBWAY_FEED_URLS[group]] = body
    return _Upstream(bodies)


async def _probe() -> tuple[httpx.Response, dict]:
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://q1") as http:
        return await http.get("/healthz"), (await http.get("/api/status")).json()


async def test_a_route_outside_the_table_is_named_and_held_for_the_process(world):
    """Through the real refresher: NQRW serves an X9 trip, which the table lacks. /healthz
    raises subway-route-unmapped and still answers 200 (never gating); /api/status names X9
    and the group that served it. The next poll serves no X9, and the code holds: the table
    is code, so only a deploy clears it."""
    clock = world
    await pollers._refresh_subways(
        app_module.app, _upstream(NOW, {"NQRW": _feed(NOW, [("x9-1", "X9", "219N")])})
    )
    health, status = await _probe()
    assert health.status_code == 200
    assert models.HEALTH_SUBWAY_ROUTE_UNMAPPED in health.json()["degraded"]
    assert status["subway_unmapped_routes"] == {"X9": ["NQRW"]}

    clock[0] = NOW + 20
    await pollers._refresh_subways(app_module.app, _upstream(NOW + 20, {}))
    health, status = await _probe()
    assert models.HEALTH_SUBWAY_ROUTE_UNMAPPED in health.json()["degraded"]
    assert status["subway_unmapped_routes"] == {"X9": ["NQRW"]}


async def test_ss_and_d_on_nqrw_raise_nothing(world):
    """SS rides SIR and is absent from the archive's routes.txt; it is in the table, so the
    in-table-but-absent-from-the-archive path raises nothing. A D trip on the NQRW feed
    passes too: the check is route in table, not route under this group."""
    await pollers._refresh_subways(
        app_module.app,
        _upstream(
            NOW,
            {
                "SIR": _feed(NOW, [("ss-1", "SS", "219S")]),
                "NQRW": _feed(NOW, [("d-1", "D", "219N")]),
            },
        ),
    )
    health, status = await _probe()
    assert models.HEALTH_SUBWAY_ROUTE_UNMAPPED not in health.json()["degraded"]
    assert status["subway_unmapped_routes"] == {}


async def test_a_trip_not_yet_started_latches_its_route_and_two_groups_merge(world):
    """The arrivals-only path the check reads: X9 on NQRW is a trip that starts at 09:00
    New York, an hour after the world's clock (08:00), so the decoder indexes it for
    arrivals and does not place it. ACE serves X9 too, placed. The latch names both groups."""
    await pollers._refresh_subways(
        app_module.app,
        _upstream(
            NOW,
            {
                "NQRW": _feed(NOW, [("x9-later", "X9", "219N")], start_time="09:00:00"),
                "ACE": _feed(NOW, [("x9-now", "X9", "219S")]),
            },
        ),
    )
    placed = app_module.app.state.feed_cache["subways"]["data"]
    assert [train["trip_id"] for train in placed if train.get("route_id") == "X9"] == ["x9-now"]
    _, status = await _probe()
    assert status["subway_unmapped_routes"] == {"X9": ["ACE", "NQRW"]}


async def test_a_static_route_outside_the_table_is_named_and_expects_nothing(world):
    """THE SIDE MEMBERSHIP READS (this branch's adversarial round, its F2). The static
    routes-per-station index names X9 at 219 beside 2 and 5, and no feed serves it: /healthz
    raises subway-route-unmapped, /api/status names X9 as listed in the static index, and
    219's board still expects 1-7+S through 2 and 5 and answers 200, X9 expecting nothing.
    When a feed serves X9 as well, both sources are named."""
    app_module.app.state.subway_station_routes["219"] = ["2", "5", "X9"]
    await pollers._refresh_subways(app_module.app, _upstream(NOW, {}))
    health, status = await _probe()
    assert health.status_code == 200
    assert models.HEALTH_SUBWAY_ROUTE_UNMAPPED in health.json()["degraded"]
    assert status["subway_unmapped_routes"] == {"X9": ["static"]}
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://q1") as http:
        board = await http.get("/api/subway-arrivals/219")
    assert board.status_code == 200
    assert sorted(board.json()["systems"]) == ["1-7+S"]
    assert cache._expected_groups(["2", "5", "X9"]) == {"1-7+S"}

    world[0] = NOW + 20
    await pollers._refresh_subways(
        app_module.app, _upstream(NOW + 20, {"NQRW": _feed(NOW + 20, [("x9-1", "X9", "219N")])})
    )
    _, status = await _probe()
    assert status["subway_unmapped_routes"] == {"X9": ["NQRW", "static"]}
