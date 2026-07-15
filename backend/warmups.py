"""Background static-GTFS warmup tasks: load each static group off the startup
critical path, retrying on failure, and flip its app.state status field.

Depends on main for the subway loaders and STATIC_RETRY_S: those are the names
the tests monkeypatch on the main module (main is the composition root), so the
warmups resolve them through `main.` at call time to keep
`monkeypatch.setattr(main, "load_subway_stops", ...)` and `main.STATIC_RETRY_S`
effective after the split. The railroad/path/ferry static modules are patched at
their own module (monkeypatch.setattr(main.ferry_static, "load_ferry_static", ...)
sets it on the shared module object), so those are imported directly.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI

import ferry_static
import main
import path_static
import railroad_static

# Log through the "main" logger (not __name__) so the transition logs and the
# tests' caplog(logger="main") targeting are unchanged by the split.
logger = logging.getLogger("main")


def _set_static_status(
    app: FastAPI, field: str, status: str, exc: BaseException | None = None
) -> None:
    """Set a static group's status, logging only on a TRANSITION so a long retry
    loop (or the per-poll checks that read this) never spams the log. `field` is
    one of "subway_static_status", "railroad_static_status", or
    "path_static_status"."""
    if getattr(app.state, field, None) == status:
        return
    setattr(app.state, field, status)
    if status == "failed":
        logger.warning("%s failed to load (%s); retrying in %ds", field, exc, main.STATIC_RETRY_S)
    elif status == "ready":
        logger.info("%s ready", field)


async def _warm_subway_static(app: FastAPI) -> None:
    """Load the subway static GTFS (stops, route lines, station markers) in the
    background, retrying every STATIC_RETRY_S until it succeeds. Fills the same
    app.state fields the handlers read, then flips the group to ready. A failed
    attempt leaves stops None, so the poller keeps noting a warming 503 and
    /healthz reports the failure until a retry succeeds."""
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
            _set_static_status(app, "subway_static_status", "failed", exc)
            await asyncio.sleep(main.STATIC_RETRY_S)
            continue
        app.state.subway_stops = stops
        app.state.subway_routes = routes
        app.state.subway_stations = stations
        app.state.subway_station_routes = station_routes
        _set_static_status(app, "subway_static_status", "ready")
        return


async def _warm_railroad_static(app: FastAPI) -> None:
    """Load the railroad static GTFS in the background, retrying every
    STATIC_RETRY_S. load_railroad_static is lenient per system (a download or
    parse failure for one system yields None for it, GPS-only, without raising),
    so the group reaches ready once the attempt COMPLETES even if a system is
    None; only an unexpected raise (never per-system None) drives the failed and
    retry path. Fills the derived stops and route geometry the handlers read."""
    while True:
        try:
            # Whole-attempt deadline (main.STATIC_ATTEMPT_DEADLINE_S): a backstop over
            # the per-transfer asyncio.timeout each downloader already holds, so a
            # wedged attempt cannot stall this retry loop forever. A timeout raises
            # TimeoutError, caught below and retried like any other load failure.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                data = await railroad_static.load_railroad_static()
        except Exception as exc:
            _set_static_status(app, "railroad_static_status", "failed", exc)
            await asyncio.sleep(main.STATIC_RETRY_S)
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
    """Load the PATH static GTFS in the background, retrying every
    STATIC_RETRY_S. load_path_static is lenient (a download or parse failure
    yields {} without raising), and PATH is a SINGLE system: unlike the
    railroad group, which reaches ready with one system None because the other
    still deserves serving, an empty PATH result means the only system failed,
    so the group stays failed and retries. That keeps the endpoint contract
    honest: while failed they serve [] under no-cache ("ask again later"), and
    ready-with-cache-headers is only ever reached with real data."""
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
            _set_static_status(app, "path_static_status", "failed", exc)
            await asyncio.sleep(main.STATIC_RETRY_S)
            continue
        if not data.get("stops"):
            _set_static_status(
                app,
                "path_static_status",
                "failed",
                RuntimeError("PATH static GTFS unavailable or empty"),
            )
            await asyncio.sleep(main.STATIC_RETRY_S)
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
    """Load the NYC Ferry static GTFS in the background, retrying every
    STATIC_RETRY_S. Exactly the PATH single-system pattern: load_ferry_static
    is lenient ({} on any failure, no raise), and an empty result means the
    only system failed, so the group stays failed and retries. That keeps the
    endpoint contract honest: while failed they serve [] under no-cache ("ask
    again later"), and ready-with-cache-headers is only reached with real data.
    ferry_static (the full parsed tables, including the trip -> route map 14b
    needs) is kept on app.state for that later phase to consume without
    re-parsing."""
    while True:
        try:
            # Whole-attempt deadline (main.STATIC_ATTEMPT_DEADLINE_S): a backstop over
            # ferry_static's own per-transfer asyncio.timeout, so a wedged attempt
            # cannot stall this retry loop forever. A timeout raises TimeoutError,
            # caught below and retried like any other load failure.
            async with asyncio.timeout(main.STATIC_ATTEMPT_DEADLINE_S):
                data = await ferry_static.load_ferry_static()
        except Exception as exc:
            _set_static_status(app, "ferry_static_status", "failed", exc)
            await asyncio.sleep(main.STATIC_RETRY_S)
            continue
        if not data.get("stops"):
            _set_static_status(
                app,
                "ferry_static_status",
                "failed",
                RuntimeError("NYC Ferry static GTFS unavailable or empty"),
            )
            await asyncio.sleep(main.STATIC_RETRY_S)
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
