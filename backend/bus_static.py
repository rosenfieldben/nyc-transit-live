"""Background-built, on-demand index of bus route geometry.

Bus shapes live in six borough GTFS zips (~52 MB compressed) whose bulk is
stop_times.txt — which we never read. A background task downloads the zips
one at a time, selects one representative shape per route and direction (the
variant used by the most trips), and writes one small JSON file per route
under data/cache/bus_routes/. Request handlers read single per-route files on
demand — the on-disk cache is the source of truth and nothing but a status
flag stays in memory. Startup is not blocked: the endpoint reports "building"
until the index is ready.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUS_CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "bus_routes"
MANIFEST_PATH = BUS_CACHE_DIR / "_manifest.json"

# Rebuild the index when the cached one is older than this (matches the
# subway static GTFS policy). On Railway the disk is wiped every deploy, so
# in practice each deploy rebuilds once in the background.
MAX_AGE_DAYS = 30

BUS_GTFS_URLS = {
    "manhattan": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_m.zip",
    "brooklyn": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_b.zip",
    "bronx": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_bx.zip",
    "queens": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_q.zip",
    "staten_island": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_si.zip",
    "mta_bus_co": "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_busco.zip",
}

# Route ids become cache filenames; reject anything that couldn't be a real
# MTA route id (also blocks path traversal via the API path parameter).
_ROUTE_ID_RE = re.compile(r"^[A-Za-z0-9+\-]{1,16}$")

# missing -> building -> ready | failed
# NOTE: per-process state — the deploy runs a single uvicorn worker. The
# request path reads geometry from disk, so workers would only disagree
# about status, not data.
_status = "missing"

# Whether the manifest behind the current "ready" status recorded failed
# boroughs — lets the API say "index is incomplete" instead of a plain 404.
_partial = False

# Set on shutdown so the build thread exits at the next check point instead
# of pinning interpreter exit until all six downloads finish (task.cancel()
# cannot interrupt a thread).
_stop = threading.Event()


def status() -> str:
    return _status


def is_partial() -> bool:
    return _partial


def stop() -> None:
    _stop.set()


def _route_path(route_id: str) -> Path:
    return BUS_CACHE_DIR / f"{route_id}.json"


def _atomic_write_json(path: Path, payload: dict) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def _process_zip(zip_path: Path, skip_routes: set[str]) -> set[str]:
    """Extract representative per-route geometry from one borough zip.

    Returns the route ids written. Routes already produced by an earlier
    borough zip are skipped (each route is operated by one division, so
    first-writer-wins is just dedupe insurance).
    """
    with zipfile.ZipFile(zip_path) as zf:
        # trips.txt: count trips per (route, direction, shape) so the most
        # frequently used variant becomes the representative.
        shape_use: dict[tuple, Counter] = defaultdict(Counter)
        with zf.open("trips.txt") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                route_id = (row.get("route_id") or "").strip()
                shape_id = (row.get("shape_id") or "").strip()
                if not route_id or not shape_id or route_id in skip_routes:
                    continue
                if not _ROUTE_ID_RE.match(route_id):
                    continue
                direction = (row.get("direction_id") or "?").strip() or "?"
                shape_use[(route_id, direction)][shape_id] += 1

        # Representative shape per route+direction: most trips, then
        # lexicographic shape_id as a deterministic tie-break. A shape can be
        # selected by several route/direction pairs, so map to a list.
        wanted: dict[str, list] = defaultdict(list)
        for (route_id, direction), counts in shape_use.items():
            shape_id = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            wanted[shape_id].append((route_id, direction))

        # shapes.txt: collect points only for the selected shapes.
        points: dict[str, list] = defaultdict(list)
        with zf.open("shapes.txt") as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")):
                shape_id = row.get("shape_id")
                if shape_id not in wanted:
                    continue
                try:
                    points[shape_id].append(
                        (
                            int(row["shape_pt_sequence"]),
                            round(float(row["shape_pt_lat"]), 5),
                            round(float(row["shape_pt_lon"]), 5),
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    continue  # malformed row

    by_route: dict[str, dict] = defaultdict(dict)
    for shape_id, pts in points.items():
        pts.sort()
        polyline = [[p[1], p[2]] for p in pts]
        for route_id, direction in wanted[shape_id]:
            by_route[route_id][direction] = polyline

    written: set[str] = set()
    for route_id, directions in by_route.items():
        # Drop degenerate directions (empty / single point) individually.
        polylines = [directions[d] for d in sorted(directions) if len(directions[d]) >= 2]
        if not polylines:
            continue  # nothing drawable
        _atomic_write_json(
            _route_path(route_id), {"route": route_id, "directions": polylines}
        )
        written.add(route_id)
    return written


def _build_index_sync() -> set[str]:
    """Download and process all borough zips; returns all route ids written.

    Runs in a worker thread. One zip on disk at a time; a failed borough is
    logged, recorded in the manifest, and skipped so one bad download doesn't
    lose the other five. Checks the shutdown event between boroughs and
    between download chunks; on shutdown the manifest is NOT written, so the
    next startup rebuilds rather than trusting a partial index.
    """
    BUS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Sweep temp files orphaned by an earlier hard kill mid-write.
    for stale in BUS_CACHE_DIR.glob("*.part"):
        stale.unlink(missing_ok=True)

    routes: set[str] = set()
    failed: list[str] = []
    started = time.time()
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for key, url in BUS_GTFS_URLS.items():
            if _stop.is_set():
                return routes
            # Download into the cache dir with a .part suffix so the sweep
            # above also cleans up zips orphaned by a hard kill.
            fd, tmp_name = tempfile.mkstemp(
                dir=BUS_CACHE_DIR, prefix="gtfs_dl.", suffix=".zip.part"
            )
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "wb") as f:
                    with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        for chunk in resp.iter_bytes():
                            if _stop.is_set():
                                return routes
                            f.write(chunk)
                written = _process_zip(tmp, skip_routes=routes)
                routes |= written
                logger.info("bus route index: %s contributed %d routes", key, len(written))
            except Exception as exc:
                logger.warning("bus route index: %s failed (%s); skipping", key, exc)
                failed.append(key)
            finally:
                tmp.unlink(missing_ok=True)
    if routes:
        _atomic_write_json(
            MANIFEST_PATH,
            {"routes": sorted(routes), "failed": failed, "built_at": int(time.time())},
        )
        global _partial
        _partial = bool(failed)
        status_text = ("partial (failed: %s)" % ", ".join(failed)) if failed else "ready"
        logger.info(
            "bus route index %s: %d routes in %.0fs",
            status_text,
            len(routes),
            time.time() - started,
        )
    return routes


async def ensure_index() -> None:
    """Load a fresh cached index, or (re)build it in a worker thread.

    Called as a background task at startup — never blocks serving. A cached
    manifest that records failed boroughs is served immediately (partial data
    beats none) but still triggers a rebuild so a transient download failure
    doesn't leave whole boroughs missing for 30 days.
    """
    global _status, _partial
    have_partial = False
    try:
        if (
            MANIFEST_PATH.exists()
            and time.time() - MANIFEST_PATH.stat().st_mtime < MAX_AGE_DAYS * 86400
        ):
            cached = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            routes = {r for r in cached.get("routes", []) if isinstance(r, str)}
            failed = cached.get("failed") or []
            if routes:
                _status = "ready"
                _partial = bool(failed)
                if not failed:
                    logger.info("bus route index: %d routes loaded from cache", len(routes))
                    return
                have_partial = True
                logger.info(
                    "bus route index: partial cache (%d routes; failed: %s); rebuilding",
                    len(routes), ", ".join(map(str, failed)),
                )
    except Exception as exc:
        logger.warning("bus route index manifest unreadable (%s); rebuilding", exc)

    if not have_partial:
        _status = "building"
    try:
        routes = await asyncio.to_thread(_build_index_sync)
    except Exception as exc:
        logger.error("bus route index build failed: %s", exc)
        if not have_partial:
            _status = "failed"
        return
    if routes:
        _status = "ready"
    elif not have_partial:
        _status = "failed"


def get_route_geometry(route_id: str) -> dict | None:
    """Read one route's geometry from the cache, or None if unknown.

    The on-disk cache is the source of truth (no in-memory membership gate):
    the regex blocks path traversal, and a missing file simply returns None.
    This also keeps responses consistent if multiple workers ever share the
    cache directory.
    """
    if not _ROUTE_ID_RE.match(route_id or ""):
        return None
    try:
        return json.loads(_route_path(route_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
