"""Cross-system helpers for the feed decoders.

Holds what more than one system module needs (or, for a few single-user
helpers, what the phase spec groups here): the NYC and railroad coordinate
bounding boxes, the MTA Bus Time key lookup, the feed-header content timestamp,
GTFS stop-time and trip-start parsing, per-station arrival trimming, the
ScheduleRelationship drop sets, the New York timezone and trip-start grace
windows, and the cross-poll previous-anchor carry. Nothing here is
system-specific; the per-system modules import from it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

# The submodules all log through this one logger. Named "feeds" (not __name__)
# so records and main.py's logging config are unchanged by the package split.
logger = logging.getLogger("feeds")


# The .env file lives in the project root, one level up from backend/. Three
# .parent hops (not two) because this file is now nested in the feeds/ package:
# shared.py -> feeds/ -> backend/ -> project root. (Before the split, feeds.py
# sat directly in backend/ and used two.)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env")


# NYC bounding box. The bus feed occasionally emits out-of-range coordinates
# (e.g. (0, 0) from a depot/test vehicle) that would scatter markers across the
# globe; this is the same invariant the subway golden test asserts.
NYC_LAT_MIN, NYC_LAT_MAX = 40.4, 41.1
NYC_LON_MIN, NYC_LON_MAX = -74.3, -73.6


def _in_nyc(lat: float, lon: float) -> bool:
    return NYC_LAT_MIN <= lat <= NYC_LAT_MAX and NYC_LON_MIN <= lon <= NYC_LON_MAX


# Railroad bounding box: much wider than the bus/subway NYC box because the LIRR
# runs out to Montauk and the MNR to Poughkeepsie / Wassaic / New Haven. Used to
# drop any stray out-of-range vehicle coordinate; every captured sample falls
# inside, so this is a sanity guard, not a service-area definition.
RAILROAD_LAT_MIN, RAILROAD_LAT_MAX = 40.3, 42.1
RAILROAD_LON_MIN, RAILROAD_LON_MAX = -74.5, -71.7


def _in_railroad_box(lat: float, lon: float) -> bool:
    return (
        RAILROAD_LAT_MIN <= lat <= RAILROAD_LAT_MAX and RAILROAD_LON_MIN <= lon <= RAILROAD_LON_MAX
    )


# Kept in shared (not railroad.py) because both RAILROAD_FEED_URLS (railroad.py)
# and ALERT_FEED_URLS (alerts.py) build on this same MTA Dataservice base.
# Same %2F-encoded base as the subway feeds (the literal-slash form is an
# unmatched API Gateway route that 403s with a misleading "Missing Authentication
# Token"; the encoded form needs no key).
_RAILROAD_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds"


def _api_key() -> str:
    key = os.getenv("BUS_TIME_API_KEY")
    if not key or key == "your-key-here":
        raise RuntimeError(
            "BUS_TIME_API_KEY is not set. Copy .env.example to .env in the "
            "project root and add your MTA Bus Time key."
        )
    return key


def _header_timestamp(feed) -> float | None:
    """The feed's content time (FeedHeader.timestamp, MTA's clock) as a float,
    or None when the feed omits it (the field is 0). This is distinct from the
    app server's poll time — see the freshness handling in main.py."""
    return float(feed.header.timestamp) or None


NYC_TZ = ZoneInfo("America/New_York")


# Show a trip slightly before its scheduled start; anything further out is a
# not-yet-departed phantom that would pile up at terminals.
TRIP_START_GRACE_S = 120


# Fallback when a trip's scheduled start can't be derived: don't trust a trip
# still sitting at its first listed stop more than this far in the future.
MAX_FUTURE_FIRST_STOP_S = 180


def _stop_time(stu) -> int | None:
    """Latest available POSIX time for a stop_time_update, or None.

    Latest (departure when both are set) matters for the "still upcoming"
    test: a train dwelling or held at a station has arrival in the past but
    departure in the future, and must not be treated as past that stop —
    otherwise held trains get plotted one station ahead.
    """
    times = []
    for field in ("arrival", "departure"):
        if stu.HasField(field):
            event = getattr(stu, field)
            if event.HasField("time") and event.time:
                times.append(event.time)
    return max(times) if times else None


