#!/usr/bin/env python3
"""Live upstream contract monitor.

CI is deliberately hermetic: the golden fixtures pin decode behavior against
captured reality, so a green pipeline proves the code still parses YESTERDAY's
bytes. It cannot tell when live reality drifts out from under those goldens:
an upstream schema change, a moved or renamed feed URL, a dead community
bridge, a static feed that expired. This script is the other half. It runs on
a schedule (see .github/workflows/contract-monitor.yml), fetches every upstream
source and the production deployment, and decodes each with the SAME functions
production uses, so a pass means the actual app code paths still work against
today's live data.

Two guiding principles shape every check here:

  1. FAIL means a human should look TODAY; WARN means notable but expected in
     some conditions; PASS means healthy. A monitor that flaps gets muted, so
     every check bands its judgement (ranges, not exact counts) and knows which
     emptiness is normal (a railroad feed is legitimately thin overnight; the
     ferry is closed at night; an alerts feed with zero active alerts is good
     news). WHY each band sits where it does is spelled out at its constant or
     its check.

  2. Reuse, never reimplement. Every check calls the production parse/decode
     functions (feeds._decode_feed, feeds._decode_railroad_feed,
     feeds._decode_path_feed, feeds._decode_alerts, path_static._parse_zip,
     ferry_static._parse_zip, railroad_static._parse_stops, static_data's
     parsers). If a check ran forked logic, it could pass while production
     broke, or vice versa, defeating the point. Where a module exposes no
     bytes-level entry point (subway static parses from a fixed on-disk path),
     the monitor feeds it a temp copy rather than reimplement the parse.

Structure: each check is a small, individually testable function that returns a
Result (name, PASS | WARN | FAIL, detail). Fetching, the clock, and the
environment are injected so the whole suite runs hermetically in tests with no
network. main() wires the real httpx fetcher, the wall clock, and os.environ,
prints one line per check, writes a markdown table to the job summary when
running under GitHub Actions, and exits non-zero only when something FAILed
(WARNs never fail the run but always appear).

This monitor changes no app behavior: it adds no endpoint, touches no poller or
warmup, and is never a gate on the normal CI pipeline.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Callable, NamedTuple

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

# The gen_*.py scripts use this same two-line preamble so a script run directly
# (python scripts/contract_monitor.py) can import the app modules that live in
# backend/, which is not on sys.path when scripts/ is the entry directory.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import feeds  # noqa: E402
import ferry_static  # noqa: E402
import path_static  # noqa: E402
import railroad_static  # noqa: E402
import static_data  # noqa: E402
from cache import _sanitize_upstream as _sanitize  # noqa: E402

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}


class Result(NamedTuple):
    name: str
    status: str
    detail: str


def _worst(statuses: list[str]) -> str:
    """The most severe status in the list (FAIL > WARN > PASS), or PASS when the
    list is empty. Checks accumulate per-concern statuses and fold them here so
    one FAIL among several PASSes still fails the whole check."""
    return max(statuses, key=lambda s: _SEVERITY[s]) if statuses else PASS


# ---------------------------------------------------------------------------
# Fetching (injected)
# ---------------------------------------------------------------------------


class FetchResult(NamedTuple):
    status: int
    content: bytes


# A fetcher takes (url, headers, params) and returns a FetchResult, or raises on
# a transport error. Injected everywhere so tests never touch the network.
Fetcher = Callable[..., FetchResult]

# One short pause between the two attempts. WHY exactly one retry: a single
# transient blip (a dropped connection, a momentary 5xx from a CDN) should not
# page a human, but a source that is genuinely down stays down across a few
# seconds, so a second consecutive miss is a real signal. More retries would
# only delay a true failure and mask a degrading source.
RETRY_DELAY_S = 3.0

# Per-request ceiling for the real fetcher. Generous: the largest download is
# the subway static zip (a few MB), and the monitor is not latency-sensitive.
REQUEST_TIMEOUT_S = 30.0


def _fetch_retrying(
    fetch: Fetcher,
    url: str,
    sleep: Callable[[float], None],
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> tuple[FetchResult | None, str]:
    """Fetch once, and on a transport error OR a non-200, wait a short delay and
    try exactly once more. Returns (result, "") on a 200, else (None, detail)
    where detail is a sanitized reason for the caller's FAIL line. Any URL in an
    exception string (including a key-bearing bus URL) is scrubbed by _sanitize,
    so a failure detail never leaks a secret."""
    last = ""
    for attempt in (1, 2):
        try:
            res = fetch(url, headers=headers, params=params)
        except Exception as exc:  # noqa: BLE001 - any transport failure is a miss
            last = f"transport error: {_sanitize(exc)}"
        else:
            if res.status == 200:
                return res, ""
            last = f"HTTP {res.status}"
        if attempt == 1:
            sleep(RETRY_DELAY_S)
    return None, last


def make_httpx_fetcher(timeout: float = REQUEST_TIMEOUT_S) -> Fetcher:
    """The production fetcher: a plain follow-redirects GET. follow_redirects is
    on because two static sources (ferry utility URL, the MTA developer static
    paths) 30x to their real zip."""

    def fetch(
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> FetchResult:
        resp = httpx.get(
            url, headers=headers, params=params, follow_redirects=True, timeout=timeout
        )
        return FetchResult(resp.status_code, resp.content)

    return fetch


# ---------------------------------------------------------------------------
# Thresholds and bands (WHY at each constant)
# ---------------------------------------------------------------------------

# Realtime freshness by feed header (the feed's own content clock). 10 minutes
# for the MTA feeds: they regenerate roughly every 30 seconds, so a 10 minute
# gap means the upstream stopped publishing, not ordinary jitter. This is a
# WARN per feed (a single lagging line group happens) and only a FAIL when
# EVERY live feed in a group is stale (a systemic publish outage).
REALTIME_STALE_S = 600.0

# PATH bridge freshness is tighter: the community bridge rewrites its feed about
# every 15 seconds, so a 5 minute gap already means it stopped writing. The
# bridge is a single source, so its staleness is a FAIL (there is no sibling
# feed to fall back to).
PATH_STALE_S = 300.0

# Share of PATH bridge trip-update entities whose stop ids must resolve against
# the 13a static parent table. A couple of unresolved is tolerable (one renamed
# station); a large share means the static table and the bridge diverged and
# rider trains are silently dropped from the map.
PATH_RESOLVE_FLOOR = 0.95

# Share of in-service ferry realtime trips that must join to a route through the
# static trips table. A ferry realtime trip carries an EMPTY route_id, so this
# join is the only way to color it; a low rate means the static/realtime trip id
# namespaces drifted apart. 90% leaves room for a handful of brand-new trip ids
# the static feed has not caught up to yet.
FERRY_JOIN_FLOOR = 0.90

# A published static feed whose end date is within this window is about to
# expire (WARN); one already past is expired (FAIL). Generous because publishers
# reissue every few weeks and a month of runway is plenty of warning.
FEED_END_WARN_DAYS = 30

# Production /api/status per-feed poll-age threshold, same 10 minute reasoning
# as the direct feed freshness. Reported at WARN (not FAIL): the deployment's
# feed set depends on its own config (for example a bus key it may not carry),
# and the direct upstream checks above are the authoritative FAIL signal; the
# production section corroborates without flapping the whole run red on a
# transient deploy-side poll hiccup.
PRODUCTION_FEED_STALE_S = 600.0

# Static count floors, all set well below the real numbers so ordinary feed
# churn never trips them; they exist to catch a gutted or truncated feed.
SUBWAY_STATIC_MIN_STOPS = 100  # real stops.txt carries ~1900 platform+parent rows
RAILROAD_STATIC_MIN_STOPS = 20  # LIRR ~240, MNR ~180 stops
PATH_STATIC_MIN_PARENTS = 10  # 13 PATH parent stations
FERRY_STATIC_MIN_ROUTES = 7  # 9 NYC Ferry routes
FERRY_STATIC_MIN_STOPS = 40  # 50 NYC Ferry stops

# Required zip members per static source: the files the production parser opens.
# stop_times.txt is required for PATH and ferry (a real GTFS always ships it,
# and its absence in a live feed is a real truncation) even though the trimmed
# test fixtures omit it for size; the presence check is structural, separate
# from the parse.
SUBWAY_REQUIRED_MEMBERS = ("stops.txt", "shapes.txt")
RAILROAD_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt")
PATH_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt", "stop_times.txt")
FERRY_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt", "stop_times.txt")

# NYC Ferry daily service window in ET (~06:00 first departures to ~22:30 last
# arrivals). Used so an empty realtime feed reads as FAIL-worthy only when boats
# should be running; overnight emptiness is the normal closed state. Intentionally
# generous at both ends so a schedule that starts a touch earlier or runs later
# does not trip the monitor.
FERRY_SERVICE_START = dt_time(6, 0)
FERRY_SERVICE_END = dt_time(22, 30)

# NYC Ferry has no production realtime decoder yet (phase 14b is not built), so
# the monitor names the two Connexionz GTFS-RT endpoints here. FOLLOWUP: when
# ferry realtime lands in feeds/, delete these and import the URLs and decode
# from there, the same way the other realtime checks already reuse feeds.
_FERRY_RT_BASE = "https://nycferry.connexionz.net/rtt/public/utility/gtfsrealtime.aspx"
FERRY_RT_URLS = {
    "alert": _FERRY_RT_BASE + "/alert",
    "tripupdate": _FERRY_RT_BASE + "/tripupdate",
}

# Connexionz (ferry) is a community-hosted feed like the PATH bridge, so the
# monitor identifies itself with the project's courteous User-Agent, exactly as
# the app does for PATH, instead of an anonymous default.
_COURTEOUS_UA = {"User-Agent": feeds.PATH_USER_AGENT}


# ---------------------------------------------------------------------------
# Shared realtime helpers
# ---------------------------------------------------------------------------


def _parse_feed(raw: bytes) -> gtfs_realtime_pb2.FeedMessage:
    """Parse GTFS-Realtime bytes into a FeedMessage. Raises DecodeError on a
    body that is not a valid protobuf (the caller turns that into a FAIL). This
    is the same parse the production decoders do internally; it is called here
    only to count raw feed entities and read the header, independent of whether
    the static tables resolve those entities."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    return feed


