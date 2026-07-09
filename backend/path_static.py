"""Download and load the static GTFS for PATH (Port Authority Trans-Hudson).

The phase-13a data foundation: PATH's stops, routes, shapes, and trips loaded
into memory. PATH's schema is close to the railroads (opaque plain stop ids
with no N/S suffix), so this module is modeled on railroad_static, not on the
subway helpers in static_data. main.py's lifespan warms this in the background
and stores the results on their own app.state fields (path_stops, path_routes):
PATH stop ids are numeric and collide with MTA numeric ids, so they are never
merged into any shared namespace (the same system-scoped discipline as the
alerts join).

The static feed is published by Trillium on behalf of the Port Authority of
New York and New Jersey (PANYNJ) and is subject to PANYNJ license terms.

PATH has no official GTFS-Realtime feed; a later phase will consume a community
bridge feed. REALTIME TRIP IDS ARE UNSTABLE: the bridge feed was probed live on
2026-07-05 and its trip ids showed 100% churn across upstream refreshes,
including 29 of 50 trains whose ids changed while their arrival payloads were
byte-identical. Nothing in this module or any later PATH phase may key anything
on PATH trip ids. The static trips table parsed here is used ONLY as internal
grouping input (modal shape selection, and picking the representative trip for
the 13d station order) and is never joined to realtime;
feeds.match_path_identities synthesizes train identity from stable fields
(route, direction, next stop, nearest arrival time) instead, walking this
module's build_path_station_order output for its advance matching.

The realtime bridge references PARENT station ids, so the parent stations
(location_type=1) are the marker set; the child platform to parent map is built
now because later phases need it.
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

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STATIC_DIR = PROJECT_ROOT / "data" / "gtfs_static"

# Verified 2026-07-05: 200, ~1.2 MB, application/zip. The http:// variant
# redirects; use https. The module and tests never depend on the URL resolving.
PATH_STATIC_URL = "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip"
PATH_STATIC_ZIP = _STATIC_DIR / "gtfs_path.zip"

# Re-download the static GTFS when the cached copy is older than this, the same
# policy as the railroads: Trillium republishes a few times a year and stop
# coordinates change rarely.
MAX_AGE_DAYS = 30

# Whole-transfer deadline: httpx's timeout is per socket read, so this bounds
# the WHOLE transfer, stopping a trickling response from stalling the download
# indefinitely. The load runs in a background warmup task (main.py
# _warm_path_static) that retries on failure, so this is a per-attempt ceiling,
# not a startup gate. The zip is ~1.2 MB, so 120s is generous.
_DOWNLOAD_DEADLINE_S = 120


async def _download_zip() -> None:
    """Download the PATH GTFS zip atomically into its cache path."""
    dest = PATH_STATIC_ZIP
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
    logger.info("Downloading PATH static GTFS from %s", PATH_STATIC_URL)
    try:
        # httpx's timeout is per socket read; bound the whole transfer so a
        # trickling response can't stall indefinitely.
        async with asyncio.timeout(_DOWNLOAD_DEADLINE_S):
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                async with client.stream("GET", PATH_STATIC_URL) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Downloaded PATH static GTFS to %s", dest)


# The parsers take file-like binary streams (what ZipFile.open yields) so tests
# can inject fixture tables via io.BytesIO without touching disk or network.


def _parse_stops(raw: IO[bytes]) -> tuple[dict[str, dict], dict[str, str]]:
    """stops.txt -> (parents, child_to_parent).

    parents: stop_id -> {id, name, lat, lon} for parent stations only
    (location_type=1). The realtime bridge references parent station ids, so
    parents are the marker set; child platforms never become markers. Parent
    rows with a missing or malformed coordinate are skipped.

    child_to_parent: child stop_id -> parent stop_id for every row carrying a
    parent_station. Built now because later phases need to fold platform-level
    references up to the station level; a child row needs no usable coordinate
    to be mapped.
    """
    parents: dict[str, dict] = {}
    child_to_parent: dict[str, str] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        stop_id = (row.get("stop_id") or "").strip()
        if not stop_id:
            continue
        parent = (row.get("parent_station") or "").strip()
        if parent:
            child_to_parent[stop_id] = parent
        if (row.get("location_type") or "").strip() != "1":
            continue
        try:
            lat = float(row.get("stop_lat") or "")
            lon = float(row.get("stop_lon") or "")
        except ValueError:
            continue
        parents[stop_id] = {
            "id": stop_id,
            "name": (row.get("stop_name") or "").strip() or None,
            "lat": lat,
            "lon": lon,
        }
    return parents, child_to_parent


def _parse_trips(raw: IO[bytes]) -> dict[str, dict]:
    """trips.txt -> trip_id -> {route_id, direction_id, shape_id, headsign},
    each a stripped string or None when blank. Rows with no trip_id are skipped;
    first-writer-wins on a duplicate trip_id.

    The trip_id keys here are STATIC schedule ids used only to group trips for
    the modal shape selection; they are never joined to realtime (the bridge
    feed's trip ids are unstable, see the module docstring).
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


def _parse_shapes(raw: IO[bytes]) -> dict[str, list]:
    """shapes.txt -> shape_id -> [[lat, lon], ...] ordered by shape_pt_sequence,
    coords rounded to 5 decimals (~1 m, matching the subway/railroad shape
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


def _parse_stop_times(raw: IO[bytes]) -> dict[str, list[str]]:
    """stop_times.txt -> trip_id -> stop ids ordered by stop_sequence.

    The stop ids here are CHILD PLATFORM ids (7817xx, 7947xx), NOT the parent
    station ids the realtime bridge uses; callers must resolve them through
    the child_to_parent map before joining anything realtime-facing (see
    build_path_station_order). Rows with a blank trip_id/stop_id or a
    non-integer stop_sequence are skipped; a duplicate (trip, sequence) keeps
    the first row seen, matching the first-writer-wins discipline of the
    other parsers.
    """
    raw_seqs: dict[str, dict[int, str]] = defaultdict(dict)
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        trip_id = (row.get("trip_id") or "").strip()
        stop_id = (row.get("stop_id") or "").strip()
        if not trip_id or not stop_id:
            continue
        try:
            seq = int((row.get("stop_sequence") or "").strip())
        except ValueError:
            continue
        raw_seqs[trip_id].setdefault(seq, stop_id)
    return {
        trip_id: [stop_id for _seq, stop_id in sorted(seqs.items())]
        for trip_id, seqs in raw_seqs.items()
    }


def build_path_station_order(
    trips: dict[str, dict],
    stop_times: dict[str, list[str]],
    child_to_parent: dict[str, str],
) -> dict[tuple[str, str | None], list[str]]:
    """Ordered PARENT-station list per (route_id, direction_id), for the 13d
    advance matcher's successor relation (feeds.match_path_identities).

    For each (route_id, direction_id) the representative trip is the one with
    the MOST stop_times rows (the full-length run; short-turn variants stop
    early), ties broken to the smallest trip_id so the pick is deterministic
    across regenerations. Every sequence entry is resolved through
    child_to_parent BEFORE the order is built: stop_times.txt lists child
    platform ids, which the realtime bridge never uses, so an unresolved
    sequence would contain zero feed-visible ids and the matcher's
    predecessor lookups would all miss. Entries with no parent mapping are
    dropped, and repeat visits keep only the first occurrence (a well-formed
    run visits each station once; several platforms of one station must not
    make the station its own predecessor).

    Orders shorter than 2 stations are dropped: a successor relation needs at
    least one edge. A pure transform (no zip read, no network), like
    build_path_route_shapes, so the lifespan builds it from app.state
    without re-parsing.
    """
    best: dict[tuple[str, str | None], tuple[int, str]] = {}
    for trip_id, seq in stop_times.items():
        trip = trips.get(trip_id)
        if not trip or not trip.get("route_id"):
            continue
        key = (trip["route_id"], trip.get("direction_id"))
        rank = (-len(seq), trip_id)  # most stops wins; ties to smallest trip_id
        if key not in best or rank < (-best[key][0], best[key][1]):
            best[key] = (len(seq), trip_id)

    order: dict[tuple[str, str | None], list[str]] = {}
    for key, (_length, trip_id) in best.items():
        parents: list[str] = []
        seen: set[str] = set()
        for stop_id in stop_times[trip_id]:
            parent = child_to_parent.get(stop_id)
            if parent and parent not in seen:
                parents.append(parent)
                seen.add(parent)
        if len(parents) >= 2:
            order[key] = parents
    return order


def _parse_routes(raw: IO[bytes]) -> dict[str, dict]:
    """routes.txt -> route_id -> {long_name, short_name, color, text_color},
    each a stripped string or None when blank. Rows with no route_id are
    skipped; first-writer-wins on a duplicate route_id.

    Unlike the railroads (own palette), PATH's route_color/route_text_color ARE
    read: the seven PATH routes ship rider-recognizable colors and the project
    has no palette of its own for them. route_desc is deliberately NEVER read:
    the live feed carries stale 2020 Sandy-closure text on route 862.
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


def _parse_zip(zip_path: Path) -> dict:
    """Parse the PATH GTFS zip into {stops, child_to_parent, trips, shapes,
    routes, stop_times} in a single open. stops/trips/shapes are required
    members (a missing one raises KeyError for load_path_static to recover
    from); routes.txt is optional and yields an empty table when absent, the
    same rider-facing-convenience leniency as the railroads. stop_times.txt
    is optional too, but for a different reason: a cached zip downloaded
    before 13d lacks the member, and degrading to an empty table (which only
    disables the advance-match branch of the identity matcher) beats wedging
    the whole PATH group on an otherwise good cache."""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open("stops.txt") as raw:
            stops, child_to_parent = _parse_stops(raw)
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
        "child_to_parent": child_to_parent,
        "trips": trips,
        "shapes": shapes,
        "routes": routes,
        "stop_times": stop_times,
    }


