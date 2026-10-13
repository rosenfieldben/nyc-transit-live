#!/usr/bin/env python3
"""A01 (High) "Repeated NJ Transit token rejection bypasses the cooldown" : the record.

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

FIXED ON claude/a01-mint-allowance, SO THIS SCRIPT NOW CHECKS THE FIX. Every check
that moved carries the record's value in its label ("was: ..."), in the 2026-09-05
FIXED scripts' shape (f01's is the model): it exits 0 while the repaired behaviour
holds and non-zero the moment it comes back. THE BEFORE, as the 2026-10-12 record
measured it at 1e3e73d: ten njt_post calls on one cache against a fake that mints
every time and refuses every token made 11 getToken and 20 data POSTs and left no
cooldown, an eleventh call minted again, each later call's first data POST re-sent
the token the previous call had seen refused (9 of 9), and a mint whose token was
refused reset F05's ladder to its 60s base; in the process as it shipped, every feed
cycle and every alert cycle after the refusals began paid one getToken POST (12 of 12
and 4 of 4).

THE REPAIR. njt_post's one re-mint branch, where it raises for a fresh token NJ
Transit refused, now hands the refusal to the cache (TokenCache.reject_fresh): the
token is compare-and-cleared and the ladder starts from MINT_REJECTED_COOLDOWN_BASE_S
(600s) to the shared cap, so the next poll is held without a request. And the streak
ends on ACCEPTANCE, not on issue: a working mint ends the hold it is in, and only a
200 on the token it issued (TokenCache.confirm) resets the ladder. And because the
ladder alone still lets one process spend the account (its 1800s cap is 48 probes a
day, two getToken POSTs each), the process also stops itself at
njt_auth.DAILY_MINT_ALLOWANCE getToken POSTs per Eastern day and holds to the next
Eastern midnight, serving any token it still holds through that hold as through any
other.

FIVE ARMS, and what each one is for:

  ARM 1, the audit's number, inverted. Ten sequential njt_post calls sharing one
  TokenCache, against a fake that mints on every getToken POST and answers every data
  POST with the invalid-token shape. Measured: getToken POSTs, data POSTs, what each
  call raised, what an eleventh call does, and the cooldown read BEHAVIORALLY and on
  TokenCache.cooldown(), the public method /api/status publishes. Its second half is
  the ladder across a refused fresh token: a failed mint's window, then a mint whose
  token is refused, then a failed mint again.

  ARM 2, the budget. The two consumers that poll on their own (the feed poller
  pollers._refresh_njt through main.fetch_njt_trains, and the alert poller
  pollers._refresh_alerts through main.fetch_service_alerts) driven through their
  real paths on a hand-cranked clock at the production cadences, read live, from
  noon Eastern across the next midnight, against a fake that mints every token and
  refuses every one. Measured: the getToken POSTs per Eastern date, when the first
  cooldown opens and whose it is, the allowance hold on /api/status and /healthz, and
  the next date's resumption. Run twice, the second time with both pollers in flight
  together whenever both are due.

  ARM 3, the control. F05's failed-mint path at this commit: N concurrent callers
  against a FAILING mint make one getToken POST and the rest raise
  NjtMintCooldownError without a request, and the real feed poller publishes the
  window on /api/status. Unchanged by the repair, which is what lets the row say
  PR #101's cooldown is intact.

  ARM 4, the process as it ships. A FRESH INTERPRETER (this same file, run with
  --as-shipped-process) enters the real main.lifespan, which starts the real poll
  loops pollers._poll_feeds and pollers._poll_alerts, against njt_auth.TOKEN_CACHE
  exactly as the module built it at import. The process first runs healthy on one
  token, then NJ Transit starts refusing every token it is shown. Measured: the
  getToken POSTs after that, in total and per scheduler cycle, credited to a
  consumer by the minted token its next data POST carries.

  ARM 5, what the allowance hold costs. The real feed poller holds a token minted
  thirteen hours earlier, past MAX_TOKEN_AGE_S, while getToken fails from noon, so the
  day's allowance is spent on replacement attempts. Measured: whether the over-age
  token keeps the feed served through the allowance hold; then, an hour in, NJ
  Transit revokes that token while getToken recovers, and what reaches NJ Transit
  until the reset, what the feed and /healthz say, and what the first poll after
  the reset does.

  EVERY ARM POSTS THROUGH njt_auth's OWN TRANSPORT, and the fake answers at the
  socket layer beneath it (httpx.AsyncHTTPTransport.handle_async_request, where an
  httpx client hands a request to its connection pool, as a02 fakes it). Nothing in
  njt_auth is swapped and no httpx client is replaced, so the repair is measured on
  the path production takes.

WHY THE COUNTS, NOT THE COOLDOWN SURFACE, CARRY THE VERDICT. A repair taken back out
could leave a cooldown published by some other route while the minting resumed, so
every arm also counts what the fake upstream received, and the counts are what the
FIXED checks assert first.

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented):

  * njt_auth.njt_post, the one door, including its single re-mint branch.
  * njt_auth._httpx_post, the real transport, in every arm.
  * njt_auth.TokenCache: real instances with an injected clock pair (the
    constructor's own seams) in arms 1 and 3, and in arm 4 the module's own
    TOKEN_CACHE as import built it. njt_auth.mint reads the fake's bytes, so every
    exception type is the one production raises.
  * pollers._refresh_njt -> main.fetch_njt_trains -> njt_post, behind the real
    njt_static_status gate (arms 2, 3 and 5), and pollers._refresh_alerts ->
    main.fetch_service_alerts with the NJ Transit alert feed's njt_post (arm 2).
  * Arm 4: main.lifespan, warmups._warm_njt_static, pollers._poll_feeds with its
    real TaskGroup cycle, pollers._poll_alerts, and the NJ Transit consumers they
    run.
  * routes.status /api/status and /healthz, served through the real ASGI app.
  * The cadences and the budget, read live: pollers.POLL_INTERVAL_S,
    pollers.ALERT_POLL_INTERVAL_S (checked against env_seams.DEFAULTS),
    njt_auth.DAILY_MINT_LIMIT, njt_auth.DAILY_MINT_ALLOWANCE,
    njt_auth.MINT_COOLDOWN_BASE_S, njt_auth.MINT_REJECTED_COOLDOWN_BASE_S,
    njt_auth.MINT_COOLDOWN_CAP_S, njt_auth.MINT_QUOTA_RESET_MARGIN_S,
    njt_auth.MAX_TOKEN_AGE_S, and README.md's own mint table.

WHAT IS INJECTED (the faults, stated plainly):

  * The NJ Transit upstream. FakeRailData answers getToken with HTTP 200 and a
    fresh, distinct {"UserToken": ...} every time (arms 1 and 4), or HTTP 500 with
    the suite's REAL_500 body (arm 3 and the ladder half of arm 1). Data POSTs
    answer HTTP 500 {"errorMessage":"Invalid token."}, the exact shape
    njt_auth.is_auth_error recognises. Arm 4's healthy phase is answered with an
    empty, valid GTFS-Realtime FeedMessage.
  * THE SOCKET LAYER, in both interpreters, before any backend import:
    httpx.AsyncHTTPTransport.handle_async_request and its sync twin are replaced by
    one fake (SocketLayer). A URL naming "njtransit" or "raildata" hard-exits; a POST
    to the loopback fake host is NJ Transit and is answered by the drive's
    FakeRailData; a GET to the fake host is a keyless alert feed and gets an empty,
    valid FeedMessage; any other host is an unreachable network.
  * Arms 2, 3 and 5: njt_auth.TOKEN_CACHE is replaced by a fresh, default-constructed
    instance on the drive's clock, as F05 does. Arm 4 does not replace it.
  * Arm 5: the fake revokes one token (refused_tokens) and turns getToken back on at
    the same instant, the one shape in which a held token dies while a fresh one
    would work.
  * Arms 2 and 5 drop log records for the length of their drives (quiet_poll_logs):
    thousands of polls through a hold each log the same warning. Nothing else.
  * No proxy: HTTP(S)_PROXY and ALL_PROXY, both spellings, are set empty before any
    import.
  * A hand-cranked clock in arms 1, 2, 3 and 5, the wall clock fixed at 2026-10-12
    12:00 EDT, so the Eastern dates and the midnight arm 2 crosses are the same on
    every run. Arm 4 runs on the real clocks the import-time cache was built with; it
    measures counts, its drive lasts a few seconds, and the hold it meets (600s) is
    far longer than that.
  * Arm 4 only, F07's precedent for driving the real loops: the poll cadences are
    scaled by CADENCE_SCALE (ratio kept); the five non-NJT feed refreshers are no-ops
    (one of them marks each feed cycle); pollers._refresh_alerts is wrapped by a
    pass-through that marks each alert cycle; the four non-NJT static warmups and the
    bus index build idle; and njt_static's loader returns F07's stub tables so the
    REAL NJT warmup reaches ready without a request.
  * DATA_DIR is a fresh temporary directory; the repository's data/ is never written.

WHAT IT READS BY NAME, and why that is not a pin. The counts come from the fake, the
cooldown from the public TokenCache.cooldown() and from /api/status, and an outcome
is classified by isinstance against the public exception classes, so a rename of any
private name the mechanism lives in leaves it green. The names it writes are
injection seams: httpx's own two socket-layer methods in every arm; in arm 3
njt_auth.TOKEN_CACHE; in arm 4 the two cadence constants, the FEED_REFRESHERS
registry, pollers._refresh_alerts (wrapped) and njt_static.load_njt_static, which are
REQUIRED, and the four main._warm_* warmups and bus_static.ensure_index, which are
OPTIONAL (a rename leaves the real warmup failing its download into the unreachable
network, with no NJ Transit request).

HERMETIC, AND WHY THE CREDENTIALS START "f05-". The consumers check
njt_auth.is_configured(), so this script must look CONFIGURED, which is the case
_hermetic.contain(keep_credentials=True) / verify(expect_configured=True) exists
for; verify() accepts a fabricated pair only if the username starts "f05-" or
"f07-". Every NJT URL seam and the two keyless alert seams are a loopback address set
before the first backend import, and the socket layer and FakeRailData both hard-fail
on any URL naming "njtransit" or "raildata". No socket is opened and no mint is
spent, in either interpreter.

RUN IT (from the repository root):

    backend/.venv/bin/python docs/reviews/audit-2026-10-12/a01_njt_fresh_token_rejected.py

RECORDED DISPOSITION: FIXED (claude/a01-mint-allowance). The script exits 0 while the
repaired behaviour holds and 1 the moment it comes back: take reject_fresh out of
njt_post's raising branch and arm 1 goes red at 11 and 20 again; let a working mint
reset the ladder and arm 1's ladder half goes red at 60s; lift the allowance and arm 2
goes red at 11 getToken POSTs on its first date, the tenth at 90min and NJ Transit's
refusal drawn by the eleventh at 2.0h (measured with NJT_DAILY_MINT_ALLOWANCE=100000).
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
# The keyless alert feeds too, so their GETs address the fake host, where the
# socket layer answers them with an empty FeedMessage (and a second wall behind it).
os.environ["ALERTS_RT_BASE"] = f"{FAKE_HOST}/a01-fake/alerts-rt"
os.environ["FERRY_ALERTS_URL"] = f"{FAKE_HOST}/a01-fake/ferry-alerts"
# Fabricated, and "f05-" for the allow-list reason given in the docstring.
FAKE_USERNAME = "f05-a01-fake-username"
FAKE_PASSWORD = "f05-a01-fake-password"
os.environ["NJT_USERNAME"] = FAKE_USERNAME
os.environ["NJT_PASSWORD"] = FAKE_PASSWORD
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="a01-njt-data-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR
# No proxy, in either interpreter: set empty (urllib and httpx read an empty value
# as unset, and load_dotenv never overrides a key that is set). Every client
# production builds then has the same transports here as in a container with no
# proxy, wherever this runs, and none of them has a proxy route out. It also halves
# the cost of each client njt_auth builds per request (one transport, one TLS
# context, instead of two when a developer machine sets HTTPS_PROXY).
for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
    os.environ[_proxy_var] = ""
    os.environ[_proxy_var.lower()] = ""

sys.path.insert(0, str(REPO / "docs" / "reviews" / "audit-2026-09-05"))
import _hermetic  # noqa: E402

_hermetic.contain(keep_credentials=True)

sys.path.insert(0, str(BACKEND))

import asyncio  # noqa: E402
import contextlib  # noqa: E402
import logging  # noqa: E402
import time  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import httpx  # noqa: E402

# ---------------------------------------------------------------------------
# The fake socket layer (WHAT IS INJECTED), installed in BOTH interpreters
# before any backend import. Faked here, and not at njt_auth._httpx_post or at a
# client's transport= argument, so that every object production composes above
# the connection pool (njt_auth's transport function, the client it builds, any
# httpx transport a repair wraps around AsyncHTTPTransport) runs in every arm.
# ---------------------------------------------------------------------------


class SocketLayer:
    """Who answers at the socket layer (`upstream`, the current drive's
    FakeRailData, or None between drives), and what reached it. The routing
    rules are the docstring's THE SOCKET LAYER bullet."""

    upstream = None  # the FakeRailData answering NJ Transit for the current drive
    methods: list[str] = []
    unreachable: list[str] = []


