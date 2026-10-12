#!/usr/bin/env python3
"""A01 (High) "Repeated NJ Transit token rejection bypasses the cooldown" : reproduction.

THE AUDIT'S CLAIM (docs/reviews/audit-2026-10-12, report of 12 October 2026 over
main at 1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2, finding A01):

    "Failure. njt_post replaces an invalid token once per call, but a successful
    mint clears the cache's failure cooldown even if the data endpoint rejects
    that new token. Repeating this across normal polling cycles mints another
    token each time. The existing fix for failed mint requests does not cover
    successful minting followed by authentication rejection."

    "Evidence. Ten calls sharing one token cache, using a fake transport that
    always mints successfully and always returns the recognized invalid-token
    data response, produced 11 mint requests and 20 data requests. Cooldown
    remained unset. Under the repository's documented ten-token daily account
    limit, quota enforcement would stop later mints only after the budget had
    already been consumed. The monitor and production also share account
    spending."

    Code evidence (where to look, never what is asserted here):
      backend/njt_auth.py:984          (TokenCache.get, the success path)
      backend/njt_auth.py:1054         (njt_post, the first token_cache.get)
      backend/tests/test_njt_auth.py:459  (test_one_remint_then_the_attempt_fails)

WHAT THE FINDING IS: the one-re-mint-per-attempt rule holds inside a call and
nothing carries across calls. TokenCache starts a cooldown only when the mint
callback RAISES, and a mint that returns a token clears any cooldown and resets
the backoff ladder. A token NJ Transit then refuses is reported by njt_post as
NjtAuthError without a word to the cache, so it leaves no cooldown behind, stays
cached as good, and the next call (the next poll) re-posts it, re-mints, and is
refused again. F05's cooldown (09-05 audit, merged as PR #101) covers a mint that
FAILS; a mint that works never reaches it.

FOUR ARMS, and what each one is for:

  ARM 1, the audit's number. Ten sequential njt_post calls sharing one
  TokenCache, against a fake that mints on every getToken POST and answers
  every data POST with the invalid-token shape. Measured: getToken POSTs, data
  POSTs, and whether a cooldown exists afterwards, read BEHAVIORALLY (an
  eleventh call still reaches getToken, and TokenCache.cooldown(), the public
  method /api/status publishes, is None). Its second half measures the
  mechanism: the rejected fresh token stays cached as good (the next call's
  first data POST carries it), and a mint whose token was refused resets the
  F05 backoff ladder exactly as a working one does.

  ARM 2, the production cadence. The two consumers that poll on their own (the
  feed poller pollers._refresh_njt through main.fetch_njt_trains, and the alert
  poller pollers._refresh_alerts through main.fetch_service_alerts) driven
  through their real paths on a hand-cranked timeline laid at the production
  cadences, read live. Measured: the mints one poll costs, the mints one cycle
  costs when both run, the instants the getToken POSTs land, and the budget
  arithmetic (F05's form) that turns those into how long the README's spare
  mints and the documented ten last. Then the end state: the same drive against
  a fake that refuses after the account's tenth mint, to see what stops the
  storm and when. To hold a clock it can crank, this arm stands a
  default-constructed TokenCache in njt_auth.TOKEN_CACHE's place, as F05 does,
  so it measures the refreshers and the door, not the process as built.

  ARM 3, the control. F05's failed-mint path at this commit: N concurrent
  callers against a FAILING mint make one getToken POST and the rest raise
  NjtMintCooldownError without a request, and the same /api/status surface
  that reads null in arm 2 reads a running cooldown here. This is what lets
  the row say the gap is specific to the mint-succeeds-then-data-rejects path
  and that PR #101's cooldown is intact.

  ARM 4, the process as it ships. A FRESH INTERPRETER (this same file, run with
  --as-shipped-process) enters the real main.lifespan, which starts the real
  poll loops pollers._poll_feeds and pollers._poll_alerts, against
  njt_auth.TOKEN_CACHE exactly as the module built it at import (never replaced,
  never re-clocked; nothing else has run in that interpreter to touch it), and
  through njt_auth's own transport: nothing in njt_auth is swapped, and the fake
  answers UNDER _httpx_post, at the httpx client it builds. The process first
  runs healthy on one token, then NJ Transit starts refusing every token it is
  shown. Measured: the getToken POSTs each scheduler cycle costs after that,
  credited to a consumer by the minted token its next data POST carries. This
  is the arm that sees a repair carried by how TOKEN_CACHE is constructed, by
  the lifespan's wiring, by a gate in the poll loops, or by the transport, none
  of which arms 1 to 3 execute.

WHY THE COUNTS, NOT THE COOLDOWN SURFACE, CARRY THE VERDICT. "Cooldown remained
unset" is read twice (TokenCache.cooldown() and /api/status njt_mint_cooldown),
but a repair can stop the storm with a breaker of its own that never reaches
that surface, and both reads would then stay null while the minting stopped.
Every arm therefore also counts what the fake upstream received. WHAT THOSE
COUNTS COVER, AND WHAT THEY DO NOT: a repair changes them if it lives anywhere
on the paths the four arms execute: njt_post and TokenCache (arm 1, on a cache
this script builds), the refreshers and the feed and alert fetchers (arm 2), and
in ARM 4 the import-time TOKEN_CACHE, njt_auth's transport, main.lifespan, the
real poll loops and everything they call. A repair that lives off those paths
is not measured here: outside the process (an operator, a supervisor, a
deployment override of an environment seam), or in a static loader (arm 4 idles
the four non-NJT warmups and hands the real NJT warmup a stub load, so the only
NJ Transit requests are the pollers'). That residual is stated, not claimed
away.

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented):

  * njt_auth.njt_post, the one door, including its single re-mint branch.
  * njt_auth.TokenCache: real instances with an injected clock pair (the
    constructor's own seams) in arms 1 to 3, and in arm 4 the module's own
    TOKEN_CACHE as import built it. njt_auth.mint reads the fake's bytes, so
    every exception type is the one production raises.
  * pollers._refresh_njt -> main.fetch_njt_trains -> njt_post, behind the real
    njt_static_status gate.
  * pollers._refresh_alerts -> main.fetch_service_alerts ->
    feeds.alerts.active_alert_feeds, the NJT alert feed taking its real
    njt_post path.
  * Arm 4: main.lifespan (the app state it builds, the tasks it starts and how
    it stops them), warmups._warm_njt_static, pollers._poll_feeds with its real
    TaskGroup cycle, pollers._poll_alerts, the NJ Transit consumers they run,
    and njt_auth._httpx_post, the real transport, multipart body and all.
  * routes.status /api/status (its njt_mint_cooldown key and the njt feed's
    last_error) and /healthz, served through the real ASGI app.
  * The cadences and the budget, read live: pollers.POLL_INTERVAL_S,
    pollers.ALERT_POLL_INTERVAL_S (checked against env_seams.DEFAULTS, so a
    developer .env override cannot skew the arithmetic), njt_auth.DAILY_MINT_LIMIT,
    njt_auth.MINT_COOLDOWN_BASE_S, and README.md's own mint table, parsed the
    way f05_njt_failed_mint_storm.py parses it.

WHAT IS INJECTED (the faults, stated plainly):

  * The NJ Transit upstream. FakeRailData answers getToken with HTTP 200 and a
    fresh, distinct {"UserToken": ...} every time (arms 1, 2 and 4), or HTTP 500
    with the suite's REAL_500 body (arm 3), or the observed daily-cap refusal
    after a set count (arm 2's end state). Data POSTs answer HTTP 500
    {"errorMessage":"Invalid token."}, the exact shape njt_auth.is_auth_error
    recognises and backend/tests/test_njt_auth.py pins as INVALID_TOKEN. Arm 2's
    warm-up poll and arm 4's healthy phase are answered with an empty, valid
    GTFS-Realtime FeedMessage.
  * Arms 1 to 3: njt_auth._httpx_post (the module's default transport, the seam
    every consumer reaches) is replaced by the fake for a drive and by a
    transport that hard-exits between drives; in arms 2 and 3, njt_auth.TOKEN_CACHE
    is replaced by a fresh instance per drive so each drive starts from a state
    it chose on a clock it cranks, which is how F05 puts one clock in front of
    every consumer at once. Arm 4 replaces neither: the fake answers the real
    transport's POSTs from under it (FakeRailData.answer, reading the form fields
    out of the multipart body).
  * A hand-cranked clock in arms 1 to 3. Every timeline is relative to t = 0 of
    the simulated rejection, and the wall clock is fixed at 2026-10-12 12:00
    EDT, so the output does not depend on the day this runs. Arm 4 runs on the
    real clocks the import-time cache was built with; it measures counts, not
    times, its drive lasts well under a second, and every hold in play (a 60s
    cooldown, any breaker a repair adds) is far longer than that.
  * The five keyless alert feeds are an httpx.MockTransport answering an empty,
    valid FeedMessage, so an alert poll is an ordinary poll in which only NJ
    Transit fails.
  * Arm 4 only, F07's precedent for driving the real loops: the poll cadences
    are scaled by CADENCE_SCALE (ratio kept); the five non-NJT feed refreshers
    named in pollers.FEED_REFRESHERS are no-ops (one of them marks each feed
    cycle); pollers._refresh_alerts is wrapped by a pass-through that marks each
    alert cycle and forwards every argument unchanged; the four non-NJT static
    warmups and the bus index build idle until shutdown; njt_static's loader
    returns F07's stub tables so the REAL NJT warmup reaches ready without a
    request; and every httpx client the interpreter constructs (async and sync,
    njt_auth's own included) is forced onto an in-process MockTransport that
    hard-fails on NJ Transit hosts, answers a POST from the fake and a GET (a
    keyless alert feed) with an empty FeedMessage.
  * DATA_DIR is a fresh temporary directory; the repository's data/ is never
    written. The app gets a new starlette State before each drive in arms 2 and
    3, so nothing one drive leaves on app.state can decide the next drive's
    count; arm 4 is a separate interpreter.

WHAT IT READS BY NAME, and why that is not a pin. Nothing private is read to
decide a check: the counts come from the fake, the cooldown from the public
TokenCache.cooldown() and from /api/status, and an outcome is classified by
isinstance against the public exception classes (NjtAuthError,
NjtMintCooldownError, NjtMintQuotaError, NjtUpstreamError), so a subclass of
NjtAuthError for the refused fresh token, or any rename of the private names the
mechanism lives in (TokenCache._start_cooldown, _serve_through_cooldown,
_live_token, _cooldown_until, _cooldown_reason, _backoff_s, njt_post's _mint,
_post, _body_or_raise), leaves it green. The names it writes are INJECTION
SEAMS: in arms 1 to 3 njt_auth._httpx_post and njt_auth.TOKEN_CACHE, the two F05
uses; in arm 4 httpx.AsyncClient and httpx.Client, the two cadence constants,
the FEED_REFRESHERS registry, pollers._refresh_alerts (wrapped, not replaced) and
njt_static.load_njt_static, which are REQUIRED (a missing one raises, and the
script goes red without leaving the machine), and the four main._warm_* warmups
and bus_static.ensure_index, which are OPTIONAL: a rename of those leaves the
real warmup running against the in-process client, where it fails its download
and makes no NJ Transit request, so the counts and the verdict do not move
(measured: renaming _warm_subway_static, _warm_ferry_static and ensure_index
throughout backend/ leaves the script green, with zero sockets).

HERMETIC, AND WHY THE CREDENTIALS START "f05-". The consumers check
njt_auth.is_configured(), so this script must look CONFIGURED, which is the case
_hermetic.contain(keep_credentials=True) / verify(expect_configured=True)
exists for. verify() accepts a fabricated pair only if the username starts
"f05-" or "f07-" (its allow-list names the two 09-05 scripts that drive a
configured deployment). Widening that list is an edit to the 09-05 harness,
which this branch does not make, so the pair here is "f05-a01-...". Wall 1 is
what keeps it safe: every NJT URL seam, and the two keyless alert seams
(ALERTS_RT_BASE, FERRY_ALERTS_URL), is a loopback address set before the first
backend import, and every fake hard-fails on any URL naming "njtransit" or
"raildata". In arms 1 to 3 the real transport is never reachable; in arm 4 it
runs, but every httpx client it can build is the in-process one. No socket is
opened and no mint is spent, in either interpreter (the 09-05 _tracer records
two interpreters, zero resolutions, zero connections).

RUN IT (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a01_njt_fresh_token_rejected.py

RECORDED DISPOSITION: VERIFIED. The script exits 0 while the code behaves the
way the audit says and the ledger records, and 1 the moment it stops. A repair
on any path the arms execute (the door, the cache, the transport, the
refreshers, the poll loops, the lifespan, or how TOKEN_CACHE is built) that
stops a refused fresh token from buying the next mint changes the counts and
turns arm 1, 2 or 4 red, while arm 3 stays green under it.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"

# ---------------------------------------------------------------------------
# The containment, installed BEFORE any backend module is imported. env_seams
# reads these once at import and load_dotenv never overrides a key already set.
# ---------------------------------------------------------------------------

FAKE_HOST = "http://127.0.0.1:9"
# The two data endpoints the consumers post to. A data POST's last path segment
# is how arm 4 tells the feed poller's requests from the alert poller's.
FEED_ENDPOINT = "getTripUpdates"
ALERT_ENDPOINT = "getAlerts"
os.environ["NJT_TOKEN_URL"] = f"{FAKE_HOST}/a01-fake/getToken"
os.environ["NJT_TU_URL"] = f"{FAKE_HOST}/a01-fake/{FEED_ENDPOINT}"
os.environ["NJT_ALERTS_URL"] = f"{FAKE_HOST}/a01-fake/{ALERT_ENDPOINT}"
os.environ["NJT_STATIC_URL"] = f"{FAKE_HOST}/a01-fake/getStatic"
# The keyless alert feeds too: arm 4's loops build their own clients, so these
# are a second wall behind the in-process client they are forced onto.
os.environ["ALERTS_RT_BASE"] = f"{FAKE_HOST}/a01-fake/alerts-rt"
os.environ["FERRY_ALERTS_URL"] = f"{FAKE_HOST}/a01-fake/ferry-alerts"
# Fabricated, and "f05-" for the allow-list reason given in the docstring.
FAKE_USERNAME = "f05-a01-fake-username"
FAKE_PASSWORD = "f05-a01-fake-password"
os.environ["NJT_USERNAME"] = FAKE_USERNAME
os.environ["NJT_PASSWORD"] = FAKE_PASSWORD
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="a01-njt-data-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR

sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain(keep_credentials=True)

sys.path.insert(0, str(BACKEND))

import asyncio  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import bus_static  # noqa: E402
import cache as cache_module  # noqa: E402
import env_seams  # noqa: E402
import httpx  # noqa: E402
import main as main_module  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import pollers  # noqa: E402
from feeds import alerts as alerts_feed  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402
from starlette.datastructures import State  # noqa: E402

_hermetic.verify(expect_configured=True)

# The cache the module built at import. Arm 4 runs in a fresh interpreter in which
# nothing has touched it; this reference only lets arm 4 SAY that, it decides no check.
TOKEN_CACHE_AS_IMPORTED = njt_auth.TOKEN_CACHE


FAILURES: list[str] = []


def check(label: str, condition: bool, measured: str = "", audit: str = "") -> None:
    """One named expectation, PASS or FAIL, with the measured value beside the
    audit's. Every check runs; the script exits on the tally."""
    mark = "PASS" if condition else "FAIL"
    tail = []
    if measured:
        tail.append(f"measured {measured}")
    if audit:
        tail.append(f"audit {audit}")
    print(f"  [{mark}] {label}{(': ' + '; '.join(tail)) if tail else ''}")
    if not condition:
        FAILURES.append(label)


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def guard(url: str) -> None:
    """The hard stop. No request in this script may name an NJ Transit host."""
    lowered = str(url).lower()
    if "njtransit" in lowered or "raildata" in lowered:
        raise SystemExit(f"A01 CONTAINMENT BREACH: a request was addressed to {url!r}")


