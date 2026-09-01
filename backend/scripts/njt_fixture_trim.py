"""The NJ Transit fixture trim, shared by both generators.

EXTRACTED SO THE REALTIME CAPTURE CAN RE-TRIM. gen_njt_fixture.py used to own this
inline, which was fine while the static fixture was the only thing that read it.
It is not fine now: the realtime capture must be able to widen the trim to cover
the trips that were actually in flight when it was taken, or the committed
static/realtime pair does not join and every golden built on the pair is
measuring the trim rather than the decoder.

THE DEFECT THAT FORCED THIS, recorded because the shape recurs. The realtime
generator checked whether a live capture joined the COMMITTED TRIM: 25 trips, two
lexicographically-first per route. At 5:20 PM there were 165 trips in flight and
none of them were those 25, so the join measured 0.0000 and the script refused.
The refusal blamed "trip ids roll over with each schedule publication", which the
script had never measured and which was false that evening: the archive
re-downloaded to a byte-identical 3,294,076 bytes, the same publication the
2026-08-05 probe had joined live realtime against at 112/112. A gate that asserts
a cause it did not measure sends the reader to the wrong place, and this one sent
them to a rollover that had not happened.

THE FIX IS COHERENCE BY CONSTRUCTION rather than by luck of the capture hour. The
realtime script now joins against the FULL live static, and on success re-trims
the static to the must-include set UNION the trips the capture actually contains.
The pair therefore always joins, and the goldens can assert a MEASURED join floor
instead of hoping.
"""

from __future__ import annotations

# Two identity stops the fixture must always carry, by id. Checked by NAME in the
# generator because this feed's ids are small integers with heavy cross-system
# collision, so presence alone proves nothing.
IDENTITY_STOPS = {"109": "New York", "112": "Newark"}

TRIPS_PER_ROUTE = 2  # enough for a join golden, small enough to stay a trim
PASC_TRIO = 3  # "the PASC trio": three Pascack-only stations, per the 15a spec


def get(row: dict, key: str) -> str:
    """A CSV cell as a stripped string, tolerant of a missing column."""
    return (row.get(key) or "").strip()


def port_jervis_trips(trips: list[dict], calls: dict[str, list[dict]]) -> set[str]:
    """Every trip on the Port Jervis line, IN BOTH DIRECTIONS.

    PORT JERVIS HAS NO ROUTE OF ITS OWN. The probe found its service running under
    the MAIN (6) and BERG (5) route ids, with the Port Jervis identity appearing
    nowhere in routes.txt and only in trip_headsign. So the OUTBOUND trips can only
    be found by headsign, which is what this starts from.

    THE HEADSIGN ALONE IS HALF THE SERVICE, AND THAT WAS A REAL DEFECT. A headsign
    names a destination, so it is direction-dependent: trains running back from
    Port Jervis are headsigned Hoboken or Secaucus and match nothing here. The old
    version stopped at the headsign and then took "stops only these trips serve",
    which on the full feed is EMPTY BY CONSTRUCTION, because every west-of-Hudson
    station is also served by the return working that the headsign filter missed.
    Proven synthetically: adding one inbound trip headsigned Hoboken over the same
    stops collapses that exclusive set from three stations to none.

    The contrast with _exclusive_stops in the generator is the whole lesson, and
    the two functions sat side by side: that one keys on route_id, which is the
    same in both directions, so its Pascack answer was always sound. This one keyed
    on headsign, which is not.

    THE COMPLETION IS DIRECTION-AGNOSTIC: any trip that CALLS AT the terminal a
    headsigned trip ends at is the same line's service, whichever way it is
    pointing. Returns the union, so callers get the line rather than one direction
    of it.
    """
    headsigned = {
        get(trip, "trip_id")
        for trip in trips
        if "port jervis" in get(trip, "trip_headsign").lower()
    }
    # The terminals those trips reach: normally the single Port Jervis station, but
    # taken from the data rather than by name so a rename cannot silently empty it.
    terminals: set[str] = set()
    for trip_id in headsigned:
        stops_on_trip = [get(call, "stop_id") for call in calls.get(trip_id, [])]
        if stops_on_trip:
            terminals.add(stops_on_trip[-1])
    if not terminals:
        return headsigned
    return headsigned | {
        trip_id
        for trip_id, trip_calls in calls.items()
        if any(get(call, "stop_id") in terminals for call in trip_calls)
    }