def _socket_route(request: httpx.Request) -> str:
    url = str(request.url)
    guard(url)
    SocketLayer.methods.append(request.method)
    if not url.startswith(FAKE_HOST + "/"):
        SocketLayer.unreachable.append(url)
        raise httpx.ConnectError("A01: no network is reachable from this script", request=request)
    if request.method != "POST":
        return "alert-feed"
    if SocketLayer.upstream is None:
        raise SystemExit(
            f"A01 CONTAINMENT BREACH: an NJ Transit POST to {url!r} reached the socket layer "
            "with no fake upstream installed"
        )
    return "njt"


async def _fake_async_socket_layer(self, request: httpx.Request) -> httpx.Response:
    await request.aread()
    if _socket_route(request) == "alert-feed":
        return httpx.Response(200, content=EMPTY_FEED, request=request)
    status, body = await SocketLayer.upstream.respond(str(request.url), form_fields(request))
    return httpx.Response(status, content=body, request=request)


def _fake_sync_socket_layer(self, request: httpx.Request) -> httpx.Response:
    request.read()
    if _socket_route(request) == "alert-feed":
        return httpx.Response(200, content=EMPTY_FEED, request=request)
    status, body = SocketLayer.upstream.answer(str(request.url), form_fields(request))
    return httpx.Response(status, content=body, request=request)


