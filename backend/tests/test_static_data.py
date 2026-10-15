"""Tests for the static subway GTFS download/cache logic and shape loading.

Small zips are built in tmp_path; SUBWAY_GTFS_ZIP / SUBWAY_GTFS_URL are
monkeypatched so no test touches the network (failure cases point the URL at
a closed local port).
"""

import asyncio
import csv
import io
import json
import os
import time
import zipfile
import zlib
from pathlib import Path

import httpx
import pytest

import static_data
import static_shared

pytestmark = pytest.mark.anyio

# Nothing listens here: connection refused, instantly.
DEAD_URL = "http://127.0.0.1:9/gtfs_subway.zip"

STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]

# The real subway shape, and C5's parent-station gate now depends on it: 101 is the
# PARENT station (location_type 1, the clickable marker) and 101N is a platform
# under it (the id realtime references). A stops.txt of platform rows alone is a
# publication validate_subway_archive rejects, so a fixture without a parent row
# would be testing an archive the loader would never accept.
STOP_ROWS = [
    {
        "stop_id": "101",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "1",
    },
    {
        "stop_id": "101N",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "0",
        "parent_station": "101",
    },
]


def csv_text(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def write_gtfs_zip(path, stop_rows=STOP_ROWS, shape_rows=None, members=None):
    """Write a minimal GTFS zip; `members` overrides the file map entirely.

    Deliberately still minimal (stops.txt, plus shapes.txt when asked): the
    single-table loaders and the missing-member tests below are what it exists
    for. Anything handed to load_subway_stops goes through write_loadable_gtfs_zip
    instead, because that path now runs the validator.
    """
    if members is None:
        members = {"stops.txt": csv_text(STOPS_COLS, stop_rows)}
        if shape_rows is not None:
            members["shapes.txt"] = csv_text(SHAPES_COLS, shape_rows)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def write_loadable_gtfs_zip(path, stop_rows=STOP_ROWS, shape_rows=None):
    """Write a zip that passes validate_subway_archive (C5 seam).

    The validator requires stops.txt, shapes.txt, trips.txt, stop_times.txt AND
    transfers.txt, and since A02 it PARSES all five and wants a row from each, so every
    archive a cache-lifecycle test hands the loader carries one usable row per member:
    one trip, one call at the platform 101N, one shape point, one cross-stop pair. Header
    only used to be enough for three of them (an empty shapes.txt yielded the same []
    route lines a missing one did), and that is exactly the archive A02 and F08 found
    being promoted; it is now a publication the validator refuses, and the tests that
    refuse it say so by name.
    """
    write_gtfs_zip(path, members=loadable_members(stop_rows=stop_rows, shape_rows=shape_rows))


def loadable_members(stop_rows=STOP_ROWS, shape_rows=None, **override):
    """The five members of a subway archive the validator accepts, as text, with
    `override` replacing any of them by name (text, or bytes for a corrupt member)."""
    members = {
        "stops.txt": csv_text(STOPS_COLS, stop_rows),
        "trips.txt": csv_text(TRIPS_COLS, TRIP_ROWS),
        "shapes.txt": csv_text(SHAPES_COLS, SHAPE_ROWS if shape_rows is None else shape_rows),
        "stop_times.txt": csv_text(STOP_TIMES_COLS, STOP_TIME_ROWS),
        "transfers.txt": csv_text(TRANSFERS_COLS, TRANSFER_ROWS),
    }
    members.update(override)
    return members


@pytest.fixture
def gtfs_zip(tmp_path, monkeypatch):
    """Point the module at a tmp zip path and a dead URL by default."""
    path = tmp_path / "gtfs_subway.zip"
    monkeypatch.setattr(static_data, "SUBWAY_GTFS_ZIP", path)
    monkeypatch.setattr(static_data, "SUBWAY_GTFS_URL", DEAD_URL)
    return path


def age_file(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


# ---------------- load_subway_stops ----------------


async def test_fresh_cache_parsed_without_downloading(gtfs_zip, monkeypatch):
    write_loadable_gtfs_zip(gtfs_zip)

    async def fail(*args, **kwargs):  # any download attempt is a test failure
        raise AssertionError("should not download with a fresh cache")

    monkeypatch.setattr(static_data, "_download_zip", fail)
    stops = await static_data.load_subway_stops()
    assert stops["101N"] == {"name": "Alpha", "lat": 40.7, "lon": -74.0}


async def test_stale_cache_with_failed_download_falls_back(gtfs_zip):
    write_loadable_gtfs_zip(gtfs_zip)
    age_file(gtfs_zip, days=40)  # past MAX_AGE_DAYS; the dead URL fails fast
    stops = await static_data.load_subway_stops()
    assert "101N" in stops


async def test_missing_cache_with_failed_download_raises(gtfs_zip):
    assert not gtfs_zip.exists()
    with pytest.raises(httpx.HTTPError):
        await static_data.load_subway_stops()


@pytest.mark.parametrize(
    "make_bad_cache",
    [
        lambda path: path.write_bytes(b"this is not a zip archive"),
        lambda path: write_gtfs_zip(path, members={"agency.txt": "agency_id\nMTA\n"}),
    ],
    ids=["corrupt-zip", "zip-missing-stops.txt"],
)
async def test_unusable_fresh_cache_redownloads_exactly_once(gtfs_zip, monkeypatch, make_bad_cache):
    make_bad_cache(gtfs_zip)
    calls = []

    async def fake_download(*, cache_serves=None):
        calls.append(cache_serves)
        write_loadable_gtfs_zip(gtfs_zip)

    monkeypatch.setattr(static_data, "_download_zip", fake_download)
    stops = await static_data.load_subway_stops()
    assert calls == [False], "one download, told the cache it replaces does not serve"
    assert "101N" in stops


async def test_malformed_coordinate_rows_skipped(gtfs_zip):
    rows = STOP_ROWS + [
        {"stop_id": "BAD1", "stop_name": "NoCoords", "stop_lat": "", "stop_lon": ""},
        {"stop_id": "BAD2", "stop_name": "Garbage", "stop_lat": "north", "stop_lon": "-74.0"},
    ]
    write_loadable_gtfs_zip(gtfs_zip, stop_rows=rows)
    stops = await static_data.load_subway_stops()
    assert "101N" in stops
    assert "BAD1" not in stops and "BAD2" not in stops


# ---------------- load_subway_route_shapes ----------------


def shape_rows(shape_id, points):
    return [
        {
            "shape_id": shape_id,
            "shape_pt_sequence": str(i),
            "shape_pt_lat": str(lat),
            "shape_pt_lon": str(lon),
        }
        for i, (lat, lon) in enumerate(points, start=1)
    ]


def test_route_shapes_bad_zip_returns_empty_list(gtfs_zip):
    gtfs_zip.write_bytes(b"corrupt")
    assert static_data.load_subway_route_shapes() == []


# ---------------- load_subway_stations ----------------


def test_load_subway_stations_parent_stations_only(gtfs_zip):
    rows = [
        {
            "stop_id": "A01",
            "stop_name": "Parent",
            "stop_lat": "40.7",
            "stop_lon": "-74.0",
            "location_type": "1",
        },
        {
            "stop_id": "A01N",
            "stop_name": "Parent",
            "stop_lat": "40.7",
            "stop_lon": "-74.0",
            "location_type": "0",
            "parent_station": "A01",
        },
        {
            "stop_id": "BAD",
            "stop_name": "NoCoords",
            "stop_lat": "",
            "stop_lon": "",
            "location_type": "1",
        },
    ]
    write_gtfs_zip(gtfs_zip, stop_rows=rows)
    stations = static_data.load_subway_stations()
    assert set(stations) == {"A01"}  # platform A01N excluded; BAD has no coords
    assert stations["A01"] == {"name": "Parent", "lat": 40.7, "lon": -74.0}


def test_load_subway_stations_bad_zip_returns_empty(gtfs_zip):
    gtfs_zip.write_bytes(b"corrupt")
    assert static_data.load_subway_stations() == {}


def test_variant_dedup_keeps_branch_drops_express(gtfs_zip):
    # 20-point trunk; an express variant sharing every point (0% new) must be
    # dropped; an 18-point branch with 5 new points (~28% new) must survive.
    trunk = [(40.0 + i / 100, -74.0) for i in range(20)]
    express = trunk[1:]  # 19 points, all already covered
    branch = trunk[:13] + [(41.0 + i / 100, -73.5) for i in range(5)]
    rows = (
        shape_rows("A..N01R", trunk)
        + shape_rows("A..N02X", express)
        + shape_rows("A..N03R", branch)
        + shape_rows("A..S01R", trunk)  # southbound: filtered by direction
    )
    write_gtfs_zip(gtfs_zip, shape_rows=rows)
    routes = static_data.load_subway_route_shapes()
    assert [r["route"] for r in routes] == ["A"]
    polylines = routes[0]["polylines"]
    assert len(polylines) == 2  # trunk + branch; express deduped away
    assert sorted(len(p) for p in polylines) == [18, 20]


# ---------------- routes-per-station index (H5) ----------------
#
# The subway has no committed trimmed GTFS fixture (these tests build synthetic
# zips), and the real stop_times.txt is tens of MB, so the routes-per-station
# index is covered with synthetic tables here rather than a golden.

TRIPS_COLS = ["route_id", "trip_id", "service_id", "trip_headsign", "direction_id", "shape_id"]
STOP_TIMES_COLS = ["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"]
TRANSFERS_COLS = ["from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time"]
# One usable row each, what write_loadable_gtfs_zip's archives carry (A02: the validator
# parses every member and wants a row from each).
TRIP_ROWS = [{"route_id": "1", "trip_id": "t1", "trip_headsign": "Uptown", "shape_id": "1..N01R"}]
STOP_TIME_ROWS = [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}]
SHAPE_ROWS = [
    {
        "shape_id": "1..N01R",
        "shape_pt_sequence": "1",
        "shape_pt_lat": "40.7",
        "shape_pt_lon": "-74.0",
    },
    {
        "shape_id": "1..N01R",
        "shape_pt_sequence": "2",
        "shape_pt_lat": "40.71",
        "shape_pt_lon": "-74.01",
    },
]
# ONE REAL COMPLEX, both directions, as the MTA writes them: the validator's floor is one
# cross-stop row (the operator's ruling on review finding H2), so every archive that has to
# PASS validation carries a pair, and an archive with none is a test's subject, not a
# fixture's accident.
TRANSFER_ROWS = [
    {"from_stop_id": "101", "to_stop_id": "103", "transfer_type": "2", "min_transfer_time": "180"},
    {"from_stop_id": "103", "to_stop_id": "101", "transfer_type": "2", "min_transfer_time": "180"},
]

# Two parent stations (101, 103) with N/S child platforms, the real subway shape.
ROUTE_STOP_ROWS = [
    {
        "stop_id": "101",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "1",
        "parent_station": "",
    },
    {
        "stop_id": "101N",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "0",
        "parent_station": "101",
    },
    {
        "stop_id": "101S",
        "stop_name": "Alpha",
        "stop_lat": "40.7",
        "stop_lon": "-74.0",
        "location_type": "0",
        "parent_station": "101",
    },
    {
        "stop_id": "103",
        "stop_name": "Beta",
        "stop_lat": "40.71",
        "stop_lon": "-74.01",
        "location_type": "1",
        "parent_station": "",
    },
    {
        "stop_id": "103N",
        "stop_name": "Beta",
        "stop_lat": "40.71",
        "stop_lon": "-74.01",
        "location_type": "0",
        "parent_station": "103",
    },
]


def test_derive_subway_station_routes_folds_platforms_to_parents():
    # THE FOLD, pinned: stop_times lists platform ids (101N/101S), so the index
    # must be keyed by the PARENT station (101) the markers serve, never a
    # platform. Route 1 visits both stations; route 2 only the first.
    trip_routes = {"t1": "1", "t2": "2"}
    trip_stops = {"t1": ["101N", "103N"], "t2": ["101S"]}
    child_to_parent = {"101N": "101", "101S": "101", "103N": "103"}
    idx = static_data.derive_subway_station_routes(trip_routes, trip_stops, child_to_parent)
    assert idx == {"101": ["1", "2"], "103": ["1"]}
    assert not (set(idx) & set(child_to_parent))  # no platform id leaks in as a key


def test_derive_subway_station_routes_ignores_routeless_trips():
    idx = static_data.derive_subway_station_routes({"t": None}, {"t": ["101N"]}, {"101N": "101"})
    assert idx == {}


def test_load_subway_station_routes_end_to_end(gtfs_zip):
    write_gtfs_zip(
        gtfs_zip,
        members={
            "stops.txt": csv_text(STOPS_COLS, ROUTE_STOP_ROWS),
            "trips.txt": csv_text(
                TRIPS_COLS,
                [
                    {"route_id": "1", "trip_id": "t1"},
                    {"route_id": "2", "trip_id": "t2"},
                ],
            ),
            "stop_times.txt": csv_text(
                STOP_TIMES_COLS,
                [
                    {"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"},
                    {"trip_id": "t1", "stop_id": "103N", "stop_sequence": "2"},
                    {"trip_id": "t2", "stop_id": "101S", "stop_sequence": "1"},
                ],
            ),
        },
    )
    idx = static_data.load_subway_station_routes()
    assert idx == {"101": ["1", "2"], "103": ["1"]}


# ---------------- F1: the index is required, so its tables are too ----------------
#
# THE FINDING, in one sentence: stop_times.txt was not a required member, this loader
# swallowed every exception and returned {}, and /api/subway-stations then served
# routes: [] for all 496 stations while subway_static_status stayed "ready" and
# /healthz stayed green. Three consumers read that index and all three are
# rider-visible (the transfer ring, the hub label class, the station alerts join), so
# the reduced archive was promoted and the operator surface said nothing.
#
# The two tests below used to assert the swallowing, one for a missing table and one
# for a corrupt zip. They now assert the opposite, which is the whole change: these are
# the same two inputs, with the answer reversed on purpose rather than deleted, so the
# reversal is legible in the diff.


def test_load_subway_station_routes_raises_on_missing_tables(gtfs_zip):
    # A zip without trips/stop_times cannot produce the index, and returning {} made
    # that indistinguishable from a network where nothing calls anywhere. It raises
    # now, and validate_subway_archive rejects such an archive before the loader is
    # reached at all (the test below).
    write_gtfs_zip(gtfs_zip)  # default: stops.txt only, no trips/stop_times
    with pytest.raises(Exception):
        static_data.load_subway_station_routes()


def test_load_subway_station_routes_raises_on_bad_zip(gtfs_zip):
    gtfs_zip.write_bytes(b"corrupt")
    with pytest.raises(Exception):
        static_data.load_subway_station_routes()


def test_load_subway_station_routes_raises_on_corrupt_stop_times(gtfs_zip):
    """A stop_times.txt that is present and unreadable: its DEFLATE payload damaged, so
    the archive opens and lists and only READING that member raises.

    THIS TEST'S PREMISE REVERSED WITH A02, and the reversal is the fix. It used to
    assert that validate_subway_archive PASSED this archive (the member was checked for
    presence only) and that only the loader refusing to swallow turned it into a failed
    load. The validator now reads the member through the loader's own generator, so it
    refuses the archive, naming the member, and the loader still raises over the same
    bytes when validation is bypassed: both halves of the rule, on the damage a
    decoding byte cannot show."""
    body = csv_text(STOP_TIMES_COLS, [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}])
    with zipfile.ZipFile(gtfs_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stops.txt", csv_text(STOPS_COLS, ROUTE_STOP_ROWS))
        zf.writestr("shapes.txt", csv_text(SHAPES_COLS, SHAPE_ROWS))
        zf.writestr("trips.txt", csv_text(TRIPS_COLS, [{"route_id": "1", "trip_id": "t1"}]))
        zf.writestr("stop_times.txt", body)
        zf.writestr("transfers.txt", csv_text(TRANSFERS_COLS, TRANSFER_ROWS))
    # Corrupt stop_times.txt's DEFLATE payload in place. Its stored CRC and its bytes
    # then disagree, so opening the archive and listing it still work and only READING
    # that member raises. A garbled CSV ROW would not do: the parser skips those by
    # design, and that tolerance is deliberately kept.
    #
    # LOCATED THROUGH header_offset, not by searching for the name. The filename
    # appears twice, in the local header and again in the central directory, and a
    # first draft of this test flipped a byte after the LAST occurrence, which
    # corrupted the central directory and made the whole archive unopenable. That
    # version passed for the wrong reason, which is exactly what the premise assertion
    # below is here to catch.
    raw = bytearray(gtfs_zip.read_bytes())
    with zipfile.ZipFile(gtfs_zip) as zf:
        offset = zf.getinfo("stop_times.txt").header_offset
    name_len = int.from_bytes(raw[offset + 26 : offset + 28], "little")
    extra_len = int.from_bytes(raw[offset + 28 : offset + 30], "little")
    payload = offset + 30 + name_len + extra_len
    raw[payload] ^= 0xFF
    gtfs_zip.write_bytes(bytes(raw))

    # THE PREMISE, asserted rather than assumed: the archive is structurally complete,
    # so require_members passes it and the refusal below is the PARSE's. Without this the
    # test could pass because the zip had become unopenable, which the bad-zip test above
    # already covers.
    with zipfile.ZipFile(gtfs_zip) as zf:
        assert set(zf.namelist()) == {
            "stops.txt",
            "shapes.txt",
            "trips.txt",
            "stop_times.txt",
            "transfers.txt",
        }
        static_data.require_members(zf, static_data._REQUIRED_MEMBERS)
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_archive(zf)
    assert str(err.value).startswith("stop_times.txt is not readable")

    # And with validation bypassed, the load's own read of the same bytes fails as a
    # damaged member fails (zlib.error from inflate, or BadZipFile if the damage lands
    # where the CRC sees it first), and the residual arm discards the cache: the
    # classes A02b added to the subway's tuple, pinned for the subway.
    with pytest.raises((zlib.error, zipfile.BadZipFile)):
        static_data.load_subway_station_routes()
    assert not gtfs_zip.exists(), "an archive the load cannot read must not stay the cache"


@pytest.mark.parametrize("missing", ["stop_times.txt", "trips.txt", "transfers.txt"])
def test_validate_rejects_an_archive_without_the_station_routes_tables(gtfs_zip, missing):
    """F1's reproduction, pinned. A publication missing either table must fail the
    load through require_members, exactly as PATH and the ferry already do, so the
    group reaches "failed" rather than "ready" with an empty index.

    THE transfers.txt CASE IS THE STATION COMPLEX INDEX'S, not the routes index's, and
    it shares this test because the rule and the failure are the same: without it every
    stop is a complex of one, Times Square loses its ring and its name at zooms 12 and
    13, and the kicker lists one platform's routes (claude/subway-hub-definition)."""
    members = {
        "stops.txt": csv_text(STOPS_COLS, ROUTE_STOP_ROWS),
        "shapes.txt": csv_text(SHAPES_COLS, ()),
        "trips.txt": csv_text(TRIPS_COLS, ()),
        "stop_times.txt": csv_text(STOP_TIMES_COLS, ()),
        "transfers.txt": csv_text(TRANSFERS_COLS, TRANSFER_ROWS),
    }
    del members[missing]
    write_gtfs_zip(gtfs_zip, members=members)
    with zipfile.ZipFile(gtfs_zip) as zf:
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_archive(zf)
    # Named, because the operator reads this string off /api/status as
    # last_download_error and "invalid archive" alone would not say which file.
    assert missing in str(err.value)


@pytest.mark.parametrize("missing", ["stop_times.txt", "trips.txt", "transfers.txt"])
async def test_a_reduced_publication_fails_the_load_rather_than_serving_an_empty_index(
    gtfs_zip, monkeypatch, missing
):
    """F1 END TO END at the loader, which is the scenario as it would actually arrive:
    the MTA publishes an archive without the table, so the cached copy is rejected AND
    the redownload of the same publication is rejected too, and the load raises. The
    warmup turns that into subway_static_status "failed", HEALTH_SUBWAY_STATIC_FAILED,
    a degraded /healthz, and the monitor's existing subway-static check.

    BOTH ENDS MATTER. Rejecting the cache alone would only mean "treating as absent"
    and a redownload; it is the second rejection that makes it a failure rather than a
    slow path to the same reduced archive.

    The [transfers.txt] case is the station complex index's reduced publication, which
    the same chain has to refuse for the same reason (the ruling's "a zip lacking
    transfers.txt fails the load")."""
    members = {
        "stops.txt": csv_text(STOPS_COLS, ROUTE_STOP_ROWS),
        "shapes.txt": csv_text(SHAPES_COLS, ()),
        "trips.txt": csv_text(TRIPS_COLS, ()),
        "stop_times.txt": csv_text(STOP_TIMES_COLS, ()),
        "transfers.txt": csv_text(TRANSFERS_COLS, TRANSFER_ROWS),
    }
    del members[missing]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    reduced = buf.getvalue()

    downloads = []

    # INJECTED AT THE TRANSFER, not at _download_zip, and that distinction is the
    # test. A first draft stubbed _download_zip to write the reduced archive straight
    # to the cache path, which bypassed staged_fetch entirely: the archive was promoted
    # unvalidated, stops.txt parsed fine, and the load returned happily. That draft
    # would have passed with _REQUIRED_MEMBERS reverted, because nothing in it ever ran
    # the validator on the download. The real chain is staged_fetch validating the
    # STAGED bytes and refusing to promote them, so the real chain is what runs here.
    real_staged_fetch = static_shared.staged_fetch

    async def publishes(url, dest, validate, **kwargs):
        async def transfer(u, stage, deadline_s):
            downloads.append(1)
            stage.write_bytes(reduced)

        await real_staged_fetch(url, dest, validate, **kwargs, download=transfer)

    monkeypatch.setattr(static_data, "staged_fetch", publishes)
    write_gtfs_zip(gtfs_zip, members=members)  # the cache is the reduced archive too

    with pytest.raises(static_data.StaticValidationError) as err:
        await static_data.load_subway_stops()
    assert missing in str(err.value)
    assert downloads == [1], "the rejected cache must be re-downloaded exactly once"
    # The cache is untouched by the rejection, which is the last-known-good rule at the
    # archive level: staged_fetch deletes the stage and leaves dest alone.
    assert gtfs_zip.exists()


def test_a_station_with_no_trips_is_still_tolerated(gtfs_zip):
    """The line between an archive this loader cannot read and an archive that says
    nothing calls at a stop. The second is data: the index simply has no entry for
    that station, and every consumer reads that as no routes. Only the first is a
    failed load, which is what keeps this change from turning a quiet night into an
    outage."""
    write_gtfs_zip(
        gtfs_zip,
        members={
            "stops.txt": csv_text(STOPS_COLS, ROUTE_STOP_ROWS),
            "shapes.txt": csv_text(SHAPES_COLS, ()),
            "trips.txt": csv_text(TRIPS_COLS, [{"route_id": "1", "trip_id": "t1"}]),
            # t1 calls at 101N only, so 103 is a station with no trips.
            "stop_times.txt": csv_text(
                STOP_TIMES_COLS, [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}]
            ),
        },
    )
    assert static_data.load_subway_station_routes() == {"101": ["1"]}
    # An archive with headers and no trip rows at all is the same kind of quiet to this
    # loader, which still loads it rather than failing. It never gets here from upstream:
    # since A02 the validator refuses a trips.txt or stop_times.txt with no usable row
    # (test_a_member_with_no_usable_row_is_refused), so this is the loader's own
    # tolerance, not a publication the app would promote.
    write_gtfs_zip(
        gtfs_zip,
        members={
            "stops.txt": csv_text(STOPS_COLS, ROUTE_STOP_ROWS),
            "shapes.txt": csv_text(SHAPES_COLS, ()),
            "trips.txt": csv_text(TRIPS_COLS, ()),
            "stop_times.txt": csv_text(STOP_TIMES_COLS, ()),
        },
    )
    assert static_data.load_subway_station_routes() == {}


