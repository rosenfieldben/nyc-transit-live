"""Unit tests for the shape-variant selection in bus_static._process_zip."""

import csv
import io
import json
import zipfile

import pytest

import bus_static
from bus_static import _process_zip

TRIPS_COLS = [
    "route_id",
    "service_id",
    "trip_id",
    "trip_headsign",
    "direction_id",
    "block_id",
    "shape_id",
]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]


def csv_text(columns, rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    return buf.getvalue()


def make_zip(tmp_path, trips, shapes):
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("trips.txt", csv_text(TRIPS_COLS, trips))
        zf.writestr("shapes.txt", csv_text(SHAPES_COLS, shapes))
    return path


def trip(route, direction, shape, n=1):
    """n trip rows for the same route/direction/shape."""
    return [
        {
            "route_id": route,
            "trip_id": f"{route}-{direction}-{shape}-{i}",
            "direction_id": direction,
            "shape_id": shape,
        }
        for i in range(n)
    ]


def shape(shape_id, points):
    return [
        {
            "shape_id": shape_id,
            "shape_pt_sequence": str(seq),
            "shape_pt_lat": str(lat),
            "shape_pt_lon": str(lon),
        }
        for seq, lat, lon in points
    ]


SA = shape("SA", [(1, 40.10, -73.10), (2, 40.20, -73.20)])
SB = shape("SB", [(1, 40.50, -73.50), (2, 40.60, -73.60), (3, 40.70, -73.70)])
SA_GEO = [[40.10, -73.10], [40.20, -73.20]]
SB_GEO = [[40.50, -73.50], [40.60, -73.60], [40.70, -73.70]]


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bus_static, "BUS_CACHE_DIR", cache)
    return cache


def read_route(cache_dir, route_id):
    return json.loads((cache_dir / f"{route_id}.json").read_text())


def test_most_used_variant_wins(tmp_path, cache_dir):
    z = make_zip(tmp_path, trip("M1", "0", "SA", n=1) + trip("M1", "0", "SB", n=3), SA + SB)
    written = _process_zip(z, skip_routes=set())
    assert written == {"M1"}
    assert read_route(cache_dir, "M1")["directions"] == [SB_GEO]


def test_tie_breaks_to_lexicographically_larger_shape(tmp_path, cache_dir):
    z = make_zip(tmp_path, trip("M1", "0", "SA", n=2) + trip("M1", "0", "SB", n=2), SA + SB)
    _process_zip(z, skip_routes=set())
    assert read_route(cache_dir, "M1")["directions"] == [SB_GEO]


def test_one_polyline_per_direction_sorted(tmp_path, cache_dir):
    z = make_zip(tmp_path, trip("M1", "1", "SB") + trip("M1", "0", "SA"), SA + SB)
    _process_zip(z, skip_routes=set())
    # direction "0" sorts before "1"
    assert read_route(cache_dir, "M1")["directions"] == [SA_GEO, SB_GEO]


def test_degenerate_direction_dropped_individually(tmp_path, cache_dir):
    one_point = shape("SP", [(1, 40.9, -73.9)])
    z = make_zip(tmp_path, trip("M1", "0", "SA") + trip("M1", "1", "SP"), SA + one_point)
    _process_zip(z, skip_routes=set())
    assert read_route(cache_dir, "M1")["directions"] == [SA_GEO]


def test_route_with_only_degenerate_shapes_not_written(tmp_path, cache_dir):
    one_point = shape("SP", [(1, 40.9, -73.9)])
    z = make_zip(tmp_path, trip("M1", "0", "SP"), one_point)
    assert _process_zip(z, skip_routes=set()) == set()
    assert not (cache_dir / "M1.json").exists()


def test_skip_routes_respected(tmp_path, cache_dir):
    z = make_zip(tmp_path, trip("M1", "0", "SA"), SA)
    assert _process_zip(z, skip_routes={"M1"}) == set()
    assert not (cache_dir / "M1.json").exists()


def test_invalid_route_id_skipped(tmp_path, cache_dir):
    z = make_zip(tmp_path, trip("../evil", "0", "SA"), SA)
    assert _process_zip(z, skip_routes=set()) == set()
    assert list(cache_dir.iterdir()) == []


def test_points_sorted_by_sequence(tmp_path, cache_dir):
    out_of_order = shape("SA", [(2, 40.20, -73.20), (1, 40.10, -73.10)])
    z = make_zip(tmp_path, trip("M1", "0", "SA"), out_of_order)
    _process_zip(z, skip_routes=set())
    assert read_route(cache_dir, "M1")["directions"] == [SA_GEO]


def test_coordinates_rounded_to_five_decimals(tmp_path, cache_dir):
    precise = shape("SA", [(1, 40.123456789, -73.987654321), (2, 40.2, -73.3)])
    z = make_zip(tmp_path, trip("M1", "0", "SA"), precise)
    _process_zip(z, skip_routes=set())
    assert read_route(cache_dir, "M1")["directions"][0][0] == [40.12346, -73.98765]


