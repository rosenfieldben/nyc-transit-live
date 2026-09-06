"""F06 (P2), "Partial static startup failures persist until restart": reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, verbatim):

    "Railroad warmup retries when both systems fail. If one succeeds, it marks the
     aggregate `ready` and terminates. Injecting a first-call-only Metro-North
     failure left `MNR_stops=None`, aggregate status `ready`, and exactly one
     attempt per system, even after the shortened retry interval elapsed. Bus
     indexing likewise terminated after one injected failure although the second
     call was programmed to succeed. Partial bus builds also rely on a later
     startup for another build."

    Evidence links: backend/warmups.py L192, backend/bus_static.py L301,
    backend/main.py L323.

WHAT THIS SCRIPT MEASURES, AND HOW.

  Part A (warmups.py, the railroad warmup). Production code: the real
  warmups._warm_railroad_static loop, the real railroad_static.load_railroad_static
  gather, the real railroad_static._load_one (cache validation, freshness, parse),
  the real archive validators, run as an asyncio task exactly the way
  backend/main.py line 324 schedules it. Injected: (1) two hand-built minimal GTFS
  zips in a temp dir, with railroad_static.RAILROAD_STATIC_ZIPS pointed at them, so
  nothing downloads; LIRR's is present and valid before the run, MNR's is absent;
  (2) railroad_static._download_zip is replaced by a fake that RAISES on its first
  MNR call and WRITES a valid MNR archive on every later call, so the second
  attempt was programmed to succeed; (3) a counting wrapper around the real
  _load_one, which is how attempts per system are counted; (4) main.STATIC_RETRY_S
  shortened to 0.01s through the documented monkeypatch seam (warmups._rung caps
  every schedule rung at it), so a 1.0s observation window spans roughly 100 retry
  intervals. No module source is edited. A control run, where BOTH systems fail,
  measures how many retries that same window actually produces.

  Part B (bus_static.py, the index task). Production code: the real
  bus_static.ensure_index, the real _build_index_sync, _download_borough and
  _process_zip. Injected: a temp cache dir, an httpx.MockTransport serving
  hand-built borough zips (bus_static's httpx reference is swapped for a shim whose
  Client() carries that transport, so no socket is opened), and a counting wrapper
  around _build_index_sync that raises once and then delegates to the real build,
  which under the healthy transport would succeed.

  Part C (what "a later startup" means). Same production build path, with two of
  the six borough downloads injected as HTTP 503. Measures which operators are
  missing, what the manifest records, and what it takes to get the missing ones.

  Part D (main.py, startup scheduling). Runtime: the REAL main.lifespan is driven
  as an async context manager with the warmups/pollers/bus index replaced by
  instant no-op coroutines and asyncio.create_task spied on, so every task the
  startup creates is counted. Static: main.lifespan's own source is parsed with ast,
  and the whole backend package is scanned for other create_task sites and for any
  add_done_callback.

NO NJ TRANSIT ANYTHING. No NJT module is imported by this script's own code paths,
no NJT function is called, and the three credential variables that backend/.env
may carry are deleted from the environment after the imports. Nothing here can
reach raildata.njtransit.com or spend a mint.

Fully hermetic and deterministic: no network, no live feed, no dependence on
today's date. Every archive it reads it wrote itself, seconds earlier.

RUN:
  .venv/bin/python docs/reviews/audit-2026-09-05/f06_partial_startup_no_recovery.py

Exits 0 while F06 still behaves as recorded, non-zero (with the failed assertion
named) if the code has changed underneath the audit record.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import inspect
import io
import json
import logging
import os
import sys
import tempfile
import time
import types
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. The pop below this used to
# be the whole scrub and it was not one: env_seams calls load_dotenv when it is
# imported, which refills any credential the pop removed. The addresses set here are
# what make this process unable to reach NJ Transit at all, credentials or not. See
# _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

import bus_static  # noqa: E402
import main  # noqa: E402
import railroad_static  # noqa: E402
import warmups  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


# The backend modules call load_dotenv at import, which copies backend/.env (if a
# developer has one) into os.environ. Nothing below touches NJ Transit, but the
# variables are removed anyway so no code path reached from here could mint.
# BLANKED, NOT POPPED. A pop deletes the key, and load_dotenv fills deleted keys:
# this backend loads the .env twice (env_seams and feeds.shared), so a pop here was
# undone by whichever import came next. See _hermetic.blank.
_hermetic.blank("NJT_USERNAME", "NJT_PASSWORD", "BUS_TIME_API_KEY")


# --------------------------------------------------------------------------
# Log capture. The warmups log through logging.getLogger("main"); capturing
# instead of printing keeps the retry loop's warnings out of the report while
# still letting the script count them as evidence.
# --------------------------------------------------------------------------
class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        out = []
        for r in self.records:
            try:
                out.append(r.getMessage())
            except Exception:
                out.append(str(r.msg))
        return out


CAP = _Capture()
# backend/main.py calls logging.basicConfig at import, so the root logger already
# prints. Replace its handlers with the capture for the length of this run: the
# retry loop below logs a few hundred lines, and the ones that matter as evidence
# are printed back in the report blocks instead.
_ROOT = logging.getLogger()
_SAVED_HANDLERS = list(_ROOT.handlers)
_ROOT.handlers = [CAP]
_ROOT.setLevel(logging.INFO)

RETRY_CEILING_S = 0.01  # main.STATIC_RETRY_S for the run; caps every backoff rung
OBSERVE_S = 1.0  # how long we watch a finished warmup for a retry it never makes

FAILURES: list[str] = []


def check(label: str, condition: bool) -> bool:
    """Record one assertion of the audit record without aborting the run, so the
    report prints every measured number before the script exits non-zero."""
    if not condition:
        FAILURES.append(label)
    return condition


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# Minimal railroad GTFS archives (test data, not logic under test)
# --------------------------------------------------------------------------
STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
TRIPS_COLS = ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]
ROUTES_COLS = ["route_id", "route_short_name", "route_long_name", "route_color"]


def csv_text(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def write_rail_zip(path: Path, system: str) -> None:
    """A minimal but genuinely valid archive for one railroad system: it passes
    railroad_static.validate_railroad_archive and parses through _parse_system."""
    stops = [
        {"stop_id": f"{system}1", "stop_name": f"{system} Terminal", "stop_lat": "40.7506",
         "stop_lon": "-73.9935"},
        {"stop_id": f"{system}2", "stop_name": f"{system} Outbound", "stop_lat": "40.8000",
         "stop_lon": "-73.9000"},
    ]
    trips = [
        {"route_id": f"{system}R", "service_id": "A", "trip_id": f"{system}_t1",
         "trip_headsign": "Outbound", "direction_id": "0", "shape_id": f"{system}_s1"},
    ]
    shapes = [
        {"shape_id": f"{system}_s1", "shape_pt_sequence": "1", "shape_pt_lat": "40.7506",
         "shape_pt_lon": "-73.9935"},
        {"shape_id": f"{system}_s1", "shape_pt_sequence": "2", "shape_pt_lat": "40.8000",
         "shape_pt_lon": "-73.9000"},
    ]
    routes = [
        {"route_id": f"{system}R", "route_short_name": "", "route_long_name": f"{system} Line",
         "route_color": "00B2A9"},
    ]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("stops.txt", csv_text(STOPS_COLS, stops))
        zf.writestr("trips.txt", csv_text(TRIPS_COLS, trips))
        zf.writestr("shapes.txt", csv_text(SHAPES_COLS, shapes))
        zf.writestr("routes.txt", csv_text(ROUTES_COLS, routes))


# --------------------------------------------------------------------------
# Part A: the railroad warmup
# --------------------------------------------------------------------------
async def part_a(tmp: Path) -> dict:
    rule("PART A  backend/warmups.py:192  _warm_railroad_static, partial load")

    saved = {
        "zips": railroad_static.RAILROAD_STATIC_ZIPS,
        "download": railroad_static._download_zip,
        "load_one": railroad_static._load_one,
        "retry_s": main.STATIC_RETRY_S,
    }
    real_load_one = saved["load_one"]

    zips = {"LIRR": tmp / "gtfs_lirr.zip", "MNR": tmp / "gtfs_mnr.zip"}
    railroad_static.RAILROAD_STATIC_ZIPS = zips
    main.STATIC_RETRY_S = RETRY_CEILING_S

    load_calls: dict[str, int] = {"LIRR": 0, "MNR": 0}
    download_calls: dict[str, int] = {"LIRR": 0, "MNR": 0}
    aggregate_calls = {"n": 0}

    async def counting_load_one(system: str):
        load_calls[system] += 1
        if system == "LIRR":
            aggregate_calls["n"] += 1  # one per load_railroad_static gather
        return await real_load_one(system)

    async def fake_download(system: str) -> None:
        """The injected fault, and only here. Metro-North's FIRST download raises;
        every later call publishes a valid archive, so a second attempt would have
        recovered the system."""
        download_calls[system] += 1
        if system == "MNR" and download_calls["MNR"] == 1:
            raise RuntimeError("injected first-call-only Metro-North download failure")
        write_rail_zip(zips[system], system)

    railroad_static._load_one = counting_load_one
    railroad_static._download_zip = fake_download

    try:
        # LIRR starts with a valid, recently written cache; MNR starts with nothing.
        write_rail_zip(zips["LIRR"], "LIRR")
        zips["MNR"].unlink(missing_ok=True)

        app = FastAPI()
        app.state.railroad_static_status = "loading"  # what main.lifespan sets

        before = len(CAP.records)
        started = time.monotonic()
        # The exact call backend/main.py line 324 makes.
        task = asyncio.create_task(warmups._warm_railroad_static(app))
        await asyncio.wait_for(task, timeout=10)
        settle_s = time.monotonic() - started

        at_settle = dict(load_calls)
        rung0 = warmups._rung(0)
        await asyncio.sleep(OBSERVE_S)
        after_wait = dict(load_calls)
        new_logs = CAP.messages()[before:]

        stops = app.state.railroad_stops
        routes = app.state.railroad_routes
        status = app.state.railroad_static_status

        print(f"  retry ceiling main.STATIC_RETRY_S      : {main.STATIC_RETRY_S}s")
        print(f"  first backoff rung warmups._rung(0)    : {rung0}s")
        print(f"  warmup task settled after              : {settle_s * 1000:.1f} ms")
        print(f"  observation window after it settled    : {OBSERVE_S}s "
              f"(= {OBSERVE_S / rung0:.0f} retry intervals)")
        print()
        print(f"  aggregate load_railroad_static calls   : {aggregate_calls['n']}")
        print(f"  _load_one attempts at settle           : LIRR={at_settle['LIRR']}  "
              f"MNR={at_settle['MNR']}")
        print(f"  _load_one attempts after the window    : LIRR={after_wait['LIRR']}  "
              f"MNR={after_wait['MNR']}")
        print(f"  _download_zip calls                    : LIRR={download_calls['LIRR']}  "
              f"MNR={download_calls['MNR']}  (LIRR served from its valid cache)")
        print()
        print(f"  app.state.railroad_static_status       : {status!r}")
        print(f"  railroad_stops['LIRR']                 : "
              f"{len(stops['LIRR'])} stops {sorted(stops['LIRR'])}")
        print(f"  railroad_stops['MNR']                  : {stops['MNR']!r}")
        print(f"  railroad_routes['MNR']                 : {routes['MNR']!r}")
        print(f"  railroad_station_routes['MNR']         : {app.state.railroad_station_routes['MNR']!r}")
        print(f"  warmup task done()                     : {task.done()}")
        print()
        print("  log records emitted by the warmup:")
        for m in new_logs:
            print(f"    | {m}")

        ok = True
        ok &= check("A: aggregate status is ready", status == "ready")
        ok &= check("A: MNR stops are None", stops["MNR"] is None)
        ok &= check("A: MNR routes are empty", routes["MNR"] == [])
        ok &= check("A: LIRR loaded 2 stops", len(stops["LIRR"]) == 2)
        ok &= check("A: exactly one attempt per system at settle",
                    at_settle == {"LIRR": 1, "MNR": 1})
        ok &= check("A: still exactly one attempt per system after the window",
                    after_wait == {"LIRR": 1, "MNR": 1})
        ok &= check("A: MNR was never re-downloaded", download_calls["MNR"] == 1)
        ok &= check("A: the warmup task terminated", task.done())
        ok &= check("A: no failure was logged for the partial load",
                    not [m for m in new_logs if "railroad_static_status failed" in m])

        # ---- control: prove the window really does contain many retries ----
        print()
        print("  CONTROL (both systems failing, same shortened interval and window):")
        load_calls["LIRR"] = load_calls["MNR"] = 0
        download_calls["LIRR"] = download_calls["MNR"] = 0
        aggregate_calls["n"] = 0
        gate = {"open": False}

        async def gated_download(system: str) -> None:
            download_calls[system] += 1
            if not gate["open"]:
                raise RuntimeError("injected outage for both systems")
            write_rail_zip(zips[system], system)

        railroad_static._download_zip = gated_download
        zips["LIRR"].unlink(missing_ok=True)
        zips["MNR"].unlink(missing_ok=True)

        app2 = FastAPI()
        app2.state.railroad_static_status = "loading"
        ctask = asyncio.create_task(warmups._warm_railroad_static(app2))
        await asyncio.sleep(OBSERVE_S)
        control_attempts = dict(load_calls)
        control_status = app2.state.railroad_static_status
        gate["open"] = True
        for _ in range(400):
            if app2.state.railroad_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        control_final = app2.state.railroad_static_status
        ctask.cancel()
        await asyncio.gather(ctask, return_exceptions=True)

        print(f"    attempts inside the SAME {OBSERVE_S}s window : "
              f"LIRR={control_attempts['LIRR']}  MNR={control_attempts['MNR']}")
        print(f"    status while both were failing           : {control_status!r}")
        print(f"    status once the outage cleared           : {control_final!r}")
        ok &= check("A-control: the all-fail loop retries many times in the same window",
                    min(control_attempts.values()) >= 10)
        ok &= check("A-control: all-fail is marked failed, not ready",
                    control_status == "failed")
        ok &= check("A-control: all-fail recovers by itself", control_final == "ready")

        return {
            "ok": ok,
            "attempts_at_settle": at_settle,
            "attempts_after_window": after_wait,
            "downloads": dict(download_calls),
            "status": status,
            "mnr_stops": stops["MNR"],
            "control_attempts": control_attempts,
            "rung0": rung0,
        }
    finally:
        railroad_static.RAILROAD_STATIC_ZIPS = saved["zips"]
        railroad_static._download_zip = saved["download"]
        railroad_static._load_one = saved["load_one"]
        main.STATIC_RETRY_S = saved["retry_s"]


# --------------------------------------------------------------------------
# Borough GTFS archives and a mock transport for the bus index
# --------------------------------------------------------------------------
BUS_TRIPS_COLS = ["route_id", "service_id", "trip_id", "direction_id", "shape_id"]
BUS_SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]

ROUTES_BY_BOROUGH = {
    "manhattan": ["M1", "M2"],
    "brooklyn": ["B1"],
    "bronx": ["BX1"],
    "queens": ["Q1"],
    "staten_island": ["S1", "S2"],
    "mta_bus_co": ["QM1"],
}


def borough_zip_bytes(key: str) -> bytes:
    trips, shapes = [], []
    for i, route in enumerate(ROUTES_BY_BOROUGH[key]):
        shape_id = f"{route}_sh"
        trips.append({"route_id": route, "service_id": "A", "trip_id": f"{route}_t1",
                      "direction_id": "0", "shape_id": shape_id})
        for seq in (1, 2):
            shapes.append({"shape_id": shape_id, "shape_pt_sequence": str(seq),
                           "shape_pt_lat": f"{40.70 + i / 100 + seq / 1000:.5f}",
                           "shape_pt_lon": f"{-73.90 - i / 100 - seq / 1000:.5f}"})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("trips.txt", csv_text(BUS_TRIPS_COLS, trips))
        zf.writestr("shapes.txt", csv_text(BUS_SHAPES_COLS, shapes))
    return buf.getvalue()


BOROUGH_ZIPS = {key: borough_zip_bytes(key) for key in ROUTES_BY_BOROUGH}
KEY_BY_PATH = {httpx.URL(url).path: key for key, url in bus_static.BUS_GTFS_URLS.items()}


def install_bus_transport(failing: set[str]) -> None:
    """Swap bus_static's httpx reference for a shim whose Client carries a
    MockTransport. The real httpx.Client, streaming, raise_for_status and chunk
    loop all still run; only the socket is gone."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = KEY_BY_PATH.get(request.url.path)
        if key is None:
            return httpx.Response(404, text="unknown borough url")
        if key in failing:
            return httpx.Response(503, text=f"injected {key} borough failure")
        return httpx.Response(200, content=BOROUGH_ZIPS[key])

    shim = types.SimpleNamespace(
        Client=lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    bus_static.httpx = shim


def reset_bus_module(cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    for f in cache.glob("*"):
        f.unlink()
    bus_static.BUS_CACHE_DIR = cache
    bus_static.MANIFEST_PATH = cache / "_manifest.json"
    bus_static._status = "missing"
    bus_static._partial = False
    bus_static._stop.clear()


# --------------------------------------------------------------------------
# Part B: the bus index task stops after one failure
# --------------------------------------------------------------------------
async def part_b(tmp: Path) -> dict:
    rule("PART B  backend/bus_static.py:301  ensure_index, one failed build")

    saved = {
        "cache": bus_static.BUS_CACHE_DIR,
        "manifest": bus_static.MANIFEST_PATH,
        "httpx": bus_static.httpx,
        "build": bus_static._build_index_sync,
        "status": bus_static._status,
        "partial": bus_static._partial,
    }
    real_build = saved["build"]
    cache = tmp / "bus_b"
    try:
        reset_bus_module(cache)
        install_bus_transport(failing=set())  # every borough healthy: a build WOULD work
        builds = {"n": 0}

        def counting_build():
            builds["n"] += 1
            if builds["n"] == 1:
                raise RuntimeError("injected first-call-only bus index build failure")
            return real_build()  # programmed to succeed, with all six boroughs healthy

        bus_static._build_index_sync = counting_build

        before = len(CAP.records)
        started = time.monotonic()
        await bus_static.ensure_index()  # the exact call backend/main.py line 337 makes
        settle_s = time.monotonic() - started
        builds_at_settle = builds["n"]

        await asyncio.sleep(OBSERVE_S)
        builds_after = builds["n"]
        status_after = bus_static.status()
        route_files = sorted(p.name for p in cache.glob("*.json"))
        logs = CAP.messages()[before:]

        print(f"  ensure_index returned after            : {settle_s * 1000:.1f} ms")
        print(f"  _build_index_sync calls at return      : {builds_at_settle}")
        print(f"  _build_index_sync calls after {OBSERVE_S}s idle : {builds_after}")
        print(f"  bus_static.status()                    : {status_after!r}")
        print(f"  bus_static.is_partial()                : {bus_static.is_partial()}")
        print(f"  route geometry files written           : {route_files}")
        print(f"  manifest exists                        : {bus_static.MANIFEST_PATH.exists()}")
        print("  log records:")
        for m in logs:
            print(f"    | {m}")

        ok = True
        ok &= check("B: exactly one build attempt", builds_at_settle == 1)
        ok &= check("B: no second build ever happens on its own", builds_after == 1)
        ok &= check("B: status is failed", status_after == "failed")
        ok &= check("B: no route geometry was produced", route_files == [])

        # The second call was genuinely programmed to succeed: prove it by making
        # the call the application never makes.
        print()
        print("  Same process, ensure_index() invoked a SECOND time by hand")
        print("  (this is what a restart would do; nothing in the app does it):")
        await bus_static.ensure_index()
        recovered_status = bus_static.status()
        recovered_files = sorted(p.stem for p in cache.glob("*.json") if p.stem != "_manifest")
        print(f"    _build_index_sync calls               : {builds['n']}")
        print(f"    bus_static.status()                   : {recovered_status!r}")
        print(f"    routes now on disk                    : {recovered_files}")
        ok &= check("B: the programmed second build does succeed when invoked",
                    builds["n"] == 2 and recovered_status == "ready")
        ok &= check("B: the second build produces all eight routes",
                    len(recovered_files) == 8)

        return {"ok": ok, "builds_at_settle": builds_at_settle, "builds_after": builds_after,
                "status": status_after, "recovered_status": recovered_status,
                "recovered_routes": len(recovered_files)}
    finally:
        bus_static.BUS_CACHE_DIR = saved["cache"]
        bus_static.MANIFEST_PATH = saved["manifest"]
        bus_static.httpx = saved["httpx"]
        bus_static._build_index_sync = saved["build"]
        bus_static._status = saved["status"]
        bus_static._partial = saved["partial"]


# --------------------------------------------------------------------------
# Part C: what a partial bus build leaves behind, and what repairs it
# --------------------------------------------------------------------------
async def part_c(tmp: Path) -> dict:
    rule("PART C  what 'partial builds rely on a later startup' means concretely")

    saved = {
        "cache": bus_static.BUS_CACHE_DIR,
        "manifest": bus_static.MANIFEST_PATH,
        "httpx": bus_static.httpx,
        "build": bus_static._build_index_sync,
        "status": bus_static._status,
        "partial": bus_static._partial,
    }
    real_build = saved["build"]
    cache = tmp / "bus_c"
    failing = {"staten_island", "mta_bus_co"}
    try:
        reset_bus_module(cache)
        install_bus_transport(failing=failing)
        builds = {"n": 0}

        def counting_build():
            builds["n"] += 1
            return real_build()

        bus_static._build_index_sync = counting_build

        print("  the six downloads a build performs (bus_static.BUS_GTFS_URLS):")
        for key, url in bus_static.BUS_GTFS_URLS.items():
            mark = "INJECTED FAILURE" if key in failing else "healthy"
            print(f"    {key:<15} {httpx.URL(url).path:<16} {mark}")
        print(f"  rebuild age policy bus_static.MAX_AGE_DAYS : {bus_static.MAX_AGE_DAYS} days")
        print()

        before = len(CAP.records)
        await bus_static.ensure_index()
        manifest = json.loads(bus_static.MANIFEST_PATH.read_text(encoding="utf-8"))
        on_disk = sorted(p.stem for p in cache.glob("*.json") if p.stem != "_manifest")
        missing = sorted(r for key in failing for r in ROUTES_BY_BOROUGH[key])
        geometry_probe = {r: bus_static.get_route_geometry(r) for r in missing}
        logs = CAP.messages()[before:]

        print(f"  first build: _build_index_sync calls   : {builds['n']}")
        print(f"  bus_static.status()                    : {bus_static.status()!r}")
        print(f"  bus_static.is_partial()                : {bus_static.is_partial()}")
        print(f"  manifest['failed']                     : {manifest['failed']}")
        print(f"  manifest['routes'] ({len(manifest['routes'])})".ljust(41)
              + f": {manifest['routes']}")
        print(f"  route files on disk ({len(on_disk)})".ljust(41) + f": {on_disk}")
        print(f"  routes of the failed operators ({len(missing)})".ljust(41) + f": {missing}")
        print(f"  get_route_geometry for each of those   : "
              f"{ {k: v for k, v in geometry_probe.items()} }")
        print("  log records:")
        for m in logs:
            print(f"    | {m}")

        ok = True
        ok &= check("C: the partial build is reported ready", bus_static.status() == "ready")
        ok &= check("C: is_partial() flags the gap", bus_static.is_partial() is True)
        ok &= check("C: the manifest names the failed operators",
                    manifest["failed"] == ["staten_island", "mta_bus_co"])
        ok &= check("C: only the healthy operators' routes exist",
                    on_disk == ["B1", "BX1", "M1", "M2", "Q1"])
        ok &= check("C: every missing route serves no geometry",
                    all(v is None for v in geometry_probe.values()))

        # Nothing inside the running process repairs it.
        await asyncio.sleep(OBSERVE_S)
        print()
        print(f"  after {OBSERVE_S}s of the same process running:")
        print(f"    _build_index_sync calls               : {builds['n']}")
        print(f"    get_route_geometry('S1')              : "
              f"{bus_static.get_route_geometry('S1')!r}")
        ok &= check("C: no rebuild happens inside the process", builds["n"] == 1)
        ok &= check("C: the missing operator stays missing",
                    bus_static.get_route_geometry("S1") is None)

        # A LATER STARTUP: a new lifespan calls ensure_index again. The partial
        # manifest is younger than MAX_AGE_DAYS, so it is served AND rebuilt.
        print()
        print("  simulating the LATER STARTUP (a second ensure_index, boroughs repaired):")
        install_bus_transport(failing=set())
        before2 = len(CAP.records)
        await bus_static.ensure_index()
        manifest2 = json.loads(bus_static.MANIFEST_PATH.read_text(encoding="utf-8"))
        on_disk2 = sorted(p.stem for p in cache.glob("*.json") if p.stem != "_manifest")
        logs2 = CAP.messages()[before2:]
        print(f"    _build_index_sync calls               : {builds['n']}")
        print(f"    bus_static.status() / is_partial()    : {bus_static.status()!r} / "
              f"{bus_static.is_partial()}")
        print(f"    manifest['failed']                    : {manifest2['failed']}")
        print(f"    route files on disk ({len(on_disk2)})".ljust(43) + f": {on_disk2}")
        print(f"    get_route_geometry('S1') is not None  : "
              f"{bus_static.get_route_geometry('S1') is not None}")
        print("    log records:")
        for m in logs2:
            print(f"      | {m}")

        ok &= check("C: the later startup rebuilds", builds["n"] == 2)
        ok &= check("C: the later startup repairs the gap",
                    manifest2["failed"] == [] and len(on_disk2) == 8
                    and bus_static.is_partial() is False)

        return {"ok": ok, "failed_operators": manifest["failed"],
                "routes_partial": len(on_disk), "routes_full": len(on_disk2),
                "builds": builds["n"]}
    finally:
        bus_static.BUS_CACHE_DIR = saved["cache"]
        bus_static.MANIFEST_PATH = saved["manifest"]
        bus_static.httpx = saved["httpx"]
        bus_static._build_index_sync = saved["build"]
        bus_static._status = saved["status"]
        bus_static._partial = saved["partial"]


# --------------------------------------------------------------------------
# Part D: startup scheduling, and the absence of any re-scheduling
# --------------------------------------------------------------------------
async def _stub_warm_subway_static(app):
    return None


async def _stub_warm_railroad_static(app):
    return None


async def _stub_warm_path_static(app):
    return None


async def _stub_warm_ferry_static(app):
    return None


async def _stub_warm_njt_static(app):
    return None


async def _stub_poll_feeds(app):
    return None


async def _stub_poll_alerts(app):
    return None


async def _stub_ensure_index():
    return None


class _AsyncioSpy:
    """Proxies the asyncio module, recording every create_task the lifespan makes."""

    def __init__(self, log: list[str], labels: dict) -> None:
        self._log = log
        self._labels = labels

    def __getattr__(self, name):
        return getattr(asyncio, name)

    def create_task(self, coro, *a, **kw):
        self._log.append(self._labels.get(coro.cr_code, coro.cr_code.co_qualname))
        return asyncio.create_task(coro, *a, **kw)


async def part_d() -> dict:
    rule("PART D  backend/main.py:323  startup scheduling, and no re-scheduling")

    stubs = {
        "_warm_subway_static": _stub_warm_subway_static,
        "_warm_railroad_static": _stub_warm_railroad_static,
        "_warm_path_static": _stub_warm_path_static,
        "_warm_ferry_static": _stub_warm_ferry_static,
        "_warm_njt_static": _stub_warm_njt_static,
        "_poll_feeds": _stub_poll_feeds,
        "_poll_alerts": _stub_poll_alerts,
    }
    labels = {fn.__code__: name for name, fn in stubs.items()}
    labels[_stub_ensure_index.__code__] = "bus_static.ensure_index"

    saved_main = {name: getattr(main, name) for name in stubs}
    saved_ensure = bus_static.ensure_index
    saved_asyncio = main.asyncio
    created: list[str] = []
    try:
        for name, fn in stubs.items():
            setattr(main, name, fn)
        bus_static.ensure_index = _stub_ensure_index
        main.asyncio = _AsyncioSpy(created, labels)

        app = FastAPI()
        async with main.lifespan(app):
            await asyncio.sleep(0.05)  # every stub task has finished by now
            during_startup = list(created)
            finished = {
                "subway": app.state.subway_static_task.done(),
                "railroad": app.state.railroad_static_task.done(),
                "path": app.state.path_static_task.done(),
                "ferry": app.state.ferry_static_task.done(),
                "njt": app.state.njt_static_task.done(),
                "bus_index": app.state.bus_index_task.done(),
            }
            await asyncio.sleep(0.3)  # 30 shortened retry intervals of doing nothing
            after_idle = list(created)
    finally:
        main.asyncio = saved_asyncio
        for name, fn in saved_main.items():
            setattr(main, name, fn)
        bus_static.ensure_index = saved_ensure
        bus_static._stop.clear()  # lifespan shutdown set it

    counts: dict[str, int] = {}
    for name in during_startup:
        counts[name] = counts.get(name, 0) + 1

    print("  RUNTIME: tasks created by one real main.lifespan startup")
    for name in sorted(counts):
        print(f"    {name:<28} created {counts[name]} time(s)")
    print(f"    total create_task calls      : {len(during_startup)}")
    print(f"    every warmup task done()     : {finished}")
    print(f"    create_task calls after they finished and 0.3s passed: "
          f"{len(after_idle) - len(during_startup)}")

    ok = True
    ok &= check("D: each warmup is scheduled exactly once",
                all(counts.get(n) == 1 for n in stubs))
    ok &= check("D: the bus index is scheduled exactly once",
                counts.get("bus_static.ensure_index") == 1)
    ok &= check("D: every warmup task ran to completion", all(finished.values()))
    ok &= check("D: nothing is scheduled again after they complete",
                after_idle == during_startup)

    # Static structure: the same claim read off the source.
    src = inspect.getsource(main.lifespan)
    tree = ast.parse(src)
    scheduled: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "create_task" and node.args:
            inner = node.args[0]
            if isinstance(inner, ast.Call):
                f = inner.func
                scheduled.append(f.id if isinstance(f, ast.Name)
                                 else f"{getattr(f.value, 'id', '?')}.{f.attr}")
    print()
    print("  STATIC: asyncio.create_task calls in main.lifespan's own source")
    print(f"    {scheduled}")

    watched = ("_warm_subway_static", "_warm_railroad_static", "_warm_path_static",
               "_warm_ferry_static", "_warm_njt_static", "bus_static.ensure_index")
    ok &= check("D: source shows one create_task per warmup",
                all(scheduled.count(w) == 1 for w in watched))

    # No other scheduler and no completion hook anywhere in the backend package.
    other_sites: list[str] = []
    done_callbacks: list[str] = []
    for py in sorted(BACKEND.glob("*.py")) + sorted(BACKEND.glob("*/*.py")):
        if "tests" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), start=1):
            if "add_done_callback" in line:
                done_callbacks.append(f"{py.relative_to(REPO)}:{i}")
            if "create_task(" in line and any(w.split(".")[-1] in line for w in watched):
                where = f"{py.relative_to(REPO)}:{i}"
                if py.name != "main.py":
                    other_sites.append(where)
    print(f"    create_task sites for these tasks outside main.py : {other_sites}")
    print(f"    add_done_callback anywhere in the backend package : {done_callbacks}")
    ok &= check("D: main.lifespan is the only scheduler", other_sites == [])
    ok &= check("D: no completion hook re-arms a finished task", done_callbacks == [])

    return {"ok": ok, "counts": counts, "rescheduled": len(after_idle) - len(during_startup)}


