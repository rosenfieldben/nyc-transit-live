"""Tests for the static subway GTFS download/cache logic and shape loading.

Small zips are built in tmp_path; SUBWAY_GTFS_ZIP / SUBWAY_GTFS_URL are
monkeypatched so no test touches the network (failure cases point the URL at
a closed local port).
"""

import csv
import io
import json
import os
import time
import zipfile
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


def write_loadable_gtfs_zip(path, stop_rows=STOP_ROWS, shape_rows=()):
    """Write a zip that passes validate_subway_archive (C5 seam).

    The validator requires stops.txt, shapes.txt, trips.txt, stop_times.txt AND
    transfers.txt to be PRESENT, so every archive a cache-lifecycle test hands the loader
    carries all five. Header-only is enough for four of them and keeps each test's subject
    unchanged: an empty shapes.txt yields the same [] route lines a missing one used to, an
    empty stop_times.txt yields the same {} routes-per-station index, and an empty
    transfers.txt makes every station a complex of one.

    THE LAST TWO ARE NEW AS OF THE F1 BRANCH. This docstring claimed trips.txt was
    required for a while when it was not, which is its own small lesson about a comment
    outliving the tuple it describes; both are genuinely required now and
    backend/static_data.py's _REQUIRED_MEMBERS says why. transfers.txt joined them on
    claude/subway-hub-definition, for the station complexes.
    """
    write_gtfs_zip(
        path,
        members={
            "stops.txt": csv_text(STOPS_COLS, stop_rows),
            "trips.txt": csv_text(TRIPS_COLS, ()),
            "shapes.txt": csv_text(SHAPES_COLS, shape_rows),
            "stop_times.txt": csv_text(STOP_TIMES_COLS, ()),
            "transfers.txt": csv_text(TRANSFERS_COLS, ()),
        },
    )


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

    async def fail(*args):  # any download attempt is a test failure
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

    async def fake_download():
        calls.append(1)
        write_loadable_gtfs_zip(gtfs_zip)

    monkeypatch.setattr(static_data, "_download_zip", fake_download)
    stops = await static_data.load_subway_stops()
    assert len(calls) == 1
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
    """A stop_times.txt that is present and unreadable. This is the case a required
    member set alone does NOT cover: the archive is structurally complete, so
    validate_subway_archive passes it, and only the loader refusing to swallow turns
    it into a failed load. It is the mutation target for the removed except clause."""
    body = csv_text(STOP_TIMES_COLS, [{"trip_id": "t1", "stop_id": "101N", "stop_sequence": "1"}])
    with zipfile.ZipFile(gtfs_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("stops.txt", csv_text(STOPS_COLS, ROUTE_STOP_ROWS))
        zf.writestr("shapes.txt", csv_text(SHAPES_COLS, ()))
        zf.writestr("trips.txt", csv_text(TRIPS_COLS, [{"route_id": "1", "trip_id": "t1"}]))
        zf.writestr("stop_times.txt", body)
        zf.writestr("transfers.txt", csv_text(TRANSFERS_COLS, ()))
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

    # THE PREMISE, asserted rather than assumed: this archive is structurally complete,
    # so require_members passes it. That is what makes this the case only the loader's
    # own refusal to swallow can catch, and what makes it the mutation target for the
    # deleted except clause. Without this assertion the test could be passing because
    # the zip had become unopenable, which the bad-zip test above already covers.
    with zipfile.ZipFile(gtfs_zip) as zf:
        assert set(zf.namelist()) == {
            "stops.txt",
            "shapes.txt",
            "trips.txt",
            "stop_times.txt",
            "transfers.txt",
        }
        static_data.validate_subway_archive(zf)  # raises if the premise is wrong

    with pytest.raises(Exception):
        static_data.load_subway_station_routes()


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
        "transfers.txt": csv_text(TRANSFERS_COLS, ()),
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
        "transfers.txt": csv_text(TRANSFERS_COLS, ()),
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
    # An archive with headers and no trip rows at all is the same kind of quiet, and
    # is still a load rather than a failure.
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
