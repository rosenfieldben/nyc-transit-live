#!/usr/bin/env python3
"""A02b (Medium), "Related medium-priority NJT variant" of A02: reproduction.

THE ID. The auditor gave this paragraph no ID of its own. It sits inside A02 ("Subway
archive validation can replace good data with a persistent failure", High) under the
heading "Related medium-priority NJT variant.", and "A02b" is THIS REPOSITORY'S label
for it, not the auditor's. The priority (Medium) is the auditor's word, from that
heading.

THE AUDIT'S CLAIM (the 2026-10-12 report, "Prioritized findings", A02, the paragraph
headed "Related medium-priority NJT variant.", verbatim):

    "An optional shapes.txt member with damaged DEFLATE data passes NJT publication
     validation, then raises an uncaught zlib.error during parsing. Three local
     attempts failed with zero repair downloads. Honor the promised optional-geometry
     degradation for decompression errors, or reject the publication coherently
     before promotion."

    Code evidence cited for it: backend/njt_static.py:608 (validate_njt_publication)
    and backend/njt_static.py:856 (the shapes.txt parse in _parse_shapes_member).
    The article's other three links (backend/static_data.py:142, :232, :438) belong to
    A02's subway case and are not exercised here.

WHAT IS PRODUCTION CODE HERE (unmodified, called as the app calls it):
  * warmups._warm_njt_static: the real retry loop, its `except Exception` arm,
    _fail_and_wait, _retry_delay (main.STATIC_RETRY_SCHEDULE_S) and the group status
    it publishes.
  * njt_static.load_njt_static: the cached-archive validity test, the freshness test,
    _download_zip, _parse_zip and the residual arm under the parse.
  * The staged publication path: njt_static._download_zip ->
    static_shared.staged_fetch -> njt_static._download_via_token (real) ->
    static_shared.validate_archive -> njt_static.validate_njt_publication -> promote,
    with static_shared's real per-archive record.
  * njt_static._parse_open and its shapes.txt parse, reached through the load.
  * The served surfaces, through the real ASGI app (httpx.ASGITransport, no
    lifespan): GET /api/njt-stops, GET /api/njt-routes and GET /api/status (its
    njt_static group state and its static_archives.njt record).

WHAT IS INJECTED (and nothing else):
  1. DATA_DIR points at a fresh temp directory before any backend import, so the NJT
     cache path is a temp path and data/ is never read or written.
  2. njt_auth.njt_post, the ONE call _download_via_token makes, is replaced by a fake
     RailData static endpoint that returns the publication staged for the phase,
     counts every request (a request here IS a download attempt), and hard-fails on
     any URL naming njtransit or raildata. njt_auth._httpx_post, the transport
     beneath every mint, is replaced by one that hard-fails if it is ever called, so
     no mint can be attempted at all (and none is: the count is checked). That is a
     private seam; if it ever moved, its count would read 0 vacuously, and the
     socket block (every connect and DNS lookup raises, and that count is checked
     too) is the guard that would still hold.
  3. njt_auth.is_configured is replaced by `lambda env=None: True`, as f09 does, so
     the warmup and the loader do not take their not-configured short circuit. NO
     credential is set, read or invented: the containment below blanks both, and with
     njt_post faked nothing would read them anyway.
  4. njt_static.load_njt_static, AS THE WARMUP RESOLVES IT, is the real function with
     its documented `now` argument fixed at 2026-10-12 12:00 New York (the audit's
     date, inside the committed fixture's service span 20260818..20270501). Without
     that, the service-date guard reads the wall clock and this script would turn red
     on 2027-05-02 for a reason unrelated to the finding. The wrapper also records,
     per attempt, what the real function returned or raised and how many downloads
     the fake saw during it. That is how "attempt" is counted.
  5. Time, for the duration of each warmup drive only. asyncio.sleep records the
     delay the warmup asked for and returns at once (the f05 technique), and THE SAME
     DELAY MOVES THE CLOCK THE CODE READS: time.time, time.monotonic, time.perf_counter
     and their _ns forms return real time plus an offset that only the drive moves
     (patched on the time module, which is how every backend module reads them, and
     also wherever a backend module holds one of those functions bound at module
     level, found by identity, not by name). So attempt 2 runs 14.72 s and attempt 3
     46.45 s after attempt 1, as in production, and a repair keyed on elapsed time
     sees that time pass wherever it lives. (A drive that returned from sleep without
     moving time measured a tight loop: a 10 s "re-download after a failed parse"
     grace escaped it.) Moving asyncio's clock is safe here because no timer is
     pending across the retry sleep: the warmup's attempt deadline is entered and
     left inside each attempt. random is seeded, so the +-10% jitter is reproducible.
     Not moved: datetime.now(), and a clock captured in a default argument or a
     closure; nothing on this path reads time that way at 1e3e73d.
     THE HORIZON. The audit's three attempts run at the warmup's own cadence. The
     drive then keeps the same warmup task running and samples the rest of the cached
     archive's life: each later attempt is taken at the next of these clock offsets
     from the first download (or the warmup's own delay, if that is later): one per
     day from 1 to 29 days, then 30 days less an hour, then 30 days plus an hour. The
     retries in between are not run, so 34 attempts stand for about 8,600, and the one
     repair this horizon cannot see is one that waits for more than 33 consecutive
     failures rather than for time. 30 days is the freshness window load_njt_static
     applies at 1e3e73d; it is written here as a number, the record, not read from the
     code by name, so renaming the constant cannot turn this script red and changing
     the window is a change of the record.
  6. The publications. All three are the committed fixture backend/tests/fixtures/
     njt_gtfs/ (seven members) zipped with ZIP_DEFLATED, byte for byte, except:
       DAMAGED (the finding): ONE byte of shapes.txt's DEFLATE stream, its first
         block header, rewritten so the block type bits read 11, which RFC 1951
         reserves as an error. Inflate itself rejects the stream ("invalid block
         type"). The stored CRC-32 and both sizes are untouched, so this is damage
         INSIDE the deflate data, not a CRC mismatch, and the script proves the read
         raises zlib.error and not zipfile.BadZipFile before it uses the archive.
       CRC-CAUGHT (the control): one byte of the same stream flipped at its
         midpoint (or the first position after it where the outcome holds), which
         inflate decodes to wrong bytes without complaint, so the member's CRC check
         raises zipfile.BadZipFile at the end of the read. Most single-byte flips
         land here (15,793 of the 15,830 positions in the second half of this
         stream when measured once while writing this script; only 36 raised
         zlib.error), which is why the finding's damage has to be chosen rather
         than flipped at random. The control shows the degradation the loader
         promises for an unreadable shapes.txt does fire for that exception type.
       GOOD (the baseline): no damage.
     Position is immaterial to the mechanism: whatever inflate rejects surfaces as
     zlib.error wherever it sits in the stream, and the first block header is the one
     position where every inflate implementation must reject it.

THE PROMISE THE FINDING HOLDS THE CODE TO is in the docstring of the shapes.txt parse
(the ledger row quotes it with its line; this script does not read it): an unreadable
shapes.txt "degrades to no lines, loudly, and the rest of the load carries on."

WHAT "UNCAUGHT" MEANS HERE, measured rather than assumed: neither the shapes.txt
parse's degradation arm nor load_njt_static's residual arm handles zlib.error, so it
leaves load_njt_static as an exception. The warmup's generic `except Exception` does
catch it, records "failed" and schedules the next rung, so the task stays alive and
retrying (unlike 09-05 F09, where the raise escaped the warmup). What it retries is the
same cached bytes: the cache passes the light validator and is fresh by mtime, so no
attempt downloads again until the cache is 30 days old. That persistence is measured,
not inferred: the drive samples the whole window (item 5) and checks that every attempt
in it downloads nothing and raises zlib.error, and that the attempt past it downloads.
The attempt past the window is also the witness that the moved clock reaches the
loader's own age test: if it did not, that check fails, rather than the in-window
checks passing for want of time having moved.

HERMETIC: _hermetic.contain() before the first backend import and _hermetic.verify()
after; every socket connect and DNS lookup raises (the count is printed and must be
zero); no credential, no mint, no network, no NJ Transit host. Deterministic: the
service-date clock is fixed (item 4), the jitter is seeded, the moved clock is real
time plus offsets the drive sets, and no wall-clock instant reaches a check or a
printed number. Runs in a few seconds.

RUN IT (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a02b_njt_shapes_zlib_error.py

Exits 0 while the finding behaves as recorded, 1 with the failed checks named.

RECORDED DISPOSITION: VERIFIED. The damaged publication passes the real publication
gate and is promoted as a success; each of the audit's three warmup attempts then
raises zlib.error out of load_njt_static with no repair download, and so does every
attempt sampled across the rest of the cached archive's 30-day life, because the cache
still passes the light validator and is fresh; the first download after the first
comes only when the cache ages out; and NJ Transit serves no stations at all, while the
CRC-caught control degrades to stations without lines.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import io
import logging
import os
import random
import shutil
import socket
import struct
import sys
import tempfile
import time
import unittest.mock
import zipfile
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
FIXTURE_DIR = BACKEND / "tests" / "fixtures" / "njt_gtfs"

# The cache root is read once, at import, through env_seams, so it is set first.
_TMP = Path(tempfile.mkdtemp(prefix="a02b-audit-2026-10-12-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)  # a crash leaves nothing behind either
os.environ["DATA_DIR"] = str(_TMP / "data")

# CONTAINMENT, BEFORE THE FIRST BACKEND IMPORT (see docs/reviews/audit-2026-09-05/
# _hermetic.py for the leak this closes). No credentials are kept: this script never
# needs to look configured to njt_auth, only to the two is_configured() callers, and
# item 3 of the docstring handles those without a credential.
sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()


# Nothing here may open a socket. Installed before the backend imports so an import
# that dialled out would be caught too.
class _NetworkAttempted(RuntimeError):
    pass


_DIALS: list[str] = []


def _blocked(*args, **kwargs):
    _DIALS.append(repr(args[:2]))
    raise _NetworkAttempted("this reproduction is hermetic; no outbound connection is allowed")


socket.socket.connect = _blocked  # type: ignore[method-assign]
socket.socket.connect_ex = _blocked  # type: ignore[method-assign]
socket.create_connection = _blocked  # type: ignore[assignment]
socket.getaddrinfo = _blocked  # type: ignore[assignment]

sys.path.insert(0, str(BACKEND))

# main before warmups: warmups imports main, and main imports names back out of
# warmups, so importing warmups first hits the half-built module (f09's note).
import main  # noqa: E402
import warmups  # noqa: E402

import httpx  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import static_shared  # noqa: E402

_hermetic.verify()

NY = ZoneInfo("America/New_York")
# The audit's date, noon in New York. Inside the committed fixture's service span,
# which the GOOD baseline proves by passing the real service-date guard at it.
FIXED_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=NY).timestamp()
ATTEMPTS_TO_DRIVE = 3  # the audit's "three local attempts"

# The rest of the cached archive's life, as the drive samples it after those three
# (docstring item 5). FRESH_DAYS is the record, measured at 1e3e73d: the age at
# which load_njt_static stops treating a valid cache as fresh.
DAY = 86400.0
FRESH_DAYS = 30
IN_WINDOW_AT = [d * DAY for d in range(1, FRESH_DAYS)] + [FRESH_DAYS * DAY - 3600]
AGED_OUT_AT = FRESH_DAYS * DAY + 3600
LATER_AT = IN_WINDOW_AT + [AGED_OUT_AT]

APP = main.app
CACHE = njt_static.NJT_STATIC_ZIP


class VirtualClock:
    """Real time plus an offset that only a warmup drive moves (docstring item 5).

    Installed on the time module for the duration of a drive, and on any backend
    module attribute that IS one of the real clock functions (a `from time import
    monotonic` binding), found by identity so no module or name is pinned here."""

    REAL = {
        name: getattr(time, name)
        for name in (
            "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns"
        )
    }

    def __init__(self) -> None:
        self.offset = 0.0

    def _fake(self, name: str):
        real = self.REAL[name]
        if name.endswith("_ns"):
            return lambda: real() + int(self.offset * 1_000_000_000)
        return lambda: real() + self.offset

    def install(self) -> list[tuple[object, str, object]]:
        fakes = {name: self._fake(name) for name in self.REAL}
        by_id = {id(real): fakes[name] for name, real in self.REAL.items()}
        undo: list[tuple[object, str, object]] = []
        for name, fake in fakes.items():
            undo.append((time, name, getattr(time, name)))
            setattr(time, name, fake)
        for module in list(sys.modules.values()):
            origin = Path(getattr(module, "__file__", None) or "/")
            if origin.parent != BACKEND:
                continue
            for attr, value in list(vars(module).items()):
                fake = by_id.get(id(value))  # REAL keeps the originals alive, so ids are unique
                if fake is not None:
                    undo.append((module, attr, value))
                    setattr(module, attr, fake)
        return undo

    @staticmethod
    def uninstall(undo: list[tuple[object, str, object]]) -> None:
        for owner, attr, value in reversed(undo):
            setattr(owner, attr, value)


CLOCK = VirtualClock()


def fmt_offset(seconds: float) -> str:
    """A clock offset from the drive's start, as printed (deterministic: the drive
    sets every offset from seeded delays and the fixed sample points)."""
    if seconds < DAY:
        return f"t+{seconds:.2f}s"
    days, rest = divmod(round(seconds), int(DAY))
    return f"t+{days}d {rest // 3600:02d}h{rest % 3600 // 60:02d}m"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

CHECKS: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, measured: str, audit: str = "not stated") -> bool:
    CHECKS.append((bool(ok), label, f"measured {measured}; audit: {audit}"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"         measured {measured}; audit: {audit}")
    return bool(ok)


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class LogCapture(logging.Handler):
    """What an operator would read in the process log, per phase. Printed, never
    asserted on: log wording is not behavior this record pins."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.name}: {record.getMessage()}")

    def drain(self) -> list[str]:
        out, self.lines = self.lines, []
        return out


