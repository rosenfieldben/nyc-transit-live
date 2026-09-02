"""Download and load the static GTFS for the MTA railroads (LIRR + Metro-North).

The Phase-2 data foundation: each system's stops, trips, shapes, and routes
loaded into memory. Railroad GTFS diverges from the subway schema (opaque plain
stop_ids with no N/S suffix, different shape_id formats), so the subway helpers in
static_data are intentionally NOT reused. main.py's lifespan loads this at startup
and stores the per-system stops on app.state.railroad_stops, which
feeds._decode_railroad_placements uses to place the position-less trains at their
next station. The trips and shapes tables feed the route-geometry builder, and the
routes table supplies the rider-facing route names on /api/railroad-routes.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import env_seams
import route_geometry
from static_routes import fold_stop_routes
from static_shared import (
    cached_archive_is_valid,
    require_members,
    require_parsed,
    staged_fetch,
)

logger = logging.getLogger(__name__)

DATA_DIR = env_seams.directory("DATA_DIR", "data")
_STATIC_DIR = DATA_DIR / "gtfs_static"

# The canonical S3 URLs the MTA developer paths 301 to, verified 2026-06-22
# (both serve 200 application/zip over https; the old plain-http
# web.mta.info/developers/data/... paths redirect here). The module and tests
# never depend on either URL resolving.
# Overridable base (C6), with the per-system filenames kept as suffixes because
# they are part of the MTA's publishing scheme rather than of the host.
RAILROAD_STATIC_BASE = env_seams.url("RAILROAD_STATIC_BASE", "https://rrgtfsfeeds.s3.amazonaws.com")
RAILROAD_STATIC_URLS = {
    "LIRR": RAILROAD_STATIC_BASE + "/gtfslirr.zip",
    "MNR": RAILROAD_STATIC_BASE + "/gtfsmnr.zip",
}
RAILROAD_STATIC_ZIPS = {
    "LIRR": _STATIC_DIR / "gtfs_lirr.zip",
    "MNR": _STATIC_DIR / "gtfs_mnr.zip",
}

# Re-download a system's static GTFS when the cached copy is older than this. The
# MTA republishes it a few times a year; stop coordinates change rarely.
MAX_AGE_DAYS = 30

# Members a railroad system's load READS and cannot degrade around: stops place
# every marker and every position-less train, trips and shapes draw the route
# lines, routes name them. stop_times.txt is excluded on purpose even though the
# load reads it: it feeds only the routes-per-station index (H5, popup
# enrichment), whose absence already yields an empty index, so requiring it would
# turn a small degradation into a whole system missing from the map.
_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt", "routes.txt")


def validate_railroad_archive(zf: zipfile.ZipFile) -> None:
    """Can we serve one railroad system from this archive? Raises on no.

    Deliberately STRICTER than _parse_system, which treats routes.txt as optional
    at parse time. That leniency is about surviving an already-cached archive;
    this is about whether to accept a NEW publication, and a publication missing
    its route names is a broken one worth rejecting while the previous archive
    still serves.
    """
    require_members(zf, _REQUIRED_MEMBERS)
    # Nonempty through the loader's own parsers, so "validated" means "the load
    # will produce something" rather than "the files exist". This is the railroad's
    # copy of third-audit finding 4's gate, the check R3 grew here first.
    require_parsed(lambda: _parse_stops(zf), "stops.txt", "stops")
    require_parsed(lambda: _parse_routes(zf), "routes.txt", "routes")


def validate_railroad_publication(zf: zipfile.ZipFile) -> None:
    """The gate a NEW archive must pass before it may replace the cached one.

    Strictly stronger than validate_railroad_archive, and the difference is the
    whole point: it runs the REAL parse of every table the load reads, so an
    archive that would fail the load can never be promoted over a working one.

    Without this the pipeline had a hole exactly the shape of the bug it exists to
    fix. A publication with clean stops.txt and routes.txt but, say, an
    undecodable byte in trips.txt passed the light validator, was renamed over the
    last-known-good, and was then deleted by _load_one's residual arm: one bad
    publication, and both the new archive and the good one it replaced were gone.
    Running the full parse BEFORE the rename closes it.

    The cost lands where it belongs. This runs once per download attempt, while
    the light validator runs on every load, so the expensive tables are parsed
    twice only when something is actually being published.
    """
    validate_railroad_archive(zf)
    _parse_open(zf)


async def _download_zip(system: str) -> None:
    """Stage, validate, then promote one system's archive (see static_shared)."""
    await staged_fetch(
        RAILROAD_STATIC_URLS[system],
        RAILROAD_STATIC_ZIPS[system],
        validate_railroad_publication,
        key=f"railroad_{system}",
        label=f"{system} static GTFS",
    )