# ---------------------------------------------------------------------------
# Realtime checks
# ---------------------------------------------------------------------------


class _FeedObs(NamedTuple):
    """One realtime feed's observation, fed to the pure evaluators below."""

    key: str
    ok: bool  # fetched 200 AND decoded without raising
    detail: str  # failure reason when not ok
    header_ts: float | None
    entity_count: int


def _evaluate_subway(obs: list[_FeedObs], now: float, stale_s: float) -> Result:
    """Pure banding for the subway feed group (kept separate from fetching so the
    boundary cases are unit-testable with synthetic observations).

    Bands:
      - Any feed that failed to fetch or decode is a FAIL (a line group is
        unreadable). Those feeds are excluded from the emptiness/staleness
        tallies below so a down feed is not also counted as merely empty.
      - Zero entities on EVERY live feed is a system-wide outage (FAIL); zero on
        SOME feeds is ordinary off-peak on a single line group (WARN).
      - Stale header on EVERY live feed is a systemic publish freeze (FAIL);
        stale on some is a single lagging group (WARN). A feed that omits its
        header timestamp is a lesser WARN (unusual but not an outage).
    """
    failed = [o for o in obs if not o.ok]
    live = [o for o in obs if o.ok]
    statuses: list[str] = []
    details: list[str] = []

    if failed:
        statuses.append(FAIL)
        details.append("down: " + ", ".join(f"{o.key} ({o.detail})" for o in failed))

    if live:
        empty = [o for o in live if o.entity_count == 0]
        if len(empty) == len(live):
            statuses.append(FAIL)
            details.append("every live feed carried zero entities")
        elif empty:
            statuses.append(WARN)
            details.append("no entities: " + ", ".join(o.key for o in empty))

        stale = [o for o in live if o.header_ts is not None and now - o.header_ts > stale_s]
        no_ts = [o for o in live if o.header_ts is None]
        if len(stale) == len(live):
            statuses.append(FAIL)
            details.append(f"every live feed header older than {int(stale_s)}s")
        elif stale:
            statuses.append(WARN)
            details.append("stale header: " + ", ".join(o.key for o in stale))
        if no_ts:
            statuses.append(WARN)
            details.append("no header timestamp: " + ", ".join(o.key for o in no_ts))

    if not statuses:
        return Result("subway-realtime", PASS, f"{len(live)} feeds fresh and carrying data")
    return Result("subway-realtime", _worst(statuses), "; ".join(details))


