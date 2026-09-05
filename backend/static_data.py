"""Download and load the MTA static subway GTFS (station coordinates)."""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import zipfile
from collections import defaultdict
from typing import IO

import env_seams
from static_routes import fold_stop_routes
from static_shared import (
    StaticValidationError,
    cached_archive_is_valid,
    parse_member,
    require_members,
    require_parsed,
    staged_fetch,
)

logger = logging.getLogger(__name__)

# The data root is overridable (C6) so the contract tier can point the whole
# cache at a tmp directory; unset, this is the same path it always was.
DATA_DIR = env_seams.directory("DATA_DIR", "data")
SUBWAY_GTFS_ZIP = DATA_DIR / "gtfs_static" / "gtfs_subway.zip"
# Overridable (C6), used whole. The contract tier publishes archives from its own
# simulator so a rejected publication and the finding-4 cold start can be driven
# against the real warmup rather than a monkeypatched loader.
SUBWAY_GTFS_URL = env_seams.url(
    "SUBWAY_GTFS_URL", "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"
)

# Re-download the static GTFS when the cached copy is older than this. The MTA
# republishes it a few times a year; station coordinates change rarely.
MAX_AGE_DAYS = 30

# THE RULE FOR THIS SET, and it is the same rule in all four loaders: require a
# member when its absence is a loss a rider would SEE, so that keeping the
# last-known-good is better than promoting the reduced archive. Requiring more than
# that is not free, because at cold start with no cache a required member missing
# means the system is absent from the map entirely rather than merely reduced.
#
# stops.txt places every marker and every train. shapes.txt draws every subway
# route line.
#
# trips.txt and stop_times.txt are deliberately NOT here even though the subway
# reads both. Their only subway consumer is load_subway_station_routes (the H5
# routes-per-station popup enrichment), which already swallows any problem and
# returns an empty index, and the map is fully functional without it: the route
# lines come from the shape_id regex, not from trips.txt. An earlier version of
# this list required trips.txt while excluding stop_times.txt on exactly the
# "it only feeds enrichment" grounds that apply to both, which was inconsistent;
# the visible-loss rule above is the one that actually distinguishes them. PATH
# and ferry DO require stop_times.txt, because there it drives advance matching
# and the dock/route alert join (13d, H5) rather than an enrichment.
_REQUIRED_MEMBERS = ("stops.txt", "shapes.txt")


def validate_subway_archive(zf: zipfile.ZipFile) -> None:
    """Can we serve the subway from this archive? Raises StaticValidationError if not."""
    require_members(zf, _REQUIRED_MEMBERS)
    # THIRD-AUDIT FINDING 4'S GATE. A stops.txt with headers and no usable rows
    # parses to {} and used to be promoted to "ready" forever: every station gone
    # from the map, nothing retrying, because the archive was structurally fine.
    # Running the loader's own parser (not a generic row count) makes this the same
    # question the load asks.
    #
    # THE PARENT-STATION PREDICATE, not the all-rows one, and that choice is the
    # gate. stops.txt yields the platform-level ids that place trains, while the
    # clickable station markers come from the location_type=1 PARENT rows only.
    # Gating on all rows let a stops.txt of nothing but platform rows through:
    # trains placed, every station marker gone, promoted to ready, nothing
    # retrying, which is finding 4's exact symptom reached by a different table.
    # This check subsumes the all-rows one it replaced, because every parent row is
    # also a row _parse_stops_rows keeps, so one gate carries both properties and a
    # second would be dead weight (mutation testing is what showed it: with this
    # line present, deleting the all-rows check changed nothing).
    require_parsed(
        lambda: parse_member(zf, "stops.txt", _parse_stations_rows),
        "stops.txt",
        "parent stations",
    )


async def _download_zip() -> None:
    """Stage, validate, then promote the subway archive (see static_shared)."""
    await staged_fetch(
        SUBWAY_GTFS_URL,
        SUBWAY_GTFS_ZIP,
        validate_subway_archive,
        key="subway",
        label="static subway GTFS",
    )


def _parse_stops_rows(raw: IO[bytes]) -> dict[str, dict]:
    """stops.txt rows -> stop_id -> name/lat/lon.

    Split out from _parse_stops so the validator can run this exact parse over a
    STAGED archive, which has no cache path to read from yet.

    Realtime feeds reference platform-level stop ids (e.g. "R16N"); stops.txt
    contains those alongside parent stations, all with coordinates. Rows with
    missing or malformed coordinates are skipped.
    """
    stops: dict[str, dict] = {}
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


def _parse_stations_rows(raw: IO[bytes]) -> dict[str, dict]:
    """stops.txt rows -> PARENT station_id -> name/lat/lon (location_type == 1 only).

    Split out of load_subway_stations for the same reason _parse_stops_rows was
    split out of _parse_stops: validate_subway_archive has to ask this exact
    question of a STAGED archive, and a predicate the validator reimplements is a
    predicate that drifts from the loader.
    """
    stations: dict[str, dict] = {}
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        if (row.get("location_type") or "").strip() != "1":
            continue
        station_id = (row.get("stop_id") or "").strip()
        if not station_id:
            continue
        try:
            lat = float(row.get("stop_lat") or "")
            lon = float(row.get("stop_lon") or "")
        except ValueError:
            continue
        stations[station_id] = {
            "name": (row.get("stop_name") or "").strip() or None,
            "lat": lat,
            "lon": lon,
        }
    return stations