def _parse_stops(zf: zipfile.ZipFile) -> dict[str, dict]:
    """stops.txt -> stop_id -> {name, lat, lon}. stop_ids are opaque plain ids
    (no N/S suffix); coords are kept at full precision. Rows with a missing or
    malformed coordinate are skipped."""
    stops: dict[str, dict] = {}
    with zf.open("stops.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            stop_id = (row.get("stop_id") or "").strip()
            if not stop_id:
                continue
            try:
                lat = float(row.get("stop_lat") or "")
                lon = float(row.get("stop_lon") or "")
            except ValueError:
                continue
            stops[stop_id] = {
                "name": (row.get("stop_name") or "").strip() or None,
                "lat": lat,
                "lon": lon,
            }
    return stops


def _parse_trips(zf: zipfile.ZipFile) -> dict[str, dict]:
    """trips.txt -> trip_id -> {route_id, direction_id, shape_id, headsign}, each
    a stripped string or None when blank. Rows with no trip_id are skipped;
    first-writer-wins on a duplicate trip_id."""
    trips: dict[str, dict] = {}
    with zf.open("trips.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            trip_id = (row.get("trip_id") or "").strip()
            if not trip_id or trip_id in trips:
                continue
            trips[trip_id] = {
                "route_id": (row.get("route_id") or "").strip() or None,
                "direction_id": (row.get("direction_id") or "").strip() or None,
                "shape_id": (row.get("shape_id") or "").strip() or None,
                "headsign": (row.get("trip_headsign") or "").strip() or None,
            }
    return trips


def _parse_shapes(zf: zipfile.ZipFile) -> dict[str, list]:
    """shapes.txt -> shape_id -> [[lat, lon], ...] ordered by shape_pt_sequence,
    coords rounded to 5 decimals (~1 m, matching the subway/bus shape rounding).
    Rows with a blank shape_id or a malformed point are skipped."""
    raw_points: dict[str, list] = defaultdict(list)
    with zf.open("shapes.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            shape_id = (row.get("shape_id") or "").strip()
            if not shape_id:
                continue
            try:
                # Build the point first; a malformed row must not create an empty
                # shape entry via the defaultdict before the append.
                point = (
                    int(row["shape_pt_sequence"]),
                    round(float(row["shape_pt_lat"]), 5),
                    round(float(row["shape_pt_lon"]), 5),
                )
            except (KeyError, ValueError, TypeError):
                continue  # malformed point row
            raw_points[shape_id].append(point)
    shapes: dict[str, list] = {}
    for shape_id, points in raw_points.items():
        points.sort()  # by shape_pt_sequence
        shapes[shape_id] = [[lat, lon] for (_seq, lat, lon) in points]
    return shapes


def _parse_routes(zf: zipfile.ZipFile) -> dict[str, dict]:
    """routes.txt -> route_id -> {long_name, short_name}, each a stripped string
    or None when blank. Rows with no route_id are skipped; first-writer-wins on a
    duplicate route_id.

    routes.txt is treated as OPTIONAL: a zip without it yields an empty table
    rather than failing the whole system load, because the names are a rider-facing
    convenience, not load-critical like stops/trips/shapes. route_color is
    deliberately NOT read: the project uses its own palette rather than agency
    branding (see the README MTA-branding note), so the agency colors are unused.
    """
    routes: dict[str, dict] = {}
    try:
        member = zf.open("routes.txt")
    except KeyError:
        return routes  # optional member absent: no names, not a load failure
    with member as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            route_id = (row.get("route_id") or "").strip()
            if not route_id or route_id in routes:
                continue
            routes[route_id] = {
                "long_name": (row.get("route_long_name") or "").strip() or None,
                "short_name": (row.get("route_short_name") or "").strip() or None,
            }
    return routes


def _parse_stop_times(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    """stop_times.txt -> trip_id -> [stop_id]. Only membership matters for the
    routes-per-station index (which stops a trip visits), so rows are collected
    unsorted. Railroad stop_ids are flat (no parent/child split), so these join
    straight to the stop markers. Treated as OPTIONAL, like routes.txt: a
    repackaged zip without it yields an empty table so the system still loads
    (the index just comes up empty), rather than failing the whole load."""
    trip_stops: dict[str, list[str]] = defaultdict(list)
    try:
        member = zf.open("stop_times.txt")
    except KeyError:
        return {}  # optional member absent: no routes-per-station, not a failure
    with member as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            trip_id = (row.get("trip_id") or "").strip()
            stop_id = (row.get("stop_id") or "").strip()
            if not trip_id or not stop_id:
                continue
            trip_stops[trip_id].append(stop_id)
    return dict(trip_stops)


def derive_railroad_stop_routes(
    trips: dict[str, dict], stop_times: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Pure: stop_id -> sorted [route_id] serving it, for ONE railroad system.
    Railroad stops are flat (no parent/child fold), so the stop_times stop ids
    join directly. Delegates the join to static_routes.fold_stop_routes after
    pulling route_id out of each trip record."""
    trip_routes = {trip_id: t.get("route_id") for trip_id, t in trips.items()}
    return fold_stop_routes(trip_routes, stop_times)


def _parse_system(zip_path: Path) -> dict:
    """Parse one railroad GTFS zip into {stops, trips, shapes, routes, stop_times}
    in a single open.

    Kept lenient about routes.txt and stop_times.txt (each yields an empty table
    when absent) even though validate_railroad_archive now requires routes.txt.
    The two answer different questions and the split is deliberate: the validator
    decides whether to ACCEPT an archive, this decides how to read one that is
    already here, and a parser that tolerates a missing optional member stays
    usable by the tests that feed it hand-built zips."""
    with zipfile.ZipFile(zip_path) as zf:
        return _parse_open(zf)


def _parse_open(zf: zipfile.ZipFile) -> dict:
    """The parse itself, over an already-open archive, so validate_railroad_publication
    can run the REAL load against a staged file that has no cache path yet."""
    return {
        "stops": _parse_stops(zf),
        "trips": _parse_trips(zf),
        "shapes": _parse_shapes(zf),
        "routes": _parse_routes(zf),
        "stop_times": _parse_stop_times(zf),
    }


def build_railroad_route_shapes(
    trips: dict[str, dict], shapes: dict[str, list], route_names: dict[str, dict] | None = None
) -> list[dict]:
    """Per-route representative polylines for one railroad system.

    Returns [{"route": route_id, "name": str | None, "polylines": [...]}, ...]
    sorted by route_id. `name` is the rider-facing route name (long_name, else
    short_name, else null) looked up in `route_names` (the parsed routes.txt
    table); pass it to fill names, omit it for a geometry-only build. A pure
    transform over the already-parsed tables (no zip read, no network), so the
    lifespan builds it from app.state.railroad_static[system] without re-parsing.
    A route dropped here for having no usable geometry (below) also loses its
    name: it has no line to draw and no trains to place, so it is invisible either
    way (documented on the /api/railroad-routes endpoint).

    Railroad shape_ids are not route-encoded (unlike the subway A..N04R form), so
    routes are grouped via trips.txt (trip -> route_id, shape_id) rather than a
    shape_id regex. This also serves MNR, whose realtime trip_ids do not join
    trips.txt: the route line is built from the STATIC trips/shapes only, and the
    frontend associates a train with its route by route_id plus coordinate
    projection, never by the realtime trip_id.

    For each route the distinct shape_ids its trips use are collected (blank
    shape_ids skipped), polylines pulled from `shapes`, and added-geometry dedup
    keeps branch variants while collapsing shared-track and reverse-direction
    variants. Both steps are route_geometry.route_polylines, which is where that
    rule and its determinism argument now live: 15c gave it a second caller (NJ
    Transit), and a rule that decides whether a branch appears on the map is the
    last thing that should exist as two copies drifting apart.
    """
    routes: list[dict] = []
    # Drop a route with no usable geometry (deliberately unlike the subway
    # load_subway_route_shapes, which appends every route even with empty
    # polylines); a railroad route line is only emitted when it has geometry.
    # route_polylines omits such a route, so this comprehension inherits the rule.
    for route_id, kept in route_geometry.route_polylines(trips, shapes).items():
        info = (route_names or {}).get(route_id) or {}
        name = info.get("long_name") or info.get("short_name")
        routes.append({"route": route_id, "name": name, "polylines": kept})
    return routes


async def _load_one(system: str) -> dict | None:
    """Ensure/refresh one system's zip and parse it, or None on any failure.

    Lenient by design: a download or parse failure for this system logs and
    returns None rather than raising, so one system can never block the other.
    """
    zip_path = RAILROAD_STATIC_ZIPS[system]
    # FRESH NOW MEANS VALID AND RECENT (C5). R3's invalidate-on-empty-parse lived
    # here: it raised on a clean parse yielding zero stops and unlinked the cache,
    # because a just-downloaded bad zip is fresh by mtime and every later attempt
    # would otherwise re-parse the same bad bytes forever. That special case is now
    # the general rule from two directions: validate_railroad_archive refuses to
    # promote such a publication at all, and cached_archive_is_valid rejects one
    # already on disk before it is parsed, so the retry re-fetches by itself.
    usable = zip_path.exists() and cached_archive_is_valid(zip_path, validate_railroad_archive)
    fresh = usable and time.time() - zip_path.stat().st_mtime < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            await _download_zip(system)
        except Exception as exc:
            if not usable:
                logger.warning("%s static GTFS download failed (%s); no cached copy", system, exc)
                return None
            # Serving old while new is bad, INCLUDING past MAX_AGE_DAYS: the age
            # policy exists to pick up upstream's corrections, so it yields to
            # validity rather than dropping a working system. staged_fetch recorded
            # the reason for /api/status.
            logger.warning(
                "%s static GTFS re-download failed (%s); using the cached copy", system, exc
            )
    try:
        data = _parse_system(zip_path)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, csv.Error) as exc:
        # The residual below the CACHED-archive validator, which is lighter than the
        # one every publication passes. Reaching here means the bytes on disk were
        # fully parseable when they were promoted and are not now (rot, a truncated
        # write) or predate C5 entirely. Unlink is right in exactly that case and
        # only that case: this file IS the cache, nothing better sits behind it, and
        # keeping it would wedge every retry on the same bytes. A freshly promoted
        # archive cannot land here, because validate_railroad_publication ran this
        # same parse before the rename.
        logger.warning("Cached %s static GTFS is unparseable (%s); discarding", system, exc)
        zip_path.unlink(missing_ok=True)
        return None
    logger.info(
        "Loaded %s static GTFS: %d stops, %d trips, %d shapes, %d routes",
        system,
        len(data["stops"]),
        len(data["trips"]),
        len(data["shapes"]),
        len(data["routes"]),
    )
    return data


async def load_railroad_static() -> dict[str, dict | None]:
    """Load per-system static GTFS for the railroads.

    Returns {"LIRR": {stops, trips, shapes} | None, "MNR": {...} | None}. Each
    system is ensured/refreshed and parsed independently and leniently: a failure
    for one leaves it None without raising or affecting the other, so this never
    raises on a single-system failure even though placement consumes it. The
    systems load concurrently to keep cold-start under the healthcheck window;
    _load_one swallows its own exceptions and returns None, so a plain gather
    (no return_exceptions) preserves the per-system None semantics.

    NOTE the caller now reads the AGGREGATE (warmups._warm_railroad_static, R3): a
    result where EVERY system came back unusable (None, or parsed but carrying no
    stops) counts as a failed attempt and retries, while a partial result (one
    system usable) still reaches ready. That judgment
    deliberately lives in the warmup, not here: this function's per-system None
    contract is load-bearing both for the single-failure case (the surviving
    system must still be served) and for the tests that assert it, so nothing
    about the return shape changed. Only the interpretation of an all-None result
    moved, from "ready, fully degraded, never retried" to "failed, retry".
    """
    systems = list(RAILROAD_STATIC_URLS)
    results = await asyncio.gather(*(_load_one(system) for system in systems))
    return dict(zip(systems, results))
