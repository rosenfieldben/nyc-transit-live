"""Tests for the railroad (LIRR / MNR) static GTFS download/cache + parsing.

Small synthetic LIRR/MNR-style zips are built in tmp_path; RAILROAD_STATIC_ZIPS /
RAILROAD_STATIC_URLS are monkeypatched so no test touches the network (failure
cases point the URLs at a closed local port). No real-zip fixture: the real
archives are large and gitignored.
"""

import csv
import io
import os
import time
import zipfile

import pytest

import railroad_static

pytestmark = pytest.mark.anyio

# Nothing listens here: connection refused, instantly.
DEAD_URL = "http://127.0.0.1:9/google_transit.zip"

STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
TRIPS_COLS = ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]

# Numeric/opaque stop_ids (no N/S suffix) and shape_ids that would NOT match the
# subway shape regex, mirroring the railroad GTFS shape.
STOP_ROWS = [
    {"stop_id": "8", "stop_name": "Hicksville", "stop_lat": "40.768", "stop_lon": "-73.531"},
    {"stop_id": "12", "stop_name": "Jamaica", "stop_lat": "40.7005", "stop_lon": "-73.8095"},
]
TRIP_ROWS = [
    {
        "route_id": "5",
        "service_id": "A",
        "trip_id": "GO5_1",
        "trip_headsign": "Babylon",
        "direction_id": "0",
        "shape_id": "5",
    },
    {
        "route_id": "8",
        "service_id": "A",
        "trip_id": "GO8_1",
        "trip_headsign": "Montauk",
        "direction_id": "1",
        "shape_id": "",
    },  # blank shape_id
    {
        "route_id": "3",
        "service_id": "A",
        "trip_id": "GO3_1",
        "trip_headsign": "",
        "direction_id": "",
        "shape_id": "",
    },  # all blank optionals
]


