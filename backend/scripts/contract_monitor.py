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
import re
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

import env_seams  # noqa: E402
import feeds  # noqa: E402
import ferry_static  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import path_static  # noqa: E402
import railroad_static  # noqa: E402
import static_data  # noqa: E402
from cache import _sanitize_upstream as _sanitize  # noqa: E402
from feeds import njt as njt_feed  # noqa: E402

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


# A fetcher takes (url, headers, params, files) and returns a FetchResult, or
# raises on a transport error. Injected everywhere so tests never touch the
# network. `files` is the NJ Transit addition (15a): passing it makes the request a
# POST carrying multipart/form-data, which is the ONLY shape the RailData
# endpoints answer. Every other source here is a GET and passes it as None.
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
    files: dict[str, str] | None = None,
) -> tuple[FetchResult | None, str]:
    """Fetch once, and on a transport error OR a non-200, wait a short delay and
    try exactly once more. Returns (result, "") on a 200, else (None, detail)
    where detail is a sanitized reason for the caller's FAIL line. Any URL in an
    exception string (including a key-bearing bus URL) is scrubbed by _sanitize,
    so a failure detail never leaks a secret.

    `files` turns the request into a POST (NJ Transit, 15a). RETRYING SUCH A
    REQUEST IS SAFE ONLY BECAUSE THE CALLER ALREADY HOLDS ITS TOKEN: the retry
    re-sends the same form, so it costs one more data fetch and no additional
    mint. The mint itself must never come through here, and does not."""
    last = ""
    for attempt in (1, 2):
        try:
            res = fetch(url, headers=headers, params=params, files=files)
        except Exception as exc:  # noqa: BLE001 - any transport failure is a miss
            last = f"transport error: {_sanitize(exc)}"
        else:
            if res.status == 200:
                return res, ""
            last = f"HTTP {res.status}"
        if attempt == 1:
            sleep(RETRY_DELAY_S)
    return None, last


