"""Tests for the railroad (LIRR / MNR) static GTFS download/cache + parsing.

Small synthetic LIRR/MNR-style zips are built in tmp_path; RAILROAD_STATIC_ZIPS /
RAILROAD_STATIC_URLS are monkeypatched so no test touches the network (failure
cases point the URLs at a closed local port). No real-zip fixture: the real
archives are large and gitignored.

ONE REAL TABLE IS COMMITTED, THOUGH: fixtures/railroad_gtfs/ holds each system's
real routes.txt, 13 LIRR rows and 6 MNR rows copied from the live archives (its
README records the probe and the single deliberate departure). The colour tests
below read those rather than synthetic rows, because what they assert is what the
FEEDS publish, and a synthetic row would only prove the parser agrees with
whatever the test author typed.
"""

import csv
import io
import os
import time
import zipfile
from pathlib import Path

import pytest

import railroad_static

pytestmark = pytest.mark.anyio

# Nothing listens here: connection refused, instantly.
DEAD_URL = "http://127.0.0.1:9/google_transit.zip"

STOPS_COLS = ["stop_id", "stop_name", "stop_lat", "stop_lon"]
TRIPS_COLS = ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"]
SHAPES_COLS = ["shape_id", "shape_pt_sequence", "shape_pt_lat", "shape_pt_lon"]
# Both colour columns are parsed as of claude/railroad-route-colors; the comment
# here used to say route_color was deliberately not read (own palette), which was
# wrong about both this project's rule and what these feeds publish. See
# railroad_static._parse_routes for the corrected reasoning and the probe numbers.
ROUTES_COLS = [
    "route_id",
    "route_short_name",
    "route_long_name",
    "route_color",
    "route_text_color",
]

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
ROUTE_ROWS = [
    {
        "route_id": "5",
        "route_long_name": "Montauk Branch",
        "route_color": "00B2A9",
        "route_text_color": "121212",
    },
    {
        "route_id": "8",
        "route_short_name": "WH",
        "route_long_name": "",
        "route_color": "00A1DE",
        "route_text_color": "121212",
    },
]


