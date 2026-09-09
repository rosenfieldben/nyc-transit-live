#!/usr/bin/env python3
"""F10 (P2): "Timeout failures disagree across health surfaces." Verification harness.

THE AUDIT'S CLAIM, quoted from docs/reviews/audit-2026-09-05.md section 1:

    "The deadline wrapper catches its timeout, records a 504, and marks the response
     envelope's systems failed. It does not call the separate degradation callback.
     The outer wrapper receives a normal return, leaving the operational health map
     unchanged.

     The exact composed path was reproduced with old subway data, an initially healthy
     eight-group subway health map, and a fresh bus feed. Afterwards, the subway cache
     recorded 504 and envelope systems had `ok:false`, but the operational map still
     said all eight groups were healthy and health classifications were empty."

    Acceptance note: "Keeping readiness HTTP 200 when another mode is usable can remain
    an explicit availability decision; the falsely empty degradation list is the defect."

WHAT THIS SCRIPT MEASURES
    One real poll cycle is run twice over the same seeded process state, and four
    surfaces are read after each run:
      1. the subway cache entry's recorded error status         (audit: 504)
      2. /api/subways envelope `systems[*].ok`                  (audit: false)
      3. /api/status `subway_feeds`                             (audit: still 8/8 healthy)
      4. /healthz `degraded` plus its HTTP status                (audit: empty list, 200)

    FIXED ON claude/release1-small-fixes, so arm A now checks the fix. Same seed, same
    injected fault, same four surfaces; rows 3 and 4 are required to report 0/8 and
    ['subway-groups-down'] instead, and each check that moved carries the audit's value
    in its label. Arm B is untouched: it was always the control that showed the
    machinery existed, and the fix is the cycle handing that same callback to the
    deadline path.

    Arm A trips backend/pollers.py REFRESH_DEADLINE_S on the subway refresh.
    Arm B is the control: the same composition, same seed, but the subway upstream
    raises an unclassified exception instead of hanging, so _total_refresh's
    degradation callback DOES fire. The two arms differ in exactly one injected fault,
    which is what isolates the disagreement to the timeout path.

WHAT IS PRODUCTION CODE (nothing here is re-implemented)
    pollers._poll_feeds          the real cycle, its TaskGroup and its wrapper composition
                                 (backend/pollers.py lines 1006 to 1013)
    pollers._total_refresh       the outer wrapper (line 186)
    pollers._bounded_refresh     the deadline wrapper (line 102)
    pollers._feed_degrader       the degradation callback factory (line 157)
    pollers._refresh_subways     the real subway refresher (line 411)
    pollers._refresh_buses       the real bus refresher (line 256)
    routes/status.py             the real /api/status, /healthz and _health_codes
                                 (majority-outage classification at line 259)
    cache._note_failure, pollers._mark_all_systems_failed, pollers._system_freshness
    The endpoints are served through the real ASGI app (httpx.ASGITransport over
    main.app), so surfaces 2, 3 and 4 are measured on served JSON, not on app.state.

WHAT IS INJECTED (and why)
    * main.fetch_subway_trains and main.fetch_vehicle_positions are replaced. The
      refreshers resolve them through `main.` at call time (see pollers.py's module
      docstring), so this swaps ONLY the upstream fetch. Cycle 1 returns a healthy
      eight-group result, so the initially healthy subway health map and the eight
      envelope blocks are BUILT BY PRODUCTION CODE rather than hand written. Cycle 2
      hangs (arm A) or raises ValueError (arm B).
    * The four sources this finding is not about (railroads, path, ferry, njt) are
      replaced with no-op refreshers so the cycle stays hermetic. NJ TRANSIT IS NEVER
      CONTACTED: its refresher never runs, NJT credentials are scrubbed from the
      environment, and a network guard makes any real httpx egress raise.
    * REFRESH_DEADLINE_S is compressed from 45s to 0.05s for the wedged cycle only
      (the healthy cycles keep a generous 30s), so the run takes milliseconds instead
      of 45 seconds. The wrapper under test is unchanged.
    * POLL_INTERVAL_S is set to a sentinel and asyncio.sleep is wrapped so the loop is
      observed at its cycle boundary and then cancelled. This is the same technique
      backend/tests/test_pollers_concurrency.py uses (_CycleClock).
    * pollers._feed_degrader is wrapped by a PASS THROUGH counter: the real hook is
      built and really called, the wrapper only records that it was called. This turns
      "does not call the separate degradation callback" into a measured number.
    * The subway entry is aged by subtracting a fixed offset from its timestamps after
      the healthy cycle. That is a clock shift standing in for waiting 120 seconds.

DETERMINISM
    No live feed, no upstream host, no captured feed header compared against today's
    date. Every age in the output is an offset constructed inside this run (poll age
    120.0s, content lag 5.0s), so the arithmetic against FEED_STALE_AFTER_S is the same
    on any day. The only wall-clock reads are the ones production makes itself.

RUN (from the repository root):
    cd /home/user/nyc-transit-live && \
      .venv/bin/python docs/reviews/audit-2026-09-05/f10_deadline_health_disagreement.py

Exits 0 while the finding still behaves as recorded, non-zero if reality has changed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# HARD CONSTRAINT: no NJ Transit mint may be spent. This script always scrubbed before
# the import and again after, which is the correct order and was the model for the
# other ten; what it lacked was the ADDRESS wall, so its safety rested entirely on the
# scrub being right. Now neither wall is load bearing alone. See _hermetic.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

import httpx  # noqa: E402

import main as app_module  # noqa: E402
import njt_auth  # noqa: E402
import pollers  # noqa: E402
from feeds import (  # noqa: E402
    SUBWAY_FEED_URLS,
    combine_group_arrivals,
    combine_group_trains,
)
from routes import status as status_routes  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


# The after-import scrub this script always had, now through the shared helper so it
# blanks rather than pops (a pop is refilled by feeds.shared's own load_dotenv) and so
# the assertion covers the addresses too.
_hermetic.verify()


# --------------------------------------------------------------------------------
# Network guard: any real egress through httpx becomes a loud failure. The ASGI
# transport used to serve the endpoints is a different class and is unaffected.
# --------------------------------------------------------------------------------
def _blocked_async(self, *args, **kwargs):
    raise RuntimeError("network egress attempted; this reproduction must stay hermetic")


def _blocked_sync(self, *args, **kwargs):
    raise RuntimeError("network egress attempted; this reproduction must stay hermetic")


httpx.AsyncHTTPTransport.handle_async_request = _blocked_async
httpx.HTTPTransport.handle_request = _blocked_sync

# The two unclassified-failure paths log with logger.exception; quiet them so the
# measured block below is readable. Nothing about the behavior under test changes.
logging.getLogger("main").setLevel(logging.CRITICAL)

APP = app_module.app
GROUPS = sorted(SUBWAY_FEED_URLS)
GROUP_COUNT = len(SUBWAY_FEED_URLS)

# The sentinel the cycle sleeps for between generations; unmistakable in the clock.
POLL_INTERVAL_SENTINEL = 987654.0
# Generous for the healthy cycle (nothing may time out there), compressed for the
# wedged cycle so the script finishes in milliseconds.
HEALTHY_DEADLINE_S = 30.0
WEDGED_DEADLINE_S = 0.05
# How far back the subway's last successful poll is pushed: "old subway data".
SUBWAY_POLL_AGE_S = 120.0
# The upstream content lag recorded by that poll. Under FEED_STALE_AFTER_S on
# purpose, so no content-staleness code can fire and cloud the measurement.
SUBWAY_CONTENT_LAG_S = 5.0
BUS_CONTENT_LAG_S = 5.0

TRAIN_TEMPLATE = {
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
BUSES = [
    {"id": "MTA NYCT_1", "route_id": "M15", "latitude": 40.7, "longitude": -74.0, "bearing": 90.0}
]

degrader_calls: list[str] = []


def install_degrader_probe() -> None:
    """Wrap pollers._feed_degrader in a pass through counter.

    The REAL hook is still built and, when the production code calls it, really
    invoked. The wrapper only appends the source name, which is how "the separate
    degradation callback was not called" becomes a measured zero instead of an
    inference from the health map.
    """
    real_factory = pollers._feed_degrader

    def counting_factory(app, name, entry):
        real_mark = real_factory(app, name, entry)

        def mark() -> None:
            degrader_calls.append(name)
            real_mark()

        return mark

    pollers._feed_degrader = counting_factory


def install_stub_refreshers() -> None:
    """Replace the four sources this finding is not about.

    _poll_feeds resolves each refresher by name out of the pollers module globals on
    every cycle, so setting the module attribute is what the loop will actually run.
    The subway and bus refreshers are deliberately left as the real ones.
    """
    pollers._refresh_railroads = noop_refresh
    pollers._refresh_path = noop_refresh
    pollers._refresh_ferry = noop_refresh
    pollers._refresh_njt = noop_refresh


async def noop_refresh(app, client) -> None:
    """Stands in for the railroads / path / ferry / njt refreshers.

    Their entries stay unfilled, which keeps them out of every health computation
    (routes/status.py skips an entry whose data is None) and guarantees no NJ Transit
    code path runs at all.
    """
    return None


def make_healthy_subway_fetch(now_fn):
    """One healthy eight-group subway result, in fetch_subway_trains' exact tuple shape."""

    async def fetch_subway_trains(stops, client):
        now = now_fn()
        trains_by_group = {}
        arrivals_by_group = {}
        for index, group in enumerate(SUBWAY_FEED_URLS):
            train = dict(TRAIN_TEMPLATE)
            train["trip_id"] = f"trip-{group}"
            train["route_id"] = group[0]
            train["next_time"] = now + 60 + index
            trains_by_group[group] = [train]
            arrivals_by_group[group] = {}
        return (
            combine_group_trains(trains_by_group),
            combine_group_arrivals(arrivals_by_group),
            now - SUBWAY_CONTENT_LAG_S,  # feed_timestamp
            [],  # failed_feeds: a fully healthy poll
            trains_by_group,
            arrivals_by_group,
        )

    return fetch_subway_trains