def empty_feed() -> bytes:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_760_284_800
    return feed.SerializeToString()


# The response shapes, byte for byte the ones backend/tests/test_njt_auth.py pins.
INVALID_TOKEN = b'{"errorMessage":"Invalid token."}'
REAL_500 = b'{"errorMessage":"An unexpected error occurred."}'
QUOTA_REFUSAL = json.dumps({"errorMessage": "Daily usage limit of 10 reached."}).encode()
EMPTY_FEED = empty_feed()

# Arms 1 and 3 call njt_post directly, as the audit did, so they name their own
# URLs and credential mapping rather than reading the environment.
DIRECT_TOKEN_URL = f"{FAKE_HOST}/a01-direct/getToken"
DIRECT_DATA_URL = f"{FAKE_HOST}/a01-direct/getGTFS"
DIRECT_ENV = {njt_auth.USERNAME_VAR: "f05-a01-direct-user", njt_auth.PASSWORD_VAR: "f05-a01-pw"}


class Clock:
    """A hand-cranked monotonic clock and wall clock that move together.

    TokenCache takes both as constructor arguments, which is how a drive moves
    time by exactly the cadence production would sleep and nothing waits.
    """

    # 2026-10-12 12:00 EDT, the audit's delivery day at noon. Fixed, so the quota
    # hold printed in arm 2 is the same on every run.
    NOON_EDT = datetime(2026, 10, 12, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def wall_clock(self) -> float:
        return self.NOON_EDT + self.now

    def set(self, t: float) -> None:
        self.now = float(t)


class FakeRailData:
    """The whole NJ Transit upstream, in process, answering from memory.

    Records every getToken POST (when, and what it answered) separately from
    every data POST (when, which endpoint, and which token it carried), because
    the getToken count is what spends njt_auth.DAILY_MINT_LIMIT and the token
    a data POST carries is how a reader sees what the cache handed out. `log`
    holds both in arrival order, which is what lets arm 4 say which consumer a
    mint went to: the consumer whose data POST next carries that minted token.
    """

    def __init__(
        self,
        clock: Clock,
        *,
        mint_fails: bool = False,
        accept_data: bool = False,
        quota_after: int | None = None,
        hold_first_mint: asyncio.Event | None = None,
        latency_ticks: int = 0,
    ) -> None:
        self.clock = clock
        self.mint_fails = mint_fails
        self.accept_data = accept_data
        # Past this many getToken POSTs the answer is the observed daily-cap
        # refusal, which is what NJ Transit does once the account's ten are spent.
        self.quota_after = quota_after
        self.hold_first_mint = hold_first_mint
        # Simulated request latency, in event-loop turns. Zero answers at once.
        # The overlap drive sets it high enough that two consumers started
        # together really are in flight together, the way two network requests
        # a few hundred milliseconds long would be. Turns, not seconds, so the
        # run stays deterministic and nothing waits on a wall clock.
        self.latency_ticks = latency_ticks
        self.token_posts = 0
        self.mint_times: list[float] = []
        self.mint_answers: list[str] = []
        self.data_log: list[tuple[float, str, str]] = []
        self.log: list[dict] = []
        self._held = False

    @property
    def data_posts(self) -> int:
        return len(self.data_log)

    async def __call__(self, url: str, form: dict, timeout_s: float, **_: object):
        """The fake as njt_auth's transport seam sees it (arms 1 to 3)."""
        guard(url)
        for _ in range(self.latency_ticks):
            await asyncio.sleep(0)
        if self.hold_first_mint is not None and _is_token_url(url) and not self._held:
            self._held = True
            await self.hold_first_mint.wait()
        return self.answer(url, form)

    def answer(self, url: str, form: dict) -> tuple[int, bytes]:
        """One request's answer, synchronously. Arm 4 reaches this from UNDER the real
        njt_auth transport, through the in-process httpx client it is forced onto."""
        guard(url)
        if _is_token_url(url):
            self.token_posts += 1
            self.mint_times.append(self.clock.now)
            if self.quota_after is not None and self.token_posts > self.quota_after:
                self.mint_answers.append("quota-refusal")
                self.log.append({"kind": "token", "token": None})
                return (500, QUOTA_REFUSAL)
            if self.mint_fails:
                self.mint_answers.append("500")
                self.log.append({"kind": "token", "token": None})
                return (500, REAL_500)
            # A distinct token per mint, of the probe's ~21-character shape, so a
            # data POST's token says which mint it came from.
            token = f"a01-tok-{self.token_posts:03d}-012345678"
            self.mint_answers.append(token)
            self.log.append({"kind": "token", "token": token})
            return (200, json.dumps({"UserToken": token}).encode())
        endpoint = url.rstrip("/").rsplit("/", 1)[-1]
        carried = str(form.get("token", ""))
        self.data_log.append((self.clock.now, endpoint, carried))
        self.log.append({"kind": "data", "endpoint": endpoint, "token": carried})
        if self.accept_data:
            return (200, EMPTY_FEED)
        return (500, INVALID_TOKEN)


def _is_token_url(url: str) -> bool:
    return str(url).rstrip("/").endswith("getToken")


# Per-request latency for ARM 2's overlap drive, in event-loop turns. Measured
# when this was written: at 0 or 1 turn the feed poll finishes its whole
# mint-and-retry before the alert poll's first NJT request, and the pair still
# costs two mints; at every value tried from 2 to 2000 it costs one. 200 is a
# hundred times that edge, so a refactor that adds an await does not move it.
OVERLAP_TICKS = 200


async def _forbidden_transport(url: str, form: dict, timeout_s: float, **_: object):
    raise SystemExit(f"A01 CONTAINMENT BREACH: the real njt_auth transport was called for {url!r}")


# Arm 4's fresh interpreter runs this same file with this flag, and in it njt_auth's
# own transport is left exactly as the module defines it (the fake sits UNDER it).
AS_SHIPPED_FLAG = "--as-shipped-process"
AS_SHIPPED = sys.argv[1:] == [AS_SHIPPED_FLAG]

# The default between drives in arms 1 to 3. A drive installs its own fake and
# restores this.
if not AS_SHIPPED:
    njt_auth._httpx_post = _forbidden_transport


def install(fake: FakeRailData) -> None:
    njt_auth._httpx_post = fake


def uninstall() -> None:
    njt_auth._httpx_post = _forbidden_transport


def reset_token_cache(clock: Clock) -> njt_auth.TokenCache:
    """Arms 2 and 3 only: stand a cold, default-constructed cache on `clock` in
    front of every consumer. Arm 4 never calls this."""
    fresh = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    njt_auth.TOKEN_CACHE = fresh
    return fresh


def alerts_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        guard(str(request.url))
        return httpx.Response(200, content=EMPTY_FEED)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30)