def test_malformed_shape_rows_skipped(tmp_path, cache_dir):
    rows = SA + [
        {
            "shape_id": "SA",
            "shape_pt_sequence": "3",
            "shape_pt_lat": "garbage",
            "shape_pt_lon": "-73.0",
        }
    ]
    z = make_zip(tmp_path, trip("M1", "0", "SA"), rows)
    _process_zip(z, skip_routes=set())
    assert read_route(cache_dir, "M1")["directions"] == [SA_GEO]


def test_shared_shape_written_to_every_selecting_route(tmp_path, cache_dir):
    # Two routes whose representative shape is the SAME shape_id: both route
    # files must be written (a shape_id->single-pair map would drop one).
    z = make_zip(tmp_path, trip("M1", "0", "SA") + trip("M2", "0", "SA"), SA)
    written = _process_zip(z, skip_routes=set())
    assert written == {"M1", "M2"}
    assert read_route(cache_dir, "M1")["directions"] == [SA_GEO]
    assert read_route(cache_dir, "M2")["directions"] == [SA_GEO]


def test_missing_direction_id_bucketed_separately(tmp_path, cache_dir):
    no_dir = [{"route_id": "M1", "trip_id": "M1-x", "direction_id": "", "shape_id": "SB"}]
    z = make_zip(tmp_path, trip("M1", "0", "SA") + no_dir, SA + SB)
    _process_zip(z, skip_routes=set())
    # "?" bucket sorts after "0", so both polylines appear, SA first.
    assert read_route(cache_dir, "M1")["directions"] == [SA_GEO, SB_GEO]


# ---------------- _build_index_sync: per-borough failure + deadline ----------------


class _FakeStream:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield self._data


class _FakeBuildClient:
    def __init__(self, data=b"zipbytes"):
        self._data = data

    def stream(self, method, url):
        return _FakeStream(self._data)


def test_download_borough_enforces_whole_transfer_deadline(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bus_static, "BUS_CACHE_DIR", cache)
    monkeypatch.setattr(bus_static, "BUS_ZIP_DEADLINE_S", -1)  # any elapsed exceeds it
    bus_static._stop.clear()
    with pytest.raises(TimeoutError):
        bus_static._download_borough(_FakeBuildClient(), "queens", "http://x", set())
    assert list(cache.glob("*.part")) == []  # the temp file is cleaned up


def test_build_index_records_a_failed_borough_and_keeps_the_rest(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bus_static, "BUS_CACHE_DIR", cache)
    monkeypatch.setattr(bus_static, "MANIFEST_PATH", cache / "_manifest.json")
    monkeypatch.setattr(bus_static, "_partial", False)
    bus_static._stop.clear()

    failing = "queens"

    def fake_download(client, key, url, skip_routes):
        if key == failing:
            raise TimeoutError("deadline exceeded")  # e.g. an over-deadline download
        return {f"route-{key}"}

    monkeypatch.setattr(bus_static, "_download_borough", fake_download)
    routes = bus_static._build_index_sync()

    assert f"route-{failing}" not in routes
    assert len(routes) == len(bus_static.BUS_GTFS_URLS) - 1  # the other boroughs succeeded
    manifest = json.loads((cache / "_manifest.json").read_text())
    assert manifest["failed"] == [failing]  # recorded, not a total build failure
    assert bus_static.is_partial() is True


def test_download_borough_raises_build_stopped_on_shutdown(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bus_static, "BUS_CACHE_DIR", cache)
    bus_static._stop.set()  # shutdown signalled before/at the download
    try:
        with pytest.raises(bus_static._BuildStopped):
            bus_static._download_borough(_FakeBuildClient(), "queens", "http://x", set())
        assert list(cache.glob("*.part")) == []  # temp file cleaned up
    finally:
        bus_static._stop.clear()  # module-global; don't leak to other tests


def test_build_index_aborts_without_manifest_on_shutdown(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(bus_static, "BUS_CACHE_DIR", cache)
    monkeypatch.setattr(bus_static, "MANIFEST_PATH", cache / "_manifest.json")
    bus_static._stop.clear()

    calls = []

    def fake_download(client, key, url, skip_routes):
        calls.append(key)
        if len(calls) == 2:
            raise bus_static._BuildStopped  # shutdown mid-build, on the 2nd borough
        return {f"route-{key}"}

    monkeypatch.setattr(bus_static, "_download_borough", fake_download)
    try:
        routes = bus_static._build_index_sync()
        # Returned the routes gathered so far; did NOT record a failed borough
        # and did NOT write a (partial) manifest the next startup would trust.
        assert routes == {"route-manhattan"}  # first borough only
        assert not (cache / "_manifest.json").exists()
    finally:
        bus_static._stop.clear()