# "main" is the warmups' logger (warmups.py logs under main's name). Captured here and
# not propagated, so the log lands in order on stdout instead of on the root handler
# main.py installs at import.
LOG = LogCapture()
for _name in ("njt_static", "static_shared", "main"):
    logging.getLogger(_name).addHandler(LOG)
    logging.getLogger(_name).setLevel(logging.INFO)
    logging.getLogger(_name).propagate = False


# ---------------------------------------------------------------------------
# The publications, built from the committed fixture
# ---------------------------------------------------------------------------

FIXTURE = {p.name: p.read_bytes() for p in sorted(FIXTURE_DIR.iterdir()) if p.is_file()}
SHAPES = "shapes.txt"


def zip_fixture() -> bytes:
    """The fixture as a publication. Every member carries one fixed timestamp, so the
    archive is byte-identical on every run (writestr with a bare name would stamp
    each member with the current local time)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in FIXTURE.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 12, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return buf.getvalue()


def stream_span(archive: bytes, member: str) -> tuple[int, int]:
    """(first byte, length) of `member`'s compressed stream inside `archive`, read
    from its local file header (30 fixed bytes, then the name, then the extra)."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        info = zf.getinfo(member)
    fixed = archive[info.header_offset + 26 : info.header_offset + 30]
    name_len, extra_len = struct.unpack("<HH", fixed)
    return info.header_offset + 30 + name_len + extra_len, info.compress_size


