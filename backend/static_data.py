"""Download and load the MTA static subway GTFS (station coordinates)."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger("uvicorn.error")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBWAY_GTFS_ZIP = PROJECT_ROOT / "data" / "gtfs_static" / "gtfs_subway.zip"
SUBWAY_GTFS_URL = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"

# Re-download the static GTFS when the cached copy is older than this. The MTA
# republishes it a few times a year; station coordinates change rarely.
MAX_AGE_DAYS = 30


async def _download_zip() -> None:
    SUBWAY_GTFS_ZIP.parent.mkdir(parents=True, exist_ok=True)
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
            logger.warning(
                "Static GTFS re-download failed (%s); using stale cached copy", exc
            )
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