# --------------------------------------------------------------------------
async def amain() -> int:
    with tempfile.TemporaryDirectory(prefix="f06_") as td:
        tmp = Path(td)
        a = await part_a(tmp)
        b = await part_b(tmp)
        c = await part_c(tmp)
        d = await part_d()

    rule("SUMMARY  measured numbers")
    print(f"  (a) railroad partial startup, Metro-North failing once")
    print(f"      attempts per system, at settle and after {OBSERVE_S}s "
          f"({OBSERVE_S / a['rung0']:.0f} retry intervals):")
    print(f"        LIRR {a['attempts_at_settle']['LIRR']} -> "
          f"{a['attempts_after_window']['LIRR']}     "
          f"MNR {a['attempts_at_settle']['MNR']} -> {a['attempts_after_window']['MNR']}")
    print(f"      aggregate status {a['status']!r}, railroad_stops['MNR'] "
          f"{a['mnr_stops']!r}")
    print(f"      control (both systems failing, same window): "
          f"LIRR {a['control_attempts']['LIRR']} attempts, "
          f"MNR {a['control_attempts']['MNR']} attempts")
    print(f"  (b) bus index build attempts: {b['builds_at_settle']} at return, "
          f"{b['builds_after']} after {OBSERVE_S}s idle; status {b['status']!r}")
    print(f"      the programmed second build, invoked by hand: {b['recovered_status']!r} "
          f"with {b['recovered_routes']} routes")
    print(f"  (c) partial build: failed operators {c['failed_operators']}, "
          f"{c['routes_partial']}/{c['routes_full']} routes present; "
          f"repaired only by a second ensure_index")
    print(f"  (d) tasks created by one startup: "
          f"{ {k: v for k, v in sorted(d['counts'].items())} }; "
          f"re-scheduled afterwards: {d['rescheduled']}")

    print()
    if FAILURES:
        print("REALITY HAS CHANGED. These recorded behaviors no longer hold:")
        for f in FAILURES:
            print(f"  - {f}")
        print()
        print("DISPOSITION: FAILED TO CONFIRM THE RECORD (see the list above)")
        return 1

    print("DISPOSITION: VERIFIED  A partial railroad warmup (LIRR cached, one injected "
          "Metro-North download failure) reaches status 'ready' with MNR stops None after "
          "exactly 1 attempt per system, and makes no further attempt across "
          f"{OBSERVE_S / a['rung0']:.0f} shortened retry intervals, while the both-systems-fail "
          f"control retries {a['control_attempts']['MNR']} times in the same window; "
          "ensure_index runs _build_index_sync exactly once and never reaches the "
          "second build that was programmed to succeed; a partial build leaves "
          "staten_island and mta_bus_co missing (5 of 8 routes) until another "
          "ensure_index, which only a new startup makes; and main.lifespan schedules "
          "each warmup exactly once with no re-scheduling and no done callback.")
    return 0


if __name__ == "__main__":
    try:
        _code = asyncio.run(amain())
    finally:
        _ROOT.handlers = _SAVED_HANDLERS
    sys.exit(_code)
