"""A02 (High), "Subway archive validation can replace good data with a persistent failure".

THE AUDIT'S CLAIM (the 2026-10-12 report, finding A02, verbatim):

    Failure. "The staged subway validator verifies some members and the presence of
    required tables, but does not fully parse every table required for successful
    publication. A structurally complete archive containing unreadable trips.txt can
    replace the good archive. The warmup then fails while building the station-route
    index. Subsequent attempts accept the same newly cached archive as fresh and do
    not download a repair."

    Evidence. "A local staged-download reproduction promoted an archive with valid
    basic tables and invalid UTF-8 in trips.txt. Three attempts all raised
    UnicodeDecodeError; only one download occurred. Archive status reported a
    successful promotion and zero failed downloads. This is a stronger failure mode
    than the prior audit's geometry-only corruption finding."

    Repair. "Validate the complete required publication before replacement and retain
    the last fully usable snapshot. A parse failure in a supposedly valid cached
    archive must invalidate that cache or force a staged repair. Test good cache →
    bad publication → healthy publication, checking both continuity and eventual
    recovery."

    Code evidence, as the report links it at 1e3e73d (where to LOOK; nothing below
    asserts on a line, a symbol or a comment):
      backend/static_data.py:142   the subway archive's staged download
      backend/static_data.py:232   the "usable" and "fresh" decision in the loader
      backend/static_data.py:438   the station-route index's reads of trips.txt and
                                   stop_times.txt

    The same article's "Related medium-priority NJT variant" (a damaged DEFLATE
    shapes.txt in the NJ Transit archive) is reproduced by a02b, not here.

WHAT IS PRODUCTION CODE HERE, unmodified and called directly:
    warmups._warm_subway_static    THE subway static warmup: load stops, route lines,
                                   station markers, the station-route index (in a
                                   worker thread) and the complex index, retrying on
                                   its own backoff schedule. Every attempt below is
                                   one turn of its own retry loop, waiting the delay
                                   that loop asks for (on a virtual clock, below).
    and, under it, everything it reaches: static_data.load_subway_stops deciding
    "usable and fresh", static_data._download_zip, static_shared.staged_fetch
    (sweep, stage, validate, promote), static_shared._stream_to_file (the default
    transfer: the httpx client, its whole-transfer deadline, redirects,
    raise_for_status and the streamed write into the stage file),
    static_data.validate_subway_archive, static_shared.cached_archive_is_valid, the
    station-route index loader, and static_shared.archive_status.
    main.app over httpx.ASGITransport: GET /api/status (the group state
    "subway_static", "static_archives" and "static_subway_gtfs"), GET
    /api/subway-stops (the stations and the routes the index gives each one) and GET
    /api/subway-routes (route lines). Every state read in this script goes through
    those three endpoints, never through app.state.
    static_shared.validate_archive and cached_archive_is_valid are also called
    standalone on each probe archive, because the spec asks what they answer. Those
    calls hand them the staged validator themselves, so they report what that
    validator says about the bytes, NOT what the loader decides about its cache. The
    loader's decision is measured by behavior instead: whether the next attempt
    downloads (M6, A1.2, ARMS 4 and 5).

WHAT IS INJECTED, and this is the whole of it. Items 2 to 4 are installed BEFORE
the first backend import, so a module that binds a name at import (from asyncio
import sleep, from time import time) binds the injected one.
    1. DATA_DIR points at a fresh temp directory and SUBWAY_GTFS_URL at
       http://127.0.0.1:9 (nothing listens), both before the first backend import,
       through the env_seams C6 seams.
    2. THE SOCKET LAYER. httpx.AsyncHTTPTransport.handle_async_request (and its
       sync twin, httpx.HTTPTransport.handle_request), the point where an httpx
       client hands a request to its connection pool, is replaced by a fake that
       answers with whichever local archive this script is "publishing" and COUNTS
       the requests it gets. That count is the download count. It refuses any URL
       naming njtransit or raildata and any URL that is not the subway seam, and a
       final check fails the run if it ever had to. Faked BELOW production's
       transfer, not at it: 09-05's F08 rebinds the module global
       static_shared._stream_to_file, which a behavior-preserving refactor (binding
       the default transfer as a default argument) silently bypasses, and wrapping
       staged_fetch's `download=` parameter by name has the same exposure. Nothing
       in this backend reaches a socket except through an httpx client, so every
       staged download, however it is wired, arrives here.
    3. A VIRTUAL CLOCK. time.time, time.monotonic and time.perf_counter (and their
       _ns forms) read the real clock plus an offset that only grows. The offset
       advances by exactly the backoff each failed attempt asks for (item 4), and
       by an explicit jump where an arm says time passes (a restart a month later,
       the run-up to MAX_AGE_DAYS). The file system stamps writes with the REAL
       clock, so after each attempt every file under DATA_DIR whose mtime falls
       inside that attempt's real-time window is moved onto the virtual clock (an
       mtime production code set explicitly, to a computed time, is outside the
       window and left as set). random is seeded, so the warmup's backoff jitter,
       and with it every virtual time printed, repeats run to run.
    4. asyncio.sleep (as asyncio.sleep and as asyncio.tasks.sleep) is wrapped for
       the warmup task ONLY, and only for a positive delay (a zero-delay checkpoint
       is not a backoff). The warmup sleeps between attempts; the wrapper turns
       that sleep into the observation point: it
       records which exception the warmup is backing off from (sys.exc_info, read
       while the warmup's own except block is live, with a fallback that finds it
       along the task's await chain), reads the three endpoints, optionally changes
       the world (the publication), advances the virtual clock by the delay, and
       then either lets the next attempt run at once or stops the loop by raising a
       private BaseException. So an attempt here is one turn of the real retry loop
       at the real schedule, with only the waiting removed.
    5. The archives are built here. stops.txt and transfers.txt are the committed
       fixture backend/tests/fixtures/subway_gtfs, byte for byte (copied from the
       live 2026-08-27 publication; 496 parent stations, 150 cross-stop transfer
       rows). That fixture has no trips.txt, stop_times.txt or shapes.txt, so those
       are synthesized: three trips on routes 1, 7 and A calling at five parent
       stations (101, 103, 127, 725, A27), and one shape per route. The BAD archive
       differs from the GOOD one in exactly one byte: a 0xFF (never valid in UTF-8)
       at the end of the first trip's headsign in trips.txt.
    6. main.app's subway fields are reset to what main.lifespan sets before each
       warmup run, and static_shared.reset_archive_status() clears the per-archive
       record (it is module state and outlives a run), so each run starts as a
       freshly started process would. The warmup runs once per process
       (main.lifespan starts it), so "good cache, then a bad publication" reaches
       production through a restart with an aged cache, which is what ARM 5 drives.
       A restart here is that reset plus a fresh run of the warmup, not a run of
       main.lifespan (which would start every other system's warmup and poller), so
       code that lifespan alone runs before the warmup is not exercised; what this
       script records is the warmup's own attempts inside one process.

HERMETIC: no network (the only transfer is the fake socket layer above, and the URL
it would have used is a dead loopback port), no credentials (_hermetic.contain()
before the backend import, _hermetic.verify() after it, and the NJ Transit and Bus
Time keys blanked), no NJ Transit module is driven, nothing under data/ is read or
written (the temp tree is removed at exit, also on a failure), archives are built
with a fixed zip timestamp so their digests are stable, and nothing depends on
today's date: every age is measured on the virtual clock. A real-time watchdog
stops a run whose warmup wedges (10 s per run of the warmup), so a backoff that
stops passing through asyncio.sleep fails the run in seconds instead of hanging it.
Runs in about five seconds, most of it the roughly 160 real warmup attempts of
ARMS 4 and 5.

RUN IT, from the repository root:
    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a02_unreadable_trips_promoted.py

Exits 0 while A02 still behaves as recorded, 1 with the failed checks named if the
code has changed underneath the record.

RECORDED DISPOSITION: VERIFIED. The audit's three numbers reproduce exactly (three
attempts, three UnicodeDecodeErrors, one download, promoted with zero failed
downloads), and the mechanism is measured rather than read: of the five members the
validator requires, it parses two (stops.txt, transfers.txt) and only checks the
presence of the other three, and the loader keeps a cache it cannot parse as usable
and fresh. On the virtual clock a repair published right after the bad promotion is
not fetched by any attempt over the first six hours of the real backoff schedule or
at one hour short of MAX_AGE_DAYS, from a cold cache and from a good archive the
warmup had loaded a month earlier; it is fetched one hour past MAX_AGE_DAYS. This
row generalizes the 2026-09-05 F08 (shapes.txt, the geometry-only instance, still
standing): trips.txt and stop_times.txt are the members where the same gap fails
the whole warmup.
"""