def write_railroad_zip(
    path,
    stops=STOP_ROWS,
    trips=TRIP_ROWS,
    shapes=DEFAULT_SHAPE_ROWS,
    routes=ROUTE_ROWS,
    members=None,
):
    """Write a minimal railroad GTFS zip; `members` overrides the file map entirely."""
    if members is None:
        members = {
            "stops.txt": csv_text(STOPS_COLS, stops),
            "trips.txt": csv_text(TRIPS_COLS, trips),
            "shapes.txt": csv_text(SHAPES_COLS, shapes),
            "routes.txt": csv_text(ROUTES_COLS, routes),
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


def test_parse_system_returns_five_tables(tmp_path):
    path = tmp_path / "g.zip"
    write_railroad_zip(path)
    data = railroad_static._parse_system(path)
    assert set(data) == {"stops", "trips", "shapes", "routes", "stop_times"}
    # Full-precision coords (no rounding for stops).
    assert data["stops"]["8"] == {"name": "Hicksville", "lat": 40.768, "lon": -73.531}
    assert data["stops"]["12"]["lat"] == 40.7005
    assert data["routes"]["5"]["long_name"] == "Montauk Branch"
    # stop_times.txt is optional (the default synthetic zip omits it); a missing
    # member degrades to an empty table, not a load failure.
    assert data["stop_times"] == {}


# ---------------- _parse_stop_times + derive_railroad_stop_routes (H5) ----------------
#
# The railroads have no committed trimmed GTFS fixture (the tests build synthetic
# zips inline), so the routes-per-station index is covered with synthetic tables
# here rather than a golden over a trim.

STOP_TIMES_COLS = ["trip_id", "stop_id", "arrival_time", "departure_time", "stop_sequence"]


def test_parse_stop_times_optional_member_present(tmp_path):
    path = tmp_path / "g.zip"
    write_railroad_zip(
        path,
        members={
            "stops.txt": csv_text(STOPS_COLS, STOP_ROWS),
            "trips.txt": csv_text(TRIPS_COLS, TRIP_ROWS),
            "shapes.txt": csv_text(SHAPES_COLS, DEFAULT_SHAPE_ROWS),
            "routes.txt": csv_text(ROUTES_COLS, ROUTE_ROWS),
            "stop_times.txt": csv_text(
                STOP_TIMES_COLS,
                [
                    {"trip_id": "GO5_1", "stop_id": "12", "stop_sequence": "1"},
                    {"trip_id": "GO5_1", "stop_id": "8", "stop_sequence": "2"},
                ],
            ),
        },
    )
    data = railroad_static._parse_system(path)
    assert data["stop_times"] == {"GO5_1": ["12", "8"]}


def test_derive_railroad_stop_routes_joins_flat_stops():
    trips = {
        "GO5_1": {"route_id": "5", "direction_id": "0", "shape_id": "5", "headsign": None},
        "GO8_1": {"route_id": "8", "direction_id": "1", "shape_id": None, "headsign": None},
    }
    stop_times = {
        "GO5_1": ["12", "8"],  # route 5 serves Jamaica + Hicksville
        "GO8_1": ["12"],  # route 8 serves Jamaica
    }
    idx = railroad_static.derive_railroad_stop_routes(trips, stop_times)
    # Flat railroad stops join directly; Jamaica (12) is served by both routes.
    assert idx == {"12": ["5", "8"], "8": ["5"]}


def test_derive_railroad_stop_routes_ignores_routeless_trips():
    trips = {"t": {"route_id": None, "direction_id": None, "shape_id": None, "headsign": None}}
    idx = railroad_static.derive_railroad_stop_routes(trips, {"t": ["12"]})
    assert idx == {}


def test_parse_routes_reads_names_blank_to_none(tmp_path):
    path = tmp_path / "g.zip"
    write_railroad_zip(path)
    routes = railroad_static._parse_system(path)["routes"]
    assert routes["5"] == {
        "long_name": "Montauk Branch",
        "short_name": None,
        "color": "00B2A9",
        "text_color": "121212",
    }
    # route 8 carries a short_name but a blank long_name.
    assert routes["8"] == {
        "long_name": None,
        "short_name": "WH",
        "color": "00A1DE",
        "text_color": "121212",
    }


def test_parse_routes_missing_member_degrades_to_empty(tmp_path):
    # routes.txt is optional: a zip without it must still load (empty routes table,
    # stops/trips/shapes intact), NOT fail the whole system load.
    path = tmp_path / "g.zip"
    write_railroad_zip(
        path,
        members={
            "stops.txt": csv_text(STOPS_COLS, STOP_ROWS),
            "trips.txt": csv_text(TRIPS_COLS, TRIP_ROWS),
            "shapes.txt": csv_text(SHAPES_COLS, DEFAULT_SHAPE_ROWS),
        },
    )
    data = railroad_static._parse_system(path)
    assert data["routes"] == {}
    assert "8" in data["stops"] and "GO5_1" in data["trips"]  # the rest loaded fine


# ---------------- routes.txt colours, against each feed's real table ----------------
#
# These read fixtures/railroad_gtfs/, which is the live routes.txt for each system
# (see that directory's README for the probe and its one deliberate departure). The
# point of using the real tables is that the claim under test is about what the
# FEEDS publish, which synthetic rows cannot establish.

RAILROAD_FIXTURES = Path(__file__).parent / "fixtures" / "railroad_gtfs"


def parse_fixture_routes(tmp_path, fixture_name):
    """Parse one system's committed real routes.txt through the production parser."""
    path = tmp_path / "g.zip"
    write_railroad_zip(
        path,
        members={
            "stops.txt": csv_text(STOPS_COLS, STOP_ROWS),
            "trips.txt": csv_text(TRIPS_COLS, TRIP_ROWS),
            "shapes.txt": csv_text(SHAPES_COLS, DEFAULT_SHAPE_ROWS),
            "routes.txt": (RAILROAD_FIXTURES / fixture_name).read_text(encoding="utf-8"),
        },
    )
    return railroad_static._parse_system(path)["routes"]


def test_parse_routes_reads_both_colours_verbatim_from_the_lirr_feed(tmp_path):
    routes = parse_fixture_routes(tmp_path, "lirr_routes.txt")
    assert len(routes) == 13  # the live feed's route count, not a synthetic subset

    # VERBATIM: the hex arrives exactly as published. Upper case, no leading "#",
    # nothing normalised. Asserted as literals rather than against a transform of
    # the file, so a parser that lower-cased or prepended "#" fails here.
    assert routes["1"]["color"] == "00985F"
    assert routes["1"]["text_color"] == "FFFFFF"
    assert routes["9"]["color"] == "C60C30"
    assert routes["2"]["text_color"] == "121212"

    # Every route carries both, which is the fact the old comment denied. The one
    # exception is the fixture's own blanked row, below.
    assert all(r["color"] for rid, r in routes.items() if rid != "11")
    assert all(r["text_color"] for r in routes.values())

    # A COLOUR IS NOT A ROUTE ID: Ronkonkoma and Greenport share one purple.
    assert routes["4"]["color"] == routes["13"]["color"] == "A626AA"

    # The LIRR feed publishes no route_short_name COLUMN at all, so short_name is
    # None for all thirteen. This is the absent-column path, not a blank cell.
    assert all(r["short_name"] is None for r in routes.values())
    assert routes["5"]["long_name"] == "Montauk Branch"


def test_parse_routes_reads_both_colours_verbatim_from_the_mnr_feed(tmp_path):
    routes = parse_fixture_routes(tmp_path, "mnr_routes.txt")
    assert len(routes) == 6  # the live feed's route count

    assert routes["1"]["color"] == "009B3A"  # Hudson, green
    assert routes["2"]["color"] == "0039A6"  # Harlem, blue
    assert all(r["text_color"] == "FFFFFF" for r in routes.values())

    # THE NEW HAVEN FAMILY SHARES ONE RED. Four route ids, one colour, so anything
    # keying a map by colour would collapse New Canaan, Danbury and Waterbury into
    # New Haven.
    assert [routes[rid]["color"] for rid in ("3", "4", "5", "6")] == ["EE0034"] * 4
    assert routes["3"]["long_name"] == "New Haven"
    assert routes["6"]["long_name"] == "Waterbury"


def test_parse_routes_blank_colour_is_none_and_the_two_columns_are_independent(tmp_path):
    # The fixture blanks route_color on LIRR 11 (Belmont Park) and leaves its
    # route_text_color filled: the one row where the columns disagree. A blank
    # column is None, never "" and never a fallback colour, and text_color comes
    # from its OWN column, so reading it out of route_color's would answer None.
    routes = parse_fixture_routes(tmp_path, "lirr_routes.txt")
    assert routes["11"]["color"] is None
    assert routes["11"]["text_color"] == "FFFFFF"
    assert routes["11"]["long_name"] == "Belmont Park"  # the row parsed, it is not skipped


def test_parse_routes_carries_hex_verbatim_without_normalising(tmp_path):
    # Synthetic on purpose, and separate from the fixture tests above: neither live
    # feed publishes a "#" or lower case, so the only way to pin that the parser
    # does not "helpfully" normalise is to hand it something a normaliser would
    # change. Whitespace is the one thing stripped (GTFS exporters pad cells).
    rows = [
        {"route_id": "h", "route_color": "#00985F", "route_text_color": "#FFFFFF"},
        {"route_id": "lo", "route_color": "00985f", "route_text_color": "ffffff"},
        {"route_id": "pad", "route_color": "  00985F  ", "route_text_color": " FFFFFF "},
    ]
    path = tmp_path / "g.zip"
    write_railroad_zip(path, routes=rows)
    routes = railroad_static._parse_system(path)["routes"]
    assert routes["h"]["color"] == "#00985F"  # the "#" is neither stripped nor added
    assert routes["h"]["text_color"] == "#FFFFFF"
    assert routes["lo"]["color"] == "00985f"  # case is left exactly as published
    assert routes["lo"]["text_color"] == "ffffff"
    assert routes["pad"]["color"] == "00985F"  # whitespace only
    assert routes["pad"]["text_color"] == "FFFFFF"


def test_parse_routes_absent_colour_columns_are_none(tmp_path):
    # A publication whose routes.txt has no colour columns at all (the pre-2026
    # shape this project assumed) still parses: both fields are None, and the names
    # are unaffected.
    path = tmp_path / "g.zip"
    write_railroad_zip(
        path,
        members={
            "stops.txt": csv_text(STOPS_COLS, STOP_ROWS),
            "trips.txt": csv_text(TRIPS_COLS, TRIP_ROWS),
            "shapes.txt": csv_text(SHAPES_COLS, DEFAULT_SHAPE_ROWS),
            "routes.txt": csv_text(
                ["route_id", "route_long_name"], [{"route_id": "5", "route_long_name": "Montauk"}]
            ),
        },
    )
    routes = railroad_static._parse_system(path)["routes"]
    assert routes["5"] == {
        "long_name": "Montauk",
        "short_name": None,
        "color": None,
        "text_color": None,
    }


def test_parse_routes_skips_blank_id_and_dedups_first_wins(tmp_path):
    rows = [
        {"route_id": "", "route_long_name": "No Id"},  # blank route_id: skipped
        {"route_id": "9", "route_long_name": "First"},
        {"route_id": "9", "route_long_name": "Second"},  # duplicate route_id: first wins
    ]
    path = tmp_path / "g.zip"
    write_railroad_zip(path, routes=rows)
    routes = railroad_static._parse_system(path)["routes"]
    assert set(routes) == {"9"}
    assert routes["9"]["long_name"] == "First"


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
        # A zip that PARSES CLEANLY but yields zero stops (an upstream bad publish:
        # header-only stops.txt). It has to invalidate the cache like the structural
        # failures above, not just return the empty parse: the warmup now treats an
        # all-systems-empty load as a failed attempt and retries, and a retry that
        # re-parses the same fresh-by-mtime bytes could never heal.
        lambda path: write_railroad_zip(path, stops=[]),
    ],
    ids=["corrupt-zip", "zip-missing-stops.txt", "zip-parses-to-zero-stops"],
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


