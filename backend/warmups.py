"""Background static-GTFS warmup tasks: load each static group off the startup
critical path, retrying on failure, and flip its app.state status field.

Depends on main for the subway loaders and the retry constants (STATIC_RETRY_S,
STATIC_RETRY_SCHEDULE_S, STATIC_ATTEMPT_DEADLINE_S): those are the names the tests
monkeypatch on the main module (main is the composition root), so the warmups
resolve them through `main.` at call time to keep
`monkeypatch.setattr(main, "load_subway_stops", ...)` and
`main.STATIC_RETRY_S` effective after the split. The railroad/path/ferry static
modules are patched at their own module
(monkeypatch.setattr(main.ferry_static, "load_ferry_static", ...) sets it on the
shared module object), so those are imported directly.

Retry timing lives in two separate knobs that must not be conflated: the backoff
SCHEDULE (STATIC_RETRY_SCHEDULE_S, walked by _retry_delay) governs how long to
wait BETWEEN attempts, while STATIC_ATTEMPT_DEADLINE_S bounds how long a single
attempt may run. STATIC_RETRY_S is the steady-state interval AND the ceiling every
schedule rung is capped at, which is what keeps shrinking it in a test enough to
make the whole schedule instant.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable

from fastapi import FastAPI

import ferry_static
import main
import njt_auth
import njt_static
import path_static
import railroad_static

# Log through the "main" logger (not __name__) so the transition logs and the
# tests' caplog(logger="main") targeting are unchanged by the split.
logger = logging.getLogger("main")


def _set_static_status(
    app: FastAPI,
    field: str,
    status: str,
    exc: BaseException | None = None,
    retry_in_s: float | None = None,
) -> None:
    """Set a static group's status, logging only on a TRANSITION so a long retry
    loop (or the per-poll checks that read this) never spams the log. `field` is one
    of "subway_static_status", "railroad_static_status", "path_static_status",
    "ferry_static_status", or "njt_static_status". retry_in_s is the delay actually
    scheduled for the next attempt: the retry interval is a backoff schedule now,
    not a flat constant, so the log must report the real wait rather than the
    steady-state ceiling. Because this logs only the TRANSITION, the delay it names
    is the FIRST rung; _fail_and_wait logs each later escalation, so the whole
    schedule stays visible without one line per attempt.

    "not-configured" (NJT only, 15a) is a TERMINAL state, not a failure: it is
    logged once at INFO rather than WARNING and names no retry, because there is
    nothing to retry and nothing broken. An operator who did not mean to leave NJ
    Transit unconfigured needs to see the line; one who did must not see a
    warning about it on every deploy."""
    if getattr(app.state, field, None) == status:
        return
    setattr(app.state, field, status)
    if status == "failed":
        delay = main.STATIC_RETRY_S if retry_in_s is None else retry_in_s
        logger.warning("%s failed to load (%s); retrying in %.0fs", field, _describe(exc), delay)
    elif status == "ready":
        logger.info("%s ready", field)
    elif status == "not-configured":
        logger.info("%s not configured (%s); this system is disabled", field, _describe(exc))


def _describe(exc: BaseException | None) -> str:
    """A non-empty reason for the failure log. str(exc) is EMPTY for several
    argument-less exceptions the warmups actually hit, the important one being the
    R2 attempt deadline: str(TimeoutError()) is "", which would log a bare "failed
    to load ()" and name no cause at all. Fall back to the class name so every
    failure says something.

    str(exc) is called EAGERLY here, where the old lazy "%s" left it to the logging
    machinery, so a pathological __str__ that raises would now propagate out of
    _fail_and_wait and kill the retry loop for good. A describer that can itself
    fail is worse than a vague message, so it degrades to the class name."""
    if exc is None:
        return "unknown"
    try:
        return str(exc) or type(exc).__name__
    except Exception:
        return type(exc).__name__


def _retry_delay(attempt: int, rand: Callable[[], float] = random.random) -> float:
    """Seconds to wait before retry number `attempt` (0-based: attempt 0 is the
    wait after the FIRST failed load).

    Walks main.STATIC_RETRY_SCHEDULE_S and then holds at its final rung, applying
    +-10% jitter (see the constant's comment for why the schedule has that shape).
    `rand` is injected so a test can pin the jitter to its bounds instead of
    sampling; production uses random.random.

    Every rung is capped at main.STATIC_RETRY_S. That is not a special case: the
    schedule RISES to the steady-state interval, so no rung should ever exceed it.
    Keeping the cap live (read at call time, not baked into the tuple) is what
    preserves STATIC_RETRY_S as the single ceiling knob and the documented
    monkeypatch surface: a test that shrinks it to 0.01 shrinks every rung with
    it, so the warmup state-machine tests keep driving retries instantly."""
    return _rung(attempt) * (0.9 + 0.2 * rand())


def _rung(attempt: int) -> float:
    """The scheduled interval for retry number `attempt` BEFORE jitter: the
    schedule entry, held at the last one once the schedule runs out, capped at the
    steady-state ceiling. Separate from _retry_delay so an escalation can be
    detected by comparing rungs, which jitter would otherwise blur."""
    schedule = main.STATIC_RETRY_SCHEDULE_S or (main.STATIC_RETRY_S,)
    rung = schedule[min(max(attempt, 0), len(schedule) - 1)]
    return min(rung, main.STATIC_RETRY_S)


async def _fail_and_wait(app: FastAPI, field: str, exc: BaseException, attempt: int) -> int:
    """Record a failed warmup attempt, sleep this attempt's backoff delay, and
    return the next attempt index.

    Shared by EVERY warmup failure path so they all advance one schedule: the
    exception path (which an R2 attempt-deadline TimeoutError also takes, since
    `except Exception` catches it) and, for the single-system groups, the
    empty-result path. Routing them all through here is what keeps a timed-out
    attempt and a returned-nothing attempt indistinguishable from the retry
    loop's point of view, and keeps the transition logging in exactly one place
    (so neither path can double-log).

    Logging: _set_static_status fires only on the loading -> failed TRANSITION, so
    on its own it would report the FIRST rung (about 15s) and never speak again,
    leaving the log claiming a 15s cadence through an outage that has long since
    settled onto the 300s one. So each ESCALATION to a longer rung logs once too.
    That is bounded by the length of the schedule (a handful of lines per outage,
    then silence), which keeps the no-per-attempt-spam rule intact while making the
    real cadence honest."""
    delay = _retry_delay(attempt)
    _set_static_status(app, field, "failed", exc, retry_in_s=delay)
    # Compare the RUNGS, not the jittered delays, so jitter alone never logs.
    if attempt and _rung(attempt) > _rung(attempt - 1):
        logger.warning(
            "%s still failing (%s); backing off, next retry in %.0fs",
            field,
            _describe(exc),
            delay,
        )
    await asyncio.sleep(delay)
    return attempt + 1


async def _warm_subway_static(app: FastAPI) -> None:
    """Load the subway static GTFS (stops, route lines, station markers) in the
    background, retrying on the STATIC_RETRY_SCHEDULE_S backoff until it succeeds.
    Fills the same app.state fields the handlers read, then flips the group to
    ready. A failed attempt leaves stops None, so the poller keeps noting a
    warming 503 and /healthz reports the failure until a retry succeeds."""
    attempt = 0
    while True:
        try:
            # Whole-attempt deadline (see main.STATIC_ATTEMPT_DEADLINE_S). It bounds
            # the download and the inline parses; the to_thread parse below can outlive
            # it (a Python thread cannot be force-cancelled), which is fine because a
            # CPU-bound parse finishes on its own, unlike a network transfer that can
            # trickle forever. A timeout raises TimeoutError, caught by `except
            # Exception` below and driven down the same failed-then-retry path.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                stops = await main.load_subway_stops()
                routes = main.load_subway_route_shapes()  # reuse the zip the stops load ensured
                stations = main.load_subway_stations()
                # Off the event loop: this one parses the full stop_times.txt (~36 MB,
                # millions of rows), so running it inline would block every other warmup,
                # the pollers, and /healthz for the length of the parse. The lighter
                # sibling loaders (stops/shapes/stations) stay inline as before.
                station_routes = await asyncio.to_thread(main.load_subway_station_routes)
        except Exception as exc:
            attempt = await _fail_and_wait(app, "subway_static_status", exc, attempt)
            continue
        app.state.subway_stops = stops
        app.state.subway_routes = routes
        app.state.subway_stations = stations
        app.state.subway_station_routes = station_routes
        _set_static_status(app, "subway_static_status", "ready")
        return


async def _warm_railroad_static(app: FastAPI) -> None:
    """Load the railroad static GTFS in the background, retrying on the
    STATIC_RETRY_SCHEDULE_S backoff. load_railroad_static is lenient per system (a
    download or parse failure for one system yields None for it, GPS-only, without
    raising), and that PARTIAL result still reaches ready: one system down is a
    real degraded state worth serving, since the other system's stops still place
    its trains. What does NOT reach ready is a load where EVERY system came back
    empty (below), whether that is None or a parse that yielded no stops: that is a
    total failure wearing a success's clothes, and it used to be marked ready and
    never retried, so a boot-time outage that cleared a minute later stayed broken
    until the next deploy. Fills the derived stops and
    route geometry the handlers read."""
    attempt = 0
    while True:
        try:
            # Whole-attempt deadline (main.STATIC_ATTEMPT_DEADLINE_S): a backstop over
            # the per-transfer asyncio.timeout each downloader already holds, so a
            # wedged attempt cannot stall this retry loop forever. A timeout raises
            # TimeoutError, caught below and retried like any other load failure.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                data = await railroad_static.load_railroad_static()
        except Exception as exc:
            attempt = await _fail_and_wait(app, "railroad_static_status", exc, attempt)
            continue
        if not any(d and d.get("stops") for d in data.values()):
            # EVERY system failed. The loader is deliberately lenient per system and
            # never raises for this (its per-system None contract is load-bearing for
            # the single-failure case), so the AGGREGATE judgment lives here, in the
            # caller that owns the retry loop. Same shape as the PATH and ferry
            # empty-result paths below: a second failure path in the same loop,
            # routed through _fail_and_wait so it is indistinguishable from a raised
            # error or an R2 deadline timeout, and logged exactly once by the shared
            # transition guard.
            #
            # The test is EMPTINESS, not None-ness, deliberately matching those
            # siblings: a system that downloaded and parsed but yielded zero stops is
            # just as unusable as one that returned None (it places no trains and its
            # endpoint would serve [] under a one-hour cache header), so it must not
            # be the thing that rescues the group from retrying. `d and d.get("stops")`
            # covers both shapes at once.
            attempt = await _fail_and_wait(
                app,
                "railroad_static_status",
                RuntimeError("railroad static GTFS unavailable for every system"),
                attempt,
            )
            continue
        app.state.railroad_static = data
        # Derived so the placement path (_refresh_railroads) reads stops unchanged;
        # a None system stays None (GPS-only), never a crash.
        app.state.railroad_stops = {
            system: (d["stops"] if d else None) for system, d in data.items()
        }
        app.state.railroad_routes = {
            system: (
                railroad_static.build_railroad_route_shapes(d["trips"], d["shapes"], d["routes"])
                if d
                else []
            )
            for system, d in data.items()
        }
        # Routes-per-station index per system (H5). .get("stop_times"): a cached
        # zip from before H5 parses without the table, so the derive comes up
        # empty and station popups just omit routes, rather than the load failing.
        app.state.railroad_station_routes = {
            system: (
                railroad_static.derive_railroad_stop_routes(d["trips"], d.get("stop_times") or {})
                if d
                else {}
            )
            for system, d in data.items()
        }
        _set_static_status(app, "railroad_static_status", "ready")
        return


async def _warm_path_static(app: FastAPI) -> None:
    """Load the PATH static GTFS in the background, retrying on the
    STATIC_RETRY_SCHEDULE_S backoff. load_path_static is lenient (a download or
    parse failure yields {} without raising), and PATH is a SINGLE system: unlike
    the railroad group, which reaches ready with one system None because the other
    still deserves serving, an empty PATH result means the only system failed,
    so the group stays failed and retries. That keeps the endpoint contract
    honest: while failed they serve [] under no-cache ("ask again later"), and
    ready-with-cache-headers is only ever reached with real data."""
    attempt = 0
    while True:
        try:
            # Whole-attempt deadline (main.STATIC_ATTEMPT_DEADLINE_S): the OUTER
            # ceiling layered over path_static's own per-transfer
            # asyncio.timeout(_DOWNLOAD_DEADLINE_S) (13a). Both stay: the inner bounds
            # just the download, this bounds the whole attempt. A timeout raises
            # TimeoutError, caught below and retried like any other load failure.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                data = await path_static.load_path_static()
        except Exception as exc:
            attempt = await _fail_and_wait(app, "path_static_status", exc, attempt)
            continue
        if not data.get("stops"):
            attempt = await _fail_and_wait(
                app,
                "path_static_status",
                RuntimeError("PATH static GTFS unavailable or empty"),
                attempt,
            )
            continue
        app.state.path_static = data
        app.state.path_stops = data["stops"]
        app.state.path_routes = path_static.build_path_route_shapes(
            data["trips"], data["shapes"], data["routes"]
        )
        # The advance matcher's successor relation. .get(): a cached zip from
        # before 13d parses without a stop_times table, and the group must
        # still reach ready (matching degrades to same-stop only, which
        # load_path_static already warned about).
        app.state.path_station_order = path_static.build_path_station_order(
            data["trips"], data.get("stop_times") or {}, data["child_to_parent"], data["stops"]
        )
        # Routes-per-station index (H5). Same .get("stop_times") leniency as the
        # station order: a pre-13d cached zip has no stop_times, so the index
        # comes up empty and station popups omit routes, rather than failing.
        app.state.path_station_routes = path_static.derive_path_station_routes(
            data["trips"], data.get("stop_times") or {}, data["child_to_parent"]
        )
        _set_static_status(app, "path_static_status", "ready")
        return


async def _warm_ferry_static(app: FastAPI) -> None:
    """Load the NYC Ferry static GTFS in the background, retrying on the
    STATIC_RETRY_SCHEDULE_S backoff. Exactly the PATH single-system pattern: load_ferry_static
    is lenient ({} on any failure, no raise), and an empty result means the
    only system failed, so the group stays failed and retries. That keeps the
    endpoint contract honest: while failed they serve [] under no-cache ("ask
    again later"), and ready-with-cache-headers is only reached with real data.
    ferry_static (the full parsed tables, including the trip -> route map 14b
    needs) is kept on app.state for that later phase to consume without
    re-parsing."""
    attempt = 0
    while True:
        try:
            # Whole-attempt deadline (main.STATIC_ATTEMPT_DEADLINE_S): a backstop over
            # ferry_static's own per-transfer asyncio.timeout, so a wedged attempt
            # cannot stall this retry loop forever. A timeout raises TimeoutError,
            # caught below and retried like any other load failure.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                data = await ferry_static.load_ferry_static()
        except Exception as exc:
            attempt = await _fail_and_wait(app, "ferry_static_status", exc, attempt)
            continue
        if not data.get("stops"):
            attempt = await _fail_and_wait(
                app,
                "ferry_static_status",
                RuntimeError("NYC Ferry static GTFS unavailable or empty"),
                attempt,
            )
            continue
        app.state.ferry_static = data
        app.state.ferry_stops = data["stops"]
        app.state.ferry_routes = ferry_static.build_ferry_route_shapes(
            data["trips"], data["shapes"], data["routes"]
        )
        # Routes-per-station index (H5). .get("stop_times"): a cached zip parsed
        # before H5 (or the committed trim, which carries no stop_times) yields
        # an empty index, so dock popups just omit the served routes rather than
        # the load failing.
        app.state.ferry_station_routes = ferry_static.derive_ferry_stop_routes(
            data["trips"], data.get("stop_times") or {}
        )
        _set_static_status(app, "ferry_static_status", "ready")
        return


async def _warm_njt_static(app: FastAPI) -> None:
    """Load the NJ Transit static GTFS in the background, retrying on the
    STATIC_RETRY_SCHEDULE_S backoff. The PATH/ferry single-system pattern, plus the
    one state no other group has.

    THE NOT-CONFIGURED SHORT CIRCUIT, AND WHY IT IS FIRST. NJ Transit is the only
    upstream in this app behind credentials the deployment may simply not have.
    Without this arm, an unconfigured deployment would enter the ordinary retry
    loop and stay in it forever: every attempt would fail identically, every
    failure would look like a broken upstream on /api/status, and each attempt
    would POST at a mint endpoint whose rate limit is real and unpublished. So the
    check runs BEFORE the loop, the state is its own string rather than a shade of
    "failed", and the task RETURNS rather than sleeping. Credentials cannot appear
    mid-process (they are read from the environment at boot), so there is nothing
    to wake up for.

    The check is deliberately duplicated with load_njt_static's own guard. That one
    is what makes "absent credentials reach no socket" a property of the LOADER,
    provable in a hermetic test with no app at all; this one is what keeps the app
    from spinning a retry loop around it. Neither subsumes the other, and the
    NjtNotConfigured arm below catches the case where a loader raises it from
    somewhere the pre-check could not see.

    Everything else matches the ferry warmup exactly: load_njt_static is lenient
    ({} on any failure, no raise), NJ Transit is a single system, so an empty
    result means the only system failed and the group stays failed and retries.
    """
    if not njt_auth.is_configured():
        _set_static_status(
            app,
            "njt_static_status",
            "not-configured",
            RuntimeError(f"{njt_auth.USERNAME_VAR}/{njt_auth.PASSWORD_VAR} are not set"),
        )
        return
    attempt = 0
    while True:
        try:
            # Whole-attempt deadline (main.STATIC_ATTEMPT_DEADLINE_S): the OUTER
            # ceiling over njt_static's own per-transfer asyncio.timeout, so a
            # wedged attempt cannot stall this retry loop forever. For NJT that
            # attempt may contain up to three POSTs (a mint, a rejected fetch, a
            # re-mint and its retry), which is exactly the kind of multi-request
            # attempt the 300s ceiling was sized for.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                data = await njt_static.load_njt_static()
        except njt_auth.NjtNotConfigured as exc:
            # Belt and braces with the pre-check above. Reaching here means the
            # credentials went away between the check and the load, which no
            # supported path does; treating it as the terminal state rather than a
            # failure keeps the two answers consistent whichever one fires.
            _set_static_status(app, "njt_static_status", "not-configured", exc)
            return
        except Exception as exc:
            attempt = await _fail_and_wait(app, "njt_static_status", exc, attempt)
            continue
        if not data.get("stops"):
            attempt = await _fail_and_wait(
                app,
                "njt_static_status",
                RuntimeError("NJ Transit static GTFS unavailable or empty"),
                attempt,
            )
            continue
        app.state.njt_static = data
        app.state.njt_stops = data["stops"]
        # Routes-per-station index (H5): the field /api/njt-stops merges onto each
        # marker. Port Jervis stations come back carrying MAIN and BERG, which is
        # what the feed actually says (see derive_njt_stop_routes).
        app.state.njt_station_routes = njt_static.derive_njt_stop_routes(
            data["trips"], data["stop_times"]
        )
        # Route lines (15c), built here from the tables the load already parsed, the
        # same place and the same way the railroad block builds its own. Empty when
        # the publication carried no shapes.txt: the group still reaches ready,
        # because lines are additive and stations and trains do not depend on them.
        # .get("shapes"): a zip cached before 15c parses without the table, so the
        # builder sees no geometry and the routes list comes up empty rather than
        # the load failing on a key that a pre-15c cache never had.
        app.state.njt_routes = njt_static.build_njt_route_shapes(
            data["trips"], data.get("shapes") or {}, data["routes"]
        )
        # 15b's join target (trip_id -> route, headsign, train number) and the
        # panel era's scheduled calls per stop. Built here, from the tables the
        # load already parsed, so neither later phase re-reads the zip.
        app.state.njt_trips = njt_static.build_njt_trip_index(data["trips"])
        app.state.njt_stop_schedule = njt_static.build_njt_stop_schedule(
            data["trips"], data["stop_times"]
        )
        _set_static_status(app, "njt_static_status", "ready")
        return