from __future__ import annotations

import asyncio
import atexit
import csv
import hashlib
import io
import logging
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURE = BACKEND / "tests" / "fixtures" / "subway_gtfs"

# The cache root and the upstream URL must be set BEFORE static_data imports: both
# are read once, at module import, through env_seams.
_TMP = Path(tempfile.mkdtemp(prefix="a02-audit-2026-10-12-"))
atexit.register(shutil.rmtree, _TMP, True)  # also on a failed check or a crash
DATA_ROOT = _TMP / "data"
DEAD_URL = "http://127.0.0.1:9/gtfs_subway.zip"  # discard port; nothing listens
os.environ["DATA_DIR"] = str(DATA_ROOT)
os.environ["SUBWAY_GTFS_URL"] = DEAD_URL


# ---------------------------------------------------------------------------
# Injection 3: the virtual clock (before any backend import)
# ---------------------------------------------------------------------------

_REAL_TIME = time.time
_REAL_TIME_NS = time.time_ns
_REAL_MONOTONIC = time.monotonic
_REAL_MONOTONIC_NS = time.monotonic_ns
_REAL_PERF = time.perf_counter
_REAL_PERF_NS = time.perf_counter_ns


class Clock:
    """The real clock plus an offset that only grows."""

    offset = 0.0

    @classmethod
    def now(cls) -> float:
        return _REAL_TIME() + cls.offset

    @classmethod
    def advance(cls, seconds: float) -> None:
        if seconds > 0:
            cls.offset += seconds

    @classmethod
    def advance_to(cls, when: float) -> None:
        cls.advance(when - cls.now())


time.time = lambda: _REAL_TIME() + Clock.offset
time.time_ns = lambda: _REAL_TIME_NS() + int(Clock.offset * 1e9)
time.monotonic = lambda: _REAL_MONOTONIC() + Clock.offset
time.monotonic_ns = lambda: _REAL_MONOTONIC_NS() + int(Clock.offset * 1e9)
time.perf_counter = lambda: _REAL_PERF() + Clock.offset
time.perf_counter_ns = lambda: _REAL_PERF_NS() + int(Clock.offset * 1e9)
random.seed(20261012)  # the warmup's backoff jitter, so virtual times repeat


class Attempt:
    """The real time at which the attempt now running began."""

    real_start = 0.0


def stamp_attempt_writes() -> None:
    """Move every file the attempt just wrote onto the virtual clock.

    A write takes its mtime from the REAL clock, so a file written during the attempt
    has an mtime inside the attempt's real-time window; the virtual time of that
    write is that mtime plus the offset (the offset never changes during an attempt).
    An mtime production code set explicitly from time.time() or a computation on it
    lies outside the window once the offset is nonzero, and is left as set.
    """
    if not Clock.offset:
        return
    low, high = Attempt.real_start - 0.25, _REAL_TIME() + 0.25
    for path in DATA_ROOT.rglob("*") if DATA_ROOT.exists() else ():
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
            if low <= mtime <= high:
                os.utime(path, (mtime + Clock.offset, mtime + Clock.offset))
        except OSError:
            continue


# ---------------------------------------------------------------------------
# Injection 4: the observation point (before any backend import)
# ---------------------------------------------------------------------------

