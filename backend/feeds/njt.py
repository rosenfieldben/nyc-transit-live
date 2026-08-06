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
  3. ADDED WAS NEVER OBSERVED in either probe. It is accepted anyway, expects no
     static join, and synthesizes its display name from route plus train number.
     Never assumed away, never crashed on.
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

import env_seams
import njt_auth
from feeds.shared import (
    _DROP_STOP_RELATIONSHIPS,
    _DROP_TRIP_RELATIONSHIPS,
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
# WHAT THAT MAKES THE WORST HONEST AGE OF A SERVED NJT TRAIN:
#
#     23s   worst observed peak header lag
#   + 20s   one poll interval (pollers.POLL_INTERVAL_S), the most that can elapse
#           between the feed being published and this app fetching it
#   -----
#     43s   worst expected age at the moment we serve it
#
# cache.FEED_STALE_AFTER_S is 90s, which is a little over 2x that. The headroom
# is deliberate and is the same shape as the monitor's WARN/FAIL gap: a threshold
# that trips on ordinary peak jitter trains an operator to ignore it. NJ Transit
# is the TIGHTEST-MARGIN system under that shared 90s budget (the MTA feeds
# regenerate every ~30s but publish with far less lag), so if 90 ever moves, this
# is the derivation that has to be re-checked first.
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
# day against a rate limit NJ Transit does not publish, and 4 token mints (the
# 6h MAX_TOKEN_AGE_S ceiling); if that ever proves too warm, POLL_INTERVAL_S and
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
                "stop_id": call["stop_id"],
                "stop_name": stop["name"],
                "delay": call["delay"],
                "prev_lat": None,
                "prev_lon": None,
                "prev_time": None,
                "next_time": departure,
            }

    # Case 2: before the first call. `or` rather than an explicit None check: a
    # first call with only a departure is still a time this can be judged against.
    first = calls[0]
    first_time = first["arrival"] if first["arrival"] is not None else first["departure"]
    if first_time is not None and now < first_time:
        if first_time > now + MAX_FUTURE_FIRST_STOP_S:
            return None  # listed far ahead of its own origin: not a running train
        stop = stops[first["stop_id"]]
        return {
            "latitude": stop["lat"],
            "longitude": stop["lon"],
            "status": "approaching",
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
    for prev, nxt in zip(calls, calls[1:]):
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
                "stop_id": nxt["stop_id"],  # the stop it is heading for
                "stop_name": next_stop["name"],
                "delay": nxt["delay"],
                "prev_lat": prev_stop["lat"],
                "prev_lon": prev_stop["lon"],
                "prev_time": left,
                "next_time": right,
            }

    # Case 4: past the last call, within the terminal grace.
    last = calls[-1]
    last_time = last["departure"] if last["departure"] is not None else last["arrival"]
    if last_time is not None and now < last_time + _TERMINAL_GRACE_S:
        stop = stops[last["stop_id"]]
        return {
            "latitude": stop["lat"],
            "longitude": stop["lon"],
            "status": "at-station",
            "stop_id": last["stop_id"],
            "stop_name": stop["name"],
            "delay": last["delay"],
            "prev_lat": None,
            "prev_lon": None,
            "prev_time": None,
            "next_time": last_time,
        }
    return None


def _identity(tu, entity_id: str, trips: dict[str, dict]) -> tuple[dict, str | None]:
    """(identity fields, cross-check warning) for one trip_update.

    THE JOIN, and the cross-check the probe says will never fire. trip_id joins
    app.state.njt_trips for route_id, headsign and the train number; separately,
    entity.id was equal to the train number and to trip_short_name at 745 of 745
    observations. So the equality is asserted as a CROSS-CHECK and a mismatch is
    reported as a warning on the poll result rather than dropping the train: the
    probe says it will not happen, and if it starts happening we want to know
    rather than guess which side moved.

    AN ADDED TRIP (decoder law 3) is expected to miss the static entirely. Its
    display name is synthesized from the realtime route_id plus the train number,
    which is everything a rider needs to identify it on a board, and the join miss
    is NOT reported as a warning: a miss is the documented shape for ADDED, and
    warning on it would train the operator to ignore the signal that matters.
    """
    trip_id = tu.trip.trip_id or ""
    static = trips.get(trip_id) or {}
    # The realtime route_id is preferred when present: for an ADDED trip it is the
    # only route there is, and for a scheduled trip the probe found the two agree.
    route_id = (tu.trip.route_id or None) or static.get("route_id")
    train_num = static.get("short_name") or (entity_id or None)
    headsign = static.get("headsign")
    if headsign is None:
        # Synthesized, and marked as such by construction rather than by a flag:
        # "NEC 3800" is what a board would show for a train the schedule does not
        # know, and it degrades to whichever half exists.
        parts = [part for part in (route_id, train_num) if part]
        headsign = " ".join(parts) if parts else None

    warning = None
    if static and entity_id and train_num and entity_id != train_num:
        warning = (
            f"entity.id {entity_id!r} does not match trip_short_name {train_num!r} "
            f"for trip {trip_id!r}"
        )
    return (
        {
            "trip_id": trip_id or f"{SYSTEM}:{entity_id}",
            "route_id": route_id,
            "headsign": headsign,
            "train_num": train_num,
            "joined": bool(static),
        },
        warning,
    )


