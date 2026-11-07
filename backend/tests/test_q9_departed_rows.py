"""Q9 of the freshness contract's Amendment 1: a kept row is pruned once its time has passed.

PATH, NJ Transit and the ferry keep their last decode on every failed poll, and the
railroads keep theirs on a TOTAL failure, which returns before any merge. None of them
pruned, so a departed train was served as `reported`, counting down to "now", for as long
as an outage lasted (A1.2: an NJ Transit row read "as of 4h ago departs now" at T0+14400 s).
Q9 answered yes: each prunes through drop_expired_arrivals' own rule with its zero grace
(feeds.row_departed), retention unchanged and nothing undeparted dropped.

Each family's world here is its real refresher, its upstream fetch replaced by a success
and then a failure, and its real board served through the ASGI app, on one frozen clock: a
successful poll at T holds a row due at T+30 and one due at T+300 (and, where the family's
rows carry a departure, a train dwelling from T+10 to T+120); the failed poll at T+20 keeps
all of them; the failed poll at T+40 drops exactly the one whose time has passed.
"""

from __future__ import annotations

import httpx
import pytest

import main as app_module
import njt_auth
import pollers
from feeds import row_departed

pytestmark = pytest.mark.anyio

T = 1000.0
OBSERVED = 995.0


@pytest.fixture
def world(monkeypatch):
    """The app.state the four boards and their refreshers read, isolated by monkeypatch,
    and the frozen clock, handed back as a one-element list."""
    state = app_module.app.state
    primed = {
        "feed_cache": {
            name: app_module._fresh_entry()
            for name in ("buses", "subways", "railroads", "path", "ferry", "njt")
        },
        "path_stops": {"26733": {"id": "26733", "name": "Newark", "lat": 40.73, "lon": -74.16}},
        "path_arrivals": {},
        "path_identity": app_module.new_path_identity_state("t"),
        "path_station_order": {},
        "path_feed_health": None,
        "ferry_static_status": "ready",
        "ferry_static": {},
        "ferry_stops": {"18": {"id": "18", "name": "East 34th Street", "lat": 40.7, "lon": -73.9}},
        "ferry_arrivals": {},
        "ferry_feed_health": None,
        "njt_static_status": "ready",
        "njt_stops": {"105": {"name": "New York Penn Station", "lat": 40.75, "lon": -73.99}},
        "njt_trips": {},
        "njt_arrivals": {},
        "njt_feed_health": None,
        "railroad_stops": {
            "LIRR": {"12": {"name": "Jamaica", "lat": 40.7, "lon": -73.8}},
            "MNR": {"1": {"name": "Grand Central", "lat": 40.75, "lon": -73.97}},
        },
        "railroad_arrivals": {},
        "railroad_positions": {},
        "railroad_feed_health": None,
        "subway_arrivals": {},
        "subway_arrivals_by_system": {},
        "subway_positions": {},
        "subway_feed_health": None,
        "subway_unmapped_routes": {},
        "subway_stops": None,
    }
    for name, value in primed.items():
        monkeypatch.setattr(state, name, value, raising=False)
    clock = [T]
    monkeypatch.setattr(app_module.time, "time", lambda: clock[0])
    return clock


async def _board(path: str) -> dict:
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://q9") as http:
        response = await http.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def _row(trip: str, arrival: float, departure: float | None = None, **more) -> dict:
    """A kept row as its family's decoder writes it: a departure only where the family's
    rows carry one (NJ Transit and the ferry)."""
    row = {"trip_id": trip, "route_id": "R", "arrival": arrival, **more}
    if departure is not None:
        row["departure"] = departure
    return {**row, "observed_at": OBSERVED, "provenance": "reported"}


def test_the_rule_reads_the_later_of_arrival_and_departure_with_no_grace():
    """A row has departed when the later of its two times is before now: a train dwelling
    with its departure ahead has not, a row with no time is kept, and a row due exactly now
    is kept (drop_expired_arrivals' `>= now`, its zero grace)."""
    assert row_departed({"arrival": 999.0}, T) is True
    assert row_departed({"arrival": 1000.0}, T) is False
    assert row_departed({"arrival": 990.0, "departure": 1100.0}, T) is False
    assert row_departed({"arrival": None, "departure": 999.0}, T) is True
    assert row_departed({"arrival": None, "departure": None}, T) is False
    assert row_departed({"route_id": "R"}, T) is False


async def test_path_prunes_a_departed_kept_row_on_a_failed_poll(world, monkeypatch):
    """PATH, every failure total: the row due at T+30 is served through the failed poll at
    T+20 and gone at T+40; the row due at T+300 stays, still `reported`."""
    soon = {"route_id": "862", "arrival": T + 30, "observed_at": OBSERVED, "provenance": "reported"}
    later = {**soon, "arrival": T + 300}

    async def healthy(client, stops):
        return [], {"26733": {"To New Jersey": [soon, later]}}, OBSERVED, 0

    async def down(client, stops):
        raise httpx.ConnectError("bridge down")

    monkeypatch.setattr(app_module, "fetch_path_trains", healthy)
    await pollers._refresh_path(app_module.app, None)
    monkeypatch.setattr(app_module, "fetch_path_trains", down)
    world[0] = T + 20
    await pollers._refresh_path(app_module.app, None)
    board = await _board("/api/path-arrivals/26733")
    assert [r["arrival"] for r in board["directions"]["To New Jersey"]] == [T + 30, T + 300]
    world[0] = T + 40
    await pollers._refresh_path(app_module.app, None)
    board = await _board("/api/path-arrivals/26733")
    assert board["directions"] == {"To New Jersey": [later]}


