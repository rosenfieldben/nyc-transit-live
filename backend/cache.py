"""Feed-cache primitives shared by the pollers and the route handlers.

The in-memory cache entry shapes, the warming/last-known-on-failure serving
contract, the freshness threshold, and the upstream-error sanitizer. A leaf
module: it imports nothing from main/pollers/routes, so everything else can
import it without a cycle. _serve_cached takes the app explicitly (rather than
closing over a module global) precisely so it can live here.
"""

from __future__ import annotations

import logging
import re
import time

from fastapi import HTTPException, Response

import env_seams
from feeds import active_alert_feeds, iter_rows

# Log through the "main" logger (not __name__) so records and main.py's logging
# config are unchanged by the split, the same discipline the feeds package uses.
logger = logging.getLogger("main")

# Upstream-staleness threshold: how far the feed's CONTENT time (MTA's clock)
# may lag the poll time (this server's clock) before the data is considered
# stale. Used by /healthz and reported via /api/status. Computed from two
# server-captured timestamps (fetched_at - feed_timestamp), so the browser
# clock is never involved; the frontend mirrors this in helpers.js.
FEED_STALE_AFTER_S = 90
# NOT overridable, unlike the frontend constant it is mirrored by (C6 gave that one
# a flag-gated query override so the contract tier can watch the page dim without
# waiting 90s). The two can therefore disagree, but only inside a browser that
# asked for it: this value still governs /healthz and /api/status for everyone.
#
# NJ TRANSIT IS THE TIGHTEST-MARGIN SYSTEM UNDER THIS SHARED BUDGET (15b), so if
# this number ever moves, that is the derivation to re-check first: 23s worst
# observed peak header lag plus a healthy poll gap is a 45s typical worst age, and
# a cycle that burns a full REFRESH_DEADLINE_S takes that to 88s, which leaves
# nothing. Crossing it reports STALE rather than serving anything wrong, so the
# edge is noisy rather than dangerous. The full working, including why the probe's
# overnight figures must be doubled before they are compared against anything, is
# at THE FRESHNESS BUDGET, DERIVED in feeds/njt.py. It is not repeated here so the
# two cannot drift into disagreeing versions.

# How long ONE failed subsystem's data is carried forward inside an aggregate
# envelope before it is dropped (C2). Ten minutes, and the reasoning is the same
# shape as the alerts retention cap but at a much shorter horizon because vehicle
# positions decay faster than service alerts: a ten-minute-old train position,
# rendered AS stale, is honest context a rider can use, while an hour-old one is a
# ghost. Past this the data goes and only the system's SystemFreshness block
# remains, still reporting the outage. Absence plus an explanation beats a
# confident wrong position.
# Overridable (C6): the contract tier compresses every cadence so a scenario that
# has to outlive a threshold finishes in seconds instead of minutes. Unset, this is
# the prior literal.
FEED_RETENTION_MAX_S = env_seams.seconds("FEED_RETENTION_MAX_S", 600)

# WHETHER A FAILED SUBSYSTEM'S DATA IS ACTUALLY CARRIED FORWARD.
#
# ON as of C2 PR2, and THIS FLAG MUST ONLY EVER MOVE IN THE SAME COMMIT AS THE
# CLIENT-SIDE STALE RENDERING. Retention is honest only when the retained data is
# drawn AS stale (dimmed markers, an "as of Xm ago" popup line, a status line that
# names the degraded system, and a glide that freezes instead of dead-reckoning a
# dead feed). PR1 shipped the per-system blocks with this off precisely because
# nothing read them yet: turning it on then would have put a failed group's trains
# on the map at full opacity with no staleness marker, trading honest absence for
# ghost trains, which is worse than the defect being fixed. PR2 flips it here and
# lands that rendering in the same commit, so the two can never be separated by a
# revert of one half. The retention cap in FEED_RETENTION_MAX_S above states the
# same condition ("rendered AS stale") as its own justification.
#
# If a future change ever needs to disable the dimming, disable retention with it.
# The e2e spec "C2b" pins the pairing: the first frame retained data appears, it is
# already dimmed.
FEED_RETENTION_ENABLED = True