_REAL_SLEEP = asyncio.sleep
_HOOKS: dict[asyncio.Task, object] = {}
_IN_HOOK: set[asyncio.Task] = set()


class _StopDriving(BaseException):
    """Ends the warmup's retry loop once an arm has seen the attempts it asked for."""


def _backing_off_from(task: asyncio.Task) -> BaseException | None:
    """The exception the warmup is backing off from when it sleeps.

    sys.exc_info first: today the sleep runs inside the warmup's except block, so the
    exception being handled IS the attempt's failure. The fallback keeps that reading
    true for a loop restructured to sleep after its except block: the failure is then
    still a local (or an argument) somewhere along the task's await chain.
    """
    handled = sys.exc_info()[1]
    if handled is not None:
        return handled
    coro = task.get_coro()
    while coro is not None:
        frame = getattr(coro, "cr_frame", None)
        for value in frame.f_locals.values() if frame is not None else ():
            if isinstance(value, BaseException):
                return value
        coro = getattr(coro, "cr_await", None)
    return None


async def _observing_sleep(delay, result=None):
    task = asyncio.current_task()
    hook = _HOOKS.get(task)
    backoff = isinstance(delay, (int, float)) and delay > 0
    if hook is None or task in _IN_HOOK or not backoff:
        return await _REAL_SLEEP(delay, result)
    handled = _backing_off_from(task)
    _IN_HOOK.add(task)
    try:
        keep_going = await hook(handled, float(delay))
    finally:
        _IN_HOOK.discard(task)
    if not keep_going:
        raise _StopDriving()
    return result


# Both spellings: the package attribute (asyncio.sleep, and `from asyncio import
# sleep`) and the defining module's (asyncio.tasks.sleep). Any other task, and any
# zero-delay checkpoint, still gets the real sleep.
asyncio.sleep = _observing_sleep
asyncio.tasks.sleep = _observing_sleep


# ---------------------------------------------------------------------------
# Injection 2: the fake socket layer (before any backend import)
# ---------------------------------------------------------------------------

import httpx  # noqa: E402


class Upstream:
    publishing: Path | None = None
    requests = 0
    refused: list[str] = []  # any URL the fake refused; a final check fails on it


def _answer(request: httpx.Request) -> httpx.Response:
    """The connection pool's job, faked: count the request, answer from a local file."""
    url = str(request.url)
    lowered = url.lower()
    if "njtransit" in lowered or "raildata" in lowered or url != DEAD_URL:
        Upstream.refused.append(url)
        raise AssertionError(f"refusing a request this script does not publish: {url!r}")
    Upstream.requests += 1
    if Upstream.publishing is None:
        raise httpx.ConnectError("nothing is published upstream", request=request)
    body = Upstream.publishing.read_bytes() if request.method == "GET" else b""
    return httpx.Response(200, content=body, request=request)


async def _fake_async_socket_layer(self, request: httpx.Request) -> httpx.Response:
    return _answer(request)


def _fake_sync_socket_layer(self, request: httpx.Request) -> httpx.Response:
    return _answer(request)


# Every httpx client with a pooled transport, async or sync, however it is built.
httpx.AsyncHTTPTransport.handle_async_request = _fake_async_socket_layer
httpx.HTTPTransport.handle_request = _fake_sync_socket_layer


# ---------------------------------------------------------------------------
# Containment, then the backend
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()
sys.path.insert(0, str(BACKEND))

import main  # noqa: E402
import static_data  # noqa: E402
import static_shared  # noqa: E402
import warmups  # noqa: E402

_hermetic.verify()
# Nothing here drives NJ Transit or Bus Time, but blank anything that could
# authenticate (blanked, not popped: see _hermetic.blank for why a pop is undone).
_hermetic.blank("NJT_USERNAME", "NJT_PASSWORD", "BUS_TIME_API_KEY")


# backend/main.py calls logging.basicConfig at import. Capture instead of printing so
# the report stays readable.
class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")
        except Exception:
            self.lines.append(f"{record.levelname} {record.name}: {record.msg}")


CAPTURE = _Capture()
_ROOT = logging.getLogger()
_ROOT.handlers = [CAPTURE]
_ROOT.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

FAILED: list[str] = []


def check(name: str, ok: bool, measured: object, audit: object) -> bool:
    """One named expectation: PASS/FAIL, the measured value, the audit's value."""
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"        measured: {measured}")
    print(f"        audit   : {audit}")
    if not ok:
        FAILED.append(name)
    return ok


def rule(title: str) -> None:
    print()
    print("-" * 78)
    print(title)
    print("-" * 78)


def elapsed(seconds: float) -> str:
    """Virtual time since an arm's reference point, to the second."""
    total = int(round(seconds))
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    clock = f"{hours}:{minutes:02d}:{secs:02d}"
    return f"+{days}d {clock}" if days else f"+{clock}"


# ---------------------------------------------------------------------------
# The archives
# ---------------------------------------------------------------------------

STOPS_BYTES = (FIXTURE / "stops.txt").read_bytes()
TRANSFERS_BYTES = (FIXTURE / "transfers.txt").read_bytes()


