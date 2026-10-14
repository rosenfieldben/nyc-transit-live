"""F08 (P2), "Bad subway geometry can replace known-good geometry": the repair, pinned.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, verbatim):

    "Validation requires `shapes.txt` to exist but does not parse it. Starting from
     an archive yielding one route, a replacement with invalid UTF-8 only in
     `shapes.txt` passed the actual staged-publication validator and overwrote the
     good archive. Route count then fell to zero. Archive status reported
     successful promotion, no download error and zero failed downloads."

    Remedy (the symmetric case this script also checks): "Test empty and
    unreadable geometry independently of stop validation."

FIXED ON claude/a02-publication-validation, by the rule the 2026-10-12 audit's A02
generalized this finding into: the validator is the loader's parser. Every member the
subway load reads is read at validation through the one generator the load reads it
with, and must yield at least one row, so shapes.txt is parsed before promotion and a
geometry the loader would drop is refused at staging while the last-known-good archive
keeps drawing.

BEFORE (this script's record at 1e3e73d, every number measured by the same phases
below): validate_subway_archive required shapes.txt to EXIST and parsed only stops.txt
(and, from claude/subway-hub-definition, transfers.txt). A replacement whose shapes.txt
alone held one 0xFF byte was ACCEPTED, promoted over the good cache, and dropped the
served subway geometry from 1 route (5 points) to 0 (0 points), while archive_status
reported a promotion, last_download_error None and failed_downloads 0; the bad cache
then persisted (cached_archive_is_valid True, no download on the next load, 0 routes)
until MAX_AGE_DAYS. A well formed but EMPTY shapes.txt was accepted and promoted
identically. An archive with no shapes.txt at all was correctly refused.

AFTER, asserted below: the invalid-UTF-8 replacement and the header-only one are each
REFUSED at staging, the cache is byte-untouched and still draws 1 route (5 points),
and archive_status records the refusal (not promoted, failed_downloads 1,
last_download_error naming shapes.txt and the codec's own words). The control is
unchanged: an archive without shapes.txt is refused.

WHAT THIS SCRIPT MEASURES, AND HOW.

  PRODUCTION CODE, unmodified and called directly:
    static_data.load_subway_stops      the loader the subway warmup awaits
    static_data._download_zip          its staged publication call
    static_shared.staged_fetch         sweep, stage, validate, promote
    static_shared.validate_archive     the zip-open boundary
    static_data.validate_subway_archive the publication validator itself
    static_data.load_subway_route_shapes the route-line loader the warmup calls next
    static_shared.archive_status       the operator surface /api/status reads

  The staged path is driven end to end, not called in isolation: every archive in
  this script arrives at the cache by way of load_subway_stops deciding the cache
  is stale, calling _download_zip, and staged_fetch validating and promoting it.
  The mechanism is measured by behavior (which archives the real validator refuses),
  never by reading the source: the record used to parse static_data.py for the
  validator's member lists, which a behavior-preserving refactor could turn red.

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
       shapes.txt at all (the control). Each carries one trip and one call, which
       the repaired validator requires.
    5. Between publications the cache's mtime is set 40 days back, past
       static_data.MAX_AGE_DAYS, so the loader decides to re-download. That is a
       clock nudge on a file this script wrote, not a dependence on today's date.

NO NJ TRANSIT ANYTHING. No NJT module is imported, no NJT function is called, and
the NJT credential variables backend/.env may carry are blanked after the imports.
Nothing here can reach raildata.njtransit.com or spend a mint. Nothing here opens a
socket at all.

Hermetic and deterministic: no network, no live feed, no committed fixture needed
(the subway static GTFS is a 5.6 MB download that expands to 43 MB, not a committed
fixture, so the archives are built here), and no dependence on today's date.

RUN, from the repository root:
  .venv/bin/python docs/reviews/audit-2026-09-05/f08_bad_geometry_replaces_good.py

RECORDED DISPOSITION: FIXED (claude/a02-publication-validation). Exits 0 while the
repair holds, non-zero (with every check that moved named) the moment it does not.
"""