def check_subway_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    stops: dict[str, dict],
    *,
    feed_urls: dict[str, str] | None = None,
    stale_s: float = REALTIME_STALE_S,
) -> Result:
    """Every subway feed: reachable (200), decodable, header fresh, and carrying
    entities (banded by _evaluate_subway). Runs the production _decode_feed on
    each payload so schema drift the bare protobuf parse tolerates (a renamed
    field the decoder reads, a shape the walk assumes) still surfaces here."""
    feed_urls = feed_urls if feed_urls is not None else feeds.SUBWAY_FEED_URLS
    obs: list[_FeedObs] = []
    for key, url in feed_urls.items():
        res, detail = _fetch_retrying(fetch, url, sleep)
        if res is None:
            obs.append(_FeedObs(key, False, detail, None, 0))
            continue
        try:
            feed = _parse_feed(res.content)
            # Run the real decoder end to end. Its return is discarded: this call
            # exists to prove the production decode path does not raise on live
            # data. The entity count and header come from the bare parse above so
            # they do not depend on whether `stops` resolved anything.
            feeds._decode_feed(res.content, stops, key, now)
        except DecodeError as exc:
            obs.append(_FeedObs(key, False, f"undecodable ({_sanitize(exc)})", None, 0))
            continue
        except Exception as exc:  # noqa: BLE001 - decoder raising on live data is the break
            obs.append(_FeedObs(key, False, f"decoder raised ({_sanitize(exc)})", None, 0))
            continue
        obs.append(_FeedObs(key, True, "", feeds._header_timestamp(feed), len(feed.entity)))
    return _evaluate_subway(obs, now, stale_s)


def check_railroad_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    stops_by_system: dict[str, dict],
    *,
    feed_urls: dict[str, str] | None = None,
    stale_s: float = REALTIME_STALE_S,
) -> Result:
    """Every railroad feed: reachable, decodable, and the placement/arrivals
    decode plus direction inference run without raising. NO entity floor: the
    railroads run thin overnight and a legitimately empty feed is normal.
    Freshness is judged only for systems whose header tracks publish time
    (RAILROAD_FRESHNESS_SYSTEMS, today LIRR); MNR stamps a lagging shared clock
    the app deliberately ignores, so flagging MNR on it would be a false alarm.

    _decode_railroad_feed exercises the direction-inference heuristic internally
    (the most fragile railroad path, since it reads live stop progressions), so
    calling it is enough to prove inference does not raise."""
    feed_urls = feed_urls if feed_urls is not None else feeds.RAILROAD_FEED_URLS
    statuses: list[str] = []
    details: list[str] = []
    live = 0
    for system, url in feed_urls.items():
        res, detail = _fetch_retrying(fetch, url, sleep)
        if res is None:
            statuses.append(FAIL)
            details.append(f"{system} down ({detail})")
            continue
        stops = stops_by_system.get(system, {})
        try:
            feed = _parse_feed(res.content)
            feeds._decode_railroad_feed(res.content, system, stops, now)
        except DecodeError as exc:
            statuses.append(FAIL)
            details.append(f"{system} undecodable ({_sanitize(exc)})")
            continue
        except Exception as exc:  # noqa: BLE001 - decoder raising on live data is the break
            statuses.append(FAIL)
            details.append(f"{system} decoder raised ({_sanitize(exc)})")
            continue
        live += 1
        if system in feeds.RAILROAD_FRESHNESS_SYSTEMS:
            ts = feeds._header_timestamp(feed)
            if ts is None:
                statuses.append(WARN)
                details.append(f"{system} omitted its header timestamp")
            elif now - ts > stale_s:
                statuses.append(WARN)
                details.append(f"{system} header older than {int(stale_s)}s")
    if not statuses:
        return Result("railroad-realtime", PASS, f"{live} feeds decoded, direction inference ran")
    return Result("railroad-realtime", _worst(statuses), "; ".join(details))


