"""Tests for the NJ Transit fixture trim (backend/scripts/njt_fixture_trim.py).

WHY THIS FILE EXISTS AT ALL. The trim used to live inline in a credentialed
generator that nothing could run in CI, so it had no tests, and it shipped two
defects that only surfaced when a human tried to use it:

  1. The realtime capture joined live trips against the 25-trip TRIM instead of
     the publication, measured 0.0000 at rush hour, and refused with a cause it
     had never measured.
  2. The Port Jervis station set was computed from a headsign-keyed trip set,
     which is direction-dependent, so on the full feed it cancelled itself to
     empty and took a downstream guard with it.

Extracting the trim into a plain module made both testable without credentials,
which is most of the point of the extraction.

The module lives under scripts/ (not an importable package), so it is loaded from
its file path, the same way the generators load it.
"""

from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path

_TRIM_PATH = Path(__file__).resolve().parent.parent / "scripts" / "njt_fixture_trim.py"
_spec = importlib.util.spec_from_file_location("njt_fixture_trim", _TRIM_PATH)
trim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trim)


def _calls(pairs) -> dict[str, list[dict]]:
    """{trip_id: [call rows]} from (trip_id, [stop_id, ...]) pairs."""
    return {
        trip_id: [
            {"trip_id": trip_id, "stop_id": stop_id, "stop_sequence": str(i + 1)}
            for i, stop_id in enumerate(stop_ids)
        ]
        for trip_id, stop_ids in pairs
    }


# The Port Jervis line as the feed actually publishes it: west-of-Hudson stations
# beyond Suffern, reached over Main/Bergen track shared with everything else.
WEST_OF_HUDSON = ["Otisville", "Middletown", "PortJervis"]
SHARED_TRACK = ["Hoboken", "Ridgewood", "Suffern"]


# ---------------------------------------------------------------------------
# Port Jervis: the direction-dependence that cancelled the set
# ---------------------------------------------------------------------------


def test_the_headsign_alone_finds_only_one_direction_of_the_line():
    """THE DEFECT, stated as the measurement that exposes it.

    A headsign names a DESTINATION, so it is direction-dependent. Trains running
    back from Port Jervis are headsigned Hoboken and match no headsign filter for
    Port Jervis, yet they call at every west-of-Hudson station. Under the old
    headsign-only trip set those return workings counted as "other", so the
    exclusive set was empty BY CONSTRUCTION on any complete publication.

    This is what made the generator report 0 stations against a byte-identical
    archive the probe had counted 9 in: not drift, a definition that cannot
    survive both directions being present.
    """
    outbound_only = [
        {"trip_id": "OUT", "trip_headsign": "Port Jervis"},
        {"trip_id": "OTHER", "trip_headsign": "Suffern"},
    ]
    calls = _calls([("OUT", SHARED_TRACK + WEST_OF_HUDSON), ("OTHER", SHARED_TRACK)])
    headsigned = {
        t["trip_id"] for t in outbound_only if "port jervis" in t["trip_headsign"].lower()
    }
    assert trim.exclusive_stops(headsigned, calls) == set(WEST_OF_HUDSON), (
        "with only the outbound trip present the headsign set looks correct, which is "
        "exactly why the defect survived: the TRIM happened to contain only outbound trips"
    )

    # Now add the return working the real feed carries. Same stations, headsigned
    # for the other end of the line.
    both = outbound_only + [{"trip_id": "IN", "trip_headsign": "Hoboken"}]
    calls_both = dict(calls)
    calls_both["IN"] = _calls([("IN", WEST_OF_HUDSON[::-1] + SHARED_TRACK[::-1])])["IN"]
    headsigned_both = {t["trip_id"] for t in both if "port jervis" in t["trip_headsign"].lower()}
    assert trim.exclusive_stops(headsigned_both, calls_both) == set(), (
        "THE CANCELLATION. One return working empties the set completely, which is what "
        "the full publication always contains and the trim happened not to."
    )


