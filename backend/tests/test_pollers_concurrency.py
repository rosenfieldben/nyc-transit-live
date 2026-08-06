"""Structured-concurrency tests for the two poll loops and the ferry pair (C4).

Hermetic and deterministic: no sleeps to wait things out, no wall-clock timing
assertions. Children are event-gated so a test can hold one open and observe what
the cycle does meanwhile, and the loop's own sleep is replaced by a counter, which
is what makes "the cycle did not proceed to its sleep" a fact rather than a guess.

WHAT THESE PIN, in one sentence: a generation must not overlap its successor.
asyncio.gather does NOT cancel or await its siblings when one child raises, so
before C4 a single unexpected error propagated out of the cycle, the loop logged
it and slept, and the surviving children kept running into the NEXT generation,
where their writes landed in app.state after a new cycle had already started.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from google.protobuf.message import DecodeError

import main as app_module
import pollers

pytestmark = pytest.mark.anyio


@pytest.fixture
def cache():
    app_module.app.state.feed_cache = {
        name: app_module._fresh_entry()
        # EVERY registry key, and it must stay that way: _poll_feeds reads
        # cache[name] for each registered source BEFORE the TaskGroup children
        # start, so a missing key raises in the cycle body rather than in a child.
        # The loop then logs and sleeps, the clock below turns that sleep into a
        # no-op, and the whole suite spins at full speed instead of failing. That
        # is what adding "njt" to the registry without this line did.
        for name in ("buses", "subways", "railroads", "path", "ferry", "njt")
    }
    return app_module.app.state.feed_cache


class _CycleClock:
    """Replaces the loop's inter-cycle sleep with an observable counter.

    The loop sleeps for exactly POLL_INTERVAL_S (or ALERT_POLL_INTERVAL_S) between
    cycles and nothing else in these tests sleeps for that long, so keying on the
    delay identifies the cycle boundary precisely. `cycles` is therefore "how many
    times the loop has finished a generation", which is the quantity every overlap
    assertion below is really about.
    """

    def __init__(self, interval: float):
        self.interval = interval
        self.cycles = 0
        self.pending_children: list[list[str]] = []
        self._real_sleep = asyncio.sleep

    async def sleep(self, delay, *args, **kwargs):
        if delay == self.interval:
            self.cycles += 1
            # Which CHILDREN are still alive at the cycle boundary. Filtered to the
            # cycle's own wrapper rather than "every task": all_tasks() also holds
            # the test's task and pytest's plumbing, which are not what this is
            # asking about. A non-empty list here means a child of the generation
            # that just ended is still running, which is the orphan.
            self.pending_children.append(
                sorted(
                    task.get_coro().__qualname__
                    for task in asyncio.all_tasks()
                    if not task.done()
                    and task.get_coro().__qualname__.startswith(
                        ("_total_refresh", "_bounded_refresh")
                    )
                )
            )
            delay = 0
        return await self._real_sleep(delay, *args, **kwargs)


async def _settle(turns: int = 25) -> None:
    """Yield the event loop enough times that anything runnable has run.

    Used to prove a NEGATIVE (the cycle has not advanced). A fixed number of turns
    with no real delay keeps that deterministic: if the loop were going to start a
    second generation, it needs only a few turns to do so.
    """
    for _ in range(turns):
        await asyncio.sleep(0)


async def test_c4_a_failing_child_does_not_orphan_its_slow_sibling(cache, monkeypatch):
    # THE NAMED OVERLAP REGRESSION, mirroring the audit's reproduction.
    #
    # Child A (buses) raises something nobody classified. Child B (subways) is slow.
    # Pre-C4: gather propagated A's error at once, the loop logged it and slept, and
    # B kept running detached, so B's write landed in the NEXT generation. Now the
    # cycle cannot reach its sleep until B has finished, so B's write is always in
    # its own generation and A's failure is recorded against A alone.
    clock = _CycleClock(pollers.POLL_INTERVAL_S)
    monkeypatch.setattr(pollers.asyncio, "sleep", clock.sleep)

    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    wrote_in_cycle: list[int] = []

    # BOTH CHILDREN MISBEHAVE ONLY IN THE FIRST GENERATION. Later generations run
    # clean, which keeps the failure mode of this test bounded: under the pre-C4
    # gather the loop would otherwise spin (the patched sleep returns instantly)
    # spawning a fresh detached child every cycle, and the storm buries the
    # assertion instead of reporting it.
    generations = {"buses": 0, "subways": 0}

    async def failing_buses(app, client):
        generations["buses"] += 1
        if generations["buses"] == 1:
            raise RuntimeError("a shape no handler names")

    async def slow_subways(app, client):
        generations["subways"] += 1
        if generations["subways"] > 1:
            return
        slow_started.set()
        await release_slow.wait()
        # The generation this write belongs to: 0 means "before any cycle boundary",
        # which is the only honest answer for work started in the first generation.
        wrote_in_cycle.append(clock.cycles)
        app.state.feed_cache["subways"]["data"] = ["a train"]

    async def idle(app, client):
        return None

    monkeypatch.setattr(pollers, "_refresh_buses", failing_buses)
    monkeypatch.setattr(pollers, "_refresh_subways", slow_subways)
    for name in ("_refresh_railroads", "_refresh_path", "_refresh_ferry"):
        monkeypatch.setattr(pollers, name, idle)

    loop_task = asyncio.create_task(pollers._poll_feeds(app_module.app))
    try:
        await asyncio.wait_for(slow_started.wait(), timeout=5)
        # A has already raised by now (it raises on its first step). The cycle must
        # NOT have moved on: this is the assertion that fails pre-C4.
        await _settle()
        assert cache["buses"]["error"] is not None  # A's failure recorded immediately
        assert clock.cycles == 0  # and the cycle is still waiting for B

        release_slow.set()
        await asyncio.wait_for(_wait_for(lambda: clock.cycles >= 1), timeout=5)
    finally:
        # Release before cancelling so teardown is clean even when the assertions
        # above FAIL. Under the pre-C4 gather this test does not just fail, it fails
        # while leaving a pile of detached children blocked on this event, which is
        # the defect itself; ungated they would hang the run instead of reporting it.
        release_slow.set()
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task
        await _settle()  # let any detached child finish before the loop closes

    # B's write landed in ITS OWN generation, not a later one. Only the first write
    # is asserted: the loop keeps cycling, so later generations write again (with
    # the gate already open), which is the loop working, not an overlap.
    assert wrote_in_cycle[0] == 0
    assert cache["subways"]["data"] == ["a train"]
    # A's failure is A's alone: the other four are untouched by it.
    assert cache["buses"]["error"]["status"] == 500
    assert "buses" in cache["buses"]["error"]["detail"]
    for name in ("subways", "railroads", "path", "ferry"):
        assert cache[name]["error"] is None
    # And no child survived into the next generation.
    assert clock.pending_children[0] == []


async def _wait_for(predicate, turns: int = 500) -> None:
    """Yield until `predicate` holds, then return; RAISE if it never does.

    It used to return silently on exhaustion, which made every caller's
    `await asyncio.wait_for(_wait_for(...))` assert nothing at all: a cycle that
    never completed looked identical to one that did.
    """
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError(f"condition never held after {turns} event-loop turns")


async def test_c4_shutdown_cancels_children_and_is_not_swallowed(cache, monkeypatch):
    # THE CLASSIC BUG IN CATCH-ALL CHILDREN. _total_refresh catches everything so a
    # child cannot break the cycle, which is exactly the shape that swallows a
    # cancellation and hangs shutdown forever. CancelledError must pass through, and
    # the child must actually observe it.
    clock = _CycleClock(pollers.POLL_INTERVAL_S)
    monkeypatch.setattr(pollers.asyncio, "sleep", clock.sleep)

    started = asyncio.Event()
    child_saw_cancel = asyncio.Event()

    async def blocks_forever(app, client):
        started.set()
        try:
            await asyncio.Event().wait()  # never set
        except asyncio.CancelledError:
            child_saw_cancel.set()
            raise

    async def idle(app, client):
        return None

    monkeypatch.setattr(pollers, "_refresh_buses", blocks_forever)
    for name in ("_refresh_subways", "_refresh_railroads", "_refresh_path", "_refresh_ferry"):
        monkeypatch.setattr(pollers, name, idle)

    loop_task = asyncio.create_task(pollers._poll_feeds(app_module.app))
    await asyncio.wait_for(started.wait(), timeout=5)
    loop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop_task

    assert child_saw_cancel.is_set()  # the cancellation reached the child
    assert loop_task.cancelled()  # and was not swallowed on the way out
    assert clock.cycles == 0  # cancelled mid-cycle, never reached the sleep
    # The wrapper must not have recorded a cancellation as this system's failure:
    # shutdown is not an upstream problem.
    assert cache["buses"]["error"] is None


async def test_c4_alerts_cycle_joins_its_child_and_survives_its_failure(monkeypatch):
    # The alerts loop's outer layer, same shape at its own scale. It has ONE child,
    # so the await is already the join; what C4 adds is the child's totality. Before,
    # an unexpected error escaped into the cycle handler and was logged with the
    # alerts cache left reporting itself healthy.
    app_module.app.state.alerts_cache = app_module._fresh_alerts_entry()
    entry = app_module.app.state.alerts_cache
    clock = _CycleClock(pollers.ALERT_POLL_INTERVAL_S)
    monkeypatch.setattr(pollers.asyncio, "sleep", clock.sleep)

    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    async def slow_then_failing(app, client):
        calls.append(clock.cycles)
        started.set()
        await release.wait()
        raise RuntimeError("a shape no handler names")

    monkeypatch.setattr(pollers, "_refresh_alerts", slow_then_failing)

    loop_task = asyncio.create_task(pollers._poll_alerts(app_module.app))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await _settle()
        assert clock.cycles == 0  # the cycle waits for its child
        release.set()
        await asyncio.wait_for(_wait_for(lambda: clock.cycles >= 1), timeout=5)
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    # The failure is recorded against the alerts cache rather than vanishing into a
    # log line, and the loop kept running.
    assert entry["error"]["status"] == 500
    assert "alerts" in entry["error"]["detail"]
    assert calls[0] == 0
    # No pending-children assertion here, deliberately: _poll_alerts creates no tasks
    # (its single child is awaited directly), so such a check could never fail and
    # would only look like coverage. The join it has is the await itself, which is
    # what `clock.cycles == 0` above proves.


# ---------------- the ferry pair ----------------


class _ResponseStub:
    def __init__(self, status: int):
        self.status = status
        self.content = b""

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError(
                f"Server error '{self.status} Internal Server Error' for url "
                "'https://nycferry.example/gtfsrealtime.aspx?type=vehicleposition'",
                request=None,
                response=None,
            )


class _PairClient:
    """One endpoint 500s; the other hangs until released. Records which endpoint
    calls started and which returned, so a leaked sibling is visible."""

    def __init__(self, failing: str, hanging: str):
        self.failing = failing
        self.hanging = hanging
        self.started: list[str] = []
        self.completed: list[str] = []
        self.cancelled: list[str] = []
        self.release = asyncio.Event()

    async def get(self, url, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        self.started.append(endpoint)
        if endpoint == self.failing:
            self.completed.append(endpoint)
            return _ResponseStub(500)
        try:
            await self.release.wait()  # the trickling sibling
        except asyncio.CancelledError:
            self.cancelled.append(endpoint)
            raise
        self.completed.append(endpoint)
        return _ResponseStub(200)


async def test_c4_ferry_pair_cancels_the_sibling_and_raises_one_cause():
    # THE ORPHAN THE AUDIT NAMED, at the ferry's own two-endpoint gather. One
    # endpoint 500s while the other trickles: gather propagated the 500 and left the
    # trickler running, still holding a connection, and the next poll started a
    # second pair beside it. The TaskGroup cancels the sibling and does not return
    # until it has stopped.
    from feeds.ferry import FERRY_TRIPUPDATE_ENDPOINT, FERRY_VEHICLE_ENDPOINT, fetch_ferry_data

    client = _PairClient(failing=FERRY_VEHICLE_ENDPOINT, hanging=FERRY_TRIPUPDATE_ENDPOINT)

    with pytest.raises(httpx.HTTPError) as raised:
        # Bounded by the FAILURE, not by the hang: release is never set, so if the
        # sibling were merely left running this would hang until the timeout.
        await asyncio.wait_for(fetch_ferry_data(client, {}), timeout=5)

    # ONE cause, and it is the underlying HTTP error rather than a group.
    assert not isinstance(raised.value, BaseExceptionGroup)
    assert "500" in str(raised.value)
    assert "ExceptionGroup" not in repr(raised.value)
    # Both legs started, and the hanging one was CANCELLED rather than leaked.
    assert sorted(client.started) == sorted([FERRY_VEHICLE_ENDPOINT, FERRY_TRIPUPDATE_ENDPOINT])
    assert client.cancelled == [FERRY_TRIPUPDATE_ENDPOINT]
    assert FERRY_TRIPUPDATE_ENDPOINT not in client.completed


async def test_c4_ferry_pair_records_exactly_one_sanitized_failure(cache, monkeypatch):
    # End to end at the poller: the caller must record ONE failure, routed by the
    # cause's TYPE (so the existing httpx.HTTPError handler still catches it), with
    # the URL stripped from the detail. A group would miss every handler and publish
    # a repr listing the endpoints.
    from feeds.ferry import FERRY_TRIPUPDATE_ENDPOINT, FERRY_VEHICLE_ENDPOINT

    app_module.app.state.ferry_static_status = "ready"
    app_module.app.state.ferry_static = {"trips": {}, "routes": {}}
    cache["ferry"].update(data=[{"id": "H1"}], fetched_at=1.0, error=None)

    client = _PairClient(failing=FERRY_TRIPUPDATE_ENDPOINT, hanging=FERRY_VEHICLE_ENDPOINT)
    await asyncio.wait_for(pollers._refresh_ferry(app_module.app, client), timeout=5)

    error = cache["ferry"]["error"]
    assert error["status"] == 502  # the upstream handler, not the 500 catch-all
    assert "Upstream NYC Ferry feed error" in error["detail"]
    assert "500" in error["detail"]
    assert "nycferry.example" not in error["detail"]  # sanitized
    # The detail is ONE cause's message, not a summary of both legs. Asserting the
    # absence of the token "ExceptionGroup" would have been theatre: recorded details
    # are built from str(exc), where that token never appears even for a real group.
    # The endpoint names DO appear in a group's message, so their absence is the
    # assertion with teeth.
    assert FERRY_VEHICLE_ENDPOINT not in error["detail"]
    assert error["detail"].count("Server error") == 1
    assert cache["ferry"]["data"] == [{"id": "H1"}]  # last-known kept
    assert client.cancelled == [FERRY_VEHICLE_ENDPOINT]  # sibling cancelled, not leaked


class _NotAnException(BaseException):
    """Stands in for SystemExit / KeyboardInterrupt: outside the Exception hierarchy,
    so _total_refresh deliberately does not catch it."""


async def test_c4_a_child_that_escapes_the_wrapper_still_cannot_orphan_its_siblings(
    cache, monkeypatch
):
    # WHAT THE TASKGROUP ACTUALLY BUYS, isolated. With total children even gather
    # would join, because gather only abandons siblings when one of them RAISES. So
    # this test breaks the totality on purpose: a BaseException passes through
    # _total_refresh by design (SystemExit and KeyboardInterrupt must not be recorded
    # as an upstream failure), and a future child added without the wrapper would
    # behave the same way.
    #
    # Under gather that child's sibling is detached and keeps running into the next
    # generation. Under a TaskGroup it is cancelled and joined before the cycle can
    # exit, which is the guarantee that survives someone breaking the assumption.
    clock = _CycleClock(pollers.POLL_INTERVAL_S)
    monkeypatch.setattr(pollers.asyncio, "sleep", clock.sleep)

    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def escapes_the_wrapper(app, client):
        await sibling_started.wait()  # let the sibling get into its wait first
        raise _NotAnException("the wrapper catches Exception, and this is not one")

    async def blocked_sibling(app, client):
        sibling_started.set()
        try:
            await asyncio.Event().wait()  # never set: only cancellation ends this
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    async def idle(app, client):
        return None

    monkeypatch.setattr(pollers, "_refresh_buses", escapes_the_wrapper)
    monkeypatch.setattr(pollers, "_refresh_subways", blocked_sibling)
    for name in ("_refresh_railroads", "_refresh_path", "_refresh_ferry"):
        monkeypatch.setattr(pollers, name, idle)

    # The group re-raises as a BaseExceptionGroup, which is NOT an Exception, so the
    # cycle's defensive handler does not swallow it and the loop ends. That is the
    # correct answer for a BaseException: it is not this loop's to absorb.
    with pytest.raises(BaseExceptionGroup) as raised:
        await asyncio.wait_for(pollers._poll_feeds(app_module.app), timeout=5)

    assert isinstance(raised.value.exceptions[0], _NotAnException)
    assert sibling_cancelled.is_set()  # cancelled, not left running
    assert clock.cycles == 0  # and it never reached the sleep


async def test_c4_an_unclassified_failure_degrades_every_operator_surface(cache, monkeypatch):
    # REVIEW FIX. Recording entry["error"] alone was a false green: /api/status builds
    # degraded_systems from the PER-SYSTEM last_error and never reads entry["error"],
    # C2's blocks kept reporting ok: true so the client would not dim, and the
    # contract monitor reads neither. A source could stop polling entirely while
    # every operator surface stayed healthy, which is the exact shape this audit arc
    # exists to delete.
    clock = _CycleClock(pollers.POLL_INTERVAL_S)
    monkeypatch.setattr(pollers.asyncio, "sleep", clock.sleep)

    # Seed the two aggregate envelopes as healthy, the way a good poll leaves them.
    cache["subways"]["systems"] = {
        group: {"fetched_at": 1000.0, "ok": True, "retained_since": None, "routes": ["A"]}
        for group in ("ACE", "BDFM")
    }
    app_module.app.state.subway_feed_health = {"total": 8, "ok": 8, "failed": []}
    app_module.app.state.ferry_feed_health = {"total": 1, "ok": 1, "failed": []}

    async def unclassified(app, client):
        raise TypeError("a bug in our own code, not the upstream's")

    async def idle(app, client):
        return None

    monkeypatch.setattr(pollers, "_refresh_subways", unclassified)
    monkeypatch.setattr(pollers, "_refresh_ferry", unclassified)
    for name in ("_refresh_buses", "_refresh_railroads", "_refresh_path"):
        monkeypatch.setattr(pollers, name, idle)

    loop_task = asyncio.create_task(pollers._poll_feeds(app_module.app))
    try:
        await asyncio.wait_for(_wait_for(lambda: clock.cycles >= 1), timeout=5)
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    # The error, as before.
    assert cache["subways"]["error"]["status"] == 500
    # THE TYPE, NOT THE MESSAGE: the detail is served publicly and str(exc) of an
    # unclassified exception is arbitrary text.
    assert "TypeError" in cache["subways"]["error"]["detail"]
    assert "a bug in our own code" not in cache["subways"]["error"]["detail"]
    # And every per-system surface now says so too.
    assert all(block["ok"] is False for block in cache["subways"]["systems"].values())
    assert app_module.app.state.subway_feed_health["ok"] == 0
    assert len(app_module.app.state.subway_feed_health["failed"]) == 8
    assert app_module.app.state.ferry_feed_health == {"total": 1, "ok": 0, "failed": ["ferry"]}


async def test_c4_a_spurious_cancellation_is_recorded_instead_of_vanishing(cache, monkeypatch):
    # REVIEW FIX. A TaskGroup DISCARDS a child that ends cancelled: no error, no log,
    # the cycle reports success and sleeps. So a refresher that raises a bare
    # CancelledError with nobody cancelling it would stop polling that system forever
    # while its cache showed no error at all. Task.cancelling() tells the two apart.
    clock = _CycleClock(pollers.POLL_INTERVAL_S)
    monkeypatch.setattr(pollers.asyncio, "sleep", clock.sleep)
    app_module.app.state.path_feed_health = {"total": 1, "ok": 1, "failed": []}

    async def cancels_itself(app, client):
        raise asyncio.CancelledError("nobody asked for this")

    async def idle(app, client):
        return None

    monkeypatch.setattr(pollers, "_refresh_path", cancels_itself)
    for name in ("_refresh_buses", "_refresh_subways", "_refresh_railroads", "_refresh_ferry"):
        monkeypatch.setattr(pollers, name, idle)

    loop_task = asyncio.create_task(pollers._poll_feeds(app_module.app))
    try:
        await asyncio.wait_for(_wait_for(lambda: clock.cycles >= 1), timeout=5)
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    # The cycle survived (the group discards a cancelled child), and the failure is
    # visible rather than silent.
    assert cache["path"]["error"]["status"] == 500
    assert "CancelledError" in cache["path"]["error"]["detail"]
    assert app_module.app.state.path_feed_health == {"total": 1, "ok": 0, "failed": ["PATH"]}


def test_c4_first_leaf_picks_the_first_failure_and_descends_nested_groups():
    # REVIEW FIX. Which cause wins when BOTH legs fail was arbitrary and unpinned:
    # every existing test produced a one-element group, so exceptions[0] could have
    # been exceptions[-1] with the suite still green. A TaskGroup appends in
    # COMPLETION order, so the first entry is the failure that cancelled the other,
    # which is the one worth recording.
    from feeds.ferry import _first_leaf

    first = httpx.ConnectError("the leg that failed first")
    second = RuntimeError("the sibling, losing the race")
    assert _first_leaf(ExceptionGroup("pair", [first, second])) is first
    # A nested group must never reach a handler as an opaque repr.
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [first]), second])
    assert _first_leaf(nested) is first


# ---------------------------------------------------------------------------
# NJ Transit: a failed poll must say so on the block the CLIENT reads (15b)
# ---------------------------------------------------------------------------


async def _run_njt_refresh(monkeypatch, cache, raiser):
    """Drive _refresh_njt once with a static group that is ready and a fetch that
    fails the given way, starting from a healthy per-system block."""
    app = app_module.app
    monkeypatch.setattr(app.state, "njt_static_status", "ready", raising=False)
    monkeypatch.setattr(app.state, "njt_stops", {"109": {}}, raising=False)
    monkeypatch.setattr(app.state, "njt_trips", {"T1": {}}, raising=False)
    entry = cache["njt"]
    # A healthy previous generation, exactly as a successful poll leaves it.
    entry.update(
        data=[{"id": "T1"}],
        fetched_at=1000.0,
        feed_timestamp=1000.0,
        error=None,
        systems={"njt": {"fetched_at": 1000.0, "ok": True, "retained_since": None, "routes": None}},
    )

    async def boom(*_args, **_kwargs):
        raise raiser

    monkeypatch.setattr(app_module, "fetch_njt_trains", boom)
    await pollers._refresh_njt(app, client=None)
    return entry


@pytest.mark.parametrize(
    "raiser",
    [
        httpx.ConnectError("upstream refused"),
        pollers.njt_auth.NjtAuthError("token rejected"),
        pollers.njt_auth.NjtUpstreamError("HTTP 503"),
        DecodeError("not a protobuf"),
    ],
    ids=["transport", "auth", "upstream", "decode"],
)
async def test_a_failed_njt_poll_marks_its_own_system_block_not_just_the_cache_error(
    monkeypatch, cache, raiser
):
    """THE DEFECT THE CONTRACT TIER FOUND, pinned one layer down.

    /api/njt-trains publishes a C2 per-system block, and the client reads THAT to
    decide whether to draw a train as stale. Every classified failure in
    _refresh_njt used to record the cache error and the feed_health dict and leave
    the block reporting ok: True, because only the subway and railroad refreshers
    called _mark_all_systems_failed and the unclassified-failure degrader was the
    one path here that did.

    The consequence is the exact trade C2 exists to refuse: retention is honest
    only when the retained data is drawn AS stale, so a block still claiming ok
    through an outage turns "last-known trains, dimmed" into "ghost trains at full
    opacity". Parametrized across all four classified failures because the
    original defect was per-branch, and one fixed branch would have looked green.

    THE RETAINED TRAINS STAY, which is the other half of the same claim: a failed
    poll learned nothing new, so the previous answer is still the best available.
    It is the block, not the data, that has to change.
    """
    entry = await _run_njt_refresh(monkeypatch, cache, raiser)
    assert entry["systems"]["njt"]["ok"] is False, entry["systems"]
    # fetched_at is deliberately untouched: "this system's data is from then" is
    # still true, and its divergence from the envelope's advancing clock IS the
    # staleness signal.
    assert entry["systems"]["njt"]["fetched_at"] == 1000.0
    assert entry["data"] == [{"id": "T1"}], "a failed poll keeps the last-known trains"
    assert entry["error"] is not None
    assert app_module.app.state.njt_feed_health == {"total": 1, "ok": 0, "failed": ["njt"]}


async def test_the_not_configured_njt_poll_also_marks_the_block(monkeypatch, cache):
    """The same claim on the path that makes no request at all.

    A deployment whose credentials are removed after a healthy start would
    otherwise keep serving its last trains with a block that says they are fine,
    forever, because nothing else ever writes to that block again.
    """
    app = app_module.app
    monkeypatch.setattr(app.state, "njt_static_status", "not-configured", raising=False)
    entry = cache["njt"]
    entry.update(
        data=[{"id": "T1"}],
        fetched_at=1000.0,
        systems={"njt": {"fetched_at": 1000.0, "ok": True, "retained_since": None, "routes": None}},
    )
    await pollers._refresh_njt(app, client=None)
    assert entry["systems"]["njt"]["ok"] is False
    assert "not configured" in entry["error"]["detail"]
