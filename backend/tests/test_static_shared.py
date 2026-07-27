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
import hashlib
import io
import types
import zipfile
from pathlib import Path

import httpx
import pytest

import ferry_static
import main as app_module
import path_static
import railroad_static
import static_data
import static_shared
import warmups

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
_TRIP_ROWS = [
    {"route_id": "R1", "service_id": "wk", "trip_id": "t1", "direction_id": "0", "shape_id": "s1"}
]
_SHAPE_ROWS = [
    {
        "shape_id": "s1",
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


def good_archive(stops=_STOP_ROWS, drop=(), routes=_ROUTE_ROWS) -> bytes:
    """A zip every one of the four validators accepts. `drop` removes members."""
    members = {
        "stops.txt": _csv(_STOPS_COLS, stops),
        "routes.txt": _csv(_ROUTES_COLS, routes),
        "trips.txt": _csv(_TRIPS_COLS, _TRIP_ROWS),
        "shapes.txt": _csv(_SHAPES_COLS, _SHAPE_ROWS),
        "stop_times.txt": _csv(_STOP_TIMES_COLS, _STOP_TIME_ROWS),
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


BAD_PUBLICATIONS = {
    "truncated-zip": lambda: good_archive()[: len(good_archive()) // 2],
    "html-error-page": lambda: b"<!doctype html><html><body>503 Service Unavailable</body></html>",
    "missing-member": lambda: good_archive(drop=("stops.txt",)),
    "headers-only-stops": lambda: good_archive(stops=[]),
    "unsupported-compression": unsupported_compression,
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
            static_data.validate_subway_archive,
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
    ),
    Loader(
        "railroad",
        railroad_static,
        "railroad_LIRR",
        _railroad_paths,
        _load_railroad,
        lambda data: bool(data) and "101" in data["stops"],
        railroad_static.validate_railroad_archive,
        railroad_static._REQUIRED_MEMBERS,
    ),
    Loader(
        "path",
        path_static,
        "path",
        _path_paths,
        _load_path,
        lambda data: "101" in data.get("stops", {}),
        path_static.validate_path_archive,
        path_static._REQUIRED_MEMBERS,
    ),
    Loader(
        "ferry",
        ferry_static,
        "ferry",
        _ferry_paths,
        _load_ferry,
        lambda data: "101" in data.get("stops", {}),
        ferry_static.validate_ferry_archive,
        ferry_static._REQUIRED_MEMBERS,
    ),
]
LOADER_IDS = [loader.name for loader in LOADERS]


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
        await real(url, dest, validate, **kwargs, download=publisher(payload))

    monkeypatch.setattr(module, "staged_fetch", fetch)


def age(path: Path, days: float) -> None:
    import os
    import time

    old = time.time() - days * 86400
    os.utime(path, (old, old))


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
@pytest.mark.parametrize("shape", sorted(BAD_PUBLICATIONS))
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


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
@pytest.mark.parametrize("shape", sorted(BAD_PUBLICATIONS))
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


@pytest.mark.parametrize("loader", LOADERS, ids=LOADER_IDS)
@pytest.mark.parametrize("shape", sorted(BAD_PUBLICATIONS))
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
    # No stop_times.txt: the subway and the railroads read it only for the
    # routes-per-station popup enrichment, which already degrades to an empty
    # index, so requiring it would turn a small degradation into a whole system
    # missing from the map. PATH walks it for 13d advance matching and ferry for
    # the H5 dock/route join, where its absence is a silent wrong answer.
    "subway": ("shapes.txt", "stops.txt", "trips.txt"),
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