def csv_bytes(columns: list[str], rows: list[tuple]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


# Headsign last, so "the end of the first data row" is a free-text field: the bad
# byte sits where a mis-encoded publication would put it, in a name.
TRIPS_BYTES = csv_bytes(
    ["route_id", "service_id", "trip_id", "shape_id", "direction_id", "trip_headsign"],
    [
        ("1", "Weekday", "T1-N", "1..N03R", "0", "Van Cortlandt Park-242 St"),
        ("7", "Weekday", "T7-N", "7..N97R", "0", "Flushing-Main St"),
        ("A", "Weekday", "TA-N", "A..N55R", "0", "Inwood-207 St"),
    ],
)
# Platform ids, which the index folds to their parents through stops.txt.
STOP_TIMES_BYTES = csv_bytes(
    ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"],
    [
        ("T1-N", "08:00:00", "08:00:00", "127N", "1"),
        ("T1-N", "08:20:00", "08:20:00", "103N", "2"),
        ("T1-N", "08:22:00", "08:22:00", "101N", "3"),
        ("T7-N", "08:00:00", "08:00:00", "725N", "1"),
        ("TA-N", "08:00:00", "08:00:00", "A27N", "1"),
    ],
)
SHAPES_BYTES = csv_bytes(
    ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"],
    [
        ("1..N03R", "1", "40.755290", "-73.987495"),
        ("1..N03R", "2", "40.884667", "-73.900870"),
        ("1..N03R", "3", "40.889248", "-73.898583"),
        ("7..N97R", "1", "40.755477", "-73.987691"),
        ("7..N97R", "2", "40.759600", "-73.830030"),
        ("A..N55R", "1", "40.757308", "-73.989735"),
        ("A..N55R", "2", "40.868072", "-73.919899"),
    ],
)
# What the station-route index is when the good archive loads: parent station ->
# routes. Derived by hand from the rows above, then compared with what the app serves.
EXPECTED_INDEX = {"101": ["1"], "103": ["1"], "127": ["1"], "725": ["7"], "A27": ["A"]}
EXPECTED_STATIONS = 496  # parent stations in the committed stops.txt (its README)

GOOD_MEMBERS: dict[str, bytes] = {
    "stops.txt": STOPS_BYTES,
    "shapes.txt": SHAPES_BYTES,
    "trips.txt": TRIPS_BYTES,
    "stop_times.txt": STOP_TIMES_BYTES,
    "transfers.txt": TRANSFERS_BYTES,
}
MEMBERS = list(GOOD_MEMBERS)


def with_invalid_byte(data: bytes) -> bytes:
    """The member with one 0xFF inserted at the end of its first data row."""
    first_eol = data.index(b"\n")
    end = data.index(b"\n", first_eol + 1)
    if data[end - 1 : end] == b"\r":
        end -= 1
    return data[:end] + b"\xff" + data[end:]


def write_zip(path: Path, members: dict[str, bytes]) -> Path:
    """Fixed timestamps, so the same members always give the same bytes."""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 12, 0, 0, 0)), content)
    return path


UPSTREAM_DIR = _TMP / "upstream"
UPSTREAM_DIR.mkdir(parents=True)
GOOD = write_zip(UPSTREAM_DIR / "good.zip", GOOD_MEMBERS)
BAD = write_zip(
    UPSTREAM_DIR / "bad_trips.zip",
    {**GOOD_MEMBERS, "trips.txt": with_invalid_byte(TRIPS_BYTES)},
)
CORRUPT = {
    m: write_zip(UPSTREAM_DIR / f"corrupt_{m}.zip", {**GOOD_MEMBERS, m: with_invalid_byte(b)})
    for m, b in GOOD_MEMBERS.items()
}
ABSENT = {
    m: write_zip(
        UPSTREAM_DIR / f"absent_{m}.zip", {k: v for k, v in GOOD_MEMBERS.items() if k != m}
    )
    for m in GOOD_MEMBERS
}

CACHE = static_data.SUBWAY_GTFS_ZIP
MAX_AGE_DAYS = static_data.MAX_AGE_DAYS
MAX_AGE_S = MAX_AGE_DAYS * 86400
# How long the persistence arms follow the real backoff schedule before the clock
# jumps to the run-up to MAX_AGE_DAYS: the whole rising schedule and then hours of
# its steady-state rung, about 75 attempts. A repair that waits on a count of failed
# attempts (up to about that many) or on elapsed time (up to MAX_AGE_DAYS minus an
# hour) fetches inside this window or at the jump.
SCHEDULE_WINDOW_S = 6 * 3600
WATCHDOG_S = 10.0  # real seconds one run of the warmup may take before it is stopped


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else "(absent)"


def place_cache(source: Path) -> None:
    """A cache written now, on the virtual clock."""
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, CACHE)
    now = Clock.now()
    os.utime(CACHE, (now, now))


def clear_data() -> None:
    """An empty DATA_DIR: no cache, and nothing any loader kept beside it."""
    if DATA_ROOT.exists():
        shutil.rmtree(DATA_ROOT)


# ---------------------------------------------------------------------------
# Driving the warmup
# ---------------------------------------------------------------------------

API: httpx.AsyncClient | None = None


async def served(full: bool = True) -> dict:
    """What the app serves right now, read only through its public endpoints."""
    status = (await API.get("/api/status")).json()
    seen = {
        "group": status.get("subway_static"),
        "archive": dict((status.get("static_archives") or {}).get("subway") or {}),
        "gtfs_age_s": (status.get("static_subway_gtfs") or {}).get("age_s"),
    }
    if full:
        stops = await API.get("/api/subway-stops")
        routes = await API.get("/api/subway-routes")
        rows = stops.json() if stops.status_code == 200 else []
        seen.update(
            stops_http=stops.status_code,
            stations=len(rows),
            index={r["id"]: r["routes"] for r in rows if r.get("routes")},
            route_lines=len(routes.json()) if routes.status_code == 200 else None,
        )
    return seen


def cache_is_valid() -> bool | None:
    """static_shared.cached_archive_is_valid on the cache, handed the staged validator
    HERE. What the validator says about the bytes, not the loader's decision."""
    if not CACHE.exists():
        return None
    return static_shared.cached_archive_is_valid(CACHE, static_data.validate_subway_archive)


def reset_app() -> None:
    """main.app's subway fields as main.lifespan sets them, and a clear archive record."""
    state = main.app.state
    state.subway_stops = None
    state.subway_routes = []
    state.subway_stations = {}
    state.subway_station_routes = {}
    state.subway_station_complexes = {}
    state.subway_static_status = "loading"
    static_shared.reset_archive_status()


class _Wedged(Exception):
    """A run of the warmup outlived the real-time watchdog."""


def until(n: int):
    """A plan that stops the loop at pause n and never moves the clock but by backoff."""
    return lambda pause, seen, delay: (pause < n, None)


