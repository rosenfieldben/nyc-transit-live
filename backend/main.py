"""FastAPI app exposing decoded MTA realtime data (buses + subways + commuter
rail / railroads) as JSON.

The composition root after the wiring split: this module owns app creation, the
lifespan (state init + background tasks), middleware, the router includes, and
the static-file mount. The feed/serving logic lives in sibling modules
(cache, pollers, warmups, routes/); this module RE-EXPORTS every name they moved
so the tests keep patching and calling them on `main` (main._refresh_path,
main._warm_ferry_static, main.fetch_subway_trains, ...) exactly as before. The
pollers and warmups read their swappable feed/loader dependencies back through
`main.` at call time, which is what keeps those monkeypatches effective; see
their module docstrings. __all__ declares that re-export surface (and silences
unused-import warnings for the names main exposes but does not itself call).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

import airtrain_static
import bus_static
import ferry_static
import path_static
import railroad_static

# The moved wiring, re-exported into main's namespace (see the module docstring
# and __all__). cache is a leaf; warmups/pollers read their swappable deps back
# through `main.` at call time, so importing them here before STATIC_RETRY_S is
# defined below is fine. routes are pure APIRouters, included after app creation.
from cache import (
    FEED_STALE_AFTER_S,
    _feed_age,
    _fresh_alerts_entry,
    _fresh_entry,
    _note_failure,
    _require_filled_cache,
    _sanitize_upstream,
    _serve_cached,
    _static_endpoint_ready,
)
from feeds import (
    ALERT_RETENTION_MAX_S,
    fetch_ferry_data,
    fetch_path_trains,
    fetch_railroad_trains,
    fetch_service_alerts,
    fetch_subway_trains,
    fetch_vehicle_positions,
    new_path_identity_state,
)
from pollers import (
    _poll_alerts,
    _poll_feeds,
    _refresh_alerts,
    _refresh_buses,
    _refresh_ferry,
    _refresh_path,
    _refresh_railroads,
    _refresh_subways,
)
from routes import airtrain as airtrain_routes
from routes import buses as buses_routes
from routes import ferry as ferry_routes
from routes import path as path_routes
from routes import railroad as railroad_routes
from routes import status as status_routes
from routes import subway as subway_routes
from static_data import (
    load_subway_route_shapes,
    load_subway_stations,
    load_subway_stops,
)
from warmups import (
    _set_static_status,
    _warm_ferry_static,
    _warm_path_static,
    _warm_railroad_static,
    _warm_subway_static,
)

logger = logging.getLogger(__name__)

# Uvicorn configures its own loggers but leaves the root logger bare, so
# module loggers (feeds, bus_static, static_data) would be invisible. Give
# root a handler if nothing else has; keep root at WARNING so third-party
# INFO noise (e.g. httpx per-request lines) stays out, and opt our modules in.
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s:     %(message)s")
for _mod in (__name__, "feeds", "bus_static", "static_data"):
    logging.getLogger(_mod).setLevel(logging.INFO)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Static GTFS loads in the background, off the startup critical path (the durable
# version of the old _DOWNLOAD_DEADLINE_S stopgap). Each group runs its own warmup
# task with a "loading" -> "ready" | "failed" state machine: on failure it sleeps
# STATIC_RETRY_S and retries until it succeeds or the app shuts down, so a degraded
# network at boot self-heals instead of stranding the map until the next deploy.
# Kept here (the composition root) rather than in warmups, because it is the name
# tests shorten (monkeypatch.setattr(main, "STATIC_RETRY_S", ...)); the warmups
# read main.STATIC_RETRY_S so that patch stays effective.
STATIC_RETRY_S = 300  # module-level so tests can shorten it


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Static GTFS loads in the BACKGROUND (see _warm_subway_static /
    # _warm_railroad_static), so startup returns immediately and never spends
    # Railway's healthcheck window on a download. State starts empty and each
    # group starts "loading"; the warmup tasks fill these fields and flip to
    # "ready" (or "failed" and retry). Until subway static is ready the poller
    # notes a warming 503 and the static endpoints report loading; railroad
    # serves GPS-only trains until its stops arrive.
    app.state.subway_stops = None
    app.state.subway_routes = []
    app.state.subway_stations = {}
    app.state.subway_static_status = "loading"
    app.state.railroad_static = {}  # {system: {stops, trips, shapes, routes} | None}
    app.state.railroad_stops = {}
    app.state.railroad_routes = {}
    app.state.railroad_static_status = "loading"
    # PATH static (13a) feeding the realtime decode and identity matcher (13b,
    # 13d). Own app.state fields, never merged into a shared namespace: numeric
    # PATH stop ids collide with MTA numeric ids across systems, the same
    # reason the alerts join is system-scoped.
    app.state.path_static = {}  # {stops, child_to_parent, trips, shapes, routes, stop_times} or {}
    app.state.path_stops = {}
    app.state.path_routes = []
    # Ordered parent stations per (route_id, direction_id), the advance
    # matcher's successor relation; empty until the warmup builds it (the
    # matcher just never advance-matches meanwhile).
    app.state.path_station_order = {}
    app.state.path_static_status = "loading"
    # 13d synthetic identity state, carried across polls by _refresh_path. The
    # epoch is minted per process so ids can never collide across restarts (a
    # browser holding markers keyed on the old process's ids must not see them
    # silently rebound to unrelated trains). A failed poll leaves the state
    # untouched: failures are not generations, so they neither expire
    # identities nor advance anchors.
    app.state.path_identity = new_path_identity_state(secrets.token_hex(3))
    # NYC Ferry static (14a; realtime is a later phase). Own app.state fields,
    # never merged into a shared namespace: ferry stop ids are short numerics
    # that collide with MTA and PATH ids. ferry_static holds the full parsed
    # tables (including the trip -> route map 14b joins against, since ferry
    # realtime trip descriptors carry an empty route_id).
    app.state.ferry_static = {}  # {stops, trips, shapes, routes} or {}
    app.state.ferry_stops = {}
    app.state.ferry_routes = []
    app.state.ferry_static_status = "loading"
    # AirTrain JFK is a committed static fixture (data/airtrain_jfk.json), not a
    # network download, so it loads SYNCHRONOUSLY here and is ready the instant the
    # server accepts requests (no warmup task, no "loading" state, no 503). Loading
    # it may raise and abort boot, which is intended: unlike the graceful network
    # loaders above, a bad committed fixture is a build bug that must fail loudly.
    app.state.airtrain = airtrain_static.load_airtrain()
    app.state.feed_cache = {
        "buses": _fresh_entry(),
        "subways": _fresh_entry(),
        "railroads": _fresh_entry(),
        "path": _fresh_entry(),
        "ferry": _fresh_entry(),
    }
    # Active service-alerts index, refreshed by its own slower poll (_poll_alerts).
    app.state.alerts_cache = _fresh_alerts_entry()
    # Per-station arrivals index, rebuilt by each successful subway poll.
    app.state.subway_arrivals = {}
    # Same, per railroad system: {"LIRR": {...}, "MNR": {...}}, each system
    # replaced only on a poll where it decoded (a transient failure keeps its
    # last-known arrivals). Empty until the first successful railroad poll.
    app.state.railroad_arrivals = {}
    # Per-station PATH arrivals, rebuilt by each successful PATH poll (empty
    # until the first one; a failed poll keeps the last-known index). Its own
    # field, never merged with the MTA indexes: PATH ids collide numerically.
    app.state.path_arrivals = {}
    # Per-dock NYC Ferry arrivals, rebuilt by each successful ferry poll (empty
    # until the first one; a failed poll keeps the last-known index). Its own
    # field, never merged: ferry ids collide numerically with MTA and PATH ids.
    app.state.ferry_arrivals = {}
    # Per-trip previous-poll position, used to carry a prev interpolation anchor
    # forward when the feed pruned the just-departed stop (see carry_forward_prev).
    app.state.subway_positions = {}
    # Same, for the railroad placements, keyed by (system, trip_id).
    app.state.railroad_positions = {}
    # Per-feed-group health of the most recent subway poll (None until the first
    # poll), surfaced by /api/status so a partial feed outage is visible.
    app.state.subway_feed_health = None
    # Same, for the railroad feeds (LIRR + MNR).
    app.state.railroad_feed_health = None
    # Same, for the single PATH bridge feed.
    app.state.path_feed_health = None
    # Same, for the NYC Ferry realtime feeds (VehiclePositions + TripUpdates,
    # polled as one all-or-nothing feed).
    app.state.ferry_feed_health = None
    # Background warmups: static GTFS (each group retries on failure) and the bus
    # route index. Startup never waits on any of them.
    app.state.subway_static_task = asyncio.create_task(_warm_subway_static(app))
    app.state.railroad_static_task = asyncio.create_task(_warm_railroad_static(app))
    app.state.path_static_task = asyncio.create_task(_warm_path_static(app))
    app.state.ferry_static_task = asyncio.create_task(_warm_ferry_static(app))
    app.state.feed_poll_task = asyncio.create_task(_poll_feeds(app))
    # Service alerts poll on their own slower loop, independent of the position poll.
    app.state.alert_poll_task = asyncio.create_task(_poll_alerts(app))
    # Bus route geometry indexes in the background — startup never waits on
    # the ~52 MB of borough GTFS zips; /api/bus-route reports until ready.
    app.state.bus_index_task = asyncio.create_task(bus_static.ensure_index())
    yield
    # Signal the build thread first: task.cancel() alone can't interrupt the
    # worker thread, and interpreter exit would block joining it otherwise.
    bus_static.stop()
    app.state.bus_index_task.cancel()
    app.state.feed_poll_task.cancel()
    app.state.alert_poll_task.cancel()
    # cancel() during a warmup's retry sleep raises CancelledError inside the
    # sleep, so shutdown never waits out a mid-retry STATIC_RETRY_S.
    app.state.subway_static_task.cancel()
    app.state.railroad_static_task.cancel()
    app.state.path_static_task.cancel()
    app.state.ferry_static_task.cancel()
    # Await all so cleanup (e.g. the poller's client close) finishes before
    # shutdown proceeds; the stop event bounds how long the build task runs.
    for task in (
        app.state.bus_index_task,
        app.state.feed_poll_task,
        app.state.alert_poll_task,
        app.state.subway_static_task,
        app.state.railroad_static_task,
        app.state.path_static_task,
        app.state.ferry_static_task,
    ):
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="NYC Transit Live", version="0.3.0", lifespan=lifespan)

# Feed payloads (thousands of buses, ~450 KB of route geometry) are JSON that
# compresses ~5-10x; only bodies over ~1 KB are worth the CPU.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Content-Security-Policy for the frontend document. default-src 'self' locks every
# resource to same-origin (script-src inherits it, so it stays strict: every script
# is a same-origin file, no inline <script>, no CDN since H2 self-hosted Leaflet).
# The narrow relaxations, each justified:
#   - img-src: the OSM basemap tile origin (frontend/systems/shared.js) plus data:
#     (a common, low-risk allowance; no data: image is used today).
#   - connect-src 'self': the /api/* polling, all same-origin.
#   - style-src adds 'unsafe-inline'. Verified necessary: the app renders inline
#     style="" attributes in its popup/marker HTML (route colors, the arr-badge
#     backgrounds, the bus-bearing SVG rotation), and without 'unsafe-inline' the
#     browser refuses them ("Refused to apply inline style ..."). This relaxes only
#     STYLES, never scripts; tightening it would mean moving those inline styles to
#     classes or CSS custom properties, a rendering refactor left as a followup.
# NO HSTS: Railway terminates TLS, so the app must not assert transport policy above
# the platform.
_CSP = (
    "default-src 'self'; "
    "img-src 'self' data: https://tile.openstreetmap.org; "
    "connect-src 'self'; "
    "style-src 'self' 'unsafe-inline'"
)

# Security headers apply to the browser-rendered frontend (index.html and its static
# assets), not to the JSON APIs or FastAPI's own /docs: a document CSP is meaningless
# on a fetched JSON response and would needlessly break the CDN-backed Swagger UI.
_NON_FRONTEND_PREFIXES = ("/api", "/healthz", "/docs", "/redoc", "/openapi.json")

_SECURITY_HEADERS = {
    "Content-Security-Policy": _CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


@app.middleware("http")
async def security_headers(request, call_next):
    """Set the frontend security headers (CSP + friends) on the HTML/static surface.
    The e2e static server (tests/e2e/serve.js) mirrors these exact values so the
    Playwright browser enforces the same CSP against the real app; keep them in sync."""
    response = await call_next(request)
    if not request.url.path.startswith(_NON_FRONTEND_PREFIXES):
        response.headers.update(_SECURITY_HEADERS)
    return response


# One APIRouter per concern (see routes/); each carries the full /api/* paths, so
# no prefix. Order does not matter (paths are disjoint); the static mount below is
# last so /api/* wins.
app.include_router(buses_routes.router)
app.include_router(subway_routes.router)
app.include_router(railroad_routes.router)
app.include_router(path_routes.router)
app.include_router(ferry_routes.router)
app.include_router(airtrain_routes.router)
app.include_router(status_routes.router)


class RevalidatingStaticFiles(StaticFiles):
    """StaticFiles that asks the browser to revalidate every load.

    The frontend assets are unhashed (no build step) and served under stable
    names (index.html, helpers.js, map.js, style.css), so a long-lived cache
    would serve a stale bundle after a deploy (the symptom this fixes). With
    no-cache the browser keeps the file but revalidates via the ETag and
    Last-Modified StaticFiles already sets, so an unchanged file is a cheap 304
    and a deployed change is picked up immediately.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Mounted last so /api/* routes take priority; html=True serves index.html at /.
app.mount("/", RevalidatingStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

# The re-export surface: every name the tests reach on `main` after the wiring
# split, plus the app/lifespan/logger. Listed so ruff treats the re-export-only
# imports as used and so the module's public interface is explicit. `time`,
# `path_static`, `ferry_static`, `railroad_static` are here because tests patch
# through them (monkeypatch.setattr(main.time, "time", ...),
# monkeypatch.setattr(main.ferry_static, "load_ferry_static", ...)).
__all__ = [
    "app",
    "lifespan",
    "logger",
    "time",
    "path_static",
    "ferry_static",
    "railroad_static",
    "STATIC_RETRY_S",
    "FEED_STALE_AFTER_S",
    "ALERT_RETENTION_MAX_S",
    "fetch_vehicle_positions",
    "fetch_subway_trains",
    "fetch_railroad_trains",
    "fetch_path_trains",
    "fetch_ferry_data",
    "fetch_service_alerts",
    "new_path_identity_state",
    "load_subway_stops",
    "load_subway_stations",
    "load_subway_route_shapes",
    "_fresh_entry",
    "_fresh_alerts_entry",
    "_feed_age",
    "_note_failure",
    "_sanitize_upstream",
    "_serve_cached",
    "_require_filled_cache",
    "_static_endpoint_ready",
    "_set_static_status",
    "_warm_subway_static",
    "_warm_railroad_static",
    "_warm_path_static",
    "_warm_ferry_static",
    "_refresh_buses",
    "_refresh_subways",
    "_refresh_railroads",
    "_refresh_path",
    "_refresh_ferry",
    "_refresh_alerts",
    "_poll_feeds",
    "_poll_alerts",
]
