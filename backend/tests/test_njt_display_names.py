"""Tests for njt_static.display_name: NJ Transit's all-caps names, shown in ordinary case.

THE WORLD THESE TESTS PIN. Between contract monitor runs #425 and #426
(2026-11-02/03) NJ Transit upper-cased every stop name and trip headsign of its rail
GTFS. fixtures/njt_display_names_2026-11-06.tsv carries every one of them from the
2026-11-06 capture: all 229 stop names in stops.txt order (the 65 light-rail rows no
trip calls at included) and all 38 distinct headsigns, sorted, each beside the
string a rider is shown. The published column is copied verbatim from that capture.
The displayed column was written by display_name and then read row by row before it
was committed, so changing any row changes what riders read and hear, and has to be
argued for in the diff that does it.

THE OTHER HALF is the committed 2026-09-01 fixture under fixtures/njt_gtfs/, which is
mixed case: every name and headsign in it must pass through byte for byte, because
the goldens in test_njt_static.py are built on those strings.

The capture itself is not committed. Only its names are, in the table, and the seven
stops.txt, trips.txt and routes.txt rows the parser tests below quote verbatim. One
more routes.txt row below is synthetic and is marked as such.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

import njt_static
from conftest import golden_fixture_guard

TABLE = Path(__file__).parent / "fixtures" / "njt_display_names_2026-11-06.tsv"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "njt_gtfs"
golden = golden_fixture_guard(
    FIXTURE_DIR / "stops.txt",
    "backend/scripts/gen_njt_fixture.py (needs NJT_USERNAME and NJT_PASSWORD)",
)

# The 2026-11-06 stops.txt header and two of its rows, verbatim: Penn Station New
# York and Newark Penn, the two identity stations, under their new ids.
STOPS_2026_11_06 = (
    "stop_id,stop_code,stop_name,stop_desc,stop_lat,stop_lon,zone_id,stop_url,"
    "location_type,parent_station,stop_timezone,wheelchair_boarding\n"
    '105,NY,"NEW YORK PENN STATION","NYP",40.750046,-73.992358,329,,,,,\n'
    '107,NP,"NEWARK PENN STATION","NWK",40.734221,-74.164554,336,,,,,\n'
)

# The 2026-11-06 trips.txt header and three of its rows, verbatim: one headsign with
# an ordinal and an acronym, one that is nothing but an acronym, and one whose train
# number is lettered ("MNBN BUS").
TRIPS_2026_11_06 = (
    "trip_id,route_id,service_id,trip_headsign,trip_short_name,direction_id,block_id,"
    "shape_id,wheelchair_accessible\n"
    '1,1,1,30TH ST. PHL.,"4628",0,4628,1,\n'
    '163,3,1,MSU,"6273",1,6273,163,\n'
    '1652,6,4,HOBOKEN,"MNBN BUS",0,MNBN BUS,461,\n'
)

# The 2026-11-06 routes.txt header and two of its rows, verbatim. Route long names
# are still mixed case there, and route short names are codes.
ROUTES_2026_11_06 = (
    "route_id,agency_id,route_short_name,route_long_name,route_desc,route_type,"
    "route_url,route_color,route_text_color\n"
    '4,NJT,"HBLR","Hudson-Bergen Light Rail",,113,,008080,\n'
    '9,NJT,"NEC","Northeast Corridor",,113,,DD3439,\n'
)

# SYNTHETIC, NOT FROM THE CAPTURE: a routes.txt row written for this test. Every real
# route long name is still mixed case (16 of 16 on 2026-11-06), so no real row can
# show whether the parser would recase an all-caps one.
ROUTES_SYNTHETIC_ALL_CAPS = '99,NJT,"PJL","PORT JERVIS LINE",,113,,000000,\n'


def _table() -> list[tuple[str, str, str]]:
    lines = TABLE.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "kind\tpublished\tdisplayed"
    assert lines[-1] == ""  # newline-terminated, and no blank row inside
    rows = []
    for line in lines[1:-1]:
        kind, published, displayed = line.split("\t")
        rows.append((kind, published, displayed))
    return rows


def _rows(kind: str) -> list[tuple[str, str]]:
    return [(published, displayed) for k, published, displayed in _table() if k == kind]


def _parse(table: str, parser):
    return parser(io.BytesIO(table.encode()))


def _fixture_column(member: str, column: str) -> list[str]:
    with (FIXTURE_DIR / member).open(encoding="utf-8-sig", newline="") as f:
        return [row[column] for row in csv.DictReader(f)]


# ---------------------------------------------------------------------------
# The 2026-11-06 golden table
# ---------------------------------------------------------------------------


def test_the_2026_11_06_table_holds_all_229_stop_names_and_all_38_headsigns():
    assert {kind for kind, _, _ in _table()} == {"stop", "headsign"}
    assert len(_rows("stop")) == 229
    headsigns = [published for published, _ in _rows("headsign")]
    assert len(headsigns) == 38
    assert headsigns == sorted(set(headsigns))


def test_every_name_published_on_2026_11_06_is_all_capitals():
    """THE PREMISE, so the goldens below are not vacuous: a table that held a
    mixed-case name would be testing the passthrough, not the rule."""
    for _, published, _ in _table():
        assert any(ch.isalpha() for ch in published), published
        assert not any(ch.islower() for ch in published), published


def test_every_2026_11_06_stop_name_displays_exactly_as_the_table_says():
    wrong = [
        (published, njt_static.display_name(published), displayed)
        for published, displayed in _rows("stop")
        if njt_static.display_name(published) != displayed
    ]
    assert wrong == []


def test_every_2026_11_06_headsign_displays_exactly_as_the_table_says():
    wrong = [
        (published, njt_static.display_name(published), displayed)
        for published, displayed in _rows("headsign")
        if njt_static.display_name(published) != displayed
    ]
    assert wrong == []


def test_every_acronym_kept_upper_is_one_the_publication_prints():
    """None is a guess: each is a whole letter run of some published name, and each
    survives into the displayed column."""
    published_runs = set()
    displayed_runs = set()
    for _, published, displayed in _table():
        published_runs.update(njt_static._LETTER_RUN.findall(published))
        displayed_runs.update(njt_static._LETTER_RUN.findall(displayed))
    assert njt_static._DISPLAY_ACRONYMS <= published_runs
    assert njt_static._DISPLAY_ACRONYMS <= displayed_runs


def test_a_displayed_name_displays_as_itself():
    for _, _, displayed in _table():
        assert njt_static.display_name(displayed) == displayed


# ---------------------------------------------------------------------------
# The rule's edges, named
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "published, displayed",
    [
        ("30TH ST. PHL.", "30th St. PHL."),
        ("2ND STREET LIGHT RAIL STATION", "2nd Street Light Rail Station"),
        ("PRINCETON JCT.", "Princeton Jct."),
        ("WAYNE/ROUTE 23 TRANSIT CENTER [RR]", "Wayne/Route 23 Transit Center [RR]"),
        ("SALISBURY MILLS-CORNWALL", "Salisbury Mills-Cornwall"),
        ("MSU", "MSU"),
        ("  HOBOKEN  ", "Hoboken"),
        # Not in the 2026-11-06 publication, which prints no apostrophe and no accent:
        # the 2026-09-01 capture's "Newark Int'l Airport" upper-cased, and an accented
        # capital, each kept inside its word.
        ("NEWARK INT'L AIRPORT", "Newark Int'l Airport"),
        ("\u00c9COLE STREET", "\u00c9cole Street"),
    ],
)
def test_an_all_caps_name_keeps_its_words_and_takes_ordinary_case(published, displayed):
    assert njt_static.display_name(published) == displayed


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_name_displays_as_none(blank):
    assert njt_static.display_name(blank) is None


@pytest.mark.parametrize("chosen", ["MetroPark", "Westside DeKalb Jct", "Newark Int'l Airport"])
def test_a_case_the_publisher_chose_is_never_flattened(chosen):
    """The lowercase guard, tested directly: an inner capital or a small letter after
    an apostrophe is the publisher's choice, and the capitals rule must not reach it."""
    assert njt_static.display_name(chosen) == chosen