from __future__ import annotations

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
# ONE TRIP AND ONE CALL, in every archive below. They were headers only until the A02
# repair (claude/a02-publication-validation) made the validator read every member the
# load reads and want a row from each: a header-only trips.txt is now a publication it
# refuses, and an archive refused for THAT would measure nothing about shapes.txt.
GOOD_TRIPS_TXT = csv_text(
    TRIPS_COLS, [{"route_id": "A", "service_id": "wk", "trip_id": "t1", "shape_id": "A..N01R"}]
)
GOOD_SHAPES_TXT = csv_text(SHAPES_COLS, SHAPE_ROWS)

# stop_times.txt joined _REQUIRED_MEMBERS with the F1 branch (MR2's review finding:
# the routes-per-station index feeds the transfer ring, the hub label class and the
# station alerts join, so an archive without it is a reduced archive). In every archive
# below, with one call, for the same reason trips.txt has a row: this record is about a
# shapes.txt that is invalid UTF-8, and every archive here has to be otherwise complete
# or the validator rejects it for the wrong reason and the record stops measuring what
# it was taken for. That is exactly what happened when the member was added: the run
# derailed at phase 2 with "REJECTED missing required member(s): stop_times.txt".
GOOD_STOP_TIMES_TXT = csv_text(
    ["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"],
    [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}],
)
# In every archive below, for the reason stop_times.txt is: transfers.txt became a required
# member of the subway archive on claude/subway-hub-definition, with a floor of one
# cross-stop row, and an archive without one would be rejected for THAT, not for the invalid
# shapes.txt this record is about. With it present the four archives still differ only where
# they are meant to.
GOOD_TRANSFERS_TXT = csv_text(
    ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"],
    [{"from_stop_id": "101", "to_stop_id": "103", "transfer_type": "2", "min_transfer_time": "180"}],
)

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
        "stop_times.txt": GOOD_STOP_TIMES_TXT,
        "transfers.txt": GOOD_TRANSFERS_TXT,
    },
)
ARCHIVE_BAD_UTF8 = write_zip(
    UPSTREAM_DIR / "bad_utf8_shapes.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,  # byte identical to the good archive's
        "trips.txt": GOOD_TRIPS_TXT,
        "shapes.txt": BAD_SHAPES_BYTES,
        "stop_times.txt": GOOD_STOP_TIMES_TXT,
        "transfers.txt": GOOD_TRANSFERS_TXT,
    },
)
ARCHIVE_EMPTY_SHAPES = write_zip(
    UPSTREAM_DIR / "empty_shapes.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,
        "trips.txt": GOOD_TRIPS_TXT,
        "shapes.txt": EMPTY_SHAPES_TXT,
        "stop_times.txt": GOOD_STOP_TIMES_TXT,
        "transfers.txt": GOOD_TRANSFERS_TXT,
    },
)
ARCHIVE_NO_SHAPES = write_zip(
    UPSTREAM_DIR / "no_shapes.zip",
    {
        "stops.txt": GOOD_STOPS_TXT,
        "trips.txt": GOOD_TRIPS_TXT,
        "stop_times.txt": GOOD_STOP_TIMES_TXT,
        "transfers.txt": GOOD_TRANSFERS_TXT,
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
    promoted_before = static_shared.archive_status().get("subway", {}).get("last_promoted_at")
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
        # A refusal leaves the previous promotion's time in place, so "this publication
        # was promoted" is a change in it, not its presence.
        "promoted": status.get("last_promoted_at") != promoted_before,
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
# The run
# ---------------------------------------------------------------------------

BRANCH = "claude/a02-publication-validation"


async def amain() -> int:
    print("=" * 78)
    print("F08  Bad subway geometry can replace known-good geometry (the repair)")
    print("=" * 78)
    print(f"repo            : {_REPO}")
    print(f"DATA_DIR seam   : {os.environ['DATA_DIR']}")
    print(f"cache path      : {CACHE}")
    print(f"upstream URL    : {static_data.SUBWAY_GTFS_URL}  (nothing listens; the")
    print("                  transfer is replaced by a local file copy)")
    print(f"MAX_AGE_DAYS    : {static_data.MAX_AGE_DAYS}")
    print()

    # ---- the mechanism, by behavior ----------------------------------------
    print("-" * 78)
    print("MECHANISM  what validate_subway_archive asks of shapes.txt, by behavior")
    print("-" * 78)
    verdicts = {
        "good": validator_verdict(ARCHIVE_GOOD),
        "absent": validator_verdict(ARCHIVE_NO_SHAPES),
        "one 0xFF": validator_verdict(ARCHIVE_BAD_UTF8),
        "header only": validator_verdict(ARCHIVE_EMPTY_SHAPES),
    }
    for label, (verdict, message) in verdicts.items():
        print(f"  shapes.txt {label:<12}: {verdict} {message}")
    print("  BEFORE: absent was the only refusal; one 0xFF and header only were ACCEPTED,")
    print("  because shapes.txt was checked for presence and never parsed.")
    check(verdicts["good"][0] == "ACCEPTED", "the good archive is no longer accepted")
    check(
        verdicts["absent"][0] == "REJECTED" and "shapes.txt" in verdicts["absent"][1],
        "an archive with no shapes.txt is no longer refused (the control)",
    )
    check(
        verdicts["one 0xFF"][0] == "REJECTED"
        and verdicts["one 0xFF"][1].startswith("shapes.txt is not readable as CSV (")
        and "can't decode byte 0xff" in verdicts["one 0xFF"][1],
        "an invalid-UTF-8 shapes.txt is ACCEPTED again (or refused without naming the "
        f"member and the codec's words): {verdicts['one 0xFF']}",
    )
    check(
        verdicts["header only"] == ("REJECTED", "shapes.txt yielded no usable route shape points"),
        f"a header-only shapes.txt is ACCEPTED again: {verdicts['header only']}",
    )
    print()

    # ---- phase 1: publish the good archive --------------------------------
    print("-" * 78)
    print("PHASE 1  cold start: publish the GOOD archive through the real staged path")
    print("-" * 78)
    good = await publish_and_load(ARCHIVE_GOOD)
    show("after load_subway_stops + load_subway_route_shapes:", good)
    good_digest = good["cache_digest_after"]
    routes_before = good["route_entries"]
    points_before = good["points"]
    check(good["transfers"] == 1, "the cold start no longer performs exactly one download")
    check(
        routes_before == 1 and good["routes"] == ["A"] and points_before == 5,
        f"the good archive no longer yields exactly one route of 5 points (got {good['routes']})",
    )
    check(good["stops"] == 2, "the good archive no longer yields its two stops")
    print()

    # ---- phase 2: THE FINDING, refused ------------------------------------
    print("-" * 78)
    print("PHASE 2  THE FINDING: a replacement whose shapes.txt alone is invalid UTF-8")
    print("-" * 78)
    print("  stops.txt in the replacement is byte identical to the good archive's;")
    print("  the only difference is one 0xFF byte inside a shapes.txt latitude.")
    age_cache(days=40)  # past MAX_AGE_DAYS, so the loader re-downloads
    bad = await publish_and_load(ARCHIVE_BAD_UTF8)
    show("after the replacement was published:", bad)
    routes_after = bad["route_entries"]
    points_after = bad["points"]
    check(bad["transfers"] == 1, "the stale cache no longer triggers a download")
    check(
        bad["cache_digest_after"] == good_digest,
        "the invalid-UTF-8 replacement overwrote the good cache again",
    )
    check(
        routes_after == 1 and points_after == 5,
        f"the served geometry fell again ({routes_after} routes, {points_after} points)",
    )
    check(
        bad["load_error"] is None and bad["stops"] == 2,
        "load_subway_stops no longer serves the cached archive through the refusal",
    )
    check(not bad["promoted"], "archive status reports a promotion again")
    check(
        bad["failed_downloads"] == 1
        and (bad["last_download_error"] or "").startswith(
            "invalid archive: shapes.txt is not readable as CSV ("
        ),
        f"archive status no longer records the refusal by member ({bad['last_download_error']!r}, "
        f"failed_downloads {bad['failed_downloads']})",
    )
    print()

    # ---- phase 2b: the next load ------------------------------------------
    print("  The next load, with the replacement withdrawn upstream (no download allowed):")
    still_valid = static_shared.cached_archive_is_valid(CACHE, static_data.validate_subway_archive)
    os.utime(CACHE)  # the good cache, fresh again: the next load must not download
    nxt = await publish_and_load(None, forbid_download=True)
    print(f"    cached_archive_is_valid(cache)      : {still_valid}")
    print(f"    downloads attempted on next load    : {nxt['transfers']}")
    print(f"    route entries on next load          : {nxt['route_entries']}")
    check(
        still_valid and nxt["transfers"] == 0 and nxt["route_entries"] == 1,
        "the good cache no longer serves its route on the next load",
    )
    print()

    # ---- phase 3: the symmetric case the remedy names ---------------------
    print("-" * 78)
    print("PHASE 3  the remedy's other half: a well formed but EMPTY shapes.txt")
    print("-" * 78)
    age_cache(days=40)
    empty = await publish_and_load(ARCHIVE_EMPTY_SHAPES)
    show("after the header-only shapes.txt was published:", empty)
    check(
        empty["cache_digest_after"] == good_digest,
        "the header-only archive overwrote the good cache again",
    )
    check(
        empty["route_entries"] == 1 and not empty["promoted"],
        "the header-only archive is promoted to a zero-route cache again",
    )
    check(
        empty["last_download_error"]
        == "invalid archive: shapes.txt yielded no usable route shape points",
        f"the empty geometry is no longer refused by name ({empty['last_download_error']!r})",
    )
    print()

    # ---- phase 4: the control ---------------------------------------------
    print("-" * 78)
    print("PHASE 4  CONTROL: shapes.txt ABSENT (the presence gate does fire)")
    print("-" * 78)
    age_cache(days=40)
    missing = await publish_and_load(ARCHIVE_NO_SHAPES)
    show("after the shapes-less archive was offered:", missing)
    check(
        missing["cache_digest_after"] == good_digest,
        "a refused publication no longer leaves the cached archive byte-untouched",
    )
    check(
        missing["route_entries"] == 1,
        "a refused publication no longer preserves the served route geometry",
    )
    check(
        missing["last_download_error"] is not None and not missing["promoted"],
        "a refused publication is no longer recorded in archive status",
    )
    print()

    # ---- the arithmetic ----------------------------------------------------
    print("=" * 78)
    print("THE ARITHMETIC")
    print("=" * 78)
    print(f"  routes before the bad publication     : {routes_before} ({points_before} points)")
    print(f"  routes after the bad publication      : {routes_after} ({points_after} points)")
    print("                                          BEFORE the repair: 0 (0 points)")
    print(f"  validator, invalid UTF-8              : {verdicts['one 0xFF'][0]} (BEFORE: ACCEPTED)")
    print(f"  validator, header only                : {verdicts['header only'][0]} (BEFORE: ACCEPTED)")
    print(f"  validator, member absent              : {verdicts['absent'][0]} (unchanged)")
    print(f"  archive status after the refusal      : promoted={bad['promoted']}, "
          f"failed_downloads={bad['failed_downloads']}")
    print(f"                                          error={bad['last_download_error']!r}")
    print()

    shutil.rmtree(_TMP, ignore_errors=True)

    if FAILURES:
        print("THE REPAIR NO LONGER HOLDS. These checks moved:")
        for failure in FAILURES:
            print(f"  - {failure}")
        print()
        print("DISPOSITION: FAILED TO CONFIRM THE RECORD (see the list above)")
        return 1

    print(
        f"DISPOSITION: FIXED ({BRANCH})  BEFORE: validate_subway_archive checked shapes.txt "
        "for presence only, so a replacement whose shapes.txt alone held one 0xFF byte was "
        "accepted, promoted over the known-good cache, and dropped the served subway "
        "geometry from 1 route (5 points) to 0 while archive_status reported a promotion, "
        "no error and 0 failed downloads, and a header-only shapes.txt was promoted the same "
        "way. AFTER: the validator reads shapes.txt through the route loader's own parser, "
        f"so both are refused at staging, the cache is byte-untouched and still draws "
        f"{routes_after} route ({points_after} points), and archive_status records the "
        f"refusal ({bad['failed_downloads']} failed download, not promoted, "
        f"{bad['last_download_error']!r}); an archive without shapes.txt is still refused."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
