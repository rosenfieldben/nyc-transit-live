"""Service alerts: the GTFS-RT alert feeds, the active-now window logic, the
per-alert decode, the fetch aggregation, and the per-system retention merge that
carries a down feed's alerts forward across a partial outage.

Five of the six feeds are keyless GETs. NJ Transit's (15b) is a POST behind the
token door, and is the reason this module now distinguishes three feed sets:
ALERT_FEED_URLS (every feed that exists), active_alert_feeds() (the ones THIS
process will poll, which drops an unconfigured NJT), and KEYLESS_ALERT_FEEDS (the
ones a bare GET can reach)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import NamedTuple

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

import env_seams
import njt_auth
from feeds import njt as njt_feed
from feeds.shared import _RAILROAD_BASE, logger, parse_feed

# Keyless GTFS-RT Service Alerts feeds. The four MTA feeds are camsys-published on
# the same %2F-encoded base as the railroad feeds. Keyed by the system this app
# serves so each decoded alert can be tagged with its system. Deliberately NOT
# camsys%2Fall-alerts: that bundle mixes in agencies this app does not map
# (Access-A-Ride, bridges/tunnels, outer systems), which would surface alerts with
# no marker or route to attach to.
#
# "ferry" is a DIFFERENT host and publisher: NYC Ferry's Connexionz GTFS-RT alert
# endpoint (https, the same host and scheme as the 14a static and 14b realtime ferry
# feeds), not camsys. It slots in here because the decode below is pure GTFS-RT with
# no agency-specific handling, and the gather/retention/health machinery is keyed
# generically by system, so a fifth feed needs only this entry. Verified 2026-07-09
# as a valid ServiceAlert feed; it returns application/x-protobuf directly (no
# redirect), so the generic fetch handles it. A decode failure marks only "ferry"
# degraded (per-system retention), it never breaks the poll.
# Two overridable seams (C6), because the five feeds sit on two hosts. ALERTS_RT_BASE
# is its own variable rather than the shared MTA Dataservice constant so a contract
# scenario can take the alert feeds down while the railroad realtime feeds keep
# advancing, which is the exact partial-outage shape C1 and C2 are about.
ALERTS_RT_BASE = env_seams.url("ALERTS_RT_BASE", _RAILROAD_BASE)
FERRY_ALERTS_URL = env_seams.url(
    "FERRY_ALERTS_URL",
    "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx/alert",
)
#
# "njt" is the SIXTH feed and the first that is neither keyless nor a GET (15b).
# Every RailData endpoint is POST multipart/form-data with a token as a form
# field, so the gather below dispatches on the key; everything downstream (the
# per-system retention, the health map, degraded_systems) is keyed generically by
# system and needs no other change.
#
# WHAT THE 2026-08-05 RUSH PROBE FOUND IN THIS FEED, recorded here because each
# fact changes how a consumer must read it:
#
#   - active_period IS A 24-HOUR DISPLAY TTL, NOT AN EVENT WINDOW. An alert about
#     next week legitimately vanishes from the feed within a day. Upstream expiry
#     is honored AS-IS by _alert_window_status: that is NJ Transit's editorial
#     choice recorded, not ours to extend. Do not "fix" a disappearing alert by
#     widening the window here; it would put text on a rider's screen that the
#     agency has stopped publishing.
#   - THE MAJORITY ARE STOP-SCOPED: 162 of 263 at peak, joining stops.txt cleanly.
#     So informed_entity stop scoping MUST survive serialization end to end, which
#     is what 15c's station join reads. models.Alert.stops carries it and
#     test_feeds_alerts pins it; nothing may narrow that to routes-only.
#   - route_type ARRIVES AS 2 HERE AND 113 IN THE STATIC. Tolerated: neither value
#     is read by any join, and the discrepancy is upstream's.
#   - header_text DUPLICATES description_text. Both are decoded (the shape is
#     generic) and a renderer should show ONE, not both.
#   - THE CONTENT CHANGES EVERY POLL. Any hashing for change detection must
#     exclude the header, which is the C1 banner rule reaffirmed rather than a new
#     one.
#
# AND WHAT PRODUCTION FOUND ON 2026-09-07, which the rush probe could not see
# because it never ran on a quiet day: THIS FEED ANSWERS HTTP 200 WITH A ZERO-BYTE
# BODY WHEN THERE ARE NO ACTIVE RAIL ALERTS. It is a served state, not an outage,
# and njt_alerts_served_empty below carries the rule, the evidence and the
# ambiguity the rule accepts. Note that it is NOT the same shape as this producer's
# TripUpdates feed overnight, which is a thirteen-byte header-only message (decoder
# law 6 in feeds/njt.py); this one has no header at all.
ALERT_FEED_URLS = {
    "subway": ALERTS_RT_BASE + "/camsys%2Fsubway-alerts",
    "bus": ALERTS_RT_BASE + "/camsys%2Fbus-alerts",
    "LIRR": ALERTS_RT_BASE + "/camsys%2Flirr-alerts",
    "MNR": ALERTS_RT_BASE + "/camsys%2Fmnr-alerts",
    "ferry": FERRY_ALERTS_URL,
    "njt": njt_feed.NJT_ALERTS_URL,
}

# The system key for the one feed that is POSTed rather than GETed.
NJT_ALERT_SYSTEM = "njt"

# The feeds a bare keyless GET can reach, derived rather than re-listed so a
# seventh feed lands here automatically.
#
# WHO READS THIS: the contract monitor's alerts-realtime check. Its injected
# fetcher GETs by default, and the NJ Transit endpoint answers a GET with nothing
# usable, so pointing that check at the full table would have it report a hard
# FAIL against a feed it was never able to speak to. The monitor checks the NJT
# alerts feed in its njt-realtime check instead, which is where the run's single
# minted token lives. This is a deliberate split, and the monitor's tests assert
# both halves of it (njt absent from this set, present in the full one) so it
# cannot decay into an accidental gap.
KEYLESS_ALERT_FEEDS = {key: url for key, url in ALERT_FEED_URLS.items() if key != NJT_ALERT_SYSTEM}


def active_alert_feeds(env: dict[str, str] | None = None) -> dict[str, str]:
    """The alert feeds THIS PROCESS will actually poll.

    NJ Transit is dropped entirely when it has no credentials, and "entirely" is
    the point: not fetched, not counted in the totals, and NOT SEEDED INTO THE
    HEALTH MAP. Keeping it as a permanently-failing system would put "njt" in
    degraded_systems forever on every deployment that does not run NJ Transit, and
    a banner that is always degraded is one nobody reads. An unconfigured system
    is not a degraded one; 15a made that distinction for the static group and this
    is the same distinction for alerts.

    ONE FUNCTION, THREE CALLERS, and they must agree or the honesty breaks in a
    way that is hard to see: the gather here, the health seeding in
    cache._fresh_alerts_entry, and the total-outage failed set in
    pollers._refresh_alerts. If the health map were seeded from the full table
    while the gather used this one, njt would report last_error None forever and
    still never be fresh.
    """
    feeds = dict(ALERT_FEED_URLS)
    if not njt_auth.is_configured(env):
        feeds.pop(NJT_ALERT_SYSTEM, None)
    return feeds


# ---- Service alerts ----

_ALERT_EFFECT = gtfs_realtime_pb2.Alert.Effect


_ALERT_CAUSE = gtfs_realtime_pb2.Alert.Cause


def _alert_window_status(
    periods: list[tuple[int | None, int | None]], now: float
) -> tuple[str, int | None, int | None]:
    """Classify an alert's active_period list against `now`, returning
    (status, starts_at, ends_at):

      "active": some period covers now. starts_at is the earliest covering period's
                start; ends_at is the EFFECTIVE end for expiry (see below).
      "future": no period covers now but at least one starts after now (planned work)
      "ended":  no period covers now and none is still upcoming (all elapsed)

    Open bounds follow the feed facts: an EMPTY period list means the alert is
    always active (no window constraint); a None start is open on the left; a None
    end (the decode maps an end of 0 or unset to None) is open-ended. A period
    covers now on the half-open interval [start, end), matching the GTFS-RT spec.
    "future" is split out from "ended" because only not-yet-active planned work is
    worth counting for /api/status; a fully elapsed alert is just gone.

    THE EFFECTIVE END IS THE LATEST end among the periods COVERING now, and None when
    any of those is open-ended. It used to be the end of the EARLIEST-STARTING covering
    period, which expired an alert prematurely whenever periods OVERLAP: given
    [(0, 100), (50, 500)] at now=60 both cover, the earliest-started is (0, 100), so
    ends_at came back 100 and every downstream expiry check (the retention re-filter,
    the client's sort) treated a live alert as finished at 100 instead of 500. Taking
    the latest end among covering periods fixes exactly that.

    THE MAX IS OVER THE COVERING SET, NOT OVER EVERY NOT-YET-ENDED PERIOD, which is a
    correction to this function's first attempt at the fix. Including periods that have
    not STARTED reached further than the bug being fixed and broke three things:

      a. It overshot the window actually in effect. On real captured data
         (alert lmm:planned_work:32622 in tests/fixtures/alerts_mnr.pb, five periods)
         it reported an end 24 DAYS past the window the alert was actually serving,
         and ends_at is a PUBLIC field the client sorts and displays.
      b. An open-ended period that had not started yet made ends_at null outright, so
         guard 1 of merge_alert_generations could never expire the alert and
         compareAlerts promoted a nearly-finished alert above genuinely indefinite
         ones in every popup and the banner.
      c. It carried an alert through the GAP between two periods, where a poll that
         decoded would have classified it "future" and suppressed it, so during an
         outage riders could see a weekend service change presented as in effect on a
         Wednesday.

    Dropping an alert at the end of its current period and letting the next decode
    bring it back when its next period opens is both simpler and what the decode
    already does; a retained alert should not outlive the window a live poll would
    have given it.
    """
    if not periods:
        return "active", None, None
    covering: list[tuple[int | None, int | None]] = []
    has_future = False
    for start, end in periods:
        started = start is None or now >= start
        not_ended = end is None or now < end
        if started and not_ended:
            covering.append((start, end))
        elif not started:
            has_future = True  # begins later: planned, not yet active
    if covering:
        # starts_at reports the EARLIEST covering start (the alert has been active
        # longest); an open start sorts first. The end is the LATEST covering end.
        covering.sort(key=lambda p: float("-inf") if p[0] is None else p[0])
        start = covering[0][0]
        ends = [end for _, end in covering]
        effective_end = (
            None if any(e is None for e in ends) else max(e for e in ends if e is not None)
        )
        return "active", start, effective_end
    return ("future", None, None) if has_future else ("ended", None, None)


def _translated(ts) -> str | None:
    """First English translation of a TranslatedString, else the first available,
    else None. The text is kept VERBATIM (subway alerts embed route tokens like
    [Q]); normalizing or stripping it is 12b's rendering concern, not the decode's."""
    translations = ts.translation
    if not translations:
        return None
    for tr in translations:
        if tr.language and tr.language.lower().startswith("en"):
            return tr.text
    return translations[0].text


def _enum_name(enum_wrapper, value: int) -> str:
    """GTFS-RT enum value to its name, falling back to the raw int as a string for
    a value newer than the bundled binding (rather than raising on an unknown)."""
    try:
        return enum_wrapper.Name(value)
    except ValueError:
        return str(value)


# What /api/status and the contract monitor say about a served-empty NJ Transit
# alerts feed, in the words an operator reads. One literal, three readers (the
# decode below, the poller's health write, the monitor's summary), so the three
# surfaces cannot describe the same state three ways.
NJT_ALERTS_SERVED_EMPTY_DETAIL = "served empty body: no active NJ Transit alerts"


def njt_alerts_served_empty(feed_key: str, raw: bytes) -> bool:
    """True for the ONE body this module reads as a served state rather than a
    failure: NJ Transit's alerts feed answering HTTP 200 with zero bytes.

    THE OBSERVATION, 2026-09-07. From 00:30 Eastern production never decoded this
    feed; it decoded once at about 16:33 and was empty again after. The contract
    monitor's own fetch at 17:35 saw the same zero-byte body, and njtransit.com's
    Travel Alerts page at that hour listed every rail line as "No current alerts or
    advisories". So the empty body is what NJ Transit publishes when it has no
    active rail alerts, and it is a different shape from the same producer's
    overnight TripUpdates feed, which is a thirteen-byte header-only message
    (decoder law 6) that parse_feed already accepts. The alerts feed's empty form
    carries no header at all, which is precisely the shape parse_feed rule 1
    rejects.

    THE AMBIGUITY, STATED RATHER THAN HIDDEN. A DEAD ENDPOINT COULD SEND THESE SAME
    BYTES. Zero bytes behind a 200 is also what a misconfigured gateway, a truncated
    response or a retired route would produce, and nothing in the body distinguishes
    the two: this rule cannot tell "no alerts" from "no feed". The price is accepted
    deliberately, because the alternative is worse in the case that actually happens
    every night: treating it as undecodable fails the alerts poller and the
    contract monitor on every quiet night, which is a permanent red that gets muted,
    and a muted monitor sees nothing at all. What still catches a dead endpoint is
    everything AROUND this rule, none of which is relaxed: a non-200 still fails, a
    transport error still fails, and ONE BYTE OF GARBAGE IS STILL A FAILURE.

    NARROW BY CONSTRUCTION, in both directions:
      * feed_key, so this can never widen to another system. A zero-byte body from
        the subway, bus, LIRR, MNR or ferry alert feed stays the C3 failure
        parse_feed made it, and a test pins that.
      * `not raw`, so this can never widen to any other body. A one-byte body goes
        to parse_feed and fails there, and a test pins that too.
    """
    return feed_key == NJT_ALERT_SYSTEM and not raw


def _decode_alerts(raw: bytes, feed_key: str, now: float) -> tuple[list[dict], int]:
    """Decode one service-alerts feed into (active alerts, suppressed_count).

    Returns one plain dict per alert that is ACTIVE at `now`:
      {id, system, header, description, effect, cause, routes, stops,
       starts_at, ends_at}
    where routes/stops are the informed_entity selectors deduped in first-seen
    order (an alert's informed_entity list mixes route-only, stop-only, and
    both-carrying selectors, each with an agency_id we do not need to keep here),
    and starts_at/ends_at come from the period covering now (ends_at None when
    open-ended). Subway stop selectors are PARENT-STATION ids (e.g. "R20", "245"),
    the same id space as the static station index, so 12b can join them directly.

    Not-yet-active planned work (a "future" window) is excluded from the list but
    counted into suppressed_count, so /api/status can report how much upcoming work
    is being held back; fully elapsed alerts are dropped and not counted. `now` is
    frozen by the golden test for determinism.

    NJ TRANSIT'S SERVED-EMPTY BODY DECODES AS ZERO ALERTS rather than raising, and
    njt_alerts_served_empty carries the whole rule and the ambiguity it accepts.
    This is the only place the rule is applied, so reverting the two lines below is
    the mutation that must kill a test: a caller that WANTS to report the state
    separately (the poller, the monitor) asks the predicate, and gets the same
    answer this does.
    """
    if njt_alerts_served_empty(feed_key, raw):
        return [], 0
    # parse_feed rejects an empty or malformed body (C3); fetch_service_alerts
    # catches it per FEED, so one poisoned system joins the failed set and the
    # other four systems' alerts are unaffected.
    feed = parse_feed(raw)

    alerts: list[dict] = []
    suppressed = 0
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        # Map each TimeRange to (start, end); an end of 0 or unset is open-ended
        # (None), a missing start is open on the left (None).
        periods = [
            (
                tr.start if tr.HasField("start") else None,
                tr.end if (tr.HasField("end") and tr.end) else None,
            )
            for tr in alert.active_period
        ]
        status, starts_at, ends_at = _alert_window_status(periods, now)
        if status == "ended":
            continue
        if status == "future":
            suppressed += 1
            continue

        routes: list[str] = []
        stops: list[str] = []
        for sel in alert.informed_entity:
            if sel.route_id and sel.route_id not in routes:
                routes.append(sel.route_id)
            if sel.stop_id and sel.stop_id not in stops:
                stops.append(sel.stop_id)

        alerts.append(
            {
                "id": entity.id,
                "system": feed_key,
                "header": _translated(alert.header_text),
                "description": _translated(alert.description_text),
                "effect": _enum_name(_ALERT_EFFECT, alert.effect),
                "cause": _enum_name(_ALERT_CAUSE, alert.cause),
                "routes": routes,
                "stops": stops,
                "starts_at": starts_at,
                "ends_at": ends_at,
            }
        )
    return alerts, suppressed


# Whole-request deadline for ONE alert feed. Deliberately under the caller's
# REFRESH_DEADLINE_S (45s in pollers.py) so this fires first and a slow feed degrades
# to an ordinary per-feed failure, instead of the caller's backstop firing and having
# to call the whole poll a total outage. Generous next to a healthy fetch (these feeds
# answer in low single-digit seconds, the subway one being the largest at ~400 KB).
ALERT_FEED_DEADLINE_S = 20.0


# The exception families whose MESSAGE may be published, as opposed to only logged.
#
# EACH ONE IS ALREADY PUBLISHED IN THIS EXACT SHAPE BY A SIBLING REFRESHER's
# classified arm, which is the whole test for membership: _refresh_buses and
# _refresh_path record httpx's errors through _sanitize_upstream, and _refresh_njt
# has recorded njt_auth's composed errors the same way since 15b. Carrying the
# alerts feeds' own reasons to /api/status must REUSE that boundary, never widen
# it, so this list is the boundary written down rather than a new judgement.
#
# NJ TRANSIT'S UPSTREAM ERROR IS IN THE LIST WITH ITS EYES OPEN. njt_auth._quote
# puts up to 200 characters of a DATA endpoint's body into NjtUpstreamError, which
# is a decision that module made deliberately (bodies are the upstream's words, and
# the one body that is a secret, getToken's, is never quoted). _refresh_njt already
# serves that string on /api/status for the same failure of the same provider, so
# excluding it here would only make the two NJT blocks disagree about one outage.
_PUBLISHABLE_FEED_ERRORS: tuple[type[BaseException], ...] = (
    httpx.HTTPError,
    njt_auth.NjtNotConfigured,
    njt_auth.NjtAuthError,
    njt_auth.NjtUpstreamError,
)


def _describe_feed_error(exc: BaseException) -> str:
    """A readable reason for a failed feed, SAFE TO SERVE PUBLICLY.

    Not just str(exc), for two separate reasons.

    FIRST, str(TimeoutError()) IS THE EMPTY STRING, so a timed-out feed would
    otherwise be recorded and logged with no cause at all (the same trap R3 hit on
    the warmup path).

    SECOND, THE TYPE AND NOT THE MESSAGE FOR AN UNCLASSIFIED EXCEPTION. This string
    used to be logged and then replaced by a fixed marker on /api/status; it is now
    recorded against a system's health and served there, which puts it under the
    rule _total_refresh states in pollers.py for that surface: str(exc) of an
    arbitrary exception is arbitrary text, and arbitrary text can be a filesystem
    path, a config value or a chunk of a body nobody meant to republish. The
    sibling refreshers classify by TYPE in their except clauses and so never face
    this; the gather this serves catches everything (return_exceptions=True), so
    the classification has to happen here instead. Anything outside
    _PUBLISHABLE_FEED_ERRORS is a fault in this process rather than an upstream
    one, and the caller logs its traceback, which is where a fault of ours belongs.

    Every branch is guaranteed to produce something an operator can act on.
    """
    if isinstance(exc, TimeoutError):
        return f"no response within {ALERT_FEED_DEADLINE_S:.0f}s"
    if isinstance(exc, _PUBLISHABLE_FEED_ERRORS):
        return str(exc) or exc.__class__.__name__
    return f"internal error fetching this feed ({exc.__class__.__name__})"


class AlertsFetch(NamedTuple):
    """What ONE alerts poll produced, per feed rather than in aggregate.

    A NAMED TUPLE RATHER THAN A BARE ONE because the last two fields are the ones a
    caller is most likely to confuse, and both are keyed by system: `failed` names
    the feeds that did not decode AND WHY, `served_empty` names the feeds that
    decoded a served-empty body. They are disjoint by construction.

    `failed` CARRIES REASONS, not just keys, and that is a change from the sorted
    key list this used to return. The poller recorded a fixed "alert feed
    unavailable this poll" against every failed system because there was nothing
    else to record, so the actual cause (a connect error, a 502, a timeout, an
    undecodable body) reached the log and never /api/status, and diagnosing a
    partial alerts outage meant a log search. Iterating it still yields the system
    keys, which is all merge_alert_generations and the health rewrite ever needed.
    Insertion order is the sorted key order, so a log line built from it is stable.
    """

    alerts: list[dict]
    suppressed: int
    failed: dict[str, str]
    served_empty: list[str]


class AllAlertFeedsFailed(RuntimeError):
    """Every configured alert feed failed this poll.

    A RuntimeError SUBCLASS so pollers._refresh_alerts' existing `except
    (RuntimeError, TimeoutError)` catches it unchanged, and so does every test that
    raises a plain RuntimeError to stand in for a total outage. What it adds is
    `feed_errors`: the same per-feed reasons AlertsFetch.failed would have carried,
    so the total-outage path can write a real cause into each system's health block
    instead of the generic marker it had to use when the reasons died with the
    exception message.
    """

    def __init__(self, feed_errors: dict[str, str]) -> None:
        self.feed_errors = dict(feed_errors)
        super().__init__("All alert feeds failed: " + _join_feed_errors(self.feed_errors))


def _join_feed_errors(feed_errors: Mapping[str, str]) -> str:
    """One line naming every failed feed and its reason, for a log record and for
    the total-outage exception message. Written once so the two read alike."""
    return "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items())


async def fetch_service_alerts(client: httpx.AsyncClient) -> AlertsFetch:
    """Fetch every configured alert feed concurrently; return an AlertsFetch of
    (active alerts, suppressed_count, failed feeds and why, served-empty feeds).

    Mirrors fetch_subway_trains: per-feed failures (a fetch error, a timeout, or an
    undecodable protobuf) are logged and skipped so one bad feed does not drop every
    alert, and this raises AllAlertFeedsFailed only when EVERY feed fails. `failed`
    maps each feed key that dropped this poll to why, in sorted key order, and is
    empty on a fully successful poll. The caller owns the client. `now` is captured
    once so all feeds filter against the same instant.

    `served_empty` NAMES A SUCCESS, NOT A FAILURE. It carries the feeds whose 200
    was NJ Transit's zero-byte served-empty body (njt_alerts_served_empty): those
    decoded, contributed no alerts, and must be treated exactly like any other
    decode by everything downstream, so they are absent from `failed`, their
    previously-retained alerts are released, and their fresh_at advances. The list
    exists only so the caller can SAY so on /api/status, which is what keeps the
    state distinguishable from both a quiet decode and a failure.

    EACH FEED CARRIES ITS OWN DEADLINE, and that is load-bearing for the "one bad feed
    does not drop every alert" promise. These five run in ONE gather, so a deadline
    applied around the whole call cannot distinguish a single trickling feed from a
    total outage: it cancels the four responses that already landed and the caller,
    seeing only a timeout, has to treat every system as failed. That put four healthy
    feeds' alerts into retention and deleted them half an hour later. Bounding each
    feed separately keeps a slow feed a PER-FEED failure, which the machinery below
    already handles correctly, and leaves the caller's whole-refresh deadline as a
    backstop that can now only fire when essentially everything is slow.
    """
    now = time.time()

    async def fetch(key: str, url: str) -> bytes:
        # A whole-request deadline per feed. The client's own timeout=30 bounds the gap
        # between BYTES; this bounds the exchange, so a feed that dribbles forever under
        # that floor still fails on its own rather than holding up the poll.
        async with asyncio.timeout(ALERT_FEED_DEADLINE_S):
            if key == NJT_ALERT_SYSTEM:
                # THROUGH THE TOKEN DOOR, never this client. njt_auth.njt_post owns
                # the process-wide single-flight token cache, which is what makes
                # this feed, the NJT trains poller and the static loader share ONE
                # token: three callers meeting an expired token together produce one
                # re-mint, not three, against a rate limit NJ Transit does not
                # publish. A client.post here would route around that lock.
                return await njt_auth.njt_post(url, {})
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    # THE ACTIVE SET, not the full table: an unconfigured NJ Transit is absent
    # rather than permanently failing (see active_alert_feeds).
    feed_urls = active_alert_feeds()
    keys = list(feed_urls)
    results = await asyncio.gather(
        *(fetch(k, feed_urls[k]) for k in keys),
        return_exceptions=True,
    )

    alerts: list[dict] = []
    suppressed = 0
    feed_errors: dict[str, str] = {}
    served_empty: list[str] = []
    for key, result in zip(keys, results):
        if isinstance(result, BaseException):
            feed_errors[key] = _describe_feed_error(result)
            if not isinstance(result, (TimeoutError, *_PUBLISHABLE_FEED_ERRORS)):
                # Its MESSAGE is deliberately not published (see
                # _describe_feed_error), so the traceback has to reach an operator
                # somewhere: an unexpected exception inside this gather is a fault
                # in this process, and the log is where a fault of ours is
                # diagnosed. This is strictly more than the line below carried
                # before, which had the message and never the traceback.
                logger.warning(
                    "alert feed %s failed with an unexpected error", key, exc_info=result
                )
            continue
        try:
            decoded, feed_suppressed = _decode_alerts(result, key, now)
        except DecodeError as exc:
            feed_errors[key] = f"undecodable protobuf ({exc})"
            continue
        # AFTER the decode, not instead of it, so the served-empty rule has exactly
        # one implementation: if _decode_alerts stops honoring it, this feed lands
        # in feed_errors above and never reaches here at all.
        if njt_alerts_served_empty(key, result):
            served_empty.append(key)
        alerts.extend(decoded)
        suppressed += feed_suppressed

    # SORTED, so the reasons an operator reads on /api/status and the one-line log
    # record below arrive in a stable order rather than in gather order.
    feed_errors = {key: feed_errors[key] for key in sorted(feed_errors)}
    if feed_errors:
        logger.warning(
            "%d of %d alert feeds failed: %s",
            len(feed_errors),
            len(feed_urls),
            _join_feed_errors(feed_errors),
        )
    if len(feed_errors) == len(feed_urls):
        raise AllAlertFeedsFailed(feed_errors)
    return AlertsFetch(alerts, suppressed, feed_errors, sorted(served_empty))


# How long a failed alert system's alerts are carried forward before they drop.
# A stale vehicle position is still roughly where the vehicle is, but a stale
# "delays right now" alert becomes active misinformation the longer the feed is
# down, so retention is bounded: after this, the system's alerts drop and only
# the health surface still reports the outage. 30 minutes is comfortably longer
# than any brief upstream blip while staying short of the horizon where a
# service alert is likely to have changed on the ground.
ALERT_RETENTION_MAX_S = 1800


def merge_alert_generations(
    prev_alerts: list[dict] | None,
    fresh_alerts: list[dict],
    failed_systems: Iterable[str],
    prev_retained_since: Mapping[str, float],
    now: float,
    max_retention_s: float,
) -> tuple[list[dict], dict[str, float]]:
    """Merge the previous served alert index with this poll's fresh alerts so a
    single alert feed going down retains that system's alerts instead of silently
    deleting them (railroad arrivals already retain per system; alerts did not).

    Pure and clock-injected: `now` and `prev_retained_since` are passed in, never
    read from a wall clock or module state, so the whole retention decision is a
    deterministic function of its inputs. Returns
    (merged_alerts, retained_since) where retained_since maps each system CURRENTLY
    served from carried-forward (not fresh) alerts to the instant its retention
    began; its keys are exactly the systems serving retained alerts, and the caller
    records the timestamps into the per-system health surface.

    Per system:
      - NOT failed this poll: its alerts come exclusively from fresh_alerts, which
        replace wholesale (fresh is authoritative; a decoded feed is ground truth).
      - failed this poll: its alerts are carried forward from prev_alerts, with two
        guards:
        1. Re-filter each carried alert against `now` on its ends_at (active while
           now is before ends_at; open-ended when ends_at is None), so an alert that
           expired DURING the outage drops instead of being pinned alive by the
           outage.

           THIS IS NOT QUITE THE SAME RULE _decode_alerts APPLIES, and the docstring
           used to claim it was. A carried alert has only its collapsed ends_at here,
           not its original active_period list, so a MULTI-PERIOD alert whose
           effective end spans a gap between periods (see _alert_window_status: the
           effective end is the latest end among periods not yet ended) is carried
           through that gap, where a poll that actually decoded would have classified
           it "future" and suppressed it. The exposure is bounded: it needs an outage
           AND a multi-period alert AND now to fall in a gap, and guard 2 caps the
           whole thing at max_retention_s. Carrying the periods themselves would fix
           it properly and belongs with the per-system envelope work, not here.
           starts_at is deliberately not rechecked either: it is the earliest covering
           start at decode time, so for a gap it reads as long past and would not
           catch this case anyway.
        2. Cap total retention age at max_retention_s measured from when the
           system first went down (prev_retained_since, or now for a newly-failed
           system). This is the guard that eventually clears an OPEN-ENDED alert
           (ends_at None), which guard 1 can never expire on its own.

    fresh_alerts carries alerts only from systems that decoded (a failed feed
    contributes none), so fresh and failed are disjoint by construction; the
    fresh filter below is defensive belt-and-suspenders, not a live dedup.
    """
    failed = set(failed_systems)
    merged = [a for a in fresh_alerts if a.get("system") not in failed]

    prev_by_system: dict[str | None, list[dict]] = defaultdict(list)
    for alert in prev_alerts or []:
        prev_by_system[alert.get("system")].append(alert)

    retained_since: dict[str, float] = {}
    for system in failed:
        # Explicit None check, not truthiness: a retention-start timestamp can be
        # 0.0 (epoch), which `or now` would wrongly reset every poll.
        started = prev_retained_since.get(system)
        if started is None:
            started = now
        if now - started >= max_retention_s:
            continue  # capped: drop this system's alerts; health still flags it
        carried = [
            alert
            for alert in prev_by_system.get(system, [])
            if alert.get("ends_at") is None or now < alert["ends_at"]
        ]
        if carried:
            merged.extend(carried)
            retained_since[system] = started
    return merged, retained_since