# Every httpx client with a pooled transport, async or sync, however it is built.
httpx.AsyncHTTPTransport.handle_async_request = _fake_async_socket_layer
httpx.HTTPTransport.handle_request = _fake_sync_socket_layer

import bus_static  # noqa: E402
import cache as cache_module  # noqa: E402
import env_seams  # noqa: E402
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
        # Tokens NJ Transit has revoked: refused even while accept_data is set, which
        # is how arm 5 has one held token die while fresh ones would work.
        self.refused_tokens: set[str] = set()
        self.token_posts = 0
        self.mint_times: list[float] = []
        self.mint_answers: list[str] = []
        self.data_log: list[tuple[float, str, str]] = []
        self.log: list[dict] = []
        self._held = False

    @property
    def data_posts(self) -> int:
        return len(self.data_log)

    async def respond(self, url: str, form: dict) -> tuple[int, bytes]:
        """One NJ Transit request as the socket layer hands it over, from under
        njt_auth's own transport in every arm: the simulated latency, the held
        first mint, then the answer."""
        guard(url)
        for _ in range(self.latency_ticks):
            await asyncio.sleep(0)
        if self.hold_first_mint is not None and _is_token_url(url) and not self._held:
            self._held = True
            await self.hold_first_mint.wait()
        return self.answer(url, form)

    def answer(self, url: str, form: dict) -> tuple[int, bytes]:
        """One request's answer, synchronously (the sync socket layer calls this
        directly; nothing in the NJ Transit path is sync today)."""
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
        if self.accept_data and carried not in self.refused_tokens:
            return (200, EMPTY_FEED)
        return (500, INVALID_TOKEN)


def _is_token_url(url: str) -> bool:
    return str(url).rstrip("/").endswith("getToken")


# Per-request latency for ARM 2's overlap drive, in event-loop turns. Measured
# when the record was written: at 0 or 1 turn the feed poll finishes its whole
# mint-and-retry before the alert poll's first NJT request, so the two are never
# in flight together; at every value tried from 2 to 2000 they are. 200 is a
# hundred times that edge, so a refactor that adds an await does not move it.
OVERLAP_TICKS = 200


AS_SHIPPED_FLAG = "--as-shipped-process"
AS_SHIPPED = sys.argv[1:] == [AS_SHIPPED_FLAG]


def install(fake: FakeRailData) -> None:
    """Make `fake` the NJ Transit the socket layer answers from. njt_auth itself is
    never touched: its own transport runs above the fake in every arm."""
    SocketLayer.upstream = fake


def uninstall() -> None:
    """Between drives no upstream is installed, and an NJ Transit POST hard-exits."""
    SocketLayer.upstream = None


def reset_token_cache(clock: Clock) -> njt_auth.TokenCache:
    """Arms 2, 3 and 5: stand a cold, default-constructed cache on `clock` in front
    of every consumer, as F05 does. Default-constructed, so the allowance it holds to
    is the one the process-wide cache takes. Arm 4 never calls this."""
    fresh = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    njt_auth.TOKEN_CACHE = fresh
    return fresh


def alerts_client() -> httpx.AsyncClient:
    """The shared client the pollers are handed, built as pollers builds its own.
    Its keyless alert GETs land at the socket layer like every other request."""
    return httpx.AsyncClient(timeout=30)


@contextlib.contextmanager
def quiet_poll_logs():
    """Arms 2 and 5 drive thousands of polls through a hold, and every one logs the
    same warning. Their counts and surfaces are what is checked, so the log lines are
    dropped for the drive's length and nothing else is touched."""
    logging.disable(logging.WARNING)
    try:
        yield
    finally:
        logging.disable(logging.NOTSET)


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


