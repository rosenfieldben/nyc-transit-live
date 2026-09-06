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

RECORDED DISPOSITION: VERIFIED. The script exits 0 while the finding still
behaves as recorded and non-zero the moment any part of it changes.
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

sys.path.insert(0, str(BACKEND))

import asyncio  # noqa: E402
import random  # noqa: E402
import unittest.mock  # noqa: E402

import httpx  # noqa: E402
from google.transit import gtfs_realtime_pb2 as pb  # noqa: E402

import cache as cache_module  # noqa: E402
import main as main_module  # noqa: E402
import njt_auth  # noqa: E402
import njt_static  # noqa: E402
import pollers  # noqa: E402
import warmups  # noqa: E402
from feeds import alerts as alerts_feed  # noqa: E402

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
    ) -> None:
        self.mint_response = mint
        self.data_response = data
        self.hold_first_mint = hold_first_mint
        self.token_posts = 0
        self.data_posts = 0

    async def __call__(self, url: str, form: dict, timeout_s: float, **_: object):
        guard(url)
        if "getToken" in url:
            self.token_posts += 1
            if self.hold_first_mint is not None and self.token_posts == 1:
                # Hold the first mint open so every other caller is provably
                # parked inside TokenCache.get while it runs.
                await self.hold_first_mint.wait()
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


