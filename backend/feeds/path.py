"""PATH realtime: the community GTFS-RT bridge feed decode and the synthetic
cross-poll identity matcher.

The bridge's own trip ids are UNSTABLE across upstream refreshes (verified: they
churn on every regeneration), so nothing keys on them; match_path_identities
mints stable ids from stable fields instead. That finding is kept here with the
code it constrains.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict

import httpx
from google.transit import gtfs_realtime_pb2

from feeds.shared import (
    _DROP_STOP_RELATIONSHIPS,
    _DROP_TRIP_RELATIONSHIPS,
    _header_timestamp,
    _stop_time,
    _trim_arrivals,
)

# ---- PATH (Port Authority Trans-Hudson) ----

# Community GTFS-RT bridge (jamespfennell/path-train-gtfs-realtime, sourced
# from the PANYNJ API). Unofficial with no SLA, so the URL is an env override:
# pointing at a self-hosted bridge is a config change, not a code change.
PATH_RT_URL = os.getenv("PATH_RT_URL", "https://path.transitdata.nyc/gtfsrt")


# Sent on every bridge request. The bridge is a community service; a
# descriptive User-Agent lets its maintainer see who is polling and reach out,
# instead of an anonymous default UA.
PATH_USER_AGENT = "nyc-transit-live (+https://github.com/rosenfieldben/nyc-transit-live)"


# PATH direction_id semantics, verified against static trips.txt across all 7
# routes via a headsign-by-direction tally (2026-07-05): 0 runs toward the New
# Jersey terminal (Newark, Hoboken, Journal Square, Harrison, Grove St), 1
# toward the New York terminal (33rd Street, World Trade Center). These labels
# are the arrivals bucket keys AND the placed train's direction field.
_PATH_DIRECTION = {0: "To New Jersey", 1: "To New York"}


def _decode_path_feed(
    raw: bytes, stops: dict[str, dict], now: float
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None, int]:
    """Decode the PATH bridge feed into (train placements, per-station
    arrivals, feed_timestamp, unresolved_entities).

    The bridge serves TripUpdate entities only (no VehiclePositions), each
    observed carrying EXACTLY ONE stop_time_update: the next arrival. The scan
    below does not assume that: it takes the FIRST resolvable, still-upcoming
    stop_time_update and ignores any later ones, so a bridge that starts
    emitting full stop lists neither breaks the decode nor changes its output
    shape. One consequence is deliberate: arrivals index only that one chosen
    stop per trip (there is nothing downstream to index today).

    Stop ids are the PARENT station ids from the 13a static stops table, so
    `stops` is app.state.path_stops and the stop_id IS the station id (no
    platform suffix, no child folding needed). An entity none of whose stop
    ids resolve there is skipped and COUNTED: unresolved_entities is returned
    so the caller can surface a persistent count (it means the static table
    and the bridge disagree, e.g. a station renumber, and those trains are
    silently absent from the map). The decoder itself does not log: surfacing
    belongs to the poll loop, which can rate the signal transition-only
    instead of once per decode (main._refresh_path). A known station whose
    stop was merely SKIPPED/NO_DATA is a resolvable id and is NOT counted.

    PLACEMENT mirrors the railroad conventions: drop canceled/deleted trips
    and skipped/no-data stops, keep a just-passed grace of 60s, fall back to
    the first resolvable stop when no stop carries a time (next_time null),
    and drop a trip whose only timed stops are all past. There is no
    not-yet-started filter: the bridge emits only live next-arrival
    predictions (no start_date/start_time to derive one from), so the subway
    phantom problem cannot arise. prev_* is ALWAYS null at the decode level:
    the carry-forward anchor memory the other systems use keys on trip ids,
    and PATH bridge trip ids do not survive an upstream refresh (see
    path_static's module docstring), so an anchor keyed on them would
    silently mismatch. match_path_identities (13d) owns anchors instead,
    filling them only after a synthetic identity match.

    ARRIVALS are bucketed by direction_id ("To New York" / "To New Jersey",
    see _PATH_DIRECTION), with "Trains" as the residual for a direction-less
    trip, matching the railroad bucket discipline (keys present only when
    populated, sorted soonest-first, capped at ARRIVALS_PER_DIRECTION). Rows
    are {route_id, arrival} ONLY: the bridge's trip hash is unstable across
    upstream refreshes and display-poor, so since the 13d cleanup it appears
    in no served payload at all (the trains side dropped it for the matcher's
    synthetic id; the shape parity with railroad rows was not worth keeping
    the one remaining leak).

    feed_timestamp is the bridge's WRITE time, a fair "is the bridge alive"
    signal. It advances even when the entity content is unchanged, because the
    bridge regenerates (~15s) faster than the upstream refreshes: consecutive
    polls with identical content are NORMAL for PATH, never a stuck-feed
    signal, so there is deliberately no content-unchanged staleness heuristic
    here or anywhere downstream.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

    trains: list[dict] = []
    arrivals: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    unresolved_entities = 0
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            continue  # canceled/deleted trip: drop from both placement and arrivals
        trip_id = tu.trip.trip_id or f"PATH:{entity.id}"
        route_id = tu.trip.route_id or None
        direction = (
            _PATH_DIRECTION.get(tu.trip.direction_id) if tu.trip.HasField("direction_id") else None
        )

        # One scan picks the single stop both outputs use: the first
        # resolvable, still-upcoming stop_time_update (today the only one).
        chosen = None
        chosen_time = None
        first_resolvable = None
        saw_timed = False
        saw_any_stop = False
        saw_known_station = False  # a stop_id in `stops`, even if later skipped
        for stu in tu.stop_time_update:
            saw_any_stop = True
            if not stu.stop_id or stu.stop_id not in stops:
                continue  # not a parent station id we know; try the next one
            saw_known_station = True
            if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
                continue  # skipped / no-data stop: no real prediction
            if first_resolvable is None:
                first_resolvable = stu
            t = _stop_time(stu)
            if t is None:
                continue
            saw_timed = True
            if t >= now - 60:  # same just-passed grace as the other systems
                chosen = stu
                chosen_time = t
                break
        if chosen is None and not saw_timed:
            chosen = first_resolvable  # no-times fallback: next_time stays null
        if chosen is None:
            # Count ONLY the static-vs-bridge disagreement the debug log names:
            # an entity that had stops but none resolved to a known station. A
            # known station that was merely SKIPPED/NO_DATA (a normal service
            # suspension) is a resolvable id, so it must NOT inflate this count,
            # or the log would misdirect an operator toward a nonexistent id
            # mismatch.
            if saw_any_stop and not saw_known_station:
                unresolved_entities += 1
            continue  # unresolvable, or its only timed stops are all past

        stop = stops[chosen.stop_id]
        if chosen_time is not None:
            bucket = direction or "Trains"
            arrivals[chosen.stop_id][bucket].append(
                {"route_id": route_id, "arrival": float(chosen_time)}
            )
        trains.append(
            {
                "trip_id": trip_id,
                "route_id": route_id,
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "stop_id": chosen.stop_id,
                "stop_name": stop["name"],
                "direction": direction,
                "prev_lat": None,  # no carry-forward in 13b (unstable trip ids)
                "prev_lon": None,
                "prev_time": None,
                "next_time": float(chosen_time) if chosen_time is not None else None,
            }
        )

    return trains, _trim_arrivals(arrivals), _header_timestamp(feed), unresolved_entities


async def fetch_path_trains(
    client: httpx.AsyncClient, path_stops: dict[str, dict]
) -> tuple[list[dict], dict[str, dict[str, list[dict]]], float | None, int]:
    """Fetch the PATH bridge feed; return (trains, arrivals_by_stop,
    feed_timestamp, unresolved_entities).

    Single feed, so unlike fetch_subway_trains / fetch_railroad_trains there
    is no partial-failure aggregation: an HTTP error or undecodable body
    propagates for the caller (main._refresh_path) to record, the same way the
    single-feed bus fetch behaves. The caller owns the client and must only
    call this once path_stops is populated (placement and arrivals both
    resolve parent station ids through it). unresolved_entities is the count
    of entities dropped because no stop id resolved to a known parent station
    (see _decode_path_feed): the caller surfaces it, since a persistent count
    means trains are silently missing from the map. See _decode_path_feed for
    the duplicate-generation and unstable-trip-id caveats too.
    """
    now = time.time()
    resp = await client.get(PATH_RT_URL, headers={"User-Agent": PATH_USER_AGENT})
    resp.raise_for_status()
    return _decode_path_feed(resp.content, path_stops, now)


# Same-stop identity window (13d). The live probe (2026-07-07, 40 polls, 10
# upstream generations, 238 train-transitions) measured at most 8s of
# prediction drift across an upstream refresh, and a minimum headway between
# consecutive arrivals at the same (route, direction, stop) of 2256s off-peak;
# PATH's peak headways are roughly 4 minutes (240s). 60s therefore sits two
# orders of magnitude under the off-peak headway and still 3x under peak,
# while being 7x the worst observed drift: wide enough to never drop a real
# carry, narrow enough that two distinct trains cannot fall inside one window.
PATH_MATCH_TOLERANCE_S = 60.0


# An identity absent from this many consecutive matched generations is
# dropped. Terminal arrivals simply vanish from the bridge, so most expiries
# are one poll behind reality; 3 generations (~60s of polls) also rides out a
# single-poll bridge blip without letting a stale identity linger long enough
# to same-stop match the NEXT train at that stop (headways above dwarf it).
PATH_IDENTITY_EXPIRY_GENERATIONS = 3


# The matcher works from the trains' rider-facing direction labels (the only
# direction field the decode output carries), but the static station order is
# keyed by GTFS direction_id; this inverts _PATH_DIRECTION to bridge the two.
_PATH_DIRECTION_ID = {label: str(did) for did, label in _PATH_DIRECTION.items()}


def new_path_identity_state(epoch: str) -> dict:
    """Fresh matcher state. `epoch` prefixes every minted id so ids from two
    process lifetimes can never collide: a restarted backend re-mints from
    seq 1, and without the prefix a browser holding markers keyed on the old
    process's ids would silently splice them onto unrelated trains."""
    return {"epoch": epoch, "seq": 0, "identities": {}}


def _path_time_delta(a: float | None, b: float | None) -> float:
    # Two untimed placements at the same stop are the same train re-served
    # (the no-times fallback), so they compare equal; a timed vs untimed pair
    # is incomparable and never matches.
    if a is None and b is None:
        return 0.0
    if a is None or b is None:
        return float("inf")
    return abs(a - b)


def match_path_identities(
    state: dict, trains: list[dict], station_order: dict[tuple[str, str | None], list[str]]
) -> tuple[list[dict], dict]:
    """Assign stable synthetic identities to one decoded PATH generation.

    Returns (served_trains, new_state). served_trains is what /api/path
    serves: each train rebuilt with a stable `id` and WITHOUT the bridge's
    trip_id (unstable by construction and meaningless to riders; the field
    set is constructed explicitly so a future decode field can never leak
    into the payload unreviewed). new_state replaces `state` for the next
    poll. Pure and clock-free: no wall clock, no randomness, so the goldens
    drive it with committed captures and fixed inputs.

    WHY synthetic identity at all: bridge trip ids churn 100% when the
    upstream refreshes (path_static's module docstring), so the frontend
    cannot diff markers or keep popups alive across polls without an
    identity the backend derives from stable fields instead. The live probe
    this design is built on (2026-07-07, 238 train-transitions over 10
    upstream generations) measured branch 1 below taking 98.7% of traffic
    with zero ambiguity; the 1.3% remainder were all advances (branch 2).

    Branch 1, SAME-STOP: a new train matches a known identity at the same
    (route_id, direction, stop_id) when their arrival predictions differ by
    at most PATH_MATCH_TOLERANCE_S, and the pairing is unique BOTH ways
    (exactly one candidate in the new train's window, and that candidate in
    exactly one new train's window). Any tie resets identity instead of
    guessing: a wrong carry glides a marker along the wrong journey, while a
    reset merely re-places a marker at its station, so ambiguity always
    resolves to the cheap failure. A matched train KEEPS the identity's
    existing prev anchors (it is still the same journey; this is also what
    makes a duplicate re-served generation a strict no-op at delta zero) and
    refreshes everything else from the new decode.

    Branch 2, ADVANCE: a still-unmatched new train at stop Y carries the
    identity of a train that was present in the previous generation, is now
    gone, and sat at Y's immediate predecessor X in the static station order
    for the same (route, direction), again only when the pairing is unique
    both ways. Its prev anchor becomes X (the identity's last placement and
    predicted arrival time there), which is exactly the anchor pair the
    frontend glide interpolates against. This is the only heuristic branch:
    uniqueness is what keeps two trains advancing in lockstep from swapping
    identities, because each new train sees exactly one vanished predecessor
    at its own X (X to Y, and W to X, are different stop pairs). One residual
    it cannot remove: a train TERMINATING at X while an unrelated train
    appears at Y in the same generation is observationally identical to an
    advance, so the appearing train inherits the ended identity and one
    cosmetic wrong glide (pinned in tests as accepted behavior; the next
    advance replaces the anchor).

    Otherwise a fresh identity is minted (epoch-prefixed sequence number,
    never derived from bridge hashes) with null anchors: the train appears
    placed at its station, the 13c behavior. Identities absent for
    PATH_IDENTITY_EXPIRY_GENERATIONS matched generations are dropped;
    terminal arrivals simply vanish.
    """
    identities: dict[str, dict] = state["identities"]

    def slot(train_or_snap: dict) -> tuple:
        return (train_or_snap["route_id"], train_or_snap["direction"], train_or_snap["stop_id"])

    pool_by_slot: dict[tuple, list[str]] = defaultdict(list)
    for sid, snap in identities.items():
        pool_by_slot[slot(snap)].append(sid)

    assigned: dict[int, str] = {}  # train index -> identity id
    advanced: set[int] = set()  # train indexes matched via branch 2
    matched_sids: set[str] = set()

    # Branch 1: same-slot bilateral-unique nearest-arrival match.
    new_by_slot: dict[tuple, list[int]] = defaultdict(list)
    for i, train in enumerate(trains):
        new_by_slot[slot(train)].append(i)
    for key, idxs in new_by_slot.items():
        cands = pool_by_slot.get(key)
        if not cands:
            continue
        in_window: dict[int, list[str]] = {
            i: [
                sid
                for sid in cands
                if _path_time_delta(trains[i]["next_time"], identities[sid]["next_time"])
                <= PATH_MATCH_TOLERANCE_S
            ]
            for i in idxs
        }
        claims: dict[str, list[int]] = defaultdict(list)
        for i, sids in in_window.items():
            for sid in sids:
                claims[sid].append(i)
        for i, sids in in_window.items():
            if len(sids) == 1 and len(claims[sids[0]]) == 1:
                assigned[i] = sids[0]
                matched_sids.add(sids[0])

    # Branch 2: advance match against identities that were present last
    # generation and vanished this one. Identities already missing a
    # generation or more are excluded: a train mid-system always appears in
    # the very next generation at its next stop, so an older absence is a
    # terminal arrival or a data gap, not an advance in progress.
    vanished = {
        sid for sid, snap in identities.items() if snap["missing"] == 0 and sid not in matched_sids
    }
    adv_window: dict[int, list[str]] = {}
    adv_claims: dict[str, list[int]] = defaultdict(list)
    for i, train in enumerate(trains):
        if i in assigned:
            continue
        direction_id = _PATH_DIRECTION_ID.get(train["direction"] or "")
        if direction_id is None or not train["route_id"]:
            continue
        order = station_order.get((train["route_id"], direction_id))
        if not order or train["stop_id"] not in order:
            continue
        position = order.index(train["stop_id"])
        if position == 0:
            continue  # first station of the run has no predecessor to advance from
        predecessor = order[position - 1]
        cands = [
            sid
            for sid in sorted(vanished)
            if identities[sid]["route_id"] == train["route_id"]
            and identities[sid]["direction"] == train["direction"]
            and identities[sid]["stop_id"] == predecessor
        ]
        if cands:
            adv_window[i] = cands
            for sid in cands:
                adv_claims[sid].append(i)
    for i, sids in adv_window.items():
        if len(sids) == 1 and len(adv_claims[sids[0]]) == 1:
            assigned[i] = sids[0]
            matched_sids.add(sids[0])
            advanced.add(i)

    # Rebuild the served list and the next state.
    seq = state["seq"]
    next_identities: dict[str, dict] = {}
    served: list[dict] = []
    for i, train in enumerate(trains):
        matched = assigned.get(i)
        if matched is None:
            seq += 1
            train_id = f"{state['epoch']}-{seq}"
            prev = (None, None, None)
        elif i in advanced:
            train_id = matched
            old = identities[train_id]
            prev = (old["latitude"], old["longitude"], old["next_time"])
        else:
            train_id = matched
            old = identities[train_id]
            prev = (old["prev_lat"], old["prev_lon"], old["prev_time"])
        served.append(
            {
                "id": train_id,
                "route_id": train["route_id"],
                "latitude": train["latitude"],
                "longitude": train["longitude"],
                "stop_id": train["stop_id"],
                "stop_name": train["stop_name"],
                "direction": train["direction"],
                "prev_lat": prev[0],
                "prev_lon": prev[1],
                "prev_time": prev[2],
                "next_time": train["next_time"],
            }
        )
        next_identities[train_id] = {
            "route_id": train["route_id"],
            "direction": train["direction"],
            "stop_id": train["stop_id"],
            "next_time": train["next_time"],
            "latitude": train["latitude"],
            "longitude": train["longitude"],
            "prev_lat": prev[0],
            "prev_lon": prev[1],
            "prev_time": prev[2],
            "missing": 0,
        }
    for sid, snap in identities.items():
        if sid in next_identities:
            continue
        if snap["missing"] + 1 >= PATH_IDENTITY_EXPIRY_GENERATIONS:
            continue  # expired: gone long enough that no branch may claim it
        next_identities[sid] = {**snap, "missing": snap["missing"] + 1}
    return served, {"epoch": state["epoch"], "seq": seq, "identities": next_identities}
