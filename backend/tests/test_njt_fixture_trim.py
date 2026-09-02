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
import random
from collections import defaultdict
from pathlib import Path

_TRIM_PATH = Path(__file__).resolve().parent.parent / "scripts" / "njt_fixture_trim.py"
_spec = importlib.util.spec_from_file_location("njt_fixture_trim", _TRIM_PATH)
trim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trim)

import route_geometry  # noqa: E402  (after the path-loaded trim, like the generators)


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
        pj_keep=set(),
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
        pj_keep=set(),
        mandated_stops=list(trim.IDENTITY_STOPS),
    )
    assert not (in_flight & without), "the shape of the original defect: no overlap at all"

    with_capture = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_keep=set(),
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
        pj_keep=set(),
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
    selection had drifted from the inline one it replaced.

    THE COMMITTED FIXTURE IS THE SELECTION UNION THE CAPTURE. The realtime
    generator re-trims the static around the trips its capture contains
    (extra_trip_ids), so once a capture is committed the bare selection alone
    CANNOT be the identity and was never meant to be: replaying it without the
    extras asserts a rule the writer does not follow. The replay therefore reads
    the committed njt_tu.pb and hands in the same extras, counted by the
    generator's own join check, so writer and replay stay one question. With no
    capture committed the extras are empty and this is 15a's original identity
    claim, unweakened."""
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

    # The capture's SCHEDULED-or-CANCELED trip ids, which are the ones that claim
    # a place in the schedule and therefore the ones the writer widened the trim
    # around. Counted by the generator's own _trip_ids_by_claim rather than
    # re-derived here, so the replay cannot disagree with the join check about
    # which trips those are (ADDED ids are excluded there; their empty trip_id
    # would be ignored by select_trim anyway).
    extras: set[str] = set()
    tu_path = fixture.parent / "njt_tu.pb"
    if tu_path.exists():
        gen_path = Path(__file__).resolve().parent.parent / "scripts" / "gen_njt_rt_fixture.py"
        gen_spec = importlib.util.spec_from_file_location("gen_njt_rt_fixture", gen_path)
        gen = importlib.util.module_from_spec(gen_spec)
        gen_spec.loader.exec_module(gen)
        scheduled_or_canceled, _added = gen._trip_ids_by_claim(tu_path.read_bytes())
        extras = set(scheduled_or_canceled)

    pj_trips = trim.port_jervis_trips(trips, calls)
    headsigned = {t["trip_id"] for t in trips if "port jervis" in t["trip_headsign"].lower()}
    woh = trim.west_of_hudson_stops(pj_trips, calls)
    route_of_trip = {t["trip_id"]: t["route_id"] for t in trips}
    pj_keep, _uncovered = trim.select_port_jervis_trips(
        pj_trips, headsigned, calls, woh, route_of_trip
    )
    pasc = trim.select_pasc_trio(trim.route_exclusive_stops(route_of_trip, calls, "13"))

    kept = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route=_by_route(trips),
        pj_keep=pj_keep,
        mandated_stops=list(trim.IDENTITY_STOPS) + sorted(woh) + pasc,
        extra_trip_ids=extras,
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
    pj_trips = trim.port_jervis_trips(trips, calls)
    headsigned = {t["trip_id"] for t in trips if "port jervis" in t["trip_headsign"].lower()}
    woh = trim.west_of_hudson_stops(pj_trips, calls)
    pj_keep, _uncovered = trim.select_port_jervis_trips(pj_trips, headsigned, calls, woh)
    # Suffern is the junction here, so the derived set is the shared-track stop
    # adjacent to the exclusive block plus the block itself.
    assert set(WEST_OF_HUDSON) <= woh

    # A trim that kept only the short working: exactly the failure the guard is for.
    _kept_trips, _kept_stops, _rows = trim.apply_trim(
        trips=trips,
        stops=[{"stop_id": s} for s in SHARED_TRACK + WEST_OF_HUDSON],
        calls=calls,
        kept_trip_ids={"SHORT"},
    )
    kept_stop_ids = {row["stop_id"] for row in _rows}
    assert set(WEST_OF_HUDSON) <= (woh - kept_stop_ids), (
        "the generator's subtraction must name every west-of-Hudson station the trim lost"
    )

    # And the top-up the generator actually runs prevents it, which is why a real
    # run passes: every mandated stop pulls in a trip that serves it.
    kept = trim.select_trim(
        trips=trips,
        calls=calls,
        trips_by_route={"6": trips},
        pj_keep=pj_keep,
        mandated_stops=sorted(woh),
    )
    covered = {call["stop_id"] for tid in kept for call in calls[tid]}
    assert not (woh - covered), "the mandated-stop top-up is what keeps the guard green"


# ---------------------------------------------------------------------------
# Coverage-driven Port Jervis selection
# ---------------------------------------------------------------------------
#
# THE MANDATE HOLDS BY CONSTRUCTION OR IT DOES NOT HOLD. The trim must carry the
# whole west-of-Hudson line, and the old rule kept sorted(pj_trips)[:2], which
# satisfied that only because the headsign-only set happened to contain exactly two
# full outbound runs. Once the set is direction-agnostic it also holds inbound
# workings and short-turns, and two lexicographically-first entries can easily be a
# pair that never reaches the far end.


def _line(trip_id, headsign, route, stops):
    return {"trip_id": trip_id, "trip_headsign": headsign, "route_id": route}, (trip_id, stops)


# A publication where the lexicographic pick is WRONG. The two lowest trip ids are
# short-turns that stop at Suffern; the full runs sort last.
SHORT_TURN_TRAP_TRIPS, SHORT_TURN_TRAP_PAIRS = zip(
    _line("0001", "Port Jervis", "6", SHARED_TRACK + ["Suffern"]),
    _line("0002", "Port Jervis", "5", SHARED_TRACK + ["Suffern"]),
    _line("9001", "Port Jervis", "6", SHARED_TRACK + ["Suffern"] + WEST_OF_HUDSON),
    _line("9002", "Hoboken", "5", WEST_OF_HUDSON[::-1] + ["Suffern"] + SHARED_TRACK[::-1]),
    _line("5000", "Suffern", "6", SHARED_TRACK + ["Suffern"]),
)
SHORT_TURN_TRAP_TRIPS = list(SHORT_TURN_TRAP_TRIPS)


def _trap():
    calls = _calls(list(SHORT_TURN_TRAP_PAIRS))
    trips = SHORT_TURN_TRAP_TRIPS
    pj = trim.port_jervis_trips(trips, calls)
    headsigned = {t["trip_id"] for t in trips if "port jervis" in t["trip_headsign"].lower()}
    woh = trim.west_of_hudson_stops(pj, calls)
    route_of_trip = {t["trip_id"]: t["route_id"] for t in trips}
    return trips, calls, pj, headsigned, woh, route_of_trip


def test_the_lexicographic_pick_would_have_missed_the_far_end_of_the_line():
    """THE TRAP ITSELF, measured before the fix is applied to it.

    This is not hypothetical: the two lowest trip ids here are short-turns that go
    no further than the junction, so taking the first two by id keeps neither of
    the runs that reach Otisville or Port Jervis. Under the old rule those stations
    left the fixture and every guard stayed silent, because the guards were derived
    from the same set the selection was.
    """
    _trips, calls, pj, _headsigned, woh, _routes = _trap()
    assert {"Otisville", "PortJervis"} <= woh, "the far end is part of the mandate"

    lexicographic = sorted(pj)[:2]
    covered = {c["stop_id"] for tid in lexicographic for c in calls[tid]}
    assert not (woh <= covered), (
        "the trap must actually trap: the first two by id must fail to cover the line"
    )
    assert sorted(woh - covered) == ["Middletown", "Otisville", "PortJervis"]


def test_coverage_selection_reaches_the_far_end_the_lexicographic_pick_missed():
    """The fix against the same publication. Chosen by what they cover, the kept
    trips reach every west-of-Hudson station and nothing is left uncovered."""
    _trips, calls, pj, headsigned, woh, route_of_trip = _trap()
    kept, uncovered = trim.select_port_jervis_trips(pj, headsigned, calls, woh, route_of_trip)
    covered = {c["stop_id"] for tid in kept for c in calls[tid]}
    assert not uncovered, f"nothing may be left uncovered, got {sorted(uncovered)}"
    assert woh <= covered, f"the kept trips must cover the line, missing {sorted(woh - covered)}"


def test_coverage_selection_keeps_a_trip_that_names_the_line():
    """Coverage alone could satisfy itself with inbound workings headsigned
    Hoboken, leaving the fixture carrying the line's stations and no evidence of
    whose they are. The headsign golden reads trip_headsign, so one must survive."""
    _trips, calls, pj, headsigned, woh, route_of_trip = _trap()
    kept, _uncovered = trim.select_port_jervis_trips(pj, headsigned, calls, woh, route_of_trip)
    assert kept & headsigned, "a trip headsigned Port Jervis must always be kept"


def test_coverage_selection_keeps_both_route_ids_the_line_runs_under():
    """PORT JERVIS HAS NO ROUTE OF ITS OWN, and 15a's golden asserts its stations
    report MAIN (6) and BERG (5). One full run covers all nine stations, so pure
    coverage could keep a single route-6 working and delete the evidence for the
    claim the fixture exists to support."""
    _trips, calls, pj, headsigned, woh, route_of_trip = _trap()
    kept, _uncovered = trim.select_port_jervis_trips(pj, headsigned, calls, woh, route_of_trip)
    assert {route_of_trip[t] for t in kept} >= {"5", "6"}


def test_coverage_selection_is_deterministic():
    """The committed fixture has to be reproducible, so the greedy walk is over
    sorted candidates and ties break to the first. Run it repeatedly and it must
    not move."""
    _trips, calls, pj, headsigned, woh, route_of_trip = _trap()
    answers = {
        frozenset(trim.select_port_jervis_trips(pj, headsigned, calls, woh, route_of_trip)[0])
        for _ in range(8)
    }
    assert len(answers) == 1


def test_an_unservable_station_is_reported_rather_than_silently_dropped():
    """A station no Port Jervis trip reaches on the day of the capture is a fact
    about the schedule, not a fault. The selection reports it as uncovered so the
    generator can name it, and the mandated-stop top-up still forces its row in."""
    trips = [{"trip_id": "OUT", "trip_headsign": "Port Jervis", "route_id": "6"}]
    calls = _calls([("OUT", SHARED_TRACK + WEST_OF_HUDSON)])
    pj = trim.port_jervis_trips(trips, calls)
    woh = trim.west_of_hudson_stops(pj, calls) | {"Otisville-Closed"}
    kept, uncovered = trim.select_port_jervis_trips(pj, {"OUT"}, calls, woh)
    assert uncovered == {"Otisville-Closed"}
    assert kept == {"OUT"}, "and the rest of the line is still covered"


def test_the_derived_nine_include_the_junction_that_exclusivity_drops():
    """SUFFERN IS THE NINTH, and it is derived rather than named: it is the stop
    adjacent to the exclusive block, on the shared-track side. Taking it from the
    data means a renamed or relocated junction follows automatically."""
    trips = [
        {"trip_id": "OUT", "trip_headsign": "Port Jervis", "route_id": "6"},
        {"trip_id": "TERM", "trip_headsign": "Suffern", "route_id": "6"},
    ]
    calls = _calls([("OUT", SHARED_TRACK + WEST_OF_HUDSON), ("TERM", SHARED_TRACK)])
    pj = trim.port_jervis_trips(trips, calls)
    exclusive = trim.exclusive_stops(pj, calls)
    woh = trim.west_of_hudson_stops(pj, calls)
    assert "Suffern" not in exclusive, "exclusivity drops it: a Suffern working also serves it"
    assert "Suffern" in woh, "and the junction rule puts it back"
    assert woh == exclusive | {"Suffern"}


def test_the_junction_is_found_from_an_inbound_run_too():
    """The exclusive block sits at the END of an outbound run and at the START of
    an inbound one, so the adjacent stop is looked for on whichever side has one.
    A derivation that only walked backwards would miss it on half the feed.

    THE TRIP SET IS PASSED DIRECTLY rather than derived, and that is deliberate:
    with an outbound trip also present its block sits at the far end and supplies
    the junction from the other side, so the forward branch would never have to
    work and a mutation removing it would survive. Exactly that happened, which is
    why this test isolates the inbound case instead of building a realistic feed.
    """
    calls = _calls(
        [
            # Port Jervis first, then the junction, then shared track: the exclusive
            # block is at indices 0 to 2 and the junction is the stop AFTER it.
            ("IN", WEST_OF_HUDSON[::-1] + SHARED_TRACK[::-1]),
            ("TERM", SHARED_TRACK[::-1]),
        ]
    )
    exclusive = trim.exclusive_stops({"IN"}, calls)
    assert exclusive == set(WEST_OF_HUDSON), "the far end is exclusive to the inbound run"
    stations = trim.west_of_hudson_stops({"IN"}, calls)
    assert stations == set(WEST_OF_HUDSON) | {"Suffern"}, (
        "the junction must be picked up from the stop FOLLOWING the block on an "
        f"inbound run; got {sorted(stations)}"
    )


def test_the_headsign_guarantee_bites_when_the_best_cover_is_an_inbound_working():
    """The trap above has an OUTBOUND trip as its best cover, so the headsign
    guarantee never has to do anything there. This is the publication where it
    does: the inbound working sorts first and covers the whole line by itself, so
    pure coverage would keep it alone and the fixture would carry every Port
    Jervis station with nothing naming the line they belong to.
    """
    trips = [
        {"trip_id": "0001", "trip_headsign": "Hoboken", "route_id": "6"},
        {"trip_id": "9999", "trip_headsign": "Port Jervis", "route_id": "6"},
    ]
    calls = _calls(
        [
            ("0001", WEST_OF_HUDSON[::-1] + ["Suffern"] + SHARED_TRACK),
            ("9999", SHARED_TRACK + ["Suffern"] + WEST_OF_HUDSON),
        ]
    )
    pj = trim.port_jervis_trips(trips, calls)
    woh = trim.west_of_hudson_stops(pj, calls)
    route_of_trip = {t["trip_id"]: t["route_id"] for t in trips}

    inbound_cover = {c["stop_id"] for c in calls["0001"]}
    assert woh <= inbound_cover, "the inbound working really does cover the whole line"

    kept, uncovered = trim.select_port_jervis_trips(pj, {"9999"}, calls, woh, route_of_trip)
    assert not uncovered
    assert "9999" in kept, (
        "a trip headsigned Port Jervis must be kept even when it adds no coverage, or the "
        "fixture has the line's stations and no evidence of whose they are"
    )


def test_the_greedy_walk_keeps_going_until_the_line_is_covered():
    """A publication where NO single trip covers the line: one working runs to
    Middletown, another to Port Jervis by a different set of intermediate stops.
    Covering it takes two, so a selection that stopped after the first would leave
    the far end out with everything looking fine.
    """
    trips = [
        {"trip_id": "PJ_A", "trip_headsign": "Port Jervis", "route_id": "6"},
        {"trip_id": "PJ_B", "trip_headsign": "Port Jervis", "route_id": "6"},
        {"trip_id": "TERM", "trip_headsign": "Suffern", "route_id": "6"},
    ]
    calls = _calls(
        [
            ("PJ_A", SHARED_TRACK + ["Suffern", "Sloatsburg", "Tuxedo", "Middletown"]),
            ("PJ_B", SHARED_TRACK + ["Suffern", "Harriman", "Otisville", "PortJervis"]),
            ("TERM", SHARED_TRACK + ["Suffern"]),
        ]
    )
    pj = trim.port_jervis_trips(trips, calls)
    woh = trim.west_of_hudson_stops(pj, calls)
    route_of_trip = {t["trip_id"]: t["route_id"] for t in trips}
    assert len(woh) == 7, sorted(woh)

    for trip_id in ("PJ_A", "PJ_B"):
        covered = {c["stop_id"] for c in calls[trip_id]}
        assert not (woh <= covered), f"{trip_id} alone must not cover the line"

    kept, uncovered = trim.select_port_jervis_trips(pj, {"PJ_A", "PJ_B"}, calls, woh, route_of_trip)
    assert not uncovered, f"the walk must continue until nothing is left, got {sorted(uncovered)}"
    assert kept == {"PJ_A", "PJ_B"}


def test_the_junction_survives_stop_times_rows_arriving_out_of_order():
    """stop_times.txt row order is a convention, not a guarantee, and the junction
    derivation is meaningless on a shuffled list: it reads the stop BESIDE the
    exclusive block, which is only "beside" it in call order.

    The generators build their call index straight from row order, so this is the
    one place the defensive sort earns its keep. Built here with the rows reversed
    and stop_sequence still correct, which is exactly what a publisher emitting
    grouped-by-stop rather than grouped-by-trip would produce.
    """
    ordered_stops = SHARED_TRACK + WEST_OF_HUDSON  # Suffern is SHARED_TRACK's last
    sequence = {stop_id: index + 1 for index, stop_id in enumerate(ordered_stops)}
    # NOT merely reversed: reversal is symmetric here, so both branches of the
    # junction rule still find Suffern and the sort looks unnecessary. This order
    # puts Hoboken directly before the exclusive block, so an unsorted walk reads
    # the wrong junction and claims a shared-track terminal is west of the Hudson.
    scrambled_order = ["Hoboken", "Otisville", "Middletown", "PortJervis", "Suffern", "Ridgewood"]
    shuffled = {
        "OUT": [
            {"trip_id": "OUT", "stop_id": stop_id, "stop_sequence": str(sequence[stop_id])}
            for stop_id in scrambled_order
        ],
        "TERM": [
            {"trip_id": "TERM", "stop_id": stop_id, "stop_sequence": str(sequence[stop_id])}
            for stop_id in reversed(SHARED_TRACK)
        ],
    }
    assert [row["stop_id"] for row in shuffled["OUT"]] != ordered_stops, "the rows are shuffled"

    stations = trim.west_of_hudson_stops({"OUT"}, shuffled)
    assert stations == set(WEST_OF_HUDSON) | {"Suffern"}, (
        f"the junction must still be found when rows arrive out of order; got {sorted(stations)}"
    )


# ---------------------------------------------------------------------------
# The seventh member: geometry (15c)
# ---------------------------------------------------------------------------


def _shape_rows(pairs) -> list[dict]:
    """shapes.txt rows from (shape_id, point count) pairs, in publication order.

    THE POINTS ZIG-ZAG rather than running straight, because the selection
    simplifies: three collinear points are two after Douglas-Peucker, and a helper
    that produced them would make every count below a statement about the tolerance
    instead of about the selection. The deflection here is far larger than
    NJT_SIMPLIFY_EPS, so every point survives and the counts mean what they say.
    """
    return [
        {
            "shape_id": shape_id,
            "shape_pt_sequence": str(seq),
            "shape_pt_lat": f"{40.7 + 0.01 * (seq % 2):.5f}",
            "shape_pt_lon": f"{-74.0 - 0.01 * seq:.5f}",
        }
        for shape_id, count in pairs
        for seq in range(1, count + 1)
    ]


def test_referenced_shape_ids_reads_the_trips_and_drops_blanks():
    trips = [
        {"trip_id": "T1", "shape_id": "s1"},
        {"trip_id": "T2", "shape_id": "s2"},
        {"trip_id": "T3", "shape_id": "s1"},  # a repeat is one shape, not two
        {"trip_id": "T4", "shape_id": ""},  # a trip may carry no shape
        {"trip_id": "T5"},  # or no column at all
    ]
    assert trim.referenced_shape_ids(trips) == {"s1", "s2"}


def test_select_shape_rows_keeps_every_row_of_a_wanted_shape_in_publication_order():
    rows = _shape_rows([("s1", 3), ("s2", 2), ("s3", 4)])
    kept = trim.select_shape_rows(rows, {"s1", "s3"})
    assert [row["shape_id"] for row in kept] == ["s1", "s1", "s1", "s3", "s3", "s3", "s3"]
    # PUBLICATION ORDER, so the committed file is a faithful slice: the s1 rows keep
    # their original relative order and still precede s3's, as upstream wrote them.
    assert [row["shape_pt_sequence"] for row in kept] == ["1", "2", "3", "1", "2", "3", "4"]
    assert trim.select_shape_rows(rows, set()) == []


def test_select_shape_rows_simplifies_and_keeps_the_publications_own_rows():
    """THE SECOND REDUCTION. A shape whose middle points sit on the straight line
    between its ends is committed as its ends, and the rows that survive are the
    publication's own: original coordinate text, original shape_pt_sequence numbers,
    which therefore SKIP where points were dropped."""
    straight = [
        {
            "shape_id": "s1",
            "shape_pt_sequence": str(seq),
            "shape_pt_lat": f"{40.70000 + 0.001 * seq:.5f}",
            "shape_pt_lon": f"{-74.00000 - 0.001 * seq:.5f}",
        }
        for seq in range(1, 6)
    ]
    kept = trim.select_shape_rows(straight, {"s1"})
    assert [row["shape_pt_sequence"] for row in kept] == ["1", "5"]
    # VERBATIM: the same dict objects, so the text NJ Transit published is what
    # gets written, never a float reformatted back into a string.
    assert kept[0] is straight[0] and kept[1] is straight[-1]

    # A BEND KEEPS ITS VERTEX, AND ONLY ITS VERTEX. An explicit V: points 1-2-3 lie
    # on one straight leg and 3-4-5 on the other, so 2 and 4 are exactly on the legs
    # that replace them and 3 is the only point the line cannot do without.
    v_shape = [
        {
            "shape_id": "s1",
            "shape_pt_sequence": str(seq),
            "shape_pt_lat": f"{lat:.5f}",
            "shape_pt_lon": f"{lon:.5f}",
        }
        for seq, (lat, lon) in enumerate(
            [
                (40.700, -74.000),
                (40.701, -74.001),
                (40.702, -74.002),
                (40.703, -74.001),
                (40.704, -74.000),
            ],
            start=1,
        )
    ]
    assert [row["shape_pt_sequence"] for row in trim.select_shape_rows(v_shape, {"s1"})] == [
        "1",
        "3",
        "5",
    ]


def test_what_the_trim_writes_is_a_fixed_point_of_what_the_loader_reads():
    """THE COUPLING, ON A PUBLICATION WITH MORE DECIMALS THAN THE LOADER KEEPS.

    NJ Transit publishes six decimals (its stops are 40.750568) and the loader
    rounds every coordinate to five before it simplifies. This trim used to
    simplify the full-precision text instead, so it decided on points production
    never sees: a rounding shift of half a metre moved points across the keep/drop
    threshold and the committed file stopped being a fixed point of the loader's own
    simplification. Measured before the fix, 82 of 300 synthetic publications like
    these failed; the guarded fixed-point golden would have gone red on the first
    real refresh, with no hermetic test to explain why.

    Twenty seeds rather than one, because the failure was data-dependent: any single
    shape had a better than even chance of passing while the coupling was broken.
    """
    for seed in range(20):
        rnd = random.Random(seed)
        lat, lon = 40.7, -74.0
        rows = []
        for seq in range(1, 120):
            lat += rnd.gauss(0, 0.00012)
            lon += rnd.gauss(0, 0.00012)
            rows.append(
                {
                    "shape_id": "s1",
                    "shape_pt_sequence": str(seq),
                    "shape_pt_lat": f"{lat:.6f}",
                    "shape_pt_lon": f"{lon:.6f}",
                }
            )
        committed = trim.select_shape_rows(rows, {"s1"})
        # What the loader makes of that committed file: parse, round, simplify.
        parsed = [
            [
                round(float(row["shape_pt_lat"]), route_geometry.COORD_PRECISION),
                round(float(row["shape_pt_lon"]), route_geometry.COORD_PRECISION),
            ]
            for row in committed
        ]
        again = route_geometry.simplify_polyline(parsed, route_geometry.NJT_SIMPLIFY_EPS)
        assert again == parsed, (
            f"seed {seed}: the loader reduces the {len(parsed)} committed points to "
            f"{len(again)}, so the trim and the loader are not simplifying the same points"
        )


def test_select_shape_rows_skips_a_row_that_is_not_a_point():
    """The loader skips a malformed shapes row, so the fixture must not carry one:
    a committed row the loader ignores would make the fixed-point golden compare the
    file against a parse that never saw all of it."""
    rows = [
        {
            "shape_id": "s1",
            "shape_pt_sequence": "1",
            "shape_pt_lat": "40.7",
            "shape_pt_lon": "-74.0",
        },
        {"shape_id": "s1", "shape_pt_sequence": "2", "shape_pt_lat": "", "shape_pt_lon": "-74.1"},
        {
            "shape_id": "s1",
            "shape_pt_sequence": "3",
            "shape_pt_lat": "40.9",
            "shape_pt_lon": "-74.2",
        },
    ]
    kept = trim.select_shape_rows(rows, {"s1"})
    assert [row["shape_pt_sequence"] for row in kept] == ["1", "3"]


def test_shapes_txt_is_the_seventh_member_and_the_only_optional_one():
    """Both facts in one place, because the writers read this tuple to decide what
    to write and a reader of it has to know one member may be absent."""
    assert trim.FIXTURE_MEMBERS[-1] == "shapes.txt"
    assert len(trim.FIXTURE_MEMBERS) == 7
    assert trim.OPTIONAL_MEMBERS == {"shapes.txt"}


def test_the_committed_shapes_are_exactly_what_the_committed_trips_reference():
    """THE IDENTITY REPLAY'S GEOMETRY HALF: kept shapes == shapes referenced by kept
    trips, nothing more. Read straight off the two committed files, because that
    pair is what every route-line golden is built on, and a fixture whose trips
    point at geometry it does not carry would make those goldens measure the gap
    instead of the map.

    Guarded on shapes.txt like the line goldens themselves: it skips until the
    refresh lands and FAILS in CI, rather than passing vacuously in between."""
    import csv
    import io

    fixture = Path(__file__).resolve().parent / "fixtures" / "njt_gtfs"
    shapes_path = fixture / "shapes.txt"
    if not shapes_path.exists():
        import os

        import pytest

        reason = (
            f"golden fixture missing ({shapes_path}); run backend/scripts/gen_njt_fixture.py "
            "--shapes-only to generate it"
        )
        if os.environ.get("CI"):
            pytest.fail(reason, pytrace=False)
        pytest.skip(reason)

    def rows(name):
        text = (fixture / name).read_text(encoding="utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    referenced = trim.referenced_shape_ids(rows("trips.txt"))
    committed = {trim.get(row, "shape_id") for row in rows("shapes.txt")} - {""}
    assert committed == referenced, (
        f"unreferenced shapes committed: {sorted(committed - referenced)}; "
        f"referenced shapes missing: {sorted(referenced - committed)}"
    )