def decode_njt_trip_updates(
    raw: bytes,
    stops: dict[str, dict],
    trips: dict[str, dict],
    now: float,
) -> tuple[list[dict], dict[str, list[dict]], float | None, list[str]]:
    """Decode the NJT TripUpdates feed into (trains, arrivals, feed_timestamp, warnings).

    One parse, one walk, two products, matching every other decoder in this
    package. `warnings` carries the entity.id cross-check misses for the poll
    result; it is deliberately not an error, and an empty list is the expected
    state.

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

    trains: list[dict] = []
    arrivals: dict[str, list[dict]] = defaultdict(list)
    warnings: list[str] = []

    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in _DROP_TRIP_RELATIONSHIPS:
            # DECODER LAW 1. The probe's canceled trips keep full arrival and
            # departure times on every stop, so nothing downstream could tell them
            # from a running train; this line is the only thing between that trip
            # and a rider's departure board.
            continue

        identity, warning = _identity(tu, entity.id, trips)
        if warning:
            warnings.append(warning)

        calls = _ordered_calls(tu, stops)

        # ARRIVALS: every surviving call still ahead of now. Built from the SAME
        # filtered call list as placement, so a stop dropped for one is dropped for
        # both by construction rather than by two rules kept in step by hand.
        for call in calls:
            when = call["arrival"] if call["arrival"] is not None else call["departure"]
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
                **placement,
            }
        )

    return trains, _trim_njt_arrivals(arrivals), _header_timestamp(feed), warnings


def _trim_njt_arrivals(arrivals: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Sort each stop's departures soonest-first and cap them.

    Its own trim rather than feeds.shared._trim_arrivals because the shape differs:
    that one takes {stop: {bucket: [...]}} and NJT's index is flat (see
    ARRIVALS_PER_STOP). Sorted on the same best-available time the arrivals filter
    used, then by train number so two departures sharing a minute order
    deterministically across polls rather than by dict insertion.
    """
    trimmed: dict[str, list[dict]] = {}
    for stop_id, rows in arrivals.items():
        rows.sort(
            key=lambda row: (
                row["arrival"] if row["arrival"] is not None else row["departure"],
                row["train_num"] or "",
            )
        )
        trimmed[stop_id] = rows[:ARRIVALS_PER_STOP]
    return trimmed


async def fetch_njt_trains(
    stops: dict[str, dict],
    trips: dict[str, dict],
    post=None,
) -> tuple[list[dict], dict[str, list[dict]], float | None, list[str]]:
    """Fetch and decode the NJT TripUpdates feed.

    THROUGH njt_post AND NOTHING ELSE. That is not a style preference: the token
    cache behind it is what makes three NJT callers (this poller, the alerts
    poller, the static loader) share one token and produce exactly one re-mint
    when it expires, against a rate limit NJ Transit does not publish. A direct
    POST here would route around the single-flight lock and turn every concurrent
    expiry into N mints.

    `post` is injectable for tests; production uses njt_auth.njt_post. Nothing is
    caught here: an unconfigured deployment raises NjtNotConfigured and every
    other failure raises its own type, so the REFRESHER decides what each one
    means for the cache. A decoder that swallowed them would have to guess.
    """
    sender = post if post is not None else njt_auth.njt_post
    raw = await sender(NJT_TU_URL, {})
    return decode_njt_trip_updates(raw, stops, trips, time.time())


def log_cross_check(warnings: list[str]) -> None:
    """Log the entity.id cross-check misses, at most one line per poll.

    Capped and counted rather than one line per train: the probe says this cannot
    happen, so if it ever does it will probably happen to the whole feed at once,
    and 700 identical warnings a poll would bury the signal it exists to raise.
    """
    if not warnings:
        return
    logger.warning(
        "NJ Transit entity.id / trip_short_name cross-check failed for %d trip(s); first: %s",
        len(warnings),
        warnings[0],
    )