async def wedged_subway_fetch(stops, client):
    """The injected fault for arm A: an upstream that never completes.

    _refresh_subways' only await is this call, so the deadline can only land here,
    which is the shape _bounded_refresh's docstring describes.
    """
    await asyncio.Event().wait()


async def raising_subway_fetch(stops, client):
    """The injected fault for arm B: a failure nobody classified.

    _refresh_subways catches RuntimeError and httpx.HTTPError only, and
    _bounded_refresh catches TimeoutError only, so this reaches _total_refresh and
    takes the mark_degraded path the timeout does not.
    """
    raise ValueError("synthetic unclassified subway failure")


def make_bus_fetch(now_fn):
    async def fetch_vehicle_positions(client):
        return list(BUSES), now_fn() - BUS_CONTENT_LAG_S

    return fetch_vehicle_positions


def seed_state() -> None:
    """A process that has just started: empty caches, static ready, no health yet."""
    APP.state.feed_cache = {
        name: app_module._fresh_entry()
        for name in ("buses", "subways", "railroads", "path", "ferry", "njt")
    }
    APP.state.subway_stops = {"101N": {"name": "Alpha", "lat": 40.7, "lon": -74.0}}
    APP.state.subway_positions = {}
    APP.state.subway_arrivals = {}
    APP.state.subway_arrivals_by_system = {}
    APP.state.subway_static_status = "ready"
    APP.state.subway_feed_health = None
    APP.state.railroad_feed_health = None
    APP.state.path_feed_health = None
    APP.state.ferry_feed_health = None
    APP.state.njt_feed_health = None