async def test_the_ferry_prunes_by_departure_and_keeps_a_dwelling_boat(world, monkeypatch):
    """The ferry's rows carry both times. At T+40 the boat due at T+30 is gone, the boat
    that arrived at T+10 and leaves at T+120 is still at the dock and stays, and the one due
    at T+300 stays."""
    soon = _row("soon", T + 30, T + 30)
    dwell = _row("dwell", T + 10, T + 120)
    later = _row("later", T + 300, T + 330)

    async def healthy(client, static):
        return [], {"18": {"East River": [dwell, soon, later]}}, OBSERVED

    async def down(client, static):
        raise httpx.ConnectError("ferry host down")

    monkeypatch.setattr(app_module, "fetch_ferry_data", healthy)
    await pollers._refresh_ferry(app_module.app, None)
    monkeypatch.setattr(app_module, "fetch_ferry_data", down)
    world[0] = T + 20
    await pollers._refresh_ferry(app_module.app, None)
    assert len((await _board("/api/ferry-arrivals/18"))["routes"]["East River"]) == 3
    world[0] = T + 40
    await pollers._refresh_ferry(app_module.app, None)
    board = await _board("/api/ferry-arrivals/18")
    assert [r["trip_id"] for r in board["routes"]["East River"]] == ["dwell", "later"]
    assert {r["provenance"] for r in board["routes"]["East River"]} == {"reported"}


async def test_nj_transit_prunes_a_departed_kept_row_on_a_failed_poll(world, monkeypatch):
    """NJ Transit's flat board: at T+40 the train that left at T+30 is gone, the train
    standing at Penn until T+120 and the one at T+300 stay."""
    extra = {"train_num": "3881", "headsign": "Trenton", "delay": 0}
    soon = _row("soon", T + 25, T + 30, **extra)
    dwell = _row("dwell", T + 10, T + 120, **extra)
    later = _row("later", T + 295, T + 300, **extra)

    async def healthy(stops, trips):
        return [], {"105": [dwell, soon, later]}, OBSERVED, []

    async def down(stops, trips):
        raise njt_auth.NjtUpstreamError("NJ Transit returned HTTP 503: (empty body)")

    monkeypatch.setattr(app_module, "fetch_njt_trains", healthy)
    await pollers._refresh_njt(app_module.app, None)
    monkeypatch.setattr(app_module, "fetch_njt_trains", down)
    world[0] = T + 20
    await pollers._refresh_njt(app_module.app, None)
    assert len((await _board("/api/njt-arrivals/105"))["arrivals"]) == 3
    world[0] = T + 40
    await pollers._refresh_njt(app_module.app, None)
    board = await _board("/api/njt-arrivals/105")
    assert [r["trip_id"] for r in board["arrivals"]] == ["dwell", "later"]


async def _railroads_healthy(client, stops):
    lirr = [_row("l-soon", T + 30, train_num="1"), _row("l-later", T + 300, train_num="2")]
    mnr = [_row("m-soon", T + 30, train_num="3")]
    return (
        [],
        {"LIRR": {"12": {"Outbound": lirr}}, "MNR": {"1": {"Trains": mnr}}},
        OBSERVED,
        [],
        {"LIRR": OBSERVED, "MNR": None},
        {},
    )


async def test_the_railroads_prune_on_a_total_failure_and_keep_each_systems_key(world, monkeypatch):
    """Both railroads failing returns before any merge, so drop_expired_arrivals never ran
    there. At T+40 LIRR's departed row is gone and its later one stays; Metro-North's only
    row departed, and its key stays, emptied: an absent key means "the merge removed it" to
    /healthz's dropped rung."""

    async def down(client, stops):
        raise RuntimeError("All railroad feeds failed: LIRR: 503; MNR: 503")

    monkeypatch.setattr(app_module, "fetch_railroad_trains", _railroads_healthy)
    await pollers._refresh_railroads(app_module.app, None)
    monkeypatch.setattr(app_module, "fetch_railroad_trains", down)
    world[0] = T + 20
    await pollers._refresh_railroads(app_module.app, None)
    board = await _board("/api/railroad-arrivals/LIRR/12")
    assert [r["trip_id"] for r in board["directions"]["Outbound"]] == ["l-soon", "l-later"]
    world[0] = T + 40
    await pollers._refresh_railroads(app_module.app, None)
    board = await _board("/api/railroad-arrivals/LIRR/12")
    assert [r["trip_id"] for r in board["directions"]["Outbound"]] == ["l-later"]
    assert {r["provenance"] for r in board["directions"]["Outbound"]} == {"reported"}
    assert app_module.app.state.railroad_arrivals["MNR"] == {}