def check_path_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    stops: dict[str, dict],
    *,
    url: str | None = None,
    stale_s: float = PATH_STALE_S,
    resolve_floor: float = PATH_RESOLVE_FLOOR,
) -> Result:
    """The PATH community bridge: reachable, decodable, fresh, TripUpdate-only in
    shape, and its stop ids resolving against the static parent table above the
    floor. Sent with the courteous User-Agent the app uses for the bridge.

    Note on retries: the bridge regenerates faster than the upstream refreshes,
    so identical content across polls is NORMAL for PATH; the monitor makes no
    content-changed staleness inference here (it retries only on failure, and
    freshness rides on the bridge write time, not on content changing)."""
    url = url if url is not None else feeds.PATH_RT_URL
    res, detail = _fetch_retrying(fetch, url, sleep, headers=_path_ua())
    if res is None:
        return Result("path-realtime", FAIL, f"bridge unreachable ({detail})")
    try:
        feed = _parse_feed(res.content)
        _, _, feed_ts, unresolved = feeds._decode_path_feed(res.content, stops, now)
    except DecodeError as exc:
        return Result("path-realtime", FAIL, f"undecodable ({_sanitize(exc)})")
    except Exception as exc:  # noqa: BLE001 - decoder raising on live data is the break
        return Result("path-realtime", FAIL, f"decoder raised ({_sanitize(exc)})")

    statuses: list[str] = []
    details: list[str] = []
    trip_updates = [e for e in feed.entity if e.HasField("trip_update")]

    if any(e.HasField("vehicle") for e in feed.entity):
        # The decoder assumes the bridge serves only trip updates. A vehicle
        # entity means the feed shape changed under it: notable, not an outage.
        statuses.append(WARN)
        details.append("feed now carries VehiclePositions (decoder assumes TripUpdate-only)")

    if feed_ts is None:
        statuses.append(WARN)
        details.append("feed omitted its header timestamp")
    elif now - feed_ts > stale_s:
        statuses.append(FAIL)
        details.append(f"bridge write time older than {int(stale_s)}s")

    if not trip_updates:
        # PATH runs ~20 hours and even the small hours carry a few trains, so an
        # entirely empty bridge feed is worth a look, but bands to WARN: one
        # empty snapshot should not page anyone.
        statuses.append(WARN)
        details.append("bridge feed carried no trip updates")
    elif not stops:
        # The static parent table did not load (its own static check reports the
        # reason). Stop resolution cannot be assessed without it, so do NOT emit
        # a 0%-resolved FAIL: that would misdirect an operator toward a realtime
        # id mismatch when the real cause is a monitor-side static-fetch blip.
        details.append("stop resolution not checked (static parent table unavailable)")
    else:
        # unresolved counts entities whose stop ids match no known parent station
        # (a static-vs-bridge id mismatch); a SKIPPED/NO_DATA stop at a known
        # station is not counted. So resolved is the share NOT id-mismatched.
        resolved = len(trip_updates) - unresolved
        rate = resolved / len(trip_updates)
        if rate < resolve_floor:
            statuses.append(FAIL)
            details.append(
                f"only {resolved}/{len(trip_updates)} entities resolved "
                f"({rate:.0%} < {resolve_floor:.0%})"
            )

    if not statuses:
        # A note with no status (resolution skipped because the static table was
        # unavailable) still belongs in the PASS line so the operator sees why.
        if details:
            return Result("path-realtime", PASS, "; ".join(details))
        return Result(
            "path-realtime", PASS, f"{len(trip_updates)} trains, stop ids resolved, fresh"
        )
    return Result("path-realtime", _worst(statuses), "; ".join(details))


def _in_ferry_service_hours(now: float, tz) -> bool:
    """True when `now` falls inside NYC Ferry's daily service window in ET. The
    window is closed-interval on both ends so the boundary minutes count as in
    service, and it is judged in the ferry's local timezone so it stays correct
    across DST without any offset math here."""
    local = datetime.fromtimestamp(now, tz).time()
    return FERRY_SERVICE_START <= local <= FERRY_SERVICE_END


def check_ferry_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    trips: dict[str, dict],
    *,
    urls: dict[str, str] | None = None,
    tz=None,
    join_floor: float = FERRY_JOIN_FLOOR,
) -> Result:
    """Both NYC Ferry realtime endpoints: reachable and decodable. The tripupdate
    entity floor and the trip->route join floor apply ONLY during service hours;
    outside them an empty feed is the normal closed state and passes with a note.

    The route join mirrors the 14a static contract: a realtime ferry trip carries
    an EMPTY route_id, so its route is recovered by joining trip_id through the
    static trips table. Deadheads carry an empty trip_id and are excluded from
    the join denominator."""
    urls = urls if urls is not None else FERRY_RT_URLS
    tz = tz if tz is not None else feeds.NYC_TZ
    in_service = _in_ferry_service_hours(now, tz)
    statuses: list[str] = []
    details: list[str] = []

    # Alert endpoint: decodable is all we require; ferry alerts are sporadic, so
    # empty is always fine and there is no floor.
    a_res, a_detail = _fetch_retrying(fetch, urls["alert"], sleep, headers=_ferry_ua())
    if a_res is None:
        statuses.append(FAIL)
        details.append(f"alert endpoint unreachable ({a_detail})")
    else:
        try:
            _parse_feed(a_res.content)
        except DecodeError as exc:
            statuses.append(FAIL)
            details.append(f"alert endpoint undecodable ({_sanitize(exc)})")

    # Tripupdate endpoint: the live vehicle feed.
    t_res, t_detail = _fetch_retrying(fetch, urls["tripupdate"], sleep, headers=_ferry_ua())
    if t_res is None:
        statuses.append(FAIL)
        details.append(f"tripupdate endpoint unreachable ({t_detail})")
    else:
        try:
            feed = _parse_feed(t_res.content)
        except DecodeError as exc:
            statuses.append(FAIL)
            details.append(f"tripupdate undecodable ({_sanitize(exc)})")
        else:
            trip_updates = [e for e in feed.entity if e.HasField("trip_update")]
            if not trip_updates:
                if in_service:
                    statuses.append(WARN)
                    details.append("no trip updates during service hours")
                # Outside service hours an empty feed is the normal closed state;
                # the "(closed)" summary already conveys it, so no note is added.
            elif in_service:
                joinable = [e for e in trip_updates if e.trip_update.trip.trip_id]
                if not trips:
                    # The static trips table did not load (its own static check
                    # reports why). The route join cannot be assessed without it,
                    # so do NOT emit a 0%-joined FAIL that would misattribute a
                    # monitor-side static-fetch blip to a realtime namespace break.
                    details.append("route join not checked (static trips table unavailable)")
                elif not joinable:
                    # Trip updates are present but NONE carry a trip_id, so the
                    # route join (the only way to color a ferry trip, whose
                    # realtime route_id is empty) is impossible for every one of
                    # them. Surface it rather than passing silently: this is the
                    # namespace drift the join floor exists to catch. WARN, not
                    # FAIL, to stay non-flapping if a rare all-deadhead lull
                    # (every trip carrying an empty trip id) ever occurs.
                    statuses.append(WARN)
                    details.append(f"{len(trip_updates)} trip updates but none carry a trip_id")
                else:
                    resolved = sum(
                        1
                        for e in joinable
                        if (trips.get(e.trip_update.trip.trip_id) or {}).get("route_id")
                    )
                    rate = resolved / len(joinable)
                    if rate < join_floor:
                        statuses.append(FAIL)
                        details.append(
                            f"only {resolved}/{len(joinable)} trips joined to a route "
                            f"({rate:.0%} < {join_floor:.0%})"
                        )

    if not statuses:
        note = "in service" if in_service else "closed"
        base = f"endpoints decodable ({note})"
        # Surface any status-less note (e.g. the join skipped because the static
        # trips table was unavailable) alongside the healthy summary.
        detail = base + ("; " + "; ".join(details) if details else "")
        return Result("ferry-realtime", PASS, detail)
    return Result("ferry-realtime", _worst(statuses), "; ".join(details))


