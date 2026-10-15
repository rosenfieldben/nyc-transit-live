"""C5: stage, validate, promote, and the empty-subway gate.

WHAT THESE PROVE. The pre-C5 downloaders renamed a finished download over the
cached archive before anything parsed it, so a single bad upstream publication
destroyed the last-known-good file. Every test below that says BYTE-IDENTICAL is
pinning the fix: after a rejected publication the cache must be the same bytes it
was, not merely "still parseable". A sha256 comparison is the assertion, because
"the loader still returned data" would pass even if the file had been rewritten
with different-but-valid content.

Hermetic throughout: every download is an injected callable that writes bytes, and
every cache path is a tmp dir. No socket is opened.
"""

from __future__ import annotations

import asyncio
import csv
import errno
import hashlib
import io
import os
import struct
import threading
import time
import types
import zipfile
from pathlib import Path

import httpx
import pytest

import ferry_static
import main as app_module
import njt_auth
import njt_static
import path_static
import railroad_static
import static_data
import static_shared
import warmups
from conftest import configure_njt
from tests.archive_damage import undecodable_deep_in

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# One GTFS archive builder that satisfies all four loaders
# ---------------------------------------------------------------------------
#
# The four schemas differ (the subway folds platforms to parents, PATH keys on
# location_type=1 parents, ferry and railroad stops are flat), but a single stops
# table with a parent row and a child row satisfies every one of them, so the
# per-loader tests below can share one builder and differ only in what they call.

_STOPS_COLS = [
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "location_type",
    "parent_station",
    "wheelchair_boarding",
]
_ROUTES_COLS = ["route_id", "route_short_name", "route_long_name", "route_color"]
_TRIPS_COLS = ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"]
_SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]
_STOP_TIMES_COLS = ["trip_id", "stop_id", "stop_sequence"]
_TRANSFERS_COLS = ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"]

_STOP_ROWS = [
    {
        "stop_id": "101",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "1",
        "wheelchair_boarding": "1",
    },
    {
        "stop_id": "101N",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "0",
        "parent_station": "101",
    },
]
_ROUTE_ROWS = [{"route_id": "R1", "route_long_name": "Alpha Line", "route_color": "00839C"}]
# The shape id is in the subway's own form (route, dots, direction), because the subway
# validator reads shapes.txt through its loader's parser and counts only points of a
# shape that names a route (A02). The other three loaders join trips to shapes by id
# and care nothing for its form.
_TRIP_ROWS = [
    {
        "route_id": "R1",
        "service_id": "wk",
        "trip_id": "t1",
        "direction_id": "0",
        "shape_id": "R1..N01R",
    }
]
_SHAPE_ROWS = [
    {
        "shape_id": "R1..N01R",
        "shape_pt_sequence": str(i),
        "shape_pt_lat": str(40.70 + i / 100),
        "shape_pt_lon": str(-74.00 - i / 100),
    }
    for i in range(1, 4)
]
_STOP_TIME_ROWS = [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}]


