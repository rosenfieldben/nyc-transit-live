"""Download and load the static GTFS for the MTA railroads (LIRR + Metro-North).

The Phase-2 data foundation: each system's stops, trips, and shapes loaded into
memory. Railroad GTFS diverges from the subway schema (opaque plain stop_ids with
no N/S suffix, different shape_id formats), so the subway helpers in static_data
are intentionally NOT reused. main.py's lifespan loads this at startup and stores
the per-system stops on app.state.railroad_stops, which feeds._decode_railroad_
placements uses to place the position-less trains at their next station. The
trips and shapes tables are parsed too, for a later gliding increment.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = PROJECT_ROOT / "data" / "gtfs_static"

# The canonical S3 URLs the MTA developer paths 301 to, verified 2026-06-22
# (both serve 200 application/zip over https; the old plain-http
# web.mta.info/developers/data/... paths redirect here). The module and tests
# never depend on either URL resolving.
RAILROAD_STATIC_URLS = {
    "LIRR": "https://rrgtfsfeeds.s3.amazonaws.com/gtfslirr.zip",
    "MNR": "https://rrgtfsfeeds.s3.amazonaws.com/gtfsmnr.zip",
}
RAILROAD_STATIC_ZIPS = {
    "LIRR": _STATIC_DIR / "gtfs_lirr.zip",
    "MNR": _STATIC_DIR / "gtfs_mnr.zip",
}

# Re-download a system's static GTFS when the cached copy is older than this. The
# MTA republishes it a few times a year; stop coordinates change rarely.
MAX_AGE_DAYS = 30

# Whole-transfer deadline per static zip, tighter than Railway's 300s
# healthcheck window so the subway zip plus the (now concurrent) railroad
# pair stay under it on a cold deploy. Stopgap: the durable fix is to move
# static loading off the startup critical path into a background task like
# bus_static. The MTA zips are small (S3-fast), so 120s is generous in
# practice; the residual risk is a degraded network leaving a system on
# GPS-only / 503 until the next deploy.
_DOWNLOAD_DEADLINE_S = 120


async def _download_zip(system: str) -> None:
    """Download one system's GTFS zip atomically into its cache path."""
    url = RAILROAD_STATIC_URLS[system]
    dest = RAILROAD_STATIC_ZIPS[system]
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Sweep this system's temp files orphaned by an earlier hard kill mid-download.
    # Scoped to the system's stem so it can't disturb the other system's archive.
    for stale in dest.parent.glob(f"{dest.stem}.*.part"):
        stale.unlink(missing_ok=True)
    # Unique temp name in the same dir so the final rename stays atomic and
    # concurrent downloads are last-writer-wins.
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f"{dest.stem}.", suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    logger.info("Downloading %s static GTFS from %s", system, url)
    try:
        # httpx's timeout is per socket read; bound the whole transfer so a
        # trickling response can't stall indefinitely.
        async with asyncio.timeout(_DOWNLOAD_DEADLINE_S):
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Downloaded %s static GTFS to %s", system, dest)


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


def _parse_system(zip_path: Path) -> dict:
    """Parse one railroad GTFS zip into {stops, trips, shapes} in a single open."""
    with zipfile.ZipFile(zip_path) as zf:
        return {
            "stops": _parse_stops(zf),
            "trips": _parse_trips(zf),
            "shapes": _parse_shapes(zf),
        }


# A shape variant is kept only if it adds more than this fraction of new
# geometry vs. variants already kept for the route, the same threshold and
# rationale as the subway route lines (static_data._MIN_NEW_GEOMETRY):
# express/local and the reverse-direction shape share almost all points and
# collapse, while a real branch (e.g. the New Haven line's New Canaan / Danbury
# / Waterbury legs) survives.
_MIN_NEW_GEOMETRY = 0.05