def check_alerts_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    *,
    feed_urls: dict[str, str] | None = None,
) -> Result:
    """Every service-alerts feed: reachable and decodable by the production
    _decode_alerts. NO entity floor: zero active alerts is a valid, common, and
    good steady state, so emptiness is never a fault here."""
    feed_urls = feed_urls if feed_urls is not None else feeds.ALERT_FEED_URLS
    statuses: list[str] = []
    details: list[str] = []
    for key, url in feed_urls.items():
        res, detail = _fetch_retrying(fetch, url, sleep)
        if res is None:
            statuses.append(FAIL)
            details.append(f"{key} down ({detail})")
            continue
        try:
            feeds._decode_alerts(res.content, key, now)
        except DecodeError as exc:
            statuses.append(FAIL)
            details.append(f"{key} undecodable ({_sanitize(exc)})")
        except Exception as exc:  # noqa: BLE001 - decoder raising on live data is the break
            statuses.append(FAIL)
            details.append(f"{key} decoder raised ({_sanitize(exc)})")
    if not statuses:
        return Result("alerts-realtime", PASS, f"{len(feed_urls)} alert feeds decodable")
    return Result("alerts-realtime", _worst(statuses), "; ".join(details))


def check_bus_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    api_key: str | None,
    *,
    url: str | None = None,
    stale_s: float = REALTIME_STALE_S,
) -> Result:
    """The MTA bus VehiclePositions feed, which needs a key. WARN-skipped when no
    key is available (the monitor cannot reach the feed without one, and that is
    a config choice, not an outage). The key rides as a query param through the
    injected fetcher and any error detail is sanitized, so the key-bearing URL
    never prints.

    NOTE: fetch_vehicle_positions decodes inline (no exposed bytes-level bus
    decoder), so this check does a minimal protobuf parse and a vehicle-entity
    count rather than the full _in_nyc/route decode. FOLLOWUP: extract a
    _decode_vehicles(raw) from feeds.buses so the monitor can exercise the whole
    bus decode the way it does the other systems."""
    if not api_key:
        return Result("bus-realtime", WARN, "skipped (MTA_BUS_API_KEY not set)")
    url = url if url is not None else feeds.VEHICLE_POSITIONS_URL
    res, detail = _fetch_retrying(fetch, url, sleep, params={"key": api_key})
    if res is None:
        return Result("bus-realtime", FAIL, f"feed unreachable ({detail})")
    try:
        feed = _parse_feed(res.content)
    except DecodeError as exc:
        return Result("bus-realtime", FAIL, f"undecodable ({_sanitize(exc)})")
    vehicles = sum(1 for e in feed.entity if e.HasField("vehicle"))
    ts = feeds._header_timestamp(feed)
    statuses: list[str] = []
    details: list[str] = []
    if vehicles == 0:
        # NYC buses run overnight, so an empty vehicle feed is unusual, but it
        # bands to WARN: depot lulls and holiday schedules can thin it, and a
        # flapping FAIL on a best-effort keyed check would get muted.
        statuses.append(WARN)
        details.append("no vehicles in feed")
    if ts is not None and now - ts > stale_s:
        statuses.append(WARN)
        details.append(f"header older than {int(stale_s)}s")
    if not statuses:
        return Result("bus-realtime", PASS, f"{vehicles} vehicles, fresh")
    return Result("bus-realtime", _worst(statuses), "; ".join(details))


# Small indirections so a test can point the courteous UA without reaching into
# feeds, and so the intent (community host) reads clearly at each call site.
def _path_ua() -> dict[str, str]:
    return dict(_COURTEOUS_UA)


def _ferry_ua() -> dict[str, str]:
    return dict(_COURTEOUS_UA)


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------


def _parse_zip_bytes(parse_zip: Callable[[Path], dict], raw: bytes, filename: str) -> dict:
    """Run a production _parse_zip(path) parser over fetched bytes by writing them
    to a temp file first (those parsers take a filesystem path). Reused for PATH
    and ferry, whose _parse_zip functions return the whole parsed feed."""
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / filename
        zpath.write_bytes(raw)
        return parse_zip(zpath)