async def serve(path: str, client_class: type = httpx.AsyncClient) -> httpx.Response:
    transport = httpx.ASGITransport(app=main_module.app)
    async with client_class(transport=transport, base_url="http://audit-a01") as client:
        return await client.get(path)


def seed_app() -> dict:
    """The app state a running process has once NJ Transit static is ready, as
    F05 seeds it: an njt feed entry, a stop table, and a fresh alerts entry
    (whose health map comes from the real active-feed set).

    A NEW State EACH TIME, not attributes overwritten on the old one, so nothing
    a drive leaves behind (including state a future repair might add that this
    script has never heard of) can reach the next drive and decide its count.
    """
    app = main_module.app
    app.state = State()
    app.state.feed_cache = {"njt": cache_module._fresh_entry()}
    app.state.njt_stops = {"109": {}}
    app.state.njt_trips = {}
    app.state.njt_arrivals = {}
    app.state.njt_feed_health = None
    app.state.njt_static_status = "ready"
    app.state.alerts_cache = cache_module._fresh_alerts_entry()
    return app.state.feed_cache["njt"]


def readme_numbers() -> dict:
    """The mint budget README.md records, parsed exactly as F05 parses it."""
    found = {}
    for i, line in enumerate((REPO / "README.md").read_text().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("| Contract monitor |"):
            found["monitor"] = (i, int(stripped.split("|")[2].strip()))
        elif stripped.startswith("| Production |"):
            found["production"] = (i, int(stripped.split("|")[2].strip()))
        elif stripped.startswith("| **Committed** |"):
            found["committed"] = (i, int(stripped.split("|")[2].strip().strip("*")))
    return found


def readme_f05_before() -> dict:
    """The 'before' column of README.md's F05 table (first minute, first hour, a
    full day), for a printed cross-reference only. Nothing is asserted on it, so
    a README rewrite cannot turn this script red."""
    rows = {
        "| first minute of unbroken failure |": "minute",
        "| first hour |": "hour",
        "| a full day, worst case |": "day",
    }
    found = {}
    for i, line in enumerate((REPO / "README.md").read_text().splitlines(), start=1):
        for prefix, key in rows.items():
            if line.strip().startswith(prefix):
                try:
                    found[key] = (i, float(line.split("|")[2].strip().strip("*")))
                except ValueError:
                    pass
    return found


def _secs(t: float | None) -> str:
    return "no window" if t is None else f"{t:g}s"


def _dur(t: float | None) -> str:
    if t is None:
        return "never"
    if t < 120:
        return f"{t:g}s"
    if t < 7200:
        return f"{t / 60:.1f}min"
    return f"{t / 3600:.1f}h"


def classify(exc: BaseException | None) -> str:
    """What an njt_post outcome WAS, by isinstance against the public exception
    classes, never by a class name: a subclass of NjtAuthError for the refused
    fresh token is still "auth-error". Order matters, because the cooldown and
    quota errors are themselves NjtAuthErrors. "auth-error" is what both a refused
    token and a failed cold mint raise; arm 1 tells them apart by the counts."""
    if exc is None:
        return "returned"
    if isinstance(exc, njt_auth.NjtMintCooldownError):
        return "held"
    if isinstance(exc, njt_auth.NjtMintQuotaError):
        return "quota"
    if isinstance(exc, njt_auth.NjtAuthError):
        return "auth-error"
    if isinstance(exc, njt_auth.NjtUpstreamError):
        return "upstream"
    return f"other ({type(exc).__name__})"


async def call_njt_post(cache: njt_auth.TokenCache, fake: FakeRailData) -> tuple[str, object]:
    """One direct njt_post: (its outcome, classified, and the exception it raised)."""
    try:
        await njt_auth.njt_post(
            DIRECT_DATA_URL,
            cache=cache,
            transport=fake,
            env=DIRECT_ENV,
            token_url=DIRECT_TOKEN_URL,
        )
    except (njt_auth.NjtAuthError, njt_auth.NjtUpstreamError) as exc:
        return classify(exc), exc
    return "returned", None


def _shown(outcome: tuple[str, object]) -> str:
    """For the reader only: the outcome and the class that carried it."""
    label, exc = outcome
    return label if exc is None else f"{label} ({type(exc).__name__})"


# ---------------------------------------------------------------------------
# Section 0: containment, and the production constants this script reads live
# ---------------------------------------------------------------------------


def section_containment() -> dict:
    rule("Section 0: containment, and the production numbers read live")
    print(f"  NJT_TOKEN_URL in this process : {njt_auth.NJT_TOKEN_URL}")
    print(f"  NJT_USERNAME in this process  : {os.environ['NJT_USERNAME']}")
    print(f"  DATA_DIR in this process      : {njt_static.DATA_DIR}")
    check(
        "every NJT endpoint and every alert feed this process knows points at the loopback "
        "discard port",
        all(
            str(url).startswith(FAKE_HOST)
            for url in (
                njt_auth.NJT_TOKEN_URL,
                njt_static.NJT_STATIC_URL,
                *alerts_feed.ALERT_FEED_URLS.values(),
            )
        ),
    )
    check(
        "the credentials in this process are the script's own fabricated pair",
        njt_auth.credentials() == (FAKE_USERNAME, FAKE_PASSWORD),
    )
    check(
        "the static cache root is a temporary directory, not the repository's data/",
        str(njt_static.DATA_DIR).startswith(tempfile.gettempdir()),
        str(njt_static.DATA_DIR),
    )

    poll_s = pollers.POLL_INTERVAL_S
    alert_s = pollers.ALERT_POLL_INTERVAL_S
    limit = njt_auth.DAILY_MINT_LIMIT
    readme = readme_numbers()
    print()
    print("  Production numbers, read live:")
    print(f"    pollers.POLL_INTERVAL_S       = {poll_s:g}s   (the feed poller)")
    print(f"    pollers.ALERT_POLL_INTERVAL_S = {alert_s:g}s   (the alert poller)")
    print(f"    njt_auth.DAILY_MINT_LIMIT     = {limit}")
    print(f"    njt_auth.MINT_COOLDOWN_BASE_S = {njt_auth.MINT_COOLDOWN_BASE_S:g}s")
    for key in ("monitor", "production", "committed"):
        if key in readme:
            print(f"    README.md L{readme[key][0]:<4} {key:<11}= {readme[key][1]}")
    check(
        "both cadences are the production defaults, not an env-seam override",
        poll_s == env_seams.DEFAULTS["POLL_INTERVAL_S"]
        and alert_s == env_seams.DEFAULTS["ALERT_POLL_INTERVAL_S"],
        f"{poll_s:g}s and {alert_s:g}s",
    )
    check(
        "the README's mint table parses and sums to the committed total it states",
        {"monitor", "production", "committed"} <= set(readme)
        and readme["monitor"][1] + readme["production"][1] == readme["committed"][1],
        f"{readme.get('monitor', (0, '?'))[1]} + {readme.get('production', (0, '?'))[1]} "
        f"= {readme.get('committed', (0, '?'))[1]}",
    )
    check(
        "njt_auth's daily limit is the documented ten-token limit the audit names",
        limit == 10,
        str(limit),
        "ten",
    )
    committed = readme.get("committed", (0, 0))[1]
    return {
        "poll_s": poll_s,
        "alert_s": alert_s,
        "limit": limit,
        "monitor": readme.get("monitor", (0, 0))[1],
        "production": readme.get("production", (0, 0))[1],
        "committed": committed,
        "spare": limit - committed,
    }


# ---------------------------------------------------------------------------
# ARM 1: the audit's ten calls, and the mechanism
# ---------------------------------------------------------------------------


async def arm1() -> dict:
    rule("ARM 1: ten sequential njt_post calls sharing one TokenCache (the audit's number)")
    clock = Clock()
    fake = FakeRailData(clock)  # mints every time; refuses every token in use
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)

    rows = []
    for _ in range(10):
        mints_before, data_before = fake.token_posts, fake.data_posts
        outcome = await call_njt_post(cache, fake)
        rows.append(
            {
                "mints": fake.token_posts - mints_before,
                "data": fake.data_posts - data_before,
                "tokens": [tok for _, _, tok in fake.data_log[data_before:]],
                "outcome": outcome,
            }
        )
    cooldown_after_ten = cache.cooldown()
    mints_ten, data_ten = fake.token_posts, fake.data_posts

    # THE ELEVENTH CALL IS THE BEHAVIORAL READ OF "cooldown unset". With a
    # cooldown running, TokenCache.get raises NjtMintCooldownError before any
    # getToken POST (arm 3 shows that); without one, this call mints again.
    mints_before = fake.token_posts
    eleventh = await call_njt_post(cache, fake)
    eleventh_mints = fake.token_posts - mints_before

    print()
    print("  call   getToken   data    first data POST    second data POST   raised")
    print("         POSTs      POSTs   carried token      carried token")
    print("  " + "-" * 76)
    for i, row in enumerate(rows, start=1):
        toks = row["tokens"] + ["", ""]
        print(
            f"  {i:>4}   {row['mints']:>8}   {row['data']:>5}   {toks[0]:<17}  "
            f"{toks[1]:<17}  {_shown(row['outcome'])}"
        )
    print("  " + "-" * 76)
    print(f"  total  {mints_ten:>8}   {data_ten:>5}")
    print()
    print(f"  TokenCache.cooldown() after the ten : {cooldown_after_ten}")
    print(
        f"  an 11th call                        : {eleventh_mints} getToken POST, "
        f"{_shown(eleventh)}"
    )

    check(
        "ARM1 ten calls made 11 getToken POSTs",
        mints_ten == 11,
        str(mints_ten),
        "11 mint requests",
    )
    check(
        "ARM1 ten calls made 20 data POSTs",
        data_ten == 20,
        str(data_ten),
        "20 data requests",
    )
    check(
        "ARM1 every call failed as a refused token does (an NjtAuthError that is neither the "
        "cooldown nor the quota error); none was held by a cooldown",
        all(r["outcome"][0] == "auth-error" for r in rows),
        ", ".join(sorted({r["outcome"][0] for r in rows})),
    )
    check(
        "ARM1 the one-re-mint rule holds WITHIN a call: the cold call 2 mints, every later call 1",
        [r["mints"] for r in rows] == [2] + [1] * 9 and all(r["data"] == 2 for r in rows),
        str([r["mints"] for r in rows]),
    )
    check(
        "ARM1 afterwards no cooldown is published (TokenCache.cooldown(), what /api/status reads)",
        cooldown_after_ten is None,
        str(cooldown_after_ten),
        "Cooldown remained unset",
    )
    check(
        "ARM1 and an 11th call still reaches getToken rather than being refused by a cooldown",
        eleventh_mints == 1 and eleventh[0] == "auth-error",
        f"{eleventh_mints} getToken POST, {eleventh[0]}",
    )

    # THE MECHANISM, HALF ONE: the fresh token NJ Transit refused stays cached as
    # good. njt_post's second rejection raises without telling the cache, so the
    # next call's FIRST data POST carries the token the previous call minted and
    # saw refused, and only then is it invalidated and replaced.
    carried = [
        len(rows[k]["tokens"]) >= 1
        and len(rows[k - 1]["tokens"]) >= 2
        and rows[k]["tokens"][0] == rows[k - 1]["tokens"][1]
        for k in range(1, len(rows))
    ]
    print()
    print("  THE MECHANISM, read off the tokens the data POSTs carried:")
    print("    call k's second data POST carries the token call k just minted, and NJ")
    print("    Transit refuses it. Call k+1's FIRST data POST carries that same token:")
    print("    the cache handed a refused token out again as good, and only that second")
    print("    refusal invalidates it and buys the next mint.")
    check(
        "ARM1 each later call's first data POST reuses the fresh token the last call saw refused",
        all(carried),
        f"{sum(carried)} of {len(carried)} calls",
    )

    # THE MECHANISM, HALF TWO: a mint whose token is then refused counts as a
    # SUCCESS to the F05 ladder. Open a failed-mint window, let it lapse, have the
    # next mint succeed and its token be refused, then fail a mint again: the new
    # window is the BASE again, because the refused token's mint reset the streak.
    clock = Clock()
    fake = FakeRailData(clock, mint_fails=True)
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    first = await call_njt_post(cache, fake)
    window_1 = cache.cooldown_remaining()
    clock.set(clock.now + (window_1 or 0.0))
    fake.mint_fails = False
    refused = await call_njt_post(cache, fake)
    after_refusal = cache.cooldown()
    fake.mint_fails = True
    second = await call_njt_post(cache, fake)
    window_2 = cache.cooldown_remaining()
    base = njt_auth.MINT_COOLDOWN_BASE_S
    print()
    print("  THE LADDER, across a refused fresh token:")
    print(f"    a failed mint opens a window of              {_secs(window_1)}  ({_shown(first)})")
    print(f"    it lapses; the next mint works, its token is refused ({_shown(refused)})")
    print(f"    cooldown published after that refusal       : {after_refusal}")
    print(f"    the next failed mint opens a window of       {_secs(window_2)}  ({_shown(second)})")
    print(f"    a streak that survived the refusal would have doubled to {2 * base:g}s")
    check(
        "ARM1 a refused fresh token leaves no cooldown even right after a failed-mint window",
        window_1 == base and refused[0] == "auth-error" and after_refusal is None,
        f"window {_secs(window_1)}, then {refused[0]}, then {after_refusal}",
    )
    check(
        "ARM1 the mint whose token was refused reset the F05 backoff ladder to its base",
        window_2 == base,
        f"{_secs(window_2)} against a base of {base:g}s",
    )
    return {
        "mints": mints_ten,
        "data": data_ten,
        "eleventh_mints": eleventh_mints,
        # The exception njt_post raised for a refused fresh token, kept so arm 2 can
        # look for what the feed poller published about it without spelling it out.
        "refusal": rows[0]["outcome"][1],
    }