def exclusive_stops(trip_ids: set[str], calls: dict[str, list[dict]]) -> set[str]:
    """Stops served by `trip_ids` and by no other trip.

    The one exclusivity primitive, so Port Jervis and Pascack cannot drift apart
    again. Correctness depends entirely on `trip_ids` being the WHOLE service being
    asked about, in both directions; see port_jervis_trips for what happens when it
    is only half.

    A SHORT-TURNING TRIP LEGITIMATELY SHRINKS THIS, and that is not drift. A train
    that terminates at Middletown serves the near half of the west-of-Hudson
    stations while never reaching Port Jervis, so under any definition keyed on the
    terminal it counts as "other" and removes those stations from the exclusive
    set. The number is therefore a MEASUREMENT of a specific question, not a census
    of the line's stations, and callers must report it as such.
    """
    mine: set[str] = set()
    others: set[str] = set()
    for trip_id, trip_calls in calls.items():
        target = mine if trip_id in trip_ids else others
        for call in trip_calls:
            target.add(get(call, "stop_id"))
    return (mine - others) - {""}


def _ordered_stop_ids(trip_calls: list[dict]) -> list[str]:
    """A trip's stop ids in call order. Sorted on stop_sequence defensively: the
    generators build their call index straight from stop_times.txt row order, which
    a publisher is under no obligation to keep sorted, and the junction derivation
    below is meaningless on a shuffled list."""
    ordered = sorted(trip_calls, key=lambda call: int(get(call, "stop_sequence") or 0))
    return [get(call, "stop_id") for call in ordered]


def west_of_hudson_stops(pj_trips: set[str], calls: dict[str, list[dict]]) -> set[str]:
    """The west-of-Hudson stations: the line's own stops plus the junction it leaves
    the rest of the network at.

    THE EXCLUSIVE SET IS EIGHT OF THE NINE, AND SUFFERN IS THE NINTH. That gap is
    its own artifact, separate from the direction-dependence that used to empty the
    set entirely: Suffern is a real Port Jervis station AND the terminus of trips
    headsigned "Suffern", so trips outside the line serve it and exclusivity
    correctly excludes it. Measured on the committed fixture, which reports the
    other eight.

    SO THE NINTH IS DERIVED RATHER THAN NAMED. Walk a Port Jervis run in call order
    and the exclusive stops form one contiguous block at whichever end the line
    runs to; the stop immediately beside that block is the junction, the last one
    shared with Main and Bergen service. Taking it from the data means a renamed or
    relocated junction follows automatically, where a hardcoded "Suffern" would
    quietly stop matching.

    Both directions are handled because the block sits at the END of an outbound
    run and at the START of an inbound one, so the adjacent stop is looked for on
    whichever side has one.
    """
    exclusive = exclusive_stops(pj_trips, calls)
    if not exclusive:
        # No exclusive stops means the trip set has cancelled itself (see
        # port_jervis_trips) or the line genuinely is not running. Either way there
        # is no block to find a junction beside, and inventing one would be a guess.
        return set()
    stations = set(exclusive)
    for trip_id in pj_trips:
        ordered = _ordered_stop_ids(calls.get(trip_id, []))
        positions = [i for i, stop_id in enumerate(ordered) if stop_id in exclusive]
        if not positions:
            continue
        first, last = min(positions), max(positions)
        if first > 0:
            stations.add(ordered[first - 1])
        if last < len(ordered) - 1:
            stations.add(ordered[last + 1])
    return stations - {""}