def _parse_subway_bytes(raw: bytes) -> dict:
    """Parse subway stops/stations/route-shapes from fetched zip bytes by reusing
    static_data's parsers. Those read a fixed module path and take no bytes
    argument, so the monitor writes a temp copy and points the module constant at
    it for the parse, restoring it after. WHY not fork the parse: static_data
    exposes no bytes-level entry point, and reimplementing its stops.txt /
    shapes.txt parse is exactly the divergence this monitor exists to catch.
    load_subway_stations / load_subway_route_shapes swallow their own errors and
    return empty, so stops (which raises on a bad zip) is the parse-validity
    signal."""
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "gtfs_subway.zip"
        zpath.write_bytes(raw)
        original = static_data.SUBWAY_GTFS_ZIP
        static_data.SUBWAY_GTFS_ZIP = zpath
        try:
            stops = static_data._parse_stops()
            stations = static_data.load_subway_stations()
            shapes = static_data.load_subway_route_shapes()
        finally:
            static_data.SUBWAY_GTFS_ZIP = original
    return {"stops": stops, "stations": stations, "shapes": shapes}


def _feed_end_date_status(
    zf: zipfile.ZipFile, members: set[str], now: float, tz
) -> tuple[str, str] | None:
    """(status, detail) from feed_info.txt's feed_end_date, or None when the zip
    ships no feed_info/feed_end_date (the check is 'where provided'). An end date
    already past means the published schedule has expired (FAIL); one within
    FEED_END_WARN_DAYS is about to (WARN). The date is treated as valid through
    the END of that day (a one-day grace) so the last service day is not flagged.

    No production module parses feed_info today, so this small reader is
    monitor-local; it does not fork any existing parser. FOLLOWUP: promote it to
    a shared helper if a phase ever needs feed_info in the app."""
    if "feed_info.txt" not in members:
        return None
    with zf.open("feed_info.txt") as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
        row = next(reader, None)
    if not row:
        return None
    end = (row.get("feed_end_date") or "").strip()
    if not end:
        return None
    try:
        end_day = datetime(int(end[:4]), int(end[4:6]), int(end[6:8]), tzinfo=tz)
        # Valid through the END of that day (a one-day grace) so the last service
        # day is not flagged. A far-future "never expires" sentinel (e.g.
        # 99991231) pushes end_day + one day past datetime.max, and .timestamp()
        # on an extreme year can also overflow; either way the feed is
        # unambiguously not expiring soon, so treat it as healthy (no line)
        # rather than let the OverflowError propagate and abort the whole run.
        end_ts = (end_day + timedelta(days=1)).timestamp()
    except (ValueError, IndexError, OverflowError, OSError):
        return None  # malformed or beyond-range date is not this check's to police
    if end_ts < now:
        return (FAIL, f"feed_end_date {end} is in the past")
    if end_ts - now < FEED_END_WARN_DAYS * 86400:
        return (WARN, f"feed_end_date {end} is within {FEED_END_WARN_DAYS} days")
    return (PASS, f"feed_end_date {end}")


def _apply_end_status(
    statuses: list[str], details: list[str], end_status: tuple[str, str] | None, prefix: str = ""
) -> None:
    """Fold an end-date result into a check's accumulators, surfacing only WARN or
    FAIL (a comfortably-future end date needs no line)."""
    if end_status is None or end_status[0] == PASS:
        return
    statuses.append(end_status[0])
    details.append(prefix + end_status[1])


def check_subway_static(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    *,
    url: str | None = None,
    tz=None,
) -> tuple[Result, dict | None]:
    """Subway static zip: reachable, a valid zip, required members present,
    parseable by the production parsers, and a generous stop-count floor. Returns
    the parsed tables so the subway realtime check can decode against real
    stops."""
    url = url if url is not None else static_data.SUBWAY_GTFS_URL
    tz = tz if tz is not None else feeds.NYC_TZ
    res, detail = _fetch_retrying(fetch, url, sleep)
    if res is None:
        return Result("subway-static", FAIL, f"unreachable ({detail})"), None
    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            members = set(zf.namelist())
            end_status = _feed_end_date_status(zf, members, now, tz)
        parsed = _parse_subway_bytes(res.content)
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        return Result("subway-static", FAIL, f"unparseable ({_sanitize(exc)})"), None
    statuses: list[str] = []
    details: list[str] = []
    _check_members(statuses, details, members, SUBWAY_REQUIRED_MEMBERS)
    stops = parsed["stops"]
    if len(stops) < SUBWAY_STATIC_MIN_STOPS:
        statuses.append(FAIL)
        details.append(f"only {len(stops)} stops (< {SUBWAY_STATIC_MIN_STOPS})")
    _apply_end_status(statuses, details, end_status)
    if not statuses:
        return Result("subway-static", PASS, f"{len(stops)} stops parsed"), parsed
    return Result("subway-static", _worst(statuses), "; ".join(details)), parsed


def check_railroad_static(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    *,
    urls: dict[str, str] | None = None,
    tz=None,
) -> tuple[Result, dict]:
    """Both railroad static zips (LIRR, MNR): reachable, valid, required members
    present, parseable by railroad_static._parse_stops, and a generous stop floor
    each. Returns {system: stops} for the railroad realtime check."""
    urls = urls if urls is not None else railroad_static.RAILROAD_STATIC_URLS
    tz = tz if tz is not None else feeds.NYC_TZ
    parsed: dict[str, dict] = {}
    statuses: list[str] = []
    details: list[str] = []
    for system, url in urls.items():
        res, detail = _fetch_retrying(fetch, url, sleep)
        if res is None:
            statuses.append(FAIL)
            details.append(f"{system} unreachable ({detail})")
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
                members = set(zf.namelist())
                stops = railroad_static._parse_stops(zf)
                end_status = _feed_end_date_status(zf, members, now, tz)
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
            statuses.append(FAIL)
            details.append(f"{system} unparseable ({_sanitize(exc)})")
            continue
        parsed[system] = stops
        _check_members(statuses, details, members, RAILROAD_REQUIRED_MEMBERS, prefix=f"{system} ")
        if len(stops) < RAILROAD_STATIC_MIN_STOPS:
            statuses.append(FAIL)
            details.append(f"{system} only {len(stops)} stops (< {RAILROAD_STATIC_MIN_STOPS})")
        _apply_end_status(statuses, details, end_status, prefix=f"{system} ")
    if not statuses:
        counts = ", ".join(f"{s} {len(st)}" for s, st in parsed.items())
        return Result("railroad-static", PASS, f"stops within bands ({counts})"), parsed
    return Result("railroad-static", _worst(statuses), "; ".join(details)), parsed