def read_outcome(archive: bytes, member: str) -> BaseException | None:
    """Read `member` end to end exactly as a parser would; the exception, or None."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        try:
            with zf.open(member) as raw:
                while raw.read(1 << 16):
                    pass
        except Exception as exc:  # noqa: BLE001 - the exception is the measurement
            return exc
    return None


def describe(exc: BaseException | None) -> str:
    if exc is None:
        return "read cleanly"
    return f"{type(exc).__module__}.{type(exc).__qualname__}: {exc}"


GOOD = zip_fixture()
SHAPES_START, SHAPES_LEN = stream_span(GOOD, SHAPES)

# DAMAGED: the first deflate block header's BTYPE bits (bits 1 and 2) set to 11.
_damaged = bytearray(GOOD)
_damaged[SHAPES_START] = (_damaged[SHAPES_START] & 0b11111001) | 0b00000110
DAMAGED = bytes(_damaged)
DAMAGED_OFFSET = 0

# CRC-CAUGHT: a byte flipped from the stream's midpoint on, at the first position
# where inflate completes and the CRC check is what fails. Deterministic: the
# fixture, the compressor and the search order are all fixed.
CRC_CAUGHT = b""
CRC_OFFSET = -1
for _offset in range(SHAPES_LEN // 2, SHAPES_LEN):
    _candidate = bytearray(GOOD)
    _candidate[SHAPES_START + _offset] ^= 0xFF
    if isinstance(read_outcome(bytes(_candidate), SHAPES), zipfile.BadZipFile):
        CRC_CAUGHT, CRC_OFFSET = bytes(_candidate), _offset
        break


# ---------------------------------------------------------------------------
# The injections
# ---------------------------------------------------------------------------


def guard(url: str) -> None:
    lowered = str(url).lower()
    if "njtransit" in lowered or "raildata" in lowered:
        raise SystemExit(f"A02b CONTAINMENT BREACH: a request was addressed to {url!r}")


class FakeRailDataStatic:
    """The NJ Transit static endpoint, in process. One request is one download."""

    def __init__(self) -> None:
        self.publication: bytes | None = None
        self.requests = 0
        self.urls: list[str] = []

    async def njt_post(self, url: str, form: dict | None = None, **_: object) -> bytes:
        guard(url)
        self.urls.append(url)
        if self.publication is None:
            raise AssertionError("no publication is staged for this phase")
        self.requests += 1
        return self.publication


UPSTREAM = FakeRailDataStatic()
MINT_TRANSPORT_CALLS: list[str] = []


async def _forbidden_transport(url: str, form: dict, timeout_s: float, **_: object):
    MINT_TRANSPORT_CALLS.append(url)
    raise SystemExit(
        f"A02b CONTAINMENT BREACH: the real NJ Transit transport was called for {url!r}"
    )


njt_auth.njt_post = UPSTREAM.njt_post
njt_auth._httpx_post = _forbidden_transport
njt_auth.is_configured = lambda env=None: True  # no credential is set or read

_REAL_LOAD = njt_static.load_njt_static
ATTEMPTS: list[dict] = []


async def _load_at_fixed_now(*args, **kwargs):
    """The REAL load_njt_static with its `now` seam fixed, recording each attempt
    and the drive's clock offset when it started."""
    kwargs.setdefault("now", FIXED_NOW)
    record: dict = {
        "at": CLOCK.offset, "downloads": 0, "raised": None, "stops": None, "shapes": None
    }
    ATTEMPTS.append(record)
    before = UPSTREAM.requests
    try:
        data = await _REAL_LOAD(*args, **kwargs)
    except BaseException as exc:
        record["raised"] = exc
        raise
    finally:
        record["downloads"] = UPSTREAM.requests - before
    record["stops"] = len(data.get("stops") or {})
    record["shapes"] = len(data.get("shapes") or {})
    return data