# THE THREE TIMESTAMPS (the freshness contract, canonical description; the
# models and the frontend reference this by name rather than restating it):
#
#   feed_timestamp = the UPSTREAM GENERATION time (the MTA/GTFS/bridge clock):
#       when the provider produced this content. Stored in the cache entry.
#   fetched_at     = OUR LAST SUCCESSFUL POLL time (this server's clock): when we
#       last decoded a good response from the provider. Stored in the cache entry;
#       a failed poll keeps the previous value (last-known-on-failure).
#   served_at      = THIS RESPONSE's time (this server's clock): stamped fresh in
#       the handler at response build, and DELIBERATELY NOT stored in the cache
#       entry, because its whole job is to keep moving while fetched_at holds.
#
# Each GAP is a different failure's signature, all comparing same-clock pairs so
# no browser skew enters:
#   fetched_at - feed_timestamp  = upstream lag (the provider's own feed stalled).
#   served_at  - fetched_at      = server cache age (OUR poller stopped; we keep
#                                  serving frozen last-known data). This gap is the
#                                  one the frontend was previously blind to: on a
#                                  first load against an already-stale cache it read
#                                  ~zero, so stale looked fresh. served_at makes it
#                                  explicit and skew-free.
#
# THE PER-SYSTEM RULE (C2), for the AGGREGATE endpoints only (subways: 8 feed
# groups; railroads: LIRR + MNR; alerts: 5 systems). Those fan out over several
# upstream systems, and a partial failure is still a SUCCESSFUL poll, so:
#
#   an aggregate's top-level fetched_at means "THIS POLL RAN".
#   each system's own fetched_at means "THIS SYSTEM'S DATA IS THIS OLD".
#
# They are equal on a healthy poll and DIVERGE EXACTLY WHEN SOMETHING IS WRONG,
# which is what makes the pair informative. Before C2 only the aggregate existed,
# so one failed subway group advanced the same timestamp as the seven healthy
# ones and its riders were served retained data wearing a fresh clock (or, worse,
# no data and no explanation). The per-system block is models.SystemFreshness and
# travels IN the data envelope, because the client that needs it never fetches
# /api/status. Anything reading an aggregate fetched_at as DATA freshness now has
# a truthful alternative and should be using it.


def _feed_age(entry: dict) -> float | None:
    """Seconds the feed content lagged the poll, or None if not computable.
    Both inputs are server-captured at poll time, so this is clock-skew free."""
    if entry["fetched_at"] is None or entry["feed_timestamp"] is None:
        return None
    return entry["fetched_at"] - entry["feed_timestamp"]


def _fresh_entry() -> dict:
    # fetched_at = this server's poll time; feed_timestamp = the feed's content
    # time (MTA's clock). Both are stored so freshness can be judged without the
    # browser clock (see _feed_age and FEED_STALE_AFTER_S).
    return {"data": None, "fetched_at": None, "feed_timestamp": None, "error": None}


def fresh_alert_health() -> dict:
    """One alert system's health block, at its pre-poll zero.

    TWO SITES SEED THIS and they drifted the moment a field was added: the cache
    builds the map once, and pollers._reconcile_alert_health seeds a system that
    gains credentials in-process. Both call this now, so a key added here reaches
    both or neither.

    content_at is this system's own CONTENT time, the clock the alerts decoder reads
    off each feed's header (contract 6.1). Seeded null like fresh_at, because nothing
    has decoded yet.
    """
    return {
        "fresh_at": None,
        "content_at": None,
        "retained_since": None,
        "last_error": None,
        "served_empty": None,
    }


