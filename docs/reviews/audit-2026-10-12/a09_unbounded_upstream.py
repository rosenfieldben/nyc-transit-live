"""A09 (Medium), "Upstream resource use is not sufficiently bounded": reproduction.

THE AUDIT'S CLAIM (report of 2026-10-12, finding A09, verbatim):

    Risk. "Static transfers have no byte ceiling, archive validation lacks
    member-count/uncompressed-size limits, and some parsing runs synchronously on
    the serving event loop. NJT's transport buffers the whole response. Timeouts do
    not preempt synchronous CPU work, so an oversized or pathological agency
    publication can impair all layers sharing the process."

    Evidence and boundary. "A harmless local validator taking 80 ms completed and
    promoted a stage inside an asyncio.timeout(10 ms) block after approximately
    81 ms, without a timeout. This demonstrates a resource-control gap; it does not
    demonstrate a public-user exploit or malicious provider."

    Repair. "Set source-specific compressed and expanded byte limits, archive
    member/row ceilings and finite-coordinate validation before publication. Move
    expensive work off the serving loop; use a killable subprocess where a hard CPU
    deadline is required. Test oversized downloads and members, malformed numeric
    values, last-good preservation and health responsiveness during validation."

    Code evidence (where to LOOK at 1e3e73d; nothing here asserts on any of them):
        backend/static_shared.py:159
        backend/static_shared.py:272
        backend/static_shared.py:338
        backend/njt_auth.py:568
        backend/bus_static.py:193

THE BOUNDARY, the way the audit states it. This is a resource-control gap, not a
public-user exploit and not a malicious provider: every input below is a harmless
local publication. The adjacent row in the 2026-09-05 ledger is F09 (nonfinite
coordinates); the Repair's "finite-coordinate validation" is F09 territory and is
not re-measured here.

NOT DRIVEN BY THIS SCRIPT, left to the ledger as confirmed from source only:
  - NJT's whole-response buffering (njt_auth.py:568). No NJ Transit module is driven.
  - The bus borough transfer (bus_static.py:193): a synchronous httpx.Client run in a
    worker thread, bounded by a whole-transfer time deadline and by no byte count.
  - The transport below httpx.AsyncClient. The fake wire replaces the client's
    transport, so httpcore, h11 and the socket are never exercised, and "no ceiling
    in the transport" is read from their source, not measured: h11's
    max_incomplete_event_size (16 KiB by default; httpcore passes 100 KiB) bounds an
    INCOMPLETE event, in practice the status line and headers, while body bytes are
    handed out as Data events as they arrive; neither httpx nor httpcore has a body
    limit. Python 3.12's zipfile has no member-count or ratio limit on read (its
    ZIP_FILECOUNT_LIMIT and ZIP64_LIMIT only decide when a WRITER switches to zip64).

FOUR CHECK GROUPS, each over the real static_shared staged path:

  ARM 1  The deadline that did not preempt: CLOSED on claude/a02-publication-validation,
         and now a regression check on that repair. staged_fetch validates in a worker
         thread behind an await (static_shared.py), so an expired deadline is
         delivered at that await, before the promotion, and the loop keeps running
         while the thread validates.
         1a/1b: the audit's own experiment. A validator that runs the real subway
         validator and then busy-waits to 80 ms, inside asyncio.timeout(0.01), through
         the real staged_fetch and its real default transfer. Measured: elapsed time,
         promotion, whether TimeoutError left the block, how long the event loop was
         held, and what the expired deadline does at the boundary afterwards.
         BEFORE (this record at d5f6d70): no TimeoutError, the stage promoted after
         81.5 ms (81.2 to 84.6 across runs), the loop held for all of it; with a
         suspending await after the stage, the TimeoutError arrived THERE, after the
         promotion it was meant to stop.
         AFTER, asserted: the TimeoutError leaves the block during the validation, in
         well under the validator's 80 ms, nothing is promoted, and the heartbeat
         keeps ticking; with a suspending await after it, the TimeoutError is still
         raised inside staged_fetch, before any promotion.
         1c: the PRODUCTION attempt the serving loop runs, with nothing injected into
         the pipeline. The real subway warmup, its whole-attempt deadline set to the
         same 10 ms through the documented test seam, loads a valid publication whose
         transfers.txt has 150,000 rows, so the real validator's own parse is the
         CPU work. Measured: whether the publication was promoted before the attempt
         ended, and how long a 1 ms heartbeat sharing the loop was stalled.
         BEFORE: the loop stalled for 220 to 280 ms on the validation and the
         publication was promoted before the deadline was delivered. AFTER, asserted:
         each of three attempts fails at its deadline with nothing promoted, and the
         shortest of their longest heartbeat gaps is under a quarter of the same
         validation timed standalone.
         This is the check that sees a repair made ABOVE staged_fetch (in the subway
         loader or in the warmup), which 1a cannot, because 1a calls staged_fetch on
         the script's own loop.
  ARM 2  No byte ceiling on a transfer. A valid subway publication padded to 512 MiB
         with one stored member, generated as it is served (no upstream file), through
         the real default transfer, the real subway loader and the real validator.
         Measured: every byte accepted by the transfer (2.1: in-transfer ceilings,
         declared-length checks), and the whole publication validated and promoted
         (2.2: also any size gate between the transfer and the promotion). 512 MiB is
         about 13x the ~40 MB live publication the repository describes, so a ceiling
         with ordinary headroom sits below it. The row states 512 MiB as the bound.
  ARM 3  No member-count or expanded-size limit at validation. A valid subway archive
         plus 70,000 empty members (past the 65,535 entries of a classic zip, so the
         archive is zip64), and a valid subway archive whose stop_times.txt declares
         2 GiB expanded from about 2 MB on the wire: its usual header and one call,
         then 2 GiB of lines of spaces, rows the subway's reader of the member skips
         (no trip_id, no stop_id), so it reads the same one-call table the small
         publications carry. The expansion sits on a member the subway code reads,
         never on an extra one nothing reads, so that a ceiling over the members the
         code reads turns 3.3 red: the validator requires stop_times.txt (3.0
         measures that by rejection) and, since A02, reads all of it through the
         loader's own generator, and the routes-per-station loader inflates it whole.
         Measured: both archives promoted, the member count, and stop_times.txt's
         declared size and ratio.
         WHY LINES OF SPACES AND NOT BLANK LINES, which is what this padding was
         until A02's repair made validation read the member. A CSV reader takes about
         20 times longer over empty lines than over the same bytes as long ones: 2 GiB
         of blank lines measured about 320 s to read, 2 GiB of 65,535-space lines
         about 16 s, and the padding exists to declare an expanded size, not to cost
         the run five minutes. Reading it whole at validation is now the unbounded
         work this finding describes, so a ceiling on what validation parses (in
         parse_member, say) reaches stop_times.txt and turns 3.3 red too.
  H      Hermetic and harness checks: the generated-archive builder reads back intact
         at small scale, and no socket was attempted.

WHAT IS PRODUCTION CODE HERE, unmodified and called directly:
    static_shared.staged_fetch          sweep, stage, validate, promote (1a, 1b)
    static_shared's default transfer    the streaming GET, its asyncio.timeout and its
                                        write loop (every arm; it is NOT replaced)
    static_shared.validate_archive      the zip-open boundary (every arm)
    static_data.validate_subway_archive the real subway publication validator
    static_data.load_subway_stops       the loader the subway warmup awaits (arms 2, 3)
    warmups._warm_subway_static         the subway warmup main.lifespan schedules (1c),
                                        with the inline loaders it calls after the stops
    static_shared.archive_status        the operator surface /api/status reads

WHAT IS INJECTED, and this is the whole of it:
    1. DATA_DIR points at a fresh temp directory and SUBWAY_GTFS_URL at the loopback
       discard port, both before the backend imports (env_seams reads them once).
    2. THE WIRE, one layer UNDER the production transfer: httpx.AsyncClient is
       replaced, before any backend import, by a subclass that forces an
       httpx.MockTransport and trust_env=False. The transfer function itself runs
       for real, because arm 2 asks whether IT has a byte ceiling and replacing it
       would replace the code under test. The mock serves only what this script
       publishes, sends a Content-Length like a real origin, yields to the loop
       between chunks like a real socket read, and hard-fails on any URL naming
       njtransit or raildata.
    3. 1a/1b's validator: the real subway validator followed by a busy-wait to 80 ms,
       standing in for a larger publication's parse time. CPU, not a sleep, because a
       real parse holds the interpreter lock the way a busy-wait does.
    4. 1c: main.STATIC_ATTEMPT_DEADLINE_S set for the one attempt and restored (the
       name main documents as the test seam) to 10 ms, or to a tenth of the
       publication's own standalone validation time where that is shorter, so the
       deadline expires during validation on a faster machine too; and a bare FastAPI app
       whose state starts "loading", as main.lifespan sets it.
    5. Archives built here: small ones with zipfile, the 512 MiB and 2 GiB ones by a
       short zip writer below that generates them as they are served (stored zeros,
       and one MiB of lines of spaces deflated once and repeated after a table's
       header). H.0 proves that writer's output reads back intact with
       zipfile.testzip, and that the subway's reader of a padded stop_times.txt
       yields exactly the rows of the unpadded one.

HERMETIC. _hermetic.contain() before the first backend import and verify() after
it; no credentials; an audit hook refuses any socket connect or name resolution,
and the run asserts none was attempted. Nothing reaches the network, no NJ Transit
module is driven, no mint is spent, data/ is never touched. Arm 2 writes one
512 MiB stage file under the temp directory and the run deletes it. Timing is the
only wall-clock dependence, and only arm 1 measures it, with bounds that hold by
construction: 1a's validator itself spends the 80 ms (1a.2 is an ordering, the block
ending while the validator still runs; 1a.4 bounds the heartbeat's gap at half of it,
against 4 to 16 ms measured off the loop), and 1c compares the stall, the shortest of
three attempts' (inline, every attempt's is the whole validation), with the same
validation timed standalone in the same run.

RUN IT, from the repository root:
    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a09_unbounded_upstream.py

RECORDED DISPOSITION: VERIFIED, for arms 2 and 3 (no byte ceiling, no member or
expanded-size ceiling). Arm 1 is closed on claude/a02-publication-validation and pins
the repair. Exits 0 while A09 behaves as recorded; exits 1, naming the failed checks,
the moment the code stops matching.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import logging
import os
import shutil
import struct
import sys
import tempfile
import time
import zipfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"

# ---------------------------------------------------------------------------
# Seams that env_seams reads once, at import: set before the backend imports.
# ---------------------------------------------------------------------------

_TMP = Path(tempfile.mkdtemp(prefix="a09-audit-2026-10-12-"))
SUBWAY_URL = "http://127.0.0.1:9/a09/gtfs_subway.zip"  # discard port; nothing listens
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["SUBWAY_GTFS_URL"] = SUBWAY_URL

# ---------------------------------------------------------------------------
# No sockets, enforced rather than hoped for.
# ---------------------------------------------------------------------------

SOCKET_ATTEMPTS: list[str] = []


def _refuse_sockets(event: str, args: tuple) -> None:
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        SOCKET_ATTEMPTS.append(f"{event} {args!r}"[:160])
        raise RuntimeError(f"a09: {event} refused; this script opens no sockets")


sys.addaudithook(_refuse_sockets)

# ---------------------------------------------------------------------------
# Archives the fake origin can serve. Small ones are bytes built with zipfile; the
# large ones are generated as they are served, so they never exist as a file here.
# ---------------------------------------------------------------------------

MiB = 1 << 20
_ZERO_MIB = bytes(MiB)
# One MiB of lines of spaces: sixteen lines of 65,535 spaces and a newline. A row with
# no trip_id or stop_id, which the subway's stop_times reader skips; see ARM 3 in the
# docstring for why not blank lines (a CSV reader is about 20 times slower over them).
_SPACE_LINES_MIB = (b" " * 65535 + b"\n") * 16
assert len(_SPACE_LINES_MIB) == MiB


def repeated_crc(block: bytes, count: int, crc: int = 0) -> int:
    """The CRC-32 of `count` copies of `block`, continuing from `crc`."""
    for _ in range(count):
        crc = zlib.crc32(block, crc)
    return crc


def deflated_block(data: bytes) -> bytes:
    """`data` as raw deflate from an empty window, ending on a full flush, so the
    output is self-contained: it can follow another such block or itself."""
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    return comp.compress(data) + comp.flush(zlib.Z_FULL_FLUSH)


_DEFLATED_SPACE_LINES_MIB = deflated_block(_SPACE_LINES_MIB)  # about 1,000:1
_DEFLATE_END = zlib.compressobj(9, zlib.DEFLATED, -15).flush(zlib.Z_FINISH)  # empty final block

_DOS_DATE = ((2026 - 1980) << 9) | (10 << 5) | 12  # 2026-10-12, 00:00:00


class _Member:
    """One zip member: its header fields and a callable yielding its data chunks."""

    def __init__(self, name: str, method: int, crc: int, csize: int, usize: int, parts):
        self.name = name.encode("ascii")
        self.method, self.crc, self.csize, self.usize, self.parts = method, crc, csize, usize, parts


def member_text(name: str, text: str) -> _Member:
    data = text.encode("utf-8")
    comp = zlib.compressobj(6, zlib.DEFLATED, -15)
    body = comp.compress(data) + comp.flush()
    return _Member(name, zipfile.ZIP_DEFLATED, zlib.crc32(data), len(body), len(data),
                   lambda: iter((body,)))


def member_stored_zeros(name: str, mib: int) -> _Member:
    """`mib` MiB of zeros, stored: wire size == expanded size."""
    return _Member(name, zipfile.ZIP_STORED, repeated_crc(_ZERO_MIB, mib), mib * MiB, mib * MiB,
                   lambda: (_ZERO_MIB for _ in range(mib)))


def member_padded_table(name: str, text: str, mib: int) -> _Member:
    """A CSV table `text` followed by `mib` MiB of lines of spaces, deflated at about
    1,000:1. Those lines carry no trip_id and no stop_id, so the subway's reader of
    stop_times.txt yields exactly the rows of `text` alone; only the member's declared
    expanded size differs."""
    head = text.encode("utf-8")
    body = deflated_block(head) + _DEFLATED_SPACE_LINES_MIB * mib + _DEFLATE_END
    crc = repeated_crc(_SPACE_LINES_MIB, mib, zlib.crc32(head))
    return _Member(name, zipfile.ZIP_DEFLATED, crc, len(body), len(head) + mib * MiB,
                   lambda: iter((body,)))


class ZipStream:
    """A classic (non-zip64) archive written in order, as it is served."""

    def __init__(self, members: list[_Member]) -> None:
        self.members = members
        self.offsets: list[int] = []
        offset = 0
        for m in members:
            self.offsets.append(offset)
            offset += 30 + len(m.name) + m.csize
        central = b"".join(
            struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, m.method, 0, _DOS_DATE,
                        m.crc, m.csize, m.usize, len(m.name), 0, 0, 0, 0, 0, off) + m.name
            for m, off in zip(members, self.offsets)
        )
        self.tail = central + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(members),
                                          len(members), len(central), offset, 0)
        self.size = offset + len(self.tail)

    def chunks(self):
        for m in self.members:
            yield struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, m.method, 0, _DOS_DATE,
                              m.crc, m.csize, m.usize, len(m.name), 0) + m.name
            yield from m.parts()
        yield self.tail

    def table(self) -> list[tuple]:
        """What zipfile's central directory read must report for this archive."""
        return [(m.name.decode("ascii"), m.crc, m.usize, m.csize, off)
                for m, off in zip(self.members, self.offsets)]


