"""Download and load the static GTFS for NYC Ferry.

The phase-14a data foundation: NYC Ferry's stops, routes, shapes, and trips
loaded into memory. The schema is FLATTER than PATH's: stops have no
location_type and no parent_station (no child/parent split exists or is
needed), so _parse_stops returns a single stops table. Modeled on
path_static otherwise (opaque short-numeric stop ids, modal shape selection
per route and direction). main.py's lifespan warms this in the background and
stores the results on their own app.state fields (ferry_stops, ferry_routes):
ferry stop ids are short numerics (e.g. 18) that collide with MTA and PATH
numeric ids, so they are never merged into any shared namespace (the same
system-scoped discipline as the alerts join).

The static feed is published by Connexionz on behalf of NYC Ferry
(agency_name "NYC Ferry", agency_url ferry.nyc).

REALTIME CONSTRAINT that shapes this static loader (14b consumes it, does not
re-parse it): NYC Ferry's realtime trip descriptors carry an EMPTY route_id,
so a realtime train's route can only be derived by joining its trip_id through
trips.txt. The parsed trips table therefore keys trip_id -> {route_id, ...},
and a golden pins that the trip -> route derivation resolves; 14b reads
route_id straight off this table rather than re-parsing trips.txt.
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
from collections import Counter, defaultdict
from pathlib import Path
from typing import IO

import httpx

from static_routes import fold_stop_routes

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = PROJECT_ROOT / "data" / "gtfs_static"

# Verified 2026-07-09: the utility URL 302-REDIRECTS to the resource zip
# (~44 KB) on the same host, over https end-to-end. (An earlier note claimed
# only http:// was reachable; that was an artifact of a probe environment that
# blocked plain http, not of the server.) httpx does NOT follow redirects by
# default, so _download_zip sets follow_redirects=True; requesting the final
# resource URL directly would also work, but following keeps the loader honest
# if Connexionz moves the target.
FERRY_STATIC_URL = "https://nycferry.connexionz.net/rtt/public/utility/gtfs.aspx"
FERRY_STATIC_ZIP = _STATIC_DIR / "gtfs_ferry.zip"

# Re-download the static GTFS when the cached copy is older than this, the same
# policy as PATH and the railroads: the publisher republishes a few times a
# year (feed_info version dates), and stop coordinates change rarely.
MAX_AGE_DAYS = 30

# Whole-transfer deadline: httpx's timeout is per socket read, so this bounds
# the WHOLE transfer, stopping a trickling response from stalling the download
# indefinitely. The load runs in a background warmup task (main.py
# _warm_ferry_static) that retries on failure, so this is a per-attempt
# ceiling, not a startup gate. The zip is ~44 KB, so 120s is very generous.
_DOWNLOAD_DEADLINE_S = 120


async def _download_zip() -> None:
    """Download the NYC Ferry GTFS zip atomically into its cache path."""
    dest = FERRY_STATIC_ZIP
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Sweep temp files orphaned by an earlier hard kill mid-download. Scoped to
    # this stem so it can't disturb the other systems' archives in the same dir.
    for stale in dest.parent.glob(f"{dest.stem}.*.part"):
        stale.unlink(missing_ok=True)
    # Unique temp name in the same dir so the final rename stays atomic and
    # concurrent downloads are last-writer-wins.
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f"{dest.stem}.", suffix=".part")
    os.close(fd)
    tmp = Path(tmp_name)
    logger.info("Downloading NYC Ferry static GTFS from %s", FERRY_STATIC_URL)
    try:
        # follow_redirects=True is REQUIRED here (not just tidy): the utility
        # URL 302s to the resource zip, and httpx returns the 302 unfollowed by
        # default, which raise_for_status treats as success and yields an empty
        # body. asyncio.timeout bounds the whole transfer (httpx's timeout is
        # per socket read) so a trickling response can't stall indefinitely.
        async with asyncio.timeout(_DOWNLOAD_DEADLINE_S):
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                async with client.stream("GET", FERRY_STATIC_URL) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Downloaded NYC Ferry static GTFS to %s", dest)


# The parsers take file-like binary streams (what ZipFile.open yields) so tests
# can inject fixture tables via io.BytesIO without touching disk or network.


def _parse_stops(raw: IO[bytes]) -> dict[str, dict]:
    """stops.txt -> stop_id -> {id, name, lat, lon, wheelchair}.

    NYC Ferry stops are FLAT: no location_type, no parent_station, so every row
    with a usable id and coordinate is a marker (there is no parent/child split
    like PATH). wheelchair_boarding is carried through as a bool (GTFS 1 means
    accessible; blank/other means unknown) because it is display-relevant to a
    later phase. Rows with a blank stop_id or a missing/malformed coordinate
    are skipped; first-writer-wins on a duplicate stop_id.
    """
    stops: dict[str, dict] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        stop_id = (row.get("stop_id") or "").strip()
        if not stop_id or stop_id in stops:
            continue
        try:
            lat = float(row.get("stop_lat") or "")
            lon = float(row.get("stop_lon") or "")
        except ValueError:
            continue
        stops[stop_id] = {
            "id": stop_id,
            "name": (row.get("stop_name") or "").strip() or None,
            "lat": lat,
            "lon": lon,
            # Only "1" (accessible) is affirmative; blank/"0"/"2" all read as
            # "no affirmative accessibility info", which the bool captures.
            "wheelchair": (row.get("wheelchair_boarding") or "").strip() == "1",
        }
    return stops


def _parse_routes(raw: IO[bytes]) -> dict[str, dict]:
    """routes.txt -> route_id -> {long_name, short_name, color, text_color},
    each a stripped string or None when blank. Rows with no route_id are
    skipped; first-writer-wins on a duplicate route_id.

    Like PATH, NYC Ferry's route_color/route_text_color ARE read: all nine
    routes ship rider-recognizable colors and rider-facing route_long_names,
    and the project has no palette of its own for them.
    """
    routes: dict[str, dict] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        route_id = (row.get("route_id") or "").strip()
        if not route_id or route_id in routes:
            continue
        routes[route_id] = {
            "long_name": (row.get("route_long_name") or "").strip() or None,
            "short_name": (row.get("route_short_name") or "").strip() or None,
            "color": (row.get("route_color") or "").strip() or None,
            "text_color": (row.get("route_text_color") or "").strip() or None,
        }
    return routes


def _parse_shapes(raw: IO[bytes]) -> dict[str, list]:
    """shapes.txt -> shape_id -> [[lat, lon], ...] ordered by shape_pt_sequence,
    coords rounded to 5 decimals (~1 m, matching every other system's shape
    rounding). Rows with a blank shape_id or a malformed point are skipped."""
    raw_points: dict[str, list] = defaultdict(list)
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


def _parse_trips(raw: IO[bytes]) -> dict[str, dict]:
    """trips.txt -> trip_id -> {route_id, direction_id, shape_id, headsign},
    each a stripped string or None when blank. Rows with no trip_id are
    skipped; first-writer-wins on a duplicate trip_id.

    Two consumers: build_ferry_route_shapes groups these for the modal shape
    selection, AND (the 14a-specific reason this table is returned from
    load_ferry_static, not consumed internally only) 14b joins a realtime
    train's trip_id here to recover its route_id, because the realtime feed's
    trip descriptors carry an EMPTY route_id. So route_id per trip is the
    load-bearing field; a golden pins that the derivation resolves.
    """
    trips: dict[str, dict] = {}
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


def _parse_stop_times(raw: IO[bytes]) -> dict[str, list[str]]:
    """stop_times.txt -> trip_id -> [stop_id]. Only membership matters for the
    routes-per-station index (which stops a trip visits), so rows are collected
    unsorted. NYC Ferry stops are FLAT (no parent/child split), so these ids join
    straight to the stop markers. Rows with a blank trip_id/stop_id are skipped."""
    trip_stops: dict[str, list[str]] = defaultdict(list)
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        trip_id = (row.get("trip_id") or "").strip()
        stop_id = (row.get("stop_id") or "").strip()
        if not trip_id or not stop_id:
            continue
        trip_stops[trip_id].append(stop_id)
    return dict(trip_stops)


def derive_ferry_stop_routes(
    trips: dict[str, dict], stop_times: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Pure: stop_id -> sorted [route_id] serving it. Ferry stops are flat (no
    parent/child fold), so the stop_times stop ids join directly. Delegates the
    join to static_routes.fold_stop_routes after pulling route_id out of each
    trip record. No zip read, so the warmup builds it from app.state.ferry_static
    without re-parsing, like build_ferry_route_shapes."""
    trip_routes = {trip_id: t.get("route_id") for trip_id, t in trips.items()}
    return fold_stop_routes(trip_routes, stop_times)