# ---------------------------------------------------------------------------
# ARM 2: the two production consumers at production cadence
# ---------------------------------------------------------------------------


def production_schedule(horizon_s: float, poll_s: float, alert_s: float) -> list[tuple]:
    """Every poll in [0, horizon_s) at the production cadences, as (t, consumers).

    Where both are due at one instant they run in the same cycle; the order
    inside it (feed, then alerts) does not change any count measured here.
    """
    by_t: dict[float, list[str]] = {}
    k = 0
    while k * poll_s < horizon_s:
        by_t.setdefault(k * poll_s, []).append("feed")
        k += 1
    k = 0
    while k * alert_s < horizon_s:
        by_t.setdefault(k * alert_s, []).append("alerts")
        k += 1
    return sorted(by_t.items())


async def drive_timeline(
    consts: dict, horizon_s: float, *, quota_after: int | None = None, overlap: bool = False
) -> dict:
    """Run the real feed and alert pollers over `horizon_s` of production time.

    A RUNNING PROCESS, NOT A COLD ONE. One healthy feed poll a cadence before t = 0
    mints the token the process holds; from t = 0 NJ Transit mints every token it
    is asked for and refuses every token presented. So every count below is the
    steady cost of the finding, with no cold mint folded in.
    """
    poll_s, alert_s = consts["poll_s"], consts["alert_s"]
    clock = Clock()
    cache = reset_token_cache(clock)
    entry = seed_app()
    app = main_module.app

    warm = FakeRailData(clock, accept_data=True)
    install(warm)
    try:
        clock.set(-poll_s)
        async with alerts_client() as client:
            await pollers._refresh_njt(app, client)
    finally:
        uninstall()
    warm_ok = warm.token_posts == 1 and entry["error"] is None

    fake = FakeRailData(
        clock, quota_after=quota_after, latency_ticks=OVERLAP_TICKS if overlap else 0
    )
    install(fake)
    requests_at_zero = cache.mint_requests
    events = []
    first_cooldown_at = None
    try:
        async with alerts_client() as client:
            for t, consumers in production_schedule(horizon_s, poll_s, alert_s):
                clock.set(t)
                m0, d0 = fake.token_posts, fake.data_posts
                if overlap and len(consumers) > 1:
                    await asyncio.gather(
                        pollers._refresh_njt(app, client), pollers._refresh_alerts(app, client)
                    )
                    events.append(
                        {
                            "t": t,
                            "who": "feed+alerts",
                            "mints": fake.token_posts - m0,
                            "data": fake.data_posts - d0,
                        }
                    )
                else:
                    for who in consumers:
                        m1, d1 = fake.token_posts, fake.data_posts
                        if who == "feed":
                            await pollers._refresh_njt(app, client)
                        else:
                            await pollers._refresh_alerts(app, client)
                        events.append(
                            {
                                "t": t,
                                "who": who,
                                "mints": fake.token_posts - m1,
                                "data": fake.data_posts - d1,
                            }
                        )
                if first_cooldown_at is None and njt_auth.TOKEN_CACHE.cooldown() is not None:
                    first_cooldown_at = t
    finally:
        uninstall()

    status = (await serve("/api/status")).json()
    healthz = (await serve("/healthz")).json()
    njt_alert_health = (app.state.alerts_cache.get("health") or {}).get("njt") or {}
    return {
        "warm_ok": warm_ok,
        "events": events,
        "mint_times": list(fake.mint_times),
        "mint_answers": list(fake.mint_answers),
        "token_posts": fake.token_posts,
        "data_posts": fake.data_posts,
        "cache_mint_requests": cache.mint_requests - requests_at_zero,
        "first_cooldown_at": first_cooldown_at,
        "status_cooldown": status.get("njt_mint_cooldown"),
        "status_njt_error": (status.get("feeds", {}).get("njt") or {}).get("last_error"),
        "alert_njt_error": njt_alert_health.get("last_error"),
        "healthz_degraded": healthz.get("degraded"),
    }