# ---------------- the station complex index (claude/subway-hub-definition) ----------------
#
# THE FINDING, from the label-band diagnosis of 2026-09-25: F9's hub rule counted trunks
# per stop_id, so 108 of its 124 rings sat on stops transfers.txt joins to no other (the
# B beside the C on Central Park West, the D beside the R on Fourth Avenue), and 22 of the
# 35 real complexes had no ring at all, Times Square among them, because each of its five
# stops carries one trunk. (The diagnosis said 102 and 26, grouping stops within 250 m;
# these are the same counts over this table.) THE RULING: a hub is a station complex, and
# the complexes are transfers.txt's cross-stop rows closed over by union-find.

SUBWAY_FIXTURE = Path(__file__).parent / "fixtures" / "subway_gtfs"
CENSUS_FIXTURE = Path(__file__).parents[2] / "tests" / "e2e" / "fixtures" / "subway_stops_real.json"


def test_a_three_stop_chain_is_one_complex():
    """THE UNION-FIND, which the live table cannot test: every complex it publishes is
    fully meshed, so a pairwise match would pass on it. A publication that lists A-B and
    B-C and leaves A-C implied is still one station, and only a closure says so. The
    mutation this kills is the closure replaced by a direct-partner lookup, which gives C
    the complex "B" and splits one station into two."""
    pairs = [("A", "B"), ("B", "A"), ("B", "C"), ("C", "B")]
    index = static_data.derive_subway_station_complexes(pairs, ["A", "B", "C", "D"], {})
    assert index == {"A": "A", "B": "A", "C": "A", "D": "D"}