def central_directory(path: Path) -> list[tuple] | None:
    try:
        with zipfile.ZipFile(path) as zf:
            return [(i.filename, i.CRC, i.file_size, i.compress_size, i.header_offset)
                    for i in zf.infolist()]
    except Exception:
        return None


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(MiB), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Publication:
    """One archive as the fake origin publishes it."""

    def __init__(self, *, data: bytes | None = None, stream: ZipStream | None = None):
        self.data, self.stream = data, stream
        self.size = len(data) if data is not None else stream.size
        self.digest = hashlib.sha256(data).hexdigest()[:16] if data is not None else None

    def chunks(self):
        if self.data is not None:
            for i in range(0, len(self.data), MiB):
                yield self.data[i:i + MiB]
        else:
            yield from self.stream.chunks()

    def is_cache(self, path: Path) -> bool:
        """Is `path` this publication? Byte for byte for small ones; by size and the
        full central directory (names, CRCs, sizes, offsets) for generated ones, so a
        512 MiB comparison does not hash 512 MiB twice."""
        if not path.exists() or path.stat().st_size != self.size:
            return False
        if self.data is not None:
            return file_digest(path) == self.digest
        return central_directory(path) == self.stream.table()


# ---------------------------------------------------------------------------
# The wire: installed BEFORE any backend import, so however a backend module
# spells its httpx import, the client it builds is this one.
# ---------------------------------------------------------------------------