def csv_text(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


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


DEFAULT_SHAPE_ROWS = shape_rows("5", [(40.70, -74.00), (40.71, -74.01)])


def write_railroad_zip(
    path, stops=STOP_ROWS, trips=TRIP_ROWS, shapes=DEFAULT_SHAPE_ROWS, members=None
):
    """Write a minimal railroad GTFS zip; `members` overrides the file map entirely."""
    if members is None:
        members = {
            "stops.txt": csv_text(STOPS_COLS, stops),
            "trips.txt": csv_text(TRIPS_COLS, trips),
            "shapes.txt": csv_text(SHAPES_COLS, shapes),
        }
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


@pytest.fixture
def rail_paths(tmp_path, monkeypatch):
    """Point both systems at tmp zip paths and dead URLs by default."""
    zips = {"LIRR": tmp_path / "gtfs_lirr.zip", "MNR": tmp_path / "gtfs_mnr.zip"}
    monkeypatch.setattr(railroad_static, "RAILROAD_STATIC_ZIPS", zips)
    monkeypatch.setattr(
        railroad_static, "RAILROAD_STATIC_URLS", {"LIRR": DEAD_URL, "MNR": DEAD_URL}
    )
    return zips


def age_file(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


# ---------------- _parse_system (direct, no network) ----------------


def test_parse_system_returns_three_tables(tmp_path):
    path = tmp_path / "g.zip"
    write_railroad_zip(path)
    data = railroad_static._parse_system(path)
    assert set(data) == {"stops", "trips", "shapes"}
    # Full-precision coords (no rounding for stops).
    assert data["stops"]["8"] == {"name": "Hicksville", "lat": 40.768, "lon": -73.531}
    assert data["stops"]["12"]["lat"] == 40.7005


def test_parse_system_skips_malformed_coordinate_rows(tmp_path):
    rows = STOP_ROWS + [
        {"stop_id": "BAD1", "stop_name": "NoCoords", "stop_lat": "", "stop_lon": ""},
        {"stop_id": "BAD2", "stop_name": "Garbage", "stop_lat": "north", "stop_lon": "-73.0"},
    ]
    path = tmp_path / "g.zip"
    write_railroad_zip(path, stops=rows)
    stops = railroad_static._parse_system(path)["stops"]
    assert "8" in stops
    assert "BAD1" not in stops and "BAD2" not in stops


def test_parse_system_trip_fields_blank_to_none(tmp_path):
    path = tmp_path / "g.zip"
    write_railroad_zip(path)
    trips = railroad_static._parse_system(path)["trips"]
    assert trips["GO5_1"] == {
        "route_id": "5",
        "direction_id": "0",
        "shape_id": "5",
        "headsign": "Babylon",
    }
    assert trips["GO8_1"]["shape_id"] is None  # blank shape_id -> None
    assert trips["GO8_1"]["direction_id"] == "1"
    # All-blank optionals -> None, but route_id kept.
    assert trips["GO3_1"]["direction_id"] is None
    assert trips["GO3_1"]["shape_id"] is None
    assert trips["GO3_1"]["headsign"] is None
    assert trips["GO3_1"]["route_id"] == "3"


def test_parse_system_skips_rows_without_trip_id(tmp_path):
    trips = TRIP_ROWS + [{"route_id": "9", "service_id": "A", "trip_id": "", "shape_id": "9"}]
    path = tmp_path / "g.zip"
    write_railroad_zip(path, trips=trips)
    parsed = railroad_static._parse_system(path)["trips"]
    assert set(parsed) == {"GO5_1", "GO8_1", "GO3_1"}  # the blank trip_id row dropped


def test_parse_system_duplicate_trip_id_first_wins(tmp_path):
    trips = [
        {
            "route_id": "5",
            "service_id": "A",
            "trip_id": "DUP",
            "trip_headsign": "First",
            "direction_id": "0",
            "shape_id": "5",
        },
        {
            "route_id": "9",
            "service_id": "B",
            "trip_id": "DUP",
            "trip_headsign": "Second",
            "direction_id": "1",
            "shape_id": "9",
        },
    ]
    path = tmp_path / "g.zip"
    write_railroad_zip(path, trips=trips)
    parsed = railroad_static._parse_system(path)["trips"]
    assert parsed["DUP"]["route_id"] == "5" and parsed["DUP"]["headsign"] == "First"


def test_parse_system_shapes_ordered_and_rounded(tmp_path):
    rows = [  # deliberately out of sequence order, with high-precision coords
        {
            "shape_id": "5",
            "shape_pt_sequence": "2",
            "shape_pt_lat": "40.7111149",
            "shape_pt_lon": "-74.0222261",
        },
        {
            "shape_id": "5",
            "shape_pt_sequence": "1",
            "shape_pt_lat": "40.700000",
            "shape_pt_lon": "-74.000000",
        },
        {
            "shape_id": "5",
            "shape_pt_sequence": "3",
            "shape_pt_lat": "40.7333372",
            "shape_pt_lon": "-74.0444418",
        },
        {
            "shape_id": "BAD",
            "shape_pt_sequence": "x",
            "shape_pt_lat": "40.7",
            "shape_pt_lon": "-74.0",
        },
    ]
    path = tmp_path / "g.zip"
    write_railroad_zip(path, shapes=rows)
    shapes = railroad_static._parse_system(path)["shapes"]
    # Sorted by sequence (input was 2, 1, 3) and rounded to 5 decimals.
    assert shapes["5"] == [[40.7, -74.0], [40.71111, -74.02223], [40.73334, -74.04444]]
    assert "BAD" not in shapes  # malformed sequence -> no usable point


# ---------------- _load_one: download/cache behavior ----------------


async def test_fresh_cache_parsed_without_downloading(rail_paths, monkeypatch):
    write_railroad_zip(rail_paths["LIRR"])

    async def fail(system):  # any download attempt is a test failure
        raise AssertionError("should not download with a fresh cache")

    monkeypatch.setattr(railroad_static, "_download_zip", fail)
    data = await railroad_static._load_one("LIRR")
    assert data["stops"]["8"]["name"] == "Hicksville"
    assert data["trips"]["GO5_1"]["route_id"] == "5"


async def test_stale_cache_with_failed_download_falls_back(rail_paths):
    write_railroad_zip(rail_paths["LIRR"])
    age_file(rail_paths["LIRR"], days=40)  # past MAX_AGE_DAYS; the dead URL fails fast
    data = await railroad_static._load_one("LIRR")
    assert data is not None and "8" in data["stops"]


async def test_missing_cache_with_failed_download_returns_none(rail_paths):
    assert not rail_paths["MNR"].exists()
    data = await railroad_static._load_one("MNR")
    assert data is None  # no crash, just None


@pytest.mark.parametrize(
    "make_bad_cache",
    [
        lambda path: path.write_bytes(b"this is not a zip archive"),
        lambda path: write_railroad_zip(path, members={"agency.txt": "agency_id\nLI\n"}),
    ],
    ids=["corrupt-zip", "zip-missing-stops.txt"],
)
async def test_unusable_fresh_cache_redownloads_exactly_once(
    rail_paths, monkeypatch, make_bad_cache
):
    make_bad_cache(rail_paths["LIRR"])  # fresh mtime; only the recovery download should fire
    calls = []

    async def fake_download(system):
        calls.append(system)
        write_railroad_zip(rail_paths[system])

    monkeypatch.setattr(railroad_static, "_download_zip", fake_download)
    data = await railroad_static._load_one("LIRR")
    assert calls == ["LIRR"]  # exactly one re-download
    assert "8" in data["stops"]


# ---------------- load_railroad_static: per-system independence ----------------


async def test_one_system_failure_does_not_block_the_other(rail_paths):
    write_railroad_zip(rail_paths["LIRR"])  # LIRR present; MNR missing + dead URL
    result = await railroad_static.load_railroad_static()
    assert set(result) == {"LIRR", "MNR"}
    assert result["LIRR"] is not None and "8" in result["LIRR"]["stops"]
    assert result["MNR"] is None