async def drive_warmup(plan, *, full: bool = True) -> dict:
    """Run the REAL subway warmup, one attempt per turn of its retry loop.

    At each between-attempt sleep: stamp the attempt's writes onto the virtual clock,
    observe, ask the plan (keep going?, jump the clock to when?), then let the
    backoff the warmup asked for pass on the virtual clock.
    """
    reset_app()
    start_requests = Upstream.requests
    t0 = Clock.now()
    pauses: list[dict] = []

    async def hook(handled: BaseException | None, delay: float) -> bool:
        stamp_attempt_writes()
        seen = await served(full)
        seen.update(
            attempt=len(pauses) + 1,
            at=Clock.now() - t0,
            raised=type(handled).__name__ if handled is not None else None,
            raised_text=str(handled)[:90] if handled is not None else "",
            downloads=Upstream.requests - start_requests,
            cache=digest(CACHE),
            cache_valid=cache_is_valid() if full else None,
            delay=delay,
        )
        pauses.append(seen)
        keep, jump_to = plan(len(pauses), seen, delay)
        if keep:
            Clock.advance(delay)  # the backoff passes
            if jump_to is not None:
                Clock.advance_to(jump_to)
        Attempt.real_start = _REAL_TIME()
        return keep

    Attempt.real_start = _REAL_TIME()
    task = asyncio.create_task(warmups._warm_subway_static(main.app))
    _HOOKS[task] = hook
    loop = asyncio.get_running_loop()
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        loop.call_soon_threadsafe(task.cancel)

    watchdog = threading.Timer(WATCHDOG_S, expire)
    watchdog.daemon = True
    watchdog.start()
    try:
        await task
        outcome = "ready-returned"
    except _StopDriving:
        outcome = "stopped"
    except asyncio.CancelledError:
        if not expired.is_set():
            raise
        outcome = "wedged"
    finally:
        watchdog.cancel()
        _HOOKS.pop(task, None)
    if outcome == "wedged":
        raise _Wedged(f"the warmup was still running after {WATCHDOG_S:.0f} real seconds")
    stamp_attempt_writes()
    final = await served(True)
    final.update(
        downloads=Upstream.requests - start_requests, cache=digest(CACHE), at=Clock.now() - t0
    )
    attempts = len(pauses) + (1 if outcome == "ready-returned" else 0)
    return {"pauses": pauses, "outcome": outcome, "final": final, "attempts": attempts}


def staged_verdict(path: Path) -> str:
    try:
        static_shared.validate_archive(path, static_data.validate_subway_archive)
    except static_shared.StaticValidationError as exc:
        return f"REJECTED ({exc})"
    return "ACCEPTED"


def show_pause(p: dict) -> None:
    arch = p["archive"]
    print(
        f"    attempt {p['attempt']} at {elapsed(p['at'])}: raised {p['raised']}"
        + (f" ({p['raised_text']})" if p["raised_text"] else "")
    )
    print(
        f"      downloads so far {p['downloads']}, cache {p['cache']}, "
        f"cached_archive_is_valid(cache, staged validator) {p['cache_valid']}"
    )
    age = p["gtfs_age_s"]
    print(
        f"      /api/status subway_static={p['group']!r}  static_subway_gtfs.age_s="
        f"{'null' if age is None else f'{age:.0f}'} (MAX_AGE_DAYS is {MAX_AGE_S} s)"
    )
    print(
        f"      /api/status static_archives.subway: last_promoted_at="
        f"{'set' if arch.get('last_promoted_at') else None} "
        f"last_download_error={arch.get('last_download_error')!r} "
        f"failed_downloads={arch.get('failed_downloads')}"
    )


def show_final(r: dict) -> None:
    f = r["final"]
    print(
        f"    outcome {r['outcome']} after {r['attempts']} attempt(s), "
        f"{f['downloads']} download(s), cache {f['cache']}"
    )
    print(
        f"      /api/status subway_static={f['group']!r}; /api/subway-stops "
        f"{f['stations']} stations, {len(f['index'])} with routes; "
        f"/api/subway-routes {f['route_lines']} route lines"
    )


