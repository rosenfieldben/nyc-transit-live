"""F08 (P2), "Bad subway geometry can replace known-good geometry": reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, verbatim):

    "Validation requires `shapes.txt` to exist but does not parse it. Starting from
     an archive yielding one route, a replacement with invalid UTF-8 only in
     `shapes.txt` passed the actual staged-publication validator and overwrote the
     good archive. Route count then fell to zero. Archive status reported
     successful promotion, no download error and zero failed downloads."

    Remedy (the symmetric case this script also checks): "Test empty and
    unreadable geometry independently of stop validation."

    Evidence links: backend/static_data.py L61 (the publication validator) and
    backend/static_data.py L251 (the route loader).

WHAT THIS SCRIPT MEASURES, AND HOW.

  PRODUCTION CODE, unmodified and called directly:
    static_data.load_subway_stops      the loader the subway warmup awaits
    static_data._download_zip          its staged publication call
    static_shared.staged_fetch         sweep, stage, validate, promote
    static_shared.validate_archive     the zip-open boundary
    static_data.validate_subway_archive the publication validator itself
    static_shared.require_members / require_parsed  its two gates
    static_data.load_subway_route_shapes the route-line loader the warmup calls
                                       on the next line (backend/warmups.py L174)
    static_shared.archive_status       the operator surface /api/status reads

  The staged path is driven end to end, not called in isolation: every archive in
  this script arrives at the cache by way of load_subway_stops deciding the cache
  is stale, calling _download_zip, and staged_fetch validating and promoting it.

  INJECTED, and this is the whole of it:
    1. DATA_DIR is pointed at a fresh temp directory before static_data imports,
       through the documented env_seams C6 seam, so the real repo cache is never
       read or written.
    2. SUBWAY_GTFS_URL is pointed at http://127.0.0.1:9 (the discard port, nothing
       listens) so a transfer that somehow escaped the injection below would fail
       instantly rather than reach a real host.
    3. static_shared._stream_to_file, the socket transfer and the ONLY networking
       in the pipeline, is replaced by a fake fetcher that copies a local file into
       the stage path. staged_fetch resolves that name at call time, which is the
       seam its own docstring names ("`download` is injectable so tests can publish
       bytes without a socket"). Nothing else about the pipeline is touched.
    4. Four archives, built here: a GOOD one, one whose shapes.txt holds a lone
       0xFF byte (invalid UTF-8) with a byte-identical stops.txt, one whose
       shapes.txt is a well formed header with zero data rows, and one with no
       shapes.txt at all (the control).
    5. Between publications the cache's mtime is set 40 days back, past
       static_data.MAX_AGE_DAYS, so the loader decides to re-download. That is a
       clock nudge on a file this script wrote, not a dependence on today's date.

  MEASURED: routes before, routes after, the validator's verdict on each staged
  archive, the three archive-status fields the audit names (promotion result,
  download error, failed download count), and whether the cache bytes changed.

NO NJ TRANSIT ANYTHING. No NJT module is imported, no NJT function is called, and
the NJT credential variables backend/.env may carry are removed from the
environment after the imports. Nothing here can reach raildata.njtransit.com or
spend a mint. Nothing here opens a socket at all.

Hermetic and deterministic: no network, no live feed, no committed fixture needed
(the subway static GTFS is a 40 MB download, not a committed fixture, so the
archives are built here), and no dependence on today's date.

RUN, from the repository root:
  .venv/bin/python docs/reviews/audit-2026-09-05/f08_bad_geometry_replaces_good.py

Exits 0 while F08 still behaves as recorded, non-zero (with the failed assertion
named) if the code has changed underneath the audit record.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import hashlib
import io
import logging
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_BACKEND = _REPO / "backend"

# The cache root and the upstream URL must be set BEFORE static_data imports:
# both are read once, at module import, through env_seams.
_TMP = Path(tempfile.mkdtemp(prefix="f08-audit-2026-09-05-"))
DEAD_URL = "http://127.0.0.1:9/gtfs_subway.zip"  # discard port; nothing listens
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["SUBWAY_GTFS_URL"] = DEAD_URL

# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. The pop below this used to
# be the whole scrub and it was not one: env_seams calls load_dotenv when it is
# imported, which refills any credential the pop removed. The addresses set here are
# what make this process unable to reach NJ Transit at all, credentials or not. See
# _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(_BACKEND))

import static_data  # noqa: E402
import static_shared  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


# Belt and braces: this script imports no NJT module, but backend/.env is loaded
# by env_seams at import time, so drop anything that could authenticate.
# BLANKED, NOT POPPED. A pop deletes the key, and load_dotenv fills deleted keys:
# this backend loads the .env twice (env_seams and feeds.shared), so a pop here was
# undone by whichever import came next. See _hermetic.blank.
_hermetic.blank("NJT_USERNAME", "NJT_PASSWORD", "NJT_API_KEY", "NJT_TOKEN")

FAILURES: list[str] = []


def check(condition: bool, description: str) -> bool:
    """Record a recorded-behavior check. Returns the condition."""
    if not condition:
        FAILURES.append(description)
    return condition


# ---------------------------------------------------------------------------
# The four archives
# ---------------------------------------------------------------------------

STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]
TRIPS_COLS = ["route_id", "service_id", "trip_id", "shape_id"]

# One parent station (the clickable marker, location_type 1) and one platform
# under it (the id realtime references). This is the shape backend/tests uses,
# and validate_subway_archive's parent-station gate requires the parent row.
STOP_ROWS = [
    {
        "stop_id": "101",
        "stop_name": "Alpha",
        "stop_lat": "40.70000",
        "stop_lon": "-74.00000",
        "location_type": "1",
    },
    {
        "stop_id": "101N",
        "stop_name": "Alpha",
        "stop_lat": "40.70000",
        "stop_lon": "-74.00000",
        "location_type": "0",
        "parent_station": "101",
    },
]

# One shape for one route. "A..N01R" is the real subway shape_id shape, and
# static_data._SHAPE_ID_RE reads the route prefix "A" out of it.
SHAPE_ROWS = [
    {"shape_id": "A..N01R", "shape_pt_sequence": str(i), "shape_pt_lat": lat, "shape_pt_lon": lon}
    for i, (lat, lon) in enumerate(
        [
            ("40.70000", "-74.00000"),
            ("40.71000", "-74.00100"),
            ("40.72000", "-74.00200"),
            ("40.73000", "-74.00300"),
            ("40.74000", "-74.00400"),
        ],
        start=1,
    )
]


def csv_text(columns, rows) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


GOOD_STOPS_TXT = csv_text(STOPS_COLS, STOP_ROWS)
GOOD_TRIPS_TXT = csv_text(TRIPS_COLS, ())
GOOD_SHAPES_TXT = csv_text(SHAPES_COLS, SHAPE_ROWS)

# Invalid UTF-8 ONLY in shapes.txt: a lone 0xFF byte inside a latitude field. The
# header and the row structure are otherwise exactly the good file's.
BAD_SHAPES_BYTES = (
    ",".join(SHAPES_COLS).encode("utf-8")
    + b"\r\n"
    + b"A..N01R,1,40.7\xff0000,-74.00000\r\n"
    + b"A..N01R,2,40.71000,-74.00100\r\n"
)

# Well formed, correctly encoded, and empty: the header alone.
EMPTY_SHAPES_TXT = csv_text(SHAPES_COLS, ())


def write_zip(path: Path, members: dict[str, str | bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


UPSTREAM_DIR = _TMP / "upstream"
UPSTREAM_DIR.mkdir(parents=True, exist_ok=True)

ARCHIVE_GOOD = write_zip(
    UPSTREAM_DIR / "good.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,
        "trips.txt": GOOD_TRIPS_TXT,
        "shapes.txt": GOOD_SHAPES_TXT,
    },
)
ARCHIVE_BAD_UTF8 = write_zip(
    UPSTREAM_DIR / "bad_utf8_shapes.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,  # byte identical to the good archive's
        "trips.txt": GOOD_TRIPS_TXT,
        "shapes.txt": BAD_SHAPES_BYTES,
    },
)
ARCHIVE_EMPTY_SHAPES = write_zip(
    UPSTREAM_DIR / "empty_shapes.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,
        "trips.txt": GOOD_TRIPS_TXT,
        "shapes.txt": EMPTY_SHAPES_TXT,
    },
)
ARCHIVE_NO_SHAPES = write_zip(
    UPSTREAM_DIR / "no_shapes.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,
        "trips.txt": GOOD_TRIPS_TXT,
    },
)


# ---------------------------------------------------------------------------
# The fake fetcher: the only injected part of the pipeline
# ---------------------------------------------------------------------------

_UPSTREAM: dict[str, object] = {"publishing": None, "transfers": 0, "forbid": False}


async def fake_stream_to_file(url: str, dest: Path, deadline_s: float) -> None:
    """Stand-in for static_shared._stream_to_file. Copies bytes, opens no socket."""
    if url != DEAD_URL:
        raise AssertionError(f"the pipeline asked for an unexpected URL: {url!r}")
    if "njtransit" in url:  # cannot happen; kept as an explicit tripwire
        raise AssertionError("refusing to contact NJ Transit")
    if _UPSTREAM["forbid"]:
        raise AssertionError("a download was attempted where none was expected")
    source = _UPSTREAM["publishing"]
    if source is None:
        raise AssertionError("no upstream publication was staged for this attempt")
    _UPSTREAM["transfers"] = int(_UPSTREAM["transfers"]) + 1
    shutil.copyfile(source, dest)


static_shared._stream_to_file = fake_stream_to_file  # the injection, and all of it


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

CACHE = static_data.SUBWAY_GTFS_ZIP


class LogCapture(logging.Handler):
    """What an operator would see in the process log for each phase."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.levelname} {record.name}: {record.getMessage()}")

    def drain(self) -> list[str]:
        out = list(self.records)
        self.records.clear()
        return out