njt_static.load_njt_static = _load_at_fixed_now


# ---------------------------------------------------------------------------
# Driving the real warmup, and reading the served surfaces
# ---------------------------------------------------------------------------


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=APP)
    async with httpx.AsyncClient(transport=transport, base_url="http://a02b") as client:
        return await client.get(path)


def reset_world() -> None:
    """A cold start: no cache, no archive record, and the NJT fields as main.py's
    lifespan sets them before the warmup runs (the lifespan itself is not run)."""
    CACHE.unlink(missing_ok=True)
    if CACHE.parent.exists():
        for orphan in CACHE.parent.glob("*.part"):
            orphan.unlink()
    static_shared.reset_archive_status()
    APP.state.njt_static = {}
    APP.state.njt_stops = {}
    APP.state.njt_routes = []
    APP.state.njt_station_routes = {}
    APP.state.njt_trips = {}
    APP.state.njt_stop_schedule = {}
    APP.state.njt_static_status = "loading"
    ATTEMPTS.clear()
    LOG.drain()


async def drive(publication: bytes) -> dict:
    """Cold-start the real NJT warmup against `publication` and let the same task run
    until it reaches ready or has failed every attempt the horizon names: the audit's
    three at the warmup's own cadence, then one at each LATER_AT clock offset."""
    reset_world()
    UPSTREAM.publication = publication
    downloads_before = UPSTREAM.requests
    random.seed(20261012)
    CLOCK.offset = 0.0
    delays: list[float] = []
    horizon = ATTEMPTS_TO_DRIVE + len(LATER_AT)
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *args, **kwargs):
        # The delay the warmup asked for is the time that passes before its next
        # attempt; past the audit's three, the next attempt is also held back to its
        # sample point if that is later.
        delays.append(float(delay))
        CLOCK.offset += float(delay)
        sample = len(delays) - ATTEMPTS_TO_DRIVE
        if 0 <= sample < len(LATER_AT):
            CLOCK.offset = max(CLOCK.offset, LATER_AT[sample])
        await real_sleep(0)

    undo = CLOCK.install()
    try:
        with unittest.mock.patch.object(asyncio, "sleep", recording_sleep):
            task = asyncio.create_task(warmups._warm_njt_static(APP))
            spins = 0
            while not task.done() and len(delays) < horizon and spins < 1_000_000:
                spins += 1
                await real_sleep(0)
            still_running = not task.done()
            if still_running:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    finally:
        VirtualClock.uninstall(undo)
    stops = await _get("/api/njt-stops")
    routes = await _get("/api/njt-routes")
    status = (await _get("/api/status")).json()
    cached = CACHE.read_bytes() if CACHE.exists() else None
    return {
        "attempts": [dict(a) for a in ATTEMPTS],
        "downloads": UPSTREAM.requests - downloads_before,
        "delays": delays,
        "clock_end": CLOCK.offset,
        "still_retrying": still_running,
        "stops_status": stops.status_code,
        "stations": len(stops.json()) if stops.status_code == 200 else None,
        "routes_status": routes.status_code,
        "route_lines": len(routes.json()) if routes.status_code == 200 else None,
        "njt_static": status.get("njt_static"),
        "archive": (status.get("static_archives") or {}).get("njt") or {},
        "cache": "absent" if cached is None else digest(cached),
        "log": LOG.drain(),
    }