class CycleClock:
    """Replaces the loop's inter-cycle sleep so one generation can be observed.

    Same idea as backend/tests/test_pollers_concurrency.py's _CycleClock: the loop
    sleeps for exactly POLL_INTERVAL_S between cycles, so keying on that delay marks
    the cycle boundary precisely. Here the boundary sets an event and then parks, so
    the cycle under measurement is never followed by a second one.
    """

    def __init__(self) -> None:
        self.real_sleep = asyncio.sleep
        self.done = asyncio.Event()

    async def sleep(self, delay, *args, **kwargs):
        if delay == POLL_INTERVAL_SENTINEL:
            self.done.set()
            return await self.real_sleep(3600)
        return await self.real_sleep(delay, *args, **kwargs)


async def run_one_cycle(deadline_s: float) -> None:
    """Run pollers._poll_feeds for exactly one generation, then cancel it."""
    pollers.POLL_INTERVAL_S = POLL_INTERVAL_SENTINEL
    pollers.REFRESH_DEADLINE_S = deadline_s
    clock = CycleClock()
    asyncio.sleep = clock.sleep
    task = asyncio.create_task(pollers._poll_feeds(APP))
    try:
        await asyncio.wait_for(clock.done.wait(), timeout=30)
    finally:
        asyncio.sleep = clock.real_sleep
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def age_subway_entry() -> None:
    """Push the subway's successful poll SUBWAY_POLL_AGE_S into the past.

    A clock shift instead of a real wait. Every per-system block moves with the
    aggregate, which is what a poll that happened two minutes ago would have left.
    """
    entry = APP.state.feed_cache["subways"]
    entry["fetched_at"] -= SUBWAY_POLL_AGE_S
    entry["feed_timestamp"] -= SUBWAY_POLL_AGE_S
    for block in (entry.get("systems") or {}).values():
        if block["fetched_at"] is not None:
            block["fetched_at"] -= SUBWAY_POLL_AGE_S