CAPTURE = LogCapture()
for _logger_name in ("static_data", "static_shared"):
    logging.getLogger(_logger_name).addHandler(CAPTURE)
    logging.getLogger(_logger_name).setLevel(logging.INFO)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else "(absent)"


def age_cache(days: float) -> None:
    """Backdate the cache mtime so load_subway_stops decides it is stale."""
    old = time.time() - days * 86400
    os.utime(CACHE, (old, old))


def validator_verdict(path: Path) -> tuple[str, str]:
    """Run the REAL publication validator over an archive file, standalone."""
    try:
        static_shared.validate_archive(path, static_data.validate_subway_archive)
    except static_shared.StaticValidationError as exc:
        return "REJECTED", str(exc)
    return "ACCEPTED", ""


def route_census(routes: list[dict]) -> tuple[int, int, int]:
    """(route entries, polylines, points) from load_subway_route_shapes output."""
    polylines = sum(len(r["polylines"]) for r in routes)
    points = sum(len(p) for r in routes for p in r["polylines"])
    return len(routes), polylines, points


async def publish_and_load(source: Path | None, *, forbid_download: bool = False) -> dict:
    """Run the production loader pair the subway warmup runs (warmups.py L173-174).

    Everything here is production code: load_subway_stops decides whether the cache
    is usable and fresh, calls _download_zip, which calls staged_fetch, which
    stages, validates with validate_subway_archive and promotes. Then
    load_subway_route_shapes reads the promoted cache, exactly as the warmup does
    on the next line.
    """
    _UPSTREAM["publishing"] = source
    _UPSTREAM["forbid"] = forbid_download
    before_transfers = int(_UPSTREAM["transfers"])
    before_digest = digest(CACHE)
    error: str | None = None
    stops: dict = {}
    try:
        stops = await static_data.load_subway_stops()
    except Exception as exc:  # a total failure is a legitimate outcome to report
        error = f"{type(exc).__name__}: {exc}"
    routes = static_data.load_subway_route_shapes() if CACHE.exists() else []
    stations = static_data.load_subway_stations() if CACHE.exists() else {}
    entries, polylines, points = route_census(routes)
    status = static_shared.archive_status().get("subway", {})
    return {
        "load_error": error,
        "stops": len(stops),
        "stations": len(stations),
        "route_entries": entries,
        "polylines": polylines,
        "points": points,
        "routes": [r["route"] for r in routes],
        "cache_digest_before": before_digest,
        "cache_digest_after": digest(CACHE),
        "transfers": int(_UPSTREAM["transfers"]) - before_transfers,
        "last_promoted_at": status.get("last_promoted_at"),
        "last_download_error": status.get("last_download_error"),
        "failed_downloads": status.get("failed_downloads"),
        "log": CAPTURE.drain(),
    }