def outcome(a: dict) -> str:
    exc = a["raised"]
    if exc is not None:
        return f"raised {type(exc).__module__}.{type(exc).__qualname__}: {exc}"
    return f"returned {a['stops']} stops, {a['shapes']} shapes"


def show(result: dict) -> None:
    # The audit's attempts one per line; later ones grouped where they look alike.
    rows = [
        (n, a, f"downloads {a['downloads']}, {outcome(a)}")
        for n, a in enumerate(result["attempts"], 1)
    ]
    i = 0
    while i < len(rows):
        j = i
        if rows[i][0] > ATTEMPTS_TO_DRIVE:
            while j + 1 < len(rows) and rows[j + 1][2] == rows[i][2]:
                j += 1
        first, last = rows[i][1], rows[j][1]
        if i == j:
            print(f"    attempt {rows[i][0]} ({fmt_offset(first['at'])}): {rows[i][2]}")
        else:
            print(f"    attempts {rows[i][0]}-{rows[j][0]} ({fmt_offset(first['at'])} .. "
                  f"{fmt_offset(last['at'])}, {j - i + 1} attempts): each {rows[i][2]}")
        i = j + 1
    delays = [round(d, 2) for d in result["delays"]]
    shown = delays if len(delays) <= 6 else delays[:6] + ["..."]
    print(f"    retry delays the warmup asked for : {shown} ({len(delays)} in all)")
    print(f"    warmup still retrying when stopped: {result['still_retrying']}")
    print(f"    GET /api/status njt_static        : {result['njt_static']!r}")
    print(f"    GET /api/status static_archives.njt: "
          f"last_promoted_at={'set' if result['archive'].get('last_promoted_at') else None}"
          f"  last_download_error={result['archive'].get('last_download_error')!r}"
          f"  failed_downloads={result['archive'].get('failed_downloads')}")
    print(f"    GET /api/njt-stops                : HTTP {result['stops_status']}, "
          f"{result['stations']} stations")
    print(f"    GET /api/njt-routes               : HTTP {result['routes_status']}, "
          f"{result['route_lines']} route lines")
    print(f"    cache on disk                     : {result['cache']}")
    for line in result["log"]:
        print(f"    log: {line}")