def _fresh_alerts_entry() -> dict:
    # alerts = the active-alert index (None until the first successful poll, [] once
    # a poll decoded zero active alerts); active/suppressed are the counts /api/status
    # reports. Same last-known-on-failure rule as the feed cache: a failed poll keeps
    # the last index and its fetched_at, and only a poll that decoded ever ADDS to it.
    # A failed poll can still SHRINK the content, though, because the expiry re-filter
    # and the retention cap re-run over the existing index: an alert whose own ends_at
    # passed during an outage drops rather than being pinned alive by the outage. See
    # pollers._refresh_alerts, which owns that rule and explains why the shrinkage is
    # honesty rather than data loss.
    # health = per-system freshness, so an outage (one feed down, or all of them) is
    # visible instead of silently thinning the index: fresh_at is the last decode,
    # retained_since marks a system whose alerts are being carried forward from a
    # down feed (null when fresh or once the retention cap drops them), last_error
    # flags a system failing this poll AND now says why, served_empty carries the one
    # SUCCESS worth a sentence (NJ Transit's zero-byte 200, which means no active
    # alerts rather than no feed; see feeds.njt_alerts_served_empty). Keyed by the
    # same alert systems this process actually polls (feeds.active_alert_feeds).
    # On a TOTAL outage every system is marked, so degraded_systems is truthful then
    # too; it is not a partial-outage-only signal.
    return {
        "alerts": None,
        "fetched_at": None,
        "error": None,
        "active": 0,
        "suppressed": 0,
        "health": {
            system: fresh_alert_health()
            # THE ACTIVE SET (15b): an unconfigured NJ Transit is not seeded here at
            # all, so it cannot sit in degraded_systems forever on a deployment that
            # does not run it. Same single source the gather and the total-outage
            # path read (feeds.active_alert_feeds).
            for system in active_alert_feeds()
        },
    }


def _note_failure(entry: dict, status: int, detail: str, log: bool = True) -> None:
    """Record why the latest poll failed. Last-known data keeps being served;
    the error only surfaces to clients while the cache has never been filled.
    log=False suppresses the warning for an EXPECTED, recurring condition (the
    subway warming path notes a 503 every poll while static loads, but the single
    transition warning belongs to _set_static_status, not every 20s poll)."""
    entry["error"] = {"status": status, "detail": detail}
    if log:
        logger.warning("feed poll failed (%d): %s", status, detail)


_URL_RE = re.compile(r"https?://\S+")


def _sanitize_detail(detail: str) -> str:
    """The same URL scrub as _sanitize_upstream, for a reason that arrives ALREADY
    as a string rather than as a live exception.

    The alerts fetch is the caller that needs this: it catches each feed's failure
    at the gather and hands the poller a per-feed reason it has already turned into
    text, so there is no exception left to sanitize by the time the poller records
    it against a system's health. One regex, two entry points, so a detail cannot
    reach /api/status scrubbed by one rule and not the other."""
    return _URL_RE.sub("<feed url>", detail)


def _sanitize_upstream(exc: BaseException) -> str:
    """Strip URLs from upstream error text before recording it: httpx error
    strings embed the full request URL, which for the bus feed includes the
    API key query parameter, and recorded details are served by /api/status
    and the never-filled error paths."""
    return _sanitize_detail(str(exc))