import httpx  # noqa: E402

PUBLISHED: dict[str, Publication] = {}
WIRE = {"requests": 0, "bytes_served": 0, "bodies_completed": 0}


class _Body(httpx.AsyncByteStream):
    """Serves a publication chunk by chunk, yielding to the loop like a socket read."""

    def __init__(self, pub: Publication) -> None:
        self._pub = pub

    async def __aiter__(self):
        for chunk in self._pub.chunks():
            await asyncio.sleep(0)  # a real socket read suspends here
            WIRE["bytes_served"] += len(chunk)
            yield chunk
        WIRE["bodies_completed"] += 1


def _route(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    lowered = url.lower()
    if "njtransit" in lowered or "raildata" in lowered:
        raise AssertionError(f"refusing a request naming NJ Transit: {url!r}")
    pub = PUBLISHED.get(url)
    if pub is None:
        raise AssertionError(f"the pipeline asked for an unpublished URL: {url!r}")
    WIRE["requests"] += 1
    return httpx.Response(
        200,
        headers={"content-type": "application/zip", "content-length": str(pub.size)},
        stream=_Body(pub),
    )


_FAKE_WIRE = httpx.MockTransport(_route)


class _FakeWireAsyncClient(httpx.AsyncClient):
    """Every async client in this process talks to the mock, never to a socket."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs["transport"] = _FAKE_WIRE
        kwargs["trust_env"] = False  # no proxy mounts from the environment
        super().__init__(*args, **kwargs)


httpx.AsyncClient = _FakeWireAsyncClient

# ---------------------------------------------------------------------------
# Containment, then the backend.
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain()
sys.path.insert(0, str(BACKEND))
from fastapi import FastAPI  # noqa: E402

import main  # noqa: E402
import static_data  # noqa: E402
import static_shared  # noqa: E402
import warmups  # noqa: E402

_hermetic.verify()

CACHE = static_data.SUBWAY_GTFS_ZIP

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, measured: str, audit: str) -> bool:
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"         measured: {measured}")
    print(f"         audit   : {audit}")
    return ok


# ---------------------------------------------------------------------------
# Subway publications
# ---------------------------------------------------------------------------


def csv_text(columns: list[str], rows) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def subway_members(tag: str, *, heavy_transfers: int = 0) -> dict[str, str]:
    """The members the real subway validator requires, minimal and valid.

    `tag` goes into a station name so each publication has its own bytes and a
    promotion is observable as a change of cache contents. `heavy_transfers` adds
    that many valid cross-stop rows to transfers.txt, which the validator parses.
    """
    stops = csv_text(
        ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"],
        [
            {"stop_id": "101", "stop_name": f"Alpha {tag}", "stop_lat": "40.70000",
             "stop_lon": "-74.00000", "location_type": "1"},
            {"stop_id": "101N", "stop_name": f"Alpha {tag}", "stop_lat": "40.70000",
             "stop_lon": "-74.00000", "location_type": "0", "parent_station": "101"},
        ],
    )
    transfers = [{"from_stop_id": "101", "to_stop_id": "103", "transfer_type": "2",
                  "min_transfer_time": "180"}]
    transfers += (
        {"from_stop_id": f"T{i}", "to_stop_id": f"T{i + 1}", "transfer_type": "2",
         "min_transfer_time": "180"}
        for i in range(heavy_transfers)
    )
    # One trip and one call: since A02 the validator reads trips.txt and stop_times.txt
    # through the loader's own generators and wants a row from each, so a publication
    # with headers only there is one it refuses, and every arm here needs one it accepts.
    return {
        "stops.txt": stops,
        "trips.txt": csv_text(
            ["route_id", "service_id", "trip_id", "shape_id"],
            [{"route_id": "A", "service_id": "wk", "trip_id": "t1", "shape_id": "A..N01R"}],
        ),
        "shapes.txt": csv_text(
            ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"],
            [
                {"shape_id": "A..N01R", "shape_pt_sequence": "1",
                 "shape_pt_lat": "40.70000", "shape_pt_lon": "-74.00000"},
                {"shape_id": "A..N01R", "shape_pt_sequence": "2",
                 "shape_pt_lat": "40.71000", "shape_pt_lon": "-74.00100"},
            ],
        ),
        "stop_times.txt": csv_text(
            ["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"],
            [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}],
        ),
        "transfers.txt": csv_text(
            ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"], transfers
        ),
    }


def built_archive(tag: str, *, extra_members: int = 0, heavy_transfers: int = 0) -> Publication:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for member, text in subway_members(tag, heavy_transfers=heavy_transfers).items():
            zf.writestr(member, text)
        for i in range(extra_members):
            zf.writestr(f"extra/{i:05d}.txt", b"")
    return Publication(data=buf.getvalue())


def streamed_archive(tag: str, *, pad_mib: dict[str, int] | None = None,
                     extra: tuple[_Member, ...] = ()) -> Publication:
    """The subway members (each named in `pad_mib` followed by that many MiB of space
    lines), then the `extra` members, generated as served."""
    pad_mib = pad_mib or {}
    members = [
        member_padded_table(name, text, pad_mib[name]) if name in pad_mib
        else member_text(name, text)
        for name, text in subway_members(tag).items()
    ]
    return Publication(stream=ZipStream(members + list(extra)))


def fresh_start() -> None:
    """Cold start: no cache, no status, so each publication stands alone."""
    CACHE.unlink(missing_ok=True)
    static_shared.reset_archive_status()


def subway_status() -> dict:
    return static_shared.archive_status().get("subway", {})


def cache_label(pub: Publication) -> str:
    if not CACHE.exists():
        return "absent"
    return "== published" if pub.is_cache(CACHE) else "a different archive"


async def _ticker(stop: asyncio.Event, stamps: list[float]) -> None:
    """A 1 ms heartbeat: any other coroutine sharing the loop (pollers, /healthz).
    It stamps on every wake, the last included, so a stall that runs up to the moment
    `stop` is set is still closed by a tick and counted."""
    while True:
        stamps.append(time.perf_counter())
        if stop.is_set():
            return
        await asyncio.sleep(0.001)


def longest_gap_ms(stamps: list[float]) -> float:
    return max((b - a for a, b in zip(stamps, stamps[1:])), default=0.0) * 1000


async def quiesce(limit_s: float = 10.0) -> None:
    """Harness hygiene, not a measurement: wait until the cache directory holds no
    file but the cache. On the unmodified code nothing is in flight. Under a repair
    that moves the publication off the loop, an abandoned worker may still be
    validating a stage file after an attempt ended, and its late promotion must not
    land in the next publication's measurement."""
    deadline = time.perf_counter() + limit_s
    clear = 0
    while clear < 5 and time.perf_counter() < deadline:
        busy = CACHE.parent.exists() and any(p != CACHE for p in CACHE.parent.iterdir())
        clear = 0 if busy else clear + 1
        await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# H.0: the generated-archive writer, proved at small scale
# ---------------------------------------------------------------------------


def builder_selfcheck() -> None:
    print("-" * 78)
    print("H.0  the generated-archive writer reads back intact")
    print("-" * 78)
    plain = subway_members("selfcheck")
    pub = streamed_archive("selfcheck", pad_mib={EXPANDED_MEMBER: 2},
                           extra=(member_stored_zeros("stored.bin", 3),))
    data = b"".join(pub.stream.chunks())
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        bad = zf.testzip()
        padded = zf.read(EXPANDED_MEMBER)
        contents_ok = (zf.read("stored.bin") == bytes(3 * MiB)
                       and padded == plain[EXPANDED_MEMBER].encode() + _SPACE_LINES_MIB * 2)
    # The padded table, read the way the subway reads it: its own stop_times reader
    # yields the same rows from the padded member as from the unpadded one.
    padded_rows = list(static_data._iter_trip_stops(io.BytesIO(padded)))
    plain_rows = list(static_data._iter_trip_stops(io.BytesIO(plain[EXPANDED_MEMBER].encode())))
    as_csv = (["trip_id", "stop_id"], padded_rows)
    same_table = bool(plain_rows) and padded_rows == plain_rows
    # The writer's own encoding of the subway members, unpadded, through the real
    # validator: proves the generated bytes are a publication it accepts. Unpadded so
    # that a size or ratio ceiling (a repair) fails 3.3, not this harness check.
    path = _TMP / "selfcheck.zip"
    path.write_bytes(b"".join(streamed_archive("selfcheck").stream.chunks()))
    verdict = "accepted"
    try:
        static_shared.validate_archive(path, static_data.validate_subway_archive)
    except static_shared.StaticValidationError as exc:
        verdict = f"REJECTED ({exc})"
    path.unlink()
    check(
        f"H.0 a generated archive (a {EXPANDED_MEMBER} padded with deflated lines of spaces, a "
        "stored member of zeros) passes zipfile.testzip, reads back exactly, matches its own "
        "central directory and reads as the unpadded table; unpadded, the real validator "
        "accepts the writer's output",
        bad is None and contents_ok and same_table and len(data) == pub.size
        and central_directory_of_bytes(data) == pub.stream.table() and verdict == "accepted",
        f"testzip={bad!r}, contents {'exact' if contents_ok else 'WRONG'}, padded "
        f"{EXPANDED_MEMBER} as CSV: {len(as_csv[1])} rows under {as_csv[0]} "
        f"({'same' if same_table else 'DIFFERENT'} as unpadded), {len(data):,} of "
        f"{pub.size:,} bytes; unpadded, validator {verdict}",
        "not stated (harness)",
    )
    print()


def central_directory_of_bytes(data: bytes) -> list[tuple]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return [(i.filename, i.CRC, i.file_size, i.compress_size, i.header_offset)
                for i in zf.infolist()]


# ---------------------------------------------------------------------------
# ARM 1: the deadline that did not preempt (closed; pins the repair)
# ---------------------------------------------------------------------------

DEADLINE_S = 0.010  # the audit's asyncio.timeout(10 ms)
# 1c's attempt deadline is DEADLINE_S, or a tenth of the heavy publication's own
# standalone validation time where that is shorter. A fixed 10 ms would turn 1c.0 red
# on a machine about four times faster than the one this was measured on (215 to 378 ms
# here) for a reason that has nothing to do with the finding; the tenth keeps the
# deadline expiring well inside the validation wherever this runs. The floor is where a
# deadline stops meaning anything against event-loop scheduling.
ATTEMPT_DEADLINE_FLOOR_S = 0.002
VALIDATOR_S = 0.080  # the audit's 80 ms validator
TRANSFER_RETRIES = 5
HEAVY_TRANSFERS = 150_000  # 1c's transfers.txt rows; the live table has 613

_VALIDATOR = {"started": None, "finished": None}


def slow_validator(zf: zipfile.ZipFile) -> None:
    """The real subway validator, then a busy-wait to 80 ms: synchronous CPU work."""
    started = time.perf_counter()
    _VALIDATOR["started"] = started
    try:
        static_data.validate_subway_archive(zf)
        while time.perf_counter() - started < VALIDATOR_S:
            pass
    finally:
        _VALIDATOR["finished"] = time.perf_counter()


async def validator_settled(limit_s: float = 2.0) -> None:
    """Harness hygiene: a repair that runs the validator on a worker thread leaves it
    running after the deadline; let it finish before the next run resets the record."""
    deadline = time.perf_counter() + limit_s
    while (_VALIDATOR["started"] is not None and _VALIDATOR["finished"] is None
           and time.perf_counter() < deadline):
        await asyncio.sleep(0.005)


async def run_under_deadline(pub: Publication, *, then_suspend: bool) -> dict:
    """Publish `pub`, then run the real staged_fetch inside asyncio.timeout(10 ms).

    The validator runs in a worker thread since the repair, so it goes on after the
    deadline; validator_settled waits for it before the next run, and the stage it
    reads has already been unlinked by staged_fetch.

    then_suspend=False: staged_fetch is the last statement in the block.
    then_suspend=True : the block continues with one await that suspends, as the
                        subway warmup's block does (its next await is a to_thread).

    A run in which the 10 ms expired during the TRANSFER, before the validator
    started, measures nothing about the validator; it is repeated, up to
    TRANSFER_RETRIES times, and the repeat count is reported.
    """
    PUBLISHED[SUBWAY_URL] = pub
    for attempt in range(1, TRANSFER_RETRIES + 1):
        fresh_start()
        _VALIDATOR["started"] = _VALIDATOR["finished"] = None
        stop = asyncio.Event()
        stamps: list[float] = []
        ticker = asyncio.create_task(_ticker(stop, stamps))
        await asyncio.sleep(0.005)  # let the heartbeat establish itself
        returned = False
        cache_at_return = None
        outcome = "completed"
        t0 = time.perf_counter()
        try:
            async with asyncio.timeout(DEADLINE_S):
                await static_shared.staged_fetch(
                    SUBWAY_URL,
                    CACHE,
                    slow_validator,
                    key="subway",
                    label="static subway GTFS (a09 arm 1)",
                )
                returned = True
                cache_at_return = cache_label(pub)
                if then_suspend:
                    await asyncio.sleep(0.001)
        except TimeoutError:
            outcome = "TimeoutError"
        except Exception as exc:  # a repair may refuse the stage outright; report it
            outcome = f"raised {type(exc).__name__}: {exc}"
        elapsed = time.perf_counter() - t0
        # Was the validator still running when the block ended? An ORDERING, not a
        # duration, so it holds on any machine: inline, the block cannot end before
        # the validator does; off the loop, the deadline ends it mid-validation.
        running_at_end = _VALIDATOR["started"] is not None and _VALIDATOR["finished"] is None
        # The boundary afterwards: does the expired deadline reach a later await?
        late = "slept normally"
        try:
            await asyncio.sleep(0.005)
        except asyncio.CancelledError:
            late = "CANCELLED"
            current = asyncio.current_task()
            if current is not None:
                current.uncancel()
        stop.set()
        await ticker
        await validator_settled()
        await quiesce()
        if outcome == "TimeoutError" and _VALIDATOR["started"] is None and attempt < TRANSFER_RETRIES:
            continue
        started = _VALIDATOR["started"]
        return {
            "attempts": attempt,
            "outcome": outcome,
            "returned": returned,
            "elapsed_ms": elapsed * 1000,
            "running_at_end": running_at_end,
            "validator_start_ms": None if started is None else (started - t0) * 1000,
            "validator_ran_ms": (
                None
                if started is None or _VALIDATOR["finished"] is None
                else (_VALIDATOR["finished"] - started) * 1000
            ),
            "max_gap_ms": longest_gap_ms(stamps),
            "cache_at_return": cache_at_return,
            "cache_after": cache_label(pub),
            "status": subway_status(),
            "late": late,
        }
    raise AssertionError("unreachable")


def validator_note(run: dict) -> str:
    if run["validator_start_ms"] is None:
        return (f"the validator never started (the deadline expired during the transfer "
                f"on all {run['attempts']} attempt(s))")
    return (f"the validator began {run['validator_start_ms']:.1f} ms into the block and ran "
            f"{run['validator_ran_ms'] or 0:.1f} ms")


class _LogTap(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def run_warmup_attempt(pub: Publication, deadline_s: float) -> dict:
    """One attempt of the real subway warmup under a whole-attempt deadline of deadline_s.

    The attempt has ended when the app's subway status leaves "loading" ("failed" or
    "ready", whichever the warmup reaches); the warmup is then cancelled in its
    back-off sleep. An attempt whose deadline expired before the fake origin finished
    serving the body measured the transfer, not the validation; it is repeated, up
    to TRANSFER_RETRIES times, and the repeat count is reported.
    """
    PUBLISHED[SUBWAY_URL] = pub
    tap = _LogTap()
    warm_log = logging.getLogger("main")  # the logger the warmups write to
    saved = (main.STATIC_ATTEMPT_DEADLINE_S, warm_log.propagate)
    warm_log.addHandler(tap)
    warm_log.propagate = False
    try:
        for attempt in range(1, TRANSFER_RETRIES + 1):
            fresh_start()
            tap.lines.clear()
            app = FastAPI()
            app.state.subway_static_status = "loading"  # what main.lifespan sets
            completed_before = WIRE["bodies_completed"]
            stop = asyncio.Event()
            stamps: list[float] = []
            ticker = asyncio.create_task(_ticker(stop, stamps))
            await asyncio.sleep(0.005)
            main.STATIC_ATTEMPT_DEADLINE_S = deadline_s
            t0 = time.perf_counter()
            task = asyncio.create_task(warmups._warm_subway_static(app))
            try:
                while app.state.subway_static_status == "loading" and time.perf_counter() - t0 < 30:
                    await asyncio.sleep(0.001)
                ended_ms = (time.perf_counter() - t0) * 1000
                at_end = cache_label(pub)
                status = app.state.subway_static_status
            finally:
                main.STATIC_ATTEMPT_DEADLINE_S = saved[0]
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            stop.set()
            await ticker
            transfer_done = WIRE["bodies_completed"] > completed_before
            await quiesce()
            if not transfer_done and attempt < TRANSFER_RETRIES:
                continue
            return {
                "attempts": attempt,
                "ended_ms": ended_ms,
                "status": status,
                "cache_at_end": at_end,
                "max_gap_ms": longest_gap_ms(stamps),
                "transfer_done": transfer_done,
                "log": [line for line in tap.lines if "subway_static_status" in line],
            }
    finally:
        warm_log.removeHandler(tap)
        warm_log.propagate = saved[1]
    raise AssertionError("unreachable")


async def arm1() -> dict:
    print("-" * 78)
    print("ARM 1  the deadline that did not preempt (closed; pins the repair)")
    print("-" * 78)

    # Control: the same kind of publication, no deadline. Proves the archive is a
    # valid subway publication, so the arm measures the deadline and not the bytes.
    control = built_archive("control")
    PUBLISHED[SUBWAY_URL] = control
    fresh_start()
    control_error = None
    try:
        await static_shared.staged_fetch(
            SUBWAY_URL, CACHE, slow_validator, key="subway", label="a09 arm 1 control"
        )
    except Exception as exc:
        control_error = f"{type(exc).__name__}: {exc}"
    await validator_settled()
    check(
        "1.0 control: the slow-validator publication, with no deadline, is promoted",
        control_error is None
        and control.is_cache(CACHE)
        and subway_status().get("last_promoted_at") is not None,
        f"cache {cache_label(control)}" + (f"; RAISED {control_error}" if control_error else ""),
        "not stated (control)",
    )

    # THE REPAIR, PINNED (claude/a02-publication-validation). Each check prints the
    # recorded BEFORE beside what it measures, so a regression reads as the finding.
    # 1a.4's bound: inline, the heartbeat's gap is the whole 80 ms validation; off the
    # loop it is an interpreter switch slice or two (4 to 16 ms measured).
    half = VALIDATOR_S * 1000 / 2
    a = await run_under_deadline(built_archive("a"), then_suspend=False)
    print(f"  run 1a: {a['attempts']} attempt(s); {validator_note(a)}")
    check(
        "1a.1 TimeoutError leaves asyncio.timeout(0.01) around the real staged_fetch, "
        "delivered while the validator runs",
        a["outcome"] == "TimeoutError" and a["validator_start_ms"] is not None,
        f"block outcome: {a['outcome']}; {validator_note(a)}",
        "BEFORE: completed after 81.5 ms with no TimeoutError (audit: about 81 ms)",
    )
    check(
        "1a.2 the block ended while the validator was still running, not after it",
        a["running_at_end"],
        f"{a['elapsed_ms']:.1f} ms against a {DEADLINE_S * 1000:.0f} ms deadline and the "
        f"validator's {VALIDATOR_S * 1000:.0f} ms",
        "BEFORE: 81.5 ms, after the validator had finished",
    )
    check(
        "1a.3 nothing was promoted: no cache, and status records no promotion",
        a["cache_after"] == "absent" and a["status"].get("last_promoted_at") is None,
        f"cache {a['cache_after']}, status {a['status']}",
        "BEFORE: the stage promoted over the cache ('completed and promoted a stage')",
    )
    check(
        "1a.4 the event loop kept running through the validation (1 ms heartbeat gap under "
        "half the validation)",
        a["max_gap_ms"] < half,
        f"longest heartbeat gap {a['max_gap_ms']:.1f} ms",
        "BEFORE: held for the whole validation (gap at least 80 ms)",
    )
    check(
        "1a.5 the expired deadline is spent by its TimeoutError: a later await is not cancelled",
        a["late"] == "slept normally",
        f"a 5 ms sleep after the block: {a['late']}",
        "not stated (boundary record; unchanged)",
    )

    b = await run_under_deadline(built_archive("b"), then_suspend=True)
    print(f"  run 1b: {b['attempts']} attempt(s); block continued with one await that suspends")
    check(
        "1b.1 with a suspending await after the stage, TimeoutError is still raised INSIDE "
        "staged_fetch, which never returns",
        b["outcome"] == "TimeoutError" and not b["returned"],
        f"block outcome: {b['outcome']}; staged_fetch had returned: {b['returned']}; "
        f"elapsed {b['elapsed_ms']:.1f} ms; {validator_note(b)}",
        "BEFORE: raised at the later await, after staged_fetch had returned",
    )
    check(
        "1b.2 ...so no promotion happened for it to arrive after",
        b["cache_after"] == "absent" and b["status"].get("last_promoted_at") is None,
        f"cache after the TimeoutError: {b['cache_after']}; status {b['status']}",
        "BEFORE: the promotion had already happened, so the late timeout undid nothing",
    )

    # 1c: the production attempt, nothing injected into the pipeline.
    heavy = built_archive("heavy", heavy_transfers=HEAVY_TRANSFERS)
    heavy_path = _TMP / "heavy.zip"
    heavy_path.write_bytes(heavy.data)
    standalone: list[float] = []
    standalone_error = None

    def time_standalone(runs: int) -> None:
        """The real validator alone, synchronously; the SHORTEST run of the three (two
        before the attempt, one after, so one load burst cannot inflate it) is the
        yardstick for 1c.2."""
        nonlocal standalone_error
        for _ in range(runs):
            t0 = time.perf_counter()
            try:
                static_shared.validate_archive(heavy_path, static_data.validate_subway_archive)
            except static_shared.StaticValidationError as exc:  # e.g. a row ceiling
                standalone_error = str(exc)
            standalone.append((time.perf_counter() - t0) * 1000)

    time_standalone(2)
    attempt_deadline_s = max(
        ATTEMPT_DEADLINE_FLOOR_S, min(DEADLINE_S, min(standalone) / 1000 / 10)
    )
    # THREE ATTEMPTS, AND THE SHORTEST OF THEIR LONGEST GAPS. Inline, every attempt's
    # longest gap is the whole validation, so the shortest of three still is, and 1c.2
    # still fails by construction. Off the loop, an attempt's longest gap is a few
    # interpreter switch slices (4 to 24 ms measured), but it also counts anything else
    # that stops this process for a moment (a garbage collection on the worker thread,
    # the scheduler), and one such moment in one attempt crossed the bound on a run
    # of the unmodified tree. Between attempts, the previous attempt's validation is
    # left to finish on its thread, so it does not share the next attempt's loop.
    runs = []
    for _ in range(3):
        runs.append(await run_warmup_attempt(heavy, attempt_deadline_s))
        await asyncio.sleep(2 * max(standalone) / 1000)
    c = dict(runs[0])
    c["gaps_ms"] = [run["max_gap_ms"] for run in runs]
    c["max_gap_ms"] = min(c["gaps_ms"])
    c["all_unpromoted"] = all(
        run["cache_at_end"] == "absent" and run["status"] == "failed" for run in runs
    )
    c["deadline_ms"] = attempt_deadline_s * 1000
    time_standalone(1)
    heavy_path.unlink()
    v_ms = min(standalone)
    print(f"  run 1c: {c['attempts']} attempt(s); the real subway warmup, attempt deadline "
          f"{attempt_deadline_s * 1000:.1f} ms, publication of {heavy.size:,} bytes with "
          f"{HEAVY_TRANSFERS + 1:,} transfers.txt rows")
    print(f"          attempt ended after {c['ended_ms']:.1f} ms with status {c['status']!r}; "
          f"warmup log: {c['log'] or 'nothing'}")
    check(
        "1c.0 the publication is valid and heavy against the deadline: its real validation, "
        "timed standalone, accepts it and takes at least 5x the attempt deadline",
        standalone_error is None and v_ms >= 5 * attempt_deadline_s * 1000,
        f"{v_ms:.1f} ms standalone (runs {', '.join(f'{x:.1f}' for x in standalone)} ms) "
        f"against a {attempt_deadline_s * 1000:.1f} ms deadline"
        + (f"; REJECTED: {standalone_error}" if standalone_error else ""),
        "not stated (precondition for 1c)",
    )
    check(
        "1c.1 the production warmup's expired attempt deadline was delivered before any "
        "promotion: each of the three attempts failed and no cache was written",
        c["all_unpromoted"],
        "; ".join(
            f"cache {run['cache_at_end']}, status {run['status']!r}, transfer finished "
            f"{run['transfer_done']}"
            for run in runs
        ),
        "BEFORE: promoted before the deadline was delivered ('completed and promoted a stage "
        "... without a timeout')",
    )
    # A quarter, as the record's own bound was: the in-attempt run and the standalone runs
    # see different moments of machine load, and work off this loop costs it a scheduling
    # slice (1.6 to 9.3 ms measured when the record was taken), not a validation.
    check(
        "1c.2 the warmup kept the loop it shares running through the real validation: the "
        "1 ms heartbeat's longest gap, the shortest of three attempts, is under a quarter of "
        "the shortest standalone validation",
        c["max_gap_ms"] < v_ms / 4,
        f"longest heartbeat gap {c['max_gap_ms']:.1f} ms (attempts "
        f"{', '.join(f'{g:.1f}' for g in c['gaps_ms'])} ms) against {v_ms:.1f} ms standalone "
        f"({c['max_gap_ms'] / max(v_ms, 1e-9):.2f}x)",
        "BEFORE: 220 to 280 ms, the whole validation",
    )
    print()
    return {"a": a, "b": b, "c": c, "v_ms": v_ms}


# ---------------------------------------------------------------------------
# ARM 2: no byte ceiling on a transfer
# ---------------------------------------------------------------------------

TRANSFER_MIB = 512


async def arm2() -> dict:
    print("-" * 78)
    print(f"ARM 2  no byte ceiling on a transfer ({TRANSFER_MIB} MiB stored member)")
    print("-" * 78)
    pub = streamed_archive("large", extra=(member_stored_zeros("padding.bin", TRANSFER_MIB),))
    fresh_start()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(CACHE.parent).free
    if not check(
        "2.0 scratch space for one stage file of the publication",
        free >= pub.size + 256 * MiB,
        f"{free:,} bytes free beside the cache for a {pub.size:,}-byte stage",
        "not stated (harness)",
    ):
        print()
        return {"size": pub.size}
    PUBLISHED[SUBWAY_URL] = pub
    served_before = WIRE["bytes_served"]
    completed_before = WIRE["bodies_completed"]
    error = None
    stops: dict = {}
    t0 = time.perf_counter()
    try:
        stops = await static_data.load_subway_stops()
    except Exception as exc:  # a refusal is a legitimate outcome to report
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.perf_counter() - t0
    served = WIRE["bytes_served"] - served_before
    completed = WIRE["bodies_completed"] - completed_before
    status = subway_status()
    cache_size = CACHE.stat().st_size if CACHE.exists() else 0
    promoted = pub.is_cache(CACHE)
    print(f"  body {pub.size:,} bytes; served {served:,}; load took {elapsed:.2f} s"
          + (f"; RAISED {error}" if error else ""))
    check(
        f"2.1 the real transfer accepted every byte of the {pub.size:,}-byte body",
        served == pub.size and completed == 1,
        f"{served:,} of {pub.size:,} bytes left the fake origin; body read to its end: "
        f"{completed == 1}",
        "Static transfers have no byte ceiling",
    )
    check(
        "2.2 the oversized publication was validated and promoted as the subway cache",
        error is None
        and promoted
        and len(stops) > 0
        and status.get("last_promoted_at") is not None
        and status.get("last_download_error") is None,
        f"cache {cache_size:,} bytes, identical to the publication: {promoted}, "
        f"last_download_error={status.get('last_download_error')!r}, stops loaded {len(stops)}",
        "Static transfers have no byte ceiling",
    )
    fresh_start()  # release the 512 MiB now
    print()
    return {"size": pub.size, "served": served}


# ---------------------------------------------------------------------------
# ARM 3: no member-count or expanded-size limit at validation
# ---------------------------------------------------------------------------

EXTRA_MEMBERS = 70_000
# The member arm 3's expansion sits on; the docstring's ARM 3 says why this one.
EXPANDED_MEMBER = "stop_times.txt"
EXPANDED_MIB = 2048


async def load_and_report(pub: Publication) -> tuple[str | None, dict]:
    PUBLISHED[SUBWAY_URL] = pub
    fresh_start()
    error = None
    try:
        await static_data.load_subway_stops()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return error, subway_status()


async def arm3() -> dict:
    print("-" * 78)
    print("ARM 3  no member-count or expanded-size limit at validation")
    print("-" * 78)
    # 3.0: the member that carries the expansion is one the real validator requires,
    # so a ceiling over the required members reaches it. Measured by rejection, not
    # read from a list: the small publication without it must not validate.
    verdicts = {}
    for label, drop in (("complete", None), (f"without {EXPANDED_MEMBER}", EXPANDED_MEMBER)):
        members = {n: t for n, t in subway_members("required").items() if n != drop}
        path = _TMP / "required.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, text in members.items():
                zf.writestr(name, text)
        try:
            static_shared.validate_archive(path, static_data.validate_subway_archive)
            verdicts[label] = "accepted"
        except static_shared.StaticValidationError as exc:
            verdicts[label] = f"REJECTED ({exc})"
        path.unlink()
    check(
        f"3.0 {EXPANDED_MEMBER} is a member the real subway validator requires: the small "
        "publication validates, and the same publication without it does not",
        verdicts["complete"] == "accepted"
        and verdicts[f"without {EXPANDED_MEMBER}"].startswith("REJECTED"),
        "; ".join(f"{k}: {v}" for k, v in verdicts.items()),
        "not stated (precondition for 3.3)",
    )

    many = built_archive("many", extra_members=EXTRA_MEMBERS)
    with zipfile.ZipFile(io.BytesIO(many.data)) as zf:
        offered = len(zf.infolist())
    error, status = await load_and_report(many)
    promoted_members = len(central_directory(CACHE) or [])
    check(
        f"3.1 an archive of {offered:,} members passes the real subway validator and is promoted",
        error is None
        and many.is_cache(CACHE)
        and status.get("last_promoted_at") is not None
        and status.get("last_download_error") is None,
        f"cache {cache_label(many)}, last_download_error="
        f"{status.get('last_download_error')!r}" + (f", RAISED {error}" if error else ""),
        "archive validation lacks member-count ... limits",
    )
    check(
        "3.2 the promoted cache holds every offered member",
        promoted_members == offered,
        f"{promoted_members:,} members in the cache, {offered:,} offered",
        "archive validation lacks member-count ... limits",
    )

    wide = streamed_archive("expanded", pad_mib={EXPANDED_MEMBER: EXPANDED_MIB})
    member = next(m for m in wide.stream.members if m.name.decode() == EXPANDED_MEMBER)
    declared, compressed = member.usize, member.csize
    error, status = await load_and_report(wide)
    row = {r[0]: r for r in central_directory(CACHE) or []}.get(EXPANDED_MEMBER)
    cache_declares, cache_compressed = (row[2], row[3]) if row else (0, 0)
    check(
        f"3.3 an archive whose {EXPANDED_MEMBER} declares {declared:,} expanded bytes, in "
        f"{wide.size:,} bytes on the wire, passes the real subway validator and is promoted",
        error is None
        and wide.is_cache(CACHE)
        and status.get("last_promoted_at") is not None
        and status.get("last_download_error") is None,
        f"cache {cache_label(wide)}, last_download_error="
        f"{status.get('last_download_error')!r}" + (f", RAISED {error}" if error else ""),
        "archive validation lacks ... uncompressed-size limits",
    )
    check(
        f"3.4 the promoted cache's {EXPANDED_MEMBER} declares the whole expanded size",
        cache_declares == declared and cache_compressed == compressed,
        f"{cache_declares:,} bytes declared for {cache_compressed:,} compressed "
        f"({cache_declares / max(cache_compressed, 1):,.0f}:1)",
        "archive validation lacks ... uncompressed-size limits",
    )
    fresh_start()
    print()
    return {"members": offered, "expanded": declared, "ratio": declared / compressed,
            "wide_size": wide.size}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def amain() -> int:
    print("=" * 78)
    print("A09  Upstream resource use is not sufficiently bounded")
    print("=" * 78)
    print(f"repo      : {REPO}")
    print(f"DATA_DIR  : {os.environ['DATA_DIR']}")
    print(f"upstream  : {SUBWAY_URL} (served by an in-process mock; no socket)")
    print()
    builder_selfcheck()
    one = await arm1()
    two = await arm2()
    three = await arm3()

    check(
        "H.1 no socket connect or name resolution was attempted",
        not SOCKET_ATTEMPTS,
        f"{len(SOCKET_ATTEMPTS)} attempt(s) {SOCKET_ATTEMPTS[:2]}",
        "not stated (hermetic)",
    )

    failed = [name for name, ok in RESULTS if not ok]
    print()
    if failed:
        print("FAILED CHECKS:")
        for name in failed:
            print(f"  - {name}")
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    a, c = one["a"], one["c"]
    print(
        "DISPOSITION: VERIFIED  Arm 1 closed on claude/a02-publication-validation: an 80 ms "
        "validator inside asyncio.timeout(10 ms) now raises TimeoutError out of the real "
        f"staged_fetch after {a['elapsed_ms']:.1f} ms with nothing promoted (BEFORE: promoted "
        f"after 81.5 ms, no TimeoutError), and the production subway warmup under a "
        f"{c['deadline_ms']:.0f} ms attempt deadline failed at it with nothing promoted while "
        f"its loop's longest stall was {c['max_gap_ms']:.0f} ms (BEFORE: 220 to 280 ms, then "
        "promoted). Arms 2 and 3 still stand: the real transfer and "
        f"subway validator accepted and promoted a {two['size']:,}-byte publication, an archive "
        f"of {three['members']:,} members and one whose required {EXPANDED_MEMBER} declares "
        f"{three['expanded']:,} expanded bytes ({three['ratio']:,.0f}:1), so no byte or "
        f"member-count ceiling, and no expanded-size ceiling that reaches {EXPANDED_MEMBER}, "
        "exists at or below those sizes."
    )
    return 0


def main_() -> int:
    try:
        return asyncio.run(amain())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_())
