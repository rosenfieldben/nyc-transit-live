"""Tests for the static subway GTFS download/cache logic and shape loading.

Small zips are built in tmp_path; SUBWAY_GTFS_ZIP / SUBWAY_GTFS_URL are
monkeypatched so no test touches the network (failure cases point the URL at
a closed local port).
"""

import csv
import io
import os
import time
import zipfile

import httpx
import pytest

import static_data

pytestmark = pytest.mark.anyio

# Nothing listens here: connection refused, instantly.
DEAD_URL = "http://127.0.0.1:9/gtfs_subway.zip"

STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon", "location_type", "parent_station"]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]

STOP_ROWS = [
    {"stop_id": "101", "stop_name": "Alpha", "stop_lat": "40.7", "stop_lon": "-74.0"},
    {"stop_id": "101N", "stop_name": "Alpha", "stop_lat": "40.7", "stop_lon": "-74.0"},
]


def csv_text(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def write_gtfs_zip(path, stop_rows=STOP_ROWS, shape_rows=None, members=None):
    """Write a minimal GTFS zip; `members` overrides the file map entirely."""
    if members is None:
        members = {"stops.txt": csv_text(STOPS_COLS, stop_rows)}
        if shape_rows is not None:
            members["shapes.txt"] = csv_text(SHAPES_COLS, shape_rows)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


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
    write_gtfs_zip(gtfs_zip)

    async def fail(*args):  # any download attempt is a test failure
        raise AssertionError("should not download with a fresh cache")

    monkeypatch.setattr(static_data, "_download_zip", fail)
    stops = await static_data.load_subway_stops()
    assert stops["101N"] == {"name": "Alpha", "lat": 40.7, "lon": -74.0}


async def test_stale_cache_with_failed_download_falls_back(gtfs_zip):
    write_gtfs_zip(gtfs_zip)
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
        write_gtfs_zip(gtfs_zip)

    monkeypatch.setattr(static_data, "_download_zip", fake_download)
    stops = await static_data.load_subway_stops()
    assert len(calls) == 1
    assert "101N" in stops


async def test_malformed_coordinate_rows_skipped(gtfs_zip):
    rows = STOP_ROWS + [
        {"stop_id": "BAD1", "stop_name": "NoCoords", "stop_lat": "", "stop_lon": ""},
        {"stop_id": "BAD2", "stop_name": "Garbage", "stop_lat": "north", "stop_lon": "-74.0"},
    ]
    write_gtfs_zip(gtfs_zip, stop_rows=rows)
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