def test_a_stop_in_no_row_is_its_own_complex_and_self_rows_join_nothing():
    """The ruling's wording, and the table's other kind of row: 101 -> 101 is a minimum
    transfer time within one stop, not a complex, so it must not join 101 to anything."""
    pairs = static_data._parse_transfer_pairs(
        _zip_of(
            {
                "transfers.txt": "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
                "101,101,2,180\n,103,2,180\n103,,2,180\n"
            }
        )
    )
    assert pairs == []
    index = static_data.derive_subway_station_complexes(pairs, ["101", "103"], {})
    assert index == {"101": "101", "103": "103"}


def test_a_non_station_id_links_the_closure_but_is_never_a_key():
    """An id stops.txt has no parent row for can still be the one link between two
    stations that are, so it joins the closure; it is never served, because the markers
    are the parent stations and nothing else. Platform ids fold up to their parent first,
    which is the space every consumer keys on."""
    pairs = [("101N", "X9"), ("X9", "103")]
    index = static_data.derive_subway_station_complexes(pairs, ["101", "103"], {"101N": "101"})
    assert index == {"101": "101", "103": "101"}


def test_a_transfer_type_that_says_no_change_joins_nothing():
    """Review finding H7. GTFS type 3 says a transfer between the two stops is NOT
    possible, and 4 and 5 are in-seat continuations that name a vehicle, so none of
    them makes one station of two stops. A blank type is the spec's 0 and does."""
    body = (
        "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"
        "101,103,3,\n103,101,3,\n"
        "101,105,4,\n105,107,5,\n"
        "107,109,,\n109,111,0,\n111,113,1,\n"
    )
    pairs = static_data._parse_transfer_pairs(_zip_of({"transfers.txt": body}))
    assert pairs == [("107", "109"), ("109", "111"), ("111", "113")]
    index = static_data.derive_subway_station_complexes(
        pairs, ["101", "103", "105", "107", "109", "111", "113"], {}
    )
    assert index["103"] == "103" and index["105"] == "105"
    assert {index[s] for s in ("107", "109", "111", "113")} == {"107"}