async def call_njt_post(cache: njt_auth.TokenCache) -> tuple[str, object]:
    """One direct njt_post through njt_auth's own transport, answered by whichever
    fake is installed at the socket layer: (its outcome, classified, and the
    exception it raised)."""
    try:
        await njt_auth.njt_post(
            DIRECT_DATA_URL,
            cache=cache,
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
# ARM 1: the audit's ten calls, and the ladder
# ---------------------------------------------------------------------------


async def arm1() -> dict:
    rule("ARM 1: ten sequential njt_post calls sharing one TokenCache (the audit's number)")
    clock = Clock()
    fake = FakeRailData(clock)  # mints every time; refuses every token in use
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)

    rows = []
    install(fake)
    try:
        for _ in range(10):
            mints_before, data_before = fake.token_posts, fake.data_posts
            outcome = await call_njt_post(cache)
            rows.append(
                {
                    "mints": fake.token_posts - mints_before,
                    "data": fake.data_posts - data_before,
                    "tokens": [tok for _, _, tok in fake.data_log[data_before:]],
                    "outcome": outcome,
                }
            )
        cooldown_after_ten = cache.cooldown()
        held_token = cache.peek()
        mints_ten, data_ten = fake.token_posts, fake.data_posts

        # THE ELEVENTH CALL IS THE BEHAVIORAL READ OF THE HOLD: with it running,
        # TokenCache.get refuses before any getToken POST.
        mints_before = fake.token_posts
        eleventh = await call_njt_post(cache)
        eleventh_mints = fake.token_posts - mints_before
    finally:
        uninstall()

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
    print(f"  token held after the ten            : {held_token}")
    print(
        f"  an 11th call                        : {eleventh_mints} getToken POST, "
        f"{_shown(eleventh)}"
    )

    base = njt_auth.MINT_REJECTED_COOLDOWN_BASE_S
    check(
        "ARM1 ten calls made 2 getToken POSTs, the cold mint and the one re-mint (was: 11)",
        mints_ten == 2,
        str(mints_ten),
        "11 mint requests",
    )
    check(
        "ARM1 ten calls made 2 data POSTs (was: 20)",
        data_ten == 2,
        str(data_ten),
        "20 data requests",
    )
    check(
        "ARM1 the first call carries NJ Transit's refusal and every later call is held without "
        "a request (was: every call a refused token, none held)",
        rows[0]["outcome"][0] == "auth-error"
        and [r["mints"] for r in rows] == [2] + [0] * 9
        and all(r["outcome"][0] == "held" for r in rows[1:]),
        ", ".join(f"{r['mints']}:{r['outcome'][0]}" for r in rows),
    )
    check(
        f"ARM1 afterwards a cooldown of the refused-token base ({base:g}s) is published on "
        "TokenCache.cooldown(), naming the refusal (was: unset)",
        cooldown_after_ten is not None
        and cooldown_after_ten[0] == base
        and "rejected a freshly minted token" in cooldown_after_ten[1],
        str(cooldown_after_ten),
        "Cooldown remained unset",
    )
    check(
        "ARM1 the refused token is dropped, so nothing can re-post it (was: each later call's "
        "first data POST re-sent it, 9 of 9)",
        held_token is None,
        str(held_token),
    )
    check(
        "ARM1 an 11th call is held before any getToken POST (was: it minted again)",
        eleventh_mints == 0 and eleventh[0] == "held",
        f"{eleventh_mints} getToken POST, {eleventh[0]}",
    )

    # THE LADDER ACROSS A REFUSED FRESH TOKEN. A failed mint opens F05's window; it
    # lapses; the next mint works and its token is refused, which opens a hold of
    # its own; it lapses; a mint fails again. The streak survives the mint that
    # issued the refused token, because only a 200 on that token ends it.
    clock = Clock()
    fake = FakeRailData(clock, mint_fails=True)
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    install(fake)
    try:
        first = await call_njt_post(cache)
        window_1 = cache.cooldown_remaining()
        clock.set(clock.now + (window_1 or 0.0))
        fake.mint_fails = False
        refused = await call_njt_post(cache)
        window_2 = cache.cooldown_remaining()
        clock.set(clock.now + (window_2 or 0.0))
        fake.mint_fails = True
        third = await call_njt_post(cache)
        window_3 = cache.cooldown_remaining()
    finally:
        uninstall()
    f05_base = njt_auth.MINT_COOLDOWN_BASE_S
    print()
    print("  THE LADDER, across a refused fresh token:")
    print(f"    a failed mint opens a window of        {_secs(window_1)}  ({_shown(first)})")
    print(f"    a mint works, its token is refused:    {_secs(window_2)}  ({_shown(refused)})")
    print(f"    a mint fails again:                    {_secs(window_3)}  ({_shown(third)})")
    check(
        "ARM1 a fresh token refused right after a failed-mint window opens a hold from its own "
        f"base, max({base:g}s, twice the streak) (was: no hold at all)",
        window_1 == f05_base and refused[0] == "auth-error" and window_2 == max(base, 2 * f05_base),
        f"{_secs(window_1)}, then {refused[0]} and {_secs(window_2)}",
    )
    check(
        "ARM1 the mint whose token was refused did not end the streak: the next failed mint "
        f"doubles it (was: reset to the {f05_base:g}s base)",
        window_3 == min(2 * window_2, njt_auth.MINT_COOLDOWN_CAP_S) if window_2 else False,
        f"{_secs(window_3)}",
    )
    return {
        "mints": mints_ten,
        "data": data_ten,
        "eleventh_mints": eleventh_mints,
        "hold": cooldown_after_ten[0] if cooldown_after_ten else None,
        "ladder": (window_1, window_2, window_3),
    }


# ---------------------------------------------------------------------------
# ARM 2: the two production pollers through a sustained refusal, day over day
# ---------------------------------------------------------------------------


def production_schedule(horizon_s: float, poll_s: float, alert_s: float) -> list[tuple]:
    """Every poll in [0, horizon_s) at the production cadences, as (t, consumers).

    Where both are due at one instant they run in the same cycle, feed first.
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


def eastern_date(clock: Clock, t: float) -> str:
    """The America/New_York date of instant `t` on `clock`'s timeline."""
    return (
        datetime.fromtimestamp(clock.NOON_EDT + t, ZoneInfo("America/New_York")).date().isoformat()
    )