def promoted_clean(p: dict) -> bool:
    arch = p["archive"]
    return (
        arch.get("last_promoted_at") is not None
        and arch.get("last_download_error") is None
        and arch.get("failed_downloads") == 0
    )


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def mechanism() -> dict:
    rule("MECHANISM  what the publication validator asks of each required member")
    print("  Each row is one archive differing from the good one in a single member:")
    print("  absent, or present with one 0xFF byte. Verdicts are the real staged")
    print("  validator, cached_archive_is_valid handed that validator on the same bytes,")
    print("  and up to two cold-cache attempts of the real warmup with that archive")
    print("  published (the second shows whether the LOADER keeps the cache it failed on).")
    print(f"  good archive, staged validator: {staged_verdict(GOOD)}")
    rows = {}
    for member in MEMBERS:
        absent = staged_verdict(ABSENT[member])
        staged = staged_verdict(CORRUPT[member])
        as_cache = static_shared.cached_archive_is_valid(
            CORRUPT[member], static_data.validate_subway_archive
        )
        clear_data()
        Upstream.publishing = CORRUPT[member]
        run = await drive_warmup(until(2))
        first = run["pauses"][0] if run["pauses"] else None
        second = run["pauses"][1] if len(run["pauses"]) > 1 else None
        warm = f"failed: {first['raised']}" if first else f"{run['final']['group']}"
        promoted = bool((first or run["final"])["archive"].get("last_promoted_at"))
        rows[member] = {
            "absent_rejected": absent.startswith("REJECTED"),
            "corrupt_accepted": staged == "ACCEPTED",
            "cache_valid": as_cache,
            "warmup": warm,
            "promoted": promoted,
            "route_lines": run["final"]["route_lines"],
            "next_downloads": second["downloads"] if second else None,
        }
        print(f"  {member:<15} absent: {absent[:8]}  one 0xFF: {staged[:60]}")
        print(
            f"  {'':<15} as a cache: valid={as_cache}  warmup: {warm}, "
            f"promoted={promoted}, route lines served={run['final']['route_lines']}"
            + (
                f", downloads after attempt 2: {second['downloads']}"
                if second
                else ""
            )
        )
    print()
    required = [m for m in MEMBERS if rows[m]["absent_rejected"]]
    parsed = [m for m in MEMBERS if not rows[m]["corrupt_accepted"]]
    presence_only = [m for m in required if m not in parsed]
    gap = [m for m in presence_only if rows[m]["warmup"].startswith("failed")]
    agree = [m for m in MEMBERS if rows[m]["cache_valid"] == rows[m]["corrupt_accepted"]]
    kept = {m: rows[m]["next_downloads"] for m in gap}
    check(
        "M1 every one of the five members is required (absent -> rejected)",
        required == MEMBERS,
        required,
        "'verifies ... the presence of required tables'",
    )
    check(
        "M2 the validator parses exactly two of the five (one 0xFF -> rejected)",
        parsed == ["stops.txt", "transfers.txt"],
        parsed,
        "'verifies some members' but 'does not fully parse every table'",
    )
    check(
        "M3 static_shared.cached_archive_is_valid, handed the staged validator by this "
        "script, returns the staged verdict on all five",
        agree == MEMBERS,
        f"agrees on {agree}",
        "not stated (the spec: it runs that same validator)",
    )
    check(
        "M4 members accepted unreadable that the warmup then fails on",
        gap == ["trips.txt", "stop_times.txt"],
        gap,
        "trips.txt ('stop_times.txt' not named by the audit)",
    )
    shapes = rows["shapes.txt"]
    check(
        "M5 F08's instance still stands: unreadable shapes.txt accepted, promoted, "
        "ready with 0 route lines",
        shapes["corrupt_accepted"]
        and shapes["promoted"]
        and shapes["warmup"] == "ready"
        and shapes["route_lines"] == 0,
        f"accepted={shapes['corrupt_accepted']} promoted={shapes['promoted']} "
        f"warmup={shapes['warmup']} route lines={shapes['route_lines']}",
        "the prior audit's 'geometry-only corruption finding' (09-05 F08)",
    )
    check(
        "M6 the LOADER's own decision, by behavior: after the warmup fails on a cached "
        "unreadable member, the next attempt downloads nothing (usable and fresh)",
        bool(gap) and all(v == 1 for v in kept.values()),
        f"downloads after attempt 2: {kept}",
        "'Subsequent attempts accept the same newly cached archive as fresh'",
    )
    return {"parsed": parsed, "presence_only": presence_only, "gap": gap}


async def arm1() -> dict:
    rule("ARM 1  cold cache; the publication's trips.txt holds one 0xFF; three attempts")
    print(f"  staged validator on the bad archive: {staged_verdict(BAD)}")
    clear_data()
    Upstream.publishing = BAD
    run = await drive_warmup(until(3))
    for p in run["pauses"]:
        show_pause(p)
    show_final(run)
    pauses = run["pauses"]
    raised = [p["raised"] for p in pauses]
    last = pauses[-1] if pauses else {"archive": {}, "downloads": None}
    arch = last["archive"]
    print()
    check(
        "A1.1 three attempts, all raised UnicodeDecodeError",
        raised == ["UnicodeDecodeError"] * 3,
        raised,
        "'Three attempts all raised UnicodeDecodeError'",
    )
    check(
        "A1.2 download requests at the socket layer across the three attempts "
        "(the loader's own usable-and-fresh decision, by behavior)",
        [p["downloads"] for p in pauses] == [1, 1, 1],
        [p["downloads"] for p in pauses],
        "'only one download occurred'",
    )
    check(
        "A1.3 archive status after each attempt: promoted, no error, 0 failed downloads",
        len(pauses) == 3 and all(promoted_clean(p) for p in pauses),
        f"last_promoted_at={'set' if arch.get('last_promoted_at') else None}, "
        f"last_download_error={arch.get('last_download_error')!r}, "
        f"failed_downloads={arch.get('failed_downloads')}",
        "'reported a successful promotion and zero failed downloads'",
    )
    check(
        "A1.4 the cached archive is the bad publication, byte for byte",
        bool(pauses) and all(p["cache"] == digest(BAD) for p in pauses),
        f"cache {last.get('cache')} vs bad {digest(BAD)}",
        "'can replace the good archive'",
    )
    check(
        "A1.5 static_shared.cached_archive_is_valid(cache, validate_subway_archive), as "
        "this script calls it, on the cache the loader just failed on",
        len(pauses) == 3 and all(p["cache_valid"] is True for p in pauses),
        [p["cache_valid"] for p in pauses],
        "'accept the same newly cached archive as fresh' (spec: True)",
    )
    check(
        "A1.6 the group state /api/status publishes during the attempts",
        [p["group"] for p in pauses] == ["failed"] * 3,
        [p["group"] for p in pauses],
        "not stated",
    )
    # WHERE in the attempt it fails, by elimination: the same archive with a readable
    # trips.txt loads (ARM 2), and the station-route index loader is the step that
    # reads trips.txt. Asked once, standalone, of the cache the warmup just failed on.
    try:
        static_data.load_subway_station_routes()
        index_step = "returned"
    except Exception as exc:
        index_step = f"raised {type(exc).__name__}"
    check(
        "A1.7 the station-route index loader on that cache",
        index_step == "raised UnicodeDecodeError",
        index_step,
        "'fails while building the station-route index'",
    )
    return {"run": run, "raised": raised}


async def arm2() -> dict:
    rule("ARM 2  CONTROL: cold cache; the same archive with a readable trips.txt")
    print(f"  staged validator on the good archive: {staged_verdict(GOOD)}")
    clear_data()
    Upstream.publishing = GOOD
    run = await drive_warmup(until(3))
    show_final(run)
    f = run["final"]
    check(
        "A2.1 one attempt, one download, the group ready",
        (run["attempts"], f["downloads"], f["group"]) == (1, 1, "ready"),
        (run["attempts"], f["downloads"], f["group"]),
        "not stated",
    )
    check(
        "A2.2 the station-route index builds and is served",
        f["index"] == EXPECTED_INDEX and f["stations"] == EXPECTED_STATIONS,
        f"{f['stations']} stations, index {f['index']}",
        "not stated",
    )
    return {"run": run}