def test_the_complex_id_is_the_smallest_station_in_it_whatever_the_order():
    """The rule the docstring states and review finding H10 found no test holding: the
    SMALLEST STATION id, not the first one stops.txt lists (station order is the
    MTA's, and an id that moved with it would move every complex it names) and not
    the union-find's root, which can be a non-station id that sorts lower."""
    # Stations listed out of order: the id is still "101".
    index = static_data.derive_subway_station_complexes([("103", "101")], ["103", "101"], {})
    assert index == {"103": "101", "101": "101"}
    # A non-station id that sorts below both links them and is never the id.
    index = static_data.derive_subway_station_complexes(
        [("0X", "103"), ("0X", "101")], ["103", "101"], {}
    )
    assert index == {"103": "101", "101": "101"}


def _zip_of(members: dict[str, str]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def _write_committed_archive(path, *, drop=()):
    """The committed live members as a zip. Only the two the complex loader opens are
    here (fixtures/subway_gtfs/README.md says where they came from and what is not)."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("stops.txt", "transfers.txt"):
            if name not in drop:
                zf.writestr(name, (SUBWAY_FIXTURE / name).read_bytes())


def test_the_committed_archive_complex_table(gtfs_zip):
    """THE COMPLEX TABLE ON THE REAL PUBLICATION, through the real loader."""
    _write_committed_archive(gtfs_zip)
    index = static_data.load_subway_station_complexes()

    # Times Square: five stops, one complex, named by its smallest station id. The four
    # platforms' own trunks are one each (1/2/3, 7, S, N/Q/R/W) plus the A/C/E at the
    # Port Authority stop, which is why F9's per-stop count gave none of them a ring.
    times_square = {"127", "725", "902", "A27", "R16"}
    assert {sid for sid, cid in index.items() if cid == "127"} == times_square
    # Court Square, the ruling's other named hub: the 7, the G and the E/M, three stops.
    assert {sid for sid, cid in index.items() if cid == "719"} == {"719", "F09", "G22"}
    # A stop in no row is alone, by its own id. Van Cortlandt Park is the 1's terminal.
    assert index["101"] == "101"
    assert "101" not in {cid for sid, cid in index.items() if sid != "101"}
    # And the shared-track stops the diagnosis found ringed are alone too, which is what
    # takes their rings away: 96 St on Central Park West, Carroll St, 25 St.
    for alone in ("A19", "F21", "R35"):
        assert [sid for sid, cid in index.items() if cid == index[alone]] == [alone]

    # THE STOP COUNT IS UNCHANGED: every one of the 496 parent stations is keyed, and
    # nothing else is. A transfers.txt id outside the stations would be a coined stop.
    with zipfile.ZipFile(gtfs_zip) as zf:
        stations = static_data.parse_member(zf, "stops.txt", static_data._parse_stations_rows)
    assert len(stations) == 496
    assert set(index) == set(stations)
    groups = {}
    for sid, cid in index.items():
        groups.setdefault(cid, []).append(sid)
    assert len(groups) == 444
    assert sorted(len(g) for g in groups.values() if len(g) > 1)[-1] == 5
    assert sum(1 for g in groups.values() if len(g) > 1) == 35
    # A complex id is always one of its own stations, never a coined value.
    assert all(cid in group for cid, group in groups.items())


def test_load_subway_station_complexes_raises_without_transfers(gtfs_zip):
    """The loader half of "a zip lacking transfers.txt fails the load". The validator
    half is the [transfers.txt] case of the F1 tests above; this is the loader refusing
    to swallow, so a warmup that somehow reached it still fails rather than serving every
    stop as a complex of one."""
    _write_committed_archive(gtfs_zip, drop=("transfers.txt",))
    with pytest.raises(KeyError):
        static_data.load_subway_station_complexes()


# ---------------- the floor: at least one cross-stop row ----------------
#
# THE OPERATOR'S RULING ON REVIEW FINDING H2. Presence alone let a transfers.txt that
# says nothing load as every stop alone under "ready". The validator now runs the
# loader's own transfer parser and rejects a table that yields no pair.

_TRANSFER_HEADER = "from_stop_id,to_stop_id,transfer_type,min_transfer_time\n"


def _loadable_members(transfers: str) -> dict[str, str]:
    return loadable_members(stop_rows=ROUTE_STOP_ROWS, **{"transfers.txt": transfers})


@pytest.mark.parametrize(
    "transfers",
    [
        pytest.param(_TRANSFER_HEADER, id="headers-only"),
        pytest.param(_TRANSFER_HEADER + "101,101,2,180\n103,103,2,180\n", id="self-rows-only"),
        pytest.param("from_stop,to_stop,transfer_type\n101,103,2\n", id="renamed-columns"),
        pytest.param(_TRANSFER_HEADER + "101,103,3,\n103,101,3,\n", id="no-transfer-possible-only"),
    ],
)
def test_validate_rejects_a_transfers_table_with_no_cross_stop_row(gtfs_zip, transfers):
    """Each shape of "present and saying nothing" is refused, and the error names the
    member, because an operator reads it off /api/status as last_download_error."""
    write_gtfs_zip(gtfs_zip, members=_loadable_members(transfers))
    with zipfile.ZipFile(gtfs_zip) as zf:
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_archive(zf)
    assert "transfers.txt" in str(err.value)


def test_validate_accepts_one_cross_stop_row_and_the_live_table(gtfs_zip):
    """The floor is one, and the live table is far above it: the committed 613 rows (150
    of them cross-stop) pass the same validator."""
    write_gtfs_zip(gtfs_zip, members=_loadable_members(_TRANSFER_HEADER + "101,103,2,180\n"))
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)
    live = (SUBWAY_FIXTURE / "transfers.txt").read_text(encoding="utf-8-sig")
    members = _loadable_members(live)
    members["stops.txt"] = (SUBWAY_FIXTURE / "stops.txt").read_text(encoding="utf-8-sig")
    write_gtfs_zip(gtfs_zip, members=members)
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)
        assert len(static_data._parse_transfer_pairs(zf)) == 150


async def test_a_publication_whose_transfers_say_nothing_fails_the_load(gtfs_zip, monkeypatch):
    """End to end, the way test_a_reduced_publication_fails_the_load_rather_than_serving_
    an_empty_index drives F1: the cached archive is rejected AND the redownload of the same
    publication is rejected, so the load raises and the group reports failed, rather than
    reaching ready with every stop a complex of one."""
    members = _loadable_members(_TRANSFER_HEADER)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    reduced = buf.getvalue()
    downloads = []
    real_staged_fetch = static_shared.staged_fetch

    async def publishes(url, dest, validate, **kwargs):
        async def transfer(u, stage, deadline_s):
            downloads.append(1)
            stage.write_bytes(reduced)

        await real_staged_fetch(url, dest, validate, **kwargs, download=transfer)

    monkeypatch.setattr(static_data, "staged_fetch", publishes)
    write_gtfs_zip(gtfs_zip, members=members)
    with pytest.raises(static_data.StaticValidationError) as err:
        await static_data.load_subway_stops()
    assert "transfers.txt" in str(err.value)
    assert downloads == [1]


def test_the_e2e_census_fixture_agrees_with_this_archive(gtfs_zip):
    """tests/e2e/fixtures/subway_stops_real.json is the /api/subway-stops payload the
    real loaders produced from the full live archive, and the e2e census reads it. Its
    routes come from stop_times.txt, which is not committed, so they cannot be checked
    here; everything the committed members DO determine is held to them, so the two
    fixtures cannot drift apart without this saying so."""
    _write_committed_archive(gtfs_zip)
    index = static_data.load_subway_station_complexes()
    with zipfile.ZipFile(gtfs_zip) as zf:
        stations = static_data.parse_member(zf, "stops.txt", static_data._parse_stations_rows)
    census = json.loads(CENSUS_FIXTURE.read_text())
    assert [row["id"] for row in census] == list(stations)
    assert {row["id"]: row["complex_id"] for row in census} == index
    assert {row["id"]: row["name"] for row in census} == {
        sid: s["name"] for sid, s in stations.items()
    }
    assert all(row["routes"] for row in census), "every station lists its routes"


# ---------------- A02 and F08: every member the load reads is read at validation ----------------
#
# THE FINDING (audit 2026-10-12 A02, generalizing 2026-09-05 F08): the validator required
# five members and parsed two (stops.txt, transfers.txt). One undecodable byte in trips.txt
# or stop_times.txt was promoted over a working cache, then failed every warmup attempt for
# MAX_AGE_DAYS, because the cached-archive check runs the same validator and kept answering
# that the archive was fine; in shapes.txt it was promoted and drew 0 route lines. THE RULE
# (decisions D1 and D2): the validator is the loader's parser. Each member is read at
# validation through the one generator the load reads it with, and must yield a row.


def with_invalid_byte(text: str) -> bytes:
    """The member with one 0xFF (never valid UTF-8) at the end of its first data row."""
    data = text.encode("utf-8")
    first_eol = data.index(b"\n")
    end = data.index(b"\n", first_eol + 1)
    if data[end - 1 : end] == b"\r":
        end -= 1
    return data[:end] + b"\xff" + data[end:]


def _drain_cached_shapes():
    """The generator the route-shapes loader reads, over the cached shapes.txt. The loader
    itself degrades to [] around it, so this is where its failure is visible."""
    with zipfile.ZipFile(static_data.SUBWAY_GTFS_ZIP) as zf, zf.open("shapes.txt") as raw:
        for _ in static_data._iter_shape_points(raw):
            pass


# THE COUPLING THE RULE PROMISES, one row per member the load reads: the member and the
# load's own reader of it. A member the validator refuses but no reader reads, or one a
# reader reads that the validator lets through, breaks a row of this table.
FIVE_READERS = {
    "stops.txt": lambda: static_data._parse_stops(),
    "transfers.txt": lambda: static_data.load_subway_station_complexes(),
    "trips.txt": lambda: static_data.load_subway_station_routes(),
    "stop_times.txt": lambda: static_data.load_subway_station_routes(),
    "shapes.txt": _drain_cached_shapes,
}


@pytest.mark.parametrize("member", sorted(FIVE_READERS))
def test_one_undecodable_byte_in_any_member_is_refused_and_would_fail_the_load(gtfs_zip, member):
    """Refused at validation with the member named and the codec's own words (what an
    operator reads on /api/status), and the same archive, validation bypassed by placing
    it as the cache, makes the load's reader of that member raise.

    shapes.txt by its OWN gate: the route lines are decorative, so validate_subway_geometry
    reads them (and staged_fetch decides from what is cached whether its failure refuses
    or degrades), and the always-gate accepts an archive whose only fault is there."""
    members = loadable_members(stop_rows=ROUTE_STOP_ROWS)
    members[member] = with_invalid_byte(members[member])
    write_gtfs_zip(gtfs_zip, members=members)
    gate = (
        static_data.validate_subway_geometry
        if member == "shapes.txt"
        else static_data.validate_subway_archive
    )

    with zipfile.ZipFile(gtfs_zip) as zf:
        with pytest.raises(static_data.StaticValidationError) as err:
            gate(zf)
        if member == "shapes.txt":
            static_data.validate_subway_archive(zf)  # not the always-gate's question
    assert str(err.value).startswith(f"{member} is not readable as CSV (")
    assert "can't decode byte 0xff" in str(err.value)
    assert err.value.member == member

    if member == "shapes.txt":
        # F08's symptom, and on a cold start the accepted cost: the loader degrades to no
        # route lines rather than failing.
        assert static_data.load_subway_route_shapes() == []
    with pytest.raises(UnicodeDecodeError):
        FIVE_READERS[member]()


@pytest.mark.parametrize(
    ("member", "body", "what"),
    [
        pytest.param("trips.txt", csv_text(TRIPS_COLS, ()), "trips", id="trips-headers-only"),
        pytest.param(
            "stop_times.txt",
            csv_text(STOP_TIMES_COLS, ()),
            "stop times",
            id="stop-times-headers-only",
        ),
    ],
)
def test_a_member_with_no_usable_row_is_refused(gtfs_zip, member, body, what):
    """The floor, the same one stops.txt and transfers.txt have had: a member that
    parses cleanly to nothing is refused, by its own name. The join floor below would
    refuse these too; the order (each member's own floor first) is what keeps the
    operator's message pointing at the member that is empty."""
    write_gtfs_zip(gtfs_zip, members=loadable_members(**{member: body}))
    with zipfile.ZipFile(gtfs_zip) as zf:
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_archive(zf)
    assert str(err.value) == f"{member} yielded no usable {what}"


GEOMETRY_NONE = (
    "shapes.txt yielded no usable drawable route shapes "
    "(two points of one shape whose id names a route)"
)

# Every way the route lines can come out empty: (shapes.txt body, or None for absent;
# the geometry gate's message). FORMAT DRIFT is the case the review found on a cold
# start: a shape_id the reader does not recognize as a route's draws nothing.
BAD_GEOMETRY = {
    "one 0xFF": (lambda: with_invalid_byte(csv_text(SHAPES_COLS, SHAPE_ROWS)), None),
    "header only": (lambda: csv_text(SHAPES_COLS, ()), GEOMETRY_NONE),
    "of no route": (
        lambda: csv_text(SHAPES_COLS, [dict(row, shape_id="s1") for row in SHAPE_ROWS]),
        GEOMETRY_NONE,
    ),
    "format drift": (
        lambda: csv_text(SHAPES_COLS, [dict(row, shape_id="1-N01R") for row in SHAPE_ROWS]),
        GEOMETRY_NONE,
    ),
    "one point": (lambda: csv_text(SHAPES_COLS, SHAPE_ROWS[:1]), GEOMETRY_NONE),
    # Two shapes of one route, one point each: a line is drawn per SHAPE, so this draws
    # nothing although the route has two points (the branch's review, F8).
    "one point per shape": (
        lambda: csv_text(SHAPES_COLS, [SHAPE_ROWS[0], dict(SHAPE_ROWS[1], shape_id="1..N02R")]),
        GEOMETRY_NONE,
    ),
    "absent": (None, "missing required member(s): shapes.txt"),
}


def _geometry_members(variant: str, **base) -> dict:
    members = loadable_members(**base)
    body, _message = BAD_GEOMETRY[variant]
    if body is None:
        del members["shapes.txt"]
    else:
        members["shapes.txt"] = body()
    return members


@pytest.mark.parametrize("variant", sorted(BAD_GEOMETRY))
def test_the_geometry_gate_refuses_every_empty_drawing_and_the_always_gate_does_not(
    gtfs_zip, variant
):
    """validate_subway_geometry is the route lines' gate and asks what the route-shapes
    loader would draw: at least two points of one shape whose id names a route, read
    whole. Each variant fails it by name and passes the always-gate, which no longer
    reads shapes.txt; the load's own answer agrees (no route line drawn)."""
    write_gtfs_zip(gtfs_zip, members=_geometry_members(variant))
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_geometry(zf)
    message = BAD_GEOMETRY[variant][1]
    if message is None:
        assert str(err.value).startswith("shapes.txt is not readable as CSV (")
    else:
        assert str(err.value) == message
    assert err.value.member == "shapes.txt"
    assert _drawn(static_data.load_subway_route_shapes()) == []


def _shapes_damaged_in(members: dict, compression: int) -> bytes:
    """An archive whose shapes.txt is compressed with `compression` and has its compressed
    stream damaged, so READING it raises what that decompressor raises (zlib's error, bz2's
    errno-less OSError, lzma's LZMAError) while the archive opens and lists. Deflate's first
    block header is set to the block type RFC 1951 reserves (as a02b's fixture does), since
    a flipped data byte in a stream this short is as likely to reach the CRC check first;
    the other two have a byte of their stream flipped. Located through header_offset (see
    the deflate test above for why)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body, compress_type=compression if name == "shapes.txt" else None)
    raw = bytearray(buf.getvalue())
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as zf:
        offset = zf.getinfo("shapes.txt").header_offset
    name_len = int.from_bytes(raw[offset + 26 : offset + 28], "little")
    extra_len = int.from_bytes(raw[offset + 28 : offset + 30], "little")
    payload = offset + 30 + name_len + extra_len
    if compression == zipfile.ZIP_DEFLATED:
        raw[payload] |= 0b110  # BTYPE 11
    else:
        raw[payload + 10] ^= 0xFF
    return bytes(raw)


DAMAGED_SHAPES = {
    "deflate": (zipfile.ZIP_DEFLATED, "error"),
    "bzip2": (zipfile.ZIP_BZIP2, "OSError"),
    "lzma": (zipfile.ZIP_LZMA, "LZMAError"),
}


@pytest.mark.parametrize("kind", sorted(DAMAGED_SHAPES))
async def test_a_shapes_txt_that_will_not_decompress_is_named_by_member(
    gtfs_zip, monkeypatch, fresh_archive_status, kind
):
    """WHAT EACH DECOMPRESSOR RAISES, NAMED BY MEMBER (the branch's review, F4 and F9).
    The geometry gate's refusal names shapes.txt whichever decompressor refused the
    bytes, so a cold start keys the degraded member by its name, never "archive". The
    always-gate does not read the member and passes; the message carries the exception's
    type (and only its type where that is an OSError, whose text can name a path)."""
    compression, type_name = DAMAGED_SHAPES[kind]
    publication = _shapes_damaged_in(loadable_members(stop_rows=ROUTE_STOP_ROWS), compression)
    gtfs_zip.write_bytes(publication)
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)
    with pytest.raises(static_data.StaticValidationError) as err:
        static_shared.validate_archive(gtfs_zip, static_data.validate_subway_geometry)
    assert err.value.member == "shapes.txt"
    assert str(err.value).startswith(f"shapes.txt is not readable ({type_name}")
    gtfs_zip.unlink()

    _publish(monkeypatch, {"bytes": publication}, [])
    await static_data.load_subway_stops()
    assert gtfs_zip.read_bytes() == publication, "promoted on a cold start"
    degraded = static_shared.archive_status()["subway"]["degraded_members"]
    assert list(degraded) == ["shapes.txt"] and degraded["shapes.txt"] == str(err.value)


def test_the_geometry_gate_accepts_one_drawable_shape(gtfs_zip):
    """The converse: two points of one route's shape is a line, and the gate passes it."""
    write_gtfs_zip(gtfs_zip, members=loadable_members())
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_geometry(zf)


# ---------------- the join floor: the routes-per-station index has a station in it ----------------

JOIN_MESSAGE = (
    "stop_times.txt yielded no usable call of a trip that has a route_id at a stop that "
    "folds to a parent station (trips.txt, stop_times.txt and stops.txt do not join, so the "
    "routes-per-station index would be empty)"
)
_RENAMED_TRIPS_COLS = [
    "route",
    "trip_id",
    "service_id",
    "trip_headsign",
    "direction_id",
    "shape_id",
]
_RENAMED_STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent"]

# Every member reads and holds a row in each of these, so the members' own floors pass
# them; each breaks the JOIN the index is built from, which is MR2 F1's empty index
# reached through readable tables.
JOIN_BREAKS = {
    "route_id renamed": {
        "trips.txt": csv_text(
            _RENAMED_TRIPS_COLS, [{"route": "1", "trip_id": "t1", "shape_id": "1..N01R"}]
        )
    },
    "route_id blank": {"trips.txt": csv_text(TRIPS_COLS, [dict(TRIP_ROWS[0], route_id="")])},
    "calls of no trip": {
        "stop_times.txt": csv_text(STOP_TIMES_COLS, [dict(STOP_TIME_ROWS[0], trip_id="zz")])
    },
    "parent_station renamed": {
        "stops.txt": csv_text(
            _RENAMED_STOPS_COLS,
            [{**row, "parent": row.get("parent_station", "")} for row in STOP_ROWS],
        )
    },
    "calls at no station": {
        "stop_times.txt": csv_text(STOP_TIMES_COLS, [dict(STOP_TIME_ROWS[0], stop_id="999N")])
    },
    # The platform folds, but to a parent that is no station (no location_type 1 row):
    # the index would hold 103 and no served station (the branch's review, F7).
    "calls at a parent that is no station": {
        "stops.txt": csv_text(
            STOPS_COLS,
            [*STOP_ROWS, dict(STOP_ROWS[1], stop_id="103N", parent_station="103")],
        ),
        "stop_times.txt": csv_text(STOP_TIMES_COLS, [dict(STOP_TIME_ROWS[0], stop_id="103N")]),
    },
}


@pytest.mark.parametrize("case", sorted(JOIN_BREAKS))
def test_an_archive_whose_tables_do_not_join_is_refused(gtfs_zip, case):
    """THE JOIN FLOOR (the operator's ruling on the branch's review). Refused with a
    message that says the index would be empty, and the load agrees: the same archive
    placed as the cache builds an index with no served station in it."""
    write_gtfs_zip(gtfs_zip, members=loadable_members(**JOIN_BREAKS[case]))
    with zipfile.ZipFile(gtfs_zip) as zf:
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_archive(zf)
    assert str(err.value) == JOIN_MESSAGE
    stations = static_data.load_subway_stations()
    assert stations, "the stops floor passes: the stations are there"
    assert not set(static_data.load_subway_station_routes()) & set(stations)


def test_the_join_floor_finds_the_first_station_a_route_calls_at(gtfs_zip):
    """The converse, and what the floor asks exactly: trip t1 on route 1 calls at the
    platform 101N, which folds to the station 101."""
    write_gtfs_zip(gtfs_zip, members=loadable_members())
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)
        assert static_data._first_station_route(zf) == ("101", "1")


def test_the_join_floor_walks_past_calls_that_do_not_join(gtfs_zip):
    """It asks for ONE joining call anywhere in the table, not of the first row (the
    branch's review, F6): a call of no trip, then a call at no station, then t1 at 101N,
    and the floor finds 101 on the third row."""
    calls = [
        dict(STOP_TIME_ROWS[0], trip_id="zz"),
        dict(STOP_TIME_ROWS[0], stop_id="999N"),
        STOP_TIME_ROWS[0],
    ]
    members = loadable_members(**{"stop_times.txt": csv_text(STOP_TIMES_COLS, calls)})
    write_gtfs_zip(gtfs_zip, members=members)
    with zipfile.ZipFile(gtfs_zip) as zf:
        assert static_data._first_station_route(zf) == ("101", "1")
        static_data.validate_subway_archive(zf)


def test_a_bad_byte_past_the_joins_first_hit_is_refused_by_the_drain(gtfs_zip):
    """THE JOIN STOPS AT ITS FIRST HIT, SO IT IS NOT THE FULL READ. stop_times.txt's
    first row joins, then calls running well past the text reader's first chunk, then
    one 0xFF in the last row: the join finds its station without decoding the byte, and
    the drain, which reads the whole member, is what refuses it. Without the drain this
    archive is promoted and the load is its first full read, which is A02. The A02
    record cannot see this: its stop_times.txt is one chunk, so the join decodes it all."""
    filler = [dict(STOP_TIME_ROWS[0], stop_sequence=str(n)) for n in range(2, 4002)]
    text = csv_text(STOP_TIMES_COLS, STOP_TIME_ROWS + filler).encode("utf-8")
    assert len(text) > 4 * 8192
    bad = text[:-2] + b"\xff" + text[-2:]
    write_gtfs_zip(gtfs_zip, members=loadable_members(**{"stop_times.txt": bad}))
    with zipfile.ZipFile(gtfs_zip) as zf:
        assert static_data._first_station_route(zf) == ("101", "1")
        with pytest.raises(static_data.StaticValidationError) as err:
            static_data.validate_subway_archive(zf)
    assert str(err.value).startswith("stop_times.txt is not readable as CSV (")
    assert "can't decode byte 0xff" in str(err.value)


def test_the_loadable_archive_is_accepted_and_loads_every_layer(gtfs_zip):
    """The converse, so the refusals above cannot pass by refusing everything."""
    write_gtfs_zip(gtfs_zip, members=loadable_members(stop_rows=ROUTE_STOP_ROWS))
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)
    assert static_data.load_subway_station_routes() == {"101": ["1"]}
    assert [r["route"] for r in static_data.load_subway_route_shapes()] == ["1"]