def select_port_jervis_trips(
    pj_trips: set[str],
    headsigned: set[str],
    calls: dict[str, list[dict]],
    must_cover: set[str],
    route_of_trip: dict[str, str] | None = None,
) -> tuple[set[str], set[str]]:
    """(trips to keep, stations still uncovered) for the Port Jervis mandate.

    COVERAGE-DRIVEN RATHER THAN LEXICOGRAPHIC, and the difference is the mandate
    holding by construction instead of by luck. The old rule kept
    sorted(pj_trips)[:2], which was written when that set was two outbound runs and
    therefore covered the line by accident. Once the set is direction-agnostic it
    contains inbound workings and short-turns too, and the two lexicographically
    first can easily be a pair that never reaches Otisville or Port Jervis: the
    stations would then vanish from the fixture with every guard silent, because a
    guard on a set nothing selected against cannot see it.

    Greedy set cover, which is the right tool and is close to free here: one full
    run usually covers all nine, so coverage alone often wants a single trip and
    the route-representation pass below brings it back to two. Candidates are
    walked in sorted order and ties break to the first, so the choice is
    deterministic across runs and the committed fixture is reproducible.

    A TRIP THAT NAMES THE LINE IS ALWAYS KEPT. The headsign golden reads
    trip_headsign, and coverage alone could satisfy itself entirely with inbound
    workings headsigned Hoboken, which would leave the fixture carrying the line's
    stations and no evidence of whose they are.

    Returns what it could NOT cover rather than raising: a station genuinely
    unserved on the day of the capture is a fact about the schedule, and the caller
    reports it and lets the mandated-stop top-up pull the row in from elsewhere.
    """
    remaining = set(must_cover)
    kept: set[str] = set()
    for _ in range(len(pj_trips)):
        if not remaining:
            break
        best_trip, best_cover = None, set()
        for trip_id in sorted(pj_trips):
            if trip_id in kept:
                continue
            cover = {get(call, "stop_id") for call in calls.get(trip_id, [])} & remaining
            if len(cover) > len(best_cover):
                best_trip, best_cover = trip_id, cover
        if best_trip is None:
            break
        kept.add(best_trip)
        remaining -= best_cover
    if headsigned and not (kept & headsigned):
        kept.add(sorted(headsigned)[0])
    # EVERY ROUTE ID THE LINE RUNS UNDER STAYS REPRESENTED, which coverage alone
    # does not guarantee: one full run covers all nine stations, and if it happens
    # to be a route 6 working then route 5 disappears from the west-of-Hudson
    # stations entirely. 15a's golden asserts those stations report MAIN (6) and
    # BERG (5) precisely because Port Jervis has no route of its own, so a fixture
    # that can only show one of them has lost the evidence for the claim it exists
    # to make. Costs one extra trip.
    if route_of_trip:
        represented = {route_of_trip.get(t, "") for t in kept} - {""}
        for route_id in sorted({route_of_trip.get(t, "") for t in pj_trips} - {""}):
            if route_id in represented:
                continue
            on_route = sorted(t for t in pj_trips if route_of_trip.get(t) == route_id)
            if on_route:
                kept.add(on_route[0])
                represented.add(route_id)
    return kept, remaining


def route_exclusive_stops(
    route_of_trip: dict[str, str], calls: dict[str, list[dict]], route_id: str
) -> set[str]:
    """Stops served ONLY by trips on `route_id`. Pascack Valley trains also reach
    Hoboken over shared track, so its own stations are the ones nothing else calls
    at. Keyed on route_id, which is direction-independent, so unlike the headsign
    the answer does not depend on which way the trains are pointing."""
    return exclusive_stops({t for t, r in route_of_trip.items() if r == route_id}, calls)


def select_pasc_trio(pasc_stops: set[str]) -> list[str]:
    """The PASC_TRIO stations the fixture mandates for Pascack Valley, in numeric
    stop-id order. One function on purpose: both generators and the identity
    replay in the tests must mandate the SAME three, and while each sorted for
    itself they did not — the replay sorted lexicographically where the
    generators sorted numerically, which picks a different trio as soon as the
    exclusive set outgrows PASC_TRIO, and the difference surfaces as an identity
    failure blamed on the trim."""
    return sorted(pasc_stops, key=lambda s: (int(s) if s.isdigit() else 0, s))[:PASC_TRIO]