def check_path_static(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    *,
    url: str | None = None,
    tz=None,
) -> tuple[Result, dict | None]:
    """PATH static zip: reachable, valid, required members present (including
    stop_times.txt), parseable by path_static._parse_zip, parent count above the
    floor, and the identity spot check (station 26733 exists and is named
    Newark). Returns the parsed tables for the PATH realtime check."""
    url = url if url is not None else path_static.PATH_STATIC_URL
    tz = tz if tz is not None else feeds.NYC_TZ
    res, detail = _fetch_retrying(fetch, url, sleep)
    if res is None:
        return Result("path-static", FAIL, f"unreachable ({detail})"), None
    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            members = set(zf.namelist())
            end_status = _feed_end_date_status(zf, members, now, tz)
        parsed = _parse_zip_bytes(path_static._parse_zip, res.content, "gtfs_path.zip")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        return Result("path-static", FAIL, f"unparseable ({_sanitize(exc)})"), None
    statuses: list[str] = []
    details: list[str] = []
    _check_members(statuses, details, members, PATH_REQUIRED_MEMBERS)
    stops = parsed.get("stops") or {}
    if len(stops) < PATH_STATIC_MIN_PARENTS:
        statuses.append(FAIL)
        details.append(f"only {len(stops)} parent stations (< {PATH_STATIC_MIN_PARENTS})")
    newark = stops.get("26733")
    if newark is None:
        statuses.append(FAIL)
        details.append("identity stop 26733 missing")
    elif "Newark" not in (newark.get("name") or ""):
        statuses.append(FAIL)
        details.append(f"stop 26733 name is {newark.get('name')!r}, expected Newark")
    _apply_end_status(statuses, details, end_status)
    if not statuses:
        return Result("path-static", PASS, f"{len(stops)} parents, 26733=Newark"), parsed
    return Result("path-static", _worst(statuses), "; ".join(details)), parsed


def check_ferry_static(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    *,
    url: str | None = None,
    tz=None,
) -> tuple[Result, dict | None]:
    """NYC Ferry static zip: reachable, valid, required members present (including
    stop_times.txt), parseable by ferry_static._parse_zip, route and stop counts
    above their floors, and the identity spot check (route ER exists). Fetched
    with the courteous UA (Connexionz is a community host). Returns the parsed
    tables so the ferry realtime check can join trips to routes."""
    url = url if url is not None else ferry_static.FERRY_STATIC_URL
    tz = tz if tz is not None else feeds.NYC_TZ
    res, detail = _fetch_retrying(fetch, url, sleep, headers=_ferry_ua())
    if res is None:
        return Result("ferry-static", FAIL, f"unreachable ({detail})"), None
    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            members = set(zf.namelist())
            end_status = _feed_end_date_status(zf, members, now, tz)
        parsed = _parse_zip_bytes(ferry_static._parse_zip, res.content, "gtfs_ferry.zip")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        return Result("ferry-static", FAIL, f"unparseable ({_sanitize(exc)})"), None
    statuses: list[str] = []
    details: list[str] = []
    _check_members(statuses, details, members, FERRY_REQUIRED_MEMBERS)
    routes = parsed.get("routes") or {}
    stops = parsed.get("stops") or {}
    if len(routes) < FERRY_STATIC_MIN_ROUTES:
        statuses.append(FAIL)
        details.append(f"only {len(routes)} routes (< {FERRY_STATIC_MIN_ROUTES})")
    if len(stops) < FERRY_STATIC_MIN_STOPS:
        statuses.append(FAIL)
        details.append(f"only {len(stops)} stops (< {FERRY_STATIC_MIN_STOPS})")
    if "ER" not in routes:
        statuses.append(FAIL)
        details.append("identity route ER (East River) missing")
    _apply_end_status(statuses, details, end_status)
    if not statuses:
        return Result("ferry-static", PASS, f"{len(routes)} routes, {len(stops)} stops, ER"), parsed
    return Result("ferry-static", _worst(statuses), "; ".join(details)), parsed


