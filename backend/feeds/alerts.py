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

from feeds.shared import _RAILROAD_BASE, logger

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
ALERT_FEED_URLS = {
    "subway": _RAILROAD_BASE + "/camsys%2Fsubway-alerts",
    "bus": _RAILROAD_BASE + "/camsys%2Fbus-alerts",
    "LIRR": _RAILROAD_BASE + "/camsys%2Flirr-alerts",
    "MNR": _RAILROAD_BASE + "/camsys%2Fmnr-alerts",
    "ferry": "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx/alert",
}


# ---- Service alerts ----

_ALERT_EFFECT = gtfs_realtime_pb2.Alert.Effect


_ALERT_CAUSE = gtfs_realtime_pb2.Alert.Cause


def _alert_window_status(
    periods: list[tuple[int | None, int | None]], now: float
) -> tuple[str, int | None, int | None]:
    """Classify an alert's active_period list against `now`, returning
    (status, starts_at, ends_at):

      "active": some period covers now; starts_at/ends_at are that period's bounds
      "future": no period covers now but at least one starts after now (planned work)
      "ended":  no period covers now and none is still upcoming (all elapsed)

    Open bounds follow the feed facts: an EMPTY period list means the alert is
    always active (no window constraint); a None start is open on the left; a None
    end (the decode maps an end of 0 or unset to None) is open-ended. A period
    covers now on the half-open interval [start, end), matching the GTFS-RT spec.
    "future" is split out from "ended" because only not-yet-active planned work is
    worth counting for /api/status; a fully elapsed alert is just gone.
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
        elif start is not None and now < start:
            has_future = True  # begins later: planned, not yet active
    if covering:
        # When several periods cover now, report the one that started earliest (the
        # alert has been active longest); an open start sorts first.
        covering.sort(key=lambda p: float("-inf") if p[0] is None else p[0])
        start, end = covering[0]
        return "active", start, end
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
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)  # caller handles DecodeError

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


async def fetch_service_alerts(client: httpx.AsyncClient) -> tuple[list[dict], int, list[str]]:
    """Fetch every configured alert feed concurrently; return
    (active alerts, suppressed_count, failed_feeds).

    Mirrors fetch_subway_trains: per-feed failures (a fetch error or undecodable
    protobuf) are logged and skipped so one bad feed does not drop every alert,
    and this raises only when EVERY feed fails. failed_feeds is the sorted list of
    feed keys that dropped this poll, empty on a fully successful poll. The caller
    owns the client. `now` is captured once so all feeds filter against the
    same instant.
    """
    now = time.time()

    async def fetch(url: str) -> bytes:
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
            feed_errors[key] = str(result)
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
        1. Re-filter each carried alert against `now` with the SAME activity rule
           _decode_alerts applies (active while now is before ends_at; open-ended
           when ends_at is None), so an alert that expired DURING the outage drops
           instead of being pinned alive by the outage. starts_at need not be
           rechecked: a retained alert was active at some prior now <= now, so its
           start has already passed.
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