def _trip_start_ts(trip) -> float | None:
    """Scheduled start of a subway trip as a POSIX timestamp, if derivable.

    The feeds include trips up to ~30 minutes before they depart; the NYCT
    extension flag that marks them (is_assigned) isn't readable with the
    standard bindings, so the schedule start is the discriminator. Prefers
    explicit start_time/start_date (SIR sets them); falls back to the NYCT
    trip_id convention where the prefix is the origin departure in
    centiminutes after midnight of the service day (may exceed 24h for
    post-midnight trips). Returns None if neither source parses.
    """
    # KNOWN CAVEAT (DST): we add a timedelta to service-day midnight and rely on
    # .timestamp() below. Adding a timedelta to a zoneinfo-aware datetime shifts
    # wall-clock fields without re-normalizing the UTC offset, so on the ~2
    # days/year that cross a DST boundary the derived instant can be off by an
    # hour. The schedule start is only used as a coarse "has this trip departed"
    # discriminator (TRIP_START_GRACE_S = 120s), so a twice-yearly hour skew
    # doesn't meaningfully affect placement; a precise fix would need wall-clock
    # reconstruction with fold handling and isn't worth the regression risk.
    try:
        d = trip.start_date  # YYYYMMDD
        base = datetime(int(d[:4]), int(d[4:6]), int(d[6:8]), tzinfo=NYC_TZ)
    except (ValueError, IndexError):
        base = datetime.now(NYC_TZ).replace(hour=0, minute=0, second=0, microsecond=0)

    if trip.start_time:
        try:
            h, m, s = (int(p) for p in trip.start_time.split(":"))
            return (base + timedelta(hours=h, minutes=m, seconds=s)).timestamp()
        except ValueError:
            pass  # malformed; try the trip_id prefix

    prefix = trip.trip_id.split("_", 1)[0]
    if prefix.isdigit():
        return (base + timedelta(minutes=int(prefix) / 100)).timestamp()
    return None


# Next this many upcoming trains kept per station and direction for arrivals.
ARRIVALS_PER_DIRECTION = 6


def _trim_arrivals(arrivals: dict) -> dict[str, dict[str, list[dict]]]:
    """Sort every station bucket soonest-first and cap it at
    ARRIVALS_PER_DIRECTION. Shared by the subway, railroad, and PATH decoders,
    which each grew an identical copy of this block phase by phase (the
    cleanup queued in 13b): the popup only ever shows the next few trains, so
    anything past the cap is payload weight with no reader."""
    trimmed: dict[str, dict[str, list[dict]]] = {}
    for station_id, buckets in arrivals.items():
        trimmed[station_id] = {}
        for direction, arrs in buckets.items():
            arrs.sort(key=lambda a: a["arrival"])
            trimmed[station_id][direction] = arrs[:ARRIVALS_PER_DIRECTION]
    return trimmed


# GTFS-RT schedule relationships that mean "ignore this": a CANCELED trip isn't
# running; a DELETED trip has been removed and must not render as a ghost train;
# a SKIPPED/NO_DATA stop carries no real prediction. We drop them from both
# placement and arrivals.
#
# DELETED is filtered as of gtfs-realtime-bindings 2.1.0 (the pin in
# requirements.lock), whose trip enum carries DELETED=7. Under the old 2.0.0
# binding it was not: proto2's closed-enum decoding coerced an unknown wire
# value to the field default, so a real DELETED=7 read as SCHEDULED=0 and
# slipped past this set. The getattr below was written for exactly this
# upgrade: it resolved to a collision-safe -1 sentinel under 2.0.0 and now
# resolves the real value, activating the filter with no logic change (getattr
# is kept, rather than a direct attribute read, so an older binding degrades to
# the sentinel instead of raising at import). NEW (2.1.0's other addition)
# marks a trip new relative to the static schedule, the same family as ADDED;
# ADDED trips run and are not dropped, so NEW is deliberately not dropped
# either, and unfiltered NEW is not the bug this pin fixed.
_TRIP_SR = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship
_STOP_SR = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship
_DROP_TRIP_RELATIONSHIPS = frozenset({_TRIP_SR.CANCELED, getattr(_TRIP_SR, "DELETED", -1)})
_DROP_STOP_RELATIONSHIPS = frozenset({_STOP_SR.SKIPPED, _STOP_SR.NO_DATA})