def _csv(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def good_archive(stops=_STOP_ROWS, drop=(), routes=_ROUTE_ROWS, trips=None) -> bytes:
    """A zip every one of the five loaders' validators accepts. `drop` removes members;
    `trips` substitutes raw bytes for trips.txt (used to plant an undecodable byte
    in a table the light validator does not parse)."""
    members = {
        "stops.txt": _csv(_STOPS_COLS, stops),
        "routes.txt": _csv(_ROUTES_COLS, routes),
        "trips.txt": _csv(_TRIPS_COLS, _TRIP_ROWS) if trips is None else trips,
        "shapes.txt": _csv(_SHAPES_COLS, _SHAPE_ROWS),
        "stop_times.txt": _csv(_STOP_TIMES_COLS, _STOP_TIME_ROWS),
        # One pair: the subway requires the member AND one cross-stop row (the operator's
        # ruling on review finding H2), and no other loader reads it.
        "transfers.txt": _csv(
            _TRANSFERS_COLS,
            [{"from_stop_id": "101", "to_stop_id": "103", "transfer_type": "2"}],
        ),
        # NJ Transit's two, which no other loader reads: it requires agency.txt, and its
        # service-date guard needs an added service day no earlier than today, so the one
        # row is dated far enough out that the archive never expires under the suite.
        "agency.txt": "agency_id,agency_name\nNJT,NJ TRANSIT\n",
        "calendar_dates.txt": "service_id,date,exception_type\nwk,20991231,1\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            if name not in drop:
                zf.writestr(name, content)
    return buf.getvalue()


# The four bad shapes the spec names, each a callable producing the published
# bytes. HEADERS-ONLY STOPS IS THIRD-AUDIT FINDING 4'S SHAPE: a structurally
# perfect archive whose stops table has a header row and nothing else.
def unsupported_compression() -> bytes:
    """A structurally valid zip whose members claim a compression method we cannot
    decompress, by rewriting the method field in both the local and the central
    headers. zipfile reads the directory fine (so namelist and the member checks
    pass) and raises NotImplementedError only at zf.open, which is a different
    escape route from BadZipFile and needs its own mapping to a rejection.
    """
    payload = bytearray(good_archive())
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        start = 0
        while (found := payload.find(signature, start)) != -1:
            payload[found + offset : found + offset + 2] = (99).to_bytes(2, "little")
            start = found + 4
    return bytes(payload)


def corrupt_deflate_stream() -> bytes:
    """A zip whose central directory is intact but whose stops.txt deflate stream is
    structurally damaged, which is what disk rot and a torn write actually look like.

    This raises zlib.error on read, not BadZipFile, and that distinction was a real
    permanent wedge: cached_archive_is_valid catches only StaticValidationError and
    OSError, and the loaders call it BEFORE the freshness test, so the escaping
    exception meant no download was ever attempted and the corrupt cache blocked its
    own repair forever. Corrupting the first bytes of the compressed stream (rather
    than the middle) is what makes zlib fail structurally instead of surfacing as a
    CRC mismatch, which zipfile already reports as BadZipFile.
    """
    payload = bytearray(good_archive())
    offset = payload.find(b"PK\x03\x04")
    while offset != -1:
        name_len, extra_len = struct.unpack("<HH", payload[offset + 26 : offset + 30])
        name = bytes(payload[offset + 30 : offset + 30 + name_len])
        if name == b"stops.txt":
            data = offset + 30 + name_len + extra_len
            payload[data : data + 3] = b"\xff\xff\xff"
            break
        offset = payload.find(b"PK\x03\x04", offset + 4)
    return bytes(payload)


def undecodable_deep_member() -> bytes:
    """Clean stops.txt and routes.txt, an undecodable byte in trips.txt.

    THE SHAPE THAT DEFEATED THE FIRST VERSION OF THIS PIPELINE. The light validator
    only PARSES stops and routes; it checks the rest for presence. So this archive
    passed validation, was renamed over the last-known-good, and was then deleted by
    the loader's residual arm when the real parse hit the bad byte: one bad
    publication and BOTH archives gone, which is precisely the bug C5 exists to
    prevent, surviving inside C5. staged_fetch now runs the publication validator,
    which does the full parse before the rename. The subway met the same shape later,
    as audit 2026-10-12's A02, and its validator now parses trips.txt too.
    """
    return good_archive(
        trips=b"route_id,service_id,trip_id,direction_id,shape_id\nR1,wk,t1,0,\xd1\xd1\n"
    )


BAD_PUBLICATIONS = {
    "truncated-zip": lambda: good_archive()[: len(good_archive()) // 2],
    "html-error-page": lambda: b"<!doctype html><html><body>503 Service Unavailable</body></html>",
    "missing-member": lambda: good_archive(drop=("stops.txt",)),
    "headers-only-stops": lambda: good_archive(stops=[]),
    "headers-only-routes": lambda: good_archive(routes=[]),
    "unsupported-compression": unsupported_compression,
    "corrupt-deflate-stream": corrupt_deflate_stream,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publisher(payload):
    """An injected transfer that writes `payload` to the stage file."""

    async def download(url, dest, deadline_s):
        dest.write_bytes(payload() if callable(payload) else payload)

    return download


@pytest.fixture(autouse=True)
def clean_archive_status():
    """_ARCHIVE_STATUS is module state that outlives an app; reset around each test."""
    static_shared.reset_archive_status()
    yield
    static_shared.reset_archive_status()


def accept_all(zf):
    return None


# ---------------------------------------------------------------------------
# staged_fetch: the pipeline itself
# ---------------------------------------------------------------------------


async def test_good_publication_is_promoted_and_recorded(tmp_path):
    dest = tmp_path / "gtfs.zip"
    payload = good_archive()
    await static_shared.staged_fetch(
        "https://example.invalid/gtfs.zip",
        dest,
        accept_all,
        key="k",
        label="test archive",
        download=publisher(payload),
    )
    assert dest.read_bytes() == payload
    entry = static_shared.archive_status()["k"]
    assert entry["last_promoted_at"] is not None
    assert entry["last_download_error"] is None
    assert entry["failed_downloads"] == 0
    assert not list(tmp_path.glob("*.part"))  # the stage file is gone either way


# ---------------------------------------------------------------------------
# staged_fetch's two-mode rule: what validate_when_replacing's failure does
# ---------------------------------------------------------------------------
#
# The operator's ruling on the A02 branch's review: a DECORATIVE gate (the subway's route
# lines) refuses a publication only when a cache that passes the always-gate is there to
# keep serving; with none, the publication is promoted and the failure is published on
# the archive's status as a degraded member, by name, with the parser's reason. Generic
# validators here, so the rule is pinned apart from any loader's tables.

GEOMETRY_REASON = "shapes.txt yielded no usable drawable route shapes"


def refuse_geometry(member: str | None = "shapes.txt"):
    calls: list = []

    def validate(zf):
        calls.append(zf.namelist())
        raise static_shared.StaticValidationError(GEOMETRY_REASON, member=member)

    validate.calls = calls
    return validate


async def _fetch_with_geometry(dest, payload, geometry, validate=accept_all):
    await static_shared.staged_fetch(
        "https://example.invalid/gtfs.zip",
        dest,
        validate,
        key="k",
        label="test archive",
        download=publisher(payload),
        validate_when_replacing=geometry,
    )


@pytest.mark.parametrize(("member", "key"), [("shapes.txt", "shapes.txt"), (None, "archive")])
async def test_a_decorative_failure_with_no_cache_is_promoted_and_named(tmp_path, member, key):
    """A cold start: nothing to keep, so refusing would trade the whole layer for its
    decoration. Promoted, no download error, and the degraded member on the status under
    its own name (or "archive" when the failure names none) with the parser's words."""
    dest = tmp_path / "gtfs.zip"
    payload = good_archive()
    await _fetch_with_geometry(dest, payload, refuse_geometry(member))
    assert dest.read_bytes() == payload
    entry = static_shared.archive_status()["k"]
    assert entry["last_promoted_at"] is not None
    assert entry["last_download_error"] is None and entry["failed_downloads"] == 0
    assert entry["degraded_members"] == {key: GEOMETRY_REASON}


async def test_a_decorative_failure_over_a_cache_that_serves_is_refused(tmp_path):
    """2026-09-05's F08: bad geometry never replaces an archive that passes the always-gate.
    Refused exactly as an always-gate failure is: the cache byte-identical, the failure
    counted and named, and nothing degraded about the archive still serving."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive())
    before = sha(dest)
    with pytest.raises(static_shared.StaticValidationError):
        await _fetch_with_geometry(dest, good_archive(drop=("routes.txt",)), refuse_geometry())
    assert sha(dest) == before
    assert not list(tmp_path.glob("*.part"))
    entry = static_shared.archive_status()["k"]
    assert entry["last_promoted_at"] is None and entry["failed_downloads"] == 1
    assert entry["last_download_error"] == f"invalid archive: {GEOMETRY_REASON}"
    assert entry["degraded_members"] == {}


@pytest.mark.parametrize("cache", ["a cache that serves", "no cache"])
async def test_an_absent_decorative_member_is_degraded_in_both_modes(tmp_path, cache):
    """DecorationAbsent (claude/static-rule-siblings, D3): the railroad's stop_times.txt
    and NJ Transit's shapes.txt are members their loaders document as legitimately
    absent, so a publication without one is promoted whatever the cache, never refused,
    and the member is named degraded with the gate's words. Over a cache that serves,
    the ordinary failure (present and unreadable) is refused, the case above; absence
    is not that case."""
    dest = tmp_path / "gtfs.zip"
    if cache == "a cache that serves":
        dest.write_bytes(good_archive())

    def decoration(zf):
        static_shared.require_decoration(zf, "stop_times.txt")

    payload = good_archive(drop=("stop_times.txt",))
    await static_shared.staged_fetch(
        "https://example.invalid/gtfs.zip",
        dest,
        accept_all,
        key="k",
        label="test archive",
        download=publisher(payload),
        validate_when_replacing=decoration,
        cache_serves=cache == "a cache that serves",
    )
    assert dest.read_bytes() == payload
    entry = static_shared.archive_status()["k"]
    assert entry["last_download_error"] is None and entry["failed_downloads"] == 0
    assert entry["degraded_members"] == {
        "stop_times.txt": "stop_times.txt is absent from the publication, which is served "
        "without it"
    }


async def test_a_cache_the_always_gate_refuses_is_no_cache_to_keep(tmp_path):
    """The rule asks whether the cache PASSES the always-gate, not whether a file exists:
    an unreadable one is nothing to keep serving, so the publication is promoted over it
    with the degraded member named."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(b"<!doctype html> not a zip")
    payload = good_archive()
    await _fetch_with_geometry(dest, payload, refuse_geometry())
    assert dest.read_bytes() == payload
    assert static_shared.archive_status()["k"]["degraded_members"] == {
        "shapes.txt": GEOMETRY_REASON
    }


def _refusing_the_cache(dest, raised):
    """An always-gate that passes every archive but the cache at `dest`, where it raises
    `raised`: the only call it fails is the two-mode rule's re-check of that cache."""

    def validate(zf):
        if Path(zf.filename) == dest:
            raise raised

    return validate


async def test_a_valid_zip_the_always_gate_refuses_is_no_cache_to_keep(tmp_path):
    """The converse of the unreadable cache above, with the zip itself readable: the rule
    asks the always-gate, not whether the file opens, so a cache it refuses on its bytes
    is promoted over (the branch's review, F5: the old test's cache was not a zip at all,
    so a re-check that only opened the file passed it too)."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive(drop=("routes.txt",)))
    payload = good_archive()
    refusal = static_shared.StaticValidationError("stops.txt yielded no usable parent stations")
    await _fetch_with_geometry(
        dest, payload, refuse_geometry(), validate=_refusing_the_cache(dest, refusal)
    )
    assert dest.read_bytes() == payload
    assert static_shared.archive_status()["k"]["degraded_members"] == {
        "shapes.txt": GEOMETRY_REASON
    }


@pytest.mark.parametrize(
    ("cache", "verdict", "promoted"),
    [("good", False, True), ("unreadable", True, False)],
    ids=["the caller found it unusable", "the caller found it serving"],
)
async def test_the_callers_verdict_decides_the_mode_without_a_second_read(
    tmp_path, cache, verdict, promoted
):
    """WHOSE VERDICT DECIDES (the branch's second review round). A loader has already
    judged the cache at the start of its attempt, and the mode is that judgment: a
    cache it called unusable is no cache to keep, and one it called serving is kept,
    whatever a second read here would say. Each case is set against what such a read
    would answer (a good cache, an unreadable one), and the always-gate counts that it
    was never asked about the cache at all."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive() if cache == "good" else b"<!doctype html> not a zip")
    before = sha(dest)
    asked: list = []

    def validate(zf):
        asked.append(Path(zf.filename))

    payload = good_archive(drop=("routes.txt",))
    attempt = static_shared.staged_fetch(
        "https://example.invalid/gtfs.zip",
        dest,
        validate,
        key="k",
        label="test archive",
        download=publisher(payload),
        validate_when_replacing=refuse_geometry(),
        cache_serves=verdict,
    )
    if promoted:
        await attempt
        assert dest.read_bytes() == payload
        assert static_shared.archive_status()["k"]["degraded_members"] == {
            "shapes.txt": GEOMETRY_REASON
        }
    else:
        with pytest.raises(static_shared.StaticValidationError):
            await attempt
        assert sha(dest) == before
    assert dest not in asked, "the cache was read again"


async def test_without_a_verdict_an_environment_fault_reads_as_no_cache(tmp_path):
    """A caller that passes no verdict gets the check every loader makes,
    cached_archive_is_usable, and that check reads any failure as no cache: so one
    EMFILE while re-reading a cache whose lines draw promotes geometry that draws
    nothing over it. Stations over lines, which is the ruling's order: the second
    review round showed that telling the environment's faults from the bytes' by errno
    kept caches the loader had called unusable, and then nothing served at all. The
    subway passes its verdict and never takes this path."""
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive())
    payload = good_archive(drop=("routes.txt",))
    fault = OSError(errno.EMFILE, "Too many open files")
    await _fetch_with_geometry(
        dest, payload, refuse_geometry(), validate=_refusing_the_cache(dest, fault)
    )
    assert dest.read_bytes() == payload


async def test_degraded_members_describe_the_archive_now_serving(tmp_path):
    """Replaced at every promotion, left alone by a refusal: a cold promotion names the
    member; a later bad publication is refused over that archive (which passes the
    always-gate) and the name stays, because the archive it describes is still serving;
    a clean publication then clears it."""
    dest = tmp_path / "gtfs.zip"
    await _fetch_with_geometry(dest, good_archive(), refuse_geometry())
    assert static_shared.archive_status()["k"]["degraded_members"] == {
        "shapes.txt": GEOMETRY_REASON
    }
    with pytest.raises(static_shared.StaticValidationError):
        await _fetch_with_geometry(dest, good_archive(drop=("routes.txt",)), refuse_geometry())
    assert static_shared.archive_status()["k"]["degraded_members"] == {
        "shapes.txt": GEOMETRY_REASON
    }
    await _fetch_with_geometry(dest, good_archive(drop=("routes.txt",)), accept_all)
    entry = static_shared.archive_status()["k"]
    assert entry["degraded_members"] == {} and entry["last_download_error"] is None


async def test_an_always_gate_failure_is_refused_before_the_decorative_gate_is_asked(tmp_path):
    """The two-mode rule is the decorative gate's only. A publication the always-gate
    refuses is refused with no cache at all (third-audit finding 4's cold start), and the
    decorative gate never runs on it."""
    dest = tmp_path / "gtfs.zip"
    geometry = refuse_geometry()

    def refuse_all(zf):
        raise static_shared.StaticValidationError("stops.txt yielded no usable stops")

    with pytest.raises(static_shared.StaticValidationError):
        await _fetch_with_geometry(dest, good_archive(), geometry, validate=refuse_all)
    assert not dest.exists()
    assert geometry.calls == []
    entry = static_shared.archive_status()["k"]
    assert entry["last_download_error"] == "invalid archive: stops.txt yielded no usable stops"


@pytest.mark.parametrize("shape", sorted(BAD_PUBLICATIONS))
async def test_bad_publication_leaves_the_cache_byte_identical(tmp_path, shape):
    # THE WHOLE POINT OF C5. Pre-C5 the rename happened before any parse, so each
    # of these shapes overwrote a working archive with garbage.
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive())
    before = sha(dest)
    with pytest.raises(static_shared.StaticValidationError):
        await static_shared.staged_fetch(
            "https://example.invalid/gtfs.zip",
            dest,
            # The ferry validator, because it requires all five members and so
            # rejects every shape in the table; the subway's ignores routes.txt.
            ferry_static.validate_ferry_archive,
            key="k",
            label="test archive",
            download=publisher(BAD_PUBLICATIONS[shape]),
        )
    assert sha(dest) == before
    assert not list(tmp_path.glob("*.part"))  # the stage file was cleaned up
    entry = static_shared.archive_status()["k"]
    assert entry["last_promoted_at"] is None
    assert entry["failed_downloads"] == 1
    assert entry["last_download_error"].startswith("invalid archive: ")


async def test_transfer_failure_records_the_type_only_never_the_url(tmp_path):
    # C4's sanitization rule, applied here: an httpx error's str() embeds the
    # request URL, which for some feeds carries a key. Only the type name is
    # published, and the assertion is on the ABSENCE of the URL, not just the
    # presence of the type, so a future f"{exc}" cannot slip through.
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive())
    before = sha(dest)

    async def refuse(url, dest_, deadline_s):
        raise httpx.ConnectError("connection refused to https://secret.invalid/?key=SHHH")

    with pytest.raises(httpx.ConnectError):
        await static_shared.staged_fetch(
            "https://secret.invalid/?key=SHHH",
            dest,
            accept_all,
            key="k",
            label="test archive",
            download=refuse,
        )
    entry = static_shared.archive_status()["k"]
    assert entry["last_download_error"] == "ConnectError"
    assert "SHHH" not in repr(static_shared.archive_status())
    assert sha(dest) == before


async def test_cancellation_is_not_recorded_as_a_publication_failure(tmp_path):
    # Shutdown cancels the warmup task mid-transfer. That is not upstream's fault
    # and must not show up on /api/status as a bad publication.
    dest = tmp_path / "gtfs.zip"

    async def cancelled(url, dest_, deadline_s):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await static_shared.staged_fetch(
            "https://example.invalid/gtfs.zip",
            dest,
            accept_all,
            key="k",
            label="test archive",
            download=cancelled,
        )
    assert static_shared.archive_status() == {}
    assert not list(tmp_path.glob("*.part"))


async def test_orphan_sweep_is_scoped_to_this_stem(tmp_path):
    # All four archives share one directory, and the subway used to sweep a bare
    # "*.part" glob, which could delete a SIBLING loader's in-flight transfer and
    # fail that download for no reason. The sweep clears this stem's orphans only.
    mine = tmp_path / "gtfs_subway.abc.part"
    mine.write_bytes(b"orphan from an earlier hard kill")
    siblings = [tmp_path / "gtfs_ferry.xyz.part", tmp_path / "gtfs_path.xyz.part"]
    for sibling in siblings:
        sibling.write_bytes(b"another loader is streaming into this right now")
    dest = tmp_path / "gtfs_subway.zip"
    await static_shared.staged_fetch(
        "https://example.invalid/gtfs.zip",
        dest,
        accept_all,
        key="k",
        label="test archive",
        download=publisher(good_archive()),
    )
    assert not mine.exists()
    assert all(sibling.exists() for sibling in siblings)


async def test_death_between_validation_and_promotion_leaves_the_cache_intact(
    tmp_path, monkeypatch
):
    # THE CRASH WINDOW. Validation has passed and the rename has not happened.
    # Modelled by making the rename itself fail, which leaves the process in
    # exactly the state a kill would: cache untouched, one stage file on disk.
    # The next attempt must sweep that stage file rather than accumulate them.
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive())
    before = sha(dest)
    real_replace = Path.replace

    def die(self, target):
        raise OSError("process died here")

    monkeypatch.setattr(Path, "replace", die)
    with pytest.raises(OSError):
        await static_shared.staged_fetch(
            "https://example.invalid/gtfs.zip",
            dest,
            accept_all,
            key="k",
            label="test archive",
            download=publisher(good_archive(routes=[{"route_id": "DIFFERENT"}])),
        )
    assert sha(dest) == before  # never half-promoted
    orphans = list(tmp_path.glob("*.part"))
    assert len(orphans) == 1  # the stage file the crash left behind

    monkeypatch.setattr(Path, "replace", real_replace)
    await static_shared.staged_fetch(
        "https://example.invalid/gtfs.zip",
        dest,
        accept_all,
        key="k",
        label="test archive",
        download=publisher(good_archive()),
    )
    assert not list(tmp_path.glob("*.part"))  # swept by the next attempt


# ---------------------------------------------------------------------------
# The four loaders, driven end to end through their own _download_zip
# ---------------------------------------------------------------------------


def _subway_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(static_data, "SUBWAY_GTFS_ZIP", tmp_path / "gtfs_subway.zip")
    return tmp_path / "gtfs_subway.zip"


def _railroad_paths(tmp_path, monkeypatch):
    # ONE system for these shared cases, deliberately. With MNR riding along, its
    # cacheless load would attempt a download in every case below, and _load_one's
    # lenient `except Exception` would swallow the "should not download" assertion
    # a test raises from its injected transfer, making that test pass for the wrong
    # reason. The two-system independence is covered where it belongs, in
    # test_railroad_static.py.
    monkeypatch.setattr(
        railroad_static, "RAILROAD_STATIC_ZIPS", {"LIRR": tmp_path / "gtfs_lirr.zip"}
    )
    monkeypatch.setattr(
        railroad_static, "RAILROAD_STATIC_URLS", {"LIRR": "https://example.invalid/lirr.zip"}
    )
    return tmp_path / "gtfs_lirr.zip"


def _path_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(path_static, "PATH_STATIC_ZIP", tmp_path / "gtfs_path.zip")
    return tmp_path / "gtfs_path.zip"


def _ferry_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(ferry_static, "FERRY_STATIC_ZIP", tmp_path / "gtfs_ferry.zip")
    return tmp_path / "gtfs_ferry.zip"


async def _load_subway():
    return await static_data.load_subway_stops()


async def _load_railroad():
    result = await railroad_static.load_railroad_static()
    return result["LIRR"]


async def _load_path():
    return await path_static.load_path_static()


async def _load_ferry():
    return await ferry_static.load_ferry_static()


class Loader:
    """One static loader's seams, so the shared cases below stay one copy."""

    def __init__(
        self,
        name,
        module,
        key,
        set_path,
        load,
        served,
        validate,
        required,
        raises_on_cold_failure=False,
        warm=None,
        status=None,
        fault=None,
    ):
        self.name = name
        self.module = module
        self.key = key
        self.set_path = set_path
        self.load = load
        self.served = served
        self.validate = validate
        self.required = required
        self.raises_on_cold_failure = raises_on_cold_failure
        # `validate` is the gate the loader's CACHED CHECK runs, which since
        # claude/static-rule-siblings (D2) is its publication gate in every loader.
        # The warmup that drives this loader, the app.state field it flips, and the
        # module function its LOAD reads the cache through and its validator does not
        # (so a fault injected there is met by the load, never by the cached check).
        self.warm = warm
        self.status = status
        self.fault = fault


LOADERS = [
    Loader(
        "subway",
        static_data,
        "subway",
        _subway_paths,
        _load_subway,
        lambda data: "101" in data,
        static_data.validate_subway_archive,
        static_data._REQUIRED_MEMBERS,
        # The subway loader RAISES rather than returning empty: its warmup treats
        # the exception as the failed attempt, where the other three read an empty
        # result the same way.
        raises_on_cold_failure=True,
        warm=warmups._warm_subway_static,
        status="subway_static_status",
        fault="_parse_stops_rows",
    ),
    Loader(
        "railroad",
        railroad_static,
        "railroad_LIRR",
        _railroad_paths,
        _load_railroad,
        lambda data: bool(data) and "101" in data["stops"],
        railroad_static.validate_railroad_publication,
        railroad_static._REQUIRED_MEMBERS,
        warm=warmups._warm_railroad_static,
        status="railroad_static_status",
        fault="_parse_system",
    ),
    Loader(
        "path",
        path_static,
        "path",
        _path_paths,
        _load_path,
        lambda data: "101" in data.get("stops", {}),
        path_static.validate_path_publication,
        path_static._REQUIRED_MEMBERS,
        warm=warmups._warm_path_static,
        status="path_static_status",
        fault="_parse_zip",
    ),
    Loader(
        "ferry",
        ferry_static,
        "ferry",
        _ferry_paths,
        _load_ferry,
        lambda data: "101" in data.get("stops", {}),
        ferry_static.validate_ferry_publication,
        ferry_static._REQUIRED_MEMBERS,
        warm=warmups._warm_ferry_static,
        status="ferry_static_status",
        fault="_parse_zip",
    ),
]
LOADER_IDS = [loader.name for loader in LOADERS]


def _njt_paths(tmp_path, monkeypatch):
    # Configured, so the loader runs at all (an unconfigured one raises before the
    # cache), and every request NJ Transit's transport is asked for fails the test:
    # these cases replace the loader's staged_fetch, so the token door is never reached.
    configure_njt(monkeypatch)
    monkeypatch.setattr(njt_auth, "TOKEN_CACHE", njt_auth.TokenCache())

    async def no_network(url, form, timeout_s):
        raise AssertionError(f"no NJ Transit request is allowed here, but {url!r} was sent")

    monkeypatch.setattr(njt_auth, "_httpx_post", no_network)
    monkeypatch.setattr(njt_static, "NJT_STATIC_ZIP", tmp_path / "gtfs_njt.zip")
    return tmp_path / "gtfs_njt.zip"


async def _load_njt():
    return await njt_static.load_njt_static()


# NJ TRANSIT ALONGSIDE THE FOUR, for the cases that are one rule across every loader
# (claude/static-rule-siblings). It is not in LOADERS because the cases above predate
# it there and it has its own copies of them in test_njt_static.py; good_archive carries
# its two extra members so the same archive serves all five.
NJT_LOADER = Loader(
    "njt",
    njt_static,
    "njt",
    _njt_paths,
    _load_njt,
    # Flat stops: the location_type=1 parent is not a boardable stop, its platform is.
    lambda data: "101N" in data.get("stops", {}),
    njt_static.validate_njt_publication,
    njt_static._REQUIRED_MEMBERS,
    warm=warmups._warm_njt_static,
    status="njt_static_status",
    fault="_parse_zip",
)
FIVE_LOADERS = [*LOADERS, NJT_LOADER]
FIVE_IDS = [loader.name for loader in FIVE_LOADERS]

# A publication shape is a rejection only for the loaders that consume the table it
# damages. headers-only-routes is a rejection for the railroad, PATH and ferry, all of
# which read route identity, and NOT for the subway, which never opens routes.txt (it
# draws with its own palette). Pairing them explicitly keeps every case meaningful
# instead of asserting a rejection that should not happen.
ROUTES_CONSUMERS = {"railroad", "path", "ferry"}


def _shapes_for(loader):
    return [
        shape
        for shape in sorted(BAD_PUBLICATIONS)
        if shape != "headers-only-routes" or loader.name in ROUTES_CONSUMERS
    ]


LOADER_SHAPES = [(loader, shape) for loader in LOADERS for shape in _shapes_for(loader)]
LOADER_SHAPE_IDS = [f"{loader.name}-{shape}" for loader, shape in LOADER_SHAPES]

# Every loader parses deeper tables than its light gate used to, and unlinks a cache it
# cannot parse. The subway joined this list with A02: it read trips.txt and
# stop_times.txt for the routes index all along, without a gate that parsed them or a
# residual arm, which is how an undecodable trips.txt was promoted and then wedged
# every attempt. The deep-parse regression is about that arm.
DEEP_PARSE_LOADERS = LOADERS
DEEP_PARSE_IDS = [loader.name for loader in DEEP_PARSE_LOADERS]


def _patch_download(monkeypatch, module, payload, calls=None):
    """Replace one loader module's transfer, keeping staged_fetch and the validator real.

    Patching the TRANSFER rather than _download_zip is deliberate: the staging,
    the validation and the promotion are the code under test, so only the socket
    is replaced. `calls` records the DESTINATION of each download, so a two-system
    loader can be asserted per system.
    """
    real = static_shared.staged_fetch

    async def fetch(url, dest, validate, **kwargs):
        if calls is not None:
            calls.append(dest)
        kwargs.pop("download", None)  # NJ Transit's token door, replaced like the rest
        await real(url, dest, validate, **kwargs, download=publisher(payload))

    monkeypatch.setattr(module, "staged_fetch", fetch)


def age(path: Path, days: float) -> None:
    import os
    import time

    old = time.time() - days * 86400
    os.utime(path, (old, old))


@pytest.mark.parametrize(("loader", "shape"), LOADER_SHAPES, ids=LOADER_SHAPE_IDS)
async def test_loader_serves_the_cache_when_a_publication_is_rejected(
    tmp_path, monkeypatch, loader, shape
):
    # READY PAST MAX_AGE_DAYS, and reachable ONLY this way: the cache is aged past
    # the refresh threshold so a download is genuinely attempted, and it is the
    # VALIDATION of that download that fails. Age alone never gets here.
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    age(cache, days=static_data.MAX_AGE_DAYS + 10)
    before = sha(cache)
    _patch_download(monkeypatch, loader.module, BAD_PUBLICATIONS[shape])

    data = await loader.load()

    assert loader.served(data)  # still serving, from the archive it already had
    assert sha(cache) == before  # BYTE-IDENTICAL: the rejected bytes never landed
    entry = static_shared.archive_status()[loader.key]
    assert entry["last_download_error"] is not None
    assert entry["last_promoted_at"] is None
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(("loader", "shape"), LOADER_SHAPES, ids=LOADER_SHAPE_IDS)
async def test_cold_start_with_a_bad_publication_never_reaches_ready(
    tmp_path, monkeypatch, loader, shape
):
    # The cold-start distinction from item 6: no valid cache plus a failing
    # download is failed-and-retrying. Ready always means "serving validated data".
    cache = loader.set_path(tmp_path, monkeypatch)
    assert not cache.exists()
    _patch_download(monkeypatch, loader.module, BAD_PUBLICATIONS[shape])

    if loader.raises_on_cold_failure:
        with pytest.raises(static_shared.StaticValidationError):
            await loader.load()
    else:
        assert not loader.served(await loader.load())


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
async def test_a_good_publication_is_promoted_over_a_stale_cache(tmp_path, monkeypatch, loader):
    # The healthy path stays byte-preserving: a good download ends with the new
    # archive at the same path it always used.
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(good_archive(routes=[{"route_id": "OLD", "route_long_name": "Old"}]))
    age(cache, days=static_data.MAX_AGE_DAYS + 10)
    published = good_archive()
    _patch_download(monkeypatch, loader.module, published)

    data = await loader.load()

    assert loader.served(data)
    assert cache.read_bytes() == published
    assert static_shared.archive_status()[loader.key]["last_promoted_at"] is not None


@pytest.mark.parametrize(("loader", "shape"), LOADER_SHAPES, ids=LOADER_SHAPE_IDS)
async def test_an_unservable_cache_is_treated_as_absent_and_refetched(
    tmp_path, monkeypatch, loader, shape
):
    # The cached-archive guard. Each bad shape is planted as the CACHE with a
    # FRESH mtime, which pre-C5 meant "no download needed" and wedged the loader
    # on those bytes until MAX_AGE_DAYS. It must refetch exactly once instead.
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(BAD_PUBLICATIONS[shape]())
    calls = []
    _patch_download(monkeypatch, loader.module, good_archive(), calls=calls)

    data = await loader.load()

    assert loader.served(data)
    assert calls.count(cache) == 1  # exactly one recovery download, not a loop


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
async def test_a_valid_fresh_cache_is_served_without_any_download(tmp_path, monkeypatch, loader):
    # The other side of the guard: validity is an ADDITIONAL condition on
    # freshness, never a reason to re-download something already good.
    #
    # The absence of a download is proven by RECORDING calls, not by raising from
    # the injected transfer. Raising is what this test did first, and it was
    # vacuous: every loader catches a download failure and falls back to the cache,
    # so the assertion was swallowed and the test stayed green against a loader
    # mutated to download unconditionally.
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    calls = []
    _patch_download(monkeypatch, loader.module, good_archive(), calls=calls)

    assert loader.served(await loader.load())
    assert calls == []
    assert static_shared.archive_status() == {}


# THE FRESHNESS FLOOR (claude/static-rule-siblings, D4). Whole seconds, so the mtime
# set and the clock read compare exactly.
_MTIME = 1_800_000_000
_MAX_AGE_S = 30 * 86400


@pytest.mark.parametrize(
    ("age_s", "fresh"),
    [
        (-30 * 86400, False),
        (-1, False),
        (0, True),
        (_MAX_AGE_S - 1, True),
        (_MAX_AGE_S, False),
    ],
    ids=["a month ahead", "a second ahead", "written now", "a second inside", "at the max"],
)
def test_freshness_has_a_floor_at_zero_age_and_a_ceiling_at_the_max(
    tmp_path, monkeypatch, age_s, fresh
):
    """0 <= age < max_age_days, with age read from time.time at the call. Before D4 a
    negative age passed `age < max`, so a cache dated ahead of the clock was fresh until
    the clock caught up with it, a month here."""
    cache = tmp_path / "gtfs.zip"
    cache.write_bytes(b"")
    os.utime(cache, (_MTIME, _MTIME))
    monkeypatch.setattr(time, "time", lambda: _MTIME + age_s)

    assert static_shared.cache_is_fresh(cache, usable=True, max_age_days=30) is fresh


def test_a_cache_the_check_refused_is_never_fresh_and_never_stated(tmp_path):
    # `usable` short-circuits: a path that is not there would raise on its stat.
    absent = tmp_path / "gtfs.zip"
    assert static_shared.cache_is_fresh(absent, usable=False, max_age_days=30) is False


@pytest.mark.parametrize(
    ("offset_s", "downloads"),
    [(86400, 1), (60, 1), (-86400, 0)],
    ids=["a day ahead of the clock", "a minute ahead", "a day old"],
)
@pytest.mark.parametrize("loader", FIVE_LOADERS, ids=FIVE_IDS)
async def test_a_cache_dated_ahead_of_the_clock_is_stale_in_every_loader(
    tmp_path, monkeypatch, loader, offset_s, downloads
):
    """Every loader asks static_shared.cache_is_fresh, so the floor holds in each: a
    valid cache dated ahead of the clock (the host clock stepped back after the
    promotion) is stale, the publication is downloaded once and promoted, and it lands
    dated now, so the next load downloads nothing. A cache a day old is fresh and is
    served without a download, as before."""
    cache = loader.set_path(tmp_path, monkeypatch)
    old = good_archive(routes=[{"route_id": "OLD", "route_long_name": "Old"}])
    cache.write_bytes(old)
    dated = time.time() + offset_s
    os.utime(cache, (dated, dated))
    published = good_archive()
    calls: list[Path] = []
    _patch_download(monkeypatch, loader.module, published, calls=calls)

    assert loader.served(await loader.load())
    assert calls == [cache] * downloads
    assert cache.read_bytes() == (published if downloads else old)
    if downloads:
        assert cache.stat().st_mtime <= time.time()
        calls.clear()
        assert loader.served(await loader.load())
        assert calls == [], "the promoted archive is dated now, so it is fresh"


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
async def test_every_loaders_cached_check_validates_off_the_serving_loop(
    tmp_path, monkeypatch, loader
):
    """D5 AT EVERY CALL SITE (audit 2026-10-12, A02 and A09). Each loader asks
    static_shared.cached_archive_is_usable whether its cache can be served, and that
    runs the loader's own validator in a worker thread so the loop the pollers and
    /healthz share keeps running. The subway's heartbeat test in test_static_data.py
    pins what that buys; this pins WHERE it happens for every loader, by thread rather
    than by timing: with a valid, fresh cache and no download, the validator must run
    on a thread other than the one running this test's event loop. A loader put back
    on the synchronous check runs it here, on the loop's thread, and fails.
    NJ Transit's loader has the same test in test_njt_static.py."""
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    calls = []
    _patch_download(monkeypatch, loader.module, good_archive(), calls=calls)
    threads = []
    real = loader.validate

    def recording(zf):
        threads.append(threading.get_ident())
        real(zf)

    monkeypatch.setattr(loader.module, real.__name__, recording)

    assert loader.served(await loader.load())
    assert calls == []
    assert threads, "the cached check must have run the loader's validator"
    assert threading.get_ident() not in threads


async def _drive_to_ready(app, field):
    """Run until `field` reads ready, recording each status it passes through."""
    seen: list = []
    for _ in range(2000):
        status = getattr(app.state, field)
        if not seen or seen[-1] != status:
            seen.append(status)
        if status == "ready":
            break
        await asyncio.sleep(0.001)
    return seen


# Every (loader, member) whose load REQUIRES the member, so a rotted cache there used to
# be "usable" at the light check and then fail every load: trips.txt and stop_times.txt,
# the two the light checks only checked for presence (the subway's since A02, NJ
# Transit's stop_times.txt the ledger's own aside). Not the railroad's stop_times.txt,
# which is DECORATIVE (D3): a cache whose index member rots is served without the index,
# test_railroad_static.py's case.
DEEP_REQUIRED = [
    (loader, member)
    for loader in FIVE_LOADERS
    for member in ("trips.txt", "stop_times.txt")
    if (loader.name, member) != ("railroad", "stop_times.txt")
]
DEEP_REQUIRED_IDS = [f"{loader.name}-{member}" for loader, member in DEEP_REQUIRED]


@pytest.mark.parametrize(("loader", "member"), DEEP_REQUIRED, ids=DEEP_REQUIRED_IDS)
async def test_a_cache_unreadable_deep_in_a_required_member_is_refused_at_the_check(
    tmp_path, monkeypatch, loader, member
):
    """THE CACHED CHECK IS THE PUBLICATION GATE (claude/static-rule-siblings, D2). A cache
    with one 0xFF byte far into a member the load requires, past the first chunk a text
    reader decodes and past any read that stops early, is refused at the check, named by
    member in the reader's own words, treated as absent and re-downloaded: one download,
    the new archive promoted and served. Under the light check it was called usable, and
    the load raised at the byte on every attempt until MAX_AGE_DAYS (or, after D1, was
    discarded by the residual arm, which costs the attempt and leaves the group failed)."""
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(undecodable_deep_in(good_archive(), member))
    with pytest.raises(static_shared.StaticValidationError) as refused:
        static_shared.validate_archive(cache, loader.validate)
    assert refused.value.member == member
    assert str(refused.value).startswith(f"{member} is not readable as CSV ('utf-8' codec")
    published = good_archive()
    calls: list = []
    _patch_download(monkeypatch, loader.module, published, calls=calls)

    data = await loader.load()

    assert loader.served(data)
    assert calls == [cache], "refused at the check: one download, nothing failed first"
    assert cache.read_bytes() == published


@pytest.mark.parametrize("loader", FIVE_LOADERS, ids=FIVE_IDS)
async def test_an_environment_error_at_load_keeps_every_loaders_cache(
    tmp_path, monkeypatch, loader
):
    """THE RESIDUAL ARM IS THE ARCHIVE'S, IN ALL FIVE LOADERS (the operator's ruling on
    claude/a02-publication-validation, made one tuple on claude/static-rule-siblings, D1).
    Out of file descriptors while the LOAD reads a valid, fresh cache says nothing about
    its bytes, so through each loader's real warmup the attempt fails with the cache kept,
    and the next attempt reads the same cache and reaches ready with no download. A
    discard there turned a transient fault into a failed group whenever upstream was down
    at the same moment. The fault is injected where the load reads and the cached check
    does not, so the check passes and the load meets it.

    The railroad runs here as one system (see _railroad_paths), where failing the attempt
    and returning None for it look the same; that an environment fault fails the attempt
    for BOTH systems, _load_one's contract on purpose, is
    test_railroad_static.py::test_an_environment_fault_in_one_system_fails_the_attempt_for_both."""
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    cached = cache.read_bytes()
    calls: list = []
    _patch_download(monkeypatch, loader.module, good_archive(), calls=calls)
    real = getattr(loader.module, loader.fault)
    faults = {"left": 1}

    def failing_once(*args):
        if faults["left"]:
            faults["left"] -= 1
            raise OSError(errno.EMFILE, "Too many open files")
        return real(*args)

    monkeypatch.setattr(loader.module, loader.fault, failing_once)
    app = types.SimpleNamespace(state=types.SimpleNamespace(**{loader.status: "loading"}))
    task = asyncio.create_task(loader.warm(app))
    try:
        seen = await _drive_to_ready(app, loader.status)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert seen == ["loading", "failed", "ready"]
    assert faults["left"] == 0
    assert cache.read_bytes() == cached, "the cache is kept"
    assert calls == [], "and the next attempt served it without a download"


# ---------------------------------------------------------------------------
# Third-audit finding 4, by name
# ---------------------------------------------------------------------------


async def test_finding_4_headers_only_stops_never_reaches_ready(tmp_path, monkeypatch):
    """The auditor's reproduction: a stops.txt with headers and no data rows.

    Pre-C5 it parsed to {}, the warmup promoted the group to "ready", every
    station vanished from the map and nothing ever retried, because structurally
    the archive was fine. The assertion that matters is the NEGATIVE one: the
    group is never "ready" while that publication is the only one on offer, and it
    reaches ready the moment a real one arrives.
    """
    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    cache = _subway_paths(tmp_path, monkeypatch)
    monkeypatch.setattr(static_data, "SUBWAY_GTFS_URL", "https://example.invalid/gtfs_subway.zip")
    published = {"bytes": good_archive(stops=[])}  # headers only, no data rows
    _patch_download(monkeypatch, static_data, lambda: published["bytes"])
    # The lighter subway loaders read the same cache path; they return empty on a
    # missing zip by design, so no patching is needed to keep this hermetic.

    app = types.SimpleNamespace(state=types.SimpleNamespace(subway_static_status="loading"))
    task = asyncio.create_task(warmups._warm_subway_static(app))
    try:
        for _ in range(200):
            if app.state.subway_static_status == "failed":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "failed"
        assert not cache.exists()  # the empty publication was never promoted
        # It is failed-and-RETRYING, not failed-and-stopped: a corrected upstream
        # is picked up without a redeploy.
        published["bytes"] = good_archive()
        for _ in range(400):
            if app.state.subway_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "ready"
        assert "101" in app.state.subway_stops
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------------------
# The operator surface
# ---------------------------------------------------------------------------


async def test_status_exposes_per_archive_download_state(tmp_path, monkeypatch):
    # /api/status must make the deliberate ready-but-stale state legible: which
    # archive, when it was last promoted, why the last publication was rejected.
    cache = _ferry_paths(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    age(cache, days=ferry_static.MAX_AGE_DAYS + 10)
    _patch_download(monkeypatch, ferry_static, BAD_PUBLICATIONS["headers-only-stops"])
    await ferry_static.load_ferry_static()

    app = app_module.app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        body = (await client.get("/api/status")).json()

    archives = body["static_archives"]
    assert archives["ferry"]["last_promoted_at"] is None
    assert archives["ferry"]["failed_downloads"] == 1
    # Sanitized: our own shape-naming message, never raw upstream text.
    assert archives["ferry"]["last_download_error"] == (
        "invalid archive: stops.txt yielded no usable docks"
    )


async def test_status_static_archives_is_empty_before_any_download():
    # A cold boot with a warm cache downloads nothing, so the map is empty rather
    # than carrying misleading nulls for archives nothing has touched.
    app = app_module.app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        body = (await client.get("/api/status")).json()
    assert body["static_archives"] == {}


# The member set each validator enforces, WRITTEN OUT HERE rather than read from
# the module under test. That distinction is the whole value of this block: an
# earlier version derived the parametrization from _REQUIRED_MEMBERS, and deleting
# stop_times.txt from the ferry tuple left the suite fully green, because the case
# for that member simply stopped being generated. A test that asks the code what
# it should do cannot notice the code doing less.
EXPECTED_REQUIRED = {
    # THE RULE: require a member when its absence is a loss a rider would SEE, so
    # keeping the last-known-good beats promoting the reduced archive. At cold start
    # with no cache, a required member missing means the system is absent from the
    # map entirely, so the set is not free and is not padded.
    #
    # THE SUBWAY WANTS trips.txt AND stop_times.txt NOW, and this note used to say the
    # opposite: that both fed only the H5 routes-per-station popup enrichment, which
    # already degraded to an empty index. Three consumers read that index today and all
    # three are rider-visible (MR2's transfer ring, MR2's hub label class, F11's station
    # alerts join), so an archive missing either file is a reduced archive whose
    # promotion costs a rider three things. MR2's review finding F1 is the
    # reproduction: with stop_times.txt absent the index came back empty, every station
    # drew as a local, no name rendered at zooms 12 or 13, and subway_static_status
    # stayed "ready" with /healthz green. backend/static_data.py's _REQUIRED_MEMBERS
    # comment carries the argument; this is the declaration it is held to.
    # PATH and ferry have wanted stop_times.txt all along, where it drives advance
    # matching (13d) and the dock/route alert join (H5) rather than an enrichment.
    #
    # AND transfers.txt, BY THE SAME RULE (claude/subway-hub-definition). A hub is a
    # station complex now, and the complexes are transfers.txt's cross-stop rows. Three
    # consumers read them and all three are rider-visible (the transfer ring, the hub
    # label class, MR5's kicker listing the complex's routes), so an archive without it
    # draws Times Square as five lone stops with no ring and no name at zoom 12 or 13,
    # while the status says "ready".
    #
    # AND shapes.txt LEFT IT (claude/a02-publication-validation, the operator's ruling:
    # geometry stays decorative). It has its own gate, static_data.validate_subway_geometry,
    # which refuses only when a cache that draws is there to keep and otherwise promotes
    # with shapes.txt named as a degraded member, so requiring it here would take every
    # station off the map at a cold start to protect the lines.
    "subway": ("stop_times.txt", "stops.txt", "transfers.txt", "trips.txt"),
    "railroad": ("routes.txt", "shapes.txt", "stops.txt", "trips.txt"),
    "path": ("routes.txt", "shapes.txt", "stop_times.txt", "stops.txt", "trips.txt"),
    "ferry": ("routes.txt", "shapes.txt", "stop_times.txt", "stops.txt", "trips.txt"),
}

# Every (loader, required member) pair, so a shrunk requirement list fails by name
# rather than silently. An explicit product, because a loop inside one test would
# stop at the first member and leave the rest unpinned.
REQUIRED_PAIRS = [
    (loader, member) for loader in LOADERS for member in EXPECTED_REQUIRED[loader.name]
]
REQUIRED_IDS = [f"{loader.name}-{member}" for loader, member in REQUIRED_PAIRS]


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
def test_required_member_set_is_exactly_what_is_declared(loader):
    # Catches a member being ADDED as well as removed: a widened requirement can
    # take a system down at cold start against a publication that dropped it, so
    # the set is a decision worth failing on rather than absorbing.
    assert tuple(sorted(loader.required)) == EXPECTED_REQUIRED[loader.name]


def _write(tmp_path: Path, payload: bytes) -> Path:
    """Land bytes on disk, since validate_archive opens a path (like the real callers)."""
    archive = tmp_path / "candidate.zip"
    archive.write_bytes(payload)
    return archive


@pytest.mark.parametrize(("loader", "member"), REQUIRED_PAIRS, ids=REQUIRED_IDS)
def test_validator_rejects_an_archive_missing_a_required_member(tmp_path, loader, member):
    # Each loader's required set, pinned member by member. Without this, dropping
    # a member from a _REQUIRED_MEMBERS tuple stays green: the load-level tests all
    # publish complete archives, and the parsers deliberately tolerate the optional
    # members at read time, so nothing else notices the gate relaxing.
    with pytest.raises(static_shared.StaticValidationError) as raised:
        static_shared.validate_archive(
            _write(tmp_path, good_archive(drop=(member,))), loader.validate
        )
    assert member in str(raised.value)


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
def test_validator_accepts_a_complete_archive(tmp_path, loader):
    # The converse, so the test above cannot pass by rejecting everything.
    static_shared.validate_archive(_write(tmp_path, good_archive()), loader.validate)


# ---------------------------------------------------------------------------
# Regressions from the adversarial review
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loader", DEEP_PARSE_LOADERS, ids=DEEP_PARSE_IDS)
async def test_a_publication_that_fails_the_deep_parse_never_replaces_the_cache(
    tmp_path, monkeypatch, loader
):
    """C5's own bug, found inside C5 by the adversarial review.

    An archive with clean stops.txt and routes.txt but an undecodable byte in
    trips.txt passed the light validator, was renamed over the last-known-good, and
    was then deleted by the loader's residual arm. One bad publication destroyed
    both archives. staged_fetch runs the publication validator now, which parses
    everything the load parses before the rename.

    THE SUBWAY IS IN THIS TEST NOW, and this docstring used to say why it could not
    be: that its load read only stops.txt, so an archive with a damaged trips.txt was
    "simply a VALID subway archive". Its load had read trips.txt and stop_times.txt
    for the routes-per-station index since H5, and audit 2026-10-12's A02 found that
    exact archive promoted and then failing every warmup attempt for a month. Its
    validator parses every member its load reads now, so the archive is refused
    before the rename, which is what this test asks of every loader.
    """
    cache = loader.set_path(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    age(cache, days=static_data.MAX_AGE_DAYS + 10)
    before = sha(cache)
    _patch_download(monkeypatch, loader.module, undecodable_deep_member())

    data = await loader.load()

    assert loader.served(data)  # still serving the archive it already had
    assert cache.exists(), "the cache was deleted by a bad publication"
    assert sha(cache) == before


async def test_a_corrupt_cache_does_not_block_its_own_repair(tmp_path, monkeypatch):
    """A cached archive whose deflate stream is damaged must force a re-download.

    It used to raise zlib.error straight out of cached_archive_is_valid, which the
    loaders call BEFORE the freshness test, so no download was ever attempted and
    the retry loop could never heal. The pre-C5 code recovered from this by
    accident (it downloaded first and parsed second), so it was a regression as
    well as a wedge.
    """
    cache = _ferry_paths(tmp_path, monkeypatch)
    cache.write_bytes(corrupt_deflate_stream())
    calls = []
    _patch_download(monkeypatch, ferry_static, good_archive(), calls=calls)

    data = await ferry_static.load_ferry_static()

    assert "101" in data.get("stops", {})
    assert len(calls) == 1  # the repair actually ran


def test_every_unreadable_archive_becomes_one_failure_type(tmp_path):
    # The boundary is broad on purpose: an enumerated catch list was wrong twice
    # (BadZipFile alone, then BadZipFile plus NotImplementedError, still missing
    # zlib.error). Nothing below StaticValidationError may escape validate_archive.
    def explode(_zf):
        raise LookupError("a class nobody enumerated")

    with pytest.raises(static_shared.StaticValidationError) as raised:
        static_shared.validate_archive(_write(tmp_path, good_archive()), explode)
    assert "LookupError" in str(raised.value)  # diagnosable from /api/status alone


async def test_subway_rejects_a_stops_table_with_no_parent_stations(tmp_path, monkeypatch):
    """Finding 4's symptom reached through the other subway table.

    stops.txt yields the platform ids that place trains; the clickable station
    markers come from the location_type=1 PARENT rows only. A publication of
    nothing but platform rows once passed the gate and reached ready with every
    station marker gone, which is the exact symptom the finding is about.
    """
    platforms_only = [dict(row, location_type="0") for row in _STOP_ROWS]
    cache = _subway_paths(tmp_path, monkeypatch)
    cache.write_bytes(good_archive())
    age(cache, days=static_data.MAX_AGE_DAYS + 10)
    before = sha(cache)
    _patch_download(monkeypatch, static_data, good_archive(stops=platforms_only))

    assert "101" in await static_data.load_subway_stops()  # the old archive, still serving
    assert sha(cache) == before
    assert "parent stations" in static_shared.archive_status()["subway"]["last_download_error"]


async def test_a_failed_promotion_is_recorded_like_any_other_failure(tmp_path, monkeypatch):
    # Item 5's "the failure is not silent" has to hold for a rename that cannot
    # complete, too. The rename sits outside the download/validate try block (so a
    # process death there leaves a sweepable stage file), which meant an OSError
    # from it skipped _record entirely.
    dest = tmp_path / "gtfs.zip"
    dest.write_bytes(good_archive())

    def die(self, target):
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "replace", die)
    with pytest.raises(OSError):
        await static_shared.staged_fetch(
            "https://example.invalid/gtfs.zip",
            dest,
            accept_all,
            key="k",
            label="test archive",
            download=publisher(good_archive()),
        )
    entry = static_shared.archive_status()["k"]
    assert entry["failed_downloads"] == 1
    assert entry["last_download_error"] == "OSError"
    assert entry["last_promoted_at"] is None