def reset_token_cache() -> None:
    """Clear the process-wide cache between sections so each starts cold."""
    njt_auth.TOKEN_CACHE.invalidate(None)
    njt_auth.TOKEN_CACHE.mint_quota_refused = False


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
    print("  all exist and all work. Every one of them is on the SUCCESS or the")
    print("  classification side. None of them is a shared failure state, and none")
    print("  of them is a cooldown. Section B is what happens on the other side.")


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
    cache = njt_auth.TokenCache()
    callback_calls = 0
    entered = 0

    async def failing_mint() -> str:
        nonlocal callback_calls
        callback_calls += 1
        # The REAL mint function, reading the fake upstream. The exception below
        # is production's, not the script's.
        return await njt_auth.mint(transport=fake, env=DIRECT_ENV, url=DIRECT_TOKEN_URL)

    async def caller() -> str | None:
        nonlocal entered
        entered += 1
        try:
            return await cache.get(failing_mint)
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
    print("    N   in-flight   mint_requests   callback   getToken   cache   tokens")
    print("        at gate     at gate         calls      POSTs      .mints  obtained")
    print("    " + "-" * 68)
    for row in rows:
        gate = row["at_gate"]
        print(
            f"    {row['n']:>2}   {gate['entered']:>9}   {gate['mint_requests']:>13}   "
            f"{row['callback_calls']:>8}   {row['getToken_posts']:>8}   "
            f"{row['mints']:>6}   {len(row['tokens']):>8}"
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
            f"B N={n}: the failing mint callback ran exactly N times",
            row["callback_calls"] == n and row["getToken_posts"] == n,
            f"{row['callback_calls']} callback calls, {row['getToken_posts']} getToken POSTs",
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
    twelve = next(r for r in rows if r["n"] == 12)
    print()
    print("  THE AUDIT'S SENTENCE, RE-DERIVED: 12 concurrent callers invoked the")
    print(f"  failing mint callback {twelve['callback_calls']} times and obtained "
          f"{len(twelve['tokens'])} tokens.")
    print("  The relationship across the table is attempts == N exactly, with no")
    print("  sublinearity anywhere: the lock serializes the callers and then lets")
    print("  each one mint in turn, because the second check inside the lock only")
    print("  looks for a TOKEN and a failure leaves none behind.")
    check(
        "B the audit's headline number reproduces exactly (12 callers, 12 mints, 0 tokens)",
        twelve["callback_calls"] == 12 and twelve["getToken_posts"] == 12 and twelve["tokens"] == [],
    )
    check(
        "B the two counters agree at every burst size (no hidden attempt, no double count)",
        all(r["mint_requests"] == r["callback_calls"] == r["getToken_posts"] for r in rows),
    )
    check(
        "B attempts are exactly N, not 1 and not log N",
        [r["callback_calls"] for r in rows] == [r["n"] for r in rows],
    )

    # The worst case: the same fan-out against a budget that is ALREADY spent.
    quota = await fan_out(12, (500, QUOTA_REFUSAL))
    print()
    print("  The same burst when the account's budget is ALREADY spent:")
    print(f"    getToken POSTs sent      : {quota['getToken_posts']}")
    print(f"    tokens obtained          : {len(quota['tokens'])}")
    print(f"    cache.mint_quota_refused : {quota['quota_flag']}")
    print("    Each of those 12 is an attempt that counts against the very cap it")
    print("    just bounced off, and the flag is set only after the twelfth.")
    check(
        "B a known-spent budget produces the same 12-fold fan-out",
        quota["getToken_posts"] == 12 and quota["tokens"] == [] and quota["quota_flag"] is True,
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
    """Run pollers._refresh_njt `polls` times with the static gate in `status`."""
    reset_token_cache()
    fake = FakeRailData(mint=mint_response)
    install(fake)
    app = main_module.app
    entry = seed_feed_cache()
    app.state.njt_static_status = status
    before = njt_auth.TOKEN_CACHE.mint_requests
    try:
        for _ in range(polls):
            await pollers._refresh_njt(app, client=None)
    finally:
        uninstall()
    return {
        "polls": polls,
        "status": status,
        "getToken_posts": fake.token_posts,
        "cache_delta": njt_auth.TOKEN_CACHE.mint_requests - before,
        "error": entry["error"],
    }


async def drive_alert_poller(polls: int, mint_response: tuple[int, bytes], *, creds: bool) -> dict:
    """Run pollers._refresh_alerts `polls` times. `creds` False removes the NJT
    credentials from the environment for the duration, which is what
    feeds.alerts.active_alert_feeds reads."""
    reset_token_cache()
    fake = FakeRailData(mint=mint_response)
    install(fake)
    app = main_module.app
    saved = (os.environ.get("NJT_USERNAME"), os.environ.get("NJT_PASSWORD"))
    if not creds:
        os.environ.pop("NJT_USERNAME", None)
        os.environ.pop("NJT_PASSWORD", None)
    before = njt_auth.TOKEN_CACHE.mint_requests
    try:
        app.state.alerts_cache = cache_module._fresh_alerts_entry()
        async with alerts_client() as client:
            for _ in range(polls):
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
        "cache_delta": njt_auth.TOKEN_CACHE.mint_requests - before,
        "health_keys": health_keys,
    }


async def drive_warmup(max_attempts: int, mint_response: tuple[int, bytes]) -> dict:
    """Run warmups._warm_njt_static until it has made `max_attempts` mint attempts,
    recording the backoff delay it asked for after each one.

    asyncio.sleep is replaced for the duration of this drive only: the requested
    delay is recorded and the coroutine returns immediately, so the production
    retry loop runs its real schedule at full speed.
    """
    reset_token_cache()
    random.seed(20260905)
    fake = FakeRailData(mint=mint_response)
    install(fake)
    app = main_module.app
    app.state.njt_static_status = "loading"
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *args, **kwargs):
        delays.append(float(delay))
        await real_sleep(0)

    before = njt_auth.TOKEN_CACHE.mint_requests
    try:
        with unittest.mock.patch.object(asyncio, "sleep", recording_sleep):
            task = asyncio.create_task(warmups._warm_njt_static(app))
            spins = 0
            while fake.token_posts < max_attempts and spins < 100_000:
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
        "cache_delta": njt_auth.TOKEN_CACHE.mint_requests - before,
        "delays": delays,
        "status": app.state.njt_static_status,
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
    print(f"        getToken POSTs over 6 attempts : {warm['getToken_posts']}")
    print(f"        TOKEN_CACHE.mint_requests delta: {warm['cache_delta']}")
    print(f"        backoff delays it asked for    : "
          f"{[round(d, 2) for d in warm['delays'][:5]]}")
    print(f"        the rungs those jitter around  : {[f'{r:g}' for r in rungs[:5]]}")
    print(f"        njt_static_status              : {warm['status']}")
    check(
        "C1 the warmup retries a failed mint, one getToken POST per attempt",
        warm["getToken_posts"] == 6 and warm["cache_delta"] == 6,
        f"{warm['getToken_posts']} POSTs over 6 attempts",
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
        "C2 a READY static group lets every poll re-mint: 3 polls, 3 getToken POSTs",
        ready["getToken_posts"] == 3 and ready["cache_delta"] == 3,
    )
    check(
        "C2 the njt_static_status gate blocks the poller entirely when not ready",
        failed["getToken_posts"] == 0
        and loading["getToken_posts"] == 0
        and notconf["getToken_posts"] == 0,
    )
    check(
        "C2 a KNOWN spent budget does not slow the poller down: still 3 mints in 3 polls",
        quota_polls["getToken_posts"] == 3
        and njt_auth.MINT_QUOTA_MESSAGE in (quota_polls["error"] or {}).get("detail", ""),
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
        "C3 the alert poller re-mints on every poll, and is NOT gated by njt_static_status",
        alerts_on["getToken_posts"] == 2 and alerts_on["cache_delta"] == 2,
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
    rule("Section C5: attempts per poll cycle, per minute and per hour")
    print()
    print("  Measured cost per call, from C1 to C3 above:")
    print("    one static warmup attempt  = 1 getToken POST")
    print("    one feed poll (ready)      = 1 getToken POST")
    print("    one alert poll (creds)     = 1 getToken POST")
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

    horizon_min, horizon_hour = 60.0, 3600.0
    warm_times_min = warmup_event_times(horizon_min)
    warm_times_hour = warmup_event_times(horizon_hour)
    alert_times_min = [k * alert_s for k in range(int(horizon_min // alert_s) + 1)
                       if k * alert_s < horizon_min]
    alert_times_hour = [k * alert_s for k in range(int(horizon_hour // alert_s) + 1)
                        if k * alert_s < horizon_hour]
    poll_times_min = [k * poll_s for k in range(int(horizon_min // poll_s) + 1)
                      if k * poll_s < horizon_min]
    poll_times_hour = [k * poll_s for k in range(int(horizon_hour // poll_s) + 1)
                       if k * poll_s < horizon_hour]

    regime_a_min = len(warm_times_min) + len(alert_times_min)
    regime_a_hour = len(warm_times_hour) + len(alert_times_hour)
    regime_b_min = len(poll_times_min) + len(alert_times_min)
    regime_b_hour = len(poll_times_hour) + len(alert_times_hour)

    print()
    print("    REGIME A, first minute [0, 60):")
    print(f"      warmup attempts at t = {[f'{t:g}' for t in warm_times_min]}  "
          f"({len(warm_times_min)} attempts)")
    print(f"      alert polls  at t = {[f'{t:g}' for t in alert_times_min]}  "
          f"({len(alert_times_min)} attempts)")
    print(f"      total in the first minute = {len(warm_times_min)} + "
          f"{len(alert_times_min)} = {regime_a_min} mint attempts")
    print(f"      first hour = {len(warm_times_hour)} warmup + {len(alert_times_hour)} alert "
          f"= {regime_a_hour} mint attempts")
    print()
    print("    REGIME B, first minute [0, 60):")
    print(f"      feed polls  at t = {[f'{t:g}' for t in poll_times_min]}  "
          f"({len(poll_times_min)} attempts)")
    print(f"      alert polls at t = {[f'{t:g}' for t in alert_times_min]}  "
          f"({len(alert_times_min)} attempts)")
    print(f"      total in the first minute = {len(poll_times_min)} + "
          f"{len(alert_times_min)} = {regime_b_min} mint attempts")
    print(f"      first hour = {len(poll_times_hour)} feed + {len(alert_times_hour)} alert "
          f"= {regime_b_hour} mint attempts")

    # Drive one whole simulated cycle for regime B, so the minute above is
    # measured rather than only added up.
    measured_b_feed = await drive_feed_poller(3, "ready", (500, REAL_500))
    measured_b_alert = await drive_alert_poller(1, (500, REAL_500), creds=True)
    measured_b = measured_b_feed["getToken_posts"] + measured_b_alert["getToken_posts"]
    measured_a_warm = await drive_warmup(len(warm_times_min), (500, REAL_500))
    measured_a_alert = await drive_alert_poller(1, (500, REAL_500), creds=True)
    measured_a = measured_a_warm["getToken_posts"] + measured_a_alert["getToken_posts"]
    print()
    print("    One simulated minute, DRIVEN through the real refreshers:")
    print(f"      regime A: {measured_a_warm['getToken_posts']} warmup + "
          f"{measured_a_alert['getToken_posts']} alert = {measured_a} getToken POSTs")
    print(f"      regime B: {measured_b_feed['getToken_posts']} feed + "
          f"{measured_b_alert['getToken_posts']} alert = {measured_b} getToken POSTs")
    check(
        "C5 the driven minute matches the schedule arithmetic in both regimes",
        measured_a == regime_a_min and measured_b == regime_b_min,
        f"A {measured_a} vs {regime_a_min}, B {measured_b} vs {regime_b_min}",
    )
    check(
        "C5 a sustained mint failure costs at least four attempts in its first minute",
        regime_a_min >= 4 and regime_b_min >= 4,
    )

    return {
        "poll_s": poll_s,
        "alert_s": alert_s,
        "regime_a": {
            "min": regime_a_min,
            "hour": regime_a_hour,
            "times": sorted(warm_times_hour + alert_times_hour),
        },
        "regime_b": {
            "min": regime_b_min,
            "hour": regime_b_hour,
            "times": sorted(poll_times_hour + alert_times_hour),
        },
    }


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


def section_arithmetic(rates: dict) -> dict:
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
        times = rates[key]["times"]
        t_spare = time_to_reach(times, spare)
        t_all = time_to_reach(times, limit)
        results[key] = (t_spare, t_all)
        print()
        print(f"    REGIME {name}: sustained mint failure from t = 0")
        print(f"      the {spare} spare attempts are gone at t = {t_spare:g}s "
              f"({t_spare / 60:.2f} minutes)")
        print(f"      the whole budget of {limit} is gone at t = {t_all:g}s "
              f"({t_all / 60:.2f} minutes)")
        print(f"      sustained rate: {rates[key]['min']} attempts in the first minute, "
              f"{rates[key]['hour']} in the first hour")
        print(f"      which is {rates[key]['hour'] / limit:.1f} times the whole daily "
              f"budget, every hour")
    a_spare, a_all = results["regime_a"]
    b_spare, b_all = results["regime_b"]
    check(
        "D four spare attempts are consumed inside the first minute in both regimes",
        a_spare < 60 and b_spare < 60,
        f"A at {a_spare:g}s, B at {b_spare:g}s",
    )
    check(
        "D the whole ten-a-day budget is consumed within five minutes in both regimes",
        a_all <= 300 and b_all <= 300,
        f"A at {a_all:g}s, B at {b_all:g}s",
    )
    print()
    print("  STATED HONESTLY: the ten-a-day charging behavior is the README's recorded")
    print("  observation of 2026-09-02, not something this script can retest without")
    print("  spending mints. What is measured here is the ATTEMPT RATE. The budget")
    print("  consequence follows only if the recorded upstream behavior still holds,")
    print("  which is exactly the conditional the audit itself attached to it.")
    print("  The gating is real and is stated: in regime A the feed poller makes zero")
    print("  attempts, and in regime B the warmup makes zero. Neither regime has all")
    print("  three consumers minting at once, and the rate is still 4 a minute.")
    return {"limit": limit, "spare": spare, "readme": readme, "results": results}


# ---------------------------------------------------------------------------
# Section E: the README claim the audit asks to be corrected
# ---------------------------------------------------------------------------


def section_readme_claim(arith: dict, rates: dict) -> None:
    rule("Section E: the README's 'nothing anywhere retries a failed mint'")
    line_no, line = arith["readme"]["claim"]
    print()
    print(f"  README.md L{line_no}, quoted:")
    print(f"    {line}")
    print()
    print("  Measured against that sentence:")
    print("    njt_auth ITSELF does not retry. Its module docstring says so in its own")
    print("    words: 'It never retries on a schedule ... the CALLER's schedule (the")
    print("    C-era warmup rungs) decides when to try again.' Sections A2 and A3 are")
    print("    the measurements: one re-mint per attempt, one mint across five")
    print("    attempts at a genuine 500.")
    print("    THE CALLERS DO RETRY, and there are three of them:")
    print(f"      warmups._warm_njt_static : measured 6 attempts on the rung schedule "
          f"(C1)")
    print(f"      pollers._refresh_njt     : measured 3 attempts in 3 polls when the "
          f"static group is ready (C2)")
    print(f"      pollers._refresh_alerts  : measured 2 attempts in 2 polls whenever "
          f"credentials exist (C3)")
    print(f"    So a failed mint is retried indefinitely: {rates['regime_a']['min']} attempts "
          f"in the first minute and {rates['regime_a']['hour']} in the first hour")
    print(f"    in regime A, {rates['regime_b']['min']} and {rates['regime_b']['hour']} "
          f"in regime B.")
    print()
    print("  The sentence is true of the MODULE and false of the REPOSITORY. It reads")
    print("  as a repository-wide guarantee, sitting in a list of repository-wide")
    print("  conservation properties ('a single-flight cache turns concurrent callers")
    print("  into one mint, a rejected token buys exactly one re-mint per attempt, and")
    print("  nothing anywhere retries a failed mint'), and the word is 'anywhere'.")
    print("  The audit's request to correct it is upheld.")
    check(
        "E the README still carries the uncorrected 'nothing anywhere retries' claim",
        "nothing anywhere retries a failed mint" in line,
    )
    check(
        "E and at least two independent callers measurably do retry a failed mint",
        rates["regime_a"]["min"] >= 2 and rates["regime_b"]["min"] >= 2,
    )


# ---------------------------------------------------------------------------


async def run() -> None:
    section_containment()
    await section_protections()
    await section_fanout()
    rates = await section_consumers()
    arith = section_arithmetic(rates)
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
    a_spare, a_all = arith["results"]["regime_a"]
    b_spare, b_all = arith["results"]["regime_b"]
    print()
    print(
        "DISPOSITION: VERIFIED  TokenCache.get shares a successful token (12 concurrent "
        f"callers, 1 mint) but shares no failure: with the mint failing, N concurrent "
        f"callers make exactly N getToken POSTs for N in 1, 2, 3, 5 and 12, so the audit's "
        f"12 callers / 12 attempts / 0 tokens reproduces byte for byte, on both counters. "
        f"Nothing cools down afterwards: the static warmup retries on its rung schedule, "
        f"the alert poller re-mints every {rates['alert_s']:g}s whenever credentials exist, "
        f"and the feed poller re-mints every {rates['poll_s']:g}s once njt_static_status is "
        f"ready (it is gated off before that, and the warmup stops once it is ready, so the "
        f"two never overlap). That is {rates['regime_a']['min']} attempts in the first "
        f"minute of a cold-start failure and {rates['regime_b']['min']} in a running "
        f"process, {rates['regime_a']['hour']} and {rates['regime_b']['hour']} an hour, "
        f"which spends the README's four spare mints by t = {b_spare:g}s (regime B; "
        f"{a_spare:g}s in regime A) and the whole documented ten by t = {b_all:g}s "
        f"({a_all:g}s in regime A) if the recorded ten-a-day charging still holds; "
        f"a known quota refusal slows nothing down. The README's "
        "'nothing anywhere retries a failed mint' is true of njt_auth and false of the "
        "repository."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