def test_the_direction_agnostic_set_survives_both_directions():
    """The fix, measured against the same two inputs. The set is completed by
    "calls at the terminal a headsigned trip ends at", which both directions do."""
    outbound_only = [
        {"trip_id": "OUT", "trip_headsign": "Port Jervis"},
        {"trip_id": "OTHER", "trip_headsign": "Suffern"},
    ]
    calls = _calls([("OUT", SHARED_TRACK + WEST_OF_HUDSON), ("OTHER", SHARED_TRACK)])
    both = outbound_only + [{"trip_id": "IN", "trip_headsign": "Hoboken"}]
    calls_both = dict(calls)
    calls_both["IN"] = _calls([("IN", WEST_OF_HUDSON[::-1] + SHARED_TRACK[::-1])])["IN"]

    one_way = trim.exclusive_stops(trim.port_jervis_trips(outbound_only, calls), calls)
    two_way = trim.exclusive_stops(trim.port_jervis_trips(both, calls_both), calls_both)
    assert one_way == set(WEST_OF_HUDSON)
    assert two_way == set(WEST_OF_HUDSON), (
        "the whole point: adding the return working must not change the answer, because "
        "it is the same line"
    )
    assert trim.port_jervis_trips(both, calls_both) == {"OUT", "IN"}


def test_a_publication_that_stops_naming_port_jervis_yields_nothing_rather_than_guessing():
    """No headsign names it, so there is no terminal to complete from and the set
    is empty. The generator turns that into a loud problem; what matters here is
    that it does not invent a line out of the Main and Bergen trips."""
    trips = [{"trip_id": "A", "trip_headsign": "Suffern"}]
    assert trim.port_jervis_trips(trips, _calls([("A", SHARED_TRACK)])) == set()


def test_route_exclusivity_was_never_direction_dependent():
    """The control, and the reason the two heuristics sat side by side for a phase
    with only one of them broken. route_id is the same in both directions, so the
    Pascack answer was always sound; trip_headsign is not, so Port Jervis was not."""
    route_of_trip = {"PASC_OUT": "13", "PASC_IN": "13", "OTHER": "1"}
    calls = _calls(
        [
            ("PASC_OUT", ["Hoboken", "Westwood", "Hillsdale"]),
            ("PASC_IN", ["Hillsdale", "Westwood", "Hoboken"]),
            ("OTHER", ["Hoboken"]),
        ]
    )
    assert trim.route_exclusive_stops(route_of_trip, calls, "13") == {"Westwood", "Hillsdale"}


# ---------------------------------------------------------------------------
# The trim, and the realtime widening that makes the pair join
# ---------------------------------------------------------------------------


def _publication(n_routes: int = 3, trips_per_route: int = 8):
    """A small synthetic publication: several routes, several trips each."""
    trips, pairs = [], []
    for route in range(n_routes):
        for index in range(trips_per_route):
            trip_id = f"R{route}-T{index:02d}"
            trips.append(
                {
                    "trip_id": trip_id,
                    "route_id": str(route),
                    "trip_headsign": "Somewhere",
                    "trip_short_name": trip_id,
                }
            )
            pairs.append((trip_id, ["109", "112", f"S{route}"]))
    stops = [{"stop_id": s} for s in ["109", "112"] + [f"S{r}" for r in range(n_routes)]]
    return trips, _calls(pairs), stops


def _by_route(trips):
    out = defaultdict(list)
    for t in trips:
        out[t["route_id"]].append(t)
    return out


def test_the_plain_trim_keeps_two_trips_per_route():
    trips, calls, stops = _publication()
    kept = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_trips=set(),
        mandated_stops=list(trim.IDENTITY_STOPS),
    )
    assert kept == {"R0-T00", "R0-T01", "R1-T00", "R1-T01", "R2-T00", "R2-T01"}