def nth_mint_time(schedule_costs: list[tuple[float, int]], n: int) -> float | None:
    """The instant the n-th mint happens, given (t, mints) per event in order."""
    total = 0
    for t, mints in schedule_costs:
        total += mints
        if total >= n:
            return t
    return None


async def arm2(consts: dict, refusal: BaseException | None) -> dict:
    rule("ARM 2: the feed and alert pollers, through their real paths, at production cadence")
    poll_s, alert_s = consts["poll_s"], consts["alert_s"]
    limit, spare = consts["limit"], consts["spare"]
    horizon = 3 * alert_s  # three alert cycles: long enough to see the ten go
    run = await drive_timeline(consts, horizon)
    events = run["events"]

    print()
    print(f"  Timeline: {horizon:g}s from the first refusal, polls at the production cadences.")
    print("      t   consumer   getToken POSTs   data POSTs")
    print("  " + "-" * 50)
    for e in events:
        print(f"  {e['t']:>5g}s  {e['who']:<9}  {e['mints']:>14}   {e['data']:>10}")
    print("  " + "-" * 50)
    print(f"  total               {run['token_posts']:>14}   {run['data_posts']:>10}")
    feed_ev = [e for e in events if e["who"] == "feed"]
    alert_ev = [e for e in events if e["who"] == "alerts"]
    both_at = sorted({e["t"] for e in alert_ev} & {e["t"] for e in feed_ev})
    per_cycle = [sum(e["mints"] for e in events if e["t"] == t) for t in both_at]
    print()
    print(f"  /api/status njt_mint_cooldown        : {run['status_cooldown']}")
    print(f"  /api/status feeds.njt.last_error     : {run['status_njt_error']}")
    print(f"  alerts health njt.last_error         : {run['alert_njt_error']}")

    check(
        "ARM2 the warm-up poll minted one token and was served (a running process, not a cold one)",
        run["warm_ok"],
    )
    check(
        "ARM2 every feed poll costs exactly one getToken POST (and two data POSTs)",
        bool(feed_ev) and all(e["mints"] == 1 and e["data"] == 2 for e in feed_ev),
        f"{[e['mints'] for e in feed_ev]} over {len(feed_ev)} polls",
    )
    check(
        "ARM2 every alert poll costs exactly one getToken POST (and two NJT data POSTs)",
        bool(alert_ev) and all(e["mints"] == 1 and e["data"] == 2 for e in alert_ev),
        f"{[e['mints'] for e in alert_ev]} over {len(alert_ev)} polls",
    )
    check(
        "ARM2 a cycle in which both run costs two mints, one each (single-flight shares nothing)",
        bool(per_cycle) and all(c == 2 for c in per_cycle),
        f"{per_cycle} at t = {[f'{t:g}' for t in both_at]}",
    )
    check(
        "ARM2 the token cache agrees with what the upstream received",
        run["cache_mint_requests"] == run["token_posts"],
        f"{run['cache_mint_requests']} vs {run['token_posts']}",
    )
    check(
        "ARM2 after every one of those refusals /api/status publishes no NJ Transit cooldown",
        run["status_cooldown"] is None and run["first_cooldown_at"] is None,
        str(run["status_cooldown"]),
        "Cooldown remained unset",
    )
    # What the feed poller published, compared with what njt_post RAISED for the same
    # refusal in arm 1 (passed through production's own sanitiser), rather than with
    # any wording this script would have to spell out.
    raised = cache_module._sanitize_upstream(refusal) if refusal is not None else None
    check(
        "ARM2 /api/status does report the refusal: the njt feed's last_error carries the "
        "refused-token error njt_post raised in arm 1",
        bool(raised) and raised in str(run["status_njt_error"]),
    )

    # ---- The budget arithmetic, in F05's form, over the measured per-poll cost.
    feed_cost = feed_ev[0]["mints"] if feed_ev else 0
    alert_cost = alert_ev[0]["mints"] if alert_ev else 0
    per_min = (60 / poll_s) * feed_cost + (60 / alert_s) * alert_cost
    per_hour = per_min * 60
    per_day = per_min * 1440
    # The schedule times the measured per-call cost, F05's form, computed
    # without looking at when the drive's POSTs landed; the check below then
    # compares the two.
    costs = [
        (t, feed_cost if who == "feed" else alert_cost)
        for t, consumers in production_schedule(horizon, poll_s, alert_s)
        for who in consumers
    ]
    arith_spare = nth_mint_time(costs, spare)
    arith_all = nth_mint_time(costs, limit)
    times = run["mint_times"]
    meas_spare = times[spare - 1] if len(times) >= spare else None
    meas_all = times[limit - 1] if len(times) >= limit else None
    print()
    rule("ARM 2: the budget arithmetic (F05's form), over the measured per-poll cost")
    print()
    print("  Measured cost per call, from the drive above:")
    print(f"    one feed poll  = {feed_cost} getToken POST")
    print(f"    one alert poll = {alert_cost} getToken POST")
    print()
    print("  Rate at the production cadences:")
    print(
        f"    per minute = (60 / {poll_s:g}) x {feed_cost} + (60 / {alert_s:g}) x {alert_cost}"
        f" = {60 / poll_s:g} + {60 / alert_s:g} = {per_min:g}"
    )
    print(f"    per hour   = {per_min:g} x 60   = {per_hour:g}")
    print(f"    per day    = {per_min:g} x 1440 = {per_day:g}   (attempted; see the end state)")
    print()
    print("  The budget, from README.md and njt_auth:")
    print(
        f"    committed on a quiet day = {consts['monitor']} (contract monitor) + "
        f"{consts['production']} (production) = {consts['committed']}"
    )
    print(f"    spare = {limit} - {consts['committed']} = {spare}")
    print()
    print("  When they are gone, walking the schedule one poll at a time from t = 0:")
    order = " ".join(f"{e['who'][0].upper()}{e['t']:g}" for e in events[:limit])
    print(f"    polls in order        : {order} ...")
    print(
        f"    the {spare} spare go with mint #{spare}  : t = {_dur(arith_spare)}"
        f"   (drive measured the #{spare} getToken POST at t = {_dur(meas_spare)})"
    )
    print(
        f"    the {limit} go with mint #{limit}      : t = {_dur(arith_all)}"
        f"   (drive measured the #{limit} getToken POST at t = {_dur(meas_all)})"
    )
    f05 = readme_f05_before()
    ours = {"minute": per_min, "hour": per_hour, "day": per_day}
    print()
    print("  CROSS-REFERENCE, printed and not asserted: README.md's F05 table records")
    print("  what a FAILED mint cost before PR 101's cooldown (its 'before' column):")
    for key in ("minute", "hour", "day"):
        line_no, value = f05.get(key, (None, None))
        same = "the same" if value == ours[key] else "DIFFERENT"
        print(
            f"    per {key:<6}: README L{line_no} before = {value:g}   "
            f"this path = {ours[key]:g}   ({same})"
            if value is not None
            else f"    per {key:<6}: not found in README.md"
        )
    print("  F05 closed that rate for a mint that fails; a mint that works and buys a")
    print("  refused token still reaches getToken at the rate in the right-hand column.")
    check(
        "ARM2 the arithmetic's instants are the instants the drive's getToken POSTs landed",
        arith_spare == meas_spare and arith_all == meas_all and arith_all is not None,
        f"#{spare} at {_dur(meas_spare)}, #{limit} at {_dur(meas_all)}",
    )

    # ---- The overlap bound. Single-flight dedupes only CONCURRENT mints, so the
    # two pollers share a re-mint only when their requests are in flight at the
    # same moment. Driven with both consumers started together at every instant
    # both are due and every request taking OVERLAP_TICKS loop turns: the best
    # case production could ever get, since the two loops never synchronise.
    overlap = await drive_timeline(consts, 4 * alert_s, overlap=True)
    ov_events = overlap["events"]
    ov_pairs = [e for e in ov_events if e["who"] == "feed+alerts"]
    ov_costs = [(e["t"], e["mints"]) for e in ov_events]
    ov_spare = nth_mint_time(ov_costs, spare)
    ov_all = nth_mint_time(ov_costs, limit)
    print()
    print("  THE OVERLAP BOUND: the same drive with the two pollers started together at")
    print("  every instant both are due, and every request slow enough that both are in")
    print("  flight at once (the best case; the two poll loops never synchronise):")
    print(f"    mints when both overlap : {[e['mints'] for e in ov_pairs]} per cycle")
    print(
        f"    the {spare} spare gone at     : {_dur(ov_spare)}   the {limit} gone at: "
        f"{_dur(ov_all)}"
    )
    print(
        f"    so production sits between the two: the {spare} spare last {_dur(meas_spare)} "
        f"to {_dur(ov_spare)}, the {limit}"
    )
    print(
        f"    last {_dur(meas_all)} to {_dur(ov_all)}, depending only on how the two loops' "
        "phases drift."
    )
    check(
        "ARM2 overlapping polls do share one re-mint, and every poll instant still mints once",
        bool(ov_pairs) and all(e["mints"] == 1 for e in ov_events),
        f"{[e['mints'] for e in ov_pairs]} per overlapping cycle, "
        f"{sum(e['mints'] for e in ov_events)} over {len(ov_events)} poll instants",
    )
    check(
        "ARM2 even in that best case no cooldown is ever published",
        overlap["status_cooldown"] is None and overlap["first_cooldown_at"] is None,
        str(overlap["status_cooldown"]),
    )

    # ---- The end state: what actually stops it. The fake now refuses after the
    # account's tenth getToken POST, as NJ Transit does, and the run is long
    # enough to cross that point.
    end = await drive_timeline(consts, 4 * alert_s, quota_after=limit)
    answers = end["mint_answers"]
    refusal_idx = answers.index("quota-refusal") if "quota-refusal" in answers else None
    refusal_at = end["mint_times"][refusal_idx] if refusal_idx is not None else None
    after_refusal = [e for e in end["events"] if refusal_at is not None and e["t"] > refusal_at]
    hold = (end["status_cooldown"] or {}).get("seconds_remaining")
    print()
    print(
        f"  THE END STATE: the same drive for {4 * alert_s:g}s, NJ Transit refusing after the "
        f"{limit}th mint:"
    )
    print(
        f"    getToken POSTs            : {end['token_posts']}  "
        f"({answers.count('quota-refusal')} of them the daily-cap refusal)"
    )
    print(f"    tokens issued and refused : {len(answers) - answers.count('quota-refusal')}")
    print(f"    the refusal arrived at    : t = {_dur(refusal_at)}")
    print(f"    first cooldown published  : t = {_dur(end['first_cooldown_at'])}")
    print(
        f"    getToken POSTs after it   : {sum(e['mints'] for e in after_refusal)} over "
        f"{len(after_refusal)} polls"
    )
    print(f"    /api/status cooldown      : {_dur(hold)}, detail:")
    print(f"      {(end['status_cooldown'] or {}).get('detail', '')}")
    print(f"    /healthz degraded         : {end['healthz_degraded']}")
    print("  The only thing that stops the storm is NJ Transit's own refusal: the first")
    print("  cooldown appears after the budget is gone, never before it.")
    check(
        "ARM2 the first cooldown appears only once the account's ten are spent",
        refusal_idx == limit
        and end["first_cooldown_at"] is not None
        and end["first_cooldown_at"] == refusal_at,
        (
            "no daily-cap refusal was ever reached"
            if refusal_idx is None
            else f"refusal on getToken POST #{refusal_idx + 1} at {_dur(refusal_at)}"
        )
        + f", cooldown from {_dur(end['first_cooldown_at'])}",
        "quota enforcement would stop later mints only after the budget had already been consumed",
    )
    check(
        "ARM2 after the refusal nothing reaches getToken, held to the Eastern reset",
        bool(after_refusal)
        and sum(e["mints"] for e in after_refusal) == 0
        and (hold or 0) > njt_auth.MINT_COOLDOWN_CAP_S
        and "njt-mint-quota" in (end["healthz_degraded"] or []),
        f"{sum(e['mints'] for e in after_refusal)} POSTs, hold {_dur(hold)}",
    )
    return {
        "feed_cost": feed_cost,
        "alert_cost": alert_cost,
        "per_cycle": per_cycle[0] if per_cycle else None,
        "per_min": per_min,
        "per_hour": per_hour,
        "per_day": per_day,
        "spare_at": meas_spare,
        "all_at": meas_all,
        "ov_spare": ov_spare,
        "ov_all": ov_all,
        "refusal_at": refusal_at,
        "refusal_n": None if refusal_idx is None else refusal_idx + 1,
        "hold": hold,
    }