# A second direction's modal shape is kept only if it adds more than this
# fraction of new geometry, the same threshold and point-set test as the
# railroad and subway route lines: the reverse-direction shape shares all its
# points and collapses, while a direction that genuinely diverges survives.
_MIN_NEW_GEOMETRY = 0.05


def build_path_route_shapes(
    trips: dict[str, dict], shapes: dict[str, list], routes: dict[str, dict] | None = None
) -> list[dict]:
    """Per-route representative polylines for PATH.

    Returns [{"id": route_id, "name", "color", "text_color", "shape"}, ...]
    sorted by route id, where "shape" is the kept polylines ([[lat, lon], ...]
    lists). A pure transform over the already-parsed tables (no zip read, no
    network), so the lifespan builds it from app.state.path_static without
    re-parsing. name/color/text_color come from the routes table (null when
    absent); route_desc never surfaces (stale text, see _parse_routes).

    Unlike build_railroad_route_shapes (which keeps every shape variant that
    adds geometry), PATH picks the MOST COMMON shape_id per (route_id,
    direction_id) by trip count. Some PATH routes carry many shape ids (1024
    has 18, 862 has 10), but the variants are short-turn or track-work
    patterns; the modal shape is the rider-facing line, so the variants are
    dropped rather than deduped. Ties break to the smallest shape_id so the
    pick is deterministic across process restarts. The per-direction modal
    shapes then go through the same added-geometry dedup as the railroads, so
    a reverse-direction shape (same point set, opposite order) collapses to
    one polyline while a genuinely divergent direction survives.

    A route whose modal shapes are all missing or degenerate (<2 points) is
    dropped: it has no line to draw. Routes are grouped via trips.txt, never
    via realtime ids (PATH bridge trip ids are unstable, see the module
    docstring).
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
        # Longest first so the dedup keeps the fullest line, matching the
        # railroad builder's discipline (the sort is stable, so equal-length
        # directions keep their direction order).
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


async def load_path_static() -> dict:
    """Ensure/refresh the PATH GTFS zip and parse it.

    Returns {stops, child_to_parent, trips, shapes, routes, stop_times} on
    success, or {} on any failure. Lenient by design, matching load_railroad_static: a
    download or parse failure logs and yields an EMPTY result rather than
    raising, because the warmup task in main.py owns retrying. PATH is a
    single system, so the caller treats an empty result as the whole group
    failing (there is no other system to stay up for).
    """
    zip_path = PATH_STATIC_ZIP
    fresh = zip_path.exists() and time.time() - zip_path.stat().st_mtime < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            await _download_zip()
        except Exception as exc:
            if not zip_path.exists():
                logger.warning("PATH static GTFS download failed (%s); no cached copy", exc)
                return {}
            logger.warning("PATH static GTFS re-download failed (%s); using stale cached copy", exc)
    try:
        data: dict | None = _parse_zip(zip_path)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        data = None
    # data is None for a corrupt/missing-member/undecodable cache, and a valid
    # parse that yields zero parent stations is treated as unusable too: the
    # warmup marks the single-system PATH group "failed" on empty stops (main.py
    # _warm_path_static), and without invalidating here an already fresh cache
    # would never be re-downloaded, so a transiently-empty upstream would wedge
    # the group until MAX_AGE_DAYS forced a refetch even after it self-corrected.
    # Refetching now lets the next warm retry pick up a corrected feed.
    if data is None or not data["stops"]:
        logger.warning("Cached PATH static GTFS is unusable or empty; re-downloading")
        zip_path.unlink(missing_ok=True)
        try:
            await _download_zip()
            data = _parse_zip(zip_path)
        except Exception as exc:
            logger.warning("PATH static GTFS unavailable (%s); skipping", exc)
            return {}
    if not data["stop_times"]:
        # Advance matching (13d) degrades to same-stop-only until the cache
        # refreshes with a zip that carries the member; worth one line so an
        # operator can tell degraded matching from a code bug.
        logger.warning("PATH static GTFS has no stop_times.txt; advance matching disabled")
    logger.info(
        "Loaded PATH static GTFS: %d parent stations, %d child platforms, "
        "%d trips, %d shapes, %d routes, %d trips with stop_times",
        len(data["stops"]),
        len(data["child_to_parent"]),
        len(data["trips"]),
        len(data["shapes"]),
        len(data["routes"]),
        len(data["stop_times"]),
    )
    return data
