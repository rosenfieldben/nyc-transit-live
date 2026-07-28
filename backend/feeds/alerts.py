"""Service alerts: the keyless GTFS-RT alert feeds, the active-now window
logic, the per-alert decode, the fetch aggregation, and the per-system
retention merge that carries a down feed's alerts forward across a partial
outage."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

import env_seams
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
ALERT_FEED_URLS = {
    "subway": ALERTS_RT_BASE + "/camsys%2Fsubway-alerts",
    "bus": ALERTS_RT_BASE + "/camsys%2Fbus-alerts",
    "LIRR": ALERTS_RT_BASE + "/camsys%2Flirr-alerts",
    "MNR": ALERTS_RT_BASE + "/camsys%2Fmnr-alerts",
    "ferry": FERRY_ALERTS_URL,
}


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
    """
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


def _describe_feed_error(exc: BaseException) -> str:
    """A readable reason for a failed feed. Not just str(exc): str(TimeoutError()) is
    the EMPTY STRING, so a timed-out feed would otherwise be recorded and logged with
    no cause at all (the same trap R3 hit on the warmup path). Every branch here is
    guaranteed to produce something an operator can act on."""
    if isinstance(exc, TimeoutError):
        return f"no response within {ALERT_FEED_DEADLINE_S:.0f}s"
    return str(exc) or exc.__class__.__name__


async def fetch_service_alerts(client: httpx.AsyncClient) -> tuple[list[dict], int, list[str]]:
    """Fetch every configured alert feed concurrently; return
    (active alerts, suppressed_count, failed_feeds).

    Mirrors fetch_subway_trains: per-feed failures (a fetch error, a timeout, or an
    undecodable protobuf) are logged and skipped so one bad feed does not drop every
    alert, and this raises only when EVERY feed fails. failed_feeds is the sorted list
    of feed keys that dropped this poll, empty on a fully successful poll. The caller
    owns the client. `now` is captured once so all feeds filter against the
    same instant.

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

    async def fetch(url: str) -> bytes:
        # A whole-request deadline per feed. The client's own timeout=30 bounds the gap
        # between BYTES; this bounds the exchange, so a feed that dribbles forever under
        # that floor still fails on its own rather than holding up the poll.
        async with asyncio.timeout(ALERT_FEED_DEADLINE_S):
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    keys = list(ALERT_FEED_URLS)
    results = await asyncio.gather(
        *(fetch(ALERT_FEED_URLS[k]) for k in keys),
        return_exceptions=True,
    )

    alerts: list[dict] = []
    suppressed = 0
    feed_errors: dict[str, str] = {}
    for key, result in zip(keys, results):
        if isinstance(result, BaseException):
            feed_errors[key] = _describe_feed_error(result)
            continue
        try:
            decoded, feed_suppressed = _decode_alerts(result, key, now)
        except DecodeError as exc:
            feed_errors[key] = f"undecodable protobuf ({exc})"
            continue
        alerts.extend(decoded)
        suppressed += feed_suppressed

    if feed_errors:
        logger.warning(
            "%d of %d alert feeds failed: %s",
            len(feed_errors),
            len(ALERT_FEED_URLS),
            "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items()),
        )
    if len(feed_errors) == len(ALERT_FEED_URLS):
        joined = "; ".join(f"{key}: {reason}" for key, reason in feed_errors.items())
        raise RuntimeError(f"All alert feeds failed: {joined}")
    return alerts, suppressed, sorted(feed_errors)


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