# ---------------------------------------------------------------------------
# ARM 3: the control. F05's failed-mint path still holds.
# ---------------------------------------------------------------------------


async def fan_out(n: int) -> dict:
    """N concurrent njt_post callers into one TokenCache while the MINT fails.

    The first getToken POST is held open until every caller is in flight, so
    the callers are provably concurrent rather than sequential retries.
    """
    clock = Clock()
    gate = asyncio.Event()
    fake = FakeRailData(clock, mint_fails=True, hold_first_mint=gate)
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    tasks = [asyncio.create_task(call_njt_post(cache, fake)) for _ in range(n)]
    for _ in range(20):
        await asyncio.sleep(0)
    in_flight = sum(1 for t in tasks if not t.done())
    gate.set()
    outcomes = [label for label, _exc in await asyncio.gather(*tasks)]
    posts_after_burst = fake.token_posts
    next_call = (await call_njt_post(cache, fake))[0]
    return {
        "n": n,
        "in_flight": in_flight,
        "posts": posts_after_burst,
        "refused": outcomes.count("held"),
        "data": fake.data_posts,
        "cooldown": cache.cooldown_remaining(),
        "next_call": next_call,
        "next_posts": fake.token_posts - posts_after_burst,
    }


async def arm3(consts: dict) -> dict:
    rule("ARM 3 (control): F05's failed-mint cooldown at this commit")
    rows = [await fan_out(n) for n in (1, 3, 12)]
    print()
    print("  Same door (njt_post), same cache class, same fake; only the getToken answer")
    print("  differs: HTTP 500 with the suite's REAL_500 body instead of a token.")
    print()
    print("    N   in flight   getToken   refused here   data    cooldown   next call")
    print("        at gate     POSTs      (no request)   POSTs   left       (POSTs, outcome)")
    print("    " + "-" * 74)
    for r in rows:
        print(
            f"   {r['n']:>2}   {r['in_flight']:>9}   {r['posts']:>8}   {r['refused']:>12}   "
            f"{r['data']:>5}   {r['cooldown']!s:>8}   {r['next_posts']}, {r['next_call']}"
        )
    base = njt_auth.MINT_COOLDOWN_BASE_S
    for r in rows:
        check(
            f"ARM3 N={r['n']}: one getToken POST, the other {r['n'] - 1} refused without a request",
            r["in_flight"] == r["n"]
            and r["posts"] == 1
            and r["refused"] == r["n"] - 1
            and r["data"] == 0,
            f"{r['posts']} POST, {r['refused']} refused",
        )
        check(
            f"ARM3 N={r['n']}: the failure left the base cooldown, and the next call is held by it",
            r["cooldown"] == base and r["next_posts"] == 0 and r["next_call"] == "held",
            f"{r['cooldown']}s, next call {r['next_posts']} POSTs, {r['next_call']}",
        )

    # The same /api/status surface arm 2 read as null, driven through the real
    # feed poller with a failing mint: it is live, and it does publish.
    clock = Clock()
    reset_token_cache(clock)
    seed_app()
    fake = FakeRailData(clock, mint_fails=True)
    install(fake)
    try:
        async with alerts_client() as client:
            await pollers._refresh_njt(main_module.app, client)
            posts_first = fake.token_posts
            clock.set(consts["poll_s"])
            await pollers._refresh_njt(main_module.app, client)
            posts_second = fake.token_posts - posts_first
    finally:
        uninstall()
    status = (await serve("/api/status")).json()
    published = status.get("njt_mint_cooldown")
    print()
    print("  The /api/status surface arm 2 read as null, after one feed poll whose mint failed:")
    print(f"    njt_mint_cooldown : {published}")
    print(f"    the next poll, {consts['poll_s']:g}s later: {posts_second} getToken POSTs")
    check(
        "ARM3 a failed mint in the real feed poller publishes njt_mint_cooldown on /api/status",
        published is not None and posts_first == 1 and posts_second == 0,
        f"{published}",
    )
    return {"rows": rows}