def validator_verdict(archive: bytes, validator) -> tuple[str, str, list[str]]:
    """The REAL validator at the boundary staged_fetch uses (validate_archive), at the
    fixed instant, plus the members it opened (printed, not asserted)."""
    path = _TMP / "verdict.zip"
    path.write_bytes(archive)
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, *args, **kwargs):
        opened.append(name if isinstance(name, str) else name.filename)
        return real_open(self, name, *args, **kwargs)

    with unittest.mock.patch.object(zipfile.ZipFile, "open", spy):
        try:
            static_shared.validate_archive(path, lambda zf: validator(zf, now=FIXED_NOW))
        except static_shared.StaticValidationError as exc:
            return "REJECTED", str(exc), opened
    return "ACCEPTED", "", opened


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def amain() -> int:
    rule("A02b  NJT shapes.txt with damaged DEFLATE data: zlib.error, no repair")
    print(f"fixture         : backend/tests/fixtures/njt_gtfs/ ({', '.join(FIXTURE)})")
    print(f"DATA_DIR seam   : {os.environ['DATA_DIR']}")
    print(f"static URL      : {njt_static.NJT_STATIC_URL}  (answered by the in-process fake)")
    print("service clock   : load_njt_static(now=2026-10-12 12:00 America/New_York)")

    # ---- 1. the damage, proved before it is used ---------------------------
    rule("1  THE DAMAGE: inside the deflate stream, and what reading it raises")
    good_info = zipfile.ZipFile(io.BytesIO(GOOD)).getinfo(SHAPES)
    bad_info = zipfile.ZipFile(io.BytesIO(DAMAGED)).getinfo(SHAPES)
    damaged_exc = read_outcome(DAMAGED, SHAPES)
    control_exc = read_outcome(CRC_CAUGHT, SHAPES) if CRC_CAUGHT else None
    print(f"  shapes.txt stream          : {SHAPES_LEN} compressed bytes at archive offset "
          f"{SHAPES_START} ({good_info.file_size} bytes inflated)")
    print(f"  DAMAGED, stream byte {DAMAGED_OFFSET:<6}: "
          f"0x{GOOD[SHAPES_START]:02x} -> 0x{DAMAGED[SHAPES_START]:02x} (block type bits now 11)")
    print(f"     reading shapes.txt      : {describe(damaged_exc)}")
    print(f"  CRC-CAUGHT, stream byte {CRC_OFFSET:<4}: flipped (0xFF xor)")
    print(f"     reading shapes.txt      : {describe(control_exc)}")
    print(f"  zlib.error's ancestry      : {[c.__name__ for c in zlib.error.__mro__]}")
    others_clean = all(
        read_outcome(archive, name) is None
        for archive in (DAMAGED, CRC_CAUGHT)
        for name in FIXTURE
        if name != SHAPES
    )
    differing = [i for i, (a, b) in enumerate(zip(GOOD, DAMAGED, strict=True)) if a != b]
    prerequisites = [
        check(
            isinstance(damaged_exc, zlib.error) and not isinstance(damaged_exc, zipfile.BadZipFile),
            "the DAMAGED shapes.txt raises zlib.error on read, not BadZipFile",
            describe(damaged_exc),
            "zlib.error",
        ),
        check(
            differing == [SHAPES_START]
            and (bad_info.CRC, bad_info.compress_size, bad_info.file_size)
            == (good_info.CRC, good_info.compress_size, good_info.file_size),
            "the damage is one byte inside the deflate stream; stored CRC-32 and sizes intact",
            f"{len(differing)} byte(s) differ, at archive offset(s) {differing}; CRC "
            f"{bad_info.CRC:08x} vs {good_info.CRC:08x}",
            "damaged DEFLATE data",
        ),
        check(
            isinstance(control_exc, zipfile.BadZipFile),
            "the CRC-CAUGHT control raises zipfile.BadZipFile on read",
            describe(control_exc),
        ),
        check(
            others_clean,
            "every other member of both damaged archives reads cleanly",
            "all six read cleanly" if others_clean else "a second member is unreadable",
        ),
    ]
    if not all(prerequisites):
        print()
        print("the damaged archives are not what this record needs; "
              "nothing below would mean anything")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        shutil.rmtree(_TMP, ignore_errors=True)
        return 1

    # ---- 2. the publication gate -------------------------------------------
    rule("2  THE PUBLICATION GATE: validate_njt_publication on each archive")
    verdicts = {}
    for label, archive in (("GOOD", GOOD), ("DAMAGED", DAMAGED), ("CRC-CAUGHT", CRC_CAUGHT)):
        verdict, message, opened = validator_verdict(archive, njt_static.validate_njt_publication)
        verdicts[label] = verdict
        print(f"  {label:<11}: {verdict} {message}")
        print(f"               members it asked to open: {sorted(set(opened))}")
        print(f"               shapes.txt opened: {'yes' if SHAPES in opened else 'no'}")
    # Informational only (no check reads it): the cached-archive validator's verdict.
    # Looked up softly so a rename of that helper cannot crash the record.
    light_validator = getattr(njt_static, "validate_njt_archive", None)
    light = (
        validator_verdict(DAMAGED, light_validator)[0] if light_validator else "n/a under that name"
    )
    print(f"  DAMAGED through the light cached-archive validator: {light}")
    check(verdicts["GOOD"] == "ACCEPTED", "the GOOD publication passes at the fixed instant",
          verdicts["GOOD"])
    check(verdicts["DAMAGED"] == "ACCEPTED",
          "the DAMAGED publication passes NJT publication validation",
          verdicts["DAMAGED"], "passes NJT publication validation")

    # ---- 3. baseline -------------------------------------------------------
    rule("3  BASELINE: the GOOD publication through the real warmup, cold start")
    good = await drive(GOOD)
    show(good)
    good_stations = good["stations"] or 0
    good_lines = good["route_lines"] or 0
    check(
        len(good["attempts"]) == 1 and good["attempts"][0]["raised"] is None
        and good["njt_static"] == "ready" and good["downloads"] == 1,
        "the GOOD publication is downloaded once and reaches ready on attempt 1",
        f"{len(good['attempts'])} attempt(s), {good['downloads']} download(s), "
        f"status {good['njt_static']!r}",
    )
    check(good_stations > 0 and good_lines > 0,
          "the GOOD publication serves stations and route lines",
          f"{good_stations} stations, {good_lines} route lines")

    # ---- 4. the finding ----------------------------------------------------
    rule("4  THE FINDING: the DAMAGED publication through the real warmup, cold start")
    bad = await drive(DAMAGED)
    show(bad)
    every = bad["attempts"]
    attempts = every[:ATTEMPTS_TO_DRIVE]  # the audit's three, at the warmup's own cadence
    window = every[ATTEMPTS_TO_DRIVE : ATTEMPTS_TO_DRIVE + len(IN_WINDOW_AT)]
    aged = every[ATTEMPTS_TO_DRIVE + len(IN_WINDOW_AT) :]
    zlib_raises = [a for a in attempts if isinstance(a["raised"], zlib.error)]
    repair = sum(a["downloads"] for a in attempts[1:])
    check(
        len(attempts) == ATTEMPTS_TO_DRIVE and len(zlib_raises) == ATTEMPTS_TO_DRIVE,
        "each of the audit's three warmup attempts fails with zlib.error raised out of "
        "load_njt_static",
        f"{len(zlib_raises)} of {len(attempts)} attempts raised zlib.error, at "
        f"{', '.join(fmt_offset(a['at']) for a in attempts)}",
        "three local attempts failed, zlib.error",
    )
    check(
        bool(attempts) and attempts[0]["downloads"] == 1 and repair == 0,
        "the one download is the publication itself; neither later attempt downloads a repair",
        f"{attempts[0]['downloads'] if attempts else 0} download(s) on attempt 1, "
        f"{repair} across the {max(len(attempts) - 1, 0)} later attempt(s)",
        "zero repair downloads",
    )
    window_quiet = [
        a for a in window if a["downloads"] == 0 and isinstance(a["raised"], zlib.error)
    ]
    check(
        len(window) == len(IN_WINDOW_AT) and len(window_quiet) == len(IN_WINDOW_AT),
        f"every attempt sampled across the rest of the cache's {FRESH_DAYS}-day life downloads "
        "nothing and raises zlib.error again",
        f"{len(window_quiet)} of {len(IN_WINDOW_AT)} sampled attempts "
        f"({fmt_offset(IN_WINDOW_AT[0])} .. {fmt_offset(IN_WINDOW_AT[-1])}) did; "
        f"{sum(a['downloads'] for a in window)} download(s) among the {len(window)} run",
        "not stated (the audit counted three attempts)",
    )
    check(
        len(aged) == 1 and aged[0]["downloads"] == 1,
        f"once the cache is past {FRESH_DAYS} days old, the next attempt downloads again "
        "(and so the drive's clock does reach the loader's age test)",
        (
            f"{aged[0]['downloads']} download(s) at {fmt_offset(aged[0]['at'])}, "
            f"then {outcome(aged[0])}"
        )
        if aged
        else "no attempt ran past the window",
    )
    check(
        bad["archive"].get("last_promoted_at") is not None
        and bad["archive"].get("last_download_error") is None
        and bad["archive"].get("failed_downloads") == 0,
        "/api/status records the damaged publication as a clean promotion",
        f"promoted={'yes' if bad['archive'].get('last_promoted_at') else 'no'}, "
        f"last_download_error={bad['archive'].get('last_download_error')!r}, "
        f"failed_downloads={bad['archive'].get('failed_downloads')}",
    )
    check(
        bad["cache"] == digest(DAMAGED),
        "the damaged archive stays in the cache after every failed attempt",
        f"cache {bad['cache']}, publication {digest(DAMAGED)}",
    )
    check(
        bad["njt_static"] == "failed" and bad["stations"] == 0 and bad["route_lines"] == 0
        and bad["still_retrying"],
        "NJ Transit as a whole is down and retrying, not merely without route lines",
        f"njt_static {bad['njt_static']!r}, {bad['stations']} of {good_stations} stations, "
        f"{bad['route_lines']} route lines, still retrying {bad['still_retrying']}",
    )

    # ---- 5. the control ----------------------------------------------------
    rule("5  CONTROL: the same member damaged so the CRC catches it (BadZipFile)")
    ctl = await drive(CRC_CAUGHT)
    show(ctl)
    check(
        len(ctl["attempts"]) == 1 and ctl["attempts"][0]["raised"] is None
        and ctl["njt_static"] == "ready" and ctl["stations"] == good_stations
        and ctl["route_lines"] == 0,
        "a BadZipFile shapes.txt degrades as promised: every station, no route lines",
        f"{len(ctl['attempts'])} attempt(s), status {ctl['njt_static']!r}, "
        f"{ctl['stations']} of {good_stations} stations, {ctl['route_lines']} route lines",
    )

    # ---- verdict -----------------------------------------------------------
    rule("CHECKS")
    failed = [(label, detail) for ok, label, detail in CHECKS if not ok]
    print(f"  {len(CHECKS) - len(failed)}/{len(CHECKS)} checks hold")
    print(f"  NJ Transit mint transport calls : {len(MINT_TRANSPORT_CALLS)} (must be 0)")
    print(f"  outbound connection attempts    : {len(_DIALS)} (must be 0)")
    print(f"  static requests the fake served : {UPSTREAM.requests}, "
          f"all to {sorted(set(UPSTREAM.urls))}")
    if MINT_TRANSPORT_CALLS or _DIALS:
        failed.append(("the run stayed hermetic", f"mints {MINT_TRANSPORT_CALLS}, dials {_DIALS}"))
    shutil.rmtree(_TMP, ignore_errors=True)
    print()
    if failed:
        for label, detail in failed:
            print(f"  FAILED: {label} ({detail})")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    print(
        "DISPOSITION: VERIFIED  A shapes.txt whose DEFLATE stream inflate rejects passed "
        "validate_njt_publication and was promoted as a clean success (failed_downloads 0), "
        f"then all {len(attempts)} warmup attempts raised zlib.error out of load_njt_static "
        f"with {repair} repair downloads, as did all {len(window_quiet)} sampled across the "
        f"cache's {FRESH_DAYS}-day life (the next download came only once it aged out), leaving "
        f"NJ Transit 'failed' with 0 of {good_stations} stations served. The same member damaged "
        f"so its CRC catches it (BadZipFile) degrades as promised, to {ctl['stations']} stations "
        "and 0 route lines."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