def _parse_stops() -> dict[str, dict]:
    """Read stops.txt straight out of the cached zip: stop_id -> name/lat/lon."""
    with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
        return parse_member(zf, "stops.txt", _parse_stops_rows)


async def load_subway_stops() -> dict[str, dict]:
    """Load the station lookup, downloading the static GTFS if missing or stale.

    Falls back to the cached copy when a re-download fails or publishes something
    unservable; raises only if no usable copy can be obtained at all, which is the
    warmup's signal to stay failed-and-retrying rather than reach ready.
    """
    # FRESH NOW MEANS VALID AND RECENT, not recent alone (C5). A cached archive
    # that fails its own validator (pre-C5 bytes from the era when a bad
    # publication could land, disk corruption, a hand-placed file) is treated as
    # absent, which forces a fresh staged download instead of parsing garbage.
    # This replaces the old parse-then-recover arm below it: nothing can reach the
    # parse except an archive that already passed the same gates.
    usable = SUBWAY_GTFS_ZIP.exists() and cached_archive_is_valid(
        SUBWAY_GTFS_ZIP, validate_subway_archive
    )
    fresh = usable and time.time() - SUBWAY_GTFS_ZIP.stat().st_mtime < MAX_AGE_DAYS * 86400
    if not fresh:
        try:
            await _download_zip()
        except Exception as exc:
            if not usable:
                # No valid cache AND a failing download: failed-and-retrying, never
                # ready. Raising is what puts the warmup on the R3 rung schedule.
                raise
            # SERVING OLD WHILE NEW IS BAD, deliberately, INCLUDING PAST
            # MAX_AGE_DAYS. The age policy exists to pick up upstream's
            # corrections, so it yields to validity: an archive that is stale
            # because upstream keeps publishing garbage is a reason to keep
            # serving what works, not to serve nothing. The failure is not
            # silent, and this state is not reachable by skipping a download:
            # a download was attempted and failed, staged_fetch recorded why,
            # and /api/status publishes last_download_error, last_promoted_at
            # and the failure count beside this group's state.
            logger.warning("Static GTFS re-download failed (%s); using the cached copy", exc)
    stops = _parse_stops()
    if not stops:
        # Backstop for finding 4. validate_subway_archive runs THIS parse over the
        # staged and the cached archive, so an empty result cannot get this far;
        # the check stays because the invariant belongs to the loader, and a
        # future loosening of the validator must fail loudly here (the warmup
        # retries) rather than promote a stationless map to ready.
        raise StaticValidationError("stops.txt yielded no usable stops")
    logger.info("Loaded %d subway stops from static GTFS", len(stops))
    return stops


def load_subway_stations() -> dict[str, dict]:
    """Parent stations (GTFS location_type == 1) from the cached static GTFS:
    station_id -> {name, lat, lon}.

    These carry their own coordinates and are the clickable station markers;
    realtime platform stop ids map onto them by stripping the trailing N/S
    (see feeds._platform_direction). Station markers are optional UI, so any
    parse problem logs and returns {} rather than raising.
    """
    try:
        with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
            stations = parse_member(zf, "stops.txt", _parse_stations_rows)
        logger.info("Loaded %d subway stations from static GTFS", len(stations))
        return stations
    except Exception as exc:
        logger.warning("Could not load subway stations (%s); skipping markers", exc)
        return {}


# A shape variant is kept only if it adds more than this fraction of new
# geometry vs. variants already kept for the route. Express/local variants
# share track geometry almost entirely; branches (e.g. the A's Rockaway legs)
# differ substantially and survive the cut.
_MIN_NEW_GEOMETRY = 0.05

# Subway shape_ids look like "A..N04R" / "GS.N01R": route prefix, dots, then
# the direction letter. We keep one direction per route (N and S trace the
# same tracks at map scale).
_SHAPE_ID_RE = re.compile(r"^([A-Za-z0-9]+)\.\.?N")