# ---------------------------------------------------------------------------
# ARM 4: the process as it ships, in a fresh interpreter
# ---------------------------------------------------------------------------

AS_SHIPPED_MARK = "A01-AS-SHIPPED-RESULT "
# The production cadences, scaled for the drive (F07 shortens them the same way);
# the ratio between the two loops is kept. 20s and 60s become 2ms and 6ms.
CADENCE_SCALE = 1e-4
# How many complete scheduler cycles of each loop arm 4 measures after the
# refusals begin: a minute of production polling (three feed cycles to each alert
# cycle) four times over.
FEED_WINDOWS = 12
ALERT_WINDOWS = 4
# A real-time ceiling on each wait, never reached by a healthy run (the drive takes
# tens of milliseconds); hitting it fails a check rather than hanging.
WAIT_CEILING_S = 20.0

# F07's NJ Transit stub load (f07_static_never_refreshes.py, NJT_STUB_DATA), which the
# REAL warmups._warm_njt_static turns into a ready group without any request.
NJT_STATIC_STUB = {
    "stops": {"109": {"id": "109", "name": "New York Penn Station", "lat": 40.75, "lon": -73.99}},
    "routes": {"1": {"long_name": "Atlantic City Rail Line", "short_name": "ACRL"}},
    "trips": {"T1": {"route_id": "1", "headsign": "Atlantic City", "short_name": "4600"}},
    "stop_times": {"T1": [{"stop_id": "109", "arrival": 100, "departure": 120, "seq": 1}]},
    "shapes": {"sh1": [[40.75, -73.99], [39.36, -74.44]]},
    "calendar_dates": {"S1": {"20260905"}},
}


def form_fields(request: httpx.Request) -> dict[str, str]:
    """The text fields of a POST body: multipart/form-data, which is how njt_auth's
    transport sends them (files=, one (None, value) per field), or urlencoded."""
    content_type = request.headers.get("content-type", "")
    body = request.content
    if "boundary=" not in content_type:
        return dict(parse_qsl(body.decode("utf-8", "replace")))
    boundary = content_type.split("boundary=", 1)[1].split(";")[0].strip().strip('"')
    fields: dict[str, str] = {}
    for part in body.split(b"--" + boundary.encode()):
        head, sep, value = part.partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]*)"', head) if sep else None
        if name:
            fields[name.group(1).decode()] = value.removesuffix(b"\r\n").decode()
    return fields


async def _wait_for(predicate) -> bool:
    deadline = time.monotonic() + WAIT_CEILING_S
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.001)
    return predicate()


def _owners(log: list[dict]) -> dict[int, str | None]:
    """Which consumer each minted token went to: the endpoint of the first data
    POST that carried it. Exact even if two consumers' requests interleaved,
    because every mint the fake answers is a distinct token."""
    owners: dict[int, str | None] = {}
    for i, event in enumerate(log):
        if event["kind"] != "token":
            continue
        owners[i] = None
        if event["token"] is None:
            continue
        for later in log[i + 1 :]:
            if later["kind"] == "data" and later["token"] == event["token"]:
                owners[i] = later["endpoint"]
                break
    return owners


def _windows(
    log: list[dict], owners: dict, marks: list[int], first: int, count: int, endpoint: str
):
    """Per scheduler cycle, the getToken POSTs and data POSTs that went to one
    consumer: cycle k spans log[marks[k]:marks[k + 1]], from the mark the loop left
    at the start of that cycle to the mark it left at the start of the next."""
    mints, data = [], []
    for k in range(first, first + count):
        lo, hi = marks[k], marks[k + 1]
        mints.append(
            sum(1 for i in range(lo, hi) if log[i]["kind"] == "token" and owners[i] == endpoint)
        )
        data.append(
            sum(
                1
                for i in range(lo, hi)
                if log[i]["kind"] == "data" and log[i]["endpoint"] == endpoint
            )
        )
    return mints, data


