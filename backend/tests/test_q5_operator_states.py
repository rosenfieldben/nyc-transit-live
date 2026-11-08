"""Q5 of the freshness contract's Amendment 1: the operator's code in the four states.

observations-qualified, on /healthz, used to be silent for a system that never decoded in
the process (its first KNOWN BLIND SPOT) and for PATH, the ferry and NJ Transit failing with
nothing to show (its second). Q5 answered yes: fresh and empty says nothing, stale is the
row rule at the 600 s band, and failed and never decoded are named from the block's `ok`
and the time since the last decode or, never decoded, since the process first tried, at
the same band (routes/status.py _dark_past_the_operator_band). The first-attempt clock is
written once, beside the blocks, and never reset (pollers._note_first_attempt), because a
block's retained_since starts over after the retention cap (the 2026-10-12 record's H4). A
family that is not configured is left out. And NJ Transit's poll health, written on every
poll and served nowhere (H5), is served on /api/status as njt_feeds.

The pure clauses are in test_health_observations.py. Here each family is its real
refresher on a frozen clock, read through the ASGI app's /healthz and /api/status.
"""

from __future__ import annotations

import inspect

import httpx
import pytest
import test_a03_expected_contributors as a03
import test_q9_departed_rows as q9
from test_f03_boards import NOW

import main as app_module
import njt_auth
import pollers

pytestmark = pytest.mark.anyio

T = q9.T
QUALIFIED = "observations-qualified"


async def _read(path: str) -> dict:
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://q5") as http:
        return (await http.get(path)).json()


@pytest.fixture
def subway_world(monkeypatch):
    """A03's world (F03's: the committed 1-7+S capture, station 219, a poll every 20 s)."""
    return inspect.unwrap(a03.world)(monkeypatch)


@pytest.fixture
def world(monkeypatch):
    """Q9's world: PATH, the ferry and NJ Transit ready to poll, on one frozen clock."""
    return inspect.unwrap(q9.world)(monkeypatch)


async def test_a_group_dark_since_the_process_started_is_named_on_every_poll_past_the_band(
    subway_world,
):
    """THE FIRST BLIND SPOT, CLOSED, and H4 kept out of it. 1-7+S answers 503 from the
    process's first poll (A03's world B) while seven groups decode empty feeds, polled every
    20 s for 1300 s. Its block's fetched_at stays null, and its retained_since opens at the
    first failure and STARTS OVER once the cap has run (H4), so a rule aging it by that clock
    would go quiet on the poll after the cap. Aged by the first attempt (NOW), the code is
    off through NOW+600, on the band, and on at every poll from NOW+620 to NOW+1300."""
    state = app_module.app.state
    state.feed_cache = {"subways": app_module._fresh_entry()}
    state.subway_arrivals = {}
    state.subway_arrivals_by_system = {}
    named, retained = {}, set()
    for step in range(66):
        at = NOW + a03.POLL_S * step
        board = await a03._poll(subway_world, at, None, True)
        retained.add(board["systems"][a03.FAILED]["retained_since"])
        named[int(at - NOW)] = QUALIFIED in (await _read("/healthz"))["degraded"]
    assert state.feed_cache["subways"]["first_attempt_at"] == NOW
    assert board["systems"][a03.FAILED]["fetched_at"] is None
    assert len(retained) > 1, "retained_since never started over in the run, so H4 was not tried"
    assert [age for age, on in named.items() if on] == list(range(620, 1301, 20))


@pytest.mark.parametrize("family", ["path", "ferry", "njt"])
async def test_a_single_system_family_that_never_decoded_is_named_past_the_band(
    world, monkeypatch, family
):
    """THE SECOND BLIND SPOT, CLOSED. PATH, the ferry and NJ Transit failing from the
    process's first poll serve no row and none can be dropped, so before Q5 nothing named
    them; now each is named once the first attempt (T) is more than 600 s old, and its
    feed health on /api/status names it failed."""
    if family == "path":

        async def down(client, stops):
            raise httpx.ConnectError("bridge down")

        fetch, refresh, health = "fetch_path_trains", pollers._refresh_path, "path_feeds"
    elif family == "ferry":

        async def down(client, static):
            raise httpx.ConnectError("ferry down")

        fetch, refresh, health = "fetch_ferry_data", pollers._refresh_ferry, "ferry_feeds"
    else:

        async def down(stops, trips):
            raise njt_auth.NjtUpstreamError("NJ Transit returned HTTP 503: (empty body)")

        fetch, refresh, health = "fetch_njt_trains", pollers._refresh_njt, "njt_feeds"
    monkeypatch.setattr(app_module, fetch, down)
    codes = {}
    for at in (T, T + 300, T + 600, T + 620, T + 3600):
        world[0] = at
        await refresh(app_module.app, None)
        codes[int(at - T)] = QUALIFIED in (await _read("/healthz"))["degraded"]
    assert codes == {0: False, 300: False, 600: False, 620: True, 3600: True}
    assert (await _read("/api/status"))[health]["ok"] == 0