def load_subway_route_shapes() -> list[dict]:
    """Parse shapes.txt from the cached static GTFS into drawable polylines.

    Returns [{"route": "A", "polylines": [[[lat, lon], ...], ...]}, ...] with
    coordinates rounded to 5 decimals (~1 m). Assumes the zip exists (call
    after load_subway_stops succeeds). Route lines are decorative, so any
    parse problem logs and returns [] rather than raising.
    """
    try:
        shapes: dict[str, list] = defaultdict(list)
        with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
            with zf.open("shapes.txt") as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
                for row in reader:
                    try:
                        shapes[row["shape_id"]].append(
                            (
                                int(row["shape_pt_sequence"]),
                                round(float(row["shape_pt_lat"]), 5),
                                round(float(row["shape_pt_lon"]), 5),
                            )
                        )
                    except (KeyError, ValueError, TypeError):
                        continue  # malformed row

        by_route: dict[str, list[list]] = defaultdict(list)
        for shape_id, points in shapes.items():
            match = _SHAPE_ID_RE.match(shape_id)
            if not match:
                continue
            points.sort()
            by_route[match.group(1)].append([[p[1], p[2]] for p in points])

        routes: list[dict] = []
        total = 0
        for route, variants in sorted(by_route.items()):
            variants.sort(key=len, reverse=True)
            kept: list[list] = []
            covered: set[tuple] = set()
            for polyline in variants:
                point_set = {tuple(p) for p in polyline}
                if len(point_set - covered) / max(len(point_set), 1) > _MIN_NEW_GEOMETRY:
                    kept.append(polyline)
                    covered |= point_set
            routes.append({"route": route, "polylines": kept})
            total += sum(len(p) for p in kept)
        logger.info(
            "Loaded %d subway route lines (%d points) from static GTFS",
            sum(len(r["polylines"]) for r in routes),
            total,
        )
        return routes
    except Exception as exc:
        logger.warning("Could not load subway route shapes (%s); skipping route lines", exc)
        return []


def _parse_trip_routes(zf: zipfile.ZipFile) -> dict[str, str | None]:
    """trips.txt -> trip_id -> route_id. Subway needs only the route per trip for
    the routes-per-station index (not direction/shape/headsign like the shape
    builders), so this is a minimal parse. First-writer-wins on a duplicate
    trip_id; a blank route_id is kept as None (contributes no route)."""
    trip_routes: dict[str, str | None] = {}
    with zf.open("trips.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            trip_id = (row.get("trip_id") or "").strip()
            if not trip_id or trip_id in trip_routes:
                continue
            trip_routes[trip_id] = (row.get("route_id") or "").strip() or None
    return trip_routes


def _parse_trip_stops(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    """stop_times.txt -> trip_id -> [child platform stop_id]. Order does not
    matter for the routes-per-station index (only which stops a trip visits), so
    rows are collected unsorted. The stop ids are platform ids (101N/101S) that
    must fold up to a parent station (101) before indexing; that fold happens in
    derive_subway_station_routes. Rows with a blank trip_id/stop_id are skipped.
    Streamed row by row: the real stop_times.txt is tens of MB, but only the
    compact per-trip stop lists are retained, not the raw rows."""
    trip_stops: dict[str, list[str]] = defaultdict(list)
    with zf.open("stop_times.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            trip_id = (row.get("trip_id") or "").strip()
            stop_id = (row.get("stop_id") or "").strip()
            if not trip_id or not stop_id:
                continue
            trip_stops[trip_id].append(stop_id)
    return dict(trip_stops)


def _parse_child_to_parent(zf: zipfile.ZipFile) -> dict[str, str]:
    """stops.txt -> child_stop_id -> parent_station_id for every row carrying a
    parent_station (101N -> 101). Platform ids in stop_times fold up through this
    to the parent-station markers get_subway_stops serves and subway service
    alerts scope to."""
    child_to_parent: dict[str, str] = {}
    with zf.open("stops.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        for row in reader:
            stop_id = (row.get("stop_id") or "").strip()
            parent = (row.get("parent_station") or "").strip()
            if stop_id and parent:
                child_to_parent[stop_id] = parent
    return child_to_parent


def derive_subway_station_routes(
    trip_routes: dict[str, str | None],
    trip_stops: dict[str, list[str]],
    child_to_parent: dict[str, str],
) -> dict[str, list[str]]:
    """Pure: parent station_id -> sorted [route_id] serving it. Folds child
    platform ids (101N/101S) up to their parent station (101), the id space the
    markers and alerts use, via static_routes.fold_stop_routes. No zip read, so
    the warmup can call it on already-parsed tables and a synthetic test can
    exercise it directly."""
    return fold_stop_routes(trip_routes, trip_stops, child_to_parent)


def load_subway_station_routes() -> dict[str, list[str]]:
    """Routes-per-station index (parent station_id -> [route_id]) from the cached
    static GTFS, joining stop_times -> trips -> route_id and folding platforms up
    to parents. Assumes the zip exists (call after load_subway_stops ensured it).
    Purely enriches station popups with the routes that serve a stop, so a
    route-scoped service alert reaches the station even when no train is imminent
    there; any parse problem logs and returns {} rather than raising, exactly
    like the decorative route-line and station-marker loaders."""
    try:
        with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
            trip_routes = _parse_trip_routes(zf)
            trip_stops = _parse_trip_stops(zf)
            child_to_parent = _parse_child_to_parent(zf)
        index = derive_subway_station_routes(trip_routes, trip_stops, child_to_parent)
        logger.info("Loaded subway routes-per-station index (%d stations)", len(index))
        return index
    except Exception as exc:
        logger.warning("Could not load subway station routes (%s); station popups omit routes", exc)
        return {}