async def serve(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://audit-f10") as client:
        return await client.get(path)


async def measure(second_cycle_fetch, deadline_s: float, now_fn) -> dict:
    """Seed, run one healthy cycle, age the subway, run the cycle under test, read
    every surface through the ASGI app."""
    degrader_calls.clear()
    seed_state()

    app_module.fetch_subway_trains = make_healthy_subway_fetch(now_fn)
    app_module.fetch_vehicle_positions = make_bus_fetch(now_fn)
    await run_one_cycle(HEALTHY_DEADLINE_S)

    before = {
        "subway_feed_health": copy.deepcopy(APP.state.subway_feed_health),
        "systems_ok": {
            group: block["ok"]
            for group, block in (APP.state.feed_cache["subways"].get("systems") or {}).items()
        },
        "subway_error": copy.deepcopy(APP.state.feed_cache["subways"]["error"]),
        "subway_trains": len(APP.state.feed_cache["subways"]["data"] or []),
    }
    age_subway_entry()

    app_module.fetch_subway_trains = second_cycle_fetch
    await run_one_cycle(deadline_s)

    subways_res = await serve("/api/subways")
    status_res = await serve("/api/status")
    healthz_res = await serve("/healthz")
    entry = APP.state.feed_cache["subways"]
    return {
        "before": before,
        "degrader_calls": list(degrader_calls),
        "cache_error": copy.deepcopy(entry["error"]),
        "cache_trains": len(entry["data"] or []),
        "envelope": subways_res.json(),
        "status": status_res.json(),
        "healthz_code": healthz_res.status_code,
        "healthz": healthz_res.json(),
    }


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def show(arm: dict, label: str) -> None:
    envelope_systems = arm["envelope"]["systems"] or {}
    status_body = arm["status"]
    print(f"  before the cycle under test ({label}):")
    print(f"    app.state.subway_feed_health : {arm['before']['subway_feed_health']}")
    print(f"    envelope systems ok          : {sorted(set(arm['before']['systems_ok'].values()))}"
          f"  over {len(arm['before']['systems_ok'])} groups")
    print(f"    subway cache error           : {arm['before']['subway_error']}")
    print(f"    subway trains cached         : {arm['before']['subway_trains']}")
    print("  after the cycle under test:")
    print(f"    degradation callback calls   : {arm['degrader_calls'] or 'none'}")
    print(f"    subway cache error           : {arm['cache_error']}")
    print(f"    subway trains still cached   : {arm['cache_trains']} (last known data retained)")
    ok_flags = {group: block["ok"] for group, block in envelope_systems.items()}
    print(f"    /api/subways systems ok      : {json.dumps(ok_flags, sort_keys=True)}")
    print(f"    /api/status feeds.subways    : last_error="
          f"{status_body['feeds']['subways']['last_error']}")
    print(f"    /api/status subway_feeds     : {status_body['subway_feeds']}")
    print(f"    /healthz HTTP                : {arm['healthz_code']}")
    print(f"    /healthz status              : {arm['healthz']['status']}")
    print(f"    /healthz degraded            : {arm['healthz']['degraded']}")
    print(f"    /healthz reasons             : {arm['healthz'].get('reasons', '(key omitted)')}")


async def amain() -> int:
    install_degrader_probe()
    install_stub_refreshers()
    now_fn = time.time

    deadline_arm = await measure(wedged_subway_fetch, WEDGED_DEADLINE_S, now_fn)
    control_arm = await measure(raising_subway_fetch, HEALTHY_DEADLINE_S, now_fn)

    # Re-derive the freshness arithmetic /healthz used, from the served snapshot.
    served_at = deadline_arm["status"]["served_at"]
    feeds = deadline_arm["status"]["feeds"]
    subway_poll_age = feeds["subways"]["age_s"]
    subway_content_lag = feeds["subways"]["feed_age_s"]
    bus_poll_age = feeds["buses"]["age_s"]
    bus_content_lag = feeds["buses"]["feed_age_s"]

    rule("F10 setup, re-derived from the code rather than from the audit text")
    print(f"  subway feed groups (len(SUBWAY_FEED_URLS))     : {GROUP_COUNT}")
    print(f"  groups                                         : {GROUPS}")
    print(f"  production REFRESH_DEADLINE_S (pollers.py)     : 45 s")
    print(f"  compressed for the wedged cycle here           : {WEDGED_DEADLINE_S} s")
    print(f"  FEED_STALE_AFTER_S (cache.py)                  : {status_routes.FEED_STALE_AFTER_S} s")
    print("  freshness arithmetic at the moment /api/status was served:")
    print(f"    subway poll age   = served_at - fetched_at = {subway_poll_age} s "
          f"(>= {status_routes.FEED_STALE_AFTER_S}: subway does NOT count as fresh)")
    print(f"    subway content lag = fetched_at - feed_timestamp = {subway_content_lag} s "
          f"(< {status_routes.FEED_STALE_AFTER_S}: no content-stale code)")
    print(f"    bus poll age      = {bus_poll_age} s, bus content lag = {bus_content_lag} s "
          f"(the bus feed is the fresh one)")
    print(f"    served_at         = {served_at:.3f} (this snapshot's build time)")

    rule("ARM A: the subway refresh deadline fires (the audit's exact scenario)")
    show(deadline_arm, "healthy eight-group poll, then aged by 120.0 s")

    rule("ARM B (control): same composition, an unclassified failure instead")
    show(control_arm, "healthy eight-group poll, then aged by 120.0 s")

    # Sensitivity note (print only): what the classification list would hold if the
    # retained subway content had ALSO been stale. Uses the production pure function.
    stale_cache = copy.deepcopy(APP.state.feed_cache)
    stale_cache["subways"]["feed_timestamp"] -= 10 * status_routes.FEED_STALE_AFTER_S
    sensitivity = status_routes._health_codes(
        cache=stale_cache,
        bus_index_status="ready",
        subway_static_status="ready",
        subway_feed_health={"total": GROUP_COUNT, "ok": GROUP_COUNT, "failed": []},
        njt_mint_quota=False,
        now=served_at,
    )
    rule("Sensitivity note (not an assertion)")
    print("  With the SAME untouched health map but a stale retained content time,")
    print(f"  _health_codes returns {sensitivity}. That code describes the CONTENT age,")
    print("  not the failed refresh, and subway-groups-down is still absent, so no")
    print("  configuration of this scenario surfaces the deadline as subway degradation.")

    # ---------------------------------------------------------------- assertions
    healthy_map = {"total": GROUP_COUNT, "ok": GROUP_COUNT, "failed": []}
    degraded_map = {"total": GROUP_COUNT, "ok": 0, "failed": GROUPS}
    envelope_ok = {
        group: block["ok"] for group, block in (deadline_arm["envelope"]["systems"] or {}).items()
    }
    control_ok = {
        group: block["ok"] for group, block in (control_arm["envelope"]["systems"] or {}).items()
    }

    checks = [
        (
            "arm A: the deadline wrapper recorded 504 on the subway cache entry",
            (deadline_arm["cache_error"] or {}).get("status") == 504
            and "deadline" in (deadline_arm["cache_error"] or {}).get("detail", ""),
            f"cache error = {deadline_arm['cache_error']}",
        ),
        (
            "arm A: last-known subway data was retained (8 trains still served)",
            deadline_arm["cache_trains"] == GROUP_COUNT,
            f"cached trains = {deadline_arm['cache_trains']}",
        ),
        (
            "arm A: every envelope system block on /api/subways reports ok:false",
            len(envelope_ok) == GROUP_COUNT and set(envelope_ok.values()) == {False},
            f"envelope ok flags = {envelope_ok}",
        ),
        # ---- arm A, AFTER THE FIX. Each label carries the value the audit recorded,
        # so the before and the after read side by side.
        (
            "arm A FIXED: the degradation callback WAS called (was: never called)",
            deadline_arm["degrader_calls"] == ["subways"],
            f"callback calls = {deadline_arm['degrader_calls']}",
        ),
        (
            "arm A FIXED: /api/status subway_feeds reports 0 of 8 (was: all eight healthy)",
            deadline_arm["status"]["subway_feeds"] == degraded_map,
            f"subway_feeds = {deadline_arm['status']['subway_feeds']}",
        ),
        (
            "arm A FIXED: /healthz degraded names subway-groups-down (was: empty)",
            deadline_arm["healthz"]["degraded"] == ["subway-groups-down"],
            f"degraded = {deadline_arm['healthz']['degraded']}",
        ),
        (
            "arm A: /healthz still answers 200 pass, because subway-groups-down is non-gating",
            deadline_arm["healthz_code"] == 200 and deadline_arm["healthz"]["status"] == "pass",
            f"HTTP {deadline_arm['healthz_code']}, status {deadline_arm['healthz']['status']}",
        ),
        (
            "arm A: /api/status feeds.subways.last_error DOES carry the 504",
            (deadline_arm["status"]["feeds"]["subways"]["last_error"] or {}).get("status") == 504,
            f"last_error = {deadline_arm['status']['feeds']['subways']['last_error']}",
        ),
        (
            "arm B control: the degradation callback WAS called for subways",
            control_arm["degrader_calls"] == ["subways"],
            f"callback calls = {control_arm['degrader_calls']}",
        ),
        (
            "arm B control: /api/status subway_feeds reports 0 of 8 groups healthy",
            control_arm["status"]["subway_feeds"] == degraded_map,
            f"subway_feeds = {control_arm['status']['subway_feeds']}",
        ),
        (
            "arm B control: /healthz degraded names subway-groups-down",
            control_arm["healthz"]["degraded"] == ["subway-groups-down"],
            f"degraded = {control_arm['healthz']['degraded']}",
        ),
        (
            "arm B control: envelope systems also report ok:false (both arms agree here)",
            len(control_ok) == GROUP_COUNT and set(control_ok.values()) == {False},
            f"envelope ok flags = {control_ok}",
        ),
    ]

    rule("Regression checks against the recorded disposition")
    failures = []
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"         {detail}")
        if not ok:
            failures.append(label)

    print()
    if failures:
        print("The audit record no longer matches reality. Failing checks:")
        for label in failures:
            print(f"  - {label}")
        print(
            "DISPOSITION: CHANGED. F10 no longer reproduces as recorded; see the failing "
            "checks above."
        )
        return 1

    print(
        "DISPOSITION: FIXED (claude/release1-small-fixes)  A tripped subway refresh deadline "
        f"records 504 and flips all {GROUP_COUNT} envelope blocks to ok:false. BEFORE: "
        f"/api/status still reported {GROUP_COUNT}/{GROUP_COUNT} subway groups healthy and "
        "/healthz answered 200 with degraded:[], so the one signal a watcher outside the "
        "process reads said nothing at all. AFTER: the cycle hands the SAME _feed_degrader "
        "to the deadline path that it already handed the unclassified one, so both arms now "
        "report 0/8 and degraded:['subway-groups-down']. The audit's control arm (arm B) is "
        "unchanged and is what showed the machinery already existed. /healthz still answers "
        "200, which the audit named as an explicit availability decision rather than the "
        "defect: the falsely empty degradation list was the defect, and it is gone."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