def select_trim(
    *,
    trips: list[dict],
    calls: dict[str, list[dict]],
    trips_by_route: dict[str, list[dict]],
    pj_keep: set[str],
    mandated_stops: list[str],
    extra_trip_ids: set[str] = frozenset(),
) -> set[str]:
    """The trip ids the fixture keeps.

    `extra_trip_ids` is the whole reason this is a function. Passed empty it
    reproduces the original static trim exactly; passed the trips a realtime
    capture contains, it widens the trim so the committed pair joins BY
    CONSTRUCTION rather than by whether the capture hour happened to include the
    two lexicographically-first trips of each route. Ids that name no trip in this
    publication are ignored rather than raising: an ADDED realtime trip is expected
    to match nothing, and a rollover mid-capture should not abort a trim.
    """
    kept: set[str] = set()
    for _route_id, route_trips in sorted(trips_by_route.items()):
        for trip in sorted(route_trips, key=lambda t: get(t, "trip_id"))[:TRIPS_PER_ROUTE]:
            kept.add(get(trip, "trip_id"))
    # Port Jervis trips are chosen SEPARATELY from the per-route quota, because they
    # have no route of their own: without this the per-route pick for 5 and 6 would
    # almost certainly be ordinary Main/Bergen trips and every west-of-Hudson
    # station would vanish from the fixture. WHICH ones is decided by coverage, in
    # select_port_jervis_trips, and handed in here already chosen.
    kept |= pj_keep
    # The realtime capture's own trips, so the pair joins.
    kept |= {trip_id for trip_id in extra_trip_ids if trip_id in calls}
    # Then top up for any mandated stop nothing kept covers yet.
    covered = {get(call, "stop_id") for tid in kept for call in calls.get(tid, [])}
    for stop_id in mandated_stops:
        if stop_id in covered:
            continue
        for trip_id in sorted(calls):
            if any(get(call, "stop_id") == stop_id for call in calls[trip_id]):
                kept.add(trip_id)
                covered |= {get(call, "stop_id") for call in calls[trip_id]}
                break
    return kept


def apply_trim(
    *,
    trips: list[dict],
    stops: list[dict],
    calls: dict[str, list[dict]],
    kept_trip_ids: set[str],
) -> tuple[list[dict], list[dict], list[dict]]:
    """(kept trips, kept stops, kept stop_times) for a set of trip ids.

    Stops follow the trips rather than being chosen: every stop a kept trip calls
    at is kept, which is what keeps the fixture referentially intact (no
    stop_times row can point at a stop that is not there). It is also why the
    west-of-Hudson stations survive at all, and why the guard on them has to be
    written against the kept TRIPS' calls rather than against an exclusivity
    computation that can come back empty.
    """
    kept_trips = sorted(
        (t for t in trips if get(t, "trip_id") in kept_trip_ids), key=lambda t: get(t, "trip_id")
    )
    kept_stop_ids = {
        get(call, "stop_id") for tid in kept_trip_ids for call in calls.get(tid, [])
    } - {""}
    kept_stops = [s for s in stops if get(s, "stop_id") in kept_stop_ids]
    kept_stop_times = [row for tid in sorted(kept_trip_ids) for row in calls.get(tid, [])]
    return kept_trips, kept_stops, kept_stop_times


# The six members a committed NJ Transit static fixture carries. agency, routes and
# calendar_dates go in WHOLE: calendar_dates in particular must stay whole because
# it is this feed's only schedule (there is no calendar.txt) and the service-date
# guard reads the maximum over the entire table, so any per-service trim could
# silently move the one number that guard is about. shapes.txt is deliberately not
# written at all: 15a does not parse it, and it is 10 MB of the 11.1 MB payload.
FIXTURE_MEMBERS = (
    "agency.txt",
    "routes.txt",
    "calendar_dates.txt",
    "stops.txt",
    "trips.txt",
    "stop_times.txt",
)


def write_fixture(out_dir, members: dict[str, tuple[list[str], list[dict]]]) -> None:
    """Write the fixture members as loose .txt files, one CSV each.

    Shared so the realtime generator's re-trim writes byte-identically to the
    static generator's first trim; two writers would eventually disagree about a
    quoting rule and the difference would surface as a golden diff nobody could
    explain.
    """
    import csv

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in FIXTURE_MEMBERS:
        if name not in members:
            continue
        fieldnames, rows = members[name]
        path = out_dir / name
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in fieldnames})
        print(f"  wrote {path.name} ({len(rows)} rows)")