def _parse_zip(zip_path: Path) -> dict:
    """Parse the NYC Ferry GTFS zip into {stops, trips, shapes, routes,
    stop_times} in a single open. stops/trips/shapes are required members (a
    missing one raises KeyError for load_ferry_static to recover from);
    routes.txt and stop_times.txt are optional and yield an empty table when
    absent, the same rider-facing-convenience leniency as PATH and the
    railroads. (The committed trim carries no stop_times.txt yet, so the index
    comes up empty from the fixture; the live feed does carry it. See the
    routes-per-station note in the H5 handoff.)"""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("stops.txt") as raw:
            stops = _parse_stops(raw)
        with zf.open("trips.txt") as raw:
            trips = _parse_trips(raw)
        with zf.open("shapes.txt") as raw:
            shapes = _parse_shapes(raw)
        try:
            member = zf.open("routes.txt")
        except KeyError:
            routes: dict[str, dict] = {}
        else:
            with member as raw:
                routes = _parse_routes(raw)
        try:
            member = zf.open("stop_times.txt")
        except KeyError:
            stop_times: dict[str, list[str]] = {}
        else:
            with member as raw:
                stop_times = _parse_stop_times(raw)
    return {
        "stops": stops,
        "trips": trips,
        "shapes": shapes,
        "routes": routes,
        "stop_times": stop_times,
    }