# ---------------- build_railroad_route_shapes (synthetic parsed tables) ----------------
#
# The builder is a pure transform over the already-parsed trips/shapes tables, so
# these feed dicts directly (no zip), mirroring
# test_static_data.test_variant_dedup_keeps_branch_drops_express. Trips carry only
# the two fields the builder reads (route_id, shape_id).


def _trip(route_id, shape_id):
    return {"route_id": route_id, "shape_id": shape_id}


def test_route_builder_keeps_branch_drops_express():
    # Trunk (20 pts) + an express that is a strict subset of the trunk points
    # (0% new -> dropped) + a branch that diverges (mostly new -> kept).
    trunk = [[0.0, float(i)] for i in range(20)]
    express = [[0.0, float(i)] for i in range(0, 10, 2)]  # all points lie on the trunk
    branch = [[float(i), 0.0] for i in range(15)]  # only [0, 0] shared with the trunk
    shapes = {"T": trunk, "E": express, "B": branch}
    trips = {"t1": _trip("5", "T"), "t2": _trip("5", "E"), "t3": _trip("5", "B")}

    routes = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert len(routes) == 1
    assert routes[0]["route"] == "5"
    kept = routes[0]["polylines"]
    assert trunk in kept and branch in kept  # trunk + real branch survive
    assert express not in kept  # shared-track variant collapses
    assert len(kept) == 2