def _check_members(
    statuses: list[str],
    details: list[str],
    members: set[str],
    required: tuple[str, ...],
    prefix: str = "",
) -> None:
    """Fold a required-member presence check into a static check's accumulators.
    A missing member is a FAIL (the feed is structurally truncated)."""
    missing = [m for m in required if m not in members]
    if missing:
        statuses.append(FAIL)
        details.append(prefix + "missing members: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# Production deployment check
# ---------------------------------------------------------------------------


def check_production(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    status_url: str | None,
    *,
    stale_s: float = PRODUCTION_FEED_STALE_S,
) -> list[Result]:
    """The live deployment via MONITOR_STATUS_URL + /api/status. WARN-skipped as a
    single line when the variable is unset. When set, returns a line each for:
    reachability (FAIL on non-200/non-JSON/non-object), each static group's state
    (FAIL only on a definitively FAILED warmup; a still-loading or absent group is
    a tolerated transient WARN, matching /healthz and the non-flapping design),
    per-feed poll freshness (WARN, see PRODUCTION_FEED_STALE_S for why not FAIL),
    and degraded alert systems (WARN when non-empty)."""
    if not status_url:
        return [Result("production", WARN, "skipped (MONITOR_STATUS_URL not set)")]
    url = status_url.rstrip("/") + "/api/status"
    res, detail = _fetch_retrying(fetch, url, sleep)
    if res is None:
        return [Result("production:status", FAIL, f"/api/status unreachable ({detail})")]
    try:
        data = json.loads(res.content)
    except (ValueError, UnicodeDecodeError) as exc:
        return [Result("production:status", FAIL, f"/api/status non-JSON ({_sanitize(exc)})")]
    if not isinstance(data, dict):
        # Valid JSON but not an object (null, a list, a bare number/string from a
        # proxy or error page). Fail only this line rather than letting the later
        # data.get(...) raise and abort the whole run, discarding every other
        # check's Result.
        return [Result("production:status", FAIL, "/api/status returned non-object JSON")]

    results = [Result("production:status", PASS, "/api/status reachable")]

    static_fields = ("subway_static", "railroad_static", "path_static", "ferry_static")
    # FAIL only on a definitively "failed" warmup (it retries but stays down). A
    # "loading" or absent group is the normal cold-start / redeploy transient the
    # app's own /healthz tolerates, so it is a WARN, not a page: a 6-hourly probe
    # must not flap red just because it landed mid-warmup.
    failed = [f for f in static_fields if data.get(f) == "failed"]
    warming = [f for f in static_fields if data.get(f) not in ("ready", "failed")]
    if failed:
        results.append(
            Result(
                "production:statics",
                FAIL,
                "failed: " + ", ".join(f"{f}={data.get(f)!r}" for f in failed),
            )
        )
    elif warming:
        results.append(
            Result(
                "production:statics",
                WARN,
                "not yet ready: " + ", ".join(f"{f}={data.get(f)!r}" for f in warming),
            )
        )
    else:
        results.append(Result("production:statics", PASS, "all static groups ready"))

    # isinstance guards below: /api/status is our own modeled endpoint, but a
    # proxy or error page in front of the deployment could return a differently
    # shaped JSON object. Coercing an unexpected type to empty keeps a malformed
    # body from raising (the same crash class as the non-object guard above); the
    # empty then surfaces as a WARN rather than aborting every other check.
    feeds_raw = data.get("feeds")
    feeds_map = feeds_raw if isinstance(feeds_raw, dict) else {}
    if not feeds_map:
        # A healthy running deployment always reports its live feeds here; an
        # empty, absent, or wrong-typed map means the feed cache never populated
        # (a broken startup), which the "0 feeds fresh" PASS would otherwise hide.
        results.append(Result("production:feeds", WARN, "no feeds reported by /api/status"))
    else:
        stale = []
        for name, entry in feeds_map.items():
            age = entry.get("age_s") if isinstance(entry, dict) else None
            if age is None or age > stale_s:
                stale.append(f"{name}={age}")
        if stale:
            results.append(Result("production:feeds", WARN, "stale/absent: " + ", ".join(stale)))
        else:
            results.append(Result("production:feeds", PASS, f"{len(feeds_map)} feeds fresh"))

    alerts_obj = data.get("alerts")
    degraded_raw = alerts_obj.get("degraded_systems") if isinstance(alerts_obj, dict) else None
    degraded = degraded_raw if isinstance(degraded_raw, list) else []
    if degraded:
        names = ", ".join(str(s) for s in degraded)
        results.append(Result("production:alerts", WARN, "degraded alert systems: " + names))
    else:
        results.append(Result("production:alerts", PASS, "no degraded alert systems"))

    return results


# ---------------------------------------------------------------------------
# Runner and output
# ---------------------------------------------------------------------------


def run_all(
    fetch: Fetcher, sleep: Callable[[float], None], now: float, env: dict[str, str]
) -> list[Result]:
    """Run every check and return the full result list. Statics run first because
    each yields the parsed tables the matching realtime decoder needs; a failed
    static passes an empty table on, so the realtime decode still runs (just
    without resolving stops) rather than being skipped."""
    results: list[Result] = []

    subway_res, subway = check_subway_static(fetch, sleep, now)
    railroad_res, railroad = check_railroad_static(fetch, sleep, now)
    path_res, path = check_path_static(fetch, sleep, now)
    ferry_res, ferry = check_ferry_static(fetch, sleep, now)
    results += [subway_res, railroad_res, path_res, ferry_res]

    results.append(check_subway_realtime(fetch, sleep, now, (subway or {}).get("stops", {})))
    results.append(check_railroad_realtime(fetch, sleep, now, railroad))
    results.append(check_path_realtime(fetch, sleep, now, (path or {}).get("stops", {})))
    results.append(check_ferry_realtime(fetch, sleep, now, (ferry or {}).get("trips", {})))
    results.append(check_alerts_realtime(fetch, sleep, now))
    results.append(check_bus_realtime(fetch, sleep, now, env.get("MTA_BUS_API_KEY")))

    results += check_production(fetch, sleep, now, env.get("MONITOR_STATUS_URL"))
    return results


def format_lines(results: list[Result]) -> str:
    """One aligned line per check for the run log."""
    return "".join(f"{r.status:4}  {r.name}: {r.detail}\n" for r in results)


def format_summary_table(results: list[Result]) -> str:
    """A GitHub-flavored markdown table for the Actions job summary."""
    lines = ["| Check | Status | Detail |", "| --- | --- | --- |"]
    for r in results:
        detail = r.detail.replace("|", "\\|")
        lines.append(f"| {r.name} | {r.status} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    env = dict(os.environ)
    now = time.time()
    fetch = make_httpx_fetcher()
    results = run_all(fetch, time.sleep, now, env)

    sys.stdout.write(format_lines(results))

    summary_path = env.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(format_summary_table(results))

    fails = [r for r in results if r.status == FAIL]
    warns = [r for r in results if r.status == WARN]
    sys.stdout.write(f"\n{len(fails)} FAIL, {len(warns)} WARN, {len(results)} checks total\n")
    # Exit non-zero ONLY on a FAIL. WARNs always appear but never fail the run,
    # so the schedule stays green through the normal, expected conditions.
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