@golden
def test_every_name_and_headsign_of_the_mixed_case_2026_09_01_fixture_passes_through():
    names = _fixture_column("stops.txt", "stop_name")
    headsigns = sorted(set(_fixture_column("trips.txt", "trip_headsign")))
    assert (len(names), len(headsigns)) == (164, 26)
    changed = [v for v in names + headsigns if njt_static.display_name(v) != v]
    assert changed == []
    with (FIXTURE_DIR / "stops.txt").open("rb") as raw:
        parsed = njt_static._parse_stops(raw)
    assert sorted(stop["name"] for stop in parsed.values()) == sorted(names)


# ---------------------------------------------------------------------------
# Through the parsers, on real rows
# ---------------------------------------------------------------------------


def test_an_all_caps_stops_txt_parses_to_names_in_ordinary_case():
    stops = _parse(STOPS_2026_11_06, njt_static._parse_stops)
    assert {stop_id: stop["name"] for stop_id, stop in stops.items()} == {
        "105": "New York Penn Station",
        "107": "Newark Penn Station",
    }


def test_an_all_caps_trips_txt_parses_to_headsigns_in_ordinary_case():
    trips = _parse(TRIPS_2026_11_06, njt_static._parse_trips)
    assert {trip_id: trip["headsign"] for trip_id, trip in trips.items()} == {
        "1": "30th St. PHL.",
        "163": "MSU",
        "1652": "Hoboken",
    }


def test_a_lettered_train_number_is_a_code_and_is_never_recased():
    """trip_short_name is the train number, the realtime join's second key, and 34 of
    the 2380 trips of the 2026-11-06 capture letter it (RARVBUS, MNBN BUS, PJBUS). It
    is a code like a route short name, so the capitals rule must never reach it:
    "Mnbn Bus" is neither the published number nor a word."""
    trips = _parse(TRIPS_2026_11_06, njt_static._parse_trips)
    assert {trip_id: trip["short_name"] for trip_id, trip in trips.items()} == {
        "1": "4628",
        "163": "6273",
        "1652": "MNBN BUS",
    }


def test_route_names_and_route_codes_are_never_recased():
    """The decision covers stop names and headsigns only. A route short name is a code
    ("NEC" read as "Nec" would be a different word), and a route long name is shown as
    published: the real ones are mixed case, and the synthetic all-caps row proves the
    parser would leave one alone if that changed."""
    routes = _parse(ROUTES_2026_11_06 + ROUTES_SYNTHETIC_ALL_CAPS, njt_static._parse_routes)
    assert [(r["short_name"], r["long_name"]) for r in routes.values()] == [
        ("HBLR", "Hudson-Bergen Light Rail"),
        ("NEC", "Northeast Corridor"),
        ("PJL", "PORT JERVIS LINE"),
    ]