def make_httpx_fetcher(
    timeout: float = REQUEST_TIMEOUT_S,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> Fetcher:
    """The production fetcher: a follow-redirects GET, or, when `files` is given, a
    POST that never follows one.

    THE GET ARM FOLLOWS REDIRECTS because two static sources (ferry utility URL,
    the MTA developer static paths) 30x to their real zip. A GET carries no body,
    so there is nothing a redirect could deliver anywhere it should not go.

    THE POST ARM NEVER FOLLOWS ONE (Audit 4, F2), and the asymmetry is the point.
    The POST arm exists for NJ Transit and only for it: every RailData endpoint is
    POST multipart/form-data with the token as a form field, and the mint carries
    the username and password the same way. httpx re-sends a POST body on a 307 or
    308, to a different origin included, so a redirect from raildata would deliver
    this environment's credentials to whatever host the Location named. The GET
    arm's reasoning is about a body the upstream SENDS and does not extend to one we
    send. A 3xx comes back as the status it is, and every caller reads it as a
    non-200: _NjtToken fails the mint, _fetch_retrying fails the fetch.

    httpx sends multipart when handed `files`, and (None, value) is its documented
    way to put a plain text field into a multipart body without inventing a
    filename. It stays one fetcher rather than two so the injection seam the whole
    suite is built on does not fork.

    `client_factory` builds the httpx.Client each request goes through; None means a
    plain client. It is an injection seam so a test can prove the redirect rule of
    each arm against a real httpx client over a mock transport, with no socket and
    no monkeypatching of httpx. The timeout is still passed per request, so an
    injected client cannot loosen the ceiling by accident."""

    def client() -> httpx.Client:
        return httpx.Client() if client_factory is None else client_factory()

    def fetch(
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        files: dict[str, str] | None = None,
    ) -> FetchResult:
        with client() as http:
            if files is not None:
                resp = http.post(
                    url,
                    headers=headers,
                    params=params,
                    files={key: (None, value) for key, value in files.items()},
                    follow_redirects=False,
                    timeout=timeout,
                )
            else:
                resp = http.get(
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

# Production /api/status per-feed poll-age BANDS. Two edges, not one, because a
# single threshold forces a choice between crying wolf and saying nothing:
#
#   < 600s          PASS. Normal.
#   600s to 1800s   WARN. Worth a look. Upstream blips at this scale recover on
#                   their own, and the direct upstream checks above are the
#                   authoritative signal for whether the provider is down.
#   > 1800s         FAIL. Half an hour of no successful poll is not a blip, it is
#                   an outage a person should see today.
#
# The GAP between the two edges is the point: it is what keeps a flapping upstream
# from training the operator to ignore FAILs. A monitor whose red light is usually
# noise is worse than no monitor, so FAIL is reserved for "this is really broken".
PRODUCTION_FEED_STALE_S = 600.0
PRODUCTION_FEED_FAIL_S = 1800.0

# Mirrors feeds.alerts.ALERT_RETENTION_MAX_S (1800). MIRRORED, not imported: this
# script runs standalone in CI against the PUBLIC deployment with no app package on
# sys.path, which is what lets it catch a bad deploy rather than testing the code it
# was built from. The coupling is real, so if the backend's retention horizon moves,
# move this with it: past that horizon the backend has DROPPED a down system's
# retained alerts, so riders are no longer seeing them and the outage stops being
# invisible-but-covered.
PRODUCTION_ALERT_RETENTION_MAX_S = 1800.0

# The degraded codes /healthz publishes. MIRRORED, not imported, for exactly the
# reason stated just above: what the monitor DEMANDS of production has to be its
# own statement, so that editing the app cannot quietly edit the monitor's
# expectations. Importing models.HEALTH_DEGRADED_CODES would mean deleting a code
# there silently stops this watching that state, which is the F1 blindness all
# over again, one level up.
#
# The coupling is real and is held by a test rather than by an import:
# test_contract_monitor asserts this tuple equals models.HEALTH_DEGRADED_CODES,
# so teaching the probe a new degraded state without teaching the monitor to
# watch it fails CI.
PRODUCTION_HEALTH_CODES = (
    "no-feed-fresh",
    "bus-route-index-failed",
    "subway-static-failed",
    "feed-content-stale",
    "subway-groups-down",
    "njt-mint-quota",
)

# Prose for the codes whose NAME does not say what the operator should do. Only one
# needs it: every other code reports something broken, and "njt-mint-quota" reports
# a budget that is spent, which is a different instruction entirely. OURS, not the
# deployment's: this is a literal in this file keyed by a code from this file's own
# tuple, so the "only recognized codes are printed" rule in _check_production_health
# is not widened by it. Nothing from the wire is echoed.
_HEALTH_CODE_NOTES = {
    "njt-mint-quota": (
        "production has spent NJ Transit's ten mints for the Eastern day, so its NJ "
        "Transit layer is dark until midnight; this is the budget, not an NJ Transit "
        "outage, and a redeploy would spend another mint rather than fix it"
    ),
}

# How far apart the two /api/status probes sit when witnessing a replayed
# served_at. Two seconds, and both ends of that are chosen: long enough that a
# live deployment's second response is unambiguously built later (served_at is
# stamped per response from time.time(), so live deltas are never zero), and far
# inside FEED_STALE_AFTER_S (90) so nothing the two payloads report can
# legitimately change between them and no other check has to reason about which
# probe it read.
PRODUCTION_REPLAY_PROBE_GAP_S = 2.0

# A live second response is built at least PRODUCTION_REPLAY_PROBE_GAP_S after the
# first, because the monitor slept that long after receiving the first one. Half
# of it is therefore a wide margin that still fails a frozen served_at (delta 0),
# a clock stepped backwards (delta < 0), and a caching layer with a TTL up to half
# the gap. Kept as a fraction of the gap rather than an absolute so the two move
# together.
PRODUCTION_REPLAY_MIN_ADVANCE_RATIO = 0.5

# Static count floors, all set well below the real numbers so ordinary feed
# churn never trips them; they exist to catch a gutted or truncated feed.
SUBWAY_STATIC_MIN_STOPS = 100  # real stops.txt carries ~1900 platform+parent rows
RAILROAD_STATIC_MIN_STOPS = 20  # LIRR ~240, MNR ~180 stops
PATH_STATIC_MIN_PARENTS = 10  # 13 PATH parent stations
FERRY_STATIC_MIN_ROUTES = 7  # 9 NYC Ferry routes
FERRY_STATIC_MIN_STOPS = 40  # 50 NYC Ferry stops
NJT_STATIC_MIN_ROUTES = 10  # 12 NJ Transit rail routes (probed 2026-08-05)
NJT_STATIC_MIN_STOPS = 140  # 172 NJ Transit rail stops

# NJ Transit's staleness band, and it exists because this feed answers the
# staleness question differently from every other one here. There is NO
# feed_info.txt and therefore no feed_end_date, so _feed_end_date_status has
# nothing to read; service is the 8,697-row calendar_dates table, and the last day
# it schedules IS the feed's end date. The loader's validator draws the hard line
# (a feed that leads today by nothing cannot be served from at all); this is the
# approach warning, deliberately the same 30 days as FEED_END_WARN_DAYS so the two
# staleness stories read alike even though they read different tables.
NJT_SERVICE_WARN_DAYS = 30

# Required zip members per static source: the files the production parser opens.
# stop_times.txt is required for PATH and ferry (a real GTFS always ships it,
# and its absence in a live feed is a real truncation) even though the trimmed
# test fixtures omit it for size; the presence check is structural, separate
# from the parse.
SUBWAY_REQUIRED_MEMBERS = ("stops.txt", "shapes.txt")
RAILROAD_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt")
PATH_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt", "stop_times.txt")
FERRY_REQUIRED_MEMBERS = ("stops.txt", "trips.txt", "shapes.txt", "stop_times.txt")
# NJ Transit ships NO calendar.txt and NO feed_info.txt, by design, so neither can
# be required here; requiring either would FAIL the monitor on every valid
# publication. calendar_dates.txt takes calendar's place and IS required, because
# without it the feed carries no service span at all.
NJT_REQUIRED_MEMBERS = (
    "agency.txt",
    "routes.txt",
    "trips.txt",
    "stops.txt",
    "stop_times.txt",
    "calendar_dates.txt",
)

# Members the monitor WATCHES but the app does not need. A separate tuple with a
# separate band, because the two answer different questions and conflating them
# gets the severity wrong in the direction that matters.
#
# shapes.txt is the case. njt_static deliberately does not parse it (15a defers
# geometry to 15c) and does not require it, and a hermetic test pins that a
# publication without it still serves. So upstream dropping it is worth a HUMAN
# LOOK, which is what WARN means in this file, and is emphatically not "this is
# really broken", which is what FAIL means and what exits the run non-zero. Listing
# it as required turned the 6-hourly schedule red over a member the app was
# designed to survive losing.
NJT_WATCHED_MEMBERS = ("shapes.txt",)

# stops.txt columns njt_static._parse_stops READS to build a marker: the id, the
# name the identity spot check compares, and the two coordinates a row without
# which is dropped. Watched BY NAME because that is the only way to notice the
# loss early: a publication that drops stop_lat still parses cleanly and simply
# yields zero stops, which arrives here as the min-stops FAIL with no hint that a
# column went missing rather than the feed emptying.
NJT_STOPS_REQUIRED_COLUMNS = ("stop_id", "stop_name", "stop_lat", "stop_lon")

# The two OPTIONAL GTFS columns that DESCRIBE a parent/child station model, listed
# by name rather than accepted as "any new column": a third column appearing is
# still worth someone reading the spec for, and a blanket allowance would hide it.
#
# THEIR PRESENCE IN THE HEADER MEANS NOTHING, which is the whole diagnosis behind
# the standing WARN this replaced. GTFS makes both optional and lets a publisher
# declare them with an EMPTY value on every row, which is exactly how a flat feed
# spells "flat"; NJ Transit began doing that between runs #110 and #111
# (2026-08-07). A header test therefore cannot tell "the feed went hierarchical"
# from "the publisher widened its header", and it answered WARN to both for a
# month. The shape lives in the ROWS, so that is where _njt_stops_shape looks.
NJT_STOPS_HIERARCHY_COLUMNS = ("location_type", "parent_station")

# NJ Transit Rail's daily service window in ET, and the two REALTIME bands, all
# three DERIVED FROM THE PEAK PROBE. The working is at THE FRESHNESS BUDGET,
# DERIVED in backend/feeds/njt.py and is not repeated here; the one line worth
# carrying is the probe's own caveat, that "the overnight numbers are optimistic
# by roughly 2x". A band set from an overnight sample would be about half of what
# peak needs and would page every weekday morning.
#
# THE WINDOW WRAPS MIDNIGHT, unlike the ferry's, because NJ Transit Rail does. The
# last trains reach their terminals after 01:00 and the first leave before 05:30,
# so a simple start <= t <= end test would call the busiest end of the night
# "closed" and the quietest part of the morning "in service". Generous at both
# ends for the same reason the ferry window is: a schedule that shifts by twenty
# minutes must not move the monitor.
NJT_SERVICE_START = dt_time(5, 0)
NJT_SERVICE_END = dt_time(1, 30)

# Header lag: how far behind the feed's own content clock may fall.
#
#   observed at peak   9s to 23s, worst observed rather than a tail estimate
#   generation period  ~11.8s
#
#   < 120s   PASS. Ten generation periods and over 5x the worst observed lag.
#   120-600s WARN. The publisher has missed several generations. Worth a look, and
#            deliberately not a page: brief publisher hiccups recover alone.
#   > 600s   FAIL. Matches the house REALTIME_STALE_S for the MTA feeds, and means
#            the same thing here: this is not jitter, the upstream stopped.
#
# The GAP between the edges is the point, exactly as at PRODUCTION_FEED_STALE_S: a
# monitor whose red light is usually noise is worse than no monitor.
NJT_HEADER_LAG_WARN_S = 120.0
NJT_HEADER_LAG_FAIL_S = 600.0

# Trips in flight before the placement claim below is worth making. The peak probe
# counted 745; well under any real service level, so a quiet Sunday afternoon
# still clears it, while a feed that has emptied out does not get a ratio computed
# from three trips.
NJT_MIN_TRIPS_FOR_RATIO = 20

# THE WATCHED RATIO, and the assumption it watches (15b directive).
#
# WHAT IS ASSUMED. feeds.njt._place records one assumption the probes did not
# measure directly: that this feed RETAINS ALREADY-PASSED STOPS. Placement is
# built on it. A running train is expected to have a passed call behind it and a
# future call ahead, so it lands in the dwelling case or the interpolate-between
# case. If NJ Transit ever starts PRUNING passed stops the way the railroad feeds
# do, every running train instead arrives at the not-yet-departed branch, where
# MAX_FUTURE_FIRST_STOP_S (180s, three minutes) drops anything whose first listed
# call is further out than that. NJT stop spacing at rush is minutes to tens of
# minutes, so nearly every train would vanish from the map while its arrivals
# stayed on the boards.
#
# WHY THIS IS A MONITOR CHECK AND NOT A COMMENT. A detectable failure signature
# belongs to the monitor rather than to a future rider's confusion. The decoder
# comment says what breaks; this says it out loud, on a schedule, before anyone
# has to notice a thinning map.
#
# THE DERIVATION, and why there is no invented percentage in it. The denominator
# is not "all trips": whether this feed carries the whole service day or only what
# is in flight is not something either probe settled, so a ratio over all trips
# would have an unknown healthy value and could not be banded honestly. The
# denominator is instead trips whose calls STRADDLE now, one behind and one ahead,
# which is the feed's own statement that the train is running this minute. That
# makes both halves of the check derivable rather than guessed:
#
#   1. STRADDLING COUNT. Under the assumption, service hours with hundreds of
#      trips in the feed always produce some straddling trips. Under the broken
#      assumption there are NONE, because there are no passed calls to straddle
#      with. Zero straddling trips while the feed is full IS the signature, and it
#      needs no percentage at all.
#   2. PLACED OVER STRADDLING. A trip with a call behind and a call ahead lands in
#      _place case 1 or case 3 by construction, so the healthy value is 1.0, not
#      an estimate. The floor allows for the handful of trips whose straddling
#      pair is missing both times on one side (the probe found both times present
#      on 103 of 103 calls, so this is headroom rather than an expected loss).
#
# Both are WARN. This watches an ASSUMPTION, and the honest response to "the thing
# we assumed may have changed" is a human reading the feed, not a page.
NJT_PLACEMENT_FLOOR = 0.95

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
    """Every KEYLESS service-alerts feed: reachable and decodable by the production
    _decode_alerts. NO entity floor: zero active alerts is a valid, common, and
    good steady state, so emptiness is never a fault here.

    NJ TRANSIT'S ALERT FEED IS NOT CHECKED HERE, and its absence is structural
    rather than an oversight. That feed is a POST carrying a token, so a GET at it
    returns nothing decodable and this check would report a permanent FAIL against
    a feed it never spoke to. It is checked by check_njt_realtime, which holds the
    run's one minted token; feeds.KEYLESS_ALERT_FEEDS is the seam, and the tests
    below pin njt out of this set and into the full one so the split stays
    deliberate."""
    feed_urls = feed_urls if feed_urls is not None else feeds.KEYLESS_ALERT_FEEDS
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


def _in_njt_service_hours(now: float, tz) -> bool:
    """True when `now` falls inside NJ Transit Rail's daily service window in ET.

    THE WINDOW WRAPS MIDNIGHT (05:00 to 01:30), so this cannot be the ferry's
    simple start <= t <= end test: that would call 00:30, when the last trains are
    still running, "closed", and it would call 04:00, when nothing is moving,
    "in service". Judged in local time so it stays correct across DST with no
    offset arithmetic here.
    """
    local = datetime.fromtimestamp(now, tz).time()
    return local >= NJT_SERVICE_START or local <= NJT_SERVICE_END


def _njt_straddling_trips(feed, now: float, stops: dict[str, dict]) -> set[str]:
    """Trip ids the feed itself says are running RIGHT NOW: one call behind `now`,
    one ahead.

    READ STRAIGHT OFF THE WIRE, never through the decoder, and that is the whole
    point of the check this feeds. Computing the denominator with the code whose
    output is the numerator would make a decoder that placed nothing look like a
    feed with nothing running in it, which is the exact failure being watched for.

    CANCELED trips are excluded, because they are not running and the decoder is
    right to drop them; counting them would put a permanent gap between numerator
    and denominator that has nothing to do with the assumption.
    """
    straddling: set[str] = set()
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if tu.trip.schedule_relationship in feeds._DROP_TRIP_RELATIONSHIPS:
            continue
        behind = ahead = False
        for stu in tu.stop_time_update:
            # THE SAME TWO DROP RULES THE DECODER APPLIES, both of them, because a
            # ratio whose numerator and denominator filter differently reports a
            # gap that is its own arithmetic rather than a change in the feed.
            # feeds.njt._ordered_calls drops a call for EITHER reason:
            #
            #   * an unknown stop_id. njt_static drops a stops.txt row whose
            #     coordinates will not parse, so a live feed can name a stop the
            #     static table does not have.
            #   * any relationship in _DROP_STOP_RELATIONSHIPS, which is SKIPPED
            #     AND NO_DATA. Checking SKIPPED alone left NO_DATA counted here and
            #     dropped there, and this producer already ships 238
            #     relationship-with-times calls a peak poll, so a NO_DATA carrying
            #     times is exactly its habit.
            #
            # Either mismatch pushes placed/straddling below the floor and reports
            # "the feed stopped retaining passed stops" when nothing of the kind
            # happened. NJT_PLACEMENT_FLOOR's derivation claims the healthy value
            # is 1.0 by construction, and that claim is only true if both sides
            # filter identically.
            if not stu.stop_id or stu.stop_id not in stops:
                continue
            if stu.schedule_relationship in feeds._DROP_STOP_RELATIONSHIPS:
                continue
            for when in (stu.arrival, stu.departure):
                if not when.time:
                    continue
                behind = behind or when.time <= now
                ahead = ahead or when.time > now
        if behind and ahead and tu.trip.trip_id:
            straddling.add(tu.trip.trip_id)
    return straddling


def check_njt_realtime(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    parsed: dict | None,
    username: str | None,
    password: str | None,
    *,
    token: _NjtToken | None = None,
    token_url: str | None = None,
    tu_url: str | None = None,
    alerts_url: str | None = None,
    tz=None,
) -> Result:
    """NJ Transit realtime: TripUpdates and alerts, both POSTed behind the run's
    one token, both decoded by the production code.

    WARN-SKIPPED WITH NO CREDENTIALS, exactly like check_njt_static and
    check_bus_realtime: the monitor cannot reach a credentialed feed without
    credentials, and that is a configuration choice rather than an outage. Every
    fork and pull request context takes this path, which is precisely where a live
    NJ Transit POST must not happen.

    IT SHARES THE STATIC CHECK'S TOKEN. run_all builds one _NjtToken and hands it
    to both, so a run with three RailData consumers still costs one mint. The
    tests assert that arithmetic through run_all rather than trusting the wiring.

    THE ALERTS FEED IS CHECKED HERE rather than in check_alerts_realtime, and the
    split is structural: that check's fetcher GETs, and this endpoint answers only
    a POST carrying a token. feeds.KEYLESS_ALERT_FEEDS is the seam, and both sides
    of the split are asserted so it cannot decay into a gap.

    WHAT IT ASSERTS, in order of what a failure would mean:

      1. BOTH FEEDS REACHABLE AND STRICTLY PARSEABLE, through feeds.parse_feed and
         the production decoders. A pass means the real code path works against
         today's bytes, not that a protobuf arrived.
      2. HEADER LAG inside the bands derived at NJT_HEADER_LAG_WARN_S from the
         PEAK probe (never the overnight one).
      3. TRIPS PRESENT during service hours. Empty overnight is the correct
         answer, and the probe's own 13-byte valid feed is why an empty feed is
         never a fault outside those hours.
      4. THE WATCHED PLACEMENT RATIO, which is the 15b directive: the derivation
         and the assumption it guards are at NJT_PLACEMENT_FLOOR above.
    """
    creds = _njt_credentials(username, password)
    if creds is None:
        return Result(
            "njt-realtime",
            WARN,
            f"skipped ({njt_auth.USERNAME_VAR}/{njt_auth.PASSWORD_VAR} not set)",
        )
    username, password = creds
    tz = tz if tz is not None else feeds.NYC_TZ
    mint_url = token_url if token_url is not None else njt_auth.NJT_TOKEN_URL
    trip_url = tu_url if tu_url is not None else njt_feed.NJT_TU_URL
    alert_url = alerts_url if alerts_url is not None else njt_feed.NJT_ALERTS_URL

    minter = token if token is not None else _NjtToken(fetch, mint_url, username, password)
    minted_token, mint_detail = minter.get()
    if minted_token is None:
        return Result("njt-realtime", FAIL, mint_detail)
    form = {"token": minted_token}

    statuses: list[str] = []
    details: list[str] = []
    in_service = _in_njt_service_hours(now, tz)

    # -- the alerts feed, the simpler of the two ---------------------------
    alerts_res, alerts_detail = _fetch_retrying(fetch, alert_url, sleep, files=form)
    if alerts_res is None:
        statuses.append(FAIL)
        details.append(f"alerts down ({alerts_detail})")
    else:
        try:
            feeds._decode_alerts(alerts_res.content, feeds.NJT_ALERT_SYSTEM, now)
        except Exception as exc:  # noqa: BLE001 - the decoder raising on live data IS the break
            statuses.append(FAIL)
            details.append(f"alerts undecodable ({_sanitize(exc)})")
    # NO ENTITY FLOOR ON ALERTS, matching check_alerts_realtime: zero active
    # alerts is a valid, common and good steady state.

    # -- trip updates ------------------------------------------------------
    tu_res, tu_detail = _fetch_retrying(fetch, trip_url, sleep, files=form)
    if tu_res is None:
        statuses.append(FAIL)
        details.append(f"trip updates down ({tu_detail})")
        return Result("njt-realtime", _worst(statuses), "; ".join(details))
    try:
        feed = feeds.parse_feed(tu_res.content)
    except Exception as exc:  # noqa: BLE001 - a strict-parse failure is the break
        statuses.append(FAIL)
        details.append(f"trip updates undecodable ({_sanitize(exc)})")
        return Result("njt-realtime", _worst(statuses), "; ".join(details))

    header_ts = feeds._header_timestamp(feed)
    if header_ts is None:
        statuses.append(WARN)
        details.append("trip updates carry no header timestamp, so freshness cannot be judged")
    else:
        lag = now - header_ts
        if lag > NJT_HEADER_LAG_FAIL_S:
            statuses.append(FAIL)
            details.append(f"header {int(lag)}s behind (> {int(NJT_HEADER_LAG_FAIL_S)}s)")
        elif lag > NJT_HEADER_LAG_WARN_S:
            statuses.append(WARN)
            details.append(f"header {int(lag)}s behind (> {int(NJT_HEADER_LAG_WARN_S)}s)")

    trips_table = (parsed or {}).get("trips") or {}
    stops_table = (parsed or {}).get("stops") or {}
    entities = sum(1 for e in feed.entity if e.HasField("trip_update"))
    if not entities:
        if in_service:
            statuses.append(WARN)
            details.append("no trip updates during service hours")
        # Outside service hours an empty feed is the normal closed state and the
        # probe recorded exactly that as a 13-byte valid body. The "(closed)"
        # summary already conveys it, so no note is added.
        return _njt_rt_result(statuses, details, in_service, entities)

    if not stops_table or not trips_table:
        # The static check failed and its own result says why. The placement claim
        # cannot be assessed without those tables, and emitting a 0%-placed WARN
        # here would misattribute a monitor-side archive blip to the realtime feed.
        details.append("placement not checked (static NJ Transit tables unavailable)")
        return _njt_rt_result(statuses, details, in_service, entities)

    trains, _arrivals, _ts, warnings = njt_feed.decode_njt_trip_updates(
        tu_res.content, stops_table, trips_table, now
    )
    if warnings:
        # The entity.id / trip_short_name cross-check the probe measured at 745 of
        # 745. WARN rather than FAIL: nothing is served wrong when it drifts, but
        # it is the assumption feeds.njt._identity is built on and a drift that
        # STARTS is worth a human deciding about rather than a log line nobody reads.
        statuses.append(WARN)
        details.append(f"{len(warnings)} entity.id/trip_short_name mismatches (probe: 0 of 745)")

    straddling = _njt_straddling_trips(feed, now, stops_table)
    if entities >= NJT_MIN_TRIPS_FOR_RATIO and in_service:
        if not straddling:
            # THE ASSUMPTION'S SIGNATURE, and the reason this check exists. No trip
            # in a busy feed has a call behind it and a call ahead of it, which is
            # what "NJ Transit stopped retaining passed stops" looks like from
            # here. Placement would collapse next (see NJT_PLACEMENT_FLOOR).
            statuses.append(WARN)
            details.append(
                f"{entities} trips and NONE straddle now: the feed may have stopped "
                "retaining passed stops, which feeds.njt._place assumes it does"
            )
        else:
            placed = {t["trip_id"] for t in trains} & straddling
            rate = len(placed) / len(straddling)
            if rate < NJT_PLACEMENT_FLOOR:
                statuses.append(WARN)
                details.append(
                    f"only {len(placed)}/{len(straddling)} running trips placed "
                    f"({rate:.0%} < {NJT_PLACEMENT_FLOOR:.0%})"
                )
    return _njt_rt_result(statuses, details, in_service, entities)


def _njt_rt_result(
    statuses: list[str], details: list[str], in_service: bool, entities: int
) -> Result:
    """The njt-realtime summary line, shared by every return above so a healthy run
    and a noted-but-healthy run read the same way."""
    if not statuses:
        note = "in service" if in_service else "closed"
        base = f"{entities} trip updates, both feeds decodable ({note})"
        return Result("njt-realtime", PASS, base + ("; " + "; ".join(details) if details else ""))
    return Result("njt-realtime", _worst(statuses), "; ".join(details))


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


# Static feeds we have explicitly, individually acknowledged as expired-but-live:
# the upstream publisher has not republished, its last-published file is genuinely
# past its feed_end_date, yet the schedule/topology it ships is still correct and
# no code change on our side can fix the date. For such a feed a past feed_end_date
# downgrades from FAIL to WARN, so the monitor still SHOWS the condition on every
# run (it is not silenced) but stops crying wolf with a red FAIL nothing can clear.
#
# Keyed on (feed key, the EXACT expired end date) on purpose: the acknowledgment is
# pinned to the specific stale file. When the upstream finally republishes, its new
# feed_end_date will not be in this map, so it FAILs again and forces a fresh human
# look. Remove an entry once its feed republishes. Each value is a dated reason.
#
# This is a hand-maintained allowlist, not a policy knob. FOLLOWUP: if it ever grows
# past two or three entries, that is the signal the FAIL policy itself needs
# rethinking (a general staleness grace, a different cadence), not another entry.
ACKNOWLEDGED_EXPIRED_FEEDS: dict[tuple[str, str], str] = {
    ("path", "20260601"): (
        "2026-07-12: Trillium has not republished the PATH GTFS since May 2025;"
        " the path-realtime stop-id join resolved live stop ids against this"
        " topology at 100% on that date and FAILs below its resolve floor on any"
        " poll that carries trains, which remains the real guard"
    ),
}


def _feed_end_date_status(
    key: str, zf: zipfile.ZipFile, members: set[str], now: float, tz
) -> tuple[str, str] | None:
    """(status, detail) from feed_info.txt's feed_end_date, or None when the zip
    ships no feed_info/feed_end_date (the check is 'where provided'). An end date
    already past means the published schedule has expired (FAIL, or WARN when this
    feed+date pair is in ACKNOWLEDGED_EXPIRED_FEEDS); one within FEED_END_WARN_DAYS
    is about to (WARN). The date is treated as valid through the END of that day (a
    one-day grace) so the last service day is not flagged. `key` is the feed's short
    identifier (e.g. "path") used only to look up an acknowledgment.

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
        ack = ACKNOWLEDGED_EXPIRED_FEEDS.get((key, end))
        if ack is not None:
            # Explicitly acknowledged expired-but-live feed: still surfaced, but as
            # WARN not FAIL. The acknowledgment is pinned to this exact end date, so
            # a future republish that expires is a different (key, date) pair and
            # FAILs normally.
            return (WARN, f"feed_end_date {end} is in the past (acknowledged: {ack})")
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
            end_status = _feed_end_date_status("subway", zf, members, now, tz)
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
                # Per-system key (lirr/mnr) so the two railroad feeds can be
                # acknowledged independently if one ever needs it; neither is today.
                end_status = _feed_end_date_status(system.lower(), zf, members, now, tz)
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
            # The "path" key can resolve an ACKNOWLEDGED_EXPIRED_FEEDS entry that
            # downgrades an expired PATH feed_end_date to WARN. That acknowledgment is
            # only honest because check_path_realtime independently verifies, on every
            # poll that carries trains, that live PATH stop ids still resolve against
            # this static topology above the resolve floor. If that realtime check is
            # ever removed or weakened, this acknowledgment must go with it: the WARN
            # would no longer have a real guard behind it.
            end_status = _feed_end_date_status("path", zf, members, now, tz)
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
            end_status = _feed_end_date_status("ferry", zf, members, now, tz)
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


def _njt_service_status(latest: str | None, now: float, tz) -> tuple[str, str]:
    """(status, detail) for how far NJ Transit's published service still reaches.

    THE BAND THIS FEED NEEDS AND NO OTHER ONE DOES. Every other static source here
    answers "has this publication expired" from feed_info.txt's feed_end_date, and
    NJ Transit ships no feed_info.txt at all. Its 8,697-row calendar_dates table IS
    the schedule (there is no calendar.txt either), so the last ADDED service day is
    the only end date that exists.

    FAIL when the feed leads today by NOTHING, WARN inside NJT_SERVICE_WARN_DAYS,
    otherwise PASS. That is deliberately the same shape as _feed_end_date_status'
    bands so the two staleness stories read alike, and deliberately a DIFFERENT
    reader because they read different tables.

    The split with the app is the point: njt_static's validator draws the hard line
    (it refuses to promote a publication whose service has entirely expired, so the
    last good archive keeps serving), and this bands the APPROACH, so a human sees
    the edge coming a month out rather than the morning it lands.
    """
    if latest is None:
        return (FAIL, "calendar_dates.txt schedules no service days")
    today = datetime.fromtimestamp(now, tz)
    try:
        last_day = datetime(int(latest[:4]), int(latest[4:6]), int(latest[6:8]), tzinfo=tz)
    except (ValueError, IndexError, OverflowError, OSError):
        # The parser already dropped anything that is not eight digits, so this is
        # a value like 20261332: shaped right, not a date. Not this check's to
        # police, and not something to abort the whole run over.
        return (WARN, f"last service date {latest} is not a valid date")
    lead_days = (last_day.date() - today.date()).days
    if lead_days < 0:
        return (FAIL, f"service ended {latest}, {-lead_days} days ago")
    if lead_days < NJT_SERVICE_WARN_DAYS:
        return (WARN, f"service ends {latest}, in {lead_days} days")
    return (PASS, f"service through {latest}")


class _NjtToken:
    """THE RUN'S SINGLE NJ TRANSIT TOKEN, minted on first use and shared after.

    THE MONITOR'S ANSWER TO njt_auth.TokenCache, and deliberately a much smaller
    one: this process is single-threaded and lives for one run, so there is no
    lock, no age ceiling, and no re-mint. Only the property that matters here is
    kept, and by 15b there are three consumers of it (the static archive, the
    realtime trip updates, and the realtime alerts).

    ONE MINT PER RUN, AND THE ARITHMETIC IS TIGHT ENOUGH TO CARE. NJ Transit
    issues ten tokens per account per Eastern day (observed 2026-09-02; the whole
    budget is at njt_auth.DAILY_MINT_LIMIT), every attempt counts whether or not it
    yields a token, and tokens are product-scoped so we cannot read our own counter.
    This job spends 4 of the 10 at its 6-hourly cadence and production spends 2 more
    from its own twelve-hour MAX_TOKEN_AGE_S ceiling, which is 6 committed on a
    quiet day. Three checks that each minted would make this job's line 12 on its
    own and
    take NJ Transit dark IN PRODUCTION, because the monitor and the deployment share
    one account.

    A FAILED MINT IS REMEMBERED, NOT RETRIED. Every consumer after the first gets
    the same failure detail rather than a second doomed POST at the same endpoint,
    which is the shape a rate limit punishes hardest. `get` therefore returns
    (token, detail) with exactly one of them set.
    """

    def __init__(self, fetch: Fetcher, url: str, username: str, password: str) -> None:
        self._fetch = fetch
        self._url = url
        self._username = username
        self._password = password
        self._done = False
        self._token: str | None = None
        self._detail = ""

    def get(self) -> tuple[str | None, str]:
        if not self._done:
            self._done = True
            self._token, self._detail = self._mint()
        return self._token, self._detail

    def _mint(self) -> tuple[str | None, str]:
        # Deliberately not through _fetch_retrying: that helper retries once on a
        # non-200, which for getToken would mean two mints against a cap we cannot
        # measure. A single miss here is worth a FAIL a human reads.
        try:
            minted = self._fetch(
                self._url, files={"username": self._username, "password": self._password}
            )
        except Exception as exc:  # noqa: BLE001 - any transport failure is a miss
            return None, f"mint failed (transport: {_sanitize(exc)})"
        if minted.status != 200:
            # THE DAILY CAP FIRST, through the app's own sniff. The refusal is an
            # HTTP 500, so without this it reports as "mint failed (HTTP 500)",
            # which is what a dead getToken endpoint also says, and a run at 06:17
            # would send somebody looking for an NJ Transit outage when the answer
            # is that the account's ten mints are gone until Eastern midnight. The
            # detail is njt_auth's constant, never the body, so the F3 rule below
            # holds on this arm too: a constant matched against a known phrase is
            # not a quote.
            #
            # IMPORTED RATHER THAN MIRRORED, unlike PRODUCTION_HEALTH_CODES just
            # above, and the two rules do not conflict. A mirrored constant is what
            # this monitor DEMANDS of a deployment that may be running a different
            # revision. This is a fact about the UPSTREAM, which both sides read
            # from the same wire on the same day, and reimplementing it would be
            # the "reuse, never reimplement" rule broken in the file that states it.
            if njt_auth.is_mint_quota_error(minted.status, minted.content):
                return None, f"mint failed ({njt_auth.MINT_QUOTA_MESSAGE})"
            # STATUS ONLY, NEVER THE BODY (Audit 4, F3). The body used to be quoted
            # because it is the only place NJ Transit says why, and for every data
            # endpoint that reasoning holds. Not for getToken: its body may be the
            # token itself, whatever the status, and this detail is written to the
            # job summary and the Actions log. A 3xx lands here too, because the
            # fetcher's POST arm never follows a redirect (F2).
            return None, f"mint failed (HTTP {minted.status})"
        try:
            return njt_auth.extract_token(minted.content), ""
        except njt_auth.NjtAuthError as exc:
            return None, f"mint failed ({_sanitize(exc)})"


def _njt_credentials(username: str | None, password: str | None):
    """The monitor's idea of "configured", taken from the app rather than re-derived.

    THROUGH njt_auth.credentials, NEVER a bare truthiness test on the two strings.
    Reuse, never reimplement, is this file's second guiding principle, and here it
    is load-bearing rather than tidy: the app treats the placeholders .env.example
    ships as ABSENT, and a re-derived `if not username or not password` reads
    "your-njt-username" as configured. That sends a doomed mint to the live
    getToken endpoint on every run, out of the ten the account gets for the day,
    and reports the rejection as an NJ Transit outage instead of the WARN-skip the
    operator was promised. Whitespace-only values behave the same way, and GitHub
    renders an unavailable secret as an EMPTY STRING rather than leaving it unset,
    so this is also the path every fork and pull request context takes.
    """
    return njt_auth.credentials(
        {njt_auth.USERNAME_VAR: username or "", njt_auth.PASSWORD_VAR: password or ""}
    )


def check_njt_static(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    username: str | None,
    password: str | None,
    *,
    token: _NjtToken | None = None,
    token_url: str | None = None,
    url: str | None = None,
    tz=None,
) -> tuple[Result, dict | None]:
    """NJ Transit's static GTFS, behind the credentialed RailData API.

    WARN-SKIPPED WITH NO CREDENTIALS, exactly like check_bus_realtime with no bus
    key: the monitor cannot reach a credentialed feed without credentials, and that
    is a configuration choice rather than an outage. Full coverage in CI needs
    NJT_USERNAME and NJT_PASSWORD added as Actions secrets, which is a repo-owner
    decision and is flagged in the 15a handoff rather than assumed here.

    ONE MINT PER RUN, and it is not an optimization. NJ Transit issues ten tokens
    per account per Eastern day (observed 2026-09-02, njt_auth.DAILY_MINT_LIMIT),
    this job and production share the account, and 6 of the 10 are already committed
    on a quiet day. Since 15b the mint lives in _NjtToken and is SHARED with the
    realtime check rather than made here, so a run with three NJ Transit consumers
    still costs one token; run_all builds that object and hands the same one to both
    checks. The `token` argument defaults to None
    so a standalone call still works exactly as before, minting its own. The
    archive fetch that follows keeps the house one-retry behavior, because it
    REUSES the token and therefore costs nothing extra.

    Everything else is the ordinary static shape: reachable, a valid zip, required
    members present, parseable by the production parser (njt_static._parse_zip, so
    a pass here means the real code path works against today's bytes), counts above
    their floors, an identity spot check, and the service-date band above. Returns
    the parsed tables so a future NJT realtime check can join against them.
    """
    creds = _njt_credentials(username, password)
    if creds is None:
        return (
            Result(
                "njt-static",
                WARN,
                f"skipped ({njt_auth.USERNAME_VAR}/{njt_auth.PASSWORD_VAR} not set)",
            ),
            None,
        )
    username, password = creds
    tz = tz if tz is not None else feeds.NYC_TZ
    mint_url = token_url if token_url is not None else njt_auth.NJT_TOKEN_URL
    data_url = url if url is not None else njt_static.NJT_STATIC_URL

    minter = token if token is not None else _NjtToken(fetch, mint_url, username, password)
    minted_token, mint_detail = minter.get()
    if minted_token is None:
        return Result("njt-static", FAIL, mint_detail), None

    res, detail = _fetch_retrying(fetch, data_url, sleep, files={"token": minted_token})
    if res is None:
        return Result("njt-static", FAIL, f"unreachable ({detail})"), None
    # A 200 is not proof of authorization here: the probe's invalid-token response
    # IS an HTTP 500, so _fetch_retrying would have reported it as an ordinary
    # unreachable above. Reaching this line means the token was accepted.
    try:
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            members = set(zf.namelist())
        parsed = _parse_zip_bytes(njt_static._parse_zip, res.content, "gtfs_njt.zip")
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError) as exc:
        return Result("njt-static", FAIL, f"unparseable ({_sanitize(exc)})"), None

    statuses: list[str] = []
    details: list[str] = []
    _check_members(statuses, details, members, NJT_REQUIRED_MEMBERS)
    _check_members(statuses, details, members, NJT_WATCHED_MEMBERS, status=WARN)
    # THE SHAPE-CHANGE WATCH, which is the monitor's job in the way a floor is not.
    # njt_static treats this feed as FLAT: every parsed row is a boardable marker.
    # If that stops being true the loader's parser drops the non-boardable rows
    # (correct, and it keeps entrances off the map), but "should the marker set
    # become the PARENT stations, the way subway and PATH do it" is a design
    # decision a human owes an answer to. Nothing else can see it: NJT_STATIC_MIN_STOPS
    # is a lower bound, so 172 becoming 400 sails past, and the 109/112 identity
    # check still passes because a parent keeps the station's name. Both arms below
    # are WARN, not FAIL: the app keeps serving correctly throughout, and each is a
    # "come and look" rather than a "something is broken".
    if "stops.txt" in members:
        missing_columns, hierarchy_in_use = _njt_stops_shape(res.content)
        if missing_columns:
            statuses.append(WARN)
            details.append(
                f"stops.txt header no longer carries {', '.join(missing_columns)}, which "
                "njt_static._parse_stops reads on every row"
            )
        if hierarchy_in_use:
            statuses.append(WARN)
            details.append(
                f"stops.txt now USES {', '.join(hierarchy_in_use)}; the loader treats this "
                "feed as FLAT and a parent/child model is a human decision"
            )
    routes = parsed.get("routes") or {}
    stops = parsed.get("stops") or {}
    if len(routes) < NJT_STATIC_MIN_ROUTES:
        statuses.append(FAIL)
        details.append(f"only {len(routes)} routes (< {NJT_STATIC_MIN_ROUTES})")
    if len(stops) < NJT_STATIC_MIN_STOPS:
        statuses.append(FAIL)
        details.append(f"only {len(stops)} stops (< {NJT_STATIC_MIN_STOPS})")
    # IDENTITY SPOT CHECK: stop 109 is Penn Station New York and stop 112 is Newark
    # Penn Station (probed 2026-08-05). Both are checked BY NAME rather than by
    # presence, because presence alone proves nothing in a feed whose ids are small
    # integers: 112 already names four different places across our feeds, so an id
    # that survived a renumbering pointing at the wrong station is the exact drift
    # this check exists to catch.
    for stop_id, expected in (("109", "New York"), ("112", "Newark")):
        stop = stops.get(stop_id)
        if stop is None:
            statuses.append(FAIL)
            details.append(f"identity stop {stop_id} missing")
        elif expected not in (stop.get("name") or ""):
            statuses.append(FAIL)
            details.append(f"stop {stop_id} name is {stop.get('name')!r}, expected {expected}")
    service_status, service_detail = _njt_service_status(
        njt_static.latest_service_date(parsed.get("calendar_dates") or {}), now, tz
    )
    if service_status != PASS:
        statuses.append(service_status)
        details.append(service_detail)
    if not statuses:
        return (
            Result(
                "njt-static",
                PASS,
                f"{len(routes)} routes, {len(stops)} stops, 109/112, {service_detail}",
            ),
            parsed,
        )
    return Result("njt-static", _worst(statuses), "; ".join(details)), parsed


def _njt_stops_shape(body: bytes) -> tuple[list[str], list[str]]:
    """Read stops.txt out of the archive and answer the two questions that matter.

    Returns (required columns the header no longer carries, hierarchy columns the
    ROWS actually use). Both lists empty is the current publication.

    THE DIAGNOSIS THIS FUNCTION ENCODES, because the version it replaces produced a
    WARN on every run from #111 (2026-08-07) onward and three ledger entries noted
    it as pre-existing. That version read the first line of stops.txt and warned if
    the strings "location_type" or "parent_station" appeared among the column
    names. It was reading a HEADER to answer a question about DATA. GTFS makes both
    columns optional and a publisher may declare either with an empty value on
    every row, which is precisely how a flat feed spells "flat", so the header
    cannot distinguish a feed that went hierarchical from a publisher that widened
    its header. NJ Transit did the second: the archive captured on 2026-09-04
    (backend/tests/fixtures/njt_gtfs/stops.txt) carries both columns and an EMPTY
    value in both on all 164 of its rows, that capture being the trim of a 172-stop
    publication, and njt_static._parse_stops keeps every one of them, because its
    filter is `location_type not in ("", "0")`. The feed is as flat as it was on
    2026-08-05. Only the header grew.

    So the columns are accepted BY NAME (NJT_STOPS_HIERARCHY_COLUMNS) and the watch
    moved to the values: a non-empty parent_station, or a location_type that is not
    "" or "0", is the parser's own definition of a row that is not a boardable
    stop, and one of those really is the design question a human owes an answer to.

    A ROW SCAN, and it costs nothing worth naming: 164 rows today, and the loop
    stops as soon as both columns have been seen in use.
    """
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        with zf.open("stops.txt") as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))
            declared = set(reader.fieldnames or ())
            missing = [c for c in NJT_STOPS_REQUIRED_COLUMNS if c not in declared]
            in_use: set[str] = set()
            for row in reader:
                if (row.get("parent_station") or "").strip():
                    in_use.add("parent_station")
                # The parser's own test, verbatim (njt_static._parse_stops): blank
                # and "0" are boardable stops, every other location_type is a
                # parent, entrance, node or boarding area, and none of those is a
                # place a train calls at.
                if (row.get("location_type") or "").strip() not in ("", "0"):
                    in_use.add("location_type")
                if len(in_use) == len(NJT_STOPS_HIERARCHY_COLUMNS):
                    break
    return missing, [c for c in NJT_STOPS_HIERARCHY_COLUMNS if c in in_use]


def _check_members(
    statuses: list[str],
    details: list[str],
    members: set[str],
    required: tuple[str, ...],
    prefix: str = "",
    status: str = FAIL,
) -> None:
    """Fold a member-presence check into a static check's accumulators.

    A missing member defaults to FAIL (the feed is structurally truncated). `status`
    exists for the members a loader deliberately does not need: NJ Transit's
    shapes.txt is watched rather than required, so its absence is a WARN, and the
    band has to be the CALLER's choice because only the caller knows whether the app
    can serve without it. See NJT_WATCHED_MEMBERS."""
    missing = [m for m in required if m not in members]
    if missing:
        statuses.append(status)
        details.append(prefix + "missing members: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# Production deployment check
# ---------------------------------------------------------------------------


def _deployment_base(configured: str) -> str:
    """The deployment base URL, from either operator form of MONITOR_STATUS_URL.

    ONE VARIABLE, TWO PATHS SINCE F1. The monitor now probes /healthz as well as
    /api/status, and that deliberately did NOT become a second environment
    variable: two URLs naming one deployment is two things to get wrong, and the
    failure mode of getting the second one wrong is a health probe pointed at
    something that is not the deployment, reporting green forever."""
    # .strip() first: a pasted repository variable very often carries a trailing
    # newline or space, and without this the value would be non-empty (so it passes
    # the unset check) but produce a request to a URL with whitespace in it.
    trimmed = configured.strip().rstrip("/")
    # Case-insensitive for the same reason both forms are accepted at all:
    # /API/STATUS is the same intent, and rejecting it teaches nothing.
    if trimmed.lower().endswith("/api/status"):
        trimmed = trimmed[: -len("/api/status")].rstrip("/")
    return trimmed


def _resolve_health_url(configured: str) -> str:
    """The /healthz URL to probe, from the same variable /api/status comes from."""
    return _deployment_base(configured) + "/healthz"


def _resolve_njt_routes_url(configured: str) -> str:
    """The /api/njt-routes URL to probe, from that same one variable. Third path on
    it, and for the reason _deployment_base gives: one deployment, one variable."""
    return _deployment_base(configured) + "/api/njt-routes"


def _resolve_status_url(configured: str) -> str:
    """Accept either operator form of MONITOR_STATUS_URL and return the /api/status
    URL to fetch.

    The variable holds the deployment BASE url and this appends the path, but the
    natural instinct is to paste the full status URL you just looked at, which
    produced /api/status/api/status and a baffling 404 FAIL (hit 2026-07-24, and
    the setup instructions themselves said it the wrong way round twice). Both
    forms are now correct, because being strict here buys nothing: there is exactly
    one path this monitor ever wants, and guessing it right is not a test of the
    operator's attention."""
    # .strip() first: a pasted repository variable very often carries a trailing
    # newline or space, and without this the value would be non-empty (so it passes
    # the unset check) but produce a request to a URL with whitespace in it, i.e. a
    # permanent FAIL that looks like the deployment is down rather than like a typo.
    # Case-insensitive suffix test for the same reason the two forms are accepted at
    # all: /API/STATUS is the same intent, and rejecting it teaches nothing.
    trimmed = configured.strip().rstrip("/")
    if trimmed.lower().endswith("/api/status"):
        return trimmed
    return trimmed + "/api/status"


# 503 is /healthz ANSWERING, not failing to. The probe replies 503 exactly when it
# is degraded, which is the whole case this check exists for, so anything that
# treats a non-200 as a miss reports the most interesting probe as the least
# informative failure available.
_HEALTH_ANSWERED = (200, 503)


def _fetch_health(
    fetch: Fetcher, url: str, sleep: Callable[[float], None]
) -> tuple[FetchResult | None, str]:
    """Fetch /healthz with one retry, accepting 200 and 503 alike.

    A near-twin of _fetch_retrying rather than a flag on it, because every other
    caller in this file wants "200 or it did not happen" and widening that
    contract for one caller would quietly let a 503 count as a successful fetch
    for eight upstream checks that have no business tolerating one.
    """
    last = ""
    for attempt in (1, 2):
        try:
            res = fetch(url, headers=None, params=None, files=None)
        except Exception as exc:  # noqa: BLE001 - any transport failure is a miss
            last = f"transport error: {_sanitize(exc)}"
        else:
            if res.status in _HEALTH_ANSWERED:
                return res, ""
            last = f"HTTP {res.status}"
        if attempt == 1:
            sleep(RETRY_DELAY_S)
    return None, last


def _check_production_health(
    fetch: Fetcher, sleep: Callable[[float], None], health_url: str
) -> Result:
    """The /healthz classification, surfaced rather than re-derived.

    THE PROBE CLASSIFIES, THIS REPORTS. Every band lives in the app, beside the
    state it is judging, and this check contributes no threshold of its own: the
    monitor asking "is age_s over 600" about raw feed data is how it ended up able
    to see a dead deployment and not a sick one, because each new degraded state
    then needed the monitor to grow a second opinion about it.

    ONLY RECOGNIZED CODES ARE PRINTED. `degraded` is a JSON array from a URL an
    operator pasted into a repository variable, and this detail string is written
    to the run log and to $GITHUB_STEP_SUMMARY, which GitHub renders as markdown.
    Echoing an unrecognized string there would let anything that can answer that
    URL write into the job summary. Unrecognized codes are COUNTED instead, which
    still says "this deployment knows something I do not" without quoting it.
    """
    res, detail = _fetch_health(fetch, health_url, sleep)
    if res is None:
        return Result("production:healthz", FAIL, f"/healthz unreachable ({detail})")
    try:
        body = json.loads(res.content)
    except (ValueError, UnicodeDecodeError) as exc:
        return Result("production:healthz", FAIL, f"/healthz non-JSON ({_sanitize(exc)})")
    if not isinstance(body, dict):
        return Result("production:healthz", FAIL, "/healthz returned non-object JSON")

    codes = body.get("degraded")
    if codes is None:
        # SILENCE MUST BE CHOSEN, NEVER DEFAULTED, the same rule an unset
        # MONITOR_STATUS_URL is held to. A deployment that publishes no `degraded`
        # key is running code from before the classification existed, so every
        # state this check was added to watch is unwatched on it. Passing here
        # would make an unmonitored deployment look exactly like a healthy one.
        return Result(
            "production:healthz",
            FAIL,
            "/healthz publishes no 'degraded' key, so this deployment predates the health "
            "classification and its degraded states are UNWATCHED. Redeploy from a revision "
            "that includes it.",
        )
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        return Result("production:healthz", FAIL, "/healthz 'degraded' is not a list of strings")

    known = [code for code in PRODUCTION_HEALTH_CODES if code in codes]
    unknown = len([code for code in codes if code not in PRODUCTION_HEALTH_CODES])
    if known or unknown:
        parts = []
        if known:
            parts.append("degraded: " + ", ".join(known))
            # A code whose name reads like an outage and is not one gets its
            # sentence, so the job summary answers "what do I do about this"
            # without the reader having to know the vocabulary.
            parts += [_HEALTH_CODE_NOTES[code] for code in known if code in _HEALTH_CODE_NOTES]
        if unknown:
            # Counted, never quoted. See the docstring.
            parts.append(f"{unknown} unrecognized code(s), newer than this monitor")
        return Result("production:healthz", FAIL, "; ".join(parts))
    return Result("production:healthz", PASS, "no degraded states reported")


def _served_at_of(payload: object) -> float | None:
    """The payload's served_at as a number, or None if it has none worth reading."""
    if not isinstance(payload, dict):
        return None
    value = payload.get("served_at")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _check_production_served_at(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    first: object,
    status_url: str,
    *,
    gap_s: float = PRODUCTION_REPLAY_PROBE_GAP_S,
) -> Result:
    """Witness that /api/status is being BUILT, not replayed.

    A REPLAY CANNOT BE SEEN IN ONE READ. served_at is stamped per response, so a
    single probe of a cached copy looks exactly like a probe of a live one: the
    payload is well formed, every age it reports is internally consistent, and
    every other check in this file passes on it happily while the deployment
    behind it could be gone. Only comparison distinguishes them, so this probes
    twice, spaced.

    NOTHING PERSISTS BETWEEN CRON RUNS. Both reads happen inside one run, six
    hours apart being both useless (any cache TTL is shorter) and a state store
    this monitor deliberately does not have.

    The advance is measured against the gap we ASKED FOR, not against wall time,
    so the judgement is the same whether sleep is time.sleep or a test's no-op.
    A live deployment builds the second response at least gap_s after the first,
    because that is how long the monitor waited before asking for it.
    """
    before = _served_at_of(first)
    if before is None:
        return Result(
            "production:served_at", FAIL, "/api/status carries no usable served_at to compare"
        )
    sleep(gap_s)
    res, detail = _fetch_retrying(fetch, status_url, sleep)
    if res is None:
        # The first probe already proved reachability, so a second one failing
        # seconds later is not a cold deployment; it is one answering
        # inconsistently, which is worth a look on its own.
        return Result(
            "production:served_at", FAIL, f"second /api/status probe unreachable ({detail})"
        )
    try:
        second = json.loads(res.content)
    except (ValueError, UnicodeDecodeError) as exc:
        return Result("production:served_at", FAIL, f"second probe non-JSON ({_sanitize(exc)})")
    after = _served_at_of(second)
    if after is None:
        return Result("production:served_at", FAIL, "second probe carries no usable served_at")

    advance = after - before
    required = gap_s * PRODUCTION_REPLAY_MIN_ADVANCE_RATIO
    if advance < required:
        return Result(
            "production:served_at",
            FAIL,
            f"served_at advanced {advance:.1f}s across two probes {gap_s:.0f}s apart, under the "
            f"{required:.1f}s floor. The deployment is not building these responses: a cache or "
            "proxy in front of it is replaying one, or its clock stepped backwards.",
        )
    return Result("production:served_at", PASS, f"served_at advanced {advance:.1f}s between probes")


def check_production(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    now: float,
    status_url: str | None,
    *,
    skip: bool = False,
    stale_s: float = PRODUCTION_FEED_STALE_S,
    fail_s: float = PRODUCTION_FEED_FAIL_S,
) -> list[Result]:
    """The live deployment via MONITOR_STATUS_URL (either form, see
    _resolve_status_url). Returns a line each for: reachability (FAIL on
    non-200/non-JSON/non-object), each static group's state (FAIL unless ready),
    per-feed poll freshness (PASS/WARN/FAIL on the bands above), alert-system
    degradation (WARN, escalating to FAIL past the retention horizon), the
    /healthz degraded classification, and whether served_at is being built rather
    than replayed.

    THE LAST TWO ARE F1. Before them this section read one endpoint once, which
    let it tell that production was DEAD but never that it was ILL: a failed bus
    route index, upstream content frozen behind successful polls, most of the
    subway dark, and a cached response replaying a served_at were all invisible
    here while /api/status answered 200 with a well-formed body. The classification
    for the first three lives in /healthz beside the state it judges; the fourth
    cannot be classified from one read at all and is witnessed by comparison.

    SILENCE MUST BE CHOSEN, NEVER DEFAULTED. An unset MONITOR_STATUS_URL used to
    WARN-skip this entire section, which meant a completely unmonitored production
    looked the same as a healthy one: green. That is the one failure a monitor may
    never have, because it is invisible precisely when it matters. So an unset
    variable is now a FAIL, and the legitimate reasons to have no deployment to
    check (a fork, a local dispatch run) get an EXPLICIT opt-out that says so out
    loud in the summary."""
    if skip:
        return [
            Result(
                "production", WARN, "production checks explicitly skipped (MONITOR_SKIP_PRODUCTION)"
            )
        ]
    # .strip() in the emptiness test too: a variable set to whitespace is set as far
    # as Actions is concerned but names no deployment, and without this it would pass
    # here and then request the nonsense URL "/api/status".
    if not (status_url or "").strip():
        return [
            Result(
                "production",
                FAIL,
                "MONITOR_STATUS_URL is not set, so the deployment is UNMONITORED. Set it to the "
                "deployment base URL (for example https://your-app.up.railway.app; the full "
                "/api/status URL is accepted too) under Settings -> Secrets and variables -> "
                "Actions -> Variables. To run without a deployment on purpose, set "
                "MONITOR_SKIP_PRODUCTION=1.",
            )
        ]
    url = _resolve_status_url(status_url)
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

    # Every static group /api/status publishes, mapped to the states that are
    # acceptable for it. ANY group outside its set is a FAIL, not just a
    # definitively "failed" one. A static group that is not ready means a whole mode
    # is dark for riders: no stops, no route lines, and for subway/PATH/ferry no
    # vehicles at all, because their pollers gate on the static being loaded.
    # "Loading" is only benign if you know it is transient, and a probe that sees it
    # cannot know that.
    #
    # A MAP RATHER THAN A TUPLE, because NJ Transit (15a) has a legitimate fourth
    # state. "not-configured" means the deployment was given no NJT credentials,
    # which is a supported configuration that makes no network call at all; failing
    # on it would paint every deployment that does not run NJT permanently red. It
    # is listed HERE, beside the group, rather than special-cased at the comparison,
    # so adding a sixth group forces the same decision explicitly.
    #
    # THIS MAP IS THE ONLY THING THAT SEES A DARK NJT LAYER. check_njt_static probes
    # the RailData API from the runner and says nothing about the deployment; when
    # the runner has no credentials it WARN-skips, and a WARN never fails a run. So
    # a production instance whose NJT credentials were revoked, or whose publication
    # is stuck, is visible in exactly one place: this line.
    static_states = {
        "subway_static": ("ready",),
        "railroad_static": ("ready",),
        "path_static": ("ready",),
        "ferry_static": ("ready",),
        "njt_static": ("ready", "not-configured"),
    }
    # ACCEPTED COST, stated so the next person knows it was chosen: a scheduled run
    # that lands inside a redeploy's warmup window now goes red where it used to go
    # yellow. That is the deliberate trade. A cold start reaches ready in seconds
    # against a warm cache, the schedule is 6-hourly, and the alternative (tolerating
    # not-ready) is exactly the blind spot this PR exists to close. If redeploy-window
    # flapping ever shows up in practice, the fix is to re-check after a delay, not to
    # go back to calling a dark system yellow.
    not_ready = [f for f, allowed in static_states.items() if data.get(f) not in allowed]
    if not_ready:
        results.append(
            Result(
                "production:statics",
                FAIL,
                "not ready: " + ", ".join(f"{f}={data.get(f)!r}" for f in not_ready),
            )
        )
    else:
        results.append(Result("production:statics", PASS, "all static groups ready"))

    # isinstance guards below: /api/status is our own modeled endpoint, but a
    # proxy or error page in front of the deployment could return a differently
    # shaped JSON object. Coercing an unexpected type to empty keeps a malformed
    # body from raising (the same crash class as the non-object guard above); the
    # empty then surfaces as the FAIL immediately below, so a wrong-shaped payload
    # is reported as a regression rather than aborting every other check.
    feeds_raw = data.get("feeds")
    feeds_map = feeds_raw if isinstance(feeds_raw, dict) else {}
    if not feeds_map:
        # A healthy running deployment always reports its live feeds here; an empty,
        # absent, or wrong-typed map means the feed cache never populated (a broken
        # startup or a payload shape change), which the "0 feeds fresh" PASS would
        # otherwise hide. That is a DEPLOY regression, not an upstream mood, so it
        # fails at once rather than aging into a threshold.
        results.append(Result("production:feeds", FAIL, "no feeds reported by /api/status"))
    else:
        # age_s is computed server-side (served_at minus that feed's last successful
        # poll), so it is already free of any clock skew between this runner and the
        # deployment. Do not recompute it here from the monitor's own clock.
        never, aged, stale, invalid = [], [], [], []
        for name, entry in sorted(feeds_map.items()):
            age = entry.get("age_s") if isinstance(entry, dict) else None
            if age is None:
                # NULL age means fetched_at is None: this feed has NEVER had a
                # successful poll (routes/status.py only computes age_s once
                # fetched_at is set). That is NOT a deploy regression, and treating
                # it as one was a false FAIL: a deployment carrying no bus API key
                # serves buses.age_s = null FOREVER, which the app explicitly
                # supports (the README promises a missing key does not take down an
                # otherwise-working map, and /healthz stays healthy on it). Failing
                # there would paint a healthy deployment red on every 6-hourly run,
                # forever, which is precisely the cry-wolf outcome the two-edge band
                # exists to prevent. It is also boot-order dependent: the same
                # upstream outage would walk PASS -> WARN -> FAIL if it began after
                # boot, so failing on it would make the verdict depend on when the
                # process happened to start. WARN, unless NOTHING has ever polled.
                never.append(name)
            elif not isinstance(age, (int, float)) or isinstance(age, bool):
                # Present but not a number: the field changed shape. A real payload
                # regression, unlike the null above.
                invalid.append(f"{name}={age!r}")
            elif age < 0:
                # A negative age means fetched_at is AHEAD of served_at: a clock step
                # or a restored backup. Without this it would pass every band and
                # read as fresh, which is the monitor going blind rather than loud.
                invalid.append(f"{name}={age}")
            elif age > fail_s:
                aged.append(f"{name}={age}")
            elif age > stale_s:
                stale.append(f"{name}={age}")
        # Every feed unpolled is a different animal from one or two: nothing has ever
        # succeeded, so the cache never populated at all, which IS a broken startup.
        nothing_ever_polled = len(never) == len(feeds_map)
        if invalid or aged or nothing_ever_polled:
            parts = []
            if nothing_ever_polled:
                parts.append("no feed has ever polled: " + ", ".join(never))
            if invalid:
                parts.append("unusable age: " + ", ".join(invalid))
            if aged:
                parts.append(f"older than {fail_s:.0f}s: " + ", ".join(aged))
            if stale:
                parts.append(f"older than {stale_s:.0f}s: " + ", ".join(stale))
            if never and not nothing_ever_polled:
                parts.append("never polled: " + ", ".join(never))
            results.append(Result("production:feeds", FAIL, "; ".join(parts)))
        elif stale or never:
            parts = []
            if stale:
                parts.append(f"older than {stale_s:.0f}s: " + ", ".join(stale))
            if never:
                parts.append("never polled: " + ", ".join(never))
            results.append(Result("production:feeds", WARN, "; ".join(parts)))
        else:
            results.append(Result("production:feeds", PASS, f"{len(feeds_map)} feeds fresh"))

    results.append(_check_production_alerts(data))
    # F1. Both of these probe the deployment AGAIN, after every judgement above has
    # been made from the first payload, so a slow or flapping second probe can only
    # add a line rather than change one.
    results.append(_check_production_served_at(fetch, sleep, data, url))
    results.append(_check_production_health(fetch, sleep, _resolve_health_url(status_url)))
    # 15c, and LAST for the same reason the F1 pair comes after the payload lines:
    # it is another READ of the deployment, not another field of the one already
    # parsed. /api/status publishes nothing at all about route geometry, which is
    # exactly why nothing above could see a deployment serving no NJ Transit lines.
    results.append(
        _check_production_njt_routes(
            fetch, sleep, _resolve_njt_routes_url(status_url), data.get("njt_static")
        )
    )
    return results


def _check_production_alerts(data: dict) -> Result:
    """The alerts line: WARN while a down system's alerts are still being carried
    forward, FAIL once that carry-forward has stopped protecting riders.

    A degraded alert system is normally NOT urgent: the backend retains the down
    system's last-known alerts, so riders keep seeing them and the outage is covered.
    Two things end that grace, and both are a FAIL:

      1. retained_since older than PRODUCTION_ALERT_RETENTION_MAX_S. Past that
         horizon the backend has dropped the retained alerts, so the coverage the
         WARN was predicated on is gone and riders now see nothing for that system.
      2. A degraded system whose last successful decode (fresh_at) is older than the
         same horizon. This catches a system that is failing without ever having
         established a retention clock.

    Ages are computed against the payload's own served_at, NOT this runner's clock:
    fresh_at and retained_since are stamped by the deployment, so mixing in the
    monitor's clock would fold host skew into a 30 minute threshold.

    THE POLL-LEVEL AGE IS CHECKED FIRST, and the reason has CHANGED. It used to be
    that the per-system map could not be trusted at all during a total outage:
    pollers._refresh_alerts recorded the poll error and RETURNED before touching
    `health`, so every per-system field stayed frozen at its last healthy value,
    degraded_systems came back empty, and a total outage read PASS off the per-system
    data alone. C1 fixed that at the source; the app now marks every system failed on
    that path, so degraded_systems is truthful during a total outage and is in fact the
    most informative thing in the payload, because it names which feeds are down.

    The ordering stays, for two reasons that survive the fix. First, the poll age DATES
    the whole payload: per-system fields describe the last poll that decoded, so
    knowing how old that poll is comes before reading anything it produced. Second it
    is defence in depth against a shape this function cannot otherwise see, a poller
    that has stopped running entirely, where every field including health is frozen by
    definition. Do not delete this check on the grounds that health is now truthful:
    health being truthful is a property of the CURRENT app, and this monitor exists
    precisely to catch a deployment that is not behaving as the current app should."""
    alerts_obj = data.get("alerts")
    if not isinstance(alerts_obj, dict):
        # The app always emits this object; a missing or wrong-typed one is a payload
        # regression, and returning PASS here would report health we never checked.
        return Result("production:alerts", FAIL, "no alerts object reported by /api/status")
    degraded_raw = alerts_obj.get("degraded_systems")
    degraded = degraded_raw if isinstance(degraded_raw, list) else []
    systems_raw = alerts_obj.get("systems")
    systems = systems_raw if isinstance(systems_raw, dict) else {}

    # POLL-LEVEL first: it dates every per-system field below, and it is the one signal
    # that still moves if the poller has stopped entirely. Same two-edge band as the
    # feeds, for the same anti-flapping reason.
    poll_age = alerts_obj.get("age_s")
    if poll_age is None:
        # NEVER POLLED. On its own this is a warming deployment, which is why it warns.
        # But a never-polled index whose systems are ALL degraded is not warming, it is
        # a deployment that has never once reached an alert feed: a revoked key, a DNS
        # failure, rotated upstream URLs. That cannot heal on its own and must not sit
        # at WARN forever. (This escalation only fires on a payload the post-C1 app
        # produces; an older deployment reports no per-system errors here and still
        # warns, so it cannot raise a false alarm during a rollout.)
        if degraded and len(degraded) == len(systems) and systems:
            return Result(
                "production:alerts",
                FAIL,
                "alerts have never polled AND every system is degraded ("
                + ", ".join(str(s) for s in sorted(degraded))
                + "), so this deployment has never reached an alert feed",
            )
        return Result("production:alerts", WARN, "alerts have never polled")
    if isinstance(poll_age, (int, float)) and not isinstance(poll_age, bool):
        if poll_age < 0 or poll_age > PRODUCTION_FEED_FAIL_S:
            # The message names the per-system detail rather than telling the operator
            # to distrust it. Before C1 the health map really was frozen here and the
            # message said so; now it is truthful and is the fastest route to WHICH
            # feeds are down, so pointing away from it was actively unhelpful.
            detail = (
                f"alerts poll is {poll_age}s old (past {PRODUCTION_FEED_FAIL_S:.0f}s), so the "
                "alert index is not being refreshed"
            )
            if degraded:
                detail += "; degraded: " + ", ".join(str(s) for s in sorted(degraded))
            return Result("production:alerts", FAIL, detail)
    else:
        return Result("production:alerts", FAIL, f"alerts age_s is unusable ({poll_age!r})")

    served_at = data.get("served_at")
    horizon = PRODUCTION_ALERT_RETENTION_MAX_S
    expired = []
    if isinstance(served_at, (int, float)) and not isinstance(served_at, bool):
        for name, health in sorted(systems.items()):
            if not isinstance(health, dict):
                continue
            retained_since = health.get("retained_since")
            fresh_at = health.get("fresh_at")
            if isinstance(retained_since, (int, float)) and not isinstance(retained_since, bool):
                if served_at - retained_since > horizon:
                    expired.append(f"{name} retained {served_at - retained_since:.0f}s")
                    continue
            if name in degraded:
                if fresh_at is None:
                    # Degraded AND never successfully decoded: broken since process
                    # start, so there are no retained alerts to protect riders and no
                    # retention clock either. The isinstance guard below would skip
                    # exactly this case, leaving it permanently WARN and unable to
                    # ever escalate, which is the worst state to be lenient about.
                    expired.append(f"{name} has never decoded")
                elif (
                    isinstance(fresh_at, (int, float))
                    and not isinstance(fresh_at, bool)
                    and served_at - fresh_at > horizon
                ):
                    expired.append(f"{name} last decoded {served_at - fresh_at:.0f}s ago")
    elif degraded:
        # No skew-free way to age the deployment's timestamps, so the escalation
        # cannot be assessed. Say so rather than implying it was checked and passed.
        return Result(
            "production:alerts",
            WARN,
            "degraded alert systems (" + ", ".join(str(s) for s in degraded) + "); "
            "served_at missing so the retention horizon could not be checked",
        )

    if expired:
        return Result(
            "production:alerts",
            FAIL,
            f"alert coverage lost (past the {horizon:.0f}s retention horizon): "
            + ", ".join(expired),
        )
    if degraded:
        names = ", ".join(str(s) for s in degraded)
        return Result(
            "production:alerts", WARN, "degraded alert systems (alerts still retained): " + names
        )
    return Result("production:alerts", PASS, "no degraded alert systems")


# Route ids safe to print. The detail string below is written to the run log AND to
# $GITHUB_STEP_SUMMARY, which GitHub renders as MARKDOWN, and this payload comes
# from a URL an operator pasted into a repository variable. Same rule
# _check_production_health holds itself to: what matches is named, what does not is
# counted, so nothing that can answer that URL gets to write into the job summary.
# NJ Transit rail route ids are 1..12 today, so this is wide, not tight.
_SAFE_ROUTE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


def _check_production_njt_routes(
    fetch: Fetcher,
    sleep: Callable[[float], None],
    routes_url: str,
    njt_static_state: object,
) -> Result:
    """Does the deployment actually serve NJ Transit route lines?

    THE BLIND SPOT THIS CLOSES. 15c made route geometry ADDITIVE on purpose: a
    publication carrying no shapes.txt still parses, still serves stations and
    trains, and still reaches njt_static="ready", so /api/njt-routes answers 200
    with []. That is the state the app chose, and it is the right one, because the
    alternative is a whole mode dark over a member the loader does not need. But it
    means every existing production line reads green while the map has no NJ
    Transit lines on it at all: production:statics sees "ready", production:feeds
    sees fresh polls, and /healthz classifies nothing, because from the app's point
    of view nothing is wrong.

    ALWAYS WARN, NEVER FAIL, including on an unreachable or malformed response.
    Route lines are additive, so their absence must never exit the 6-hourly run
    non-zero and page someone; a deployment that is actually down is already red on
    production:status, which probes the same host. This line adds detection, not
    severity.

    NOT-CONFIGURED IS NOT AN EMPTY MAP. A deployment given no NJ Transit
    credentials serves [] here for a reason production:statics already reports and
    already accepts, so warning on it too would manufacture exactly the kind of
    standing WARN a monitor gets muted for. Read from the /api/status payload the
    caller already has rather than guessed at from the empty list, which is what
    the endpoint's own docstring says to do.

    THE COUNTS ARE IN THE SUMMARY on every arm, because "12 routes, 8214 points" is
    the number that makes the next run's "12 routes, 0 points" legible at a glance.
    """
    name = "production:njt-routes"
    if njt_static_state == "not-configured":
        return Result(name, PASS, "njt_static is not-configured, so no route geometry is expected")
    res, detail = _fetch_retrying(fetch, routes_url, sleep)
    if res is None:
        return Result(name, WARN, f"/api/njt-routes unreachable ({detail})")
    try:
        body = json.loads(res.content)
    except (ValueError, UnicodeDecodeError) as exc:
        return Result(name, WARN, f"/api/njt-routes non-JSON ({_sanitize(exc)})")
    if not isinstance(body, list):
        return Result(name, WARN, "/api/njt-routes returned non-list JSON")
    if not any(isinstance(entry, dict) for entry in body) and body:
        return Result(name, WARN, f"/api/njt-routes served {len(body)} entries, none an object")
    if not body:
        return Result(
            name,
            WARN,
            "0 routes, 0 points: the deployment is serving no NJ Transit route geometry, so "
            "the map draws no NJT lines. Expected when the publication carries no shapes.txt, "
            "which reaches ready by design; nothing else reports it.",
        )

    points = {}
    for index, entry in enumerate(body):
        route = entry.get("route") if isinstance(entry, dict) else None
        polylines = entry.get("polylines") if isinstance(entry, dict) else None
        total = (
            sum(len(line) for line in polylines if isinstance(line, list))
            if isinstance(polylines, list)
            else 0
        )
        # The index keys the dict so two entries claiming the same route id (or a
        # missing one) are still counted separately rather than collapsing.
        points[index] = (route, total)

    total_points = sum(count for _route, count in points.values())
    summary = f"{len(points)} routes, {total_points} points"
    empty = [route for route, count in points.values() if count == 0]
    if not empty:
        return Result(name, PASS, summary)
    named = sorted(str(r) for r in empty if _SAFE_ROUTE_ID_RE.match(str(r)))
    unnamed = len(empty) - len(named)
    parts = [f"{len(empty)} of {len(points)} routes carry NO points"]
    if named:
        parts.append("route " + ", ".join(named))
    if unnamed:
        # Counted, never quoted. See _SAFE_ROUTE_ID_RE.
        parts.append(f"{unnamed} with an unprintable route id")
    return Result(name, WARN, f"{summary}; " + "; ".join(parts))


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
    # ONE TOKEN FOR THE WHOLE RUN, built here and handed to both NJ Transit checks.
    # This is the only place the sharing is expressed, which is why the tests make
    # their conservation claim against run_all rather than against either check:
    # a wiring mistake here would leave both checks passing and the run quietly
    # spending two mints a time out of the ten the account gets for the Eastern day
    # (njt_auth.DAILY_MINT_LIMIT), 6 of which are already committed.
    #
    # BUILT EVEN WHEN CREDENTIALS ARE ABSENT, and it costs nothing: _NjtToken mints
    # lazily on first `get`, and with no credentials both checks WARN-skip before
    # reaching it. That is what keeps a fork or pull request context, where the
    # secrets resolve to empty strings, from touching NJ Transit at all.
    njt_creds = _njt_credentials(env.get(njt_auth.USERNAME_VAR), env.get(njt_auth.PASSWORD_VAR))
    njt_token = (
        _NjtToken(fetch, njt_auth.NJT_TOKEN_URL, njt_creds[0], njt_creds[1])
        if njt_creds is not None
        else None
    )
    njt_res, njt = check_njt_static(
        fetch,
        sleep,
        now,
        env.get(njt_auth.USERNAME_VAR),
        env.get(njt_auth.PASSWORD_VAR),
        token=njt_token,
    )
    results += [subway_res, railroad_res, path_res, ferry_res, njt_res]

    results.append(check_subway_realtime(fetch, sleep, now, (subway or {}).get("stops", {})))
    results.append(check_railroad_realtime(fetch, sleep, now, railroad))
    results.append(check_path_realtime(fetch, sleep, now, (path or {}).get("stops", {})))
    results.append(check_ferry_realtime(fetch, sleep, now, (ferry or {}).get("trips", {})))
    results.append(check_alerts_realtime(fetch, sleep, now))
    results.append(
        check_njt_realtime(
            fetch,
            sleep,
            now,
            njt,
            env.get(njt_auth.USERNAME_VAR),
            env.get(njt_auth.PASSWORD_VAR),
            token=njt_token,
        )
    )
    results.append(check_bus_realtime(fetch, sleep, now, env.get("MTA_BUS_API_KEY")))

    # MONITOR_SKIP_PRODUCTION is read as "set to anything non-empty", the usual
    # shell-variable convention, so =1, =true, or =yes all work and only an unset or
    # empty value means "do check production".
    results += check_production(
        fetch,
        sleep,
        now,
        env.get("MONITOR_STATUS_URL"),
        skip=bool(env.get("MONITOR_SKIP_PRODUCTION")),
    )
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
    # The monitor's value is that it watches the REAL upstreams from outside the
    # app, so it refuses to start if anything has redirected them (C6 made every
    # endpoint overridable; see backend/env_seams.assert_unset).
    #
    # AT THE ENTRY POINT, not inside main(). main() is driven directly by a unit
    # test, and a guard in there would make that test fail whenever a developer had
    # one of these set in their shell or .env, which the README presents as a
    # supported way to point a feed at a mirror. The real invocation is the script,
    # and the script is what CI runs, so guarding here loses no protection.
    env_seams.assert_unset("the contract monitor")
    sys.exit(main())