async def arm3() -> dict:
    rule("ARM 3  a good, recent cache on disk; then the warmup runs")
    clear_data()
    place_cache(GOOD)
    Upstream.publishing = GOOD  # a download, if one were made, would succeed
    print(
        f"  what 'fresh' means today: the loader's cache check passes and the mtime is "
        f"less than MAX_AGE_DAYS={MAX_AGE_DAYS} days old"
    )
    run = await drive_warmup(until(3))
    show_final(run)
    f = run["final"]
    check(
        "A3.1 no download, one attempt, the index builds from the cache",
        (f["downloads"], run["attempts"], f["group"], f["index"] == EXPECTED_INDEX)
        == (0, 1, "ready", True),
        f"downloads={f['downloads']} attempts={run['attempts']} group={f['group']!r} "
        f"index={f['index']}",
        "not stated",
    )
    return {"run": run}


def persistence_plan(state: dict):
    """Attempt 1 meets the bad publication; at pause 1 the repair is published. Then
    the real backoff schedule for SCHEDULE_WINDOW_S, then a jump to one hour short of
    MAX_AGE_DAYS after the bad promotion (two attempts there), then a jump to one hour
    past it. The plan labels each attempt with the phase it ran in."""

    def plan(pause: int, seen: dict, delay: float):
        seen["phase"] = state["phase"]
        if pause == 1:
            Upstream.publishing = GOOD  # the repair, published upstream
            state["t_bad"] = Clock.now()  # the bad promotion happened in attempt 1
        next_at = Clock.now() + delay
        if state["phase"] == "schedule" and next_at - state["t_bad"] >= SCHEDULE_WINDOW_S:
            state["phase"], state["near_from"] = "near", pause + 1
            return True, state["t_bad"] + MAX_AGE_S - 3600
        if state["phase"] == "near" and pause == state["near_from"] + 1:
            state["phase"] = "past"
            return True, state["t_bad"] + MAX_AGE_S + 3600
        if state["phase"] == "past":
            return False, None  # the attempt past MAX_AGE_DAYS failed too: stop
        return pause < 400, None

    return plan


def summarize_persistence(run: dict) -> dict:
    """Times are virtual, from the start of attempt 1 (the bad promotion)."""
    pauses = run["pauses"]
    phases: dict[str, list[dict]] = {"schedule": [], "near": [], "past": []}
    for p in pauses:
        phases.setdefault(p.get("phase", "schedule"), []).append(p)

    def span(ps: list[dict]) -> str:
        if not ps:
            return "none"
        first, last = ps[0], ps[-1]
        when = elapsed(first["at"])
        if last is not first:
            when += f" .. {elapsed(last['at'])}"
        return (
            f"attempts {first['attempt']}-{last['attempt']} ({len(ps)}) at {when}: raised "
            f"{sorted({p['raised'] or 'None' for p in ps})}, downloads "
            f"{sorted({p['downloads'] for p in ps})}, group {sorted({str(p['group']) for p in ps})}"
        )

    print(f"    schedule window: {span(phases['schedule'])}")
    print(f"    near the bound : {span(phases['near'])}")
    if phases["past"]:
        print(f"    past the bound : {span(phases['past'])}")
    f = run["final"]
    print(
        f"    outcome {run['outcome']} after {run['attempts']} attempts at "
        f"{elapsed(f['at'])}: {f['downloads']} download(s) in all, group "
        f"{f['group']!r}, index built {f['index'] == EXPECTED_INDEX}"
    )
    return phases


def persistence_checks(names: tuple[str, str], run: dict, phases: dict, state: dict) -> None:
    pauses = run["pauses"]
    first = pauses[0] if pauses else None
    before = phases["schedule"][1:] + phases["near"]
    check(
        f"{names[0]} with the repair published, no attempt before MAX_AGE_DAYS downloads it: "
        f"{SCHEDULE_WINDOW_S // 3600} h of the real backoff schedule, then two attempts "
        "at one hour short of MAX_AGE_DAYS after the bad promotion",
        first is not None
        and len(phases["schedule"]) >= 10
        and len(phases["near"]) == 2
        and all(
            p["downloads"] == 1 and p["raised"] == "UnicodeDecodeError" and p["group"] == "failed"
            for p in before
        ),
        f"{len(phases['schedule']) - 1 if phases['schedule'] else 0} further attempts in "
        f"the schedule window and {len(phases['near'])} near the bound; downloads "
        f"{sorted({p['downloads'] for p in before})}, raised "
        f"{sorted({p['raised'] or 'None' for p in before})}, group "
        f"{sorted({str(p['group']) for p in before})}",
        "'Subsequent attempts ... do not download a repair'",
    )
    f = run["final"]
    reached_past = state["phase"] == "past"
    check(
        f"{names[1]} one hour past MAX_AGE_DAYS the next attempt fetches the repair and the "
        "group is ready",
        reached_past
        and run["outcome"] == "ready-returned"
        and f["downloads"] == 2
        and f["group"] == "ready"
        and f["index"] == EXPECTED_INDEX
        and not phases["past"],
        f"outcome {run['outcome']}, {f['downloads']} download(s), group {f['group']!r}, "
        f"index built {f['index'] == EXPECTED_INDEX}, past the bound "
        f"{reached_past}, at {elapsed(f['at'])}",
        "not stated (Repair: 'healthy publication ... eventual recovery')",
    )


