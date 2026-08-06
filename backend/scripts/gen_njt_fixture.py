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
  - shapes.txt is NOT committed at all. 15a deliberately does not parse it (see
    njt_static's module docstring), it is 90% of the payload, and committing 10 MB
    of geometry nothing reads would be the worst trade in the repository. 15c
    revisits this when the line-drawing decision actually asks.

The script verifies the live feed still matches the facts probed 2026-08-05 and
that the trim preserves them, then prints the tables for eyeballing. It exits
nonzero on any drift, so a stale regeneration cannot slip in quietly. Eyeball the
printed tables against the golden test expectations before committing, per house
rules.
"""

from __future__ import annotations

import asyncio
import csv
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

OUT_DIR = REPO_ROOT / "backend" / "tests" / "fixtures" / "njt_gtfs"

# Facts probed live 2026-08-05 (overnight 02:37 EDT and rush 18:15 EDT).
# Regeneration fails loudly if the feed drifts from any of them.
EXPECTED_ROUTES = 12
EXPECTED_ROUTE_TYPE = "113"  # GTFS extended "Rail Service", not a classic 0-7 type
EXPECTED_STOPS = 172
EXPECTED_STOP_ID_MAX = 176  # ids run 1..176 with gaps; 172 of them exist
# The two identity stops, checked by NAME as well as presence: this feed's ids are
# small integers with heavy cross-system collision, so presence alone proves nothing.
IDENTITY_STOPS = {"109": "New York", "112": "Newark"}
EXPECTED_PENN_STOP_CODE = "NY"  # Penn Station New York is FLAT stop 109, stop_code NY
# Pascack Valley IS a first-class route; Port Jervis is NOT (see below).
EXPECTED_PASC_ROUTE = ("13", "PASC")
# The two route ids Port Jervis service runs under, per the probe.
EXPECTED_PORT_JERVIS_ROUTES = {"5", "6"}
# The probe counted nine Port Jervis stations. Reported rather than asserted,
# because "which stops are the Port Jervis ones" is a DERIVED set here (see
# _port_jervis_stops) and a definitional mismatch would read as feed drift when it
# is not. A difference is printed loudly for the eyeball step.
PROBED_PORT_JERVIS_STATIONS = 9
# Members that must be ABSENT. Their absence is a load-bearing fact, not a
# curiosity: njt_static's validators are built around it, and a feed that starts
# shipping calendar.txt is a real change worth a human deciding about.
EXPECTED_ABSENT = ("calendar.txt", "feed_info.txt")
# Present upstream, deliberately not committed.
EXPECTED_PRESENT_UNPARSED = "shapes.txt"

TRIPS_PER_ROUTE = 2  # enough for a join golden, small enough to stay a trim
PASC_TRIO = 3  # "the PASC trio": three Pascack-only stations, per the 15a spec


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


def _write_rows(name: str, fieldnames: list[str], rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / name
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in fieldnames})
    print(f"  wrote {out.relative_to(REPO_ROOT)} ({len(rows)} rows)")


def _get(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def _port_jervis_stops(
    trips: list[dict], calls: dict[str, list[dict]]
) -> tuple[set[str], set[str]]:
    """(Port Jervis trip ids, the stops ONLY they serve).

    PORT JERVIS HAS NO ROUTE OF ITS OWN. The probe found its service running under
    the MAIN (6) and BERG (5) route ids, with the Port Jervis identity appearing
    nowhere in routes.txt and only in trip_headsign. So the trips are found by
    headsign, which is the only place the feed says it.

    The station set is then the EXCLUSIVE one: stops that no non-Port-Jervis trip
    calls at. That definition is what makes it stable. Port Jervis trains run down
    the Main and Bergen County lines to reach Hoboken, so simply taking every stop
    a Port Jervis trip touches would sweep in most of northern New Jersey; the
    stops nothing else serves are the west-of-Hudson ones this fixture has to
    carry, and they are exactly the ones a route-keyed trim would lose.
    """
    pj_trips = {
        _get(trip, "trip_id")
        for trip in trips
        if "port jervis" in _get(trip, "trip_headsign").lower()
    }
    pj_stops: set[str] = set()
    other_stops: set[str] = set()
    for trip_id, trip_calls in calls.items():
        target = pj_stops if trip_id in pj_trips else other_stops
        for call in trip_calls:
            target.add(_get(call, "stop_id"))
    return pj_trips, pj_stops - other_stops


def _exclusive_stops(
    route_of_trip: dict[str, str], calls: dict[str, list[dict]], route_id: str
) -> set[str]:
    """Stops served ONLY by trips on `route_id`. The same exclusivity idea as
    _port_jervis_stops, applied to a first-class route: Pascack Valley trains also
    reach Hoboken over shared track, so its own stations are the ones nothing else
    calls at."""
    mine: set[str] = set()
    others: set[str] = set()
    for trip_id, trip_calls in calls.items():
        target = mine if route_of_trip.get(trip_id) == route_id else others
        for call in trip_calls:
            target.add(_get(call, "stop_id"))
    return mine - others


def main() -> int:  # noqa: C901 - one linear verify-then-trim pass, split would obscure it
    raw = _download()
    zf = zipfile.ZipFile(io.BytesIO(raw))
    members = set(zf.namelist())
    problems: list[str] = []

    print(f"\narchive members: {', '.join(sorted(members))}")
    for absent in EXPECTED_ABSENT:
        if absent in members:
            problems.append(
                f"{absent} is now PRESENT; njt_static's validators are built on its absence "
                "and this needs a human decision, not a regeneration"
            )
    if EXPECTED_PRESENT_UNPARSED not in members:
        problems.append(f"{EXPECTED_PRESENT_UNPARSED} is missing from the archive")

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

    pj_trips, pj_stops = _port_jervis_stops(trips, calls)
    pj_routes = {route_of_trip.get(t, "") for t in pj_trips} - {""}
    print(f"\nPort Jervis: {len(pj_trips)} trips under route ids {sorted(pj_routes)}")
    print(f"  stations served ONLY by Port Jervis trips: {len(pj_stops)}")
    for stop_id in sorted(pj_stops, key=lambda s: int(s) if s.isdigit() else 0):
        print(f"    stop {stop_id}: {_get(stop_by_id.get(stop_id, {}), 'stop_name')!r}")
    if not pj_trips:
        problems.append(
            "no trip headsign names Port Jervis. The identity lives ONLY there, so losing it "
            "means the line is unnameable"
        )
    if pj_routes and pj_routes != EXPECTED_PORT_JERVIS_ROUTES:
        problems.append(
            f"Port Jervis trips now run under route ids {sorted(pj_routes)}, expected "
            f"{sorted(EXPECTED_PORT_JERVIS_ROUTES)}"
        )
    if len(pj_stops) != PROBED_PORT_JERVIS_STATIONS:
        # REPORTED, NOT FAILED. See PROBED_PORT_JERVIS_STATIONS: the exclusive-set
        # definition here may legitimately count differently from the probe's nine.
        print(
            f"  NOTE: the probe counted {PROBED_PORT_JERVIS_STATIONS} Port Jervis stations and "
            f"this exclusive set has {len(pj_stops)}. Eyeball the names above before committing."
        )

    pasc_stops = _exclusive_stops(route_of_trip, calls, EXPECTED_PASC_ROUTE[0])
    pasc_trio = sorted(pasc_stops, key=lambda s: (int(s) if s.isdigit() else 0, s))[:PASC_TRIO]
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
    kept_trip_ids: set[str] = set()
    for route_id, route_trips in sorted(trips_by_route.items()):
        for trip in sorted(route_trips, key=lambda t: _get(t, "trip_id"))[:TRIPS_PER_ROUTE]:
            kept_trip_ids.add(_get(trip, "trip_id"))
    # Port Jervis trips are chosen SEPARATELY from the per-route quota, because
    # they have no route of their own: without this the per-route pick for 5 and 6
    # would almost certainly be ordinary Main/Bergen trips and every west-of-Hudson
    # station would vanish from the fixture.
    for trip_id in sorted(pj_trips)[:TRIPS_PER_ROUTE]:
        kept_trip_ids.add(trip_id)
    # Then top up for any mandated stop nothing kept covers yet.
    covered = {_get(call, "stop_id") for tid in kept_trip_ids for call in calls.get(tid, [])}
    for stop_id in list(IDENTITY_STOPS) + sorted(pj_stops) + pasc_trio:
        if stop_id in covered:
            continue
        for trip_id in sorted(calls):
            if any(_get(call, "stop_id") == stop_id for call in calls[trip_id]):
                kept_trip_ids.add(trip_id)
                covered |= {_get(call, "stop_id") for call in calls[trip_id]}
                break

    kept_trips = sorted(
        (t for t in trips if _get(t, "trip_id") in kept_trip_ids), key=lambda t: _get(t, "trip_id")
    )
    kept_stop_ids = {
        _get(call, "stop_id") for tid in kept_trip_ids for call in calls.get(tid, [])
    } - {""}
    kept_stops = [s for s in stops if _get(s, "stop_id") in kept_stop_ids]
    kept_stop_times = [row for tid in sorted(kept_trip_ids) for row in calls.get(tid, [])]

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
    dropped_pj = sorted(pj_stops - kept_stop_ids)
    if dropped_pj:
        problems.append(f"the trim drops west-of-Hudson stations: {dropped_pj}")
    dropped_pasc = [s for s in pasc_trio if s not in kept_stop_ids]
    if dropped_pasc:
        problems.append(f"the trim drops Pascack stations: {dropped_pasc}")
    if not (pj_trips & kept_trip_ids):
        problems.append("the trim keeps no Port Jervis trip, so the headsign golden has nothing")
    dangling = kept_stop_ids - set(stop_by_id)
    if dangling:
        problems.append(f"stop_times reference stops that are not in stops.txt: {sorted(dangling)}")

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
    print(
        "\nEyeball the tables above against the golden test expectations in "
        "backend/tests/test_njt_static.py before committing. shapes.txt is deliberately "
        "NOT written (15a defers it; it is 10 MB and nothing reads it yet)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
