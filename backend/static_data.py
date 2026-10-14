"""Download and load the MTA static subway GTFS (station coordinates)."""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import zipfile
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import IO

import env_seams
from static_routes import fold_stop_routes
from static_shared import (
    StaticValidationError,
    cached_archive_is_usable,
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
# trips.txt AND stop_times.txt ARE HERE NOW, and this comment used to argue the
# opposite. It said their only subway consumer was load_subway_station_routes, "the
# H5 routes-per-station popup enrichment, which already swallows any problem and
# returns an empty index", and that the map was fully functional without it. That was
# true when it was written. It stopped being true twice over, and the rule that
# distinguishes these members is the visible-loss rule above, not the age of the
# sentence below it.
#
# THREE CONSUMERS READ THAT INDEX TODAY, and losing it is visible in all three:
#
#   1. The transfer ring. MR2 draws a station as a paper ring when two or more
#      TRUNKS call there and a filled dot when one does, from station.routes. With
#      an empty index every one of the 496 stations is a dot, so the map stops
#      saying which stations are interchanges.
#   2. The hub label class. The same predicate gives a station's name label its
#      `hub` class, and the zoom band reveals only hub labels at zooms 12 and 13.
#      With an empty index no label carries it, so NO station name renders at the
#      opening zoom or at the City preset while the Names control reads pressed.
#   3. The station alerts join (F11). A route-scoped service alert reaches a station
#      through its routes, so an empty index means a station with a live alert on
#      every route serving it shows none of them.
#
# So an archive without either file is a reduced archive whose promotion costs a
# rider three things, which is exactly the case the rule says to keep the
# last-known-good for instead. MR2's review finding F1 is the reproduction: with
# stop_times.txt absent the loader returned {}, /api/subway-stations served
# routes: [] for all 496 stations, and subway_static_status stayed "ready" with
# /healthz green, so the operator surface said nothing at all. PATH and ferry have
# required stop_times.txt all along, for the same kind of reason (advance matching
# and the dock/route alert join, 13d and H5); the subway is the one that was behind.
#
# A FOURTH CONSUMER READS THAT INDEX, and the list above predates it: MR5's popup
# kicker draws the routes calling at a station as route plates, so an empty index is
# a kicker with no plates in it as well.
#
# transfers.txt IS HERE BY THE SAME RULE (claude/subway-hub-definition). A hub is a
# station COMPLEX now, not a stop_id: F9's rule counted trunks per stop, so 108 of
# its 124 rings sat on stops this table joins to no other (the B beside the C on
# Central Park West, the D beside the R on Fourth Avenue), and 22 real complexes had
# no ring at all, Times Square among them, because each of its five stops carries
# one trunk. The complexes come from transfers.txt's cross-stop rows,
# and THREE CONSUMERS READ THEM, all rider-visible: the transfer ring, the hub label
# class (the same predicate), and the kicker, which lists the routes of the complex
# rather than of the stop. Without the file every stop is its own complex, Times
# Square's ring and name go again, and the status would say "ready" over it.
#
# AND A FLOOR OF ONE CROSS-STOP ROW, the operator's ruling on review finding H2.
# Presence alone let a transfers.txt with headers only, with only self rows, or with a
# renamed column load as every stop alone under "ready", which is the loss this member
# is required to prevent; so validate_subway_archive runs the loader's own transfer
# parser over it and rejects an archive that yields no pair. The live table has 150
# such rows; one is the floor, because the question is whether the table says
# anything, not how much.
#
# EVERY MEMBER HERE IS PARSED AT VALIDATION, NOT ONLY PRESENT (audit 2026-10-12, A02,
# with 2026-09-05's F08). Presence was the rule for trips.txt, stop_times.txt and
# shapes.txt, and it let an archive the load cannot read replace one it can: one
# undecodable byte in trips.txt or stop_times.txt was promoted and then failed every
# warmup attempt for MAX_AGE_DAYS, because the cached-archive check runs this same
# validator and so kept answering that the archive was fine; the same byte in
# shapes.txt was promoted and drew 0 route lines. So each member is read at validation
# through the loader's own parser (the one generator the load reads it with, drained
# rather than kept) and must yield at least one row: "the validator passed" now means
# "the load will read every member it opens". stop_times.txt is the costly one (565,093
# rows in the live table, measured with backend/scripts/measure_subway_validation.py).
_REQUIRED_MEMBERS = ("stops.txt", "shapes.txt", "trips.txt", "stop_times.txt", "transfers.txt")


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
    # THE COMPLEX TABLE'S FLOOR (claude/subway-hub-definition), by the same mechanism
    # and for the same reason: the loader's own parser, so "the validator passed"
    # means "the load will find at least one complex of two stops". The comment on
    # _REQUIRED_MEMBERS says why presence alone was not enough.
    require_parsed(
        lambda: parse_member(zf, "transfers.txt", _parse_transfer_rows),
        "transfers.txt",
        "cross-stop transfers",
    )
    # THE THREE MEMBERS THAT WERE ONLY PRESENT (A02, F08), each through the generator
    # the load reads it with, drained to a count and kept nowhere. Cheapest first, so a
    # publication bad in several places is refused without paying for stop_times.txt.
    # The generators are looked up at call time, so the load and this gate cannot be
    # handed different parsers.
    require_parsed(lambda: _drained(zf, "trips.txt", _iter_trip_routes), "trips.txt", "trips")
    require_parsed(
        lambda: _drained(zf, "shapes.txt", _iter_shape_points),
        "shapes.txt",
        "route shape points",
    )
    require_parsed(
        lambda: _drained(zf, "stop_times.txt", _iter_trip_stops), "stop_times.txt", "stop times"
    )


def _drained(zf: zipfile.ZipFile, member: str, rows: Callable[[IO[bytes]], Iterable]) -> int:
    """The validator's consumer: read every row `rows` yields from `member`, keep a count.

    The other consumer of the same generator is the loader's, which accumulates the
    rows into the tables it serves. One reader per member, two consumers, so the gate
    and the load cannot drift apart the way presence-only did (A02)."""
    return parse_member(zf, member, lambda raw: sum(1 for _ in rows(raw)))


# THE RESIDUAL ARM (A02, decision D3), the NJ Transit loader's, for the subway.
#
# The parse-class errors reading an archive can raise. One of them out of a load over
# an archive the validator accepted means the two disagree about what is readable,
# which they have done three times before (MR2's F1, F08, A02): the cache answers
# "valid and fresh" at every attempt while every attempt fails, until MAX_AGE_DAYS.
# Discarding is right in exactly this case: this file IS the cache, nothing better
# sits behind it (a previous archive was replaced when this one was promoted), and
# keeping it wedges every retry on the same bytes. With the file gone the next attempt
# downloads. The validator parses every member the load reads, so nothing should reach
# this arm; it is here because a backstop that is never reached costs less than a
# month of a failed subway static group.
_UNREADABLE = (UnicodeDecodeError, csv.Error, zipfile.BadZipFile, zlib.error, KeyError, OSError)


@contextmanager
def _discarding_an_unreadable_cache() -> Iterator[None]:
    """Run a read of the cached archive; on a parse-class error, unlink the cache and
    re-raise, so the warmup fails this attempt and the next one downloads."""
    try:
        yield
    except _UNREADABLE as exc:
        logger.warning("Cached subway static GTFS is unparseable (%s); discarding it", exc)
        SUBWAY_GTFS_ZIP.unlink(missing_ok=True)
        raise


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
    usable = await cached_archive_is_usable(SUBWAY_GTFS_ZIP, validate_subway_archive)
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
    with _discarding_an_unreadable_cache():
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


def _iter_shape_points(raw: IO[bytes]) -> Iterator[tuple[str, str, int, float, float]]:
    """shapes.txt rows -> (route, shape_id, sequence, lat, lon) for every point the map
    can draw, coordinates rounded to 5 decimals (~1 m).

    THE ONE READER OF shapes.txt (A02, F08): load_subway_route_shapes accumulates these
    rows into polylines and validate_subway_archive drains them to a count, so the gate
    asks exactly what the load reads. A row is drawable when its shape_id names a route
    (_SHAPE_ID_RE) and its sequence and coordinates are numbers; any other row is
    skipped here, as the loader always skipped it, so the gate's floor of one row means
    "at least one point of one route line", and a table of points for shapes no route
    owns is refused rather than promoted to an empty map. A row the codec or the CSV
    reader cannot read raises, which is the case the gate exists for.
    """
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        shape_id = row.get("shape_id") or ""
        match = _SHAPE_ID_RE.match(shape_id)
        if not match:
            continue
        try:
            sequence = int(row["shape_pt_sequence"])
            lat = round(float(row["shape_pt_lat"]), 5)
            lon = round(float(row["shape_pt_lon"]), 5)
        except (KeyError, ValueError, TypeError):
            continue  # malformed row
        yield match.group(1), shape_id, sequence, lat, lon


def load_subway_route_shapes() -> list[dict]:
    """Parse shapes.txt from the cached static GTFS into drawable polylines.

    Returns [{"route": "A", "polylines": [[[lat, lon], ...], ...]}, ...] with
    coordinates rounded to 5 decimals (~1 m). Assumes the zip exists (call
    after load_subway_stops succeeds). Route lines are decorative, so any
    parse problem logs and returns [] rather than raising; the validator has
    already read this member through the same generator, so that arm is for a
    cache that changed after it was checked.
    """
    try:
        shapes: dict[str, list] = defaultdict(list)
        route_of: dict[str, str] = {}
        with zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
            with zf.open("shapes.txt") as raw:
                for route, shape_id, sequence, lat, lon in _iter_shape_points(raw):
                    shapes[shape_id].append((sequence, lat, lon))
                    route_of[shape_id] = route

        by_route: dict[str, list[list]] = defaultdict(list)
        for shape_id, points in shapes.items():
            points.sort()
            by_route[route_of[shape_id]].append([[p[1], p[2]] for p in points])

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


def _iter_trip_routes(raw: IO[bytes]) -> Iterator[tuple[str, str | None]]:
    """trips.txt rows -> (trip_id, route_id), route_id None when blank (that trip
    contributes no route). Rows with no trip_id are skipped.

    THE ONE READER OF trips.txt (A02): _parse_trip_routes accumulates these rows for
    the routes-per-station index and validate_subway_archive drains them to a count, so
    an archive whose trips.txt the index cannot read is refused before it is promoted.
    """
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        trip_id = (row.get("trip_id") or "").strip()
        if trip_id:
            yield trip_id, (row.get("route_id") or "").strip() or None


def _iter_trip_stops(raw: IO[bytes]) -> Iterator[tuple[str, str]]:
    """stop_times.txt rows -> (trip_id, child platform stop_id). Rows with a blank
    trip_id or stop_id are skipped.

    THE ONE READER OF stop_times.txt (A02), the member the subway reads the most of
    (565,093 rows in the live table): _parse_trip_stops accumulates these rows and
    validate_subway_archive drains them to a count. Streamed row by row, so neither
    consumer holds the raw rows.
    """
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        trip_id = (row.get("trip_id") or "").strip()
        stop_id = (row.get("stop_id") or "").strip()
        if trip_id and stop_id:
            yield trip_id, stop_id


def _parse_trip_routes(zf: zipfile.ZipFile) -> dict[str, str | None]:
    """trips.txt -> trip_id -> route_id, through _iter_trip_routes. Subway needs only
    the route per trip for the routes-per-station index (not direction/shape/headsign
    like the shape builders), so this is a minimal parse. First-writer-wins on a
    duplicate trip_id."""

    def accumulate(raw: IO[bytes]) -> dict[str, str | None]:
        trip_routes: dict[str, str | None] = {}
        for trip_id, route_id in _iter_trip_routes(raw):
            trip_routes.setdefault(trip_id, route_id)
        return trip_routes

    return parse_member(zf, "trips.txt", accumulate)


def _parse_trip_stops(zf: zipfile.ZipFile) -> dict[str, list[str]]:
    """stop_times.txt -> trip_id -> [child platform stop_id], through
    _iter_trip_stops. Order does not matter for the routes-per-station index (only
    which stops a trip visits), so rows are collected unsorted. The stop ids are
    platform ids (101N/101S) that must fold up to a parent station (101) before
    indexing; that fold happens in derive_subway_station_routes. Only the compact
    per-trip stop lists are retained, never the raw rows."""

    def accumulate(raw: IO[bytes]) -> dict[str, list[str]]:
        trip_stops: dict[str, list[str]] = defaultdict(list)
        for trip_id, stop_id in _iter_trip_stops(raw):
            trip_stops[trip_id].append(stop_id)
        return dict(trip_stops)

    return parse_member(zf, "stop_times.txt", accumulate)


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

    RAISES rather than returning {} on a parse problem, and that reverses what this
    function used to do. It caught every exception, logged a warning and returned an
    empty index, on the grounds that the routes were popup enrichment and the map was
    fully functional without them. Four consumers read the index now, all
    rider-visible (the transfer ring, the hub label class, the station alerts join,
    and MR5's popup kicker), and _REQUIRED_MEMBERS above names them: the files this
    reads are required members,
    so a problem parsing one is a failed load of the archive, not a warning. The
    warmup's `except Exception` catches it, the subway static group reports "failed",
    /healthz degrades, and the last-known-good index stays in app.state.

    WHAT IS STILL TOLERATED is a station with no trips serving it, which is data
    rather than failure: derive_subway_station_routes simply yields no entry for it,
    and a station absent from the index reads as no routes at every consumer. The
    difference is between an archive this loader cannot read and an archive that says
    nothing calls at a stop.

    AN ARCHIVE THIS LOADER CANNOT READ IS DISCARDED as it fails (the residual arm, A02),
    so the next attempt downloads instead of failing on the same bytes for a month."""
    with _discarding_an_unreadable_cache(), zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
        trip_routes = _parse_trip_routes(zf)
        trip_stops = _parse_trip_stops(zf)
        child_to_parent = _parse_child_to_parent(zf)
    index = derive_subway_station_routes(trip_routes, trip_stops, child_to_parent)
    logger.info("Loaded subway routes-per-station index (%d stations)", len(index))
    return index


# The GTFS transfer types that mean "a rider can change here": 0 recommended (and a
# blank, which the spec reads as 0), 1 timed, 2 with a minimum time. Type 3 says a
# transfer is NOT possible, and 4 and 5 are in-seat continuations between trips, which
# name a vehicle rather than a station; none of those joins two stops into a complex
# (review finding H7). The live table is type 2 throughout, so this is a guard.
_COMPLEX_TRANSFER_TYPES = frozenset({"", "0", "1", "2"})


def _parse_transfer_pairs(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """transfers.txt out of an open archive, through _parse_transfer_rows."""
    return parse_member(zf, "transfers.txt", _parse_transfer_rows)


def _parse_transfer_rows(raw: IO[bytes]) -> list[tuple[str, str]]:
    """transfers.txt -> the CROSS-STOP rows as (from_stop_id, to_stop_id) pairs.

    A stream-taking parser, split out for the reason _parse_stops_rows was: the
    validator has to ask this exact question of a STAGED archive, and a predicate the
    validator reimplements is one that drifts from the loader.

    The MTA publishes two kinds of row here. A row from a stop to itself (101 -> 101)
    is a minimum transfer time within one stop, which says nothing about which stops
    form a complex, so it is dropped. A row between two stops (127 -> 725) of a type
    a rider can change by is the complex: the two stops are one station. Rows with a
    blank id on either side are skipped as data, the way the other parsers here skip
    a malformed row, and so are the transfer types that say no change is possible."""
    pairs: list[tuple[str, str]] = []
    reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
    for row in reader:
        source = (row.get("from_stop_id") or "").strip()
        target = (row.get("to_stop_id") or "").strip()
        kind = (row.get("transfer_type") or "").strip()
        if source and target and source != target and kind in _COMPLEX_TRANSFER_TYPES:
            pairs.append((source, target))
    return pairs


def derive_subway_station_complexes(
    pairs: list[tuple[str, str]],
    station_ids: list[str],
    child_to_parent: dict[str, str],
) -> dict[str, str]:
    """Pure: parent station_id -> complex_id, for EVERY station in `station_ids`.

    UNION-FIND OVER THE PAIRS, not the pairs themselves, because a complex is the
    transitive closure of "a rider can change between these two stops". The live
    publication happens to list every pair of every complex (measured on the
    2026-08-27 archive: 35 complexes, none of them a chain), so on today's data a
    pairwise match gives the same answer; a publication that lists A-B and B-C and
    leaves A-C implied is still one complex, and only a closure says so.

    A STOP IN NO ROW IS ITS OWN COMPLEX, with its own id as the complex id, which is
    the ruling's wording and the honest reading of the table: a station the MTA lists
    no transfer for is a station alone. THE COMPLEX ID IS THE SMALLEST STATION ID IN
    IT, so it is stable across loads and is always a real station id rather than a
    coined one (Times Square's five stops are complex "127").

    Ids fold through child_to_parent first, because every consumer keys on the parent
    station. The live table names parents only, so the fold is a guard rather than a
    translation. An id that is no station still joins the closure (it may be the one
    link between two stations that are) but is never a key of the result."""
    parent: dict[str, str] = {}

    def find(stop: str) -> str:
        parent.setdefault(stop, stop)
        root = stop
        while parent[root] != root:
            root = parent[root]
        while parent[stop] != root:
            parent[stop], stop = root, parent[stop]
        return root

    for source, target in pairs:
        a = find(child_to_parent.get(source, source))
        b = find(child_to_parent.get(target, target))
        if a != b:
            parent[max(a, b)] = min(a, b)

    members: dict[str, list[str]] = defaultdict(list)
    for station_id in station_ids:
        members[find(station_id)].append(station_id)
    return {station_id: min(group) for group in members.values() for station_id in group}


def load_subway_station_complexes() -> dict[str, str]:
    """The station complex index (parent station_id -> complex_id) from the cached
    static GTFS: transfers.txt's cross-stop rows closed over by
    derive_subway_station_complexes, keyed by the same parent stations the markers
    are drawn from. Assumes the zip exists (call after load_subway_stops ensured it).

    RAISES rather than returning {}, for the reason load_subway_station_routes gives:
    transfers.txt is a required member because three rider-visible consumers read this
    index, so a problem reading it is a failed load of the archive, not a warning. The
    warmup's `except Exception` catches it and the last-known-good index stays, and the
    residual arm discards an archive it cannot read, as load_subway_station_routes does."""
    with _discarding_an_unreadable_cache(), zipfile.ZipFile(SUBWAY_GTFS_ZIP) as zf:
        stations = parse_member(zf, "stops.txt", _parse_stations_rows)
        child_to_parent = _parse_child_to_parent(zf)
        pairs = _parse_transfer_pairs(zf)
    index = derive_subway_station_complexes(pairs, list(stations), child_to_parent)
    logger.info(
        "Loaded subway station complex index (%d stations, %d complexes)",
        len(index),
        len(set(index.values())),
    )
    return index