def test_the_realtime_widening_is_what_makes_the_pair_join():
    """THE FIX FOR THE BLOCKED CAPTURE, as arithmetic.

    The trips in flight at any given moment are not the two lexicographically
    first of each route, and at 17:20 they had no overlap with them at all: 165
    trips in flight, 25 in the trim, zero in common. Passing the capture's trips
    as extra_trip_ids is what turns "does the committed pair join" from a property
    of the capture hour into a property of the writing step.
    """
    trips, calls, stops = _publication()
    in_flight = {"R0-T05", "R1-T06", "R2-T07"}

    without = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_trips=set(),
        mandated_stops=list(trim.IDENTITY_STOPS),
    )
    assert not (in_flight & without), "the shape of the original defect: no overlap at all"

    with_capture = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_trips=set(),
        mandated_stops=list(trim.IDENTITY_STOPS),
        extra_trip_ids=in_flight,
    )
    assert in_flight <= with_capture, "every trip the capture contains must be kept"
    assert without <= with_capture, "and the must-include set is still there"


def test_an_unknown_realtime_trip_id_is_ignored_rather_than_raising():
    """An ADDED trip joins no schedule by design (decoder law 3), and a rollover
    caught mid-capture would show the same way. Neither should abort a trim."""
    trips, calls, stops = _publication()
    kept = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_trips=set(),
        mandated_stops=list(trim.IDENTITY_STOPS),
        extra_trip_ids={"R0-T05", "ADDED-9001"},
    )
    assert "R0-T05" in kept
    assert "ADDED-9001" not in kept


def test_apply_trim_keeps_every_stop_its_trips_call_at():
    """The referential-integrity property the whole fixture rests on: no
    stop_times row may point at a stop that is not in stops.txt. It is also why
    the west-of-Hudson stations survive, and why the guard on them has to be
    written against the kept trips' calls."""
    trips, calls, stops = _publication()
    kept_ids = {"R0-T00", "R2-T03"}
    kept_trips, kept_stops, kept_stop_times = trim.apply_trim(
        trips=trips, stops=stops, calls=calls, kept_trip_ids=kept_ids
    )
    assert {t["trip_id"] for t in kept_trips} == kept_ids
    referenced = {row["stop_id"] for row in kept_stop_times}
    assert referenced <= {s["stop_id"] for s in kept_stops}
    assert referenced == {"109", "112", "S0", "S2"}


def test_the_trim_reproduces_the_committed_fixture_exactly():
    """The extraction is faithful. Re-running the trim over the ALREADY-TRIMMED
    committed fixture is the identity, which it would not be if the extracted
    selection had drifted from the inline one it replaced."""
    import csv
    import io

    fixture = Path(__file__).resolve().parent / "fixtures" / "njt_gtfs"
    if not (fixture / "trips.txt").exists():
        import pytest

        pytest.skip("the 15a static fixture is not committed in this checkout")

    def rows(name):
        text = (fixture / name).read_text(encoding="utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    trips = rows("trips.txt")
    calls = defaultdict(list)
    for row in rows("stop_times.txt"):
        calls[row["trip_id"]].append(row)

    pj_trips = trim.port_jervis_trips(trips, calls)
    pj_stops = trim.exclusive_stops(pj_trips, calls)
    route_of_trip = {t["trip_id"]: t["route_id"] for t in trips}
    pasc = sorted(trim.route_exclusive_stops(route_of_trip, calls, "13"))[: trim.PASC_TRIO]

    kept = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_trips=pj_trips,
        mandated_stops=list(trim.IDENTITY_STOPS) + sorted(pj_stops) + pasc,
    )
    assert kept == {t["trip_id"] for t in trips}, (
        "re-trimming an already-trimmed fixture must be the identity; a difference means "
        "the extracted selection no longer matches the one that produced the fixture"
    )