async def test_a_deadline_or_an_unclassified_failure_prunes_the_same_way(world, monkeypatch):
    """A refresh killed by the deadline, or failing in a way no handler names, never
    reaches the refresher's own failure arms; _feed_degrader marks it down, and prunes the
    same rows. Driven through _bounded_refresh with a wedged upstream on NJ Transit, the
    source the deadline reaches most."""

    async def healthy(stops, trips):
        return [], {"105": [_row("soon", T + 25, T + 30), _row("later", T + 295, T + 300)]}, 0, []

    monkeypatch.setattr(app_module, "fetch_njt_trains", healthy)
    await pollers._refresh_njt(app_module.app, None)
    entry = app_module.app.state.feed_cache["njt"]
    monkeypatch.setattr(pollers, "REFRESH_DEADLINE_S", 0.01)
    world[0] = T + 40

    async def wedged():
        await pollers.asyncio.Event().wait()

    await pollers._bounded_refresh(
        entry, wedged(), pollers._feed_degrader(app_module.app, "njt", entry)
    )
    assert entry["error"]["status"] == 504
    assert [r["trip_id"] for r in app_module.app.state.njt_arrivals["105"]] == ["later"]


async def test_a_retaining_familys_paths_are_untouched(world, monkeypatch):
    """Q9 named four families. A railroad PARTIAL failure still retains the failed system's
    rows, marked `retained`, and prunes them by the rule it always had; and the subway's
    total failure, which Q9 does not name, keeps its last decode whole, a departed row
    included (raised for a ruling in this branch's pull request)."""

    async def lirr_down(client, stops):
        mnr = [_row("m-later", T + 300, train_num="3")]
        return [], {"MNR": {"1": {"Trains": mnr}}}, None, ["LIRR"], {"MNR": None}, {}

    monkeypatch.setattr(app_module, "fetch_railroad_trains", _railroads_healthy)
    await pollers._refresh_railroads(app_module.app, None)
    monkeypatch.setattr(app_module, "fetch_railroad_trains", lirr_down)
    world[0] = T + 40
    await pollers._refresh_railroads(app_module.app, None)
    lirr = app_module.app.state.railroad_arrivals["LIRR"]["12"]["Outbound"]
    assert [(r["trip_id"], r["provenance"]) for r in lirr] == [("l-later", "retained")]

    departed = {"Northbound": [{"route_id": "1", "trip_id": "s", "arrival": T - 60}]}
    app_module.app.state.subway_arrivals = {"101": departed}
    app_module.app.state.subway_arrivals_by_system = {"1-7+S": {"101": departed}}
    app_module.app.state.subway_stops = {
        "101N": {"name": "Van Cortlandt", "lat": 40.9, "lon": -73.9}
    }
    app_module.app.state.feed_cache["subways"].update(data=[], fetched_at=T - 60)

    async def all_down(stops, client):
        raise RuntimeError("All subway feeds failed: every group 503")

    monkeypatch.setattr(app_module, "fetch_subway_trains", all_down)
    await pollers._refresh_subways(app_module.app, None)
    assert app_module.app.state.subway_arrivals == {"101": departed}
    assert app_module.app.state.subway_arrivals_by_system == {"1-7+S": {"101": departed}}


async def test_a_total_outage_goes_quiet_on_healthz_once_its_last_kept_row_departs(
    world, monkeypatch
):
    """WHAT Q9 COSTS THE OPERATOR UNTIL STEP 3, pinned as decided. observations-qualified
    ages a non-retaining family's kept rows, and a system serving no row reads as quiet
    unless the retention cap dropped it (routes/status.py's _was_dropped), so the second
    KNOWN BLIND SPOT at models.HEALTH_OBSERVATIONS_QUALIFIED now covers a family whose last
    kept row has departed, as well as one that failed holding none. PATH fails from T+20
    holding one row due at T+700, dated T-5: the code is on at T+620 (the row 625 s old, past
    the 600 s band) and off at T+720, the row gone, where before Q9 it stayed on for the
    whole outage. Amendment 1's Q5, built in Step 3, names a failed system from its block
    and moves this test."""
    row = {"route_id": "862", "arrival": T + 700, "observed_at": T - 5, "provenance": "reported"}

    async def healthy(client, stops):
        return [], {"26733": {"To New Jersey": [row]}}, T - 5, 0

    async def down(client, stops):
        raise httpx.ConnectError("bridge down")

    monkeypatch.setattr(app_module, "fetch_path_trains", healthy)
    await pollers._refresh_path(app_module.app, None)
    monkeypatch.setattr(app_module, "fetch_path_trains", down)
    codes = {}
    for at in (T + 620, T + 720):
        world[0] = at
        await pollers._refresh_path(app_module.app, None)
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://q9") as http:
            codes[at - T] = (await http.get("/healthz")).json()["degraded"]
    assert "observations-qualified" in codes[620]
    assert "observations-qualified" not in codes[720]
    assert app_module.app.state.path_arrivals == {}