def _zip_bytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _publish(monkeypatch, published: dict, downloads: list):
    """Inject at the TRANSFER, so staged_fetch's stage, validate and promote are real."""
    real_staged_fetch = static_shared.staged_fetch

    async def fetch(url, dest, validate, **kwargs):
        async def transfer(u, stage, deadline_s):
            downloads.append(dest.exists())
            stage.write_bytes(published["bytes"])

        await real_staged_fetch(url, dest, validate, **kwargs, download=transfer)

    monkeypatch.setattr(static_data, "staged_fetch", fetch)


@pytest.fixture
def fresh_archive_status():
    """static_shared's per-archive record is module state that outlives a test."""
    static_shared.reset_archive_status()
    yield
    static_shared.reset_archive_status()


async def test_a_bad_publication_between_good_ones_never_interrupts_the_subway(
    gtfs_zip, monkeypatch, fresh_archive_status
):
    """The test A02's row asked for: a good cache, a bad publication, then a healthy one.

    The bad one is refused at staging, the good cache keeps serving every layer through
    it (stations, the routes index, route lines), archive status records the failed
    download with the member and the codec's words, /api/status publishes that (D7), and
    the healthy publication is promoted on the next attempt."""
    import main as app_module

    write_gtfs_zip(gtfs_zip, members=loadable_members(stop_rows=ROUTE_STOP_ROWS))
    age_file(gtfs_zip, days=static_data.MAX_AGE_DAYS + 10)  # stale: the loader downloads
    good = gtfs_zip.read_bytes()
    bad_members = loadable_members(stop_rows=ROUTE_STOP_ROWS)
    bad_members["trips.txt"] = with_invalid_byte(bad_members["trips.txt"])
    renamed = [dict(row, stop_name="Alpha Renamed") for row in ROUTE_STOP_ROWS]
    healthy = _zip_bytes(loadable_members(stop_rows=renamed))
    published = {"bytes": _zip_bytes(bad_members)}
    downloads: list = []
    _publish(monkeypatch, published, downloads)

    stops = await static_data.load_subway_stops()

    assert stops["101N"]["name"] == "Alpha"  # the good cache, still serving
    assert gtfs_zip.read_bytes() == good  # byte-identical: the bad bytes never landed
    assert "101" in static_data.load_subway_stations()
    assert static_data.load_subway_station_routes() == {"101": ["1"]}
    assert [r["route"] for r in static_data.load_subway_route_shapes()] == ["1"]
    entry = static_shared.archive_status()["subway"]
    assert entry["failed_downloads"] == 1 and entry["last_promoted_at"] is None
    error = entry["last_download_error"]
    assert error.startswith("invalid archive: trips.txt is not readable as CSV ('utf-8' codec")
    assert "can't decode byte 0xff" in error
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        body = (await client.get("/api/status")).json()
    assert body["static_archives"]["subway"]["last_download_error"] == error
    assert body["static_archives"]["subway"]["failed_downloads"] == 1

    published["bytes"] = healthy  # the repair, on the next attempt
    stops = await static_data.load_subway_stops()

    assert stops["101N"]["name"] == "Alpha Renamed"
    assert gtfs_zip.read_bytes() == healthy
    entry = static_shared.archive_status()["subway"]
    assert entry["last_promoted_at"] is not None and entry["last_download_error"] is None
    assert downloads == [True, True]  # one download each, over a cache that was there