async def test_nj_transit_not_configured_is_never_named_and_reports_no_poll(world, monkeypatch):
    """THE NOT-CONFIGURED EXCLUSION. With no credentials NJ Transit never polls: no
    first attempt is written, no block, no feed health, and /healthz never names it, an
    hour in or a day in. /api/status says why in njt_static, and njt_feeds is null rather
    than a failure, since a deployment that is not configured never wears "failed"."""
    # Assigned, not patched: the world fixture patched this name, and a second patch of one
    # app.state name on one MonkeyPatch raises at the undo under pytest 9.2 (PR #130).
    app_module.app.state.njt_static_status = "not-configured"
    for at in (T, T + 3600, T + 86_400):
        world[0] = at
        await pollers._refresh_njt(app_module.app, None)
        assert QUALIFIED not in (await _read("/healthz"))["degraded"]
    entry = app_module.app.state.feed_cache["njt"]
    assert "first_attempt_at" not in entry
    assert entry.get("systems") is None
    status = await _read("/api/status")
    assert (status["njt_static"], status["njt_feeds"]) == ("not-configured", None)


async def test_nj_transit_feed_health_is_served(world, monkeypatch):
    """H5, CLOSED. app.state.njt_feed_health was written on every NJ Transit poll and
    served nowhere, while two comments said /api/status carried it. It does now, as
    njt_feeds beside path_feeds and ferry_feeds: the cross-check count on a decode, and
    the failed system on a failed poll."""
    assert (await _read("/api/status"))["njt_feeds"] is None

    async def healthy(stops, trips):
        return [], {}, q9.OBSERVED, ["one disagreement", "and another"]

    async def down(stops, trips):
        raise njt_auth.NjtUpstreamError("NJ Transit returned HTTP 503: (empty body)")

    monkeypatch.setattr(app_module, "fetch_njt_trains", healthy)
    await pollers._refresh_njt(app_module.app, None)
    assert (await _read("/api/status"))["njt_feeds"] == {
        "total": 1,
        "ok": 1,
        "failed": [],
        "cross_check_failures": 2,
    }
    monkeypatch.setattr(app_module, "fetch_njt_trains", down)
    world[0] = T + 20
    await pollers._refresh_njt(app_module.app, None)
    assert (await _read("/api/status"))["njt_feeds"] == {
        "total": 1,
        "ok": 0,
        "failed": ["njt"],
        "cross_check_failures": 0,
    }


async def test_the_first_attempt_is_written_once_and_never_by_a_poll_that_did_not_run(
    world, monkeypatch
):
    """The clock's two rules at the refresher: a poll held back by a static group still
    loading writes nothing (it tried nothing), and once written the clock is never moved,
    by a success, a failure or the passing of the cap."""
    # Assigned, not patched, for the reason the NJ Transit test above gives.
    stops = app_module.app.state.path_stops
    app_module.app.state.path_stops = {}
    world[0] = T
    await pollers._refresh_path(app_module.app, None)
    assert "first_attempt_at" not in app_module.app.state.feed_cache["path"]
    app_module.app.state.path_stops = stops

    async def healthy(client, stops):
        return [], {}, q9.OBSERVED, 0

    async def down(client, stops):
        raise httpx.ConnectError("bridge down")

    for at, fetch in ((T + 20, down), (T + 40, healthy), (T + 2000, down)):
        world[0] = at
        monkeypatch.setattr(app_module, "fetch_path_trains", fetch)
        await pollers._refresh_path(app_module.app, None)
        assert app_module.app.state.feed_cache["path"]["first_attempt_at"] == T + 20
