"""NJ Transit Rail realtime (15b): the TripUpdates decode into placed trains and
a per-stop arrivals index.

Built on what 15a already put in place, by name: njt_auth.njt_post is the one
door (its token cache is process-wide and single-flight precisely so these
pollers share the static loader's token), and njt_static publishes
app.state.njt_trips (trip_id -> route_id, headsign, short_name) and
app.state.njt_stops (stop_id -> id, name, lat, lon). The 2026-08-05 probes
measured 100% trip_id persistence across 633 survivor observations and
entity.id == train number == trip_short_name at 745/745, so THIS DECODER IS A
JOIN, NOT A MATCHER. PATH synthesizes identity because its bridge churns ids;
NJ Transit does not, and inventing a matcher here would add a failure mode the
feed does not have.

THE JOIN IS CHECKED PER TRAIN, AND A TRAIN THAT CONTRADICTS IT IS BELIEVED. The trip
ids are stable from poll to poll and NOT from publication to publication: NJ Transit
numbers its trips 1..N afresh in each one, and this process keeps the publication it
loaded at start. From 2026-11-12 to 2026-11-22 production held one that NJ Transit had
replaced at least three times, and on the 22nd every joined train it served wore another
train's destination and number. So where entity.id and the schedule's train number
disagree, the row takes nothing from the schedule (see _identity).

THE DECODER LAW, written by the rush probe and treated as ground truth. Each
rule below exists because the probe watched it happen, and each has a hermetic
test with a killing mutation:

  1. READ schedule_relationship AT BOTH LEVELS. A trip-level CANCELED trip STAYS
     IN THE FEED, still joins the static, and marks every stop SKIPPED while
     KEEPING full arrival and departure times. 8% of peak Penn stop_time_updates
     were phantoms of this shape, including a PARTIAL cancellation (normal
     through Newark, then Secaucus and Penn dropped with a plausible delay still
     attached). A decoder that reads only the stop level, or only the trip level,
     serves a train that is not running to a rider standing on the platform.
     CANCELED drops the trip from arrivals AND placement, entirely.
  2. STOP-LEVEL SKIPPED HAS TWO OBSERVED VARIANTS: with times (238 seen) and bare
     without times (35 seen). Both drop the stop. The named victim is a train
     skipping Penn while headsigned FOR Penn, which is exactly the row a rider
     would act on.
  3. ADDED IS REAL, AND IT CARRIES AN EMPTY trip_id. Both 2026-08-05 probes saw
     none, and this rule used to say so; a later live capture carried 36 of them
     out of 164 trip_updates, every one with trip_id "". It joins no static, takes
     its destination from its own last stop (_destination), and MUST BE KEYED
     ON entity.id, because 36 extras sharing the empty string collapse into one
     train and extras run on exactly the disrupted nights a rider is watching the
     map. See _identity's fallback chain, and note what the chain cannot see by
     itself: two extras claiming ONE entity.id collapse just as completely and,
     unlike an extra with no identity at all, do it silently. The chain ends in a
     collision check for that reason.
  4. delay, absolute time AND scheduled_time are all present. ABSOLUTE TIME IS
     AUTHORITATIVE; delay is carried through for display and as a cross-check,
     never as the source of a time.
  5. stop_sequence IS SPARSE AND OPAQUE: an ordering key, never an index.
  6. EMPTY-SUCCESS IS REAL (a 13-byte valid header overnight). parse_feed rule 4
     accepts it, and zero trains is a state, not a failure.

VEHICLE POSITIONS ARE NOT FETCHED, NOT PARSED, AND NOT MODELLED. The reasoning
and its numbers live at the poller registry in pollers.py, where the next person
adding a feed will actually be standing; it is repeated nowhere so it cannot
drift into two versions.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

import env_seams
import njt_auth
from feeds.shared import (
    _DROP_STOP_RELATIONSHIPS,
    _DROP_TRIP_RELATIONSHIPS,
    _STOP_SR,
    ARRIVALS_PER_DIRECTION,
    MAX_FUTURE_FIRST_STOP_S,
    _header_timestamp,
    logger,
    parse_feed,
)

# njt_auth is imported at module level and njt_static deliberately is NOT. The
# asymmetry is a real import cycle rather than a preference: njt_static reads
# feeds.shared, and importing any feeds submodule runs feeds/__init__.py first,
# which imports this module, which would then re-enter a half-initialised
# njt_static. njt_auth imports only env_seams, so it is safe from here.

# The system tag every NJT entity carries. Its own citizen, never folded into
# "railroad": different upstream, different auth, different failure modes, and
# C2's whole lesson is that systems degrade independently.
SYSTEM = "njt"

# Verified 2026-08-05: POST multipart/form-data with the token as a form field,
# exactly like the static archive. Overridable (C6) and used whole, so the
# contract tier can point them at its simulator; the seam changes WIRING, never
# behavior, so the poller still mints and POSTs against the sim.
NJT_TU_URL = env_seams.url("NJT_TU_URL", "https://raildata.njtransit.com/api/GTFSRT/getTripUpdates")
NJT_ALERTS_URL = env_seams.url(
    "NJT_ALERTS_URL", "https://raildata.njtransit.com/api/GTFSRT/getAlerts"
)

# THE FRESHNESS BUDGET, DERIVED. These are the numbers behind every NJ Transit
# staleness threshold in the app and the monitor; the thresholds themselves live
# where they are applied (cache.FEED_STALE_AFTER_S for /api/status and /healthz,
# NJT_HEADER_LAG_* in scripts/contract_monitor.py for the njt-realtime check) and
# each points back here rather than re-deriving.
#
# WHAT THE 2026-08-05 PROBES MEASURED:
#
#   - GENERATION EVERY ~11.8s. The header timestamp advances on roughly that
#     period, so the feed itself is never more than about 12s behind its own
#     source of truth.
#   - HEADER LAG 9s TO 23s AT PEAK, measured as (our receive time - header
#     timestamp) across the rush window. 23s is the worst observed, not a
#     tail estimate.
#   - "THE OVERNIGHT NUMBERS ARE OPTIMISTIC BY ROUGHLY 2x" (the probe's own
#     phrase, quoted rather than paraphrased because it is the single most
#     load-bearing caveat here). Overnight the feed is small, the publisher is
#     idle, and lag collapses. ANY THRESHOLD DERIVED FROM AN OVERNIGHT SAMPLE
#     WOULD BE ABOUT HALF OF WHAT PEAK ACTUALLY NEEDS AND WOULD PAGE EVERY
#     WEEKDAY MORNING. Every band below is therefore derived from the PEAK
#     numbers, and a future re-probe that lands overnight must double its
#     figures before comparing them against these.
#
# WHAT THAT MAKES THE WORST HONEST AGE OF A SERVED NJT TRAIN. Two numbers, not
# one, because the poll gap is not the poll interval:
#
#   TYPICAL, and what an operator sees essentially always:
#
#       23s   worst observed peak header lag
#     + 22s   the poll gap on a healthy cycle (POLL_INTERVAL_S is 20s, and
#             _poll_feeds sleeps it AFTER its TaskGroup joins, so the real
#             fetch-to-fetch period is 20s plus however long the cycle took;
#             a healthy six-source cycle is low single-digit seconds)
#     -----
#       45s   against a 90s budget: 2x headroom
#
#   WORST CASE, which is a pathological cycle rather than a busy one:
#
#       23s   worst observed peak header lag
#     + 65s   20s interval plus a full REFRESH_DEADLINE_S (45s), the hard ceiling
#             _bounded_refresh puts on ONE wedged refresher before it gives up
#     -----
#       88s   against 90s: essentially no headroom left
#
# THE SECOND NUMBER IS NOT A BUG AND IS WORTH UNDERSTANDING BEFORE CHANGING
# ANYTHING. Crossing 90s does not serve a wrong train; it makes /api/status and
# /healthz report the NJT feed STALE, which during a cycle that took 45 seconds to
# finish is a true statement. The failure mode at this edge is a noisy staleness
# flag, never a ghost, so the right response to it firing is to look at why a
# refresh took 45 seconds rather than to raise the threshold.
#
# NJ Transit is the TIGHTEST-MARGIN system under that shared 90s budget (the MTA
# feeds regenerate every ~30s but publish with far less lag), so if 90 ever moves,
# this is the derivation that has to be re-checked first.
#
# THE POLL CADENCE IS THE SHARED ONE (pollers.POLL_INTERVAL_S, 20s) rather than a
# private NJT cadence, and NJT alerts likewise ride the shared 60s alert loop.
# Both are FASTER than the 15b plan's 30s/120s. Kept deliberately: a private
# cadence would be the only per-source interval in a cycle that has exactly one,
# and for the alert loop it is not merely clutter but unrepresentable, because
# merge_alert_generations knows exactly two states per system, fresh and failed.
# A feed skipped for cadence would have to be reported as one or the other, so
# throttling NJT alerts alone would either drop its alerts from the served index
# or mark it degraded on every skipped poll. At 20s/60s this is ~5,800 requests a
# day, comfortably inside the data cap, and 2 token mints from the 12h
# MAX_TOKEN_AGE_S ceiling, which is 2 of the 10 mints NJ Transit allows this account
# per Eastern day (observed 2026-09-02, see njt_auth.DAILY_MINT_LIMIT), beside the
# contract monitor's 4 out of the same 10. If that ever proves too warm,
# POLL_INTERVAL_S and
# ALERT_POLL_INTERVAL_S are already C6 seams and moving them is a config change,
# not a code one.

# Upcoming departures kept per stop. FLAT rather than bucketed by direction or
# route, unlike the subway (platform direction) and the ferry (route name): every
# row already carries its route and headsign, and a departure board reads
# chronologically. The cap is the shared one so a busy stop's index stays the same
# size as every other system's bucket; if the 15c boards need a deeper list, that
# is a one-line change with a reason rather than a silent difference.
ARRIVALS_PER_STOP = ARRIVALS_PER_DIRECTION

# How long after its last listed departure a trip stays placeable. The feed keeps
# a trip briefly past its terminal, and dropping it the instant the clock passes
# would make an arriving train blink out while it is still standing at the
# platform.
_TERMINAL_GRACE_S = 60

# A stop whose time has just passed is still the right answer for that stop (a
# train dwelling or just departed), the same grace every other decoder applies.
_JUST_PASSED_GRACE_S = 60


def _time_and_delay(event) -> tuple[float | None, int | None]:
    """(absolute POSIX time, delay seconds) from a StopTimeEvent, or (None, None).

    ABSOLUTE TIME IS AUTHORITATIVE (decoder law 4). The probe found delay,
    absolute time and scheduled_time all populated, so a decoder could derive the
    time from schedule+delay instead; it does not, because that would make our
    answer disagree with the upstream's own on any row where the three drift, and
    the upstream's absolute time is the one a rider's departure board shows.
    delay rides along for display and cross-checking only.
    """
    if event is None:
        return None, None
    when = float(event.time) if event.HasField("time") and event.time else None
    delay = int(event.delay) if event.HasField("delay") else None
    return when, delay


def _call(stu) -> dict:
    """One stop_time_update as {stop_id, arrival, departure, delay, seq}.

    Both times are kept SEPARATELY, unlike feeds.shared._stop_time which folds
    them to the latest. That helper answers "is this stop still upcoming", which
    is all the next-station decoders need; NJT placement needs the dwell WINDOW
    (arrival <= now < departure), so it needs both edges and cannot use it.
    """
    arrival, arr_delay = _time_and_delay(stu.arrival if stu.HasField("arrival") else None)
    departure, dep_delay = _time_and_delay(stu.departure if stu.HasField("departure") else None)
    return {
        "stop_id": stu.stop_id,
        "arrival": arrival,
        "departure": departure,
        # The departure delay is the one a waiting rider feels; fall back to the
        # arrival's when the feed gives only that.
        "delay": dep_delay if dep_delay is not None else arr_delay,
        "seq": stu.stop_sequence if stu.HasField("stop_sequence") else None,
    }


def _ordered_calls(tu, stops: dict[str, dict]) -> list[dict]:
    """The trip's resolvable, non-skipped calls in travel order.

    Filters, in this order:
      - a stop_id the static does not carry (it cannot be placed or named);
      - a SKIPPED or NO_DATA stop, which covers BOTH observed SKIPPED variants
        (decoder law 2) because the relationship is read before any time is,
        so the bare no-times form drops on exactly the same line as the
        with-times form. That is the whole reason the check sits first.

    ORDERING (decoder law 5): stop_sequence is used only when EVERY surviving
    call carries one, and never as an index into anything. The probe found it
    sparse and opaque, so a partial sort would interleave numbered and unnumbered
    calls arbitrarily; GTFS-RT requires stop_time_updates in stop order anyway, so
    feed order is the honest fallback rather than a guess.
    """
    calls: list[dict] = []
    for stu in tu.stop_time_update:
        if not stu.stop_id or stu.stop_id not in stops:
            continue
        if stu.schedule_relationship in _DROP_STOP_RELATIONSHIPS:
            continue  # SKIPPED (either variant) or NO_DATA: no real prediction
        calls.append(_call(stu))
    if calls and all(call["seq"] is not None for call in calls):
        calls.sort(key=lambda call: call["seq"])
    return calls


def _first_time(call: dict) -> float | None:
    """The EARLIEST time on a call, which is the one a "has the train got here yet"
    test has to use.

    The mirror of _still_upcoming, and both exist because the two questions want
    opposite ends of a dwell: "is this stop still ahead" reads the departure, and
    "has the train reached this stop" reads the arrival. Returns None only when the
    call carries neither, which _place treats as a gap in the walk.
    """
    times = [t for t in (call["arrival"], call["departure"]) if t is not None]
    return min(times) if times else None


def _still_upcoming(call: dict) -> float | None:
    """The LATEST time on a call, which is the one an "is this stop still ahead"
    test has to use.

    THE HOUSE RULE, and it is feeds.shared._stop_time's rule applied to this
    decoder's already-parsed calls rather than to a raw stop_time_update. That
    docstring names the exact hazard: "a train dwelling or held at a station has
    arrival in the past but departure in the future, and must not be treated as
    past that stop".

    Reading arrival first was wrong here for a sharper reason than "one station
    ahead". A train standing at Penn Station that arrived five minutes ago and
    leaves in ten has an arrival well past the just-passed grace, so it dropped
    off Penn's departure board entirely while _place simultaneously drew it
    standing at that platform. The map and the board disagreed about the one train
    a rider could actually catch, and stop 109 is the station the whole trap
    matrix is written about.

    Sorting uses this too (see _trim_njt_arrivals): among surviving rows a train
    that arrived thirty seconds ago and departs in eight minutes must not sort
    ahead of one departing in one minute.
    """
    times = [t for t in (call["arrival"], call["departure"]) if t is not None]
    return max(times) if times else None


def _interpolate(
    prev_stop: dict, next_stop: dict, departed_at: float, arrives_at: float, now: float
) -> tuple[float, float]:
    """A point on the straight segment between two stops, by time fraction.

    STRAIGHT, AND THAT IS THIS PHASE'S ACCEPTED LIMIT. Following the route shape
    needs shapes.txt, which 15a deliberately left unparsed (10 MB of the 11.1 MB
    payload) pending 15c's line-drawing decision; until then a straight segment is
    an honest approximation that no consumer can mistake for a GPS fix, because
    every NJT train is tagged as schedule-derived.

    WRITTEN HERE RATHER THAN IMPORTED, because there is nothing to import: the
    subway and railroad decoders place a train AT its next station and emit
    prev_*/next_time anchors for the BROWSER to animate between polls. NJT needs a
    position between stations on the server, since its dwell window is what
    distinguishes standing-at-a-platform from running, so the arithmetic lives
    here. The anchors are emitted too, so 15c can still glide between polls the
    way every other system does.

    A zero or inverted window (arrives_at <= departed_at) yields the previous
    stop rather than dividing by zero or extrapolating past the segment: a feed
    that says a train departs and arrives at the same instant has told us nothing
    about where it is, and the stop it just left is the last thing known true.
    """
    span = arrives_at - departed_at
    if span <= 0:
        return prev_stop["lat"], prev_stop["lon"]
    fraction = (now - departed_at) / span
    # Clamped so a clock skew of a few seconds cannot place a train beyond either
    # endpoint of the segment it is on.
    fraction = min(1.0, max(0.0, fraction))
    return (
        prev_stop["lat"] + (next_stop["lat"] - prev_stop["lat"]) * fraction,
        prev_stop["lon"] + (next_stop["lon"] - prev_stop["lon"]) * fraction,
    )


def _place(calls: list[dict], stops: dict[str, dict], now: float) -> dict | None:
    """Where this trip is right now, or None when it cannot be placed.

    The four cases, in the order they are tested:

      1. DWELLING: some call has arrival <= now < departure. The train is standing
         at that platform. Both times were natively present on 103 of 103 probed
         calls, so this is the common case rather than a corner.
      2. NOT YET AT ITS FIRST LISTED STOP: placed AT that stop, approaching. Capped
         by MAX_FUTURE_FIRST_STOP_S (the shared constant the subway uses for the
         same judgement) so a trip listed hours ahead is a phantom, not a train
         parked at its origin all afternoon.

         THE ONE ASSUMPTION IN THIS FUNCTION, recorded because it is load-bearing
         and the probes did not measure it directly: THIS FEED IS TAKEN TO RETAIN
         ALREADY-PASSED STOPS. The 15b decisions block assumes it too (it defines
         placement as interpolating "between previous departure and next arrival",
         which needs a previous stop to exist), and the probe's partial
         cancellation is consistent with it, listing the whole trip including the
         normally-served leg. Under that assumption a RUNNING train always has a
         passed call behind it and so lands in case 1 or 3, never here, which is
         what makes this cap safe: it can only ever see trips that genuinely have
         not departed.

         IF THAT ASSUMPTION IS WRONG and NJ Transit prunes passed stops the way
         the railroad feeds do, every running train would arrive here instead and
         this cap would drop nearly all of them. That failure is loud rather than
         subtle: the placed-train count collapses to near zero while arrivals stay
         full, which is exactly what the monitor's entity-sanity band watches for.
         The fix would then be the railroad's: drop the cap and place at the next
         station (see _decode_railroad_feed, which documents declining this same
         cap for that reason). Left as the assumption rather than hedged both ways,
         because a decoder that tried to satisfy both would place phantoms under
         one of them.
      3. BETWEEN TWO STOPS: interpolated along the segment from the last departure
         to the next arrival.
      4. PAST ITS LAST DEPARTURE: the trip is done. Dropped after a short grace so
         a train does not blink out while still standing at its terminal.

    A call missing one of its two times degrades rather than disqualifying the
    trip: the other time stands in, which keeps a partially-timed trip placeable
    instead of vanishing it.

    A call missing BOTH times is skipped for cases 2 to 4 rather than breaking the
    walk, and that is a correctness fix rather than tidiness. The bare SKIPPED
    variant already proves this producer emits timeless stop_time_updates (35 per
    peak poll), and a timeless call in a LIVE trip used to do two things, both
    wrong. A timeless FIRST call made case 2's first_time None, so the
    MAX_FUTURE_FIRST_STOP_S phantom cap was skipped entirely and the trip fell
    through to case 4, which placed it standing at its terminal up to an hour and a
    half before it got there. A timeless MIDDLE call broke case 3's consecutive
    pairing on both sides, with the same result: a train between Newark and Penn
    teleported to Penn's platform. Pairing over the TIMED calls interpolates across
    the gap instead, which is the straight-line approximation this function already
    makes between any two stops.
    """
    if not calls:
        return None

    # Case 1: dwelling. Checked first because it is both the commonest state and
    # the only one that can name a stop_id the rider is standing at.
    for call in calls:
        arrival, departure = call["arrival"], call["departure"]
        if arrival is not None and departure is not None and arrival <= now < departure:
            stop = stops[call["stop_id"]]
            return {
                "latitude": stop["lat"],
                "longitude": stop["lon"],
                "status": "at-station",
                # PROVENANCE IS NOT `status`. That one is a MOTION phase and all three
                # of its values sit on a position we derived; this says HOW. Dwelling
                # puts the train at a stop's own coordinates, so: placed.
                "provenance": "placed",
                "stop_id": call["stop_id"],
                "stop_name": stop["name"],
                "delay": call["delay"],
                "prev_lat": None,
                "prev_lon": None,
                "prev_time": None,
                "next_time": departure,
            }

    # Cases 2 to 4 walk only the calls that carry a time, for the reason the
    # docstring gives: a timeless call is not a position, and treating it as a gap
    # in the walk is what stops it becoming a phantom.
    timed = [call for call in calls if _first_time(call) is not None]
    if not timed:
        return None

    # Case 2: before the first TIMED call, and `timed[0]` rather than `calls[0]` is
    # load-bearing: a timeless leading call made first_time None, which skipped this
    # branch and its MAX_FUTURE_FIRST_STOP_S cap entirely. See case 4, which is the
    # other half of the pair that now catches that. `or` rather than an explicit
    # None check below: a first call with only a departure is still a time this can
    # be judged against.
    first = timed[0]
    first_time = _first_time(first)
    if first_time is not None and now < first_time:
        if first_time > now + MAX_FUTURE_FIRST_STOP_S:
            return None  # listed far ahead of its own origin: not a running train
        stop = stops[first["stop_id"]]
        return {
            "latitude": stop["lat"],
            "longitude": stop["lon"],
            "status": "approaching",
            "provenance": "placed",  # drawn at the first listed stop's coordinates
            "stop_id": first["stop_id"],
            "stop_name": stop["name"],
            "delay": first["delay"],
            "prev_lat": None,
            "prev_lon": None,
            "prev_time": None,
            "next_time": first_time,
        }

    # Case 3: between two calls. Walk pairs and take the first segment that
    # straddles `now`.
    for prev, nxt in zip(timed, timed[1:]):
        left = prev["departure"] if prev["departure"] is not None else prev["arrival"]
        right = nxt["arrival"] if nxt["arrival"] is not None else nxt["departure"]
        if left is None or right is None:
            continue
        if left <= now < right:
            prev_stop, next_stop = stops[prev["stop_id"]], stops[nxt["stop_id"]]
            lat, lon = _interpolate(prev_stop, next_stop, left, right, now)
            return {
                "latitude": lat,
                "longitude": lon,
                "status": "in-transit",
                # The one NJ Transit branch that is not `placed`: _interpolate put this
                # train BETWEEN two stops, so the coordinates are computed rather than
                # read off the static index.
                "provenance": "estimated",
                "stop_id": nxt["stop_id"],  # the stop it is heading for
                "stop_name": next_stop["name"],
                "delay": nxt["delay"],
                "prev_lat": prev_stop["lat"],
                "prev_lon": prev_stop["lon"],
                "prev_time": left,
                "next_time": right,
            }

    # Case 4: PAST the last call, within the terminal grace.
    #
    # `last_time <= now` is the half this branch used to be missing, and its
    # absence is how the phantom got out: case 4 was not "the trip is done" but an
    # unconditional fallthrough for everything cases 1 to 3 declined, so a trip
    # whose calls could not be walked was placed standing at its FINAL stop however
    # far ahead that stop was, at-station, with a next_time an hour out. That is
    # exactly what MAX_FUTURE_FIRST_STOP_S exists to refuse, arriving through the
    # one branch that never consulted it.
    #
    # IT IS ONE OF TWO GUARDS AGAINST THAT PHANTOM, AND EITHER ALONE SUFFICES. The
    # other is case 2 judging `timed[0]` rather than `calls[0]`, which is what
    # guarantees it always has a first_time and therefore always applies its cap.
    # Measured rather than assumed: with case 2's fix in place, an exhaustive
    # search over every one-, two- and three-call configuration of
    # {absent, -600s, -30s, now, +30s, +600s, +5400s} on both times found ZERO
    # configurations where this condition changes the answer; and removing this
    # condition alone, or case 2's alone, leaves the suite green while removing
    # BOTH resurrects the phantom and fails
    # test_a_timeless_first_call_does_not_park_a_train_at_a_terminal_it_has_not_reached.
    #
    # Recorded that way deliberately. This project's standard is that every guard
    # has a killing mutation, and a redundant pair cannot meet it individually; the
    # honest statement is that the PAIR is load-bearing and the double mutation is
    # the one that kills. Both stay: the phantom escaped through this branch once
    # already, and one line is a cheap second lock on it.
    last = timed[-1]
    last_time = last["departure"] if last["departure"] is not None else last["arrival"]
    if last_time is not None and last_time <= now < last_time + _TERMINAL_GRACE_S:
        stop = stops[last["stop_id"]]
        return {
            "latitude": stop["lat"],
            "longitude": stop["lon"],
            "status": "at-station",
            # THE FOURTH RETURN SITE, and the one 6.1 missed. A placement that omits
            # provenance does not fail: the model default fills `unknown`, so a train
            # in the terminal grace window would have claimed its own derivation was
            # undetermined. Nothing caught it because the committed capture has no
            # train in this window, which is exactly why the guard is now synthetic.
            "provenance": "placed",
            "stop_id": last["stop_id"],
            "stop_name": stop["name"],
            "delay": last["delay"],
            "prev_lat": None,
            "prev_lon": None,
            "prev_time": None,
            "next_time": last_time,
        }
    return None


# THE THREE KINDS OF IDENTITY WARNING, each its own count everywhere it is reported
# (/api/status njt_feeds, the poll's one log line, the contract monitor's WARN). They
# were one count until 2026-11-22, and it cost a diagnosis: contract monitor runs 452
# and 455 printed "3" and "1 entity.id/trip_short_name mismatches" for warnings that
# were none of them a number mismatch.
NUMBER_MISMATCH = "number-mismatch"
NO_IDENTITY = "no-identity"
REPEATED_IDENTITY = "repeated-identity"

# HOW A ROW'S IDENTITY WAS COME BY, when it was not a clean join. The row carries one
# of these under "identity"; a clean join carries no such key at all, so its row is the
# row it always was, byte for byte.
#
#   refuted    the trip joined and the train contradicts the join: entity.id is not the
#              train number the schedule gives that trip_id. Nothing is taken from the
#              schedule.
#   unjoined   the trip_id is not in the schedule, or the trip carries none (ADDED).
REFUTED = "refuted"
UNJOINED = "unjoined"


class IdentityWarning(NamedTuple):
    """One identity warning: its kind, the sentence the log prints, and, for a number
    mismatch, the pair and the trip it was found on (empty for the other two kinds)."""

    kind: str
    text: str
    entity_id: str = ""
    short_name: str = ""
    trip_id: str = ""


@dataclass(frozen=True)
class IdentityReport:
    """What one decode found about its trains' identities.

    `joined` counts the running (not canceled) trip updates whose trip_id is in the
    schedule. `checked` counts those of them that could be checked at all, because the
    schedule gives a train number and the entity carries an id, and IT is the denominator
    a mismatch count means anything against: five mismatches of five checked is a schedule
    that is not this feed's, five of a thousand is not, and none of none is no evidence
    either way. The two are equal in every publication seen; a publication that dropped
    trip_short_name would join everything and check nothing (the branch's review, F1)."""

    joined: int
    checked: int
    warnings: list[IdentityWarning]

    def of_kind(self, kind: str) -> list[IdentityWarning]:
        return [warning for warning in self.warnings if warning.kind == kind]

    def counts(self) -> dict[str, int]:
        """The counts as /api/status publishes them (models.NjtFeedHealth)."""
        return {
            "joined": self.joined,
            "checked": self.checked,
            "number_mismatches": len(self.of_kind(NUMBER_MISMATCH)),
            "no_identity": len(self.of_kind(NO_IDENTITY)),
            "repeated_identity": len(self.of_kind(REPEATED_IDENTITY)),
        }


# WHEN A COUNT OF NUMBER MISMATCHES MEANS THE SCHEDULE, and not one odd train: at least
# five of them, making up at least one in four of the trips that could be checked. Both
# arms are needed: the floor keeps a quiet night's one or two from being a share, and
# the share keeps a handful in a full feed from being a floor. In the 2026-11-22
# incident every joined train mismatched (37 of 37 in the one fetch generation counted
# in full, and 37 to 41 over the readings), and after the redeploy none did.
#
# WHOLE NUMBERS, because the monitor puts the rule to counts read off the wire, and an
# integer there can be too large to make a float of.
SCHEDULE_BEHIND_MIN_MISMATCHES = 5
SCHEDULE_BEHIND_ONE_IN = 4

# How long that must hold before /healthz says so. NJ Transit's realtime feed and its
# schedule endpoint do not move at one instant: contract monitor run 445 (2026-11-10)
# read 41 mismatches against a schedule it had just downloaded, and the next run's
# download agreed. Ten minutes is the band the operator's other codes use.
SCHEDULE_BEHIND_HOLD_S = 600.0


def can_judge_schedule(checked: int) -> bool:
    """Whether a decode checked enough trips to say anything about the schedule, for or
    against. Fewer than the mismatch floor could never reach it, so a feed of four is not
    in step: it is four trains, whatever they say."""
    return checked >= SCHEDULE_BEHIND_MIN_MISMATCHES


def mostly_checked(joined: int, checked: int) -> bool:
    """Whether at least half of what joined could be checked. A publication that dropped
    most of its train numbers joins everything and contradicts little, which reads as
    agreement to every other count."""
    return checked * 2 >= joined


def schedule_disagrees(number_mismatches: int, checked: int) -> bool:
    """The share alone: one in four or more of the checked trips contradict the schedule."""
    return number_mismatches * SCHEDULE_BEHIND_ONE_IN >= checked


def schedule_behind(number_mismatches: int, checked: int) -> bool:
    """Whether one decode's counts say the schedule is not the one the feed is keyed to.

    THE ONE STATEMENT OF THE RULE, read by the poller for njt-schedule-behind and by the
    contract monitor for production's counts, so the two cannot come to mean different
    things by the same words."""
    return number_mismatches >= SCHEDULE_BEHIND_MIN_MISMATCHES and schedule_disagrees(
        number_mismatches, checked
    )


class BehindRun(NamedTuple):
    """The first and the latest decode that schedule_behind held for with no decode in
    between that said otherwise, as the poller's clock read them. Failed polls and decodes
    that could not say may stand between the two."""

    since: float
    latest: float


def behind_run(run: BehindRun | None, report: IdentityReport, now: float) -> BehindRun | None:
    """The run after one more successful decode, or None when this decode ends it.

    A DECODE ENDS A RUN ONLY BY SAYING OTHERWISE, which takes trips enough to judge by and
    fewer than one in four of them contradicting the schedule. A failed poll calls nothing
    here, and a decode that cannot say (an empty overnight body, four trains at 02:00, four
    of five contradicting, which is drift too thin to raise and no evidence against it)
    returns the run as it found it, so neither ends one nor lengthens one. The hold is
    therefore measured between two decodes that both saw it, a feed that went dark after
    one bad decode never ages into the code by itself, and a raised code stays raised
    through a quiet night until a decode says otherwise."""
    mismatches = len(report.of_kind(NUMBER_MISMATCH))
    if schedule_behind(mismatches, report.checked):
        return BehindRun(run.since if run is not None else now, now)
    if can_judge_schedule(report.checked) and not schedule_disagrees(mismatches, report.checked):
        return None
    return run


def behind_past_hold(run: BehindRun | None) -> bool:
    """Whether a run has lasted long enough for /healthz to name it."""
    return run is not None and run.latest - run.since >= SCHEDULE_BEHIND_HOLD_S


def _own_last_stop(tu, stops: dict[str, dict]) -> dict | None:
    """The stop this trip update itself ends at, or None when it cannot be named.

    THE LAST STOP THE TRAIN IS STILL GOING TO, which takes two readings the feed forces:

      * A SKIPPED stop is not one it goes to. A partially canceled train drops its
        terminal and keeps its row, and the stop it now ends at is the last one left.
        NO_DATA is kept: it says no prediction exists, not that the train does not call.
      * FEED ORDER IS NOT TRAVEL ORDER. On the committed capture six live trips list a
        SKIPPED intermediate stop after their terminal, so the order is stop_sequence
        wherever every remaining stop carries one (decoder law 5's rule, the one
        _ordered_calls applies), and feed order otherwise.

    NONE RATHER THAN AN EARLIER STOP when the last one is not in the schedule's stops,
    or any call names no stop at all: the last stop the schedule HAPPENS to know is not where the
    train is going, and naming it would be exactly the kind of wrong this rule exists to
    end.

    THE NAME IS THE HELD SCHEDULE'S, and that is the one thing here still taken from it.
    Where NJ Transit renumbers its STOPS too, as it did on 2026-11-03, the name is another
    station's, like every station name and place this process is serving."""
    going_to = [stu for stu in tu.stop_time_update if stu.schedule_relationship != _STOP_SR.SKIPPED]
    # A call that names no stop leaves the update unable to say where it ends: nothing puts
    # that call before or after the others.
    if not going_to or not all(stu.stop_id for stu in going_to):
        return None
    if all(stu.HasField("stop_sequence") for stu in going_to):
        # A STABLE sort, so of two calls with one sequence the later listed is the later
        # call, as it is to _ordered_calls.
        going_to.sort(key=lambda stu: stu.stop_sequence)
    return stops.get(going_to[-1].stop_id)


def _destination(
    tu,
    stops: dict[str, dict],
    lines: dict[str, str],
    route_id: str | None,
    train_num: str | None,
) -> str | None:
    """Where a train the schedule cannot speak for is going.

    ITS OWN LAST STOP, BY NAME, because that is what NJ Transit's own headsign is.
    Measured against the publication in force on 2026-11-22 (njtransit.com's public
    rail_data.zip): the headsign is the name of the trip's last stop on 1441 of 1441
    rail trips, so on all 42 refuted and all 8 unjoined trips sampled from production
    that evening; on the boards themselves the last call a trip showed was its terminal
    on 39 of the 39 whose terminal row was visible. Re-joining the held schedule by
    train number was measured beside it and refused: one sampled number was ambiguous,
    one absent, and one answered with another destination.

    THE LINE AND THE TRAIN NUMBER when that stop cannot be named ("Main/Bergen County
    Line 1724"), and never a bare route id: "5 1724" was what riders read, and 5 is a
    key in NJ Transit's tables rather than a name."""
    last = _own_last_stop(tu, stops)
    if last is not None and last["name"]:
        return last["name"]
    line = lines.get(route_id or "")
    if line and train_num:
        return f"{line} {train_num}"
    return line or (f"Train {train_num}" if train_num else None)


def _identity(
    tu,
    entity_id: str,
    trips: dict[str, dict],
    stops: dict[str, dict],
    lines: dict[str, str],
    position: int,
    seen: set[str],
) -> tuple[dict, list[IdentityWarning]]:
    """(identity fields, warnings) for one trip_update.

    THE JOIN, AND THE TRAIN'S OWN WORD ON IT. trip_id joins app.state.njt_trips for
    route_id, headsign and the train number; entity.id is the train number too, and the
    2026-08-05 probe found the two equal at 745 of 745 observations. This docstring used
    to conclude that a mismatch would never happen and to ask, if it did, which side had
    moved. It has happened, and we know. NJ Transit numbers its trips 1..N in each
    publication, so a republication hands a trip_id to another train; this process keeps
    the publication it loaded at start (A06), so after one the JOIN is what moved and
    entity.id still names the train in front of the rider. On 2026-11-22 production was
    still serving the joined answer from a schedule loaded on the 12th: 37 of 37 joined
    trains in one generation wore another train's destination and number, an NEC train to
    New York reading "Port Jervis".

    SO A REFUTED JOIN GIVES THE ROW NOTHING. Where the schedule's train number and
    entity.id disagree, the number is entity.id, the destination is _destination's, and
    the route is the realtime route_id alone: the schedule's route_id is the other
    train's as surely as its headsign is. The row is marked "refuted" so a board or a
    train can be told from a clean join. The mismatch is still warned about, as its own
    kind, because one or two of them is news and a feed full of them is a schedule that
    needs replacing (schedule_behind).

    AN UNJOINED TRIP TAKES THE SAME DESTINATION RULE, and is marked "unjoined". It is the
    documented shape for ADDED (decoder law 3), and it is also what a trip the held
    schedule has never heard of looks like, which after a republication can be a whole
    line. The join miss is NOT a warning: warning on ADDED would train the operator to
    ignore the signal that matters.

    WHAT NO RULE HERE CAN SEE. A trip whose id AND number both survive a republication
    under a changed destination: it is not refuted, and it shows the held schedule's
    (between an archived 2026-09 publication and the one in force on 2026-11-22 one rail
    trip kept both, and its destination with them). A trip there is nothing to check by,
    because the schedule gives no number or the entity no id: it shows the schedule's, as
    it always has, and is counted as joined and not as checked. And a republication that
    moves the stop ids too (_own_last_stop).

    A CLEAN JOIN IS THE ROW IT ALWAYS WAS, with one exception no publication has shown: a
    joined trip whose headsign is blank is named by its own last stop, unmarked, where it
    read the route id and the number.

    NJ TRANSIT'S ADDED TRIPS CARRY AN EMPTY trip_id. Measured on a live capture:
    164 trip_updates, 128 with a trip_id that joined, and the other 36 all ADDED
    with trip_id "". So the identity below CANNOT key on trip_id for these, and the
    consequence of getting it wrong is not cosmetic: 36 extras sharing one key
    collapse to a single train, which is 35 trains vanishing from the map on
    exactly the disrupted nights extras are running.

    THE FALLBACK CHAIN IS trip_id, THEN entity.id, THEN POSITION, and each step is
    there for a measured reason. entity.id is the train number, unique per train,
    and the probes found it equal to trip_short_name at 745 of 745 for scheduled
    trips, so it is the right identity for an ADDED one. Position is the last
    resort for an entity carrying NEITHER, which collapses everything into a single
    "njt:" key otherwise; it is not stable across polls, and that is correct,
    because a train the feed gives no identity at all cannot be tracked between
    polls and pretending otherwise would animate one train into another. That case
    also raises a warning, because a trip_update with no identity of any kind would
    be a new NJT fact.

    AND THE CHAIN IS CHECKED FOR COLLISIONS, because every step above reasons about
    ONE entity and none of them can see a second entity arriving at the same key.
    GTFS-RT requires FeedEntity.id to be unique within a message and NJT has honored
    that in every probe, so `seen` should never fire. But the empty trip_id this
    chain exists for was also unpredicted, and the two failure modes are not
    symmetric: an entity with NO identity warns loudly, while two entities sharing
    one identity would be silent, and silent is the shape that puts several running
    trains on a board as one. A repeat is separated by position and announced.
    """
    trip_id = tu.trip.trip_id or ""
    static = trips.get(trip_id) or {}
    scheduled_num = static.get("short_name")
    checked = bool(static and entity_id and scheduled_num)
    refuted = checked and entity_id != scheduled_num
    realtime_route = tu.trip.route_id or None

    route_id: str | None = realtime_route
    train_num: str | None = entity_id or None
    headsign: str | None = None
    if not refuted:
        # The realtime route_id is preferred when present: for an ADDED trip it is the
        # only route there is, and for a scheduled trip the probe found the two agree.
        route_id = realtime_route or static.get("route_id")
        train_num = scheduled_num or train_num
        headsign = static.get("headsign")
    if headsign is None:
        headsign = _destination(tu, stops, lines, route_id, train_num)

    warnings = []
    if refuted:
        warnings.append(
            IdentityWarning(
                NUMBER_MISMATCH,
                f"entity.id {entity_id!r} does not match trip_short_name {scheduled_num!r} "
                f"for trip {trip_id!r}",
                entity_id=entity_id,
                short_name=scheduled_num or "",
                trip_id=trip_id,
            )
        )
    elif not trip_id and not entity_id:
        warnings.append(
            IdentityWarning(
                NO_IDENTITY,
                f"trip_update at position {position} carries neither a trip_id nor an "
                "entity.id, so it has no identity that survives a poll; keyed by position",
            )
        )

    # THE KEY. Falls through trip_id, entity.id, position: see the docstring for
    # why each step exists and what each one costs.
    if trip_id:
        key = trip_id
    elif entity_id:
        key = f"{SYSTEM}:{entity_id}"
    else:
        key = f"{SYSTEM}:position-{position}"

    # THE COLLISION CHECK, which is about the only thing the chain above cannot see.
    # First one in keeps the clean key; a repeat is pushed onto a position-suffixed
    # one. That suffix is not stable across polls, and neither is anything else here:
    # two entities the feed made indistinguishable cannot be told apart between
    # polls by any rule. Losing the animation is the small cost; losing the train is
    # the one worth refusing. Separated with ":position-", matching the last resort
    # above and staying URL-safe, since these ids are what a future per-train route
    # would be keyed on and "#" would be read as a fragment.
    if key in seen:
        warnings.append(
            IdentityWarning(
                REPEATED_IDENTITY,
                f"trip_update at position {position} repeats the identity {key!r} of an "
                "earlier entity in this feed; keyed by position so the two do not "
                "collapse into one train",
            )
        )
        key = f"{key}:position-{position}"
    # Recording the key actually HANDED OUT, not the one asked for. The suffix
    # already makes it unique (position is), so this is redundant for the shapes
    # measured; it is what keeps the set meaning "keys in use" rather than "keys
    # requested", which is the invariant a third duplicate would rely on.
    seen.add(key)
    mark = REFUTED if refuted else (None if static else UNJOINED)
    fields = {
        "trip_id": key,
        "route_id": route_id,
        "headsign": headsign,
        "train_num": train_num,
        "joined": bool(static),
        "checked": checked,
        # Spread into the served rows as it is, so a clean join adds no key to one.
        "mark": {"identity": mark} if mark else {},
    }
    return fields, warnings


def decode_njt_trip_updates(
    raw: bytes,
    stops: dict[str, dict],
    trips: dict[str, dict],
    now: float,
    lines: dict[str, str] | None = None,
) -> tuple[list[dict], dict[str, list[dict]], float | None, IdentityReport]:
    """Decode the NJT TripUpdates feed into (trains, arrivals, feed_timestamp, identity).

    One parse, one walk, two products, matching every other decoder in this
    package. `identity` is the IdentityReport for the poll result: how many trips
    joined, and every identity warning by kind. It is deliberately not an error, and
    no warnings is the expected state.

    `lines` is the rider-facing name of each route (njt_static.build_njt_line_names),
    read only for a train whose own last stop cannot be named.

    CANCELED IS FILTERED ONCE, AT THE TOP OF THE WALK (decoder law 1), which is
    what makes the phantom impossible rather than merely unlikely: both products
    are built below that line, so no consumer can reconstruct a canceled trip from
    either of them. A partial cancellation is not this shape at all: it arrives as
    a live trip with SOME stops SKIPPED, so it flows through here and loses only
    the dropped stops, which is exactly right.
    """
    # parse_feed rejects an empty or malformed body (C3). A VALID feed with zero
    # entities decodes normally and yields zero trains, which is the overnight
    # state the probe recorded as a 13-byte body (decoder law 6).
    feed = parse_feed(raw)
    # Hoisted from the return: every row below is dated by it (see the train emit).
    feed_header = _header_timestamp(feed)

    trains: list[dict] = []
    arrivals: dict[str, list[dict]] = defaultdict(list)
    joined = checked = 0
    warnings: list[IdentityWarning] = []
    # The keys handed out so far in THIS decode, so _identity can refuse to hand the
    # same one to two entities. Scoped to the walk, never carried between polls: a
    # key repeating across polls is the same train, which is the whole point of a key.
    seen_keys: set[str] = set()

    for position, entity in enumerate(feed.entity):
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            # DECODER LAW 1. The probe's canceled trips keep full arrival and
            # departure times on every stop, so nothing downstream could tell them
            # from a running train; this line is the only thing between that trip
            # and a rider's departure board.
            continue

        identity, entity_warnings = _identity(
            tu, entity.id, trips, stops, lines or {}, position, seen_keys
        )
        joined += identity["joined"]
        checked += identity["checked"]
        warnings.extend(entity_warnings)

        calls = _ordered_calls(tu, stops)

        # ARRIVALS: every surviving call still ahead of now. Built from the SAME
        # filtered call list as placement, so a stop dropped for one is dropped for
        # both by construction rather than by two rules kept in step by hand.
        for call in calls:
            when = _still_upcoming(call)
            if when is None or when < now - _JUST_PASSED_GRACE_S:
                continue
            arrivals[call["stop_id"]].append(
                {
                    "train_num": identity["train_num"],
                    "route_id": identity["route_id"],
                    "headsign": identity["headsign"],
                    "arrival": call["arrival"],
                    "departure": call["departure"],
                    "delay": call["delay"],
                    "trip_id": identity["trip_id"],
                    "observed_at": feed_header,
                    "provenance": "reported",
                    **identity["mark"],
                }
            )

        placement = _place(calls, stops, now)
        if placement is None:
            continue
        trains.append(
            {
                "system": SYSTEM,
                "id": identity["trip_id"],
                "trip_id": identity["trip_id"],
                "route_id": identity["route_id"],
                "headsign": identity["headsign"],
                "train_num": identity["train_num"],
                # THE HEADER, BECAUSE NOTHING ELSE HERE IS DATED. NJ Transit publishes
                # no vehicle feed at all (the reasoning is at the poller registry) and
                # none of its 112 trip_updates on the committed capture carries a
                # timestamp, so the message's own generation time is the only clock
                # behind every position and every departure below. Unlike Metro-North's
                # it is a good one: generation every ~11.8s and 9s to 23s of lag at
                # peak, measured, which is why this row is age-gated and MNR's is not.
                # THE FRESHNESS BUDGET, DERIVED at the top of this file is the working.
                "observed_at": feed_header,
                **placement,
                **identity["mark"],
            }
        )

    report = IdentityReport(joined, checked, warnings)
    return trains, _trim_njt_arrivals(arrivals), feed_header, report


def _trim_njt_arrivals(arrivals: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Sort each stop's departures soonest-first and cap them.

    Its own trim rather than feeds.shared._trim_arrivals because the shape differs:
    that one takes {stop: {bucket: [...]}} and NJT's index is flat (see
    ARRIVALS_PER_STOP). Sorted on _still_upcoming, the SAME key the arrivals
    filter admitted the row on, then by train number so two departures sharing a
    minute order deterministically across polls rather than by dict insertion.
    Sharing the key is what keeps a dwelling train from sorting by an arrival five
    minutes past while a rider is reading a board ordered by departure.
    """
    trimmed: dict[str, list[dict]] = {}
    for stop_id, rows in arrivals.items():
        rows.sort(key=_arrival_sort_key)
        trimmed[stop_id] = rows[:ARRIVALS_PER_STOP]
    return trimmed


def _arrival_sort_key(row: dict) -> tuple[bool, float, bool, str]:
    """Soonest first, then by train number, with both unknowns sorted LAST.

    Two defensive halves, and the second is the one that bites. A timeless row
    cannot reach here, since the arrivals filter admits a call only after
    _still_upcoming returned a number, but a bare `_still_upcoming(row)` in the key
    would raise TypeError rather than misorder if that ever stopped being true, so
    the flag makes the key total instead of leaving a crash for a future caller.

    A row with NO train number can reach here: an entity carrying neither a trip_id
    nor an entity.id gets train_num None. Falling back to "" alone would sort it
    ahead of every named train, so on a stop where several departures share a
    minute the nameless rows would take the six slots and evict real trains from a
    rider's board. Unknown identity loses the tie-break instead of winning it.
    """
    when = _still_upcoming(row)
    train_num = row["train_num"]
    return (when is None, when if when is not None else 0.0, train_num is None, train_num or "")


async def fetch_njt_trains(
    stops: dict[str, dict],
    trips: dict[str, dict],
    lines: dict[str, str] | None = None,
    post=None,
) -> tuple[list[dict], dict[str, list[dict]], float | None, IdentityReport]:
    """Fetch and decode the NJT TripUpdates feed.

    THROUGH njt_post AND NOTHING ELSE. That is not a style preference: the token
    cache behind it is what makes three NJT callers (this poller, the alerts
    poller, the static loader) share one token and produce exactly one re-mint
    when it expires, out of ten a day per account (njt_auth.DAILY_MINT_LIMIT). A
    direct POST here would route around the single-flight lock and turn every
    concurrent expiry into N mints.

    `post` is injectable for tests; production uses njt_auth.njt_post. Nothing is
    caught here: an unconfigured deployment raises NjtNotConfigured and every
    other failure raises its own type, so the REFRESHER decides what each one
    means for the cache. A decoder that swallowed them would have to guess.
    """
    sender = post if post is not None else njt_auth.njt_post
    raw = await sender(NJT_TU_URL, {})
    return decode_njt_trip_updates(raw, stops, trips, time.time(), lines)


def log_cross_check(report: IdentityReport) -> None:
    """Log one decode's identity warnings, at most one line per poll.

    EACH KIND IS COUNTED BY ITSELF and the first warning is quoted whole. One line
    rather than one per train, because a schedule that has fallen behind mismatches the
    whole feed at once (37 to 41 trains a poll in the 2026-11-22 incident, every 20 s),
    and that many lines a poll would bury the signal the line exists to raise.
    """
    if not report.warnings:
        return
    counts = report.counts()
    logger.warning(
        "NJ Transit trip identity: %d number mismatch(es) of %d checked trip(s), "
        "%d with no identity, %d repeated; first: %s",
        counts["number_mismatches"],
        counts["checked"],
        counts["no_identity"],
        counts["repeated_identity"],
        report.warnings[0].text,
    )
