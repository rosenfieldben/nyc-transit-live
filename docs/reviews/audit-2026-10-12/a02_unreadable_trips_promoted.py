"""A02 (High), "Subway archive validation can replace good data with a persistent failure":
the repair, pinned.

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

FIXED ON claude/a02-publication-validation, by one rule: the validator is the
loader's parser. Every member the subway load reads is read at validation through the
one generator the load reads it with (static_data's _iter_* functions, drained to a
count by the validator and accumulated by the loader), and must yield at least one row;
and the three the routes-per-station index is built from must JOIN, one call of a trip
with a route at a stop that folds to a station (the join floor, the sixth row below).
So an archive the load cannot read, or whose index would be empty, is refused at
staging and the last-known-good keeps serving. shapes.txt is read by a gate of its own,
by the operator's ruling that geometry is decorative, and its failure follows the
two-mode rule (static_shared.staged_fetch): refused over a cache that serves, promoted
with shapes.txt named a degraded member on a cold start (M5g; 09-05's F08 pins both
arms). Behind it, a residual arm discards a cached archive that passes validation and
still fails the load on the archive's own bytes, so the next attempt downloads (ARM 1's
bypass check reaches it).

BEFORE (this script's record at d5f6d70, the numbers the ledger's A02 row carries):
the validator parsed 2 of its 5 required members (stops.txt and transfers.txt) and
checked the others for presence. A cold-cache publication whose trips.txt alone held
one 0xFF byte was promoted (archive status: promoted, no error, 0 failed downloads),
then 3 of 3 warmup attempts raised UnicodeDecodeError on 1 download while
cached_archive_is_valid kept answering True over the cache the loader had just failed
on; stop_times.txt had the same gap; shapes.txt (2026-09-05's F08) was promoted and
served 0 route lines. A repair published right after the bad promotion was not fetched
by any of 75 further attempts over 6 h of the real backoff (74 from a good archive
loaded a month earlier) or at MAX_AGE_DAYS less an hour; the first attempt an hour
past MAX_AGE_DAYS fetched it.

AFTER, asserted below: every one of the five members is refused with one 0xFF byte by
the gate that reads it, by name and with the codec's own words; an archive whose tables
do not join is refused by the always-gate, naming stop_times.txt; on a cold cache the
four the always-gate reads, and the unjoinable archive, are refused on every attempt,
while bad shapes.txt is promoted with every station served, 0 route lines and
shapes.txt named a degraded member on /api/status; the trips.txt and stop_times.txt corruptions
are refused at staging on every attempt from a cold cache (3 refusals, 3 downloads,
0 promotions, no cache on disk, the group failed and retrying); a repair published
after the first refusal is fetched and promoted by the very next attempt, one backoff
rung later; from a good archive loaded a month earlier the same bad publication is
refused while the good archive keeps serving all 496 stations, the station-route index
and the route lines, and the repair is promoted at the next start.

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
       through the env_seams C6 seams. The two seams that set the retry schedule are
       NOT set, and the record is taken at their shipped values or not at all: after
       the backend import, STATIC_RETRY_S and STATIC_RETRY_SCHEDULE_S, as env_seams
       parses them from the environment and from the .env its load_dotenv reads, are
       compared with env_seams' own registry defaults, and an override stops the run
       with a message naming the variable (exit 1, no DISPOSITION line) instead of a
       red that blames the finding.
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
       _ns forms) read the real clock plus an offset that only grows. So do the
       clocks that bypass time.time and read the C-level system clock:
       datetime.datetime.now() and utcnow(), the argument-less time.gmtime(),
       localtime(), ctime(), asctime() and strftime(fmt), and
       time.clock_gettime(_ns) on the wall and monotonic clocks. date.today() and
       datetime.today(), which CPython 3.12 computes from time.time, are set too,
       so that does not rest on the C module. (Before the adversarial review now()
       and utcnow() were not moved, and a cache-discard grace timed by
       datetime.now() left this script green.) The datetime methods are moved on
       the REAL classes, by writing into the built-in type's own namespace and
       calling PyType_Modified, not by rebinding a subclass over them: a stand-in
       class changes what isinstance and type() answer, and the one tried here
       broke the backend's import (a metaclass conflict in pydantic.v1). No class
       identity changes (measured once while writing this: the backend's own 1763
       tests pass unchanged with the injection installed), and a RUN check fails
       the run if any of these clocks does not read the virtual time. Timers on
       the event loop follow the offset too: loop.call_later, and an asyncio.sleep
       in any other task, are scheduled on loop.time(), which reads
       time.monotonic. The offset
       advances by exactly the backoff each failed attempt asks for (item 4), and
       by an explicit jump where an arm says time passes (a restart a month later,
       the run-up to MAX_AGE_DAYS). The file system stamps writes with the REAL
       clock, so after each attempt every file under DATA_DIR whose mtime falls
       inside that attempt's real-time window is moved onto the virtual clock (an
       mtime production code set explicitly, to a computed time, is outside the
       window and left as set). random is seeded, so the warmup's backoff jitter,
       and with it every virtual time printed, repeats run to run, give or take the
       second of real time the run itself adds to each.
       NOT MOVED: a clock read inside C code (no backend module is C), the CPU-time
       clocks, waits that block a thread in real time (threading.Timer, a threading
       wait with a timeout), and file mtimes outside DATA_DIR. A repair timed by one
       of those would not be seen to fire, and would leave this script green
       (measured: a discard on threading.Timer(3600) does).
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
       at the end of the first trip's headsign in trips.txt; BAD_STOP_TIMES puts the
       same byte at the end of stop_times.txt's first row instead. The REPAIR is the
       GOOD archive with the first headsign reworded, so it serves the same index and
       its promotion is visible as a change of cache bytes. JOIN_BROKEN is the GOOD
       archive with trips.txt's route_id header renamed to route: every member reads
       and holds rows, and no call belongs to a trip with a route.
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
Runs in about two seconds. The virtual clock carried the record's month-long
persistence arms; for the repair it carries the backoff between a refusal and the
attempt that fetches the repair, and ARM 5's month between two process starts.

RUN IT, from the repository root:
    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a02_unreadable_trips_promoted.py

Exits 0 while the repair holds, 1 with every check that moved named if it does not.

RECORDED DISPOSITION: FIXED (claude/a02-publication-validation). The BEFORE paragraph
above is the record this script used to pin; the checks below pin the AFTER, and each
prints the BEFORE value beside the one it measures, so a regression reads as the
finding coming back.
"""