def _serve_cached(
    app, name: str, response: Response, data_key: str = "data", with_systems: bool = False
) -> dict:
    """Serve {fetched_at, feed_timestamp, served_at, <data_key>} from the cache.
    Stale-but-present data is still served; the frontend judges staleness from the
    fetched_at / feed_timestamp pair (upstream lag) plus the served_at / fetched_at
    pair (server cache age), so a stuck poller serving frozen data still surfaces.
    served_at is stamped HERE at response build (never stored in the cache entry);
    see THE THREE TIMESTAMPS above. Errors only reach clients while the cache has
    never successfully filled.

    data_key names the payload field in the envelope: the MTA feeds use "data"
    (the default), the PATH feed uses "trains" (its PathFeed model). Keeping the
    envelope/warming/never-filled contract in one place means a change here
    (a header, a reworded 503) reaches every feed endpoint, PATH included.

    The app is passed in (not a module global) so this can live in the leaf cache
    module; the route handlers hand it request.app and their own response.

    Cache-Control no-store: a live feed response must never be reused from a shared
    or browser heuristic cache. A cached copy is both a staleness lie (its served_at
    would freeze at the moment it was stored) and calibration poison (the frontend
    calibrates its clock skew off served_at, so a replayed old served_at would skew
    every countdown). The warming/static no-cache and static max-age schemes live on
    the disjoint static endpoints and are untouched.
    """
    entry = app.state.feed_cache[name]
    if entry["data"] is not None:
        response.headers["Cache-Control"] = "no-store"
        body = {
            "fetched_at": entry["fetched_at"],
            "feed_timestamp": entry["feed_timestamp"],
            "served_at": time.time(),
            data_key: entry["data"],
        }
        # OPT-IN, not automatic: only the AGGREGATE feeds have subsystems. Buses,
        # PATH and ferry are each a single upstream, so their top-level fetched_at
        # already means what a per-system block would say, and adding an empty or
        # one-entry block to their envelopes would be noise the client has to
        # special-case. See THE PER-SYSTEM RULE above.
        if with_systems:
            body["systems"] = entry.get("systems")
        return body
    if entry["error"]:
        raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
    raise HTTPException(
        status_code=503, detail="Feed cache is warming up; try again in a few seconds."
    )


def _require_filled_cache(entry: dict) -> None:
    """Warming gate shared by the arrivals endpoints: until the feed's cache
    has filled once there is no per-station index worth serving, so surface
    the recorded upstream error when there is one, else the generic warming
    503. Same contract _serve_cached keeps for the feed endpoints; the three
    arrivals endpoints each carried an identical inline copy until the
    13d-era cleanup."""
    if entry["data"] is None:
        if entry["error"]:
            raise HTTPException(entry["error"]["status"], entry["error"]["detail"])
        raise HTTPException(
            status_code=503, detail="Feed cache is warming up; try again in a few seconds."
        )


def _static_endpoint_ready(status: str, response: Response, warming_detail: str) -> bool:
    """Shared warming behavior for the static-derived (decorative) endpoints.

    - loading: raise a 503 (the data is coming; do not cache anything).
    - ready: set the long cache header and return True so the caller serves data.
    - failed (retrying): set no-cache and return False so the caller serves [] that
      a browser will NOT cache, so a later retry success is not masked for an hour.
    Returning [] under a max-age here (the old behavior) was the cold-start bug:
    a browser could cache an empty payload for the whole warmup.

    ANY OTHER STATE TAKES THE FAILED ARM, which is what NJ Transit's fourth state
    ("not-configured", 15a) wants: it is not loading, so a 503 promising data would
    lie, and it is not ready, so caching an empty list for an hour would pin that
    lie in the browser. Serving [] under no-cache says "nothing here, ask again"
    without asserting why; the why is on /api/status, where an operator reads it.
    """
    if status == "loading":
        raise HTTPException(status_code=503, detail=warming_detail)
    if status == "ready":
        response.headers["Cache-Control"] = "public, max-age=3600"
        return True
    response.headers["Cache-Control"] = "no-cache"  # failed: never cache the empty
    return False