def carry_forward_prev(
    trains: list[dict],
    last_positions: dict,
    key: Callable[[dict], object] | None = None,
) -> dict:
    """Fill missing prev anchors from a persisted previous-station anchor, and return the
    position memory for the next poll.

    `key` maps a train to its memory key; it defaults to the trip_id, which is what the subway
    path passes. The railroad path passes (system, trip_id) because LIRR and MNR trip_id
    namespaces are independent and can collide. The anchor-holding behavior and the limitations
    below are identical for both; only the key differs.

    The feeds usually prune the just-departed stop, so the decode leaves prev_* null
    for most trains. We recover a prev by remembering, per trip, the station it most recently
    departed (the anchor) and holding that anchor fixed for as long as the train is approaching
    the same next stop, advancing it only when the next stop changes. Holding it fixed is the
    point: if the anchor were recomputed only on the transition poll, prev would be supplied on
    just that one poll and the train would snap to its next station on every other poll of the
    segment.

    Each poll, the anchor is: None on first sighting; the previous observation (the now-departed
    station, timed by its last predicted arrival) when the next stop changed; or the carried
    anchor when the next stop is unchanged. prev is synthesized from the anchor only when the
    feed gave no real prev (prev_lat is None), the anchor is a different, time-stamped station,
    the current placement has a next_time, and the bracket is forward (anchor time < next_time).
    A real feed prev is never overwritten. The returned dict is rebuilt from this poll's trains,
    so finished or absent trips are pruned.

    KNOWN LIMITATIONS (straight-line interpolation; both resolved by v2's route-geometry
    slicing, which is along-route and forward-only):
      * Backward slide on stop regression. The anchor advances on ANY next-stop change,
        including a backward one. If the feed revises a held or mis-predicted trip's next stop
        to a station earlier on the line while the new ETA is further out, the forward-time
        guard (anchor time < next_time) still passes and the train is drawn sliding backward
        for one poll. The guard compares ETAs, not station positions, so it cannot catch this;
        a real fix needs the static route stop order (v2).
      * Multi-poll / multi-station gap. The anchor is normally one station back, but after one
        or more missed polls (memory is preserved across a failed poll) or a 2+-station jump in
        a single interval it can be further back, so the straight line cuts across the skipped
        station(s). Self-corrects on the next good poll.
    Both are rare, self-correcting, and we accept them here rather than pull route-order logic
    forward out of v2.
    """
    new_positions: dict = {}
    for train in trains:
        mem_key = train["trip_id"] if key is None else key(train)
        next_time = train["next_time"]
        prev_obs = last_positions.get(mem_key)

        # The departed-station anchor for this poll (held fixed across a segment).
        if prev_obs is None:
            anchor = None  # first sighting: nothing behind it yet
        elif prev_obs["stop_id"] != train["stop_id"]:
            # Next stop advanced: the station approached last poll is the one just
            # departed, timed by its last predicted arrival.
            anchor = {
                "stop_id": prev_obs["stop_id"],
                "lat": prev_obs["lat"],
                "lon": prev_obs["lon"],
                "time": prev_obs["next_time"],
            }
        else:
            anchor = prev_obs["anchor"]  # same segment: keep the anchor fixed

        if (
            train["prev_lat"] is None
            and next_time is not None
            and anchor is not None
            and anchor["time"] is not None
            and anchor["stop_id"] != train["stop_id"]
            and anchor["time"] < next_time
        ):
            train["prev_lat"] = anchor["lat"]
            train["prev_lon"] = anchor["lon"]
            train["prev_time"] = anchor["time"]

        new_positions[mem_key] = {
            "stop_id": train["stop_id"],
            "lat": train["latitude"],
            "lon": train["longitude"],
            "next_time": next_time,
            "anchor": anchor,
        }
    return new_positions