def show(label: str, result: dict) -> None:
    print(f"  {label}")
    print(f"    load_subway_stops              : "
          f"{result['stops']} stops, {result['stations']} station markers"
          + (f", RAISED {result['load_error']}" if result["load_error"] else ""))
    print(f"    load_subway_route_shapes      : {result['route_entries']} route entries, "
          f"{result['polylines']} polylines, {result['points']} points "
          f"{result['routes']}")
    print(f"    cache bytes                   : {result['cache_digest_before']} -> "
          f"{result['cache_digest_after']}"
          f"  ({'REPLACED' if result['cache_digest_before'] != result['cache_digest_after'] else 'unchanged'})")
    print(f"    archive_status()['subway']    : last_promoted_at="
          f"{'set' if result['last_promoted_at'] else result['last_promoted_at']}"
          f"  last_download_error={result['last_download_error']!r}"
          f"  failed_downloads={result['failed_downloads']}")
    for line in result["log"]:
        print(f"    log                           : {line}")


# ---------------------------------------------------------------------------
# Static confirmation of the mechanism the audit names
# ---------------------------------------------------------------------------


def inspect_validator() -> dict:
    """Read backend/static_data.py: which members are required, which are parsed."""
    source = (_BACKEND / "static_data.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    required_line = None
    required: tuple = ()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_REQUIRED_MEMBERS" for t in node.targets
        ):
            required = ast.literal_eval(node.value)
            required_line = node.lineno
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "validate_subway_archive"
    )
    parsed_members = [
        ast.literal_eval(call.args[1])
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "require_parsed"
        and len(call.args) >= 2
    ]
    return {
        "required": required,
        "required_line": required_line,
        "validator_line": fn.lineno,
        "parsed_members": parsed_members,
    }


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def amain() -> int:
    print("=" * 78)
    print("F08  Bad subway geometry can replace known-good geometry")
    print("=" * 78)
    print(f"repo            : {_REPO}")
    print(f"DATA_DIR seam   : {os.environ['DATA_DIR']}")
    print(f"cache path      : {CACHE}")
    print(f"upstream URL    : {static_data.SUBWAY_GTFS_URL}  (nothing listens; the")
    print("                  transfer is replaced by a local file copy)")
    print(f"MAX_AGE_DAYS    : {static_data.MAX_AGE_DAYS}")
    print()

    # ---- the mechanism, read out of the source ----------------------------
    v = inspect_validator()
    print("-" * 78)
    print("MECHANISM  what validate_subway_archive actually asks of each member")
    print("-" * 78)
    print(f"  _REQUIRED_MEMBERS (static_data.py line {v['required_line']}) : {v['required']}")
    print(f"  validate_subway_archive (line {v['validator_line']}) parses  : "
          f"{v['parsed_members']}")
    print("  so shapes.txt is required to EXIST (require_members, presence only)")
    print("  and is never handed to a parser before promotion.")
    check(
        "shapes.txt" in v["required"],
        "shapes.txt is no longer in static_data._REQUIRED_MEMBERS",
    )
    check(
        v["parsed_members"] == ["stops.txt"],
        f"validate_subway_archive now parses {v['parsed_members']}, not only stops.txt",
    )
    print()

    # ---- phase 1: publish the good archive --------------------------------
    print("-" * 78)
    print("PHASE 1  cold start: publish the GOOD archive through the real staged path")
    print("-" * 78)
    verdict, message = validator_verdict(ARCHIVE_GOOD)
    print(f"  validator verdict on the staged bytes : {verdict} {message}")
    good = await publish_and_load(ARCHIVE_GOOD)
    show("after load_subway_stops + load_subway_route_shapes:", good)
    good_digest = good["cache_digest_after"]
    routes_before = good["route_entries"]
    points_before = good["points"]
    check(verdict == "ACCEPTED", "the good archive is no longer accepted by the validator")
    check(good["transfers"] == 1, "the cold start no longer performs exactly one download")
    check(
        routes_before == 1 and good["routes"] == ["A"],
        f"the good archive no longer yields exactly one route (got {good['routes']})",
    )
    check(good["stops"] == 2, "the good archive no longer yields its two stops")
    print()

    # ---- phase 2: THE FINDING ---------------------------------------------
    print("-" * 78)
    print("PHASE 2  THE FINDING: a replacement whose shapes.txt alone is invalid UTF-8")
    print("-" * 78)
    print("  stops.txt in the replacement is byte identical to the good archive's;")
    print("  the only difference is one 0xFF byte inside a shapes.txt latitude.")
    verdict_bad, message_bad = validator_verdict(ARCHIVE_BAD_UTF8)
    print(f"  validator verdict on the staged bytes : {verdict_bad} {message_bad}")
    age_cache(days=40)  # past MAX_AGE_DAYS, so the loader re-downloads
    bad = await publish_and_load(ARCHIVE_BAD_UTF8)
    show("after the replacement was published:", bad)
    routes_after = bad["route_entries"]
    points_after = bad["points"]
    check(
        verdict_bad == "ACCEPTED",
        "the invalid-UTF-8 shapes.txt is now REJECTED by the validator",
    )
    check(bad["transfers"] == 1, "the stale cache no longer triggers a download")
    check(
        bad["cache_digest_before"] == good_digest
        and bad["cache_digest_after"] == digest(ARCHIVE_BAD_UTF8),
        "the bad archive no longer overwrites the good cache",
    )
    check(routes_after == 0, f"the route count no longer falls to zero (got {routes_after})")
    check(
        bad["load_error"] is None and bad["stops"] == 2,
        "load_subway_stops no longer succeeds on the replacement (it now raises or loses stops)",
    )
    check(
        bad["last_promoted_at"] is not None,
        "archive status no longer reports a successful promotion",
    )
    check(
        bad["last_download_error"] is None,
        f"archive status now reports a download error ({bad['last_download_error']!r})",
    )
    check(
        bad["failed_downloads"] == 0,
        f"archive status no longer reports zero failed downloads ({bad['failed_downloads']})",
    )
    print()

    # ---- phase 2b: does it heal? ------------------------------------------
    print("  Does the bad publication heal itself on the next load?")
    still_valid = static_shared.cached_archive_is_valid(CACHE, static_data.validate_subway_archive)
    stuck = await publish_and_load(None, forbid_download=True)
    print(f"    cached_archive_is_valid(cache)      : {still_valid}")
    print(f"    downloads attempted on next load    : {stuck['transfers']}")
    print(f"    route entries on next load          : {stuck['route_entries']}")
    print(f"    the geometry stays gone for         : MAX_AGE_DAYS = "
          f"{static_data.MAX_AGE_DAYS} days, until the mtime goes stale again")
    check(
        still_valid and stuck["transfers"] == 0 and stuck["route_entries"] == 0,
        "the bad cache no longer persists across a subsequent load",
    )
    print()

    # ---- phase 3: the symmetric case the remedy names ---------------------
    print("-" * 78)
    print("PHASE 3  the remedy's other half: a well formed but EMPTY shapes.txt")
    print("-" * 78)
    age_cache(days=40)
    restored = await publish_and_load(ARCHIVE_GOOD)
    print(f"  good archive republished              : {restored['route_entries']} route entries, "
          f"{restored['points']} points")
    check(
        restored["route_entries"] == 1,
        "republishing the good archive no longer restores the single route",
    )
    verdict_empty, message_empty = validator_verdict(ARCHIVE_EMPTY_SHAPES)
    print(f"  validator verdict on header-only bytes: {verdict_empty} {message_empty}")
    age_cache(days=40)
    empty = await publish_and_load(ARCHIVE_EMPTY_SHAPES)
    show("after the header-only shapes.txt was published:", empty)
    check(
        verdict_empty == "ACCEPTED",
        "a header-only shapes.txt is now rejected by the validator",
    )
    check(
        empty["cache_digest_after"] == digest(ARCHIVE_EMPTY_SHAPES),
        "the header-only archive no longer overwrites the good cache",
    )
    check(
        empty["route_entries"] == 0 and empty["last_download_error"] is None,
        "the header-only archive no longer promotes to a zero-route cache cleanly",
    )
    print()

    # ---- phase 4: the control ---------------------------------------------
    print("-" * 78)
    print("PHASE 4  CONTROL: shapes.txt ABSENT (the presence gate does fire)")
    print("-" * 78)
    age_cache(days=40)
    restored2 = await publish_and_load(ARCHIVE_GOOD)
    print(f"  good archive republished              : {restored2['route_entries']} route entries")
    good_digest2 = restored2["cache_digest_after"]
    verdict_missing, message_missing = validator_verdict(ARCHIVE_NO_SHAPES)
    print(f"  validator verdict on the staged bytes : {verdict_missing} ({message_missing})")
    age_cache(days=40)
    missing = await publish_and_load(ARCHIVE_NO_SHAPES)
    show("after the shapes-less archive was offered:", missing)
    check(
        verdict_missing == "REJECTED" and "shapes.txt" in message_missing,
        "an archive with no shapes.txt is no longer rejected",
    )
    check(
        missing["cache_digest_after"] == good_digest2,
        "a rejected publication no longer leaves the cached archive byte-untouched",
    )
    check(
        missing["route_entries"] == 1,
        "a rejected publication no longer preserves the served route geometry",
    )
    check(
        missing["failed_downloads"] == 1 and missing["last_download_error"] is not None,
        "a rejected publication is no longer recorded in archive status",
    )
    print()

    # ---- the arithmetic ----------------------------------------------------
    print("=" * 78)
    print("THE ARITHMETIC")
    print("=" * 78)
    print(f"  routes before the bad publication     : {routes_before} "
          f"({points_before} geometry points)")
    print(f"  routes after the bad publication      : {routes_after} "
          f"({points_after} geometry points)")
    print(f"  change                                : {routes_after - routes_before} routes, "
          f"{points_after - points_before} points "
          f"({100.0 * (points_before - points_after) / max(points_before, 1):.0f} percent of the")
    print("                                          drawable subway geometry gone)")
    print(f"  validator verdict, invalid UTF-8      : {verdict_bad} (promoted)")
    print(f"  validator verdict, header-only        : {verdict_empty} (promoted)")
    print(f"  validator verdict, member absent      : {verdict_missing} (cache preserved)")
    print(f"  archive status after the bad promote  : promoted=yes, "
          f"download_error={bad['last_download_error']!r}, "
          f"failed_downloads={bad['failed_downloads']}")
    print("  the loader's only complaint           : a WARNING from")
    print("                                          load_subway_route_shapes, which returns []")
    print("                                          because route lines are decorative")
    print()

    shutil.rmtree(_TMP, ignore_errors=True)

    if FAILURES:
        print("REALITY HAS CHANGED. These recorded behaviors no longer hold:")
        for failure in FAILURES:
            print(f"  - {failure}")
        print()
        print("DISPOSITION: FAILED TO CONFIRM THE RECORD (see the list above)")
        return 1

    print("DISPOSITION: VERIFIED  validate_subway_archive requires shapes.txt to exist "
          f"(line {v['required_line']}) but parses only {v['parsed_members'][0]} "
          f"(line {v['validator_line']}), so a replacement whose shapes.txt alone holds "
          "invalid UTF-8 passed the real staged validator, was promoted over the "
          f"known-good cache, and dropped the served subway geometry from "
          f"{routes_before} route ({points_before} points) to {routes_after} "
          f"({points_after} points) while archive_status reported a successful promotion, "
          "last_download_error None and failed_downloads 0; a well formed but empty "
          "shapes.txt is accepted and promoted identically, while an archive missing "
          "shapes.txt outright is correctly rejected with the cache left byte-untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