def test_route_builder_distinct_branches_survive_new_haven_case():
    # One route, several distinct branch shapes across several trips (the MNR New
    # Haven line: New Canaan / Danbury / Waterbury). Disjoint geometry, so all four
    # are >5% new and none collapse.
    trunk = [[40.0, float(i) / 100] for i in range(30)]
    new_canaan = [[41.0, float(i) / 100] for i in range(20)]
    danbury = [[42.0, float(i) / 100] for i in range(20)]
    waterbury = [[43.0, float(i) / 100] for i in range(20)]
    shapes = {"trunk": trunk, "nc": new_canaan, "dan": danbury, "wat": waterbury}
    trips = {
        "a": _trip("1", "trunk"),
        "b": _trip("1", "nc"),
        "c": _trip("1", "dan"),
        "d": _trip("1", "wat"),
    }

    routes = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert len(routes) == 1
    assert len(routes[0]["polylines"]) == 4  # trunk + 3 distinct branches, none drop


def test_route_builder_reverse_direction_collapses():
    # The reverse-direction shape is the same point set in reversed order, so it
    # reads as 0% new (the point-set test is order-independent) and collapses.
    fwd = [[0.0, float(i)] for i in range(10)]
    rev = list(reversed(fwd))
    shapes = {"F": fwd, "R": rev}
    trips = {"t1": _trip("2", "F"), "t2": _trip("2", "R")}

    routes = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert len(routes) == 1
    assert len(routes[0]["polylines"]) == 1  # one direction kept, the reverse dropped