def _oldest_row_observed_at(rows) -> float | None:
    """The oldest observation time among the arrival rows actually served.

    THE THIRD SELECTOR, for the two endpoints whose envelope has no per-system block
    to read: PATH and the ferry are single-feed sources and no poller ever writes
    them a `systems` map. Reading the cache entry's own feed_timestamp instead would
    be wrong on both, and differently wrong on each: PATH's is the bridge's WRITE
    time, which advances on every regeneration whether or not anything upstream
    moved, and the ferry's is the VehiclePositions header, which dates the BOATS and
    not the dock predictions these rows came from.

    So the answer is taken from the rows themselves, which the decoders now date.
    Same rule as the other two, applied one level down: the worst of the parts, and
    None if any part cannot be dated, because a row with no clock must not be spoken
    for by its neighbours.

    A ROW IS TOLD FROM A CONTAINER STRUCTURALLY, not by looking for the key, and the
    walk that does it lives in feeds.iter_rows so the retention stamp cannot drift
    from this. The first version asked "does this dict carry observed_at", which
    quietly made the one case the rule exists for impossible: a row that LACKS the
    key was treated as a container, walked into, and contributed nothing, so its
    neighbours dated the board on its behalf. An undated row now forces None.

    `rows` is any nesting of dicts and lists the two callers use: {bucket: [row]} for
    PATH and {route: [row]} for the ferry. It is deliberately not used by NJ Transit
    or the railroads, whose envelopes carry a per-system block to read instead.

    An EMPTY board returns None, and that is "nothing here to date" rather than
    "cannot be dated". The two are distinguishable in the payload a client actually
    holds: an empty directions/routes map beside a null clock is the first, a
    populated one beside a null clock is the second. That is why this differs from
    _oldest_contributing_fetched_at, which falls back to the aggregate: a poll time
    is a fact about US and exists whether or not any row does, while a content time
    is a fact about rows that are not there.
    """
    found = [row.get("observed_at") for row in iter_rows(rows)]
    if not found or any(value is None for value in found):
        return None
    return min(value for value in found if value is not None)


def _system_content_at(entry: dict, system: str) -> float | None:
    """One system's own CONTENT time out of an aggregate cache entry (contract 6.1).

    The single-system counterpart of _oldest_contributing_content_at, for the four
    arrivals endpoints that serve exactly one system and therefore need no union
    rule. No fallback to the aggregate, unlike _system_fetched_at below: that
    fallback exists so an endpoint never returns null where it used to return a
    number, and this field never returned a number before. A system with no block
    yet, or one whose header is not a usable freshness signal at all (Metro-North),
    reports None, which is the honest answer and the one the model declares.
    """
    return ((entry.get("systems") or {}).get(system) or {}).get("feed_timestamp")


def _system_freshness_block(entry: dict, system: str) -> dict | None:
    """One system's per-system block, keyed by its own name, or None before the
    first poll wrote one. The single-system form of _contributing_freshness."""
    block = (entry.get("systems") or {}).get(system)
    return {system: block} if block is not None else None


def _system_fetched_at(entry: dict, system: str) -> float | None:
    """One system's own poll time out of an aggregate cache entry, falling back to
    the aggregate's when no per-system block has been written yet (C2).

    Used by the per-station arrivals endpoints, which serve ONE system's data and
    must therefore date it with that system's clock. Stamping the aggregate there
    was the defect: a healthy sibling keeps the aggregate advancing, so a retained
    system's arrivals were served wearing a fresh timestamp and the client had no
    way to tell. The fallback keeps the pre-C2 answer for an entry seeded directly
    or read before the first poll, so the endpoint never returns null where it
    previously returned a number.
    """
    block = (entry.get("systems") or {}).get(system)
    if block is None:
        return entry["fetched_at"]
    return block["fetched_at"]


def _contributing_systems(entry: dict, arrivals_by_system: dict, station_id: str) -> list[str]:
    """The systems actually contributing arrivals at one station, in sorted order.

    Shared by the two selectors below so they can never disagree about WHO is being
    asked. A group with no arrivals at this station does not participate: a down SIR
    feed must not age a Manhattan station's popup it was never going to appear in.

    MEMBERSHIP IS THE PER-GROUP INDEX, NOT THE SERVED PAYLOAD, and the difference is
    deliberate. combine_group_arrivals dedups trips across groups and _trim_arrivals
    caps each bucket, so a group can have rows at this station and still have none of
    them survive into the response. It keeps its vote anyway: the alternative is
    letting the survivors vouch for a group whose data is still behind them, and the
    only safe direction for a union clock is the one that cannot overstate freshness.
    The visible consequence is that an envelope may report a time OLDER than every row
    it carries, which the acceptance test asserts rather than tolerates.
    """
    systems = entry.get("systems") or {}
    return sorted(
        system
        for system, station_map in arrivals_by_system.items()
        if station_id in (station_map or {}) and system in systems
    )