async def arm4() -> dict:
    rule("ARM 4  PERSISTENCE, cold cache: bad publication, then the repair published")
    print("  attempt 1: upstream publishes the bad archive; nothing is cached.")
    print("  pause 1  : upstream now publishes the repaired (good) archive.")
    print(f"  then     : the real backoff schedule for {SCHEDULE_WINDOW_S // 3600} h on the "
          "virtual clock; a jump to one hour")
    print(f"             short of MAX_AGE_DAYS ({MAX_AGE_DAYS} d) after the bad promotion; a "
          "jump to one hour past it.")
    clear_data()
    Upstream.publishing = BAD
    state = {"phase": "schedule"}
    run = await drive_warmup(persistence_plan(state), full=False)
    phases = summarize_persistence(run)
    pauses = run["pauses"]
    first = pauses[0] if pauses else None
    check(
        "A4.1 attempt 1 downloads the bad publication, promotes it and fails",
        first is not None
        and first["downloads"] == 1
        and first["cache"] == digest(BAD)
        and first["raised"] == "UnicodeDecodeError"
        and promoted_clean(first),
        f"downloads {first and first['downloads']}, cache {first and first['cache']} (bad "
        f"{digest(BAD)}), raised {first and first['raised']}, promoted cleanly "
        f"{first is not None and promoted_clean(first)}",
        "'can replace the good archive'",
    )
    persistence_checks(("A4.2", "A4.3"), run, phases, state)
    return {"run": run, "phases": phases}


async def arm5() -> dict:
    rule("ARM 5  CONTINUITY: good archive LOADED; a month later a bad publication; repair")
    print("  first    : the real warmup loads the good archive (cold cache, one download).")
    print(f"  restart  : the process restarts {MAX_AGE_DAYS + 1} days later on the virtual "
          "clock; upstream now publishes")
    print("             the bad archive. pause 1: the repair is published. Then as ARM 4.")
    clear_data()
    Upstream.publishing = GOOD
    pre = await drive_warmup(until(1))
    pf = pre["final"]
    print(
        f"    good load: outcome {pre['outcome']}, {pf['downloads']} download, group "
        f"{pf['group']!r}, {pf['stations']} stations, index built {pf['index'] == EXPECTED_INDEX}"
    )
    check(
        "A5.1 the good archive loads first (ready, the index served)",
        (pre["outcome"], pf["downloads"], pf["group"], pf["index"] == EXPECTED_INDEX)
        == ("ready-returned", 1, "ready", True),
        (pre["outcome"], pf["downloads"], pf["group"], pf["index"] == EXPECTED_INDEX),
        "'good cache'",
    )
    Clock.advance((MAX_AGE_DAYS + 1) * 86400)  # the next process start, a month later
    Upstream.publishing = BAD
    state = {"phase": "schedule"}
    run = await drive_warmup(persistence_plan(state), full=False)
    phases = summarize_persistence(run)
    pauses = run["pauses"]
    first = pauses[0] if pauses else None
    check(
        "A5.2 the restarted process downloads the bad publication over the good cache, "
        "fails, and the good bytes are gone from disk",
        first is not None
        and first["downloads"] == 1
        and first["cache"] == digest(BAD)
        and first["raised"] == "UnicodeDecodeError"
        and first["group"] == "failed"
        and promoted_clean(first),
        f"downloads {first and first['downloads']}, cache {first and first['cache']} (good "
        f"{digest(GOOD)}, bad {digest(BAD)}), raised {first and first['raised']}, group "
        f"{first and first['group']!r}",
        "'can replace the good archive' (Repair: 'retain the last fully usable snapshot')",
    )
    persistence_checks(("A5.3", "A5.4"), run, phases, state)
    return {"run": run, "phases": phases}


async def amain() -> int:
    global API
    print("=" * 78)
    print("A02  Subway archive validation can replace good data with a persistent failure")
    print("=" * 78)
    print(f"repo         : {REPO}")
    print(f"DATA_DIR     : {os.environ['DATA_DIR']}")
    print(f"upstream URL : {static_data.SUBWAY_GTFS_URL} (nothing listens; fake socket layer)")
    print(f"fixture      : {FIXTURE.relative_to(REPO)} (stops.txt, transfers.txt)")
    print(f"bad archive  : trips.txt + one 0xFF; good {digest(GOOD)}, bad {digest(BAD)}")
    transport = httpx.ASGITransport(app=main.app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://a02.local") as client:
            API = client
            mech = await mechanism()
            one = await arm1()
            await arm2()
            await arm3()
            four = await arm4()
            five = await arm5()
    except _Wedged as exc:
        print()
        check("RUN the warmup returned to its backoff sleep within the watchdog", False, exc, "n/a")
        print("FAILED CHECKS:")
        for name in FAILED:
            print(f"  - {name}")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1

    print()
    check(
        "RUN no request named NJ Transit or any URL but the subway seam",
        not Upstream.refused,
        Upstream.refused or "none",
        "n/a (containment)",
    )
    print()
    if FAILED:
        print("FAILED CHECKS:")
        for name in FAILED:
            print(f"  - {name}")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    window = [
        len(four["phases"]["schedule"]) - 1,
        len(five["phases"]["schedule"]) - 1,
    ]
    print(
        "DISPOSITION: VERIFIED  A cold-cache publication whose trips.txt alone holds one "
        "0xFF byte passed the real staged validator and was promoted (archive status: "
        "promoted, no error, 0 failed downloads), then the real subway warmup failed "
        f"{len(one['raised'])} of {len(one['raised'])} attempts with UnicodeDecodeError "
        "on 1 download while /api/status reported the group failed. The validator parses "
        f"{len(mech['parsed'])} of its 5 required members ({', '.join(mech['parsed'])}), "
        f"so {' and '.join(mech['gap'])} are promoted unreadable and the loader keeps "
        "the failing cache as usable and fresh: with a repair published after the first "
        f"failure, {window[0]} and {window[1]} further attempts over "
        f"{SCHEDULE_WINDOW_S // 3600} h of the real backoff schedule (from a cold cache, "
        "and from a good archive loaded a month earlier) plus 2 each at MAX_AGE_DAYS "
        f"({MAX_AGE_DAYS}) minus 1 h made no further download, and the first attempt "
        "1 h past MAX_AGE_DAYS fetched it and reached ready (shapes.txt, F08, still "
        "promotes and serves 0 route lines)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