def test_route_builder_carries_both_colours_and_leaves_polylines_alone():
    # The builder is where routes.txt meets geometry, so this pins that the colours
    # ride along WITHOUT the geometry changing: the same trips/shapes with and
    # without a routes table must produce identical polylines.
    shapes = {"a": [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]}
    trips = {"t1": _trip("3", "a")}
    table = {
        "3": {
            "long_name": "New Haven",
            "short_name": None,
            "color": "EE0034",
            "text_color": "FFFFFF",
        }
    }
    with_colours = railroad_static.build_railroad_route_shapes(trips, shapes, table)
    geometry_only = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert with_colours[0]["color"] == "EE0034"
    assert with_colours[0]["text_color"] == "FFFFFF"
    assert geometry_only[0]["color"] is None
    assert geometry_only[0]["text_color"] is None
    # THE POLYLINES ARE UNTOUCHED, which is the half of this branch that must not
    # move: adding colour to the payload is additive, not a geometry change.
    assert with_colours[0]["polylines"] == geometry_only[0]["polylines"]


def test_route_builder_colour_survives_a_route_with_no_name():
    # A route can carry a colour and no name (the two columns are independent), and
    # the colour must not be dropped along with the missing name.
    shapes = {"a": [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]}
    trips = {"t1": _trip("7", "a")}
    table = {"7": {"long_name": None, "short_name": None, "color": "6E3219", "text_color": None}}
    built = railroad_static.build_railroad_route_shapes(trips, shapes, table)
    assert built[0]["name"] is None
    assert built[0]["color"] == "6E3219"
    assert built[0]["text_color"] is None


def test_route_builder_blank_and_degenerate_shapes_contribute_nothing():
    # Blank/None shape_ids are ignored; a route whose only shape is degenerate
    # (<2 points) yields no entry at all.
    shapes = {"good": [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]], "deg": [[5.0, 5.0]]}
    trips = {
        "t1": _trip("5", "good"),
        "t2": _trip("5", ""),  # blank shape_id skipped
        "t3": _trip("5", None),  # None shape_id skipped
        "t4": _trip("9", "deg"),  # route 9's only shape is a single point
        "t5": _trip("9", ""),
    }

    routes = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert [r["route"] for r in routes] == ["5"]  # route 9 has no usable geometry
    assert routes[0]["polylines"] == [shapes["good"]]


def test_route_builder_shared_shape_appears_under_both_routes_sorted():
    shared = [[0.0, 0.0], [0.0, 1.0], [0.0, 2.0]]
    shapes = {"sh": shared}
    trips = {"t1": _trip("9", "sh"), "t2": _trip("3", "sh")}

    routes = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert [r["route"] for r in routes] == ["3", "9"]  # output sorted by route_id
    assert routes[0]["polylines"] == [shared]
    assert routes[1]["polylines"] == [shared]


def test_route_builder_equal_length_variants_ordered_deterministically():
    # Two disjoint branches of equal length: the output order must be pinned by
    # shape_id, not by hash-salted set iteration, so it is reproducible across
    # process restarts (the subway builder gets this from insertion order). Under
    # the old unsorted-set iteration this assertion was PYTHONHASHSEED-dependent.
    branch_a = [[10.0, float(i)] for i in range(5)]
    branch_z = [[20.0, float(i)] for i in range(5)]  # same length, disjoint geometry
    shapes = {"z": branch_z, "a": branch_a}
    trips = {"t1": _trip("7", "z"), "t2": _trip("7", "a")}

    routes = railroad_static.build_railroad_route_shapes(trips, shapes)
    assert len(routes) == 1
    # sorted(shape_ids) orders "a" before "z"; the stable length sort preserves it.
    assert routes[0]["polylines"] == [branch_a, branch_z]