def _drawn(routes: list) -> list:
    """The routes the map would draw a line for: a polyline needs two points."""
    return [r["route"] for r in routes if any(len(p) >= 2 for p in r["polylines"])]


async def _status_body() -> dict:
    import main as app_module

    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return (await client.get("/api/status")).json()


@pytest.mark.parametrize("variant", sorted(BAD_GEOMETRY))
async def test_bad_geometry_on_a_cold_start_is_promoted_and_named_on_the_status(
    gtfs_zip, monkeypatch, fresh_archive_status, variant
):
    """THE TWO-MODE RULE, COLD (the operator's ruling: geometry stays decorative). With no
    cache, a publication whose only fault is its route lines is promoted: every station,
    the routes index and the complexes serve, the lines are empty, and /api/status names
    shapes.txt as a degraded member with the gate's own reason. Every deploy is a cold
    start, so this is the arm production meets."""
    publication = _zip_bytes(_geometry_members(variant, stop_rows=ROUTE_STOP_ROWS))
    downloads: list = []
    _publish(monkeypatch, {"bytes": publication}, downloads)

    stops = await static_data.load_subway_stops()

    assert "101N" in stops and downloads == [False]
    assert gtfs_zip.read_bytes() == publication, "promoted"
    assert _drawn(static_data.load_subway_route_shapes()) == []
    assert static_data.load_subway_station_routes() == {"101": ["1"]}
    entry = static_shared.archive_status()["subway"]
    assert entry["last_promoted_at"] is not None
    assert entry["last_download_error"] is None and entry["failed_downloads"] == 0
    assert list(entry["degraded_members"]) == ["shapes.txt"]
    reason = entry["degraded_members"]["shapes.txt"]
    message = BAD_GEOMETRY[variant][1]
    if message is None:
        assert reason.startswith("shapes.txt is not readable as CSV (")
    else:
        assert reason == message
    body = await _status_body()
    assert body["static_archives"]["subway"]["degraded_members"] == {"shapes.txt": reason}


