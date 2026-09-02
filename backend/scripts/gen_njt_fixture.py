#!/usr/bin/env python3
"""Generate the committed NJ Transit GTFS test fixture (backend/tests/fixtures/njt_gtfs/).

RUN THIS LOCALLY, WITH CREDENTIALS. Unlike every other generator in this
directory, the source is behind an account:

    export NJT_USERNAME=...   # or put both in the project-root .env
    export NJT_PASSWORD=...
    python backend/scripts/gen_njt_fixture.py

Nothing in CI runs this and nothing in CI touches the live API. Until the fixture
is committed, backend/tests/test_njt_static.py's goldens are gated by
conftest.golden_fixture_guard, which skips them locally and FAILS them in CI, on
purpose: 13a and 13b both merged green while ten goldens were dormant, because a
skip is invisible in a passing summary line.

WHY a trimmed committed fixture (the gen_path_fixture / gen_ferry_fixture
pattern): the loader's golden tests assert real-feed facts, so the fixture must be
a captured slice of the real feed rather than handcrafted rows. The full download
is ~3.3 MB zipped and 11.1 MB unzipped, of which shapes.txt alone is 10 MB, so it
has to be trimmed; the trim rule below is written to provably preserve every
property the goldens assert.

IT REUSES THE PRODUCTION TOKEN DOOR (njt_auth.njt_post) rather than posting for
itself, which makes this script a live smoke test of that module as a side effect:
if the mint or the multipart form ever stops working against the real API, a
regeneration says so here rather than in production.

THE TRIM RULE, and what each part exists to preserve:
  - agency.txt, routes.txt and calendar_dates.txt are committed IN FULL.
    calendar_dates in particular MUST stay whole: it is this feed's only schedule
    (there is no calendar.txt), and the service-date guard reads the MAXIMUM over
    the whole table, so any per-service trim could silently move the one number
    the guard is about.
  - trips.txt keeps, per route_id, the TRIPS_PER_ROUTE lexicographically-first
    trips, plus the same number of Port Jervis trips (identified by headsign,
    since Port Jervis has no route of its own), plus one trip per mandated stop
    that nothing else covered.
  - stops.txt keeps every stop a kept trip calls at, which is what keeps the
    fixture referentially intact: no stop_times row can point at a stop that is
    not there. The mandated stops (109, 112, the west-of-Hudson set, the PASC
    trio) are guaranteed by the trip selection above rather than bolted on after.
  - stop_times.txt keeps every row of every kept trip.
  - shapes.txt keeps every row of every shape THE KEPT TRIPS REFERENCE, and
    nothing else. 15a committed none of it, correctly: it is 90% of the payload and
    nothing parsed it. 15c draws route lines from it, so the fixture now carries
    the slice the kept trips can actually reach, which is a few dozen shapes rather
    than the whole 10 MB. The generator REFUSES rather than writing a partial
    fixture if the live publication is missing a shape the committed trips
    reference; a fixture whose trips point at geometry it does not carry would make
    every line golden measure the gap instead of the map.

REFRESHING ONLY THE GEOMETRY:

    python backend/scripts/gen_njt_fixture.py --shapes-only

That mints once, pulls the archive, reads the COMMITTED trips.txt, writes
njt_gtfs/shapes.txt for exactly the shape_ids those trips reference, and touches
no other member. It exists because the other six files are a captured slice whose
trip selection the goldens are written against: re-running the full generator to
pick up geometry would re-pick trips, move stops, and invalidate golden after
golden for no reason. A refresh that cannot find every referenced shape writes
nothing and names the ids it could not find.

The script verifies the live feed still matches the facts probed 2026-08-05 and
that the trim preserves them, then prints the tables for eyeballing. It exits
nonzero on any drift, so a stale regeneration cannot slip in quietly. Eyeball the
printed tables against the golden test expectations before committing, per house
rules.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import io
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

# The same two-line preamble the other generators use, so a script run directly
# can import the app modules that live in backend/.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import njt_auth  # noqa: E402
import njt_static  # noqa: E402

# The trim itself lives in a module both generators import, because the REALTIME
# capture has to be able to re-trim (see njt_fixture_trim's docstring for the
# defect that forced the extraction). Loaded by path for the same reason the
# monitor is: scripts/ is not an importable package.
_TRIM_SPEC = importlib.util.spec_from_file_location(
    "njt_fixture_trim", Path(__file__).resolve().parent / "njt_fixture_trim.py"
)
trim = importlib.util.module_from_spec(_TRIM_SPEC)
_TRIM_SPEC.loader.exec_module(trim)

OUT_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "njt_gtfs"

# Facts probed live 2026-08-05 (overnight 02:37 EDT and rush 18:15 EDT).
# Regeneration fails loudly if the feed drifts from any of them.
EXPECTED_ROUTES = 12
EXPECTED_ROUTE_TYPE = "113"  # GTFS extended "Rail Service", not a classic 0-7 type
EXPECTED_STOPS = 172
EXPECTED_STOP_ID_MAX = 176  # ids run 1..176 with gaps; 172 of them exist
# The two identity stops, checked by NAME as well as presence: this feed's ids are
# small integers with heavy cross-system collision, so presence alone proves nothing.
IDENTITY_STOPS = trim.IDENTITY_STOPS
EXPECTED_PENN_STOP_CODE = "NY"  # Penn Station New York is FLAT stop 109, stop_code NY
# Pascack Valley IS a first-class route; Port Jervis is NOT (see below).
EXPECTED_PASC_ROUTE = ("13", "PASC")
# The two route ids Port Jervis service runs under, per the probe.
EXPECTED_PORT_JERVIS_ROUTES = {"5", "6"}
# The probe counted nine Port Jervis stations, BY IDENTITY. Reported rather than
# asserted, because what this script measures is a different question: the stops
# nothing outside the line serves (njt_fixture_trim.exclusive_stops). The two
# disagree by one on a healthy feed, and the one is SUFFERN: trips headsigned
# "Suffern" terminate there, so it is served by trips outside the line and leaves
# the exclusive set while remaining a Port Jervis station. Measured on the
# committed fixture, which reports the other eight.
#
# So a difference here is expected rather than alarming, and the same holds for
# any other station a short-turning or terminating service also reaches. A gap is
# printed for the eyeball step and says explicitly that it is not a drift signal.
# A count of ZERO is different and IS a problem, handled at the guard below: it
# means the trip set has cancelled itself rather than that the line has shrunk.
PROBED_PORT_JERVIS_STATIONS = 9
# Members that must be ABSENT. Their absence is a load-bearing fact, not a
# curiosity: njt_static's validators are built around it, and a feed that starts
# shipping calendar.txt is a real change worth a human deciding about.
EXPECTED_ABSENT = ("calendar.txt", "feed_info.txt")
# Present upstream, and since 15c committed in the trimmed form described above.
# Still checked for presence here rather than assumed: it is the one member the
# LOADER treats as optional, so the generator is the only place that can notice the
# publication dropping it, and it says so as drift rather than writing a fixture
# whose route lines silently vanished.
SHAPES_MEMBER = "shapes.txt"

TRIPS_PER_ROUTE = trim.TRIPS_PER_ROUTE
PASC_TRIO = trim.PASC_TRIO


def _download() -> bytes:
    """Mint a token and POST for the archive, through the production auth module.

    One mint, like everything else that talks to this API: njt_post takes its token
    from the shared cache and re-mints at most once. A regeneration therefore costs
    one token against a rate limit NJ Transit does not publish.
    """
    if not njt_auth.is_configured():
        raise SystemExit(
            f"{njt_auth.USERNAME_VAR} and {njt_auth.PASSWORD_VAR} must be set (in the "
            "environment or the project-root .env) to download the NJ Transit archive."
        )
    print(f"Minting a token and POSTing {njt_static.NJT_STATIC_URL} ...")
    raw = asyncio.run(njt_auth.njt_post(njt_static.NJT_STATIC_URL))
    print(f"  got {len(raw)} bytes")
    return raw


def _read_rows(zf: zipfile.ZipFile, name: str) -> tuple[list[str], list[dict]]:
    with zf.open(name) as fh:
        reader = csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        return list(reader.fieldnames or []), list(reader)


def _write_rows(
    name: str, fieldnames: list[str], rows: list[dict], out_dir: Path | None = None
) -> None:
    out_dir = OUT_DIR if out_dir is None else out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in fieldnames})
    print(f"  wrote {out} ({len(rows)} rows)")


def _get(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def run_shapes_only(zf: zipfile.ZipFile, *, out_dir: Path | None = None) -> int:
    """Rewrite ONLY njt_gtfs/shapes.txt, for exactly the shapes the COMMITTED trips
    reference. Returns 0 on success, 1 on a refusal that wrote nothing.

    WHY THIS MODE EXISTS. The other six members are a captured slice whose TRIP
    SELECTION every golden is written against (the Port Jervis coverage pick, the
    PASC trio, the west-of-Hudson mandate). Re-running the full generator to pick up
    geometry would re-pick trips against a newer publication, move stops, and
    invalidate golden after golden to add one file. This mode reads the committed
    trips.txt instead, so the geometry lands beside a trip selection that does not
    move.

    IT REFUSES RATHER THAN WRITING A PARTIAL FIXTURE, and the refusal is the point
    of the mode being separate: if the live publication no longer carries a shape
    some committed trip references, the honest outcome is no write and the missing
    ids named. Writing the subset it could find would leave the committed pair
    internally inconsistent, and every route-line golden would then be measuring
    that gap rather than the map. Nothing is written on any refusal path, including
    the partial one, because the write happens after the last check.
    """
    out_dir = OUT_DIR if out_dir is None else out_dir
    trips_path = out_dir / "trips.txt"
    if not trips_path.exists():
        print(f"  !! {trips_path} does not exist; run the full generator first.")
        return 1
    with trips_path.open(encoding="utf-8-sig", newline="") as fh:
        committed_trips = list(csv.DictReader(fh))
    wanted = trim.referenced_shape_ids(committed_trips)
    print(f"\n{len(committed_trips)} committed trips reference {len(wanted)} distinct shape_ids")
    if not wanted:
        print("  !! the committed trips reference no shape_id at all; nothing to refresh.")
        return 1
    if SHAPES_MEMBER not in set(zf.namelist()):
        print(f"  !! the publication carries no {SHAPES_MEMBER}; fixture NOT written.")
        return 1
    shapes_cols, shape_rows = _read_rows(zf, SHAPES_MEMBER)
    kept = trim.select_shape_rows(shape_rows, wanted)
    missing = sorted(wanted - {_get(row, "shape_id") for row in kept})
    if missing:
        print(
            f"\nFEED DRIFT, fixture NOT written:\n  !! the committed trips reference "
            f"{len(missing)} shape_id(s) this publication's {SHAPES_MEMBER} does not carry: "
            f"{missing}. The committed trips and this publication disagree; regenerate the "
            "whole fixture rather than refreshing geometry against it."
        )
        return 1
    print(f"  {len(kept)} rows for {len(wanted)} shapes, of {len(shape_rows)} rows upstream")
    _write_rows(SHAPES_MEMBER, shapes_cols, kept, out_dir)
    print(
        "\nOnly shapes.txt was rewritten. Eyeball the row count above, then commit it on its own."
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--shapes-only",
        action="store_true",
        help=(
            "rewrite only shapes.txt, for the shape_ids the committed trips.txt "
            "references, and touch no other member"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # noqa: C901 - one linear verify-then-trim pass, split would obscure it
    args = _parse_args(argv)
    raw = _download()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    if args.shapes_only:
        return run_shapes_only(zf)
    members = set(zf.namelist())
    problems: list[str] = []

    print(f"\narchive members: {', '.join(sorted(members))}")
    for absent in EXPECTED_ABSENT:
        if absent in members:
            problems.append(
                f"{absent} is now PRESENT; njt_static's validators are built on its absence "
                "and this needs a human decision, not a regeneration"
            )
    if SHAPES_MEMBER not in members:
        problems.append(f"{SHAPES_MEMBER} is missing from the archive")

    agency_cols, agency = _read_rows(zf, "agency.txt")
    routes_cols, routes = _read_rows(zf, "routes.txt")
    stops_cols, stops = _read_rows(zf, "stops.txt")
    trips_cols, trips = _read_rows(zf, "trips.txt")
    stop_times_cols, stop_times = _read_rows(zf, "stop_times.txt")
    cal_cols, calendar_dates = _read_rows(zf, "calendar_dates.txt")

    # Routes: count, the extended type, and the empty text colour.
    print(f"\nroutes.txt: {len(routes)} rows")
    for route in sorted(routes, key=lambda r: _get(r, "route_id")):
        print(
            f"  route {_get(route, 'route_id'):>3}: {_get(route, 'route_short_name'):<6} "
            f"{_get(route, 'route_long_name')!r} type={_get(route, 'route_type')} "
            f"color={_get(route, 'route_color')!r} text={_get(route, 'route_text_color')!r}"
        )
    if len(routes) != EXPECTED_ROUTES:
        problems.append(f"expected {EXPECTED_ROUTES} routes, got {len(routes)}")
    wrong_type = sorted(
        _get(r, "route_id") for r in routes if _get(r, "route_type") != EXPECTED_ROUTE_TYPE
    )
    if wrong_type:
        problems.append(
            f"routes not carrying route_type={EXPECTED_ROUTE_TYPE}: {wrong_type}. The 0-7 "
            "sweep and the loader's tolerance comment both assume the extended type"
        )
    with_text_color = sorted(_get(r, "route_id") for r in routes if _get(r, "route_text_color"))
    if with_text_color:
        problems.append(
            f"route_text_color is no longer empty on {with_text_color}; a renderer relying "
            "on its own contrast fallback should be told"
        )
    route_names = {_get(r, "route_id"): _get(r, "route_short_name") for r in routes}
    if route_names.get(EXPECTED_PASC_ROUTE[0]) != EXPECTED_PASC_ROUTE[1]:
        problems.append(
            f"route {EXPECTED_PASC_ROUTE[0]} is {route_names.get(EXPECTED_PASC_ROUTE[0])!r}, "
            f"expected {EXPECTED_PASC_ROUTE[1]!r}"
        )
    named_pj = sorted(
        _get(r, "route_id")
        for r in routes
        if "port jervis" in (_get(r, "route_long_name") + _get(r, "route_short_name")).lower()
    )
    if named_pj:
        problems.append(
            f"routes {named_pj} now NAME Port Jervis. The 15a design rests on the identity "
            "living only in trip_headsign; a first-class route is a real change"
        )

    # Stops: count, id range, the two identity stops.
    stop_by_id = {_get(s, "stop_id"): s for s in stops}
    print(f"\nstops.txt: {len(stops)} rows")
    if len(stops) != EXPECTED_STOPS:
        problems.append(f"expected {EXPECTED_STOPS} stops, got {len(stops)}")
    numeric = [int(sid) for sid in stop_by_id if sid.isdigit()]
    if len(numeric) != len(stop_by_id):
        problems.append("some stop_ids are not numeric; the probe found ids 1..176")
    elif max(numeric) > EXPECTED_STOP_ID_MAX:
        problems.append(f"stop ids now run past {EXPECTED_STOP_ID_MAX} (max {max(numeric)})")
    if any(_get(s, "location_type") or _get(s, "parent_station") for s in stops):
        problems.append(
            "stops.txt now carries location_type or parent_station; the loader treats this "
            "feed as FLAT and would need a parent/child fold"
        )
    for stop_id, expected in IDENTITY_STOPS.items():
        stop = stop_by_id.get(stop_id)
        if stop is None:
            problems.append(f"identity stop {stop_id} is missing")
        else:
            print(f"  stop {stop_id}: {_get(stop, 'stop_name')!r} code={_get(stop, 'stop_code')!r}")
            if expected not in _get(stop, "stop_name"):
                name = _get(stop, "stop_name")
                problems.append(f"stop {stop_id} is {name!r}, expected to contain {expected!r}")
    penn = stop_by_id.get("109")
    if penn is not None and _get(penn, "stop_code") != EXPECTED_PENN_STOP_CODE:
        problems.append(
            f"stop 109 stop_code is {_get(penn, 'stop_code')!r}, expected "
            f"{EXPECTED_PENN_STOP_CODE!r}"
        )

    # calendar_dates: the whole schedule, additive only, and its span.
    exception_types = {_get(row, "exception_type") for row in calendar_dates}
    dates = sorted(_get(row, "date") for row in calendar_dates if _get(row, "date"))
    print(
        f"\ncalendar_dates.txt: {len(calendar_dates)} rows, exception types "
        f"{sorted(exception_types)}, span {dates[0] if dates else '(none)'} to "
        f"{dates[-1] if dates else '(none)'}"
    )
    if not dates:
        problems.append("calendar_dates.txt carries no dates; this feed has no other schedule")
    if exception_types - {"1"}:
        problems.append(
            f"calendar_dates carries non-additive exception types {sorted(exception_types)}. "
            "The loader counts only added (1) rows toward the service span; removals appearing "
            "means the service-date guard's reasoning needs re-reading"
        )
    if "calendar.txt" not in members and not dates:
        problems.append("no calendar.txt and no calendar_dates: the feed schedules nothing")

    # Trips and their calls.
    calls: dict[str, list[dict]] = defaultdict(list)
    for row in stop_times:
        trip_id = _get(row, "trip_id")
        if trip_id:
            calls[trip_id].append(row)
    for trip_calls in calls.values():
        trip_calls.sort(key=lambda r: int(_get(r, "stop_sequence") or 0))
    route_of_trip = {_get(t, "trip_id"): _get(t, "route_id") for t in trips}
    trips_by_route: dict[str, list[dict]] = defaultdict(list)
    for trip in trips:
        trips_by_route[_get(trip, "route_id")].append(trip)
    print(f"\ntrips.txt: {len(trips)} rows across {len(trips_by_route)} routes")

    blank_short_name = [t for t in trips if not _get(t, "trip_short_name")]
    if blank_short_name:
        problems.append(
            f"{len(blank_short_name)} trips carry no trip_short_name. That field IS the train "
            "number and 15b's second join key (745/745 on the probe); blanks break it"
        )

    pj_headsigned = {
        _get(t, "trip_id") for t in trips if "port jervis" in _get(t, "trip_headsign").lower()
    }
    pj_trips = trim.port_jervis_trips(trips, calls)
    pj_stops = trim.exclusive_stops(pj_trips, calls)
    # THE NINE, derived: the exclusive block plus the junction beside it (see
    # njt_fixture_trim.west_of_hudson_stops). This is what the trim must carry, and
    # what the coverage selection below is measured against.
    west_of_hudson = trim.west_of_hudson_stops(pj_trips, calls)
    pj_keep, pj_uncovered = trim.select_port_jervis_trips(
        pj_trips, pj_headsigned, calls, west_of_hudson, route_of_trip
    )
    pj_routes = {route_of_trip.get(t, "") for t in pj_trips} - {""}
    print(
        f"\nPort Jervis: {len(pj_headsigned)} trips headsigned for it, {len(pj_trips)} on the "
        f"line in both directions, under route ids {sorted(pj_routes)}"
    )
    print(f"  stations served by those trips and by nothing else: {len(pj_stops)}")
    print(f"  west-of-Hudson stations (those plus the junction): {len(west_of_hudson)}")
    for stop_id in sorted(west_of_hudson, key=lambda s: int(s) if s.isdigit() else 0):
        marker = " " if stop_id in pj_stops else "*"  # * marks the derived junction
        print(f"   {marker}stop {stop_id}: {_get(stop_by_id.get(stop_id, {}), 'stop_name')!r}")
    print(f"  covered by {len(pj_keep)} trip(s) chosen for COVERAGE, not order: {sorted(pj_keep)}")
    if not pj_headsigned:
        problems.append(
            "no trip headsign names Port Jervis. The identity lives ONLY there, so losing it "
            "means the line is unnameable"
        )
    if pj_routes and not (pj_routes <= EXPECTED_PORT_JERVIS_ROUTES):
        problems.append(
            f"Port Jervis trips now run under route ids {sorted(pj_routes)}, expected a subset "
            f"of {sorted(EXPECTED_PORT_JERVIS_ROUTES)}"
        )
    if pj_uncovered:
        # MEASURED, not diagnosed. A station no Port Jervis trip serves on the day
        # of the run is a fact about the schedule (a closure, a bus substitution, a
        # capture during a service gap), not necessarily a fault. The mandated-stop
        # top-up below still forces its stops.txt row in from whatever does serve
        # it, so the fixture stays complete; the gap is named here for the eyeball.
        names = [_get(stop_by_id.get(sid, {}), "stop_name") or sid for sid in sorted(pj_uncovered)]
        print(
            f"  NOTE: {len(pj_uncovered)} west-of-Hudson station(s) are served by NO Port "
            f"Jervis trip in this publication: {names}. The mandated-stop top-up will still "
            "put their rows in the fixture; eyeball whether the schedule really has a gap."
        )
    if len(west_of_hudson) != PROBED_PORT_JERVIS_STATIONS:
        # REPORTED, NOT FAILED, and the wording matters because the old one implied
        # drift. These are DIFFERENT MEASUREMENTS of different questions. The probe
        # counted the line's stations by identity; this counts stops that nothing
        # outside the line serves, which a train short-turning at Middletown
        # legitimately shrinks without anything having changed upstream.
        # This one IS worth a look, because west_of_hudson is derived to match the
        # probe's count rather than to answer a different question: the exclusive
        # block plus its junction was nine on the probed publication. A different
        # number means the line's shape moved, or the junction derivation found a
        # different boundary, and either is a human's call.
        print(
            f"  NOTE: the probe counted {PROBED_PORT_JERVIS_STATIONS} Port Jervis stations "
            f"and this publication derives {len(west_of_hudson)}. The exclusive set alone is "
            f"{len(pj_stops)}, which is expected to be one lower: Suffern is a Port Jervis "
            "station AND the terminus of trips headsigned Suffern, so exclusivity drops it "
            "and the junction rule puts it back. Eyeball the names above before committing."
        )

    pasc_stops = trim.route_exclusive_stops(route_of_trip, calls, EXPECTED_PASC_ROUTE[0])
    pasc_trio = trim.select_pasc_trio(pasc_stops)
    print(
        f"\nPascack Valley (route {EXPECTED_PASC_ROUTE[0]}): {len(pasc_stops)} exclusive stations"
    )
    for stop_id in pasc_trio:
        print(f"    stop {stop_id}: {_get(stop_by_id.get(stop_id, {}), 'stop_name')!r}")
    if len(pasc_trio) < PASC_TRIO:
        problems.append(
            f"only {len(pasc_trio)} Pascack-exclusive stations found, needed {PASC_TRIO}"
        )

    # --- the trim -----------------------------------------------------------
    # Shared with the realtime generator, which calls the same two functions with a
    # non-empty extra_trip_ids so the committed pair joins by construction.
    kept_trip_ids = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=trips_by_route,
        pj_keep=pj_keep,
        mandated_stops=list(IDENTITY_STOPS) + sorted(west_of_hudson) + pasc_trio,
    )
    kept_trips, kept_stops, kept_stop_times = trim.apply_trim(
        trips=trips, stops=stops, calls=calls, kept_trip_ids=kept_trip_ids
    )
    kept_stop_ids = {_get(s, "stop_id") for s in kept_stops}

    print(
        f"\ntrimmed: {len(kept_trips)} of {len(trips)} trips, {len(kept_stops)} of {len(stops)} "
        f"stops, {len(kept_stop_times)} of {len(stop_times)} stop_times rows"
    )

    # --- what the trim must preserve ---------------------------------------
    kept_routes = {_get(t, "route_id") for t in kept_trips}
    missing_routes = {_get(r, "route_id") for r in routes} - kept_routes
    if missing_routes:
        problems.append(f"the trim leaves routes with no trips: {sorted(missing_routes)}")
    for stop_id in IDENTITY_STOPS:
        if stop_id not in kept_stop_ids:
            problems.append(f"the trim drops identity stop {stop_id}")
    # THE WEST-OF-HUDSON GUARD. Same subtraction it always was, and it is LIVE
    # again only because pj_stops is no longer empty.
    #
    # WHAT MAKES IT NON-VACUOUS IS A PROPERTY OF port_jervis_trips, not of this
    # line. Every trip in that set calls at one of the terminals the headsigned
    # trips reach, and by construction no trip outside the set calls at one, so the
    # terminals are always in (mine - others). pj_stops therefore always contains
    # at least the line's own terminal, and the subtraction below always has
    # something to subtract from. Under the old headsign-only set it did not, which
    # is exactly why this check passed for a phase while protecting nothing.
    #
    # AN EARLIER ATTEMPT AT THIS FIX WAS ALSO VACUOUS and is worth recording so the
    # next person does not repeat it: asking whether the kept Port Jervis trips'
    # own calls survived cannot fail, because apply_trim keeps every stop of every
    # kept trip. It read as a stronger check and was a tautology.
    if pj_headsigned and not pj_stops:
        problems.append(
            "trips are headsigned Port Jervis but NO stop is exclusive to the line, which is "
            "the signature of the trip set cancelling itself against its own return workings "
            "(see njt_fixture_trim.port_jervis_trips). Every guard below is dead while this "
            "holds, so it is a problem rather than a note."
        )
    dropped_pj = sorted(west_of_hudson - kept_stop_ids)
    if dropped_pj:
        names = [_get(stop_by_id.get(sid, {}), "stop_name") or sid for sid in dropped_pj]
        problems.append(
            f"the trim drops west-of-Hudson stations: {names}. The mandated-stop top-up in "
            "select_trim is supposed to pull in a trip for each of these."
        )
    dropped_pasc = [s for s in pasc_trio if s not in kept_stop_ids]
    if dropped_pasc:
        problems.append(f"the trim drops Pascack stations: {dropped_pasc}")
    if not (pj_headsigned & kept_trip_ids):
        problems.append(
            "the trim keeps no trip HEADSIGNED Port Jervis, so the headsign golden has nothing. "
            "Checked against the headsigned set rather than the whole line, because it is the "
            "headsign the golden reads."
        )
    dangling = kept_stop_ids - set(stop_by_id)
    if dangling:
        problems.append(f"stop_times reference stops that are not in stops.txt: {sorted(dangling)}")

    # THE GEOMETRY SLICE (15c), selected from the trips this run just kept rather
    # than from the committed ones, because in this mode the trip selection is
    # being rewritten. Same refusal as --shapes-only: a fixture whose trips point at
    # shapes it does not carry would make every route-line golden measure that gap.
    shapes_cols, shape_rows = _read_rows(zf, SHAPES_MEMBER)
    wanted_shape_ids = trim.referenced_shape_ids(kept_trips)
    kept_shapes = trim.select_shape_rows(shape_rows, wanted_shape_ids)
    missing_shapes = sorted(wanted_shape_ids - {_get(row, "shape_id") for row in kept_shapes})
    if missing_shapes:
        problems.append(
            f"the kept trips reference {len(missing_shapes)} shape_id(s) the publication's "
            f"shapes.txt does not carry: {missing_shapes[:10]}"
        )
    print(
        f"\nshapes.txt: {len(kept_shapes)} rows for {len(wanted_shape_ids)} shapes "
        f"referenced by the {len(kept_trips)} kept trips (of {len(shape_rows)} rows upstream)"
    )

    if problems:
        print("\nFEED DRIFT, fixture NOT written:")
        for problem in problems:
            print(f"  !! {problem}")
        return 1

    _write_rows("agency.txt", agency_cols, agency)
    _write_rows("routes.txt", routes_cols, routes)
    _write_rows("calendar_dates.txt", cal_cols, calendar_dates)
    _write_rows("stops.txt", stops_cols, kept_stops)
    _write_rows("trips.txt", trips_cols, kept_trips)
    _write_rows("stop_times.txt", stop_times_cols, kept_stop_times)
    _write_rows("shapes.txt", shapes_cols, kept_shapes)
    print(
        "\nEyeball the tables above against the golden test expectations in "
        "backend/tests/test_njt_static.py before committing."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