def build_railroad_route_shapes(trips: dict[str, dict], shapes: dict[str, list]) -> list[dict]:
    """Per-route representative polylines for one railroad system.

    Returns [{"route": route_id, "polylines": [[[lat, lon], ...], ...]}, ...]
    sorted by route_id. A pure transform over the already-parsed trips/shapes
    tables (no zip read, no network), so the lifespan builds it from
    app.state.railroad_static[system] without re-parsing.

    Railroad shape_ids are not route-encoded (unlike the subway A..N04R form), so
    routes are grouped via trips.txt (trip -> route_id, shape_id) rather than a
    shape_id regex. This also serves MNR, whose realtime trip_ids do not join
    trips.txt: the route line is built from the STATIC trips/shapes only, and the
    frontend associates a train with its route by route_id plus coordinate
    projection, never by the realtime trip_id.

    For each route the distinct shape_ids its trips use are collected (blank
    shape_ids skipped), polylines pulled from `shapes`, and added-geometry dedup
    (the subway threshold) keeps branch variants while collapsing shared-track
    and reverse-direction variants (the point-set test is order-independent, so a
    reversed shape reads as 0% new and drops out). A route whose shapes are all
    blank or degenerate is dropped.
    """
    shape_ids_by_route: dict[str, set] = defaultdict(set)
    for trip in trips.values():
        route_id, shape_id = trip.get("route_id"), trip.get("shape_id")
        if route_id and shape_id:
            shape_ids_by_route[route_id].add(shape_id)

    routes: list[dict] = []
    for route_id, shape_ids in sorted(shape_ids_by_route.items()):
        # sorted(shape_ids) before the stable length sort so the variant order is
        # deterministic: set iteration of shape_id strings is salted by
        # PYTHONHASHSEED, and among equal-length variants the dedup loop keeps
        # whichever it sees first, so an unsorted set could yield a different
        # polyline order AND a different kept set across process restarts. The
        # subway builder gets this for free by iterating insertion-ordered
        # shapes.items(); we sort the shape_ids to match. shapes.get(s) (not [s])
        # tolerates a trip that references a shape_id absent from shapes.txt.
        variants = [pts for s in sorted(shape_ids) if len(pts := shapes.get(s) or []) >= 2]
        variants.sort(key=len, reverse=True)
        kept: list[list] = []
        covered: set[tuple] = set()
        for polyline in variants:
            point_set = {tuple(p) for p in polyline}
            if len(point_set - covered) / max(len(point_set), 1) > _MIN_NEW_GEOMETRY:
                kept.append(polyline)
                covered |= point_set
        # Drop a route with no usable geometry (deliberately unlike the subway
        # load_subway_route_shapes, which appends every route even with empty
        # polylines); a railroad route line is only emitted when it has geometry.
        if kept:
            routes.append({"route": route_id, "polylines": kept})
    return routes


async def _load_one(system: str) -> dict | None:
    """Ensure/refresh one system's zip and parse it, or None on any failure.

    Lenient by design: a download or parse failure for this system logs and
    returns None rather than raising, so one system can never block the other.
    """
    zip_path = RAILROAD_STATIC_ZIPS[system]
    fresh = zip_path.exists() and time.time() - zip_path.stat().st_mtime < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            await _download_zip(system)
        except Exception as exc:
            if not zip_path.exists():
                logger.warning("%s static GTFS download failed (%s); no cached copy", system, exc)
                return None
            logger.warning(
                "%s static GTFS re-download failed (%s); using stale cached copy", system, exc
            )
    try:
        data = _parse_system(zip_path)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        # Unusable cache: corrupt zip, a missing member (stops/trips/shapes), or
        # undecodable text. Refetch once rather than staying wedged.
        logger.warning("Cached %s static GTFS is unusable; re-downloading", system)
        zip_path.unlink(missing_ok=True)
        try:
            await _download_zip(system)
            data = _parse_system(zip_path)
        except Exception as exc:
            logger.warning("%s static GTFS unavailable (%s); skipping", system, exc)
            return None
    logger.info(
        "Loaded %s static GTFS: %d stops, %d trips, %d shapes",
        system,
        len(data["stops"]),
        len(data["trips"]),
        len(data["shapes"]),
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
    """
    systems = list(RAILROAD_STATIC_URLS)
    results = await asyncio.gather(*(_load_one(system) for system in systems))
    return dict(zip(systems, results))