from __future__ import annotations

import asyncio
import atexit
import calendar
import csv
import ctypes
import datetime
import gc
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
import warnings
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


# The calendar clocks (docstring item 3). datetime.datetime.now() and utcnow() read
# the C-level system clock, not time.time, so the lines above never reach them: an
# adversarial review timed a cache-discard grace on datetime.now() and this script
# stayed green while the same timer on time.time() turned it red. They are moved ON
# THE REAL CLASSES, not by rebinding a subclass over them on the datetime module. A
# plain stand-in subclass changes what isinstance, type() and pydantic's
# `obj is datetime.datetime` answer for every datetime made where the real class is
# still bound (pydantic-core makes them in C); one given a metaclass to answer as the
# real class broke the backend's own import when tried here (pydantic.v1 derives a
# class from date with a metaclass of its own: a metaclass conflict). So the classes,
# and every check made against them, stay what they are; only these clock methods
# read Clock. The argument-less calendar functions of the time module and
# clock_gettime on the wall and monotonic clocks read the same C clock and move with
# them. A run where any of this failed to take is red, by the RUN check at the end.
def install_virtual_calendar(clock) -> None:
    """Put the calendar clocks on `clock` (its now() and offset). Once, before any
    backend import, so a module that binds a name at import binds the moved one."""

    def set_on_builtin_type(cls: type, name: str, value: object) -> None:
        # A built-in type refuses setattr. Its namespace is the one object its
        # __dict__ proxy refers to: write there, then tell the interpreter that its
        # attribute caches for the type, and for every subclass, are stale.
        (namespace,) = gc.get_referents(cls.__dict__)
        namespace[name] = value
        ctypes.pythonapi.PyType_Modified.argtypes = [ctypes.py_object]
        ctypes.pythonapi.PyType_Modified.restype = None
        ctypes.pythonapi.PyType_Modified(cls)

    def today(cls):  # date.today, and datetime.today through inheritance
        return cls.fromtimestamp(clock.now())

    def now(cls, tz=None):
        return cls.fromtimestamp(clock.now(), tz)

    def utcnow(cls):  # with the real one's deprecation warning, word for word
        warnings.warn(
            "datetime.datetime.utcnow() is deprecated and scheduled for removal in a "
            "future version. Use timezone-aware objects to represent datetimes in UTC: "
            "datetime.datetime.now(datetime.UTC).",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.fromtimestamp(clock.now(), datetime.timezone.utc).replace(tzinfo=None)

    set_on_builtin_type(datetime.date, "today", classmethod(today))
    set_on_builtin_type(datetime.datetime, "now", classmethod(now))
    set_on_builtin_type(datetime.datetime, "utcnow", classmethod(utcnow))

    gmtime, localtime, ctime = time.gmtime, time.localtime, time.ctime
    asctime, strftime = time.asctime, time.strftime
    time.gmtime = lambda secs=None: gmtime(clock.now() if secs is None else secs)
    time.localtime = lambda secs=None: localtime(clock.now() if secs is None else secs)
    time.ctime = lambda secs=None: ctime(clock.now() if secs is None else secs)
    time.asctime = lambda t=None: asctime(localtime(clock.now()) if t is None else t)
    time.strftime = lambda fmt, t=None: strftime(fmt, localtime(clock.now()) if t is None else t)
    moved = {
        getattr(time, name)
        for name in ("CLOCK_REALTIME", "CLOCK_MONOTONIC", "CLOCK_MONOTONIC_RAW",
                     "CLOCK_BOOTTIME", "CLOCK_TAI", "CLOCK_UPTIME", "CLOCK_UPTIME_RAW")
        if hasattr(time, name)
    }
    if hasattr(time, "clock_gettime"):
        gettime, gettime_ns = time.clock_gettime, time.clock_gettime_ns
        time.clock_gettime = lambda clk: gettime(clk) + (clock.offset if clk in moved else 0.0)
        time.clock_gettime_ns = lambda clk: gettime_ns(clk) + (
            int(clock.offset * 1e9) if clk in moved else 0
        )


install_virtual_calendar(Clock)


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

import env_seams  # noqa: E402
import main  # noqa: E402
import static_data  # noqa: E402
import static_shared  # noqa: E402
import warmups  # noqa: E402

_hermetic.verify()
# Nothing here drives NJ Transit or Bus Time, but blank anything that could
# authenticate (blanked, not popped: see _hermetic.blank for why a pop is undone).
_hermetic.blank("NJT_USERNAME", "NJT_PASSWORD", "BUS_TIME_API_KEY")


# The record is measured at the shipped retry schedule. Every count below is walked
# off the warmup's backoff, which these two env seams set, and env_seams' load_dotenv
# fills them from a developer's .env: STATIC_RETRY_S=10 there turned this script red
# with a message that blamed the finding (399 further attempts in the window, the
# bound never reached). So each is compared with env_seams' own registry default,
# through env_seams' own parser (the value the backend read at import, wherever it
# keeps it), and a difference stops the run naming the variable instead.
TIMING_SEAMS = ("STATIC_RETRY_S", "STATIC_RETRY_SCHEDULE_S")


def timing_overrides() -> list[str]:
    found = []
    for name in TIMING_SEAMS:
        if name not in env_seams.DEFAULTS or os.environ.get(name) is None:
            continue
        default = env_seams.DEFAULTS[name]
        parse = env_seams.seconds_tuple if isinstance(default, tuple) else env_seams.seconds
        if parse(name, default) != default:
            found.append(f"{name}={os.environ[name]!r} (shipped default {default!r})")
    return found


_OVERRIDDEN = timing_overrides()
if _OVERRIDDEN:
    raise SystemExit(
        "A02 NOT RUN: this record is measured at the shipped retry schedule, and the "
        f"environment (or a .env file, which env_seams loads) overrides "
        f"{'; '.join(_OVERRIDDEN)}. Every attempt count and virtual time it prints is "
        "walked off that schedule, so a run under the override says nothing about the "
        "finding. Unset it and run again."
    )


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
BAD_STOP_TIMES = write_zip(
    UPSTREAM_DIR / "bad_stop_times.zip",
    {**GOOD_MEMBERS, "stop_times.txt": with_invalid_byte(STOP_TIMES_BYTES)},
)
# The repair: readable everywhere, the same trips and calls (so the same index), and
# different bytes, so its promotion shows as a change of cache digest.
REPAIR = write_zip(
    UPSTREAM_DIR / "repair.zip",
    {
        **GOOD_MEMBERS,
        "trips.txt": TRIPS_BYTES.replace(b"Van Cortlandt Park-242 St", b"Van Cortlandt Park"),
    },
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
# THE SIXTH ROW: every member present and readable, every one with rows, and trips.txt's
# route_id column renamed, so no call belongs to a trip with a route and the
# routes-per-station index the load builds is empty (MR2 F1's empty index, reached
# through readable tables). The join floor is what refuses it.
JOIN_BROKEN = write_zip(
    UPSTREAM_DIR / "join_route_id_renamed.zip",
    {**GOOD_MEMBERS, "trips.txt": TRIPS_BYTES.replace(b"route_id,", b"route,", 1)},
)
JOIN_MEMBER = "stop_times.txt"  # the member the join's refusal names: its rows are the calls
JOIN_REASON = (
    "stop_times.txt yielded no usable call of a trip that has a route_id at a stop that "
    "folds to a parent station (trips.txt, stop_times.txt and stops.txt do not join, so the "
    "routes-per-station index would be empty)"
)

CACHE = static_data.SUBWAY_GTFS_ZIP
MAX_AGE_DAYS = static_data.MAX_AGE_DAYS
MAX_AGE_S = MAX_AGE_DAYS * 86400
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


def staged_verdict(path: Path, gate=None) -> str:
    """One of the REAL gates over an archive file: the always-gate by default."""
    try:
        static_shared.validate_archive(path, gate or static_data.validate_subway_archive)
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

BRANCH = "claude/a02-publication-validation"


def refused_by_name(verdict: str, member: str) -> bool:
    """The staged validator's refusal names the member and carries the codec's words."""
    return verdict.startswith(f"REJECTED ({member} is not readable as CSV (") and (
        "can't decode byte 0xff" in verdict
    )


# Which gate reads which member. shapes.txt is read by the route lines' own gate, which
# staged_fetch runs under the two-mode rule; the always-gate does not ask for it.
GEOMETRY = "shapes.txt"
ALWAYS = [m for m in MEMBERS if m != GEOMETRY]


def gate_for(member: str):
    if member == GEOMETRY:
        return static_data.validate_subway_geometry
    return static_data.validate_subway_archive


async def mechanism() -> dict:
    rule("MECHANISM  what each gate asks of each member, and of the join")
    print("  Each row is one archive differing from the good one in a single member:")
    print("  absent, or present with one 0xFF byte; the sixth differs in one trips.txt")
    print("  header (route_id renamed), so every member reads and the tables do not join.")
    print("  Verdicts are the real gate that reads the member (the always-gate,")
    print("  validate_subway_archive, or for shapes.txt the geometry gate,")
    print("  validate_subway_geometry), cached_archive_is_valid handed the always-gate on")
    print("  the same bytes, and two cold-cache attempts of the real warmup with that")
    print("  archive published (the second shows whether the loader cached anything it")
    print("  cannot read).")
    print(f"  good archive: always-gate {staged_verdict(GOOD)}, geometry gate "
          f"{staged_verdict(GOOD, static_data.validate_subway_geometry)}")
    rows = {}
    for member in [*MEMBERS, "join"]:
        bad = JOIN_BROKEN if member == "join" else CORRUPT[member]
        gate = static_data.validate_subway_archive if member == "join" else gate_for(member)
        absent = "n/a" if member == "join" else staged_verdict(ABSENT[member], gate)
        always_absent = "n/a" if member == "join" else staged_verdict(ABSENT[member])
        staged = staged_verdict(bad, gate)
        as_cache = static_shared.cached_archive_is_valid(bad, static_data.validate_subway_archive)
        clear_data()
        Upstream.publishing = bad
        run = await drive_warmup(until(2))
        pauses = run["pauses"]
        final = run["final"]
        rows[member] = {
            "absent_rejected": absent.startswith("REJECTED"),
            "always_absent": always_absent,
            "refused": staged.startswith("REJECTED"),
            "named": (
                staged == f"REJECTED ({JOIN_REASON})"
                if member == "join"
                else refused_by_name(staged, member)
            ),
            "cache_valid": as_cache,
            "raised": [p["raised"] for p in pauses],
            "promoted": any(p["archive"].get("last_promoted_at") for p in pauses),
            "cached": [p["cache"] for p in pauses],
            "downloads": [p["downloads"] for p in pauses],
            "outcome": run["outcome"],
            "final": final,
        }
        label = "join" if member == "join" else member
        gate_name = "geometry" if gate is static_data.validate_subway_geometry else "always"
        print(f"  {label:<15} [{gate_name} gate] absent: {absent[:8]}  "
              f"{'route_id renamed' if member == 'join' else 'one 0xFF'}: {staged[:84]}")
        if pauses:
            print(
                f"  {'':<15} as a cache: valid={as_cache}  warmup: raised {rows[member]['raised']}, "
                f"promoted={rows[member]['promoted']}, downloads by attempt "
                f"{rows[member]['downloads']}"
            )
        else:
            arch = final["archive"]
            print(
                f"  {'':<15} as a cache: valid={as_cache}  warmup: {run['outcome']} on attempt "
                f"{run['attempts']}, {final['downloads']} download, group {final['group']!r}, "
                f"{final['stations']} stations, {final['route_lines']} route lines"
            )
            print(f"  {'':<15} degraded_members={arch.get('degraded_members')!r}")
    print()
    every = [*MEMBERS, "join"]
    refusing = [*ALWAYS, "join"]  # what the always-gate refuses, so no mode promotes
    required = [m for m in MEMBERS if rows[m]["absent_rejected"]]
    parsed = [m for m in every if rows[m]["refused"]]
    named = [m for m in every if rows[m]["named"]]
    agree = [m for m in every if rows[m]["cache_valid"] is False]
    reached = [
        m
        for m in refusing
        if rows[m]["promoted"]
        or rows[m]["outcome"] != "stopped"
        or rows[m]["raised"] != ["StaticValidationError"] * 2
        or rows[m]["cached"] != ["(absent)"] * 2
    ]
    redownload = {m: rows[m]["downloads"] for m in refusing}
    geo = rows[GEOMETRY]
    gf = geo["final"]
    garch = gf["archive"]
    check(
        "M1 every member the load reads is required by the gate that reads it (absent -> "
        "refused), and shapes.txt by the geometry gate alone",
        required == MEMBERS and rows[GEOMETRY]["always_absent"] == "ACCEPTED",
        f"{required}; the always-gate on an archive without shapes.txt: "
        f"{rows[GEOMETRY]['always_absent'][:8]}",
        "'verifies ... the presence of required tables' (BEFORE: all five by the one gate)",
    )
    check(
        "M2 each gate parses its members (one 0xFF -> refused), and the always-gate refuses "
        "tables that do not join (the sixth row)",
        parsed == every,
        parsed,
        "BEFORE: two of five, ['stops.txt', 'transfers.txt'], and no join",
    )
    check(
        "M3 each refusal names its member and carries the codec's own words (the join's names "
        "stop_times.txt and says the index would be empty), which is what archive status and "
        "/api/status publish",
        named == every,
        f"named on {named}",
        "BEFORE: no refusal to name for trips.txt, stop_times.txt, shapes.txt or the join",
    )
    check(
        "M4 static_shared.cached_archive_is_valid, handed the always-gate, refuses the four "
        "and the join as a cache, and keeps a cache whose geometry does not draw (it serves "
        "every station; geometry is decorative)",
        agree == refusing and geo["cache_valid"] is True,
        f"refuses {agree}; on bad shapes.txt {geo['cache_valid']}",
        "BEFORE: True on trips.txt, stop_times.txt, shapes.txt and the join",
    )
    check(
        "M5 no member the always-gate reads, and no unjoinable archive, reaches the warmup "
        "from a cold cache: each attempt is refused at staging, nothing is promoted, no cache "
        "is written",
        reached == [],
        f"got past staging: {reached}",
        "BEFORE: trips.txt and stop_times.txt promoted then UnicodeDecodeError; the join "
        "promoted with an empty index (0 of 496 stations with routes)",
    )
    check(
        "M5g shapes.txt on a cold cache, the two-mode rule's second mode: promoted, ready on "
        "the first attempt, every station and the index served, 0 route lines, and "
        "/api/status names shapes.txt as a degraded member with the gate's reason",
        geo["outcome"] == "ready-returned"
        and gf["downloads"] == 1
        and gf["group"] == "ready"
        and gf["stations"] == EXPECTED_STATIONS
        and gf["index"] == EXPECTED_INDEX
        and gf["route_lines"] == 0
        and bool(garch.get("last_promoted_at"))
        and garch.get("last_download_error") is None
        and list(garch.get("degraded_members") or {}) == [GEOMETRY]
        and refused_by_name(f"REJECTED ({garch['degraded_members'][GEOMETRY]})", GEOMETRY),
        f"{geo['outcome']}, {gf['downloads']} download, group {gf['group']!r}, "
        f"{gf['stations']} stations, index built {gf['index'] == EXPECTED_INDEX}, "
        f"{gf['route_lines']} route lines, degraded_members={garch.get('degraded_members')!r}",
        "BEFORE: promoted, ready with 0 route lines and nothing on the status saying why "
        "(F08's second half)",
    )
    check(
        "M6 the LOADER's decision, by behavior: the attempt after a refusal downloads again, "
        "because nothing unreadable or unjoinable was cached as usable and fresh",
        all(v == [1, 2] for v in redownload.values()),
        f"downloads by attempt {redownload}",
        "BEFORE: [1, 1] for trips.txt and stop_times.txt ('accept the same newly cached "
        "archive as fresh')",
    )
    return {"parsed": parsed}


async def arm1(member: str, bad: Path, label: str) -> dict:
    rule(f"ARM {label}  cold cache; the publication's {member} holds one 0xFF; three attempts")
    print(f"  staged validator on the bad archive: {staged_verdict(bad)}")
    clear_data()
    Upstream.publishing = bad
    run = await drive_warmup(until(3))
    for p in run["pauses"]:
        show_pause(p)
    show_final(run)
    pauses = run["pauses"]
    raised = [p["raised"] for p in pauses]
    texts = [p["raised_text"] for p in pauses]
    last = pauses[-1] if pauses else {"archive": {}}
    arch = last["archive"]
    print()
    check(
        f"A{label}.1 three attempts, each refused at staging, naming {member}",
        raised == ["StaticValidationError"] * 3 and all(t.startswith(member) for t in texts),
        list(zip(raised, [t[:40] for t in texts])),
        "BEFORE: ['UnicodeDecodeError'] * 3 ('Three attempts all raised UnicodeDecodeError')",
    )
    check(
        f"A{label}.2 download requests at the socket layer, cumulative by attempt: every "
        "attempt asks again, because nothing it could not read was kept",
        [p["downloads"] for p in pauses] == [1, 2, 3],
        [p["downloads"] for p in pauses],
        "BEFORE: [1, 1, 1] ('only one download occurred')",
    )
    check(
        f"A{label}.3 archive status after each attempt: never promoted, the failed downloads "
        f"counted, the error naming {member} and the codec's words",
        len(pauses) == 3
        and all(p["archive"].get("last_promoted_at") is None for p in pauses)
        and [p["archive"].get("failed_downloads") for p in pauses] == [1, 2, 3]
        and all(
            refused_by_name(
                "REJECTED (" + (p["archive"].get("last_download_error") or "")
                .removeprefix("invalid archive: ") + ")",
                member,
            )
            for p in pauses
        ),
        f"last_promoted_at={arch.get('last_promoted_at')}, failed_downloads="
        f"{[p['archive'].get('failed_downloads') for p in pauses]}, "
        f"last_download_error={arch.get('last_download_error')!r}",
        "BEFORE: promoted, no error, 0 failed downloads",
    )
    check(
        f"A{label}.4 no cache on disk after any attempt",
        bool(pauses) and all(p["cache"] == "(absent)" for p in pauses),
        [p["cache"] for p in pauses],
        "BEFORE: the bad publication, byte for byte ('can replace the good archive')",
    )
    standalone = static_shared.cached_archive_is_valid(bad, static_data.validate_subway_archive)
    check(
        f"A{label}.5 static_shared.cached_archive_is_valid(bad archive, validate_subway_archive)",
        standalone is False,
        standalone,
        "BEFORE: True ('accept the same newly cached archive as fresh')",
    )
    check(
        f"A{label}.6 the group state /api/status publishes during the attempts",
        [p["group"] for p in pauses] == ["failed"] * 3,
        [p["group"] for p in pauses],
        "not stated",
    )
    # THE BYPASS: the bad archive placed as the cache by hand, which nothing in the app
    # does any more, so the load's reader of the member is asked directly. It still
    # cannot read the byte, and the residual arm discards the cache it failed on, so the
    # next attempt would download rather than accept the same bytes for a month.
    place_cache(bad)
    try:
        static_data.load_subway_station_routes()
        index_step = "returned"
    except Exception as exc:
        index_step = f"raised {type(exc).__name__}"
    discarded = not CACHE.exists()
    clear_data()
    check(
        f"A{label}.7 validation bypassed (the bad archive placed as the cache by hand): the "
        "station-route index loader raises on it and the cache is discarded",
        index_step == "raised UnicodeDecodeError" and discarded,
        f"{index_step}; cache discarded: {discarded}",
        "BEFORE: raised UnicodeDecodeError and kept the cache ('fails while building the "
        "station-route index')",
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
    rule("ARM 3  CONTROL: a good, recent cache on disk; then the warmup runs")
    clear_data()
    place_cache(GOOD)
    Upstream.publishing = GOOD  # a download, if one were made, would succeed
    print(
        f"  what 'fresh' means: the loader's cache check passes and the mtime is less than "
        f"MAX_AGE_DAYS={MAX_AGE_DAYS} days old"
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


def repair_after_first(pause: int, seen: dict, delay: float):
    """At the first pause (attempt 1 refused the bad publication), publish the repair;
    allow up to four attempts, so a repair that waited would be seen to wait."""
    if pause == 1:
        Upstream.publishing = REPAIR
    return pause < 4, None


async def arm4() -> dict:
    rule("ARM 4  RECOVERY, cold cache: a bad publication, then the repair published")
    print("  attempt 1: upstream publishes the bad archive; nothing is cached.")
    print("  pause 1  : upstream now publishes the repair; the warmup's own backoff passes.")
    clear_data()
    Upstream.publishing = BAD
    run = await drive_warmup(repair_after_first)
    pauses = run["pauses"]
    for p in pauses:
        show_pause(p)
    show_final(run)
    first = pauses[0] if pauses else None
    f = run["final"]
    check(
        "A4.1 attempt 1 downloads the bad publication and refuses it at staging: nothing "
        "promoted, no cache",
        first is not None
        and first["downloads"] == 1
        and first["raised"] == "StaticValidationError"
        and first["cache"] == "(absent)"
        and first["archive"].get("last_promoted_at") is None,
        f"downloads {first and first['downloads']}, raised {first and first['raised']}, cache "
        f"{first and first['cache']}",
        "BEFORE: promoted, then UnicodeDecodeError ('can replace the good archive')",
    )
    check(
        "A4.2 the very next attempt, one backoff rung later, fetches and promotes the repair "
        "and the group is ready with the index built",
        run["outcome"] == "ready-returned"
        and run["attempts"] == 2
        and f["downloads"] == 2
        and f["cache"] == digest(REPAIR)
        and f["group"] == "ready"
        and f["index"] == EXPECTED_INDEX
        and bool(f["archive"].get("last_promoted_at")),
        f"outcome {run['outcome']} after {run['attempts']} attempts at {elapsed(f['at'])}, "
        f"{f['downloads']} downloads, cache {f['cache']} (repair {digest(REPAIR)}), group "
        f"{f['group']!r}, index built {f['index'] == EXPECTED_INDEX}",
        "BEFORE: not fetched by 75 further attempts over 6 h of the real backoff, nor at "
        "MAX_AGE_DAYS less 1 h; fetched 1 h past MAX_AGE_DAYS",
    )
    return {"run": run}


async def arm5() -> dict:
    rule("ARM 5  CONTINUITY: good archive LOADED; a month later a bad publication; repair")
    print("  first    : the real warmup loads the good archive (cold cache, one download).")
    print(f"  restart  : the process restarts {MAX_AGE_DAYS + 1} days later on the virtual "
          "clock; upstream now publishes the bad archive.")
    print("  restart  : the next process start, upstream now publishing the repair.")
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
    run = await drive_warmup(until(3))
    show_final(run)
    f = run["final"]
    arch = f["archive"]
    check(
        "A5.2 the restarted process downloads the bad publication, refuses it, and reaches "
        "ready on that attempt with the good archive serving every layer",
        run["outcome"] == "ready-returned"
        and run["attempts"] == 1
        and f["downloads"] == 1
        and f["cache"] == digest(GOOD)
        and f["group"] == "ready"
        and f["stations"] == EXPECTED_STATIONS
        and f["index"] == EXPECTED_INDEX
        and f["route_lines"] == 3
        and arch.get("last_promoted_at") is None
        and arch.get("failed_downloads") == 1
        and (arch.get("last_download_error") or "").startswith(
            "invalid archive: trips.txt is not readable as CSV ("
        ),
        f"outcome {run['outcome']} after {run['attempts']} attempt(s), {f['downloads']} "
        f"download(s), cache {f['cache']} (good {digest(GOOD)}), {f['stations']} stations, "
        f"index built {f['index'] == EXPECTED_INDEX}, {f['route_lines']} route lines; archive "
        f"status failed_downloads={arch.get('failed_downloads')}, "
        f"last_download_error={arch.get('last_download_error')!r}",
        "BEFORE: the bad publication replaced the good cache (the good bytes gone from disk) "
        "and every attempt failed until MAX_AGE_DAYS after it ('retain the last fully usable "
        "snapshot')",
    )
    Upstream.publishing = REPAIR
    nxt = await drive_warmup(until(3))
    show_final(nxt)
    g = nxt["final"]
    check(
        "A5.3 the next start downloads and promotes the repair over the good archive",
        nxt["outcome"] == "ready-returned"
        and nxt["attempts"] == 1
        and g["downloads"] == 1
        and g["cache"] == digest(REPAIR)
        and g["index"] == EXPECTED_INDEX
        and bool(g["archive"].get("last_promoted_at"))
        and g["archive"].get("last_download_error") is None,
        f"outcome {nxt['outcome']}, {g['downloads']} download, cache {g['cache']} (repair "
        f"{digest(REPAIR)}), index built {g['index'] == EXPECTED_INDEX}",
        "BEFORE: not fetched until MAX_AGE_DAYS after the bad promotion",
    )
    return {"run": run, "next": nxt}


async def amain() -> int:
    global API
    print("=" * 78)
    print("A02  Subway archive validation can replace good data (the repair)")
    print("=" * 78)
    print(f"repo         : {REPO}")
    print(f"DATA_DIR     : {os.environ['DATA_DIR']}")
    print(f"upstream URL : {static_data.SUBWAY_GTFS_URL} (nothing listens; fake socket layer)")
    print(f"fixture      : {FIXTURE.relative_to(REPO)} (stops.txt, transfers.txt)")
    print(
        f"archives     : good {digest(GOOD)}, bad trips.txt {digest(BAD)}, bad stop_times.txt "
        f"{digest(BAD_STOP_TIMES)}, repair {digest(REPAIR)}"
    )
    print(
        "retry seams  : "
        + ", ".join(f"{n}={env_seams.DEFAULTS.get(n, '(not a seam)')}" for n in TIMING_SEAMS)
        + " (the shipped defaults; no override in the environment or .env)"
    )
    transport = httpx.ASGITransport(app=main.app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://a02.local") as client:
            API = client
            mech = await mechanism()
            one = await arm1("trips.txt", BAD, "1")
            one_b = await arm1("stop_times.txt", BAD_STOP_TIMES, "1b")
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
    # The injection, measured where it is now a month ahead of real time: each calendar
    # clock a repair could be timed by reads the virtual clock. A Python on which a
    # rebinding stopped taking would fail here, not leave such a repair unseen.
    now = Clock.now()
    utc = datetime.timezone.utc
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # utcnow's, asked for here
        utcnow = datetime.datetime.utcnow()
    readings = {
        "datetime.now(utc)": datetime.datetime.now(utc).timestamp(),
        "datetime.now()": datetime.datetime.now().timestamp(),
        "datetime.utcnow()": utcnow.replace(tzinfo=utc).timestamp(),
        "datetime.today()": datetime.datetime.today().timestamp(),
        "time.gmtime()": calendar.timegm(time.gmtime()),
        "time.localtime()": time.mktime(time.localtime()),
    }
    if hasattr(time, "clock_gettime"):
        readings["clock_gettime(REALTIME)"] = time.clock_gettime(time.CLOCK_REALTIME)
    lag = {name: round(abs(value - now)) for name, value in readings.items()}
    lag["date.today()"] = 86400 * abs(
        (datetime.date.today() - datetime.date.fromtimestamp(Clock.now())).days
    )
    check(
        f"RUN every calendar clock reads the virtual time ({Clock.offset / 86400:.0f} days "
        "ahead of the real clock by now)",
        Clock.offset > 86400 and all(v <= 2 for v in lag.values()),
        f"seconds from the virtual clock: {lag}",
        "n/a (injection)",
    )
    print()
    if FAILED:
        print("FAILED CHECKS:")
        for name in FAILED:
            print(f"  - {name}")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    rung = four["run"]["final"]["at"]
    stations = five["run"]["final"]["stations"]
    lines = five["run"]["final"]["route_lines"]
    print(
        f"DISPOSITION: FIXED ({BRANCH})  BEFORE: the staged validator parsed 2 of its 5 "
        "required members, so a cold-cache publication whose trips.txt alone held one 0xFF "
        "byte was promoted (0 failed downloads) and the warmup then failed 3 of 3 attempts "
        "with UnicodeDecodeError on 1 download; stop_times.txt had the same gap and "
        "shapes.txt (F08) was promoted to 0 route lines; a repair published at once was not "
        "fetched for MAX_AGE_DAYS (75 further attempts over 6 h of the real backoff). AFTER: "
        f"the gates refuse all {len(mech['parsed']) - 1} members and the unjoinable archive "
        "through the loader's own readers (shapes.txt by its own gate, refused over a cache "
        "that serves and promoted degraded and named on a cold start), so "
        f"the trips.txt and stop_times.txt corruptions were refused at staging on "
        f"{len(one['raised'])} and {len(one_b['raised'])} of 3 attempts with 0 promotions "
        "and no cache written, archive status naming the member and the codec's words; the "
        f"repair was fetched and promoted by the next attempt, {elapsed(rung)} after the "
        "first refusal; and from a good archive loaded a month earlier the same bad "
        f"publication was refused while the good archive kept serving {stations} stations, "
        f"the station-route index and {lines} route lines, the repair promoted at the next "
        "start. A cache that passes validation and still fails the load is discarded."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
