"""F07 (P2), "Static schedules do not refresh after successful warmup": reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, verbatim):

    "Cache-age and service-calendar checks run when loaders execute. Successful
     warmup tasks return permanently. A long-lived process can cross an archive-age
     threshold or a schedule's validity boundary without reloading, revalidating or
     attempting a new publication."

    Evidence links: backend/main.py L323 (startup-only tasks), backend/warmups.py
    L184 (successful warmup returns), backend/njt_static.py L968 (NJT load-time
    age/validity checks).

WHAT THIS SCRIPT MEASURES, AND HOW.

  Part A, the task census (production: backend/main.py's real lifespan).
    The REAL main.lifespan is entered as an async context manager, so every
    background task the app starts is created by production code. Afterwards the
    script enumerates app.state's own task attributes (app.state._state, the live
    dictionary, not a list typed out from reading main.py) and asks each task
    whether it is done. Then the app is left running for dozens of real poll
    cycles and the per-loader execution counts are read again.
    Injected: every static loader and every feed refresher is replaced by a
    counting no-op coroutine, and pollers.POLL_INTERVAL_S is shortened, so the run
    is hermetic and fast. The lifespan, the warmup state machines, the poll loops
    and the task bookkeeping are all real.

  Part B, the thresholds (production: the six loaders' own constants).
    Reads MAX_AGE_DAYS out of each static module and prints the arithmetic, and
    reads the NJT service-calendar rule out of the real validator by running it.

  Part C, the NJT boundary, driven through the real loader.
    Production code: warmups._warm_njt_static, njt_static.load_njt_static,
    cached_archive_is_valid, validate_njt_archive, validate_njt_publication,
    static_shared.staged_fetch, the real /api/njt-stops and /api/status handlers.
    Injected: DATA_DIR points at a temp directory; the cached archive is a zip
    built from the COMMITTED fixture backend/tests/fixtures/njt_gtfs/*.txt; the age
    boundary is crossed by backdating that file's mtime by 31 days; the validity
    boundary is crossed with the loader's own injectable `now` seam; a renewed
    publication is the same committed fixture with every calendar_dates day shifted
    forward one year.

NO NJ TRANSIT NETWORK ACCESS, BY CONSTRUCTION. njt_auth._httpx_post (the single
transport seam every NJT request goes through) is replaced before anything runs,
with a fake that answers getToken with a fake token and getGTFS with local zip
bytes, and raises on any other URL. NJT_TOKEN_URL and NJT_STATIC_URL are also
redirected to a non-resolvable host before the backend is imported, so even a bug
in this script cannot address raildata.njtransit.com. No mint is spent: the
credentials in the environment are fabricated by this script.

HERMETIC AND DETERMINISTIC: no live feed, no socket, no dependence on today's
wall clock for any measured age. The archive age is measured against an mtime this
script sets, and the calendar validity is measured against injected timestamps.
Every log record production code emits at INFO or above is captured (the stream
handler backend/main.py installs is removed first) and reprinted in one block at
the end, so the app's own account of what it did is visible without interleaving
with the measurements.

RUN (from the repository root):
  .venv/bin/python docs/reviews/audit-2026-09-05/f07_static_never_refreshes.py

Exits 0 while F07 still behaves as recorded, non-zero (with the failed assertion
named) if the code has changed underneath the audit record.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import sys
import tempfile
import time
import zipfile
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
NJT_FIXTURE_DIR = BACKEND / "tests" / "fixtures" / "njt_gtfs"

# The whole data root moves to a temp directory BEFORE the backend is imported:
# every static loader resolves its cache path from DATA_DIR at import time, so a
# later override would be too late and this run could otherwise read or overwrite
# the developer's real archives.
_TMP = Path(tempfile.mkdtemp(prefix="f07-data-"))
os.environ["DATA_DIR"] = str(_TMP)
# Fabricated credentials. is_configured() only needs both variables non-empty and
# not the documented placeholders; the transport below is fake, so these are never
# offered to anything.
os.environ["NJT_USERNAME"] = "f07-fabricated-user"
os.environ["NJT_PASSWORD"] = "f07-fabricated-pass"
# Belt and braces with the transport seam: even a direct httpx call could not
# reach NJ Transit from here.
os.environ["NJT_TOKEN_URL"] = "http://f07-no-such-host.invalid/getToken"
os.environ["NJT_STATIC_URL"] = "http://f07-no-such-host.invalid/getGTFS"

sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

import bus_static  # noqa: E402
import ferry_static  # noqa: E402
import main  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import path_static  # noqa: E402
import pollers  # noqa: E402
import railroad_static  # noqa: E402
import static_data  # noqa: E402
from static_shared import StaticValidationError  # noqa: E402

FAILURES: list[str] = []
CAPTURED_LOGS: list[str] = []


class _Collector(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        CAPTURED_LOGS.append(f"{record.levelname} {record.name}: {record.getMessage()}")


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        FAILURES.append(label + (f" ({detail})" if detail else ""))
    return condition


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------------------
# The network guard: no NJT request may leave this process
# ---------------------------------------------------------------------------

TRANSPORT_LOG: list[str] = []
_ZIP_TO_SERVE: list[bytes] = [b""]


async def _fake_njt_transport(url: str, form: dict, timeout_s: float):
    """Stands in for njt_auth._httpx_post, the one place an NJT POST is made."""
    TRANSPORT_LOG.append(url)
    if url.endswith("getToken"):
        return 200, b'{"UserToken":"f07-fabricated-token"}'
    if url.endswith("getGTFS"):
        return 200, _ZIP_TO_SERVE[0]
    raise AssertionError(f"unexpected NJT request to {url!r}; this script allows none")


njt_auth._httpx_post = _fake_njt_transport
njt_auth.TOKEN_CACHE = njt_auth.TokenCache()

assert "njtransit" not in njt_auth.NJT_TOKEN_URL, njt_auth.NJT_TOKEN_URL
assert "njtransit" not in njt_static.NJT_STATIC_URL, njt_static.NJT_STATIC_URL


# ---------------------------------------------------------------------------
# Hermetic stand-ins for the five static groups and the two poll loops
# ---------------------------------------------------------------------------

SUBWAY_STOPS = {"101N": {"name": "Van Cortlandt Park", "lat": 40.889, "lon": -73.898}}
RAILROAD_DATA = {
    "LIRR": {
        "stops": {"1": {"name": "Amagansett", "lat": 40.976, "lon": -72.143}},
        "trips": {"t1": {"route_id": "5", "shape_id": "s1"}},
        "shapes": {"s1": [[40.7, -74.0], [40.71, -74.01]]},
        "routes": {"5": {"long_name": "Montauk Branch", "short_name": None}},
        "stop_times": {"t1": ["1"]},
    },
    "MNR": None,
}
PATH_DATA = {
    "stops": {"26733": {"id": "26733", "name": "Newark", "lat": 40.73, "lon": -74.16}},
    "child_to_parent": {"781718": "26733"},
    "trips": {"p1": {"route_id": "862", "direction_id": "0", "shape_id": "s1"}},
    "shapes": {"s1": [[40.73, -74.16], [40.71, -74.01]]},
    "routes": {"862": {"long_name": "Newark - WTC", "short_name": None, "color": "d93a30"}},
    "stop_times": {"p1": ["781718"]},
}
FERRY_DATA = {
    "stops": {"18": {"id": "18", "name": "Pier 11", "lat": 40.703, "lon": -74.005}},
    "trips": {"f1": {"route_id": "ER", "direction_id": "0", "shape_id": "s1", "headsign": None}},
    "shapes": {"s1": [[40.709, -73.967], [40.703, -74.005]]},
    "routes": {"ER": {"long_name": "East River", "short_name": None}},
}
NJT_STUB_DATA = {
    "stops": {"109": {"id": "109", "name": "New York Penn Station", "lat": 40.75, "lon": -73.99}},
    "routes": {"1": {"long_name": "Atlantic City Rail Line", "short_name": "ACRL"}},
    "trips": {"T1": {"route_id": "1", "headsign": "Atlantic City", "short_name": "4600"}},
    "stop_times": {"T1": [{"stop_id": "109", "arrival": 100, "departure": 120, "seq": 1}]},
    "shapes": {"sh1": [[40.75, -73.99], [39.36, -74.44]]},
    "calendar_dates": {"S1": {"20260905"}},
}

FEED_NAMES = ("buses", "subways", "railroads", "path", "ferry", "njt")


def install_stubs(counts: Counter, *, stub_njt: bool) -> list[tuple[object, str, object]]:
    """Replace every loader and refresher with a counting no-op. Returns the undo
    list. `stub_njt` False leaves the REAL njt_static.load_njt_static in place."""
    saved: list[tuple[object, str, object]] = []

    def swap(module: object, name: str, value: object) -> None:
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    async def subway_stops():
        counts["subway_static"] += 1
        return dict(SUBWAY_STOPS)

    async def railroad_load():
        counts["railroad_static"] += 1
        return {k: (dict(v) if v else None) for k, v in RAILROAD_DATA.items()}

    async def path_load():
        counts["path_static"] += 1
        return dict(PATH_DATA)

    async def ferry_load():
        counts["ferry_static"] += 1
        return dict(FERRY_DATA)

    async def njt_load():
        counts["njt_static"] += 1
        return dict(NJT_STUB_DATA)

    async def bus_index():
        counts["bus_index"] += 1

    swap(main, "load_subway_stops", subway_stops)
    swap(main, "load_subway_route_shapes", lambda: [])
    swap(main, "load_subway_stations", lambda: {})
    swap(main, "load_subway_station_routes", lambda: {})
    swap(railroad_static, "load_railroad_static", railroad_load)
    swap(path_static, "load_path_static", path_load)
    swap(ferry_static, "load_ferry_static", ferry_load)
    swap(bus_static, "ensure_index", bus_index)
    if stub_njt:
        swap(njt_static, "load_njt_static", njt_load)

    for feed in FEED_NAMES:
        attr = f"_refresh_{feed}"

        def make(name: str):
            async def refresher(app, client):
                counts[f"poll:{name}"] += 1
                # Record a successful poll the way a real refresher would, so the
                # realtime side of the app is genuinely HEALTHY while the static
                # side sits expired. That is the state F07 is about.
                entry = app.state.feed_cache[name]
                entry["data"] = []
                entry["fetched_at"] = time.time()
                entry["feed_timestamp"] = time.time()
                entry["error"] = None

            return refresher

        swap(pollers, attr, make(feed))

    async def refresh_alerts(app, client):
        counts["poll:alerts"] += 1

    swap(pollers, "_refresh_alerts", refresh_alerts)
    swap(pollers, "POLL_INTERVAL_S", 0.004)
    swap(pollers, "ALERT_POLL_INTERVAL_S", 0.004)
    return saved


def undo(saved: list[tuple[object, str, object]]) -> None:
    for module, name, value in reversed(saved):
        setattr(module, name, value)


async def wait_for(predicate, timeout_s: float = 10.0, step: float = 0.005) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


def task_census(app) -> dict[str, object]:
    """Every background task the REAL lifespan put on app.state, read from the live
    state dictionary rather than transcribed from main.py."""
    live = getattr(app.state, "_state", None) or vars(app.state)
    return {name: obj for name, obj in live.items() if name.endswith("_task")}


# ---------------------------------------------------------------------------
# Part A: the task census, on the real lifespan
# ---------------------------------------------------------------------------

STATIC_TASKS = (
    "subway_static_task",
    "railroad_static_task",
    "path_static_task",
    "ferry_static_task",
    "njt_static_task",
    "bus_index_task",
)
POLL_TASKS = ("feed_poll_task", "alert_poll_task")
LOADER_COUNTERS = (
    "subway_static",
    "railroad_static",
    "path_static",
    "ferry_static",
    "njt_static",
    "bus_index",
)


async def part_a() -> dict:
    counts: Counter = Counter()
    saved = install_stubs(counts, stub_njt=True)
    app = main.app
    result: dict = {}
    try:
        async with main.lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://f07") as client:
                ready = await wait_for(
                    lambda: all(
                        getattr(app.state, f, None) == "ready"
                        for f in (
                            "subway_static_status",
                            "railroad_static_status",
                            "path_static_status",
                            "ferry_static_status",
                            "njt_static_status",
                        )
                    )
                )
                result["all_ready"] = ready
                census = task_census(app)
                result["task_names"] = sorted(census)
                counts_at_ready = dict(counts)
                result["counts_at_ready"] = counts_at_ready
                # Let the running app do real work for a while: dozens of poll
                # cycles, each one a full TaskGroup generation over all six feeds.
                await wait_for(lambda: counts["poll:subways"] >= 40, timeout_s=15.0)
                result["poll_cycles"] = counts["poll:subways"]
                result["alert_cycles"] = counts["poll:alerts"]
                result["counts_after_polling"] = {k: counts[k] for k in LOADER_COUNTERS}
                result["static_done"] = {
                    name: bool(census[name].done()) for name in STATIC_TASKS if name in census
                }
                result["static_exceptions"] = {
                    name: repr(census[name].exception())
                    for name in STATIC_TASKS
                    if name in census and census[name].done() and not census[name].cancelled()
                }
                result["poll_alive"] = {
                    name: not census[name].done() for name in POLL_TASKS if name in census
                }
                res = await client.get("/api/status")
                result["status_code"] = res.status_code
                result["status_json"] = res.json()
    finally:
        undo(saved)
    return result


# ---------------------------------------------------------------------------
# Part C helpers: the committed NJT fixture, and a renewed publication
# ---------------------------------------------------------------------------


def fixture_members() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8-sig")
        for path in sorted(NJT_FIXTURE_DIR.iterdir())
        if path.suffix == ".txt"
    }


def zip_bytes(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def shift_calendar_dates(text: str, days: int) -> str:
    """The committed calendar_dates.txt with every service day moved forward."""
    lines = [line for line in text.splitlines() if line.strip()]
    header = lines[0].split(",")
    idx = header.index("date")
    out = [lines[0]]
    for line in lines[1:]:
        parts = line.split(",")
        moved = datetime.strptime(parts[idx], "%Y%m%d") + timedelta(days=days)
        parts[idx] = moved.strftime("%Y%m%d")
        out.append(",".join(parts))
    return "\n".join(out) + "\n"


async def part_c() -> dict:
    counts: Counter = Counter()
    saved = install_stubs(counts, stub_njt=False)
    result: dict = {}

    members = fixture_members()
    cached_bytes = zip_bytes(members)
    zip_path = njt_static.NJT_STATIC_ZIP
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(cached_bytes)
    _ZIP_TO_SERVE[0] = cached_bytes

    njt_load_calls: list[float] = []
    real_load = njt_static.load_njt_static

    async def counting_load(*args, **kwargs):
        njt_load_calls.append(time.monotonic())
        return await real_load(*args, **kwargs)

    saved.append((njt_static, "load_njt_static", real_load))
    njt_static.load_njt_static = counting_load

    app = main.app
    try:
        async with main.lifespan(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://f07") as client:
                ready = await wait_for(
                    lambda: getattr(app.state, "njt_static_status", None) == "ready"
                )
                result["njt_ready"] = ready
                result["loads_at_ready"] = len(njt_load_calls)
                result["transport_at_ready"] = list(TRANSPORT_LOG)
                loaded = app.state.njt_static
                result["stop_count"] = len(app.state.njt_stops)
                result["route_count"] = len(app.state.njt_routes)
                result["latest_service_date"] = njt_static.latest_service_date(
                    loaded["calendar_dates"]
                )

                # ---- cross the ARCHIVE AGE threshold -------------------------
                # The loader's freshness arithmetic reads the file mtime against
                # time.time(); backdating the mtime crosses the threshold without
                # depending on today's date at all.
                stale_mtime = time.time() - (njt_static.MAX_AGE_DAYS + 1) * 86400
                os.utime(zip_path, (stale_mtime, stale_mtime))
                age_s = time.time() - zip_path.stat().st_mtime
                result["max_age_days"] = njt_static.MAX_AGE_DAYS
                result["age_seconds"] = age_s
                result["age_threshold_seconds"] = njt_static.MAX_AGE_DAYS * 86400
                result["age_exceeded"] = age_s > njt_static.MAX_AGE_DAYS * 86400

                # ---- cross the SERVICE CALENDAR validity boundary ------------
                latest = result["latest_service_date"]
                last_day = datetime.strptime(latest, "%Y%m%d").replace(tzinfo=njt_static.NYC_TZ)
                t_on = last_day.replace(hour=12).timestamp()
                t_after = (last_day + timedelta(days=1)).replace(hour=12).timestamp()
                result["today_on"] = njt_static._today(t_on)
                result["today_after"] = njt_static._today(t_after)
                with zipfile.ZipFile(zip_path) as zf:
                    try:
                        njt_static.validate_njt_archive(zf, now=t_on)
                        result["valid_on_last_day"] = True
                        result["valid_on_last_day_error"] = ""
                    except StaticValidationError as exc:
                        result["valid_on_last_day"] = False
                        result["valid_on_last_day_error"] = str(exc)
                    try:
                        njt_static.validate_njt_archive(zf, now=t_after)
                        result["valid_day_after"] = True
                        result["valid_day_after_error"] = ""
                    except StaticValidationError as exc:
                        result["valid_day_after"] = False
                        result["valid_day_after_error"] = str(exc)

                # ---- the app keeps serving across both boundaries ------------
                before = counts["poll:njt"]
                await wait_for(lambda: counts["poll:njt"] >= before + 40, timeout_s=15.0)
                result["poll_cycles_after_boundary"] = counts["poll:njt"] - before
                result["loads_after_boundary"] = len(njt_load_calls)
                result["transport_after_boundary"] = list(TRANSPORT_LOG)
                result["status_after_boundary"] = getattr(app.state, "njt_static_status", None)
                result["njt_task_done"] = app.state.njt_static_task.done()
                res = await client.get("/api/njt-stops")
                result["stops_http"] = res.status_code
                result["stops_len"] = len(res.json())
                result["stops_cache_control"] = res.headers.get("cache-control", "")
                res = await client.get("/api/status")
                status_json = res.json()
                result["status_json_njt"] = status_json["njt_static"]
                result["status_archives"] = status_json.get("static_archives", {})
                result["status_keys"] = sorted(status_json)
                result["status_subway_gtfs"] = status_json.get("static_subway_gtfs")
                res = await client.get("/healthz")
                result["healthz_code"] = res.status_code
                result["healthz_status"] = res.json().get("status")
                result["healthz_degraded"] = res.json().get("degraded")

                # ---- what running the loader again WOULD do ------------------
                # A renewed publication, served by the fake transport. This call is
                # made BY THIS SCRIPT: nothing in the app makes it.
                renewed = dict(members)
                renewed["calendar_dates.txt"] = shift_calendar_dates(
                    members["calendar_dates.txt"], 365
                )
                _ZIP_TO_SERVE[0] = zip_bytes(renewed)
                transport_before = len(TRANSPORT_LOG)
                fresh_data = await njt_static.load_njt_static(now=t_after)
                result["manual_transport_calls"] = [
                    url.rsplit("/", 1)[-1] for url in TRANSPORT_LOG[transport_before:]
                ]
                result["manual_latest_service_date"] = njt_static.latest_service_date(
                    fresh_data.get("calendar_dates", {})
                )
                result["manual_stop_count"] = len(fresh_data.get("stops", {}))
                with zipfile.ZipFile(zip_path) as zf:
                    result["on_disk_latest"] = njt_static.latest_service_date(
                        njt_static.parse_member(
                            zf, "calendar_dates.txt", njt_static._parse_calendar_dates
                        )
                    )
                result["in_process_latest"] = njt_static.latest_service_date(
                    app.state.njt_static["calendar_dates"]
                )
                result["loads_total"] = len(njt_load_calls)
    finally:
        undo(saved)
    return result


# ---------------------------------------------------------------------------
# Part D: is there a periodic static refresh anywhere in production code?
# ---------------------------------------------------------------------------

LOADER_CALLS = (
    "load_subway_stops",
    "load_railroad_static",
    "load_path_static",
    "load_ferry_static",
    "load_njt_static",
    "ensure_index",
)


def production_sources() -> list[Path]:
    files = sorted(BACKEND.glob("*.py")) + sorted((BACKEND / "routes").glob("*.py"))
    files += sorted((BACKEND / "feeds").glob("*.py"))
    return files


def call_site_census(files: list[Path] | None = None) -> dict[str, list[str]]:
    """Every call site of a static loader in `files`, excluding its definition."""
    sites: dict[str, list[str]] = {name: [] for name in LOADER_CALLS}
    for path in files if files is not None else production_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", '"', "'")) or stripped.startswith(("def ", "async def ")):
                continue
            for name in LOADER_CALLS:
                if re.search(rf"\b{name}\s*\(", line):
                    sites[name].append(f"{path.relative_to(REPO)}:{lineno}")
    return sites


def find_lines(pattern: str) -> list[str]:
    """Every production line matching `pattern`, as path:lineno, derived at run time
    so the line numbers in this report cannot go stale."""
    found: list[str] = []
    for path in production_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith(("#", "def ", "async def ")):
                continue
            if re.search(pattern, line):
                found.append(f"{path.relative_to(REPO)}:{lineno}")
    return found


def max_age_census() -> dict[str, int]:
    ages: dict[str, int] = {}
    for module in (static_data, railroad_static, path_static, ferry_static, njt_static, bus_static):
        ages[module.__name__] = module.MAX_AGE_DAYS
    return ages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main_script() -> int:
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(_Collector())
    root.setLevel(logging.INFO)

    print("F07 verification: static schedules do not refresh after a successful warmup")
    print(f"repo:     {REPO}")
    print(f"data dir: {_TMP}  (temp; the repository's data/ is untouched)")

    section("PART A: every background task the REAL lifespan starts, and whether it can run again")
    CAPTURED_LOGS.append("---- part A: the task census ----")
    a = asyncio.run(part_a())
    print(f"  tasks found on app.state: {len(a['task_names'])}")
    for name in a["task_names"]:
        kind = "STATIC" if name in STATIC_TASKS else "REALTIME"
        state = "done (returned)" if a["static_done"].get(name) else "running"
        if name in POLL_TASKS:
            state = "running" if a["poll_alive"].get(name) else "done"
        print(f"    {kind:<9}{name:<22} {state}")
    print()
    print(f"  poll cycles observed while the app ran: {a['poll_cycles']} feed, "
          f"{a['alert_cycles']} alerts")
    print("  loader executions, at readiness -> after all those poll cycles:")
    for name in LOADER_COUNTERS:
        print(
            f"    {name:<18} {a['counts_at_ready'].get(name, 0)} -> "
            f"{a['counts_after_polling'][name]}"
        )
    print(f"  /api/status group states: njt_static={a['status_json'].get('njt_static')!r}, "
          f"subway_static={a['status_json'].get('subway_static')!r}")
    print()
    check("the lifespan starts exactly 8 background tasks", len(a["task_names"]) == 8,
          f"found {a['task_names']}")
    check("all five static warmup groups reached ready", a["all_ready"])
    check(
        "all 6 static tasks RETURNED (done, not cancelled, no exception)",
        len(a["static_done"]) == 6 and all(a["static_done"].values()),
        str(a["static_done"]),
    )
    check(
        "no static task ended in an exception",
        all(v == "None" for v in a["static_exceptions"].values()),
        str(a["static_exceptions"]),
    )
    check(
        "both realtime poll tasks are still running",
        len(a["poll_alive"]) == 2 and all(a["poll_alive"].values()),
        str(a["poll_alive"]),
    )
    check("the app really ran (40+ feed poll cycles)", a["poll_cycles"] >= 40,
          f"{a['poll_cycles']} cycles")
    check(
        "every static loader executed EXACTLY ONCE across the whole run",
        all(a["counts_after_polling"][n] == 1 for n in LOADER_COUNTERS),
        str(a["counts_after_polling"]),
    )
    check("/api/status answered 200 the whole time", a["status_code"] == 200)

    section("PART B: the thresholds the loaders actually use, and where they are read")
    ages = max_age_census()
    for module_name, days in ages.items():
        print(f"    {module_name+'.MAX_AGE_DAYS':<34} = {days} days = {days * 86400} seconds")
    print("    njt_static.validate_njt_archive gate  = latest service day >= today in New York")
    print("      (njt_static._today reads America/New_York, not UTC)")
    print()
    age_lines = find_lines(r"time\.time\(\) - .*MAX_AGE_DAYS \* 86400")
    usable_lines = find_lines(r"cached_archive_is_valid\(")
    print("  where each threshold is read, and with which clock:")
    print("    age test (time.time() against the cache file's mtime, NO injectable clock):")
    for site in age_lines:
        print(f"      {site}")
    print("    validity test (cached_archive_is_valid -> each loader's validator, `now`")
    print("    injectable):")
    for site in usable_lines:
        print(f"      {site}")
    print("    Every one of those lines sits inside a loader body, so neither test can")
    print("    run unless a loader runs.")
    sites = call_site_census()
    script_sites = call_site_census(sorted((BACKEND / "scripts").glob("*.py")))
    print()
    print("  call sites of each static loader in the SERVED process (backend/*.py,")
    print("  backend/routes/, backend/feeds/; tests excluded):")
    for name, found in sites.items():
        print(f"    {name:<22} {len(found)} site(s): {', '.join(found) or 'none'}")
    print("  call sites in backend/scripts/ (offline developer tools, never imported")
    print("  by the app; listed so the census above is not read as hiding them):")
    for name, found in script_sites.items():
        if found:
            print(f"    {name:<22} {len(found)} site(s): {', '.join(found)}")
    print()
    check("all six static modules use the same 30 day age policy",
          set(ages.values()) == {30}, str(ages))
    check(
        "each static loader has exactly ONE production call site",
        all(len(sites[name]) == 1 for name in LOADER_CALLS),
        str({k: len(v) for k, v in sites.items()}),
    )
    check(
        "five of the six sites are the warmup retry loops in backend/warmups.py",
        sum(1 for name in LOADER_CALLS if sites[name] and "warmups.py" in sites[name][0]) == 5,
        str(sites),
    )
    check(
        "the sixth is backend/main.py's startup block (bus_static.ensure_index)",
        bool(sites["ensure_index"]) and "main.py" in sites["ensure_index"][0],
        str(sites["ensure_index"]),
    )

    section("PART C: the NJT boundary, with the real loader and the committed fixture")
    CAPTURED_LOGS.append("---- part C: the NJT boundary ----")
    c = asyncio.run(part_c())
    print("  startup load, from the cached archive:")
    print(f"    njt_static_status              = {c['status_after_boundary']!r}")
    print(f"    stops loaded                   = {c['stop_count']}")
    print(f"    route lines built              = {c['route_count']}")
    print(f"    latest service date in archive = {c['latest_service_date']}")
    print(f"    NJT POSTs made so far          = {len(c['transport_at_ready'])} "
          f"(a valid, fresh cache needs no download)")
    print()
    print("  ARCHIVE AGE boundary (backdated mtime, no wall-clock dependence):")
    print(f"    cached archive age             = {c['age_seconds']:,.0f} s")
    print(f"    njt_static.MAX_AGE_DAYS        = {c['max_age_days']} days "
          f"= {c['age_threshold_seconds']:,} s")
    print(f"    age > threshold                = {c['age_exceeded']}")
    print()
    print("  SERVICE CALENDAR boundary (the loader's own injectable `now`):")
    print(f"    on {c['today_on']} (the last service day) validate_njt_archive passes = "
          f"{c['valid_on_last_day']}")
    print(f"    on {c['today_after']} (one day later)      validate_njt_archive passes = "
          f"{c['valid_day_after']}")
    print(f"      rejection reason: {c['valid_day_after_error']}")
    print()
    print("  what the running app did about either boundary:")
    print(f"    further poll cycles run        = {c['poll_cycles_after_boundary']}")
    print(f"    load_njt_static executions     = {c['loads_at_ready']} at readiness -> "
          f"{c['loads_after_boundary']} after the boundaries")
    print(f"    njt_static_task.done()         = {c['njt_task_done']}")
    print(f"    njt_static_status              = {c['status_after_boundary']!r}")
    print(f"    GET /api/njt-stops             = HTTP {c['stops_http']}, "
          f"{c['stops_len']} stations, Cache-Control: {c['stops_cache_control']!r}")
    print(f"    GET /api/status njt_static     = {c['status_json_njt']!r}")
    print(f"    GET /healthz                   = HTTP {c['healthz_code']} "
          f"{c['healthz_status']!r}, degraded={c['healthz_degraded']}")
    print(f"    archive record on /api/status  = {c['status_archives'].get('njt', {})}")
    print(f"    /api/status keys mentioning refresh/validity: "
          f"{[k for k in c['status_keys'] if 'valid' in k or 'refresh' in k or 'expir' in k]}")
    print()
    print("  the same loader, run once by hand at the post-expiry clock:")
    print(f"    NJT POSTs it made              = {c['manual_transport_calls']}")
    print(f"    latest service date returned   = {c['manual_latest_service_date']}")
    print(f"    stops returned                 = {c['manual_stop_count']}")
    print(f"    latest date now on disk        = {c['on_disk_latest']}")
    print(f"    latest date still in process   = {c['in_process_latest']}")
    print()
    check("the NJT warmup reached ready from the committed fixture", c["njt_ready"])
    check("the cached archive was served with zero NJT POSTs",
          c["transport_at_ready"] == [], str(c["transport_at_ready"]))
    check("the fixture carries a real, dated service calendar",
          bool(re.fullmatch(r"20\d{6}", c["latest_service_date"] or "")),
          str(c["latest_service_date"]))
    check("the archive is now past the 30 day age threshold", c["age_exceeded"],
          f"{c['age_seconds']:,.0f}s vs {c['age_threshold_seconds']:,}s")
    check("the validator ACCEPTS the archive on its last service day",
          c["valid_on_last_day"], c["valid_on_last_day_error"])
    check("the validator REJECTS the archive one day later",
          not c["valid_day_after"], c["valid_day_after_error"])
    check("the rejection names the expiry, so the check really is the calendar gate",
          (c["latest_service_date"] or "") in c["valid_day_after_error"],
          c["valid_day_after_error"])
    check("the app kept polling across both boundaries",
          c["poll_cycles_after_boundary"] >= 40, str(c["poll_cycles_after_boundary"]))
    check("NOTHING re-ran the loader: still exactly one execution",
          c["loads_after_boundary"] == 1, str(c["loads_after_boundary"]))
    check("the NJT warmup task is done and cannot run again", c["njt_task_done"])
    check("the group still reports ready", c["status_after_boundary"] == "ready")
    check("/api/njt-stops still serves the expired snapshot as cacheable",
          c["stops_http"] == 200 and c["stops_len"] == c["stop_count"]
          and "max-age" in c["stops_cache_control"],
          f"HTTP {c['stops_http']}, {c['stops_len']} stops, {c['stops_cache_control']!r}")
    check("/api/status reports njt_static ready with no validity or refresh field",
          c["status_json_njt"] == "ready"
          and not [k for k in c["status_keys"] if "valid" in k or "refresh" in k or "expir" in k],
          str(c["status_keys"]))
    check("/healthz stays pass while the snapshot is 31 days stale and expired",
          c["healthz_code"] == 200 and c["healthz_status"] == "pass",
          f"HTTP {c['healthz_code']} {c['healthz_status']!r} {c['healthz_degraded']}")
    check("run by hand, the loader DID re-download (getToken + getGTFS)",
          c["manual_transport_calls"] == ["getToken", "getGTFS"],
          str(c["manual_transport_calls"]))
    check("run by hand, the loader adopted the renewed publication",
          c["manual_latest_service_date"] is not None
          and c["manual_latest_service_date"] > (c["latest_service_date"] or ""),
          f"{c['latest_service_date']} -> {c['manual_latest_service_date']}")
    check("the in-process snapshot never moved: disk and process now disagree",
          c["on_disk_latest"] != c["in_process_latest"]
          and c["in_process_latest"] == c["latest_service_date"],
          f"disk {c['on_disk_latest']}, process {c['in_process_latest']}")

    section("Everything production code logged during this run (INFO and above)")
    for line in CAPTURED_LOGS or ["(nothing logged)"]:
        print(f"    {line}")
    ready_lines = [line for line in CAPTURED_LOGS if line.endswith("_static_status ready")]
    print()
    check(
        "each static group logged 'ready' exactly once per lifespan run and never again",
        len(ready_lines) == 10,
        f"{len(ready_lines)} ready lines across the two lifespan runs (expected 5 + 5)",
    )

    section("VERDICT")
    print("  Every claim in F07 held:")
    print("    - the age and calendar checks live inside the loaders, and run only when")
    print("      a loader runs (Part C: they fire correctly the one time they are called,")
    print("      and again when this script calls the loader by hand)")
    print("    - all six static tasks return after success and are done() forever (Part A)")
    print("    - the only production call site of each loader is the single startup task")
    print("      (Part B); there is no periodic static refresh anywhere in the backend")
    print("    - a process that crosses the 30 day age threshold AND its schedule's")
    print("      validity boundary keeps serving the expired snapshot as ready and")
    print("      cacheable, while /healthz answers pass with an empty degraded list")
    print("      (Part C)")
    print()
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) FAILED; reality has changed under the audit record:")
        for item in FAILURES:
            print(f"    - {item}")
        print()
        print(
            "DISPOSITION: RECORDED AS VERIFIED, BUT THE RECORD NO LONGER HOLDS "
            f"- {len(FAILURES)} check(s) failed, listed above."
        )
        return 1
    print(
        "DISPOSITION: VERIFIED "
        "- all 6 static tasks return permanently, each loader runs exactly once, and a "
        "30-day-stale, calendar-expired NJT snapshot keeps serving as ready with max-age "
        "while nothing reloads or revalidates."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main_script())