def per_date(clock: Clock, times: list[float]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for t in times:
        day = eastern_date(clock, t)
        counts[day] = counts.get(day, 0) + 1
    return counts


async def drive_days(consts: dict, horizon_s: float, *, overlap: bool = False) -> dict:
    """Run the real feed and alert pollers over `horizon_s` of production time from
    noon Eastern, NJ Transit minting every token it is asked for and refusing every
    one it is shown, past the Eastern midnight and into the next day.

    A RUNNING PROCESS, NOT A COLD ONE, as the record's drive was: one healthy feed
    poll a cadence before t = 0 mints the token the process holds. That mint is a
    getToken POST on the same Eastern date, so it is counted with the rest.
    """
    poll_s, alert_s = consts["poll_s"], consts["alert_s"]
    clock = Clock()
    cache = reset_token_cache(clock)
    entry = seed_app()
    app = main_module.app

    # One fake for the whole drive, so every getToken POST of both dates is on one
    # list; the account's ten are its quota_after, so a run that reached them would
    # be answered with NJ Transit's refusal and show it.
    fake = FakeRailData(
        clock,
        accept_data=True,
        quota_after=consts["limit"],
        latency_ticks=OVERLAP_TICKS if overlap else 0,
    )
    install(fake)
    out: dict = {"clock": clock}
    try:
        with quiet_poll_logs():
            async with alerts_client() as client:
                clock.set(-poll_s)
                await pollers._refresh_njt(app, client)
                out["warm_ok"] = fake.token_posts == 1 and entry["error"] is None
                fake.accept_data = False
                for t, consumers in production_schedule(horizon_s, poll_s, alert_s):
                    clock.set(t)
                    if overlap and len(consumers) > 1:
                        await asyncio.gather(
                            pollers._refresh_njt(app, client), pollers._refresh_alerts(app, client)
                        )
                    else:
                        for who in consumers:
                            if who == "feed":
                                await pollers._refresh_njt(app, client)
                            else:
                                await pollers._refresh_alerts(app, client)
                    held = njt_auth.TOKEN_CACHE.cooldown()
                    if "first_cooldown" not in out and held is not None:
                        out["first_cooldown"] = (t, held)
                    if njt_auth.TOKEN_CACHE.mint_quota_refused:
                        out["quota_seen"] = True
                    if njt_auth.TOKEN_CACHE.mint_allowance_spent and "spent_at" not in out:
                        # The first poll the allowance held, read on both surfaces then.
                        out["spent_at"] = t
                        out["spent_status"] = (await serve("/api/status")).json()
                        out["spent_healthz"] = (await serve("/healthz")).json()
    finally:
        uninstall()
    out["end_status"] = (await serve("/api/status")).json()
    out["end_healthz"] = (await serve("/healthz")).json()
    out["mint_times"] = list(fake.mint_times)
    out["mint_answers"] = list(fake.mint_answers)
    out["token_posts"] = fake.token_posts
    out["cache_mint_requests"] = cache.mint_requests
    out["per_date"] = per_date(clock, fake.mint_times)
    return out


async def arm2(consts: dict) -> dict:
    rule("ARM 2: the feed and alert pollers at production cadence, across an Eastern midnight")
    allowance = njt_auth.DAILY_MINT_ALLOWANCE
    limit = consts["limit"]
    margin = njt_auth.MINT_QUOTA_RESET_MARGIN_S
    # Noon Eastern to two in the morning: the first date's allowance, its hold, the
    # reset, and the second date's allowance and hold, with an hour to spare.
    horizon = 14 * 3600.0
    reset_at = 12 * 3600.0 + margin
    run = await drive_days(consts, horizon)
    clock = run["clock"]
    times = run["mint_times"]
    first_t, first_held = run.get("first_cooldown") or (None, None)
    spent_at = run.get("spent_at")
    spent_allowance = (run.get("spent_status") or {}).get("njt_mint_allowance") or {}
    spent_cooldown = (run.get("spent_status") or {}).get("njt_mint_cooldown") or {}
    spent_codes = (run.get("spent_healthz") or {}).get("degraded") or []
    end_allowance = run["end_status"].get("njt_mint_allowance") or {}
    end_codes = run["end_healthz"].get("degraded") or []
    day1, day2 = eastern_date(clock, 0.0), eastern_date(clock, reset_at)

    print()
    print(f"  {horizon / 3600:g} hours of production polling from noon Eastern, {day1}: the feed")
    print(
        f"  poller every {consts['poll_s']:g}s and the alert poller every {consts['alert_s']:g}s, "
        "NJ Transit minting every"
    )
    print("  token and refusing every one, after one healthy poll that minted the held token.")
    print()
    print(f"    getToken POSTs land at t =      : {[_dur(t) for t in times]}")
    print(f"    per Eastern date                : {run['per_date']}")
    print(f"    njt_auth.DAILY_MINT_ALLOWANCE   : {allowance}")
    print(f"    first cooldown, opened at t =   : {_dur(first_t)}  {first_held}")
    print(f"    the allowance held from t =     : {_dur(spent_at)}")
    print(f"      /api/status njt_mint_allowance: {spent_allowance}")
    print(f"      /api/status njt_mint_cooldown : {spent_cooldown}")
    print(f"      /healthz degraded             : {spent_codes}")
    print(f"    at the end, t = {_dur(horizon)}")
    print(f"      /api/status njt_mint_allowance: {end_allowance}")
    print(f"      /healthz degraded             : {end_codes}")

    check(
        "ARM2 the warm-up poll minted one token and was served (a running process, not a cold one)",
        run["warm_ok"],
    )
    check(
        f"ARM2 getToken POSTs per Eastern date equal the allowance, {allowance} on each of the two "
        "the drive touches (was: one per poll, 4 a minute, 240 an hour, 5760 a day)",
        run["per_date"] == {day1: allowance, day2: allowance},
        str(run["per_date"]),
        "every poll minted",
    )
    # THE LADDER'S SPACING, pinned as measured: the held token's own mint a cadence
    # before t = 0, the one re-mint at t = 0, two POSTs when the 600s hold lapses, and
    # the allowance found spent when the doubled 1200s hold lapses at 1800s. A ladder
    # reset by every working mint would find it spent at 1200s instead.
    first_date = [t for t in times if t < reset_at]
    check(
        "ARM2 the ladder spaces the probes on the first date: the held token's mint, the "
        "re-mint at t = 0, two at 600s, and the allowance found spent at 1800s, the 600s "
        "hold doubled (was: one getToken POST per poll)",
        first_date == [-consts["poll_s"], 0.0, 600.0, 600.0] and spent_at == 1800.0,
        f"POSTs at {[_dur(t) for t in first_date]}, spent at {_dur(spent_at)}",
    )
    check(
        f"ARM2 the account's ten are out of this process's reach: its {allowance} a day and the "
        f"monitor's {consts['monitor']} leave {limit - allowance - consts['monitor']} (was: the "
        "four spare gone at 40s and the ten at 2.0min)",
        max(run["per_date"].values()) <= allowance and allowance + consts["monitor"] < limit,
        f"{max(run['per_date'].values())} a day at most",
    )
    check(
        "ARM2 the first cooldown is ours, opened by the first refused poll from the "
        "refused-token base (was: none until NJ Transit refused getToken POST 11)",
        first_t == 0.0
        and first_held is not None
        and first_held[0] == njt_auth.MINT_REJECTED_COOLDOWN_BASE_S
        and "rejected a freshly minted token" in first_held[1],
        f"t = {_dur(first_t)}, {first_held}",
        "The first cooldown is NJ Transit's own daily-cap refusal, on getToken POST 11",
    )
    check(
        "ARM2 NJ Transit's own daily-cap refusal is never drawn (was: on getToken POST 11)",
        "quota-refusal" not in run["mint_answers"]
        and not run.get("quota_seen")
        and "njt-mint-quota" not in spent_codes + end_codes,
        f"{run['mint_answers'].count('quota-refusal')} refusals in {run['token_posts']} POSTs",
    )
    check(
        "ARM2 once the allowance is spent the hold runs to the next Eastern midnight plus the "
        "margin, and /api/status and /healthz say so (new: no allowance existed)",
        spent_at is not None
        and spent_allowance
        == {"day": day1, "spent": allowance, "limit": allowance, "spent_out": True}
        and spent_cooldown.get("seconds_remaining") == reset_at - spent_at
        and njt_auth.MINT_ALLOWANCE_MESSAGE in spent_cooldown.get("detail", "")
        and "njt-mint-allowance" in spent_codes,
        f"held from {_dur(spent_at)} for {_dur(spent_cooldown.get('seconds_remaining'))}",
    )
    check(
        "ARM2 no poll reaches getToken during the hold, and the next Eastern date resumes at "
        "midnight plus the margin, spends its own allowance, and holds again",
        spent_at is not None
        and not any(spent_at <= t < reset_at for t in times)
        and min((t for t in times if t >= reset_at), default=None) == reset_at
        and end_allowance
        == {"day": day2, "spent": allowance, "limit": allowance, "spent_out": True}
        and "njt-mint-allowance" in end_codes,
        f"first POST of {day2} at {_dur(min((t for t in times if t >= reset_at), default=None))}",
    )
    check(
        "ARM2 the token cache agrees with what the upstream received",
        run["cache_mint_requests"] == run["token_posts"],
        f"{run['cache_mint_requests']} vs {run['token_posts']}",
    )

    # The same day with the two pollers in flight together at every instant both are
    # due, which is the interleaving reject_fresh's compare-and-clear exists for.
    overlap = await drive_days(consts, horizon, overlap=True)
    ov_times = overlap["mint_times"]
    print()
    print("  THE SAME DRIVE with both pollers in flight together whenever both are due:")
    print(f"    getToken POSTs land at t =      : {[_dur(t) for t in ov_times]}")
    print(f"    per Eastern date                : {overlap['per_date']}")
    check(
        "ARM2 with the two pollers in flight together it is the same: the allowance on each "
        "date, the hold to midnight plus the margin, no refusal drawn",
        overlap["per_date"] == {day1: allowance, day2: allowance}
        and min((t for t in ov_times if t >= reset_at), default=None) == reset_at
        and "quota-refusal" not in overlap["mint_answers"],
        str(overlap["per_date"]),
    )
    return {
        "per_date": run["per_date"],
        "spent_at": spent_at,
        "reset_at": reset_at,
        "first_cooldown": first_held[0] if first_held else None,
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
    install(fake)
    try:
        tasks = [asyncio.create_task(call_njt_post(cache)) for _ in range(n)]
        for _ in range(20):
            await asyncio.sleep(0)
        in_flight = sum(1 for t in tasks if not t.done())
        gate.set()
        outcomes = [label for label, _exc in await asyncio.gather(*tasks)]
        posts_after_burst = fake.token_posts
        next_call = (await call_njt_post(cache))[0]
    finally:
        uninstall()
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

    # The /api/status surface, driven through the real feed poller with a failing
    # mint: F05's window is live there, and it does publish.
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
    print("  The /api/status surface, after one feed poll whose mint failed:")
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
    saved: list[tuple[object, str, object]] = []

    def swap(module: object, name: str, value: object, *, required: bool = True) -> None:
        """A seam. `required=False` only where skipping it changes no count: the
        code it would have idled then runs for real, meets the unreachable network
        at the socket layer, waits out its retry rung, and makes no NJ Transit
        request. A required seam that is gone raises, which fails closed."""
        if not required and not hasattr(module, name):
            return
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, value)

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
    # NOTHING IN njt_auth OR httpx IS SWAPPED ABOVE THE SOCKET LAYER: not njt_auth's
    # transport, not TOKEN_CACHE, not any client class. njt_auth's own _httpx_post
    # builds its own client with whatever transport it composes, and only the
    # connection pool's hand-off at the bottom answers from the fake, so a repair
    # carried anywhere in that stack runs.

    app = main_module.app
    out: dict = {"marked_by": marked[0] if marked else None}
    methods_before = len(SocketLayer.methods)
    install(fake)
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
            out["refused_phase_mints"] = fake.token_posts - out["healthy_mints"]
            status = (await serve("/api/status")).json()
            out["status_cooldown"] = status.get("njt_mint_cooldown")
            out["status_njt_error"] = (status.get("feeds", {}).get("njt") or {}).get("last_error")
            log = list(fake.log)
            feed_marks_now, alert_marks_now = list(feed_marks), list(alert_marks)
    finally:
        uninstall()
        for module, name, value in reversed(saved):
            setattr(module, name, value)

    out["cache_is_as_imported"] = njt_auth.TOKEN_CACHE is TOKEN_CACHE_AS_IMPORTED
    reached = SocketLayer.methods[methods_before:]
    out["njt_posts_at_socket"] = reached.count("POST")
    out["alert_gets_at_socket"] = reached.count("GET")
    out["unreachable_at_socket"] = len(SocketLayer.unreachable)
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
    posts = "yes" if payload.get("njt_posts_at_socket") else "none"
    gets = "yes" if payload.get("alert_gets_at_socket") else "none"
    print("    NJ Transit POSTs answered at the socket layer, under every object")
    print(f"      njt_auth's own transport composed                    : {posts}")
    print(f"    keyless alert GETs answered at the socket layer        : {gets}")
    print(
        f"    requests refused as an unreachable network             : "
        f"{payload.get('unreachable_at_socket')}"
    )
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
    feed_mints = feed_mints or []
    alert_mints = alert_mints or []
    after = payload.get("refused_phase_mints")
    cooldown = payload.get("status_cooldown") or {}
    print(f"    getToken POSTs after the refusals began, in all : {after}")
    check(
        "ARM4 after the refusals began the process paid ONE getToken POST in all, the one "
        "re-mint the first refused poll buys, 2 across the whole run (was: one per cycle, "
        f"{FEED_WINDOWS} of {FEED_WINDOWS} feed and {ALERT_WINDOWS} of {ALERT_WINDOWS} alert "
        "cycles)",
        after == 1 and payload.get("healthy_mints") == 1,
        f"{after} after, {payload.get('healthy_mints')} before",
    )
    check(
        "ARM4 every scheduler cycle after the first one in each loop cost no getToken POST "
        "(was: 1 each)",
        len(feed_mints) == FEED_WINDOWS
        and len(alert_mints) == ALERT_WINDOWS
        and sum(feed_mints[1:]) == 0
        and sum(alert_mints[1:]) == 0
        and sum(feed_mints) + sum(alert_mints) <= 1,
        f"feed {feed_mints}, alerts {alert_mints}",
    )
    check(
        "ARM4 /api/status publishes the refused-token hold (was: no cooldown)",
        cooldown.get("seconds_remaining") is not None
        and 0 < cooldown["seconds_remaining"] <= njt_auth.MINT_REJECTED_COOLDOWN_BASE_S
        and "rejected a freshly minted token" in cooldown.get("detail", ""),
        str(cooldown),
    )
    return {
        "feed_cycles": FEED_WINDOWS,
        "alert_cycles": ALERT_WINDOWS,
        "after": after,
        "total": (after or 0) + (payload.get("healthy_mints") or 0),
    }


# ---------------------------------------------------------------------------
# ARM 5: what the allowance hold serves, and what it cannot
# ---------------------------------------------------------------------------


async def arm5(consts: dict) -> dict:
    rule("ARM 5: an over-age token serves through the allowance hold; a refused one cannot")
    allowance = njt_auth.DAILY_MINT_ALLOWANCE
    poll_s = consts["poll_s"]
    reset_at = 12 * 3600.0 + njt_auth.MINT_QUOTA_RESET_MARGIN_S
    # The held token was minted at 23:00 the evening before, so at noon it is an hour
    # past MAX_TOKEN_AGE_S and every poll asks for a replacement. getToken fails from
    # noon, which is what spends the day's allowance while the old token still works.
    minted_at = -13 * 3600.0
    # At 13:00, inside the hold, NJ Transit revokes the held token and getToken
    # recovers: from then on a fresh token would work, and none may be asked for.
    revoked_at = 3600.0
    clock = Clock()
    cache = reset_token_cache(clock)
    entry = seed_app()
    app = main_module.app
    fake = FakeRailData(clock, accept_data=True)
    install(fake)
    polls: list[dict] = []
    try:
        with quiet_poll_logs():
            async with alerts_client() as client:
                clock.set(minted_at)
                await pollers._refresh_njt(app, client)
                held = cache.peek()
                fake.mint_fails = True
                t = 0.0
                while t <= reset_at + 3 * poll_s:
                    clock.set(t)
                    if t == revoked_at:
                        fake.refused_tokens.add(held)
                        fake.mint_fails = False
                    mints_before, data_before = fake.token_posts, fake.data_posts
                    await pollers._refresh_njt(app, client)
                    polls.append(
                        {
                            "t": t,
                            "mints": fake.token_posts - mints_before,
                            "data": [tok for _, _, tok in fake.data_log[data_before:]],
                            "error": entry["error"],
                        }
                    )
                    if t == revoked_at - poll_s:
                        before_revoke = {
                            "allowance": cache.allowance(),
                            "spent": cache.mint_allowance_spent,
                            "age": clock.now - minted_at,
                        }
                    if t == revoked_at + poll_s:
                        dark_status = (await serve("/api/status")).json()
                        dark_healthz = (await serve("/healthz")).json()
                    t += poll_s
    finally:
        uninstall()

    serving = [p for p in polls if p["t"] < revoked_at]
    dark = [p for p in polls if revoked_at < p["t"] < reset_at]
    at_revoke = next(p for p in polls if p["t"] == revoked_at)
    after = [p for p in polls if p["t"] >= reset_at]
    day1 = eastern_date(clock, 0.0)
    posts = per_date(clock, [t for t in fake.mint_times if t >= 0])
    dark_njt = (dark_status.get("feeds") or {}).get("njt") or {}
    dark_error = (dark_njt.get("last_error") or {}).get("detail") or ""
    print()
    print(f"  The held token was minted at t = {_dur(-minted_at)} before noon, so it is past")
    print(
        f"  MAX_TOKEN_AGE_S ({_dur(njt_auth.MAX_TOKEN_AGE_S)}) from the first poll; "
        "getToken fails from noon."
    )
    print(f"    getToken POSTs from noon, per Eastern date : {posts}")
    print(
        f"    before the revocation, at t = {_dur(revoked_at - poll_s)}       : allowance "
        f"{before_revoke['allowance']}, held token {_dur(before_revoke['age'])} old"
    )
    print(
        f"    feed polls served on the held token        : "
        f"{sum(1 for p in serving if p['error'] is None)} of {len(serving)}"
    )
    print(f"    at t = {_dur(revoked_at)} NJ Transit revokes it and getToken recovers")
    print(
        f"      that poll                                : {at_revoke['mints']} getToken, "
        f"{len(at_revoke['data'])} data POST"
    )
    print(
        f"      and the error it published               : "
        f"{(at_revoke['error'] or {}).get('detail', '')[:90]}"
    )
    print(
        f"    {'then ' + str(len(dark)) + ' polls to the reset':<43}: "
        f"{sum(p['mints'] for p in dark)} getToken, {sum(len(p['data']) for p in dark)} data POSTs"
    )
    print(f"      /api/status feeds.njt.last_error         : {dark_error[:90]}")
    print(f"      /healthz degraded                        : {dark_healthz.get('degraded')}")
    print(
        f"    NJ Transit dark for                        : {_dur(reset_at - revoked_at)}, "
        "though a fresh token would have worked throughout"
    )
    print(
        f"    {'at t = ' + _dur(reset_at) + ', the first poll':<43}: {after[0]['mints']} getToken, "
        f"error {after[0]['error']}"
    )
    check(
        "ARM5 an over-age token serves through the allowance hold: every feed poll before the "
        "revocation was served on it, while getToken failed and the day's allowance went "
        "(new: no allowance existed before this branch)",
        posts.get(day1) == allowance
        and before_revoke["spent"]
        and before_revoke["age"] > njt_auth.MAX_TOKEN_AGE_S
        and all(p["error"] is None and set(p["data"]) == {held} for p in serving),
        f"{sum(1 for p in serving if p['error'] is None)} of {len(serving)} polls served, "
        f"{posts.get(day1)} getToken POSTs",
    )
    check(
        "ARM5 a refused token is not served through it: from the revocation to the reset no "
        "request of either kind reaches NJ Transit, and the feed carries the allowance error",
        at_revoke["mints"] == 0
        and at_revoke["data"] == [held]
        and njt_auth.MINT_ALLOWANCE_MESSAGE in (at_revoke["error"] or {}).get("detail", "")
        and bool(dark)
        and all(p["mints"] == 0 and not p["data"] for p in dark)
        and njt_auth.MINT_ALLOWANCE_MESSAGE in dark_error
        and "njt-mint-allowance" in (dark_healthz.get("degraded") or []),
        f"{sum(p['mints'] for p in dark)} getToken and {sum(len(p['data']) for p in dark)} data "
        f"POSTs over {len(dark)} polls",
    )
    check(
        "ARM5 and NJ Transit stays dark until the reset: the first poll at the next Eastern "
        "midnight plus the margin mints, and the feed is served again",
        after[0]["t"] == reset_at
        and after[0]["mints"] == 1
        and after[0]["error"] is None
        and all(p["error"] is None for p in after),
        f"t = {_dur(after[0]['t'])}, {after[0]['mints']} getToken, error {after[0]['error']}",
    )
    return {"dark_s": reset_at - revoked_at, "served": len(serving)}


# ---------------------------------------------------------------------------


async def run() -> dict:
    consts = section_containment()
    a1 = await arm1()
    a2 = await arm2(consts)
    a3 = await arm3(consts)
    return {"consts": consts, "a1": a1, "a2": a2, "a3": a3}


BRANCH = "claude/a01-mint-allowance"


def main() -> int:
    print("A01: repeated NJ Transit token rejection bypasses the cooldown (FIXED, the record)")
    print(f"repository : {REPO}")
    print("upstream   : entirely fake, in process. No NJT host, no real credential, no mint spent.")
    print("clock      : arms 1, 2, 3 and 5 run on a hand-cranked clock, every timeline relative")
    print("             to t = 0 at noon Eastern; arm 4 counts, it does not time")
    results = asyncio.run(run())
    results["a4"] = arm4(results["consts"])
    results["a5"] = asyncio.run(arm5(results["consts"]))
    rule("Result")
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the recorded disposition:")
        for name in FAILURES:
            print(f"    - {name}")
        print()
        print("DISPOSITION: CHANGED, this script no longer matches the recorded fix.")
        return 1
    print("  every check matches the recorded disposition.")
    a1, a2, a4, a5 = results["a1"], results["a2"], results["a4"], results["a5"]
    twelve = next(r for r in results["a3"]["rows"] if r["n"] == 12)
    w1, w2, w3 = a1["ladder"]
    print()
    print(
        f"DISPOSITION: FIXED ({BRANCH})  BEFORE: ten njt_post calls sharing one TokenCache, "
        "against a fake that mints every time and refuses every token, made 11 getToken and "
        "20 data POSTs and left no cooldown, and the process as built paid one getToken POST "
        "per feed and alert cycle. AFTER: the same ten calls made "
        f"{a1['mints']} getToken and {a1['data']} data POSTs, then every call was held without "
        f"a request ({_secs(a1['hold'])} hold from the refused-token base, the token dropped); "
        f"the streak ends on acceptance, not on issue (windows {_secs(w1)}, {_secs(w2)}, "
        f"{_secs(w3)}); the process as built paid {a4.get('total')} getToken POSTs in all, "
        f"{a4.get('after')} after the refusals began; at the production cadences across an "
        f"Eastern midnight the pollers sent {a2['per_date']} getToken POSTs, the first "
        f"cooldown ours ({_secs(a2['first_cooldown'])}) and NJ Transit's refusal never drawn, "
        f"the allowance holding from {_dur(a2['spent_at'])} to midnight plus the margin; an "
        "over-age token serves through that hold, and a revoked one leaves NJ Transit dark "
        f"for {_dur(a5['dark_s'])} until the reset; and F05's failed-mint cooldown still "
        f"holds ({twelve['n']} concurrent callers, {twelve['posts']} getToken POST, "
        f"{twelve['refused']} refused without a request)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(as_shipped_child() if AS_SHIPPED else main())