@pytest.mark.parametrize("variant", sorted(BAD_GEOMETRY))
async def test_bad_geometry_over_a_cache_that_draws_is_refused(
    gtfs_zip, monkeypatch, fresh_archive_status, variant
):
    """THE TWO-MODE RULE, WITH A CACHE (2026-09-05's F08): the same publications are
    refused when a cache that passes the always-gate is there, which keeps serving
    byte-identical and keeps drawing its route; the refusal is counted and named, and
    nothing is degraded about the archive still serving."""
    write_gtfs_zip(gtfs_zip, members=loadable_members(stop_rows=ROUTE_STOP_ROWS))
    age_file(gtfs_zip, days=static_data.MAX_AGE_DAYS + 10)
    good = gtfs_zip.read_bytes()
    downloads: list = []
    publication = _zip_bytes(_geometry_members(variant, stop_rows=ROUTE_STOP_ROWS))
    _publish(monkeypatch, {"bytes": publication}, downloads)

    stops = await static_data.load_subway_stops()

    assert "101N" in stops and downloads == [True]
    assert gtfs_zip.read_bytes() == good, "refused: the cache is byte-identical"
    assert _drawn(static_data.load_subway_route_shapes()) == ["1"]
    entry = static_shared.archive_status()["subway"]
    assert entry["last_promoted_at"] is None and entry["failed_downloads"] == 1
    assert entry["last_download_error"].startswith("invalid archive: ")
    assert "shapes.txt" in entry["last_download_error"]
    assert entry["degraded_members"] == {}


@pytest.mark.parametrize("cache", ["serving", "refused by the always-gate"])
async def test_the_loader_hands_the_two_mode_rule_its_own_verdict(
    gtfs_zip, monkeypatch, fresh_archive_status, cache
):
    """ONE VERDICT PER ATTEMPT (the branch's second review round). load_subway_stops
    judges the cache once, and the two-mode rule acts on that judgment rather than
    reading the cache again: a stale cache that serves is kept through bad geometry,
    and a cache the always-gate refuses (platform rows only, no station) is no cache
    to keep, so the publication is promoted with its lines degraded and every station
    serves. The always-gate is counted: it is asked about the cache exactly once."""
    platforms_only = [row for row in ROUTE_STOP_ROWS if row["location_type"] != "1"]
    stops = ROUTE_STOP_ROWS if cache == "serving" else platforms_only
    write_gtfs_zip(gtfs_zip, members=loadable_members(stop_rows=stops))
    age = time.time() - (static_data.MAX_AGE_DAYS + 1) * 86400
    os.utime(gtfs_zip, (age, age))
    before = gtfs_zip.read_bytes()
    asked: list = []
    real = static_data.validate_subway_archive

    def counting(zf):
        if Path(zf.filename) == gtfs_zip:
            asked.append(zf.filename)
        return real(zf)

    monkeypatch.setattr(static_data, "validate_subway_archive", counting)
    publication = _zip_bytes(_geometry_members("header only", stop_rows=ROUTE_STOP_ROWS))
    _publish(monkeypatch, {"bytes": publication}, [])

    await static_data.load_subway_stops()

    assert len(asked) == 1, f"the cache was judged {len(asked)} times in one attempt"
    entry = static_shared.archive_status()["subway"]
    if cache == "serving":
        assert gtfs_zip.read_bytes() == before, "kept through bad geometry"
        assert entry["last_promoted_at"] is None and entry["degraded_members"] == {}
    else:
        assert gtfs_zip.read_bytes() == publication, "promoted over a cache that serves nothing"
        assert list(entry["degraded_members"]) == ["shapes.txt"]


async def test_a_clean_publication_after_a_degraded_cold_start_clears_the_degraded_member(
    gtfs_zip, monkeypatch, fresh_archive_status
):
    """The degraded member describes the archive serving: promoted over by a publication
    whose geometry draws, it is gone, and the lines are back."""
    published = {"bytes": _zip_bytes(_geometry_members("format drift", stop_rows=ROUTE_STOP_ROWS))}
    _publish(monkeypatch, published, [])
    await static_data.load_subway_stops()
    assert static_shared.archive_status()["subway"]["degraded_members"]

    age_file(gtfs_zip, days=static_data.MAX_AGE_DAYS + 10)
    published["bytes"] = _zip_bytes(loadable_members(stop_rows=ROUTE_STOP_ROWS))
    await static_data.load_subway_stops()

    assert static_shared.archive_status()["subway"]["degraded_members"] == {}
    assert _drawn(static_data.load_subway_route_shapes()) == ["1"]


@pytest.mark.parametrize("mode", ["cold", "a cache that serves"])
async def test_a_renamed_route_id_column_is_refused_in_both_modes(
    gtfs_zip, monkeypatch, fresh_archive_status, mode
):
    """The join floor is the always-gate's, so unlike the geometry it refuses whatever is
    cached: cold, nothing is promoted and the load fails (the warmup retries); with a
    cache, the cache serves on, its index intact."""
    if mode != "cold":
        write_gtfs_zip(gtfs_zip, members=loadable_members(stop_rows=ROUTE_STOP_ROWS))
        age_file(gtfs_zip, days=static_data.MAX_AGE_DAYS + 10)
    good = gtfs_zip.read_bytes() if gtfs_zip.exists() else None
    members = loadable_members(stop_rows=ROUTE_STOP_ROWS, **JOIN_BREAKS["route_id renamed"])
    _publish(monkeypatch, {"bytes": _zip_bytes(members)}, [])

    if mode == "cold":
        with pytest.raises(static_data.StaticValidationError):
            await static_data.load_subway_stops()
        assert not gtfs_zip.exists()
    else:
        await static_data.load_subway_stops()
        assert gtfs_zip.read_bytes() == good
        assert static_data.load_subway_station_routes() == {"101": ["1"]}
    entry = static_shared.archive_status()["subway"]
    assert entry["last_promoted_at"] is None
    assert entry["last_download_error"] == f"invalid archive: {JOIN_MESSAGE}"


