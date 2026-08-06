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
from feeds import active_alert_feeds

# Log through the "main" logger (not __name__) so records and main.py's logging
# config are unchanged by the split, the same discipline the feeds package uses.
logger = logging.getLogger("main")

# Upstream-staleness threshold: how far the feed's CONTENT time (MTA's clock)
# may lag the poll time (this server's clock) before the data is considered
# stale — used by /healthz and reported via /api/status. Computed from two
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
# observed peak header lag plus one 20s poll interval is a 43s worst honest age,
# leaving barely 2x headroom here. The full working, including why the probe's
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
    # browser clock — see _feed_age and FEED_STALE_AFTER_S.
    return {"data": None, "fetched_at": None, "feed_timestamp": None, "error": None}


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
    # flags a system failing this poll. Keyed by the same alert systems this process
    # actually polls (feeds.active_alert_feeds).
    # On a TOTAL outage every system is marked, so degraded_systems is truthful then
    # too; it is not a partial-outage-only signal.
    return {
        "alerts": None,
        "fetched_at": None,
        "error": None,
        "active": 0,
        "suppressed": 0,
        "health": {
            system: {"fresh_at": None, "retained_since": None, "last_error": None}
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


def _sanitize_upstream(exc: BaseException) -> str:
    """Strip URLs from upstream error text before recording it: httpx error
    strings embed the full request URL, which for the bus feed includes the
    API key query parameter, and recorded details are served by /api/status
    and the never-filled error paths."""
    return _URL_RE.sub("<feed url>", str(exc))


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
        for system, station_map in arrivals_by_system.items()
        if station_id in (station_map or {}) and system in systems
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
