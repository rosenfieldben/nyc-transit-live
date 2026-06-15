"""Download and load the MTA static subway GTFS (station coordinates)."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import re
import tempfile
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBWAY_GTFS_ZIP = PROJECT_ROOT / "data" / "gtfs_static" / "gtfs_subway.zip"
SUBWAY_GTFS_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"

# Re-download the static GTFS when the cached copy is older than this. The MTA
# republishes it a few times a year; station coordinates change rarely.
MAX_AGE_DAYS = 30


async def _download_zip() -> None:
    SUBWAY_GTFS_ZIP.parent.mkdir(parents=True, exist_ok=True)
    # Sweep temp files orphaned by an earlier hard kill mid-download.
    for stale in SUBWAY_GTFS_ZIP.parent.glob("*.part"):
        stale.unlink(missing_ok=True)
    # Unique temp name so concurrent workers (uvicorn --workers N all run
    # lifespan) can't interleave writes into one file; same directory keeps
    # the final rename atomic, making concurrent downloads last-writer-wins.
    fd, tmp_name = tempfile.mkstemp(
        dir=SUBWAY_GTFS_ZIP.parent, prefix="gtfs_subway.", suffix=".part"
    )
    os.close(fd)
    tmp = Path(tmp_name)
    logger.info("Downloading static subway GTFS from %s", SUBWAY_GTFS_URL)
    try:
        # httpx's timeout is per socket read; bound the whole transfer so a
        # trickling response can't stall startup indefinitely.
        async with asyncio.timeout(300):
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                async with client.stream("GET", SUBWAY_GTFS_URL) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
        tmp.replace(SUBWAY_GTFS_ZIP)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Downloaded static subway GTFS to %s", SUBWAY_GTFS_ZIP)


def _parse_stops() -> dict[str, dict]:
    """Read stops.txt straight out of the cached zip: stop_id -> name/lat/lon.

    Realtime feeds reference platform-level stop ids (e.g. "R16N"); stops.txt
    contains those alongside parent stations, all with coordinates. Rows with
    missing or malformed coordinates are skipped.
    """
    stops: dict[str, dict] = {}
    with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
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


async def load_subway_stops() -> dict[str, dict]:
    """Load the station lookup, downloading the static GTFS if missing or stale.

    Falls back to a stale cached copy if the re-download fails; raises only if
    no usable copy can be obtained at all.
    """
    fresh = (
        SUBWAY_GTFS_ZIP.exists()
        and time.time() - SUBWAY_GTFS_ZIP.stat().st_mtime < MAX_AGE_DAYS * 86400
    )
    if not fresh:
        try:
            await _download_zip()
        except Exception as exc:
            if not SUBWAY_GTFS_ZIP.exists():
                raise
            logger.warning("Static GTFS re-download failed (%s); using stale cached copy", exc)
    try:
        stops = _parse_stops()
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        # Unusable cache: corrupt zip, missing stops.txt member (repackaged
        # archive, wrong file at the path), or undecodable text. Refetch once
        # rather than staying wedged until the cache ages out.
        logger.warning("Cached static GTFS is unusable; re-downloading")
        SUBWAY_GTFS_ZIP.unlink(missing_ok=True)
        await _download_zip()
        stops = _parse_stops()
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
        stations: dict[str, dict] = {}
        with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
            with zf.open("stops.txt") as raw:
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
# the direction letter. We keep one direction per route — N and S trace the
# same tracks at map scale.
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