# A second direction's modal shape is kept only if it adds more than this
# fraction of new geometry, the same threshold and point-set test as PATH and
# the railroad/subway route lines: the reverse-direction shape shares all its
# points and collapses, while a direction that genuinely diverges survives.
_MIN_NEW_GEOMETRY = 0.05


def build_ferry_route_shapes(
    trips: dict[str, dict], shapes: dict[str, list], routes: dict[str, dict] | None = None
) -> list[dict]:
    """Per-route representative polylines for NYC Ferry.

    Returns [{"id": route_id, "name", "color", "text_color", "shape"}, ...]
    sorted by route id, where "shape" is the kept polylines ([[lat, lon], ...]
    lists). A pure transform over the already-parsed tables (no zip read, no
    network), so the lifespan builds it from app.state.ferry_static without
    re-parsing. name/color/text_color come from the routes table (null when
    absent).

    Identical modal-shape discipline to build_path_route_shapes: pick the MOST
    COMMON shape_id per (route_id, direction_id) by trip count (five NYC Ferry
    routes carry multiple shape variants per direction, East River up to 4;
    the variants are short-run or reroute patterns, so the modal shape is the
    rider-facing line). Ties break to the smallest shape_id for determinism
    across restarts. The per-direction modal shapes then go through the same
    added-geometry dedup, so a reverse-direction shape (same point set,
    opposite order) collapses to one polyline while a genuinely divergent
    direction survives.

    A route whose modal shapes are all missing or degenerate (<2 points) is
    dropped: it has no line to draw.
    """
    # (route_id -> direction_id -> shape_id -> trip count). direction_id None
    # (blank in trips.txt) is its own group rather than being folded into a
    # real direction, so a partially-tagged feed cannot skew a modal count.
    counts: dict[str, dict[str | None, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for trip in trips.values():
        route_id, shape_id = trip.get("route_id"), trip.get("shape_id")
        if route_id and shape_id:
            counts[route_id][trip.get("direction_id")][shape_id] += 1

    out: list[dict] = []
    for route_id in sorted(counts):
        modal: list[list] = []
        # Sort directions (None last) so the modal list is deterministic.
        for direction in sorted(counts[route_id], key=lambda d: (d is None, d or "")):
            tally = counts[route_id][direction]
            # Highest trip count wins; ties break to the smallest shape_id.
            shape_id = min(tally, key=lambda s: (-tally[s], s))
            points = shapes.get(shape_id) or []
            if len(points) >= 2:
                modal.append(points)
        # Longest first so the dedup keeps the fullest line (the sort is stable,
        # so equal-length directions keep their direction order).
        modal.sort(key=len, reverse=True)
        kept: list[list] = []
        covered: set[tuple] = set()
        for polyline in modal:
            point_set = {tuple(p) for p in polyline}
            if len(point_set - covered) / max(len(point_set), 1) > _MIN_NEW_GEOMETRY:
                kept.append(polyline)
                covered |= point_set
        if not kept:
            continue
        info = (routes or {}).get(route_id) or {}
        out.append(
            {
                "id": route_id,
                "name": info.get("long_name") or info.get("short_name"),
                "color": info.get("color"),
                "text_color": info.get("text_color"),
                "shape": kept,
            }
        )
    return out


async def load_ferry_static() -> dict:
    """Ensure/refresh the NYC Ferry GTFS zip and parse it.

    Returns {stops, trips, shapes, routes} on success, or {} on any failure.
    Lenient by design, matching load_path_static: a download or parse failure
    logs and yields an EMPTY result rather than raising, because the warmup
    task in main.py owns retrying. NYC Ferry is a single system, so the caller
    treats an empty result as the whole group failing.
    """
    zip_path = FERRY_STATIC_ZIP
    fresh = zip_path.exists() and time.time() - zip_path.stat().st_mtime < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            await _download_zip()
        except Exception as exc:
            if not zip_path.exists():
                logger.warning("NYC Ferry static GTFS download failed (%s); no cached copy", exc)
                return {}
            logger.warning(
                "NYC Ferry static GTFS re-download failed (%s); using stale cached copy", exc
            )
    try:
        data: dict | None = _parse_zip(zip_path)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        data = None
    # data is None for a corrupt/missing-member/undecodable cache, and a valid
    # parse yielding zero stops is treated as unusable too: the warmup marks the
    # single-system ferry group "failed" on empty stops, and without
    # invalidating here an already-fresh cache would never be re-downloaded, so
    # a transiently-empty upstream would wedge the group until MAX_AGE_DAYS
    # forced a refetch even after it self-corrected. Refetching now lets the
    # next warm retry pick up a corrected feed. (This is the empty-valid-cache
    # one-time re-download 13a's review hardened.)
    if data is None or not data["stops"]:
        logger.warning("Cached NYC Ferry static GTFS is unusable or empty; re-downloading")
        zip_path.unlink(missing_ok=True)
        try:
            await _download_zip()
            data = _parse_zip(zip_path)
        except Exception as exc:
            logger.warning("NYC Ferry static GTFS unavailable (%s); skipping", exc)
            return {}
    logger.info(
        "Loaded NYC Ferry static GTFS: %d stops, %d routes, %d shapes, %d trips",
        len(data["stops"]),
        len(data["routes"]),
        len(data["shapes"]),
        len(data["trips"]),
    )
    return data