async def as_shipped_drive() -> dict:
    """Run in the fresh interpreter. Enter the real lifespan, let the real loops
    poll NJ Transit healthily on one token, then have NJ Transit refuse every token
    it is shown, and count what each scheduler cycle costs from then on."""
    clock = Clock()  # timestamps the fake's log; no cache is given this clock
    fake = FakeRailData(clock, accept_data=True)
    feed_marks: list[int] = []
    alert_marks: list[int] = []
    in_process_requests: list[str] = []
    saved: list[tuple[object, str, object]] = []

    def swap(module: object, name: str, value: object, *, required: bool = True) -> None:
        """A seam. `required=False` only where skipping it changes no count: the
        code it would have idled then runs for real against the in-process client,
        fails its download, waits out its retry rung, and makes no NJ Transit
        request. A required seam that is gone raises, which fails closed."""
        if not required and not hasattr(module, name):
            return
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

    def in_process(request: httpx.Request) -> httpx.Response:
        """Every request any httpx client in this interpreter makes lands here. A
        POST is NJ Transit (njt_auth's own transport posts every getToken and data
        request), answered by the fake from the form it carried; a GET is a keyless
        alert feed, answered with an empty, valid FeedMessage."""
        guard(str(request.url))
        in_process_requests.append(request.method)
        if request.method == "POST":
            status, body = fake.answer(str(request.url), form_fields(request))
            return httpx.Response(status, content=body)
        return httpx.Response(200, content=EMPTY_FEED)

    real_async_client = httpx.AsyncClient
    real_sync_client = httpx.Client

    class InProcessAsyncClient(real_async_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(in_process)
            super().__init__(*args, **kwargs)

    class InProcessClient(real_sync_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(in_process)
            super().__init__(*args, **kwargs)

    async def idle(*_args, **_kwargs) -> None:
        await asyncio.Event().wait()

    async def njt_static_stub(*_args, **_kwargs) -> dict:
        return copy.deepcopy(NJT_STATIC_STUB)

    real_njt_refresher = pollers._refresh_njt
    real_refresh_alerts = pollers._refresh_alerts
    marked = []
    for _name, refresher_name in pollers.FEED_REFRESHERS:
        if getattr(pollers, refresher_name) is real_njt_refresher:
            continue

        async def no_op(app, client, _marks=not marked):
            if _marks:
                feed_marks.append(len(fake.log))

        marked.append(refresher_name)
        swap(pollers, refresher_name, no_op)

    async def marked_refresh_alerts(*args, **kwargs):
        alert_marks.append(len(fake.log))
        return await real_refresh_alerts(*args, **kwargs)

    swap(httpx, "AsyncClient", InProcessAsyncClient)
    swap(httpx, "Client", InProcessClient)
    swap(pollers, "_refresh_alerts", marked_refresh_alerts)
    swap(pollers, "POLL_INTERVAL_S", pollers.POLL_INTERVAL_S * CADENCE_SCALE)
    swap(pollers, "ALERT_POLL_INTERVAL_S", pollers.ALERT_POLL_INTERVAL_S * CADENCE_SCALE)
    for warmup in (
        "_warm_subway_static",
        "_warm_railroad_static",
        "_warm_path_static",
        "_warm_ferry_static",
    ):
        swap(main_module, warmup, idle, required=False)
    swap(bus_static, "ensure_index", idle, required=False)
    swap(njt_static, "load_njt_static", njt_static_stub)
    # NOTHING IN njt_auth IS SWAPPED: not its transport, not TOKEN_CACHE. The fake is
    # reached through njt_auth's own _httpx_post, whose httpx client is the
    # in-process one above, so a repair living in the transport runs too.

    app = main_module.app
    out: dict = {"marked_by": marked[0] if marked else None}
    try:
        async with main_module.lifespan(app):
            out["came_up"] = await _wait_for(
                lambda: (
                    getattr(app.state, "njt_static_status", None) == "ready"
                    and len(feed_marks) >= 3
                    and len(alert_marks) >= 2
                    and fake.data_posts >= 2
                )
            )
            out["healthy_mints"] = fake.token_posts
            out["healthy_data"] = fake.data_posts
            # FROM HERE NJ TRANSIT REFUSES EVERY TOKEN IT IS SHOWN, and still mints
            # every one it is asked for. Cycles whose mark was left before this line
            # are not measured, so a cycle in flight across it cannot blur a count.
            fake.accept_data = False
            first_feed, first_alert = len(feed_marks), len(alert_marks)
            out["ran"] = await _wait_for(
                lambda: (
                    len(feed_marks) >= first_feed + FEED_WINDOWS + 1
                    and len(alert_marks) >= first_alert + ALERT_WINDOWS + 1
                )
            )
            status = (await serve("/api/status", real_async_client)).json()
            out["status_cooldown"] = status.get("njt_mint_cooldown")
            out["status_njt_error"] = (status.get("feeds", {}).get("njt") or {}).get("last_error")
            log = list(fake.log)
            feed_marks_now, alert_marks_now = list(feed_marks), list(alert_marks)
    finally:
        for module, name, value in reversed(saved):
            setattr(module, name, value)

    out["cache_is_as_imported"] = njt_auth.TOKEN_CACHE is TOKEN_CACHE_AS_IMPORTED
    out["njt_posts_in_process"] = in_process_requests.count("POST")
    out["alert_gets_in_process"] = in_process_requests.count("GET")
    if out.get("ran"):
        owners = _owners(log)
        out["feed_mints"], out["feed_data"] = _windows(
            log, owners, feed_marks_now, first_feed, FEED_WINDOWS, FEED_ENDPOINT
        )
        out["alert_mints"], out["alert_data"] = _windows(
            log, owners, alert_marks_now, first_alert, ALERT_WINDOWS, ALERT_ENDPOINT
        )
        out["unowned_mints"] = sum(1 for owner in owners.values() if owner is None)
    return out


def as_shipped_child() -> int:
    """The fresh interpreter's whole job: one drive, one result line."""
    result = asyncio.run(as_shipped_drive())
    print(AS_SHIPPED_MARK + json.dumps(result, default=str))
    return 0


def arm4(consts: dict) -> dict:
    rule("ARM 4: the process as it ships (real lifespan, real poll loops, TOKEN_CACHE as built)")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), AS_SHIPPED_FLAG],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),
    )
    payload = None
    containment = ""
    for line in proc.stdout.splitlines():
        if line.startswith(AS_SHIPPED_MARK):
            payload = json.loads(line[len(AS_SHIPPED_MARK) :])
        elif line.startswith("containment:"):
            containment = line
    print()
    print("  A fresh interpreter ran this file with " + AS_SHIPPED_FLAG + ".")
    print(f"    its own containment line : {containment or '(none printed)'}")
    check(
        "ARM4 the fresh interpreter ran the drive and reported",
        proc.returncode == 0 and payload is not None,
        f"exit {proc.returncode}"
        + ("" if payload is not None else f"; stderr tail: {proc.stderr[-600:]!r}"),
    )
    if payload is None:
        return {}
    scaled_feed = consts["poll_s"] * CADENCE_SCALE
    scaled_alert = consts["alert_s"] * CADENCE_SCALE
    print(
        f"    cadences, scaled by {CADENCE_SCALE:g}  : feed {scaled_feed * 1000:g}ms, "
        f"alerts {scaled_alert * 1000:g}ms (production {consts['poll_s']:g}s and "
        f"{consts['alert_s']:g}s, ratio kept)"
    )
    print(f"    feed cycles marked by    : the no-op standing in for {payload.get('marked_by')}")
    # Printed, never asserted: a repair that replaces the cache at startup is a
    # legitimate repair, and the counts below are what would show it.
    same_cache = payload.get("cache_is_as_imported")
    print(
        "    TOKEN_CACHE at the end   : "
        + ("the instance import built" if same_cache else "a DIFFERENT instance than import built")
    )
    posts = "yes" if payload.get("njt_posts_in_process") else "none"
    gets = "yes" if payload.get("alert_gets_in_process") else "none"
    print(f"    NJ Transit POSTs answered under njt_auth's own transport : {posts}")
    print(f"    keyless alert GETs answered by the in-process client     : {gets}")
    print()
    print("  Healthy phase: NJ Transit accepts the token, both loops polling.")
    print(f"    getToken POSTs : {payload.get('healthy_mints')}")
    check(
        "ARM4 the process came up and ran on one token while NJ Transit accepted it",
        bool(payload.get("came_up"))
        and payload.get("healthy_mints") == 1
        and (payload.get("healthy_data") or 0) >= 2,
        f"{payload.get('healthy_mints')} getToken POST before the refusals",
    )
    print()
    print("  Then NJ Transit refuses every token it is shown. Per scheduler cycle after that,")
    print("  each mint credited to the consumer whose data POST next carried the minted token:")
    feed_mints, alert_mints = payload.get("feed_mints"), payload.get("alert_mints")
    print(f"    feed loop  ({FEED_WINDOWS} cycles) getToken POSTs : {feed_mints}")
    print(f"                          data POSTs     : {payload.get('feed_data')}")
    print(f"    alert loop ({ALERT_WINDOWS} cycles)  getToken POSTs : {alert_mints}")
    print(f"                          data POSTs     : {payload.get('alert_data')}")
    print(f"    mints no data POST ever carried      : {payload.get('unowned_mints')}")
    print(f"    /api/status njt_mint_cooldown        : {payload.get('status_cooldown')}")
    print(f"    /api/status feeds.njt.last_error     : {payload.get('status_njt_error')}")
    check(
        "ARM4 the real lifespan and poll loops ran every cycle measured",
        bool(payload.get("ran")),
        "" if payload.get("ran") else f"timed out after {WAIT_CEILING_S:g}s",
    )
    check(
        f"ARM4 every one of {FEED_WINDOWS} feed-loop cycles after the refusals began cost exactly "
        "one getToken POST (and two data POSTs)",
        feed_mints == [1] * FEED_WINDOWS and payload.get("feed_data") == [2] * FEED_WINDOWS,
        str(feed_mints),
    )
    check(
        f"ARM4 every one of {ALERT_WINDOWS} alert-loop cycles after the refusals began cost "
        "exactly one getToken POST (and two data POSTs)",
        alert_mints == [1] * ALERT_WINDOWS and payload.get("alert_data") == [2] * ALERT_WINDOWS,
        str(alert_mints),
    )
    return {
        "feed_cycles": FEED_WINDOWS,
        "alert_cycles": ALERT_WINDOWS,
        "mints": sum(feed_mints or []) + sum(alert_mints or []),
    }


# ---------------------------------------------------------------------------


async def run() -> dict:
    consts = section_containment()
    a1 = await arm1()
    a2 = await arm2(consts, a1["refusal"])
    a3 = await arm3(consts)
    return {"consts": consts, "a1": a1, "a2": a2, "a3": a3}


def main() -> int:
    print("A01: repeated NJ Transit token rejection bypasses the cooldown")
    print(f"repository : {REPO}")
    print("upstream   : entirely fake, in process. No NJT host, no real credential, no mint spent.")
    print("clock      : arms 1 to 3 run on a hand-cranked clock, every timeline relative to")
    print("             t = 0 of the simulated rejection; arm 4 counts, it does not time")
    results = asyncio.run(run())
    results["a4"] = arm4(results["consts"])
    rule("Result")
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the recorded disposition:")
        for name in FAILURES:
            print(f"    - {name}")
        print()
        print("DISPOSITION: NOT AS RECORDED, see the failed checks above")
        return 1
    print("  every check matches the recorded disposition.")
    c, a1, a2, a4 = results["consts"], results["a1"], results["a2"], results["a4"]
    twelve = next(r for r in results["a3"]["rows"] if r["n"] == 12)
    print()
    print(
        f"DISPOSITION: VERIFIED  Ten njt_post calls sharing one TokenCache, against a fake that "
        f"mints every time and refuses every token, made {a1['mints']} getToken and "
        f"{a1['data']} data POSTs and left no cooldown (an 11th call minted again), while "
        f"F05's failed-mint cooldown still holds ({twelve['n']} concurrent callers, "
        f"{twelve['posts']} getToken POST, {twelve['refused']} refused without a request); the "
        f"process as built (real lifespan and poll loops, import-time TOKEN_CACHE) paid "
        f"{a4['mints']} getToken POSTs for {a4['feed_cycles']} feed and {a4['alert_cycles']} "
        f"alert cycles. Through the real feed and alert pollers every poll mints "
        f"{a2['feed_cost']} ({a2['per_cycle']} per cycle when both run), {a2['per_min']:g} a "
        f"minute at {c['poll_s']:g}s/{c['alert_s']:g}s, so the README's {c['spare']} spare mints "
        f"last {_dur(a2['spare_at'])} ({_dur(a2['ov_spare'])} if the two polls overlap) and the "
        f"documented {c['limit']} last {_dur(a2['all_at'])} ({_dur(a2['ov_all'])}), and the "
        f"first cooldown is NJ Transit's own refusal on getToken POST #{a2['refusal_n']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(as_shipped_child() if AS_SHIPPED else main())