def merge_system_generations(
    fresh_by_system: dict[str, list[dict]],
    prev_by_system: dict[str, list[dict]] | None,
    failed_systems: Iterable[str],
    prev_retained_since: Mapping[str, float],
    now: float,
    max_retention_s: float,
) -> tuple[dict[str, list[dict]], dict[str, float]]:
    """Merge one poll's per-system items with the previous poll's, carrying a
    FAILED system's items forward instead of letting them vanish from the
    aggregate (C2). The direct sibling of merge_alert_generations, deliberately
    the same shape so the two retention stories read alike.

    Pure and clock-injected: `now` and `prev_retained_since` are passed in, never
    read from a wall clock or module state. Returns (merged_by_system,
    retained_since), where retained_since maps each system CURRENTLY served from
    carried-forward items to the instant its retention began.

    RETENTION KEYS ON MEMBERSHIP IN failed_systems, NEVER ON AN EMPTY LIST, and
    that distinction is the whole correctness of this function. The decoders
    classify a feed as failed ONLY at the exception boundary (a fetch error or an
    undecodable protobuf); an HTTP 200 carrying a well-formed zero-entity feed is
    a SUCCESS. So a system that genuinely has no vehicles running right now, which
    is ordinary overnight, reports an empty list on a healthy poll and MUST have
    its previous items replaced, not retained. Keying on emptiness would resurrect
    last night's trains every night. Keying on key-absence would be just as wrong:
    a railroad system that decodes but has no static stops loaded contributes no
    entries while never appearing in failed_systems.

    Per system:
      - NOT failed this poll: its items come exclusively from fresh_by_system,
        replacing wholesale, including when that means replacing with nothing.
      - failed this poll: its items are carried forward from prev_by_system,
        capped at max_retention_s measured from when the system FIRST went down
        (prev_retained_since, or now for a newly-failed one). Past the cap the
        items are dropped and only the caller's per-system freshness block still
        reports the outage.

    The items are NOT copied here: this returns the same dicts it was handed. A
    caller that mutates merged items in place (the anchor carry does) must copy
    them first, or it will edit objects a previous response already exposed.
    """
    failed = set(failed_systems)
    merged: dict[str, list[dict]] = {
        system: items for system, items in fresh_by_system.items() if system not in failed
    }

    retained_since: dict[str, float] = {}
    # SORTED, not set order. Iterating the set inserted retained systems into `merged`
    # in a hash-randomized order, and the combine helpers dedup a cross-group trip in
    # dict order, so which group owned a duplicated trip (and which arrivals won a
    # trim tie) varied between processes. Sorting makes the merge deterministic.
    for system in sorted(failed):
        # Explicit None check, not truthiness: a retention-start timestamp can be
        # 0.0 (epoch), which `or now` would wrongly reset every poll.
        started = prev_retained_since.get(system)
        if started is None:
            started = now
        if now - started >= max_retention_s:
            continue  # capped: drop this system's items; freshness still reports it
        # RECORDED WHETHER OR NOT ANYTHING WAS CARRIED. This used to sit inside
        # `if carried:`, which made retained_since mean "we are serving carried
        # items" and broke the clock for any system that went down holding an EMPTY
        # list: nothing was carried, so no clock was recorded, so the next poll saw
        # no previous start and restarted from `now` forever. A caller merging two
        # kinds of data for one system (trains and arrivals) then got two different
        # answers, and the cap could never fire for the kind that had been empty.
        # The field now means "this system has been failing since X and is still
        # inside the retention window", which is a property of the SYSTEM and so is
        # the same for every kind of data merged under it.
        retained_since[system] = started
        carried = (prev_by_system or {}).get(system) or []
        if carried:
            merged[system] = carried
    return merged, retained_since


def drop_expired_arrivals(
    arrivals_by_system: dict[str, dict[str, dict[str, list[dict]]]],
    now: float,
    only: Iterable[str],
) -> dict[str, dict[str, dict[str, list[dict]]]]:
    """Drop arrivals whose predicted time has already passed, per system.

    REQUIRED WHENEVER ARRIVALS ARE RETAINED, and the reason is a nasty interaction
    rather than mere tidiness. A retained system's arrivals keep their ORIGINAL
    absolute times, and _trim_arrivals sorts each station bucket soonest-first
    before capping it at ARRIVALS_PER_DIRECTION. An expired time is the smallest
    number in the bucket, so every stale retained row sorts AHEAD of every live one
    and fills the cap: a single failed feed group could evict all the fresh
    arrivals from a station it shares with healthy groups, leaving a rider looking
    at six departures that already happened and none of the ones that have not.
    Pruning first means a retained bucket can only ever pad out what is left after
    the live rows, never displace them.

    APPLIED ONLY TO THE SYSTEMS IN `only` (the retained ones). It must NOT touch
    fresh data: the decoders deliberately keep a stop up to a minute past its time
    (a train dwelling or just-departed is still the right answer for that station),
    and a blanket prune here would silently override that grace for every system.
    """
    retained = set(only)
    pruned: dict[str, dict[str, dict[str, list[dict]]]] = {}
    for system, stations in arrivals_by_system.items():
        if system not in retained:
            pruned[system] = stations
            continue
        kept_stations: dict[str, dict[str, list[dict]]] = {}
        for station_id, buckets in (stations or {}).items():
            kept_buckets = {
                direction: [a for a in arrs if a.get("arrival") is None or a["arrival"] >= now]
                for direction, arrs in buckets.items()
            }
            kept_buckets = {d: arrs for d, arrs in kept_buckets.items() if arrs}
            if kept_buckets:
                kept_stations[station_id] = kept_buckets
        pruned[system] = kept_stations
    return pruned