async def test_an_archive_that_passes_validation_and_fails_the_load_is_discarded(
    gtfs_zip, monkeypatch
):
    """THE RESIDUAL ARM (decision D3), reached by bypassing validation on purpose: a
    reader only the load uses (the stop_times accumulator; the validator drains and walks
    the generator under it, never this) is made to fail on a cache the validator accepts.
    The attempt fails, the cache is discarded, and the next attempt downloads rather than
    accepting the same bytes as valid and fresh for a month."""
    import types

    import main as app_module
    import warmups

    monkeypatch.setattr(app_module, "STATIC_RETRY_S", 0.01)
    write_loadable_gtfs_zip(gtfs_zip)  # valid and fresh: no download is needed
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)  # the premise: the validator accepts it
    real_reader = static_data._parse_trip_stops
    fault = {"on": True}

    def failing_reader(zf):
        if fault["on"]:
            raise csv.Error("a reader the validator does not share, failing")
        return real_reader(zf)

    monkeypatch.setattr(static_data, "_parse_trip_stops", failing_reader)
    app = types.SimpleNamespace(state=types.SimpleNamespace(subway_static_status="loading"))
    seen_at_download: list = []
    published = {"bytes": _zip_bytes(loadable_members())}
    real_staged_fetch = static_shared.staged_fetch

    async def fetch(url, dest, validate, **kwargs):
        async def transfer(u, stage, deadline_s):
            seen_at_download.append((dest.exists(), app.state.subway_static_status))
            fault["on"] = False  # the repair upstream; the load's reader works again
            stage.write_bytes(published["bytes"])

        await real_staged_fetch(url, dest, validate, **kwargs, download=transfer)

    monkeypatch.setattr(static_data, "staged_fetch", fetch)
    task = asyncio.create_task(warmups._warm_subway_static(app))
    try:
        for _ in range(400):
            if app.state.subway_static_status == "ready":
                break
            await asyncio.sleep(0.005)
        assert app.state.subway_static_status == "ready"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    # Exactly one download, made after the failed attempt discarded the cache.
    assert seen_at_download == [(False, "failed")]
    assert app.state.subway_station_routes == {"101": ["1"]}


RESIDUAL_SITES = {
    # site: (the reader only that load path uses, the call that reaches it)
    "stops": ("_parse_stops_rows", lambda: asyncio.run(static_data.load_subway_stops())),
    "station routes": ("_parse_trip_stops", lambda: static_data.load_subway_station_routes()),
    "station complexes": (
        "_parse_transfer_pairs",
        lambda: static_data.load_subway_station_complexes(),
    ),
}


@pytest.mark.parametrize("site", sorted(RESIDUAL_SITES))
def test_every_cached_read_the_residual_arm_wraps_discards_an_unreadable_cache(
    gtfs_zip, monkeypatch, site
):
    """D3 AT EACH SITE. The arm wraps three reads of the cached archive: the stops parse
    in load_subway_stops, the routes-per-station index and the complex index. At each,
    a reader the validator does not share is made to fail with a parse-class error on a
    cache the validator accepts (valid and fresh, so no download), and the cache must be
    gone afterwards, so the next attempt downloads instead of failing on the same bytes
    for a month. The warmup-level test above drives one site end to end; this pins all
    three, since removing the arm from any one of them left every other test green."""
    write_loadable_gtfs_zip(gtfs_zip)
    with zipfile.ZipFile(gtfs_zip) as zf:
        static_data.validate_subway_archive(zf)  # the premise: the validator accepts it
    reader, call = RESIDUAL_SITES[site]

    def failing(*_args):
        raise csv.Error(f"{reader}, a reader the validator does not share, failing")

    monkeypatch.setattr(static_data, reader, failing)

    with pytest.raises(csv.Error):
        call()
    assert not gtfs_zip.exists(), f"the {site} read must discard an archive it cannot read"


# (member, the rows the stand-in generator yields, the gate that reads the member, the
# load's own reader of it, what that reader builds from the stand-in's rows). The member
# is header-only, so a row can only have come from the generator. The trips and calls
# join the fixture's other tables (t1 calls at 101N), so the join floor passes on them.
GENERATORS = {
    "_iter_trip_routes": (
        "trips.txt",
        [("t1", "SENTINEL")],
        lambda zf: static_data.validate_subway_archive(zf),
        lambda: _parse_cached(static_data._parse_trip_routes),
        {"t1": "SENTINEL"},
    ),
    "_iter_trip_stops": (
        "stop_times.txt",
        [("t1", "101N")],
        lambda zf: static_data.validate_subway_archive(zf),
        lambda: _parse_cached(static_data._parse_trip_stops),
        {"t1": ["101N"]},
    ),
    "_iter_shape_points": (
        "shapes.txt",
        [("S", "S..N01R", 1, 40.0, -74.0), ("S", "S..N01R", 2, 40.1, -74.1)],
        lambda zf: static_data.validate_subway_geometry(zf),
        lambda: static_data.load_subway_route_shapes(),
        [{"route": "S", "polylines": [[[40.0, -74.0], [40.1, -74.1]]]}],
    ),
}


def _parse_cached(parse):
    with zipfile.ZipFile(static_data.SUBWAY_GTFS_ZIP) as zf:
        return parse(zf)


@pytest.mark.parametrize("name", sorted(GENERATORS))
def test_the_validator_and_the_load_read_a_member_through_one_generator(
    gtfs_zip, monkeypatch, name
):
    """D1, pinned: one reader per member, two consumers. Replacing the generator with
    one that yields a sentinel row changes BOTH what the validator counts (a member with
    no rows of its own now passes) and what the load builds (the sentinel is in it). A
    consumer that read the CSV itself would miss the sentinel, and that is the drift
    this pins against."""
    member, rows, gate, load, expected = GENERATORS[name]
    header_only = csv_text(
        {"trips.txt": TRIPS_COLS, "stop_times.txt": STOP_TIMES_COLS}.get(member, SHAPES_COLS), ()
    )
    write_gtfs_zip(gtfs_zip, members=loadable_members(**{member: header_only}))
    with zipfile.ZipFile(gtfs_zip) as zf:
        with pytest.raises(static_data.StaticValidationError):
            gate(zf)  # no row of its own: refused

    def yields_the_stand_in(raw):
        yield from rows

    monkeypatch.setattr(static_data, name, yields_the_stand_in)
    with zipfile.ZipFile(gtfs_zip) as zf:
        gate(zf)  # the gate read the stand-in's rows
    assert load() == expected  # and the load built them


# ---------------- D5: validation runs off the serving loop ----------------
#
# A02's repair made the subway validator parse every member its load reads, which on the
# live publication is 1.1 to 1.5 s of CPU (backend/scripts/measure_subway_validation.py,
# recorded in the ledger). Validation runs in two places, staged_fetch on a download and
# every loader's cached-archive check at a load, and both now hand it to a worker thread
# behind an await, so the loop the pollers and /healthz share keeps running.

SLOW_VALIDATION_S = 0.2
# The loop's longest stall inside a validation window must stay under this. A thread
# doing CPU work hands the interpreter lock back every switch interval (5 ms), so the
# heartbeat's gaps are a few milliseconds; inline, the gap IS the validation.
LOOP_STALL_BOUND_S = SLOW_VALIDATION_S / 2


def _slowed(validate, windows: list):
    """The real validator, then CPU until SLOW_VALIDATION_S has passed (a busy-wait,
    not a sleep, because a parse holds the interpreter lock and a sleep would not), its
    (start, end) recorded."""

    def slow(zf):
        started = time.perf_counter()
        validate(zf)
        while time.perf_counter() - started < SLOW_VALIDATION_S:
            pass
        windows.append((started, time.perf_counter()))

    return slow


def _stall_within(stamps: list, windows: list) -> float:
    """The longest stretch inside a validation window during which the heartbeat did not
    tick: each gap between ticks, clipped to each window it overlaps."""
    worst = 0.0
    for a, b in zip(stamps, stamps[1:]):
        for start, end in windows:
            worst = max(worst, min(b, end) - max(a, start))
    return worst


@pytest.mark.parametrize("start", ["cold, staged validation", "cache present, cached check"])
async def test_a_slow_validation_does_not_hold_the_serving_loop(gtfs_zip, monkeypatch, start):
    """D5, pinned at both places validation runs. A 1 ms heartbeat shares the loop with
    load_subway_stops while a validation that takes SLOW_VALIDATION_S runs: from a cold
    cache it is staged_fetch's, with a cache already on disk it is the cached-archive
    check's, the one every process start runs. The heartbeat must keep ticking.

    WHAT THIS TEST NEEDS TO SEE, which is why the first assertion is a precondition and
    not decoration: a validation long enough that holding the loop for it would break
    LOOP_STALL_BOUND_S. With a fast validator the same assertion on the heartbeat would
    pass inline and off the loop alike, and the test would say nothing, so it fails
    instead when the validation it watched was short."""
    windows: list = []
    slow = _slowed(static_data.validate_subway_archive, windows)
    monkeypatch.setattr(static_data, "validate_subway_archive", slow)
    if start.startswith("cold"):
        _publish(monkeypatch, {"bytes": _zip_bytes(loadable_members())}, [])
    else:
        write_loadable_gtfs_zip(gtfs_zip)  # valid and fresh: the cached check, no download
    stamps: list = []
    stop = asyncio.Event()

    async def heartbeat():
        # Stamp on EVERY wake, the last included: the tick that ends a stall is the
        # one after it, and a ticker that checked `stop` first would exit on that
        # wake without recording it, hiding a stall that runs up to the load's return.
        while True:
            stamps.append(time.perf_counter())
            if stop.is_set():
                return
            await asyncio.sleep(0.001)

    ticker = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    try:
        stops = await static_data.load_subway_stops()
    finally:
        stop.set()
        await ticker

    assert "101N" in stops
    assert len(windows) == 1 and windows[0][1] - windows[0][0] >= SLOW_VALIDATION_S
    stall = _stall_within(stamps, windows)
    assert stall < LOOP_STALL_BOUND_S, (
        f"the loop stalled {stall * 1000:.0f} ms inside a "
        f"{(windows[0][1] - windows[0][0]) * 1000:.0f} ms validation"
    )