def _oldest_contributing_content_at(
    entry: dict, arrivals_by_system: dict, station_id: str
) -> float | None:
    """The oldest CONTENT time among the systems contributing arrivals at a station.

    THE SIBLING OF _oldest_contributing_fetched_at, and the reason F03 needed one.
    That function answers "how long ago did we last POLL the feeds behind this
    board", which a repeatedly successful fetch of stale bytes keeps answering
    "one second ago" forever. This one answers "how old is what they SENT", which is
    the question a countdown depends on and the one no arrivals payload could
    express.

    THE SAME THREE RULES, deliberately, because two selectors over one contributor
    set that disagreed about which one wins would be worse than either alone:

      1. THE WORST CONTRIBUTOR ANSWERS. For a union there is no single clock, and
         reporting the newest would let a fresh group vouch for a stale one sharing
         the platform.
      2. A GROUP CONTRIBUTING NOTHING HERE DOES NOT PARTICIPATE.
      3. ONE CONTRIBUTOR THAT CANNOT BE DATED MAKES THE ANSWER None, rather than
         letting the others speak for it. The difference from its sibling is what
         that means: there, never having decoded; here, either that or a system
         whose header is not a usable freshness signal at all (Metro-North). Both
         are "this cannot be dated", and both must refuse to be averaged away.

    There is no aggregate fallback. The envelope's feed_timestamp is a min() over
    every system that decoded, including ones with no arrivals at this station, so
    using it here would reintroduce exactly the overstatement rule 2 exists to
    prevent.
    """
    systems = entry.get("systems") or {}
    contributing = [
        systems[system].get("feed_timestamp")
        for system in _contributing_systems(entry, arrivals_by_system, station_id)
    ]
    if not contributing or any(value is None for value in contributing):
        return None
    return min(value for value in contributing if value is not None)


def _contributing_freshness(entry: dict, arrivals_by_system: dict, station_id: str) -> dict | None:
    """The per-system blocks of just the systems feeding this station.

    "Other healthy contributors remain distinguishable" is the audit's acceptance
    clause, and it is a fact about a FIELD only if the board can name which
    contributor is behind. Publishing every system's block would answer a question
    the rider did not ask (a down SIR group means nothing at a Manhattan station);
    publishing only the contributors answers theirs.
    """
    systems = entry.get("systems") or {}
    names = _contributing_systems(entry, arrivals_by_system, station_id)
    return {name: systems[name] for name in names} or None


def _oldest_contributing_fetched_at(
    entry: dict, arrivals_by_system: dict, station_id: str
) -> float | None:
    """The oldest poll time among the systems actually contributing arrivals at one
    station, falling back to the aggregate's (C2).

    For a UNION of several systems' data there is no single per-system clock to
    report, and reporting the newest (or the aggregate, which tracks the newest)
    would let one fresh group vouch for a stale one sharing the same platform. The
    worst contributor is the only answer that cannot overstate freshness.

    A group with no arrivals at this station does not participate: a down SIR feed
    must not age a Manhattan station's popup it was never going to appear in.
    """
    systems = entry.get("systems") or {}
    contributing = [
        systems[system]["fetched_at"]
        for system in _contributing_systems(entry, arrivals_by_system, station_id)
    ]
    usable = [ts for ts in contributing if ts is not None]
    if not usable:
        # Either no per-system data yet (pre-first-poll, or a directly seeded entry)
        # or every contributor has never decoded. The aggregate is the pre-C2 answer
        # and keeps the endpoint from regressing to null.
        return entry["fetched_at"]
    if len(usable) < len(contributing):
        # A contributor exists that has NEVER decoded, so its data cannot be dated.
        # Do not let the others speak for it.
        return None
    return min(usable)