def test_the_exclusive_set_is_never_empty_while_the_line_is_named():
    """THE PROPERTY THE WEST-OF-HUDSON GUARD RESTS ON, and the reason that guard is
    live again rather than merely reworded.

    Every trip in the set calls at one of the terminals the headsigned trips reach,
    and by construction no trip OUTSIDE the set calls at one, so the terminals are
    always in (mine - others). The exclusive set therefore always contains at least
    the line's own terminal, whatever else is going on in the publication.

    That is what the old headsign-only set could not promise, and its emptiness is
    what silently killed the guard: an empty set minus anything is empty, so the
    check passed for every possible trim.
    """
    outbound = {"trip_id": "OUT", "trip_headsign": "Port Jervis"}
    scenarios = {
        "both directions": (
            [outbound, {"trip_id": "IN", "trip_headsign": "Hoboken"}],
            [("OUT", SHARED_TRACK + WEST_OF_HUDSON), ("IN", WEST_OF_HUDSON[::-1] + SHARED_TRACK)],
        ),
        "with a short-turn that never reaches the terminal": (
            [outbound, {"trip_id": "SHORT", "trip_headsign": "Middletown"}],
            [("OUT", SHARED_TRACK + WEST_OF_HUDSON), ("SHORT", SHARED_TRACK + ["Otisville"])],
        ),
        "with unrelated service all over the shared track": (
            [outbound] + [{"trip_id": f"X{i}", "trip_headsign": "Suffern"} for i in range(20)],
            [("OUT", SHARED_TRACK + WEST_OF_HUDSON)] + [(f"X{i}", SHARED_TRACK) for i in range(20)],
        ),
    }
    for label, (trips, pairs) in scenarios.items():
        calls = _calls(pairs)
        pj = trim.port_jervis_trips(trips, calls)
        exclusive = trim.exclusive_stops(pj, calls)
        assert exclusive, f"{label}: the exclusive set went empty and every guard on it died"
        assert "PortJervis" in exclusive, f"{label}: the terminal itself must always be in it"


def test_the_west_of_hudson_guard_fires_when_the_trim_actually_drops_a_station():
    """THE GUARD IS NON-VACUOUS, demonstrated by making it fail.

    A guard that has never been seen to fire is indistinguishable from one that
    cannot, and this file exists because two successive versions of this one could
    not. The trim here deliberately excludes the only trip serving the far end of
    the line, and the subtraction the generator performs must name the stations
    that went missing.
    """
    trips = [
        {"trip_id": "FULL", "trip_headsign": "Port Jervis", "route_id": "6"},
        {"trip_id": "SHORT", "trip_headsign": "Suffern", "route_id": "6"},
    ]
    calls = _calls([("FULL", SHARED_TRACK + WEST_OF_HUDSON), ("SHORT", SHARED_TRACK)])
    pj_stops = trim.exclusive_stops(trim.port_jervis_trips(trips, calls), calls)
    assert pj_stops == set(WEST_OF_HUDSON)

    # A trim that kept only the short working: exactly the failure the guard is for.
    _kept_trips, _kept_stops, _rows = trim.apply_trim(
        trips=trips,
        stops=[{"stop_id": s} for s in SHARED_TRACK + WEST_OF_HUDSON],
        calls=calls,
        kept_trip_ids={"SHORT"},
    )
    kept_stop_ids = {row["stop_id"] for row in _rows}
    assert sorted(pj_stops - kept_stop_ids) == sorted(WEST_OF_HUDSON), (
        "the generator's subtraction must name every west-of-Hudson station the trim lost"
    )

    # And the top-up the generator actually runs prevents it, which is why a real
    # run passes: every mandated stop pulls in a trip that serves it.
    kept = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route={"6": trips},
        pj_trips=trim.port_jervis_trips(trips, calls),
        mandated_stops=sorted(pj_stops),
    )
    covered = {call["stop_id"] for tid in kept for call in calls[tid]}
    assert not (pj_stops - covered), "the mandated-stop top-up is what keeps the guard green"
