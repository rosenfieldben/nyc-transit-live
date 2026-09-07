#!/usr/bin/env python3
"""F05 (P1) "Failed NJT token mints can repeat rapidly" : reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, finding F05):

    "The lock serializes callers and shares a successfully minted token. A failed
    mint leaves no shared failure/cooldown state. In a hermetic reproduction, 12
    concurrent callers invoked the failing mint callback 12 times and obtained
    zero tokens. Subsequent position polls, alert polls and warmup attempts can
    try again.

    The README records an observed ten-attempt daily account limit, with failed
    attempts counted, and describes a shared monitor/production budget. That
    provider charging behavior was not independently retested in this audit. If
    the recorded behavior remains correct, a short authentication failure can
    consume the remaining daily budget and disable NJT until reset. Repeated
    failed requests are proven; the account-wide quota consequence is conditional
    on that recorded upstream behavior."

    Cited: backend/njt_auth.py L661 (TokenCache.get),
    backend/tests/test_njt_auth.py L389 (the existing failed-mint test),
    README.md L472 (the documented account budget).

THE FIX, and what this script now measures (branch claude/f05-mint-cooldown).
njt_auth.TokenCache records a failed mint and a NOT-BEFORE instant, and until it
lapses every get() raises NjtMintCooldownError WITHOUT calling getToken. Two
policies, chosen by the exception type: a quota refusal holds to the next Eastern
midnight (the reset observed 2026-09-03), everything else backs off from
MINT_COOLDOWN_BASE_S doubling to MINT_COOLDOWN_CAP_S, and a success resets it. An
over-age token keeps serving through a cooldown, because MAX_TOKEN_AGE_S is a
budget ceiling and not an expiry.

SO EVERY NUMBER BELOW IS NOW A BEFORE AND AN AFTER, measured the same way, and
the script fails if the AFTER stops holding. The "before" figures are not
retyped from the audit: they are the count of scheduled attempts the same drive
produces, which is exactly what the old code turned into getToken POSTs one for
one.

NO MINT IS EVER SPENT BY THIS SCRIPT, AND THAT IS ENFORCED RATHER THAN INTENDED.
There are no NJ Transit credentials in this environment and none are created. The
script sets deliberately fake credential values, points every NJT env seam at
127.0.0.1:9 before importing a single backend module, replaces
njt_auth._httpx_post with a fake that returns bytes from memory, and every fake
transport calls a guard that turns any URL containing "njtransit" or "raildata"
into an immediate hard exit. The alerts client is an httpx.MockTransport carrying
the same guard. Nothing in this file opens a socket.

RUN IT (from the repository root):

    ./.venv/bin/python docs/reviews/audit-2026-09-05/f05_njt_failed_mint_storm.py

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented):

  * njt_auth.TokenCache, the real class, including its single-flight lock, its
    double check, its `mints` / `mint_requests` counters and its
    `mint_quota_refused` flag. Section B constructs real instances of it.
  * njt_auth.mint: every "failed mint" in this script is produced by the real
    mint function reading a fake upstream response, never by a hand-raised
    exception, so the exception types (NjtAuthError, NjtMintQuotaError) and the
    message constant (MINT_QUOTA_MESSAGE) are the ones production raises.
  * njt_auth.njt_post: the one door, including its single re-mint branch.
  * pollers._refresh_njt driven for real poll cycles, through
    main.fetch_njt_trains and njt_auth.njt_post, against the real
    njt_auth.TOKEN_CACHE, with the real njt_static_status gate.
  * pollers._refresh_alerts driven for real alert polls, through
    main.fetch_service_alerts and feeds.alerts.active_alert_feeds, with the NJT
    alert feed taking its real njt_post path.
  * warmups._warm_njt_static driven for real retry rungs, through
    njt_static.load_njt_static, njt_static._download_via_token,
    static_shared.staged_fetch and njt_auth.njt_post; the backoff numbers come
    from warmups._rung reading main.STATIC_RETRY_SCHEDULE_S.
  * The cadence constants are read live: pollers.POLL_INTERVAL_S,
    pollers.ALERT_POLL_INTERVAL_S, njt_auth.DAILY_MINT_LIMIT.
  * README.md is parsed for its own budget numbers; none of them is retyped here.

WHAT IS INJECTED (the faults, stated plainly):

  * The NJ Transit upstream. FakeRailData answers getToken with HTTP 500 and a
    body of one of two shapes: a generic fault ({"errorMessage":"An unexpected
    error occurred."}, the suite's REAL_500 control) or the observed daily-cap
    refusal ({"errorMessage":"Daily usage limit ..."}). Both are the shapes
    backend/tests/test_njt_auth.py already pins. The successful control answers
    200 with {"UserToken": ...}.
  * Fake credentials in os.environ, so is_configured() is true. They are checked
    to be the fake ones before anything runs.
  * DATA_DIR points at a fresh temporary directory, so the static warmup's cold
    start is reachable and the repository's data/ is never touched.
  * Section C1 replaces asyncio.sleep for the duration of the warmup drive only,
    recording the delay the production code asked for and returning immediately.
    The delays are recorded and compared against warmups._rung, so the schedule
    is measured rather than assumed. random is seeded so the +-10% jitter that
    _retry_delay applies is reproducible.
  * Section C4 injects ONE successful static load (a stubbed
    njt_static.load_njt_static) purely to show that the warmup task RETURNS at
    ready and therefore stops contributing.
  * The five keyless alert feeds are answered with a valid, empty GTFS-Realtime
    FeedMessage, so the alert poll is an ordinary poll in which only NJ Transit
    fails.
  * The per-minute and per-hour totals in section C5 are arithmetic over MEASURED
    per-call costs and PRODUCTION cadence constants. The arithmetic is printed in
    full so a reader can check it.

HERMETIC AND DETERMINISTIC: no network, no credentials, no NJT host, no wall
clock dependence (every timeline below is relative to t=0 of a simulated
failure, never to today's date), and the jitter source is seeded.

RECORDED DISPOSITION: FIXED (claude/f05-mint-cooldown). The script exits 0 while
the FIXED behaviour holds and non-zero the moment it regresses, which is the same
contract it had as a reproduction with the expectations moved: it used to prove
that N concurrent callers cost N getToken POSTs, and it now proves they cost one.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"

# ---------------------------------------------------------------------------
# The containment, installed BEFORE any backend module is imported. env_seams
# reads these at import time, so setting them afterwards would be too late.
# ---------------------------------------------------------------------------

FAKE_HOST = "http://127.0.0.1:9"
os.environ["NJT_TOKEN_URL"] = f"{FAKE_HOST}/f05-fake/getToken"
os.environ["NJT_TU_URL"] = f"{FAKE_HOST}/f05-fake/getTripUpdates"
os.environ["NJT_ALERTS_URL"] = f"{FAKE_HOST}/f05-fake/getAlerts"
os.environ["NJT_STATIC_URL"] = f"{FAKE_HOST}/f05-fake/getStatic"
os.environ["NJT_USERNAME"] = "f05-fake-username"
os.environ["NJT_PASSWORD"] = "f05-fake-password"
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="f05-njt-data-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR

# CONTAINMENT. This script drives the app as a CONFIGURED deployment, which is the
# finding rather than an oversight, so it keeps the fabricated credentials it set
# above. contain() leaves those alone and fills every NJ Transit address seam this
# script did not set for itself, so a route it never thought about still cannot leave
# the machine. See _hermetic.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain(keep_credentials=True)

sys.path.insert(0, str(BACKEND))

import asyncio  # noqa: E402
import random  # noqa: E402
import unittest.mock  # noqa: E402
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

import httpx  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import cache as cache_module  # noqa: E402
import main as main_module  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import pollers  # noqa: E402
import warmups  # noqa: E402
from feeds import alerts as alerts_feed  # noqa: E402

# CONTAINMENT, ASSERTED. This script is configured on purpose, so what is checked is
# that the credentials in this process are its OWN fabricated pair and that no NJ
# Transit address survived the imports.
_hermetic.verify(expect_configured=True)


FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    """Record one assertion. Every check runs, then the script exits on the tally."""
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{(': ' + detail) if detail else ''}")
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
        raise SystemExit(f"F05 CONTAINMENT BREACH: a request was addressed to {url!r}")


# The response shapes, byte for byte the ones backend/tests/test_njt_auth.py pins.
REAL_500 = b'{"errorMessage":"An unexpected error occurred."}'
QUOTA_REFUSAL = json.dumps({"errorMessage": "Daily usage limit of 10 reached."}).encode()
INVALID_TOKEN = b'{"errorMessage":"Invalid token."}'
TOKEN_OK = json.dumps({"UserToken": "f05-fake-token-0123456"}).encode()

# Section A and B call njt_auth.mint / njt_post directly, so they name their own
# URLs and their own credential mapping rather than reading the environment.
DIRECT_TOKEN_URL = f"{FAKE_HOST}/f05-direct/getToken"
DIRECT_DATA_URL = f"{FAKE_HOST}/f05-direct/getGTFS"
DIRECT_ENV = {njt_auth.USERNAME_VAR: "f05-direct-user", njt_auth.PASSWORD_VAR: "f05-direct-pass"}


class FakeRailData:
    """The whole NJ Transit upstream, in process, answering from memory.

    Counts getToken POSTs separately from data POSTs, because it is the getToken
    count that spends njt_auth.DAILY_MINT_LIMIT.
    """

    def __init__(
        self,
        *,
        mint: tuple[int, bytes] = (500, REAL_500),
        data: tuple[int, bytes] = (200, b"body"),
        hold_first_mint: asyncio.Event | None = None,
        clock: "Clock | None" = None,
        quota_after: int | None = None,
    ) -> None:
        self.mint_response = mint
        self.data_response = data
        self.hold_first_mint = hold_first_mint
        self.clock = clock
        # WHAT NJ TRANSIT ACTUALLY DOES AFTER THE TENTH. Past this many getToken
        # POSTs the answer becomes the observed daily-cap refusal, which models the
        # end state honestly: a run of ordinary failures does not go on being
        # ordinary forever, it spends the budget and then gets told so.
        self.quota_after = quota_after
        self.token_posts = 0
        self.data_posts = 0
        # WHEN each getToken POST arrived, on the drive's simulated clock. The
        # counts below say how many attempts reached NJ Transit; these say how far
        # apart they were, which is the property the cooldown actually promises and
        # the one a count alone cannot distinguish from a lucky schedule.
        self.token_post_times: list[float] = []

    def gaps(self) -> list[float]:
        """Seconds between consecutive getToken POSTs, in order."""
        t = self.token_post_times
        return [round(b - a, 2) for a, b in zip(t, t[1:], strict=False)]

    async def __call__(self, url: str, form: dict, timeout_s: float, **_: object):
        guard(url)
        if "getToken" in url:
            self.token_posts += 1
            if self.clock is not None:
                self.token_post_times.append(self.clock.now)
            if self.hold_first_mint is not None and self.token_posts == 1:
                # Hold the first mint open so every other caller is provably
                # parked inside TokenCache.get while it runs.
                await self.hold_first_mint.wait()
            if self.quota_after is not None and self.token_posts > self.quota_after:
                return (500, QUOTA_REFUSAL)
            return self.mint_response
        self.data_posts += 1
        return self.data_response


async def _forbidden_transport(url: str, form: dict, timeout_s: float, **_: object):
    raise SystemExit(f"F05 CONTAINMENT BREACH: the real njt_auth transport was called for {url!r}")


# The default from here on. Every section that needs the module-global door
# installs its own FakeRailData over this and restores it afterwards.
njt_auth._httpx_post = _forbidden_transport


def install(fake: FakeRailData) -> None:
    njt_auth._httpx_post = fake


def uninstall() -> None:
    njt_auth._httpx_post = _forbidden_transport


class Clock:
    """A hand-cranked pair of clocks, cranked by the delays production asks for.

    The cooldown is measured on a monotonic clock, and the quota hold's LENGTH is
    measured once against a wall clock. Both advance together here, so a drive that
    replays a warmup's rung schedule or a poller's cadence moves simulated time by
    exactly the interval the production code asked to sleep. Nothing waits.
    """

    # 2026-09-05 12:00:00 EDT: a plain summer noon, twelve hours before the mint
    # budget resets. Fixed, so this script's output does not depend on the day it
    # is run, which is the same discipline every other timeline here follows.
    NOON_EDT = datetime(2026, 9, 5, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()

    def __init__(self) -> None:
        self.now = 0.0
        self.wall = self.NOON_EDT

    def __call__(self) -> float:
        return self.now

    def wall_clock(self) -> float:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self.wall += seconds


def reset_token_cache(clock: Clock | None = None) -> njt_auth.TokenCache:
    """Stand a COLD cache in front of the app for the next drive, and return it.

    A fresh instance rather than a clear, because since the fix a cache carries a
    cooldown as well as a token and a flag, and a drive that inherited the previous
    section's window would measure the previous section. Every consumer in this
    script reaches njt_auth.TOKEN_CACHE by name, so replacing the module global is
    what puts the clock in front of all three of them at once.
    """
    clock = clock or Clock()
    fresh = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    njt_auth.TOKEN_CACHE = fresh
    return fresh


def empty_alert_feed() -> bytes:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = 1_756_000_000
    return feed.SerializeToString()


EMPTY_ALERT_FEED = empty_alert_feed()


def alerts_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        guard(str(request.url))
        return httpx.Response(200, content=EMPTY_ALERT_FEED)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=30)


# ---------------------------------------------------------------------------
# Section 0: containment
# ---------------------------------------------------------------------------


def section_containment() -> None:
    rule("Section 0: containment (no mint can be spent from here)")
    print(f"  NJT_TOKEN_URL in this process : {njt_auth.NJT_TOKEN_URL}")
    print(f"  NJT_USERNAME in this process  : {os.environ['NJT_USERNAME']}")
    print(f"  DATA_DIR in this process      : {njt_static.DATA_DIR}")
    check(
        "every NJT endpoint this process knows points at the loopback discard port",
        all(
            url.startswith(FAKE_HOST)
            for url in (
                njt_auth.NJT_TOKEN_URL,
                njt_static.NJT_STATIC_URL,
                alerts_feed.ALERT_FEED_URLS["njt"],
            )
        ),
    )
    check(
        "the credentials in the environment are the script's own fake pair",
        njt_auth.credentials() == ("f05-fake-username", "f05-fake-password"),
    )
    check(
        "the real transport is replaced by one that hard-exits if called",
        njt_auth._httpx_post is _forbidden_transport,
    )
    check(
        "the static cache root is a temporary directory, not the repository's data/",
        str(njt_static.DATA_DIR).startswith(tempfile.gettempdir()),
        str(njt_static.DATA_DIR),
    )


# ---------------------------------------------------------------------------
# Section A: what njt_auth already does right, measured before anything is
# accused. A finding is only fair once the protections are on the record.
# ---------------------------------------------------------------------------


async def section_protections() -> None:
    rule("Section A: the protections njt_auth already has (the control measurements)")

    # A1: the single-flight lock, on the SUCCESS path.
    fake = FakeRailData(mint=(200, TOKEN_OK))
    cache = njt_auth.TokenCache()
    gate = asyncio.Event()
    fake.hold_first_mint = gate

    async def mint_once() -> str:
        return await njt_auth.mint(transport=fake, env=DIRECT_ENV, url=DIRECT_TOKEN_URL)

    tasks = [asyncio.create_task(cache.get(mint_once)) for _ in range(12)]
    for _ in range(20):
        await asyncio.sleep(0)
    gate.set()
    tokens = await asyncio.gather(*tasks)
    print()
    print("  A1  twelve concurrent callers, mint SUCCEEDS")
    print(f"        getToken POSTs sent      : {fake.token_posts}")
    print(f"        cache.mint_requests      : {cache.mint_requests}")
    print(f"        cache.mints              : {cache.mints}")
    print(f"        distinct tokens returned : {len(set(tokens))}  callers served: {len(tokens)}")
    check(
        "A1 the lock turns 12 concurrent callers into exactly ONE successful mint",
        fake.token_posts == 1 and cache.mint_requests == 1 and cache.mints == 1,
        f"{fake.token_posts} getToken POSTs",
    )
    check(
        "A1 all 12 callers are served the same shared token",
        len(tokens) == 12 and len(set(tokens)) == 1 and tokens[0] == "f05-fake-token-0123456",
    )

    # A2: njt_post buys exactly one re-mint per attempt, then gives up.
    fake = FakeRailData(mint=(200, TOKEN_OK), data=(500, INVALID_TOKEN))
    cache = njt_auth.TokenCache()
    raised = None
    try:
        await njt_auth.njt_post(
            DIRECT_DATA_URL,
            cache=cache,
            transport=fake,
            env=DIRECT_ENV,
            token_url=DIRECT_TOKEN_URL,
        )
    except njt_auth.NjtAuthError as exc:
        raised = exc
    print()
    print("  A2  upstream keeps answering the probe's invalid-token shape")
    print(f"        getToken POSTs sent : {fake.token_posts}   data POSTs sent: {fake.data_posts}")
    print(f"        raised              : {type(raised).__name__}")
    check(
        "A2 one attempt buys exactly ONE re-mint (2 mints total), never a loop",
        fake.token_posts == 2 and fake.data_posts == 2 and isinstance(raised, njt_auth.NjtAuthError),
    )

    # A3: a genuine 500 mints nothing extra; the cached token is reused.
    fake = FakeRailData(mint=(200, TOKEN_OK), data=(500, REAL_500))
    cache = njt_auth.TokenCache()
    for _ in range(5):
        try:
            await njt_auth.njt_post(
                DIRECT_DATA_URL,
                cache=cache,
                transport=fake,
                env=DIRECT_ENV,
                token_url=DIRECT_TOKEN_URL,
            )
        except njt_auth.NjtUpstreamError:
            pass
    print()
    print("  A3  five attempts against a GENUINE 500 (the control body)")
    print(f"        getToken POSTs sent : {fake.token_posts}   data POSTs sent: {fake.data_posts}")
    check(
        "A3 a real outage costs exactly ONE mint across five attempts",
        fake.token_posts == 1 and fake.data_posts == 5,
    )

    # A4: the quota refusal is typed, named and recorded.
    fake = FakeRailData(mint=(500, QUOTA_REFUSAL))
    cache = njt_auth.TokenCache()
    quota_exc = None
    try:
        await cache.get(lambda: njt_auth.mint(transport=fake, env=DIRECT_ENV, url=DIRECT_TOKEN_URL))
    except njt_auth.NjtAuthError as exc:
        quota_exc = exc
    print()
    print("  A4  getToken answers the observed daily-cap refusal")
    print(f"        raised                  : {type(quota_exc).__name__}: {quota_exc}")
    print(f"        cache.mint_quota_refused: {cache.mint_quota_refused}")
    check(
        "A4 a spent budget is its own type, a subclass of NjtAuthError",
        isinstance(quota_exc, njt_auth.NjtMintQuotaError)
        and isinstance(quota_exc, njt_auth.NjtAuthError),
    )
    check(
        "A4 the message is the module's own constant, carrying no byte of the body",
        str(quota_exc) == njt_auth.MINT_QUOTA_MESSAGE and "Daily usage" not in str(quota_exc),
    )
    check("A4 the refusal is recorded on the cache for /healthz", cache.mint_quota_refused is True)

    print()
    print("  So the lock, the one-re-mint branch, the quota type and the quota flag")
    print("  all exist and all work, and every one of them is on the SUCCESS or the")
    print("  classification side. The finding was that none of them was a shared")
    print("  FAILURE state. Section B measures the one that now is.")


# ---------------------------------------------------------------------------
# Section B: the concurrent fan-out of a FAILED mint (the audit's number)
# ---------------------------------------------------------------------------


async def fan_out(n: int, mint_response: tuple[int, bytes]) -> dict:
    """N concurrent callers into ONE real TokenCache while the mint fails.

    The first mint is held open until every caller has entered TokenCache.get, so
    the callers are provably concurrent rather than sequential retries.
    """
    gate = asyncio.Event()
    fake = FakeRailData(mint=mint_response, hold_first_mint=gate)
    clock = Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    callback_calls = 0
    entered = 0

    async def failing_mint() -> str:
        nonlocal callback_calls
        callback_calls += 1
        # The REAL mint function, reading the fake upstream. The exception below
        # is production's, not the script's.
        return await njt_auth.mint(transport=fake, env=DIRECT_ENV, url=DIRECT_TOKEN_URL)

    cooled: list[BaseException] = []

    async def caller() -> str | None:
        nonlocal entered
        entered += 1
        try:
            return await cache.get(failing_mint)
        except njt_auth.NjtMintCooldownError as exc:
            # Refused HERE, without a request. This arm did not exist before the fix.
            cooled.append(exc)
            return None
        except njt_auth.NjtAuthError:
            return None

    tasks = [asyncio.create_task(caller()) for _ in range(n)]
    for _ in range(20):
        await asyncio.sleep(0)
    at_gate = {
        "entered": entered,
        "mint_requests": cache.mint_requests,
        "callback_calls": callback_calls,
    }
    gate.set()
    results = await asyncio.gather(*tasks)
    return {
        "n": n,
        "at_gate": at_gate,
        "callback_calls": callback_calls,
        "getToken_posts": fake.token_posts,
        "mint_requests": cache.mint_requests,
        "mints": cache.mints,
        "tokens": [r for r in results if r is not None],
        "quota_flag": cache.mint_quota_refused,
        "cooled": len(cooled),
        "cooldown_s": cache.cooldown_remaining(),
    }


async def section_fanout() -> list[dict]:
    rule("Section B: concurrent fan-out while the mint fails (measurement 1)")
    rows = []
    for n in (1, 2, 3, 5, 12):
        rows.append(await fan_out(n, (500, REAL_500)))
    print()
    print("  Real njt_auth.TokenCache, real njt_auth.mint, fake upstream answering")
    print("  HTTP 500 with the suite's control body. One row per burst size.")
    print()
    print("    N   in-flight   mint_requests   callback   getToken   refused   cache   tokens")
    print("        at gate     at gate         calls      POSTs      here      .mints  obtained")
    print("    " + "-" * 78)
    for row in rows:
        gate = row["at_gate"]
        print(
            f"    {row['n']:>2}   {gate['entered']:>9}   {gate['mint_requests']:>13}   "
            f"{row['callback_calls']:>8}   {row['getToken_posts']:>8}   "
            f"{row['cooled']:>7}   {row['mints']:>6}   {len(row['tokens']):>8}"
        )
    print()
    for row in rows:
        n = row["n"]
        check(
            f"B N={n}: every caller was in flight before the first mint returned",
            row["at_gate"]["entered"] == n and row["at_gate"]["mint_requests"] == 1,
            f"{row['at_gate']['entered']} entered, {row['at_gate']['mint_requests']} minted so far",
        )
    for row in rows:
        n = row["n"]
        check(
            f"B N={n}: the failing mint callback ran exactly ONCE, whatever N is",
            row["callback_calls"] == 1 and row["getToken_posts"] == 1,
            f"{row['callback_calls']} callback calls, {row['getToken_posts']} getToken POSTs",
        )
        check(
            f"B N={n}: the other N-1 callers were refused here, without a request",
            row["cooled"] == n - 1,
            f"{row['cooled']} of {n - 1} raised NjtMintCooldownError",
        )
        check(
            f"B N={n}: TokenCache.mint_requests agrees with the callback's own count",
            row["mint_requests"] == row["callback_calls"],
            f"{row['mint_requests']} vs {row['callback_calls']}",
        )
        check(
            f"B N={n}: zero tokens obtained and cache.mints stayed 0",
            row["tokens"] == [] and row["mints"] == 0,
        )
        check(
            f"B N={n}: and the burst left a cooldown behind for whoever comes next",
            row["cooldown_s"] == njt_auth.MINT_COOLDOWN_BASE_S,
            f"{row['cooldown_s']}s",
        )
    twelve = next(r for r in rows if r["n"] == 12)
    print()
    print("  THE AUDIT'S SENTENCE, RE-MEASURED. It recorded: 12 concurrent callers")
    print("  invoked the failing mint callback 12 times and obtained zero tokens.")
    print(f"  Now: {twelve['callback_calls']} callback call, {twelve['getToken_posts']} getToken "
          f"POST, {len(twelve['tokens'])} tokens, and the other")
    print(f"  {twelve['cooled']} callers were refused inside the process. The relationship")
    print("  across the table is attempts == 1 at every N: the second check inside")
    print("  the lock now looks for a COOLDOWN as well as a token, and a failure")
    print("  leaves one behind.")
    check(
        "B the audit's headline number is fixed (12 callers, 1 mint, 0 tokens)",
        twelve["callback_calls"] == 1 and twelve["getToken_posts"] == 1 and twelve["tokens"] == [],
    )
    check(
        "B the two counters agree at every burst size (no hidden attempt, no double count)",
        all(r["mint_requests"] == r["callback_calls"] == r["getToken_posts"] for r in rows),
    )
    check(
        "B attempts are exactly 1, not N and not log N",
        [r["callback_calls"] for r in rows] == [1] * len(rows),
    )

    # The worst case: the same fan-out against a budget that is ALREADY spent.
    quota = await fan_out(12, (500, QUOTA_REFUSAL))
    print()
    print("  The same burst when the account's budget is ALREADY spent:")
    print(f"    getToken POSTs sent      : {quota['getToken_posts']}")
    print(f"    tokens obtained          : {len(quota['tokens'])}")
    print(f"    cache.mint_quota_refused : {quota['quota_flag']}")
    print(f"    cooldown left behind     : {quota['cooldown_s']:.0f}s "
          f"({quota['cooldown_s'] / 3600:.1f}h, to the next Eastern midnight)")
    print("    One attempt is charged to the cap it just bounced off, the flag is")
    print("    set by that one, and nothing asks again today.")
    check(
        "B a known-spent budget costs ONE attempt, not twelve",
        quota["getToken_posts"] == 1 and quota["tokens"] == [] and quota["quota_flag"] is True,
    )
    check(
        "B and it holds until the Eastern reset rather than for a backoff window",
        quota["cooldown_s"] > njt_auth.MINT_COOLDOWN_CAP_S * 20,
        f"{quota['cooldown_s']:.0f}s",
    )
    return rows


# ---------------------------------------------------------------------------
# Section C: the poll-cycle fan-out (measurement 2)
# ---------------------------------------------------------------------------


def seed_feed_cache() -> dict:
    app = main_module.app
    app.state.feed_cache = {"njt": cache_module._fresh_entry()}
    entry = app.state.feed_cache["njt"]
    entry.update(
        data=[{"id": "T1"}],
        fetched_at=1000.0,
        feed_timestamp=1000.0,
        error=None,
        systems={"njt": {"fetched_at": 1000.0, "ok": True, "retained_since": None, "routes": None}},
    )
    app.state.njt_stops = {"109": {}}
    app.state.njt_trips = {}
    app.state.njt_arrivals = {}
    app.state.njt_feed_health = None
    return entry


async def drive_feed_poller(polls: int, status: str, mint_response: tuple[int, bytes]) -> dict:
    """Run pollers._refresh_njt `polls` times with the static gate in `status`.

    THE CLOCK ADVANCES BY THE REAL CADENCE between polls, which is what makes the
    count below a claim about production rather than about a tight loop: three
    polls twenty seconds apart is one minute of a running deployment.
    """
    clock = Clock()
    cache = reset_token_cache(clock)
    fake = FakeRailData(mint=mint_response, clock=clock)
    install(fake)
    app = main_module.app
    entry = seed_feed_cache()
    app.state.njt_static_status = status
    before = cache.mint_requests
    try:
        for i in range(polls):
            if i:
                clock.advance(pollers.POLL_INTERVAL_S)
            await pollers._refresh_njt(app, client=None)
    finally:
        uninstall()
    return {
        "polls": polls,
        "status": status,
        "getToken_posts": fake.token_posts,
        "cache_delta": cache.mint_requests - before,
        "error": entry["error"],
        "cooldown_s": cache.cooldown_remaining(),
    }


async def drive_alert_poller(polls: int, mint_response: tuple[int, bytes], *, creds: bool) -> dict:
    """Run pollers._refresh_alerts `polls` times. `creds` False removes the NJT
    credentials from the environment for the duration, which is what
    feeds.alerts.active_alert_feeds reads.

    The clock advances by ALERT_POLL_INTERVAL_S between polls, as the feed poller's
    drive advances by its own cadence.
    """
    clock = Clock()
    cache = reset_token_cache(clock)
    fake = FakeRailData(mint=mint_response, clock=clock)
    install(fake)
    app = main_module.app
    saved = (os.environ.get("NJT_USERNAME"), os.environ.get("NJT_PASSWORD"))
    if not creds:
        os.environ.pop("NJT_USERNAME", None)
        os.environ.pop("NJT_PASSWORD", None)
    before = cache.mint_requests
    try:
        app.state.alerts_cache = cache_module._fresh_alerts_entry()
        async with alerts_client() as client:
            for i in range(polls):
                if i:
                    clock.advance(pollers.ALERT_POLL_INTERVAL_S)
                await pollers._refresh_alerts(app, client)
        health_keys = sorted(app.state.alerts_cache["health"])
    finally:
        uninstall()
        if saved[0] is not None:
            os.environ["NJT_USERNAME"] = saved[0]
        if saved[1] is not None:
            os.environ["NJT_PASSWORD"] = saved[1]
    return {
        "polls": polls,
        "creds": creds,
        "getToken_posts": fake.token_posts,
        "cache_delta": cache.mint_requests - before,
        "health_keys": health_keys,
    }


async def drive_warmup(max_attempts: int, mint_response: tuple[int, bytes]) -> dict:
    """Run warmups._warm_njt_static until it has made `max_attempts` retry attempts,
    recording the backoff delay it asked for after each one.

    asyncio.sleep is replaced for the duration of this drive only: the requested
    delay is recorded and the coroutine returns immediately, so the production
    retry loop runs its real schedule at full speed.

    THE RECORDED DELAY ALSO CRANKS THE CLOCK, which is what makes the mint count
    here a production number: the warmup's rungs are 15s, 30s, 60s and then 300s,
    and whether an attempt reaches getToken depends entirely on where those land
    against the cooldown windows. A drive that returned from sleep without moving
    time would measure a tight loop and report one mint for a reason that has
    nothing to do with the schedule.
    """
    clock = Clock()
    cache = reset_token_cache(clock)
    random.seed(20260905)
    fake = FakeRailData(mint=mint_response, clock=clock)
    install(fake)
    app = main_module.app
    app.state.njt_static_status = "loading"
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *args, **kwargs):
        delays.append(float(delay))
        clock.advance(float(delay))
        await real_sleep(0)

    before = cache.mint_requests
    try:
        with unittest.mock.patch.object(asyncio, "sleep", recording_sleep):
            task = asyncio.create_task(warmups._warm_njt_static(app))
            spins = 0
            # COUNTED IN ATTEMPTS, not in getToken POSTs. Before the fix the two
            # were the same number; the whole point of the measurement now is that
            # they are not, so waiting on the POSTs would wait forever.
            while len(delays) < max_attempts and spins < 100_000:
                spins += 1
                await real_sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        uninstall()
    return {
        "getToken_posts": fake.token_posts,
        "cache_delta": cache.mint_requests - before,
        "delays": delays,
        "status": app.state.njt_static_status,
        "attempts": len(delays),
        "elapsed_s": clock.now,
        "gaps": fake.gaps(),
        "post_times": list(fake.token_post_times),
    }


async def drive_warmup_to_ready() -> dict:
    """The mutual-exclusion measurement: with ONE successful static load injected,
    does the warmup task end, and does it then cost anything more?"""
    reset_token_cache()
    fake = FakeRailData(mint=(500, REAL_500))
    install(fake)
    app = main_module.app
    app.state.njt_static_status = "loading"

    async def successful_load(*_args, **_kwargs):
        return {
            "stops": {"109": {"stop_id": "109", "name": "Newark Penn", "lat": 40.7, "lon": -74.2}},
            "routes": {},
            "trips": {},
            "stop_times": {},
            "shapes": {},
            "calendar_dates": {},
        }

    try:
        with unittest.mock.patch.object(njt_static, "load_njt_static", successful_load):
            task = asyncio.create_task(warmups._warm_njt_static(app))
            await asyncio.wait_for(task, timeout=5)
        # Anything the finished task might still do would show up here.
        for _ in range(50):
            await asyncio.sleep(0)
    finally:
        uninstall()
    return {
        "done": task.done(),
        "status": app.state.njt_static_status,
        "getToken_posts": fake.token_posts,
    }


def _within(times: list[float], horizon: float) -> int:
    """How many of `times` fall in [0, horizon)."""
    return sum(1 for t in times if t < horizon)


async def simulate(
    times: list[float], mint_response: tuple[int, bytes], quota_after: int | None = None
) -> dict:
    """Replay a schedule of mint ATTEMPTS through the real TokenCache and report
    which of them reached getToken.

    THE POINT OF REPLAYING RATHER THAN CALCULATING. The cooldown ladder is a piece
    of production code with a cap, a reset and two policies; writing out what it
    "would" do is exactly the kind of arithmetic that keeps agreeing with itself
    while the code drifts. Here the real cache decides, one attempt at a time, and
    what is reported is what the fake upstream actually received.
    """
    clock = Clock()
    fake = FakeRailData(mint=mint_response, clock=clock, quota_after=quota_after)
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)

    async def failing_mint() -> str:
        return await njt_auth.mint(transport=fake, env=DIRECT_ENV, url=DIRECT_TOKEN_URL)

    for t in times:
        clock.now = t
        clock.wall = Clock.NOON_EDT + t
        try:
            await cache.get(failing_mint)
        except njt_auth.NjtAuthError:
            pass
    return {"events": len(times), "post_times": list(fake.token_post_times)}


def warmup_event_times(horizon: float) -> list[float]:
    """The warmup's attempt times over `horizon` seconds, from t=0, using the
    production rung function (jitter excluded; it is +-10% and is reported
    separately)."""
    times = [0.0]
    t = 0.0
    attempt = 0
    while True:
        t += warmups._rung(attempt)
        if t > horizon:
            return times
        times.append(t)
        attempt += 1


async def section_consumers() -> dict:
    rule("Section C: the three NJT consumers, and which of them re-mints (measurement 2)")

    poll_s = pollers.POLL_INTERVAL_S
    alert_s = pollers.ALERT_POLL_INTERVAL_S
    rungs = [warmups._rung(i) for i in range(6)]
    print()
    print("  Production cadences, read live from the modules:")
    print(f"    pollers.POLL_INTERVAL_S        = {poll_s:g}s   (the feed poller)")
    print(f"    pollers.ALERT_POLL_INTERVAL_S  = {alert_s:g}s   (the alert poller)")
    print(f"    warmups._rung(0..5)            = {[f'{r:g}' for r in rungs]}  (the static warmup)")
    print(f"    njt_auth.DAILY_MINT_LIMIT      = {njt_auth.DAILY_MINT_LIMIT}")

    # C1: the static warmup.
    warm = await drive_warmup(6, (500, REAL_500))
    print()
    print("  C1  warmups._warm_njt_static, credentials present, every mint failing")
    print(f"        retry attempts driven          : {warm['attempts']}")
    print(f"        getToken POSTs they cost       : {warm['getToken_posts']}  "
          f"(before the fix: {warm['attempts']})")
    print(f"        TOKEN_CACHE.mint_requests delta: {warm['cache_delta']}")
    print(f"        backoff delays it asked for    : "
          f"{[round(d, 2) for d in warm['delays'][:5]]}")
    print(f"        the rungs those jitter around  : {[f'{r:g}' for r in rungs[:5]]}")
    print(f"        the POSTs landed at t =        : {[round(t, 1) for t in warm['post_times']]}")
    print(f"        gaps between them              : {warm['gaps']}")
    print(f"        njt_static_status              : {warm['status']}")
    check(
        "C1 the warmup still retries, and its retries no longer all reach getToken",
        warm["attempts"] == 6 and 0 < warm["getToken_posts"] < warm["attempts"],
        f"{warm['getToken_posts']} POSTs over {warm['attempts']} attempts",
    )
    check(
        "C1 TOKEN_CACHE.mint_requests agrees with what the upstream received",
        warm["cache_delta"] == warm["getToken_posts"],
    )
    check(
        "C1 NO TWO getToken POSTs are closer than the cooldown base",
        all(gap >= njt_auth.MINT_COOLDOWN_BASE_S for gap in warm["gaps"]),
        f"gaps {warm['gaps']} against a base of {njt_auth.MINT_COOLDOWN_BASE_S:g}s",
    )
    check(
        "C1 each recorded delay is its production rung within the documented +-10% jitter",
        all(
            0.9 * rungs[i] <= warm["delays"][i] <= 1.1 * rungs[i]
            for i in range(min(len(warm["delays"]), len(rungs)))
        ),
    )

    # C2: the feed poller, both sides of the njt_static_status gate.
    ready = await drive_feed_poller(3, "ready", (500, REAL_500))
    failed = await drive_feed_poller(3, "failed", (500, REAL_500))
    loading = await drive_feed_poller(3, "loading", (500, REAL_500))
    notconf = await drive_feed_poller(3, "not-configured", (500, REAL_500))
    quota_polls = await drive_feed_poller(3, "ready", (500, QUOTA_REFUSAL))
    print()
    print("  C2  pollers._refresh_njt, three polls (one minute at a 20s cadence)")
    print("        njt_static_status   getToken POSTs   recorded /api/status detail")
    print("        " + "-" * 66)
    for row in (ready, failed, loading, notconf):
        detail = (row["error"] or {}).get("detail", "")
        print(f"        {row['status']:<18}  {row['getToken_posts']:>13}   {detail[:36]}")
    print(f"        {'ready (quota body)':<18}  {quota_polls['getToken_posts']:>13}   "
          f"{(quota_polls['error'] or {}).get('detail', '')[:36]}")
    check(
        "C2 a READY static group polls three times in a minute and mints ONCE",
        ready["getToken_posts"] == 1 and ready["cache_delta"] == 1,
        f"{ready['getToken_posts']} POSTs over {ready['polls']} polls (before the fix: 3)",
    )
    check(
        "C2 the njt_static_status gate blocks the poller entirely when not ready",
        failed["getToken_posts"] == 0
        and loading["getToken_posts"] == 0
        and notconf["getToken_posts"] == 0,
    )
    check(
        "C2 and /api/status says a cooldown rather than a credential problem",
        "cooldown" in (ready["error"] or {}).get("detail", "")
        and "rejected our credentials" not in (ready["error"] or {}).get("detail", ""),
        (ready["error"] or {}).get("detail", "")[:70],
    )
    check(
        "C2 a KNOWN spent budget costs ONE mint in three polls, and still says so",
        quota_polls["getToken_posts"] == 1
        and njt_auth.MINT_QUOTA_MESSAGE in (quota_polls["error"] or {}).get("detail", ""),
        f"{quota_polls['getToken_posts']} POSTs (before the fix: 3)",
    )
    check(
        "C2 and that refusal holds to the Eastern reset, not for a backoff window",
        (quota_polls["cooldown_s"] or 0) > njt_auth.MINT_COOLDOWN_CAP_S * 20,
        f"{(quota_polls['cooldown_s'] or 0) / 3600:.1f}h",
    )

    # C3: the alert poller, both sides of the credentials gate.
    alerts_on = await drive_alert_poller(2, (500, REAL_500), creds=True)
    alerts_off = await drive_alert_poller(2, (500, REAL_500), creds=False)
    print()
    print("  C3  pollers._refresh_alerts, two polls (two minutes at a 60s cadence)")
    print(f"        credentials present : {alerts_on['getToken_posts']} getToken POSTs   "
          f"health keys {alerts_on['health_keys']}")
    print(f"        credentials absent  : {alerts_off['getToken_posts']} getToken POSTs   "
          f"health keys {alerts_off['health_keys']}")
    check(
        "C3 the alert poller reaches getToken at most once per cooldown window",
        alerts_on["getToken_posts"] <= alerts_on["polls"]
        and alerts_on["cache_delta"] == alerts_on["getToken_posts"],
        f"{alerts_on['getToken_posts']} POSTs over {alerts_on['polls']} polls at a "
        f"{alert_s:g}s cadence against a {njt_auth.MINT_COOLDOWN_BASE_S:g}s base: this "
        "poller's cadence is exactly the base, so its second poll opens the second window",
    )
    check(
        "C3 and it is still NOT gated by njt_static_status (it reaches getToken at all)",
        alerts_on["getToken_posts"] >= 1,
    )
    check(
        "C3 absent credentials drop NJ Transit from the alert set entirely",
        alerts_off["getToken_posts"] == 0 and "njt" not in alerts_off["health_keys"],
    )

    # C4: warmup and feed poller are mutually exclusive.
    to_ready = await drive_warmup_to_ready()
    print()
    print("  C4  once the static group reaches ready the warmup task RETURNS")
    print(f"        task finished : {to_ready['done']}   status: {to_ready['status']}   "
          f"further getToken POSTs: {to_ready['getToken_posts']}")
    check(
        "C4 the warmup stops for good at ready, so it and the feed poller never overlap",
        to_ready["done"] and to_ready["status"] == "ready" and to_ready["getToken_posts"] == 0,
    )

    # C5: the two regimes, and their rates.
    rule("Section C5: attempts per poll cycle, per minute, per hour and per day")
    print()
    print("  Measured cost per call, from C1 to C3 above:")
    print("    one static warmup attempt  = 1 mint ATTEMPT (0 or 1 getToken POSTs)")
    print("    one feed poll (ready)      = 1 mint attempt")
    print("    one alert poll (creds)     = 1 mint attempt")
    print()
    print("  Two regimes exist, and they are mutually exclusive by C4:")
    print()
    print("  REGIME A, cold start: the mint fails from boot, so the static group")
    print("  never reaches ready. Contributors: the warmup (rung schedule) and the")
    print("  alert poller (60s). The feed poller contributes NOTHING (C2's gate).")
    print()
    print("  REGIME B, running process: static reached ready earlier, then the token")
    print("  path starts failing (an aged-out token at MAX_TOKEN_AGE_S, a rejected")
    print("  token, rotated credentials, or a budget already spent). Contributors:")
    print("  the feed poller (20s) and the alert poller (60s). The warmup task has")
    print("  already returned (C4), so it contributes NOTHING.")
    print()
    print("  EVERY ATTEMPT BELOW IS REPLAYED THROUGH THE REAL TokenCache on an")
    print("  injected clock, so the 'after' column is measured rather than derived.")
    print("  The 'before' column is the ATTEMPT count, which is what the old code")
    print("  turned into getToken POSTs one for one (C1 to C3 above measured that")
    print("  relationship directly, and the audit record states it).")

    day = 86400.0
    warm_times_day = warmup_event_times(day)
    alert_times_day = [k * alert_s for k in range(int(day // alert_s) + 1) if k * alert_s < day]
    poll_times_day = [k * poll_s for k in range(int(day // poll_s) + 1) if k * poll_s < day]
    regimes = {
        "regime_a": sorted(warm_times_day + alert_times_day),
        "regime_b": sorted(poll_times_day + alert_times_day),
    }

    rates: dict = {}
    for key, name in (("regime_a", "A (cold start)"), ("regime_b", "B (running process)")):
        times = regimes[key]
        sim = await simulate(times, (500, REAL_500))
        rates[key] = {
            "events": times,
            "posts": sim["post_times"],
            "min": _within(times, 60.0),
            "hour": _within(times, 3600.0),
            "day": len(times),
            "posts_min": _within(sim["post_times"], 60.0),
            "posts_hour": _within(sim["post_times"], 3600.0),
            "posts_day": len(sim["post_times"]),
        }
        r = rates[key]
        print()
        print(f"    REGIME {name}, sustained mint failure from t = 0")
        print("                        attempts   getToken POSTs")
        print("                        (before)   (after)")
        print(f"      first minute        {r['min']:>6}     {r['posts_min']:>6}")
        print(f"      first hour          {r['hour']:>6}     {r['posts_hour']:>6}")
        print(f"      first day           {r['day']:>6}     {r['posts_day']:>6}")
        print(f"      the POSTs land at t = "
              f"{[round(t) for t in r['posts'][:8]]}{' ...' if len(r['posts']) > 8 else ''}")

    # Driven through the real refreshers, so the replay above is not the only
    # witness: three real poll cycles of the real _refresh_njt, one real alert poll.
    measured_b_feed = await drive_feed_poller(3, "ready", (500, REAL_500))
    measured_b_alert = await drive_alert_poller(1, (500, REAL_500), creds=True)
    measured_a_warm = await drive_warmup(3, (500, REAL_500))
    print()
    print("    Driven through the real refreshers rather than replayed:")
    print(f"      regime A: 3 warmup attempts -> {measured_a_warm['getToken_posts']} POSTs")
    print(f"      regime B: 3 feed polls      -> {measured_b_feed['getToken_posts']} POSTs")
    print(f"                1 alert poll      -> {measured_b_alert['getToken_posts']} POSTs")
    check(
        "C5 the first minute of a sustained failure costs at most ONE attempt",
        rates["regime_a"]["posts_min"] <= 1 and rates["regime_b"]["posts_min"] <= 1,
        f"A {rates['regime_a']['posts_min']}, B {rates['regime_b']['posts_min']} "
        f"(before: {rates['regime_a']['min']} and {rates['regime_b']['min']})",
    )
    check(
        "C5 the first hour costs single digits in both regimes",
        rates["regime_a"]["posts_hour"] < 10 and rates["regime_b"]["posts_hour"] < 10,
        f"A {rates['regime_a']['posts_hour']}, B {rates['regime_b']['posts_hour']} "
        f"(before: {rates['regime_a']['hour']} and {rates['regime_b']['hour']})",
    )
    check(
        "C5 no two POSTs anywhere in a simulated day are closer than the base",
        all(
            all(
                b - a >= njt_auth.MINT_COOLDOWN_BASE_S
                for a, b in zip(r["posts"], r["posts"][1:], strict=False)
            )
            for r in rates.values()
        ),
    )
    check(
        "C5 and the driven refreshers agree with the replay",
        measured_b_feed["getToken_posts"] == 1
        and measured_b_alert["getToken_posts"] == 1
        and measured_a_warm["getToken_posts"] == 1,
        f"A warmup {measured_a_warm['getToken_posts']}, B feed "
        f"{measured_b_feed['getToken_posts']}, B alert {measured_b_alert['getToken_posts']}",
    )

    rates["poll_s"] = poll_s
    rates["alert_s"] = alert_s
    return rates


# ---------------------------------------------------------------------------
# Section D: the arithmetic against the documented cap
# ---------------------------------------------------------------------------


def readme_numbers() -> dict:
    """Read the budget the README records, rather than retyping it here."""
    text = (REPO / "README.md").read_text().splitlines()
    found = {}
    for i, line in enumerate(text, start=1):
        stripped = line.strip()
        if stripped.startswith("| Contract monitor |"):
            found["monitor"] = (i, int(stripped.split("|")[2].strip()))
        elif stripped.startswith("| Production |"):
            found["production"] = (i, int(stripped.split("|")[2].strip()))
        elif stripped.startswith("| **Committed** |"):
            found["committed"] = (i, int(stripped.split("|")[2].strip().strip("*")))
        elif "nothing anywhere retries a failed mint" in stripped:
            found["claim"] = (i, stripped)
        elif "ten a day per account" in stripped:
            found.setdefault("cap", (i, stripped))
    return found


def time_to_reach(times: list[float], target: int) -> float | None:
    """The instant the target-th mint attempt happens, given a sorted event list."""
    if len(times) < target:
        return None
    return times[target - 1]


def _clock_str(t: float | None) -> str:
    """A duration in the unit a reader can hold: seconds, minutes or hours."""
    if t is None:
        return "never"
    if t < 120:
        return f"{t:g}s"
    if t < 7200:
        return f"{t / 60:.1f}min"
    return f"{t / 3600:.1f}h"


async def section_arithmetic(rates: dict) -> dict:
    rule("Section D: the arithmetic against the documented ten-a-day cap")
    readme = readme_numbers()
    print()
    print("  What README.md records (line numbers are this checkout's):")
    for key in ("cap", "monitor", "production", "committed"):
        if key in readme:
            line_no, value = readme[key]
            shown = value if isinstance(value, int) else str(value)[:70]
            print(f"    L{line_no:<4} {key:<11}: {shown}")
    limit = njt_auth.DAILY_MINT_LIMIT
    monitor = readme["monitor"][1]
    production = readme["production"][1]
    committed = readme["committed"][1]
    spare = limit - committed
    print()
    print(f"    njt_auth.DAILY_MINT_LIMIT (production constant) = {limit}")
    print(f"    committed on a quiet day = {monitor} (contract monitor) + "
          f"{production} (production) = {committed}")
    print(f"    spare = {limit} - {committed} = {spare}")
    check(
        "D the README's own table still sums to the committed total it states",
        monitor + production == committed,
        f"{monitor} + {production} = {monitor + production}",
    )
    check(
        "D the README's committed total leaves exactly four of njt_auth's ten spare",
        spare == 4 and limit == 10,
        f"spare = {spare} of {limit}",
    )

    results = {}
    for name, key in (("A (cold start)", "regime_a"), ("B (running process)", "regime_b")):
        events, posts = rates[key]["events"], rates[key]["posts"]
        row = {
            "before_spare": time_to_reach(events, spare),
            "before_all": time_to_reach(events, limit),
            "after_spare": time_to_reach(posts, spare),
            "after_all": time_to_reach(posts, limit),
        }
        results[key] = row
        print()
        print(f"    REGIME {name}: sustained mint failure from t = 0")
        print("                                     before        after")
        print(f"      the {spare} spare attempts are gone at   "
              f"{_clock_str(row['before_spare']):>10}   {_clock_str(row['after_spare']):>10}")
        print(f"      the whole budget of {limit} is gone at  "
              f"{_clock_str(row['before_all']):>10}   {_clock_str(row['after_all']):>10}")
        print(f"      sustained rate, first hour        "
              f"{rates[key]['hour']:>7}      {rates[key]['posts_hour']:>7}")
        print(f"      sustained rate, first day         "
              f"{rates[key]['day']:>7}      {rates[key]['posts_day']:>7}")
    check(
        "D the four spare attempts now outlast the first minute in both regimes",
        all(results[k]["after_spare"] > 60 for k in ("regime_a", "regime_b")),
        f"A at {_clock_str(results['regime_a']['after_spare'])}, "
        f"B at {_clock_str(results['regime_b']['after_spare'])} "
        f"(before: {_clock_str(results['regime_a']['before_spare'])} and "
        f"{_clock_str(results['regime_b']['before_spare'])})",
    )
    check(
        "D and the whole ten outlasts the first hour, rather than the first two minutes",
        all(results[k]["after_all"] > 3600 for k in ("regime_a", "regime_b")),
        f"A at {_clock_str(results['regime_a']['after_all'])}, "
        f"B at {_clock_str(results['regime_b']['after_all'])} "
        f"(before: {_clock_str(results['regime_a']['before_all'])} and "
        f"{_clock_str(results['regime_b']['before_all'])})",
    )

    # THE END STATE, and it is the honest one. 52 a day is still more than ten, so
    # the run below lets the budget really be spent: the fake answers ordinary 500s
    # until the tenth POST and the observed daily-cap refusal after it, which is
    # what NJ Transit does. The cooldown's quota arm then latches.
    print()
    print("  WHAT A WHOLE DAY OF UNBROKEN FAILURE ACTUALLY COSTS, with the upstream")
    print(f"  refusing after the {limit}th POST the way NJ Transit does:")
    spent = await simulate(rates["regime_b"]["events"], (500, REAL_500), quota_after=limit)
    posts = spent["post_times"]
    print(f"    mint attempts made by the app          : {spent['events']}")
    print(f"    getToken POSTs that reached NJ Transit : {len(posts)}")
    print(f"    they land at t =                       : "
          f"{[_clock_str(t) for t in posts]}")
    print(f"    the {limit}th is the last ordinary failure; the next is the refusal, which")
    print("    holds to Eastern midnight, and the one after it is the single attempt")
    print("    the app is entitled to make once the budget has actually reset.")
    check(
        "D a day of unbroken failure costs the ten, the refusal, and one attempt after "
        "the reset",
        limit < len(posts) <= limit + 2,
        f"{len(posts)} POSTs across {spent['events']} attempts",
    )
    check(
        "D the ladder cannot outrun its own consequence: the day ends held, not looping",
        posts[-1] < 86400.0 and len(posts) < spent["events"] / 100,
        f"{len(posts)} POSTs is {100 * len(posts) / spent['events']:.2f}% of the attempts",
    )

    print()
    print("  STATED HONESTLY, as the reproduction stated it: the ten-a-day charging")
    print("  behavior is the README's recorded observation of 2026-09-02, not something")
    print("  this script can retest without spending mints. What is measured here is the")
    print("  ATTEMPT RATE, before and after. The budget consequence follows only if the")
    print("  recorded upstream behavior still holds, which is the same conditional the")
    print("  audit attached to it. What the fix changes is not that conditional: it is")
    print("  the rate the conditional would be applied to, from 240 an hour to 6.")
    return {"limit": limit, "spare": spare, "readme": readme, "results": results}


# ---------------------------------------------------------------------------
# Section E: the README claim the audit asked to be corrected
# ---------------------------------------------------------------------------


def section_readme_claim(arith: dict, rates: dict) -> None:
    rule("Section E: the README sentence the audit asked to be corrected")
    readme = (REPO / "README.md").read_text()
    print()
    print("  THE OLD SENTENCE: 'a single-flight cache turns concurrent callers into")
    print("  one mint, a rejected token buys exactly one re-mint per attempt, and")
    print("  nothing anywhere retries a failed mint.'")
    print()
    print("  It was true of njt_auth and false of the repository. The module's own")
    print("  docstring said so in its own words, and sections A2 and A3 measure it:")
    print("  one re-mint per attempt, one mint across five attempts at a genuine 500.")
    print("  But three CALLERS had schedules, and the audit's number was the sum of")
    print(f"  them: {rates['regime_a']['min']} attempts in the first minute of a cold start and "
          f"{rates['regime_b']['min']} in a")
    print("  running process, none of it a retry any single caller could see itself")
    print("  making. The sentence sat in a list of repository-wide conservation")
    print("  properties and the word was 'anywhere'.")
    print()
    print("  WHAT THE README SAYS NOW, and what this section checks: that the claim")
    print("  is gone as a claim, that the policy is documented in its place, and that")
    print("  the arithmetic printed there is the arithmetic measured here.")
    check(
        "E the uncorrected 'nothing anywhere retries a failed mint' claim is gone",
        "nothing anywhere retries a failed mint" not in readme,
    )
    for phrase in (
        "MINT_COOLDOWN_BASE_S",
        "MINT_COOLDOWN_CAP_S",
        "next Eastern midnight",
        "NjtMintCooldownError",
        "njt_mint_cooldown",
    ):
        check(f"E the README names {phrase}", phrase in readme)
    # The numbers in the README's before/after table are the ones measured above,
    # not a second set that could drift away from them.
    for label, value in (
        ("first minute, before", rates["regime_b"]["min"]),
        ("first minute, after", rates["regime_b"]["posts_min"]),
        ("first hour, before", rates["regime_b"]["hour"]),
        ("first hour, after", rates["regime_b"]["posts_hour"]),
        ("first day, before", rates["regime_b"]["day"]),
        ("first day, after", rates["regime_b"]["posts_day"]),
    ):
        check(
            f"E the README's {label} figure ({value}) is the one measured here",
            f"| **{value}** |" in readme or f"| {value} |" in readme,
            str(value),
        )
    print()
    print("  AND THE CALLERS STILL RETRY, which is the half that must NOT have")
    print("  changed: the cooldown is a refusal, not a circuit breaker that gives up.")
    print(f"    the warmup still walks its rungs      : {rates['regime_a']['day']} attempts a day")
    print(f"    the pollers still poll                : {rates['regime_b']['day']} attempts a day")
    shares = {
        key: rates[key]["posts_day"] / rates[key]["day"] for key in ("regime_a", "regime_b")
    }
    print(f"    what reaches NJ Transit               : "
          f"{rates['regime_a']['posts_day']} ({shares['regime_a'] * 100:.1f}% of A's attempts) "
          f"and {rates['regime_b']['posts_day']} ({shares['regime_b'] * 100:.1f}% of B's)")
    check(
        "E the consumers still retry on their own schedules (nothing was disabled)",
        rates["regime_a"]["day"] > 1000 and rates["regime_b"]["day"] > 1000,
    )
    check(
        "E and under a twentieth of those retries reaches NJ Transit",
        all(share < 0.05 for share in shares.values()),
        f"A {shares['regime_a'] * 100:.1f}%, B {shares['regime_b'] * 100:.1f}%",
    )


# ---------------------------------------------------------------------------


async def run() -> None:
    section_containment()
    await section_protections()
    await section_fanout()
    rates = await section_consumers()
    arith = await section_arithmetic(rates)
    section_readme_claim(arith, rates)
    globals()["_RATES"] = rates
    globals()["_ARITH"] = arith


def main() -> int:
    print("F05: failed NJ Transit token mints can repeat rapidly")
    print(f"repository : {REPO}")
    print("upstream   : entirely fake, in process. No NJT host, no credentials, no mint spent.")
    print("clock      : every timeline is relative to t=0 of a simulated failure")
    asyncio.run(run())

    rates = globals()["_RATES"]
    arith = globals()["_ARITH"]
    rule("Result")
    if FAILURES:
        print(f"  {len(FAILURES)} check(s) no longer match the recorded disposition:")
        for name in FAILURES:
            print(f"    - {name}")
        print()
        print("DISPOSITION MISMATCH: reality has changed since the audit record was written; "
              "re-verify F05 and update docs/reviews/audit-2026-09-05.md.")
        return 1
    print("  every check matches the recorded disposition.")
    res = arith["results"]
    a, b = rates["regime_a"], rates["regime_b"]
    print()
    print(
        "DISPOSITION: FIXED  TokenCache.get now shares a FAILED mint the way it always "
        "shared a successful one. With the mint failing, N concurrent callers make "
        "exactly ONE getToken POST for N in 1, 2, 3, 5 and 12, and the other N-1 raise "
        "NjtMintCooldownError without a request: the audit's 12 callers / 12 attempts / "
        "0 tokens is now 12 callers / 1 attempt / 0 tokens. The three consumers still "
        f"retry on their own schedules ({a['day']} attempts a day in regime A, "
        f"{b['day']} in regime B) and the cooldown decides how many of those reach NJ "
        f"Transit: {b['posts_min']} in the first minute against {b['min']} before, "
        f"{b['posts_hour']} in the first hour against {b['hour']}, {b['posts_day']} in a "
        f"day against {b['day']}. The README's four spare mints now last until "
        f"{_clock_str(res['regime_b']['after_spare'])} instead of "
        f"{_clock_str(res['regime_b']['before_spare'])} and the documented ten until "
        f"{_clock_str(res['regime_b']['after_all'])} instead of "
        f"{_clock_str(res['regime_b']['before_all'])}; a day of unbroken failure ends "
        "held rather than looping, because once the ten really are spent the quota arm "
        "latches to the next Eastern midnight. An over-age token keeps serving through a "
        "cooldown, so a failed proactive re-mint no longer takes the layer dark. The "
        "README no longer claims that nothing anywhere retries a failed mint; it "
        "documents the policy and this arithmetic instead."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
