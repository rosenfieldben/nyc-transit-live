"""Tests for the NJ Transit token door (backend/njt_auth.py).

No test here touches the network: the transport is injected everywhere, and the
absent-credentials tests inject a transport that FAILS THE TEST IF CALLED, which
is how "no credentials means no network" becomes a proved property rather than a
comment.

Four things are pinned, in the order they can hurt:

  1. THE SNIFF, BOTH WAYS. is_auth_error must be true for exactly the probe's
     shape (HTTP 500 with {"errorMessage":"Invalid token."}) and false for
     everything else, including a genuine 500. A false positive spends a mint
     out of ten a day (njt_auth.DAILY_MINT_LIMIT, observed 2026-09-02, of which 6
     are committed on a quiet day); a false negative costs one retry on the
     caller's schedule. The mutation check in the 15a handoff loosens this to
     "any 500" and the control tests below are what must go red.
  2. MINT CONSERVATION UNDER CONCURRENCY. N callers finding an empty cache
     together produce exactly ONE mint.
  3. ONE RE-MINT, THEN THE ATTEMPT FAILS. Never a loop, whatever the upstream
     keeps saying, and since Audit 6 the re-mint is for an expired token: a fresh
     one (never accepted, minted within FRESH_TOKEN_S) buys none, whoever minted it.
  4. ABSENT CREDENTIALS REACH NO SOCKET, from either entry point.

Audit 4 added two more, both consequences of the secret riding in a body:

  5. A CREDENTIALED POST NEVER FOLLOWS A REDIRECT (F2). Proved against a real
     httpx client over a mock transport that answers 307 to a different origin and
     records every request: exactly one arrives, at the original URL.
  6. THE getToken BODY IS NEVER QUOTED (F3). Every test carries a canary of the
     token's own shape in the body and asserts it appears NOWHERE in the message,
     because on those paths the body may be the live token. The pre-Audit-4 test
     asserted the opposite and was inverted, not kept.

And one more, which is the reason the cap has a number at all now:

  7. THE DAILY MINT BUDGET (observed 2026-09-02). Ten mints per account per Eastern
     day, every attempt counted. The refusal is another HTTP 500, so it needs its
     own exact-shape sniff beside is_auth_error's, and the message it produces is a
     CONSTANT: matching the body against a known phrase is not quoting it, so the
     canary rides in the refusal body here too and must still appear nowhere. The
     rest is deliberately unremarkable, and that is asserted rather than assumed:
     a spent budget is a mint failure like any other, with no extra retry and no
     effect on any other feed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

import njt_auth

pytestmark = pytest.mark.anyio

URL = "https://njt.example/api/GTFSRT/getGTFS"
TOKEN_URL = "https://njt.example/api/GTFSRT/getToken"
ENV = {njt_auth.USERNAME_VAR: "rider", njt_auth.PASSWORD_VAR: "secret"}

# The probe's exact invalid-token response, byte for byte.
INVALID_TOKEN = b'{"errorMessage":"Invalid token."}'
# Same status, different body: a genuine NJ Transit fault. THE CONTROL.
REAL_500 = b'{"errorMessage":"An unexpected error occurred."}'

# A canary of the token's own shape (the probe recorded ~21 characters) that rides
# in a getToken body. The F3 tests assert it appears NOWHERE in a message, which
# is the same assertion as "the live token never reaches a log".
CANARY = "canary-tok-0123456789"

# The daily-cap refusal, observed 2026-09-02: an HTTP 500 whose errorMessage BEGINS
# "Daily usage limit". The tail carries the canary deliberately. The tail is exactly
# the part the app must never repeat, and a body shaped like this is the F3 hazard
# in its most tempting form: the app now READS this body, so "reads it" and "quotes
# it" have to be visibly different things.
QUOTA_REFUSAL = json.dumps(
    {"errorMessage": f"Daily usage limit of 10 reached. Token {CANARY} was the last."}
).encode()

# Where a hostile redirect would send the body. A different origin from every URL
# above, so a followed redirect is a request whose URL names this host.
ELSEWHERE = "https://elsewhere.example/collect"


class RecordingTransport:
    """An injected transport that scripts responses and records every request.

    `mint_responses` and `data_responses` are consumed one per call, the last one
    repeating forever, so a test says "reject, then accept" without arithmetic.
    """

    def __init__(self, mint_responses=None, data_responses=None, mint_delay_s: float = 0.0):
        self.mint_responses = list(mint_responses or [(200, json.dumps({"UserToken": "t1"}))])
        self.data_responses = list(data_responses or [(200, "zip-bytes")])
        self.mint_delay_s = mint_delay_s
        self.calls: list[tuple[str, dict]] = []

    @property
    def mints(self) -> int:
        return sum(1 for url, _form in self.calls if url == TOKEN_URL)

    @property
    def data_calls(self) -> list[dict]:
        return [form for url, form in self.calls if url != TOKEN_URL]

    @staticmethod
    def _take(queue):
        return queue[0] if len(queue) == 1 else queue.pop(0)

    async def __call__(self, url: str, form: dict, timeout_s: float):
        self.calls.append((url, dict(form)))
        if url == TOKEN_URL:
            if self.mint_delay_s:
                # A real await, so a concurrency test can have several callers
                # genuinely inside the mint window at once rather than relying on
                # a scheduling accident.
                await asyncio.sleep(self.mint_delay_s)
            status, body = self._take(self.mint_responses)
        else:
            status, body = self._take(self.data_responses)
        return status, body.encode() if isinstance(body, str) else body


def _explodes(*_args, **_kwargs):
    """A transport that fails the test if it is ever awaited. THE assertion in the
    absent-credentials tests: proving a request was not made is stronger than
    counting requests, because a counter can only see the calls that reached it."""

    async def transport(url, form, timeout_s):
        raise AssertionError(f"the transport must never be reached, but was called with {url!r}")

    return transport


# ---------------------------------------------------------------------------
# 1. The sniff, both directions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        INVALID_TOKEN,
        b'{"errorMessage": "Invalid token."}',  # whitespace after the colon
        b'{"errorMessage":"  Invalid token.  "}',  # padded value, stripped before compare
        b'{"errorMessage":"Invalid token.","detail":"ignored"}',  # extra keys are fine
        # CASE IS IGNORED, and it is the one place narrowness buys nothing: the same
        # rejection typed differently is still the rejection, and a genuine NJT 500
        # carries an entirely different sentence rather than this one recased.
        b'{"errorMessage":"Invalid Token."}',
        b'{"errorMessage":"INVALID TOKEN."}',
        b'{"errorMessage":"invalid token."}',
    ],
)
def test_the_probe_shape_is_an_auth_error(body):
    assert njt_auth.is_auth_error(500, body) is True


@pytest.mark.parametrize(
    ("status", "body", "why"),
    [
        (500, REAL_500, "a genuine NJT 500: same status, different message"),
        (500, b"", "a 500 with no body at all"),
        (500, b"<html><body>502 Bad Gateway</body></html>", "an HTML error page under a 500"),
        (500, b'"Invalid token."', "the right words, but a bare JSON string, not the object"),
        (500, b'{"errorMessage":"Invalid token"}', "no trailing period: not the probed body"),
        (500, b'{"errorMessage":"Token expired."}', "a different message entirely"),
        (500, b'{"errorMessage":null}', "errorMessage present but not a string"),
        (500, b'["Invalid token."]', "a JSON array, not an object"),
        (401, INVALID_TOKEN, "the right body under 401: NJT does not do this, so neither do we"),
        (403, INVALID_TOKEN, "the right body under 403, same reasoning"),
        # A redirect is never an auth failure, whatever body rides under it: the
        # transports never follow one (F2), so a 3xx reaches the sniff as-is, and
        # reading it as invalid-token would spend the one re-mint on a redirect.
        (307, INVALID_TOKEN, "a 307 is a redirect, never an auth failure"),
        (308, INVALID_TOKEN, "a 308 is a redirect, never an auth failure"),
        (302, b"", "a 302 with no body is a redirect, never an auth failure"),
        (200, INVALID_TOKEN, "a 200 is never an auth error whatever it says"),
        (503, b"", "an ordinary upstream outage"),
    ],
)
def test_everything_else_is_not_an_auth_error(status, body, why):
    """THE CONTROL SIDE, and the one the mutation check targets.

    Loosening is_auth_error to `status == 500` passes every test in the block
    above and fails the first two cases here, which is exactly the point: the
    positive cases alone cannot tell a correct sniff from a reckless one.
    """
    assert njt_auth.is_auth_error(status, body) is False, why


# ---------------------------------------------------------------------------
# 2. Mint conservation
# ---------------------------------------------------------------------------


async def test_concurrent_callers_share_exactly_one_mint():
    """Twenty callers, one cold cache, ONE mint.

    The delay is what makes this a real test rather than a scheduling accident:
    every caller is genuinely inside the mint window when the others arrive, so a
    cache without the single-flight lock (or with the lock but without the second
    check inside it) mints twenty times here.
    """
    transport = RecordingTransport(mint_delay_s=0.02)
    cache = njt_auth.TokenCache()
    results = await asyncio.gather(
        *(
            njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
            for _ in range(20)
        )
    )
    assert results == [b"zip-bytes"] * 20
    assert transport.mints == 1, f"20 concurrent callers minted {transport.mints} times"
    assert cache.mints == 1
    # Every data request carried the one token, as a FORM FIELD (never a header:
    # the probe showed the header form is ignored).
    assert {form["token"] for form in transport.data_calls} == {"t1"}


async def test_a_warm_cache_never_mints_again():
    transport = RecordingTransport()
    cache = njt_auth.TokenCache()
    for _ in range(5):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert transport.mints == 1


async def test_invalidate_only_clears_the_token_that_failed():
    """The compare-and-clear, which is what keeps concurrent re-mints honest.

    Caller A can meet an invalid-token response for a token another caller has
    already replaced. A blind clear would throw away the fresh one and force a
    third mint; this is the unit-level statement of that.
    """
    cache = njt_auth.TokenCache()
    await cache.get(_fixed("t1"))
    cache.invalidate("t1")
    assert cache.peek() is None

    await cache.get(_fixed("t2"))
    cache.invalidate("t1")  # a stale caller, arriving late
    assert cache.peek() == "t2", "invalidating a superseded token must not clear the live one"


def _fixed(token: str):
    async def mint():
        return token

    return mint


# --- the age ceiling, which is what makes a false negative recoverable ------


def test_the_age_ceiling_is_the_budget_line_it_claims_to_be():
    """THE CONSTANT PINNED, because it is now a budget line rather than a taste.

    Twelve hours is TWO mints a day per process, and the arithmetic at
    DAILY_MINT_LIMIT depends on that number being what it says: the contract
    monitor's 4 plus this 2 is 6 of the 10, leaving 4 spare for deploys, manual
    dispatches, fixture pulls and real expiries. At six hours this line was 4, the
    budget was 8 committed with 2 spare, and a deploy on the same day as a fixture
    pull took NJ Transit dark for riders.

    WHY LENGTHENING IT IS NOT A GAMBLE ON THE TOKEN'S LIFETIME, which is still
    undocumented and still may not be assumed. This ceiling is not what carries an
    expiry: a token that dies is recognised by is_auth_error and replaced inside the
    same attempt for exactly one extra mint, which the two tests below and the
    contract tier's three-consumer scenario measure. So the trade is at most one
    REACTIVE mint per real expiry against two PROACTIVE mints saved every day.

    The assertion is the arithmetic rather than the number alone, so a future change
    to either constant has to face the budget rather than just this literal.
    """
    assert njt_auth.MAX_TOKEN_AGE_S == 12 * 3600.0
    mints_a_day = 24 * 3600.0 / njt_auth.MAX_TOKEN_AGE_S
    assert mints_a_day == 2
    monitor_mints_a_day = 4  # 6-hourly, one shared token per run
    spare = njt_auth.DAILY_MINT_LIMIT - monitor_mints_a_day - mints_a_day
    assert spare >= 4, (
        f"a deploy, a dispatch, a fixture pull and a real expiry can all land on one "
        f"day, and only {spare} of DAILY_MINT_LIMIT are left for them"
    )


# A fixed Eastern instant every cooldown test starts from: 2026-09-05 12:00:00 EDT,
# a plain summer noon with twelve hours to run before the mint budget resets. Named
# as a datetime rather than typed as a number so the arithmetic in the tests below
# ("twelve hours to midnight") can be read off it, and so the fall-back case can be
# written by changing the date alone.
EASTERN = ZoneInfo("America/New_York")
NOON_EDT = datetime(2026, 9, 5, 12, 0, tzinfo=EASTERN).timestamp()
TWELVE_HOURS_S = 12 * 3600.0


class _Clock:
    """A hand-cranked pair of clocks, so a ceiling is tested by advancing time
    rather than by waiting twelve hours and a quota hold by naming a midnight
    rather than by waiting for one.

    BOTH ADVANCE TOGETHER, which is what a real process does and what makes the
    tests below honest: a cooldown's remaining time is measured on the monotonic
    one, and the wall clock is only ever asked how far away the next Eastern
    midnight is and which Eastern date an attempt belongs to.
    """

    def __init__(self, wall: float = NOON_EDT):
        self.now = 1000.0
        self.wall = wall

    def __call__(self):
        return self.now

    def wall_clock(self):
        return self.wall

    def advance(self, seconds):
        self.now += seconds
        self.wall += seconds


async def test_a_token_is_reused_until_it_reaches_the_age_ceiling():
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[
            (200, json.dumps({"UserToken": "first"})),
            (200, json.dumps({"UserToken": "second"})),
        ]
    )
    cache = njt_auth.TokenCache(clock=clock)
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)

    await njt_auth.njt_post(URL, **kwargs)
    clock.advance(njt_auth.MAX_TOKEN_AGE_S - 1)
    await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 1, "a token under the ceiling must be reused"

    clock.advance(1)  # exactly at the ceiling: over-age, so treated as absent
    await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 2
    assert [form["token"] for form in transport.data_calls] == ["first", "first", "second"]


async def test_a_rejection_the_sniff_misses_recovers_at_the_ceiling():
    """THE REASON THE CEILING EXISTS, stated as the failure it closes.

    is_auth_error matches one exact response shape, so a rejection that drifts (a
    reworded message, a changed punctuation) is a false negative. The module prices
    that as "costs one failed attempt; the caller's rung schedule tries again", and
    without a ceiling that was simply untrue: the non-auth path returns without
    invalidating, nothing else expires a token, and the warmup loop then re-posts
    the SAME dead token forever. Terminal until the process restarted.

    Here the upstream rejects any token but the third one, with a body the sniff
    does NOT recognise. Attempts before the ceiling all fail on the same token; the
    attempt after it mints and succeeds. That is the documented price being true.
    """
    clock = _Clock()
    drifted = '{"errorMessage":"Token is no longer valid."}'  # not the probed body

    class Upstream:
        def __init__(self):
            self.mints = 0
            self.posts = []

        async def __call__(self, url, form, timeout_s):
            if url == TOKEN_URL:
                self.mints += 1
                return 200, json.dumps({"UserToken": f"t{self.mints}"}).encode()
            self.posts.append(form["token"])
            if form["token"] == "t2":
                return 200, b"zip-bytes"
            return 500, drifted.encode()

    upstream = Upstream()
    cache = njt_auth.TokenCache(clock=clock)
    kwargs = dict(cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL)

    # Three attempts inside the ceiling: all fail, all on the same dead token, and
    # crucially the sniff never fires so nothing re-mints.
    for _ in range(3):
        with pytest.raises(njt_auth.NjtUpstreamError):
            await njt_auth.njt_post(URL, **kwargs)
        clock.advance(60)
    assert upstream.mints == 1
    assert upstream.posts == ["t1", "t1", "t1"]

    # Past the ceiling the cached token is treated as absent and the next attempt
    # mints a fresh one, which the upstream accepts.
    clock.advance(njt_auth.MAX_TOKEN_AGE_S)
    assert await njt_auth.njt_post(URL, **kwargs) == b"zip-bytes"
    assert upstream.mints == 2


async def test_the_ceiling_does_not_widen_the_sniff():
    """The ceiling must not become a second way to spend mints on an outage. A
    genuine 500, repeated inside the ceiling, still costs exactly one mint."""
    clock = _Clock()
    transport = RecordingTransport(data_responses=[(500, REAL_500)])
    cache = njt_auth.TokenCache(clock=clock)
    for _ in range(5):
        with pytest.raises(njt_auth.NjtUpstreamError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
        clock.advance(60)
    assert transport.mints == 1


async def test_mint_requests_counts_posts_sent_not_tokens_issued():
    """THE COUNTER THAT MATTERS FOR THE RATE CAP. A failed mint raises before the
    token is stored, so it never advances `mints` -- and a failed mint is exactly the
    traffic that spends the ten-a-day cap. Asserting conservation on `mints` alone
    is blind to a loop that mints unsuccessfully forever.

    THE CLOCK IS CRANKED PAST EACH COOLDOWN because since F05 that is the only way
    to send four getToken POSTs at all: four attempts made back to back cost one,
    which is the test immediately below. Here the windows are stepped over on
    purpose, so the counter is measured against traffic that really left."""
    clock = _Clock()
    transport = RecordingTransport(mint_responses=[(503, "upstream down")])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    for _ in range(4):
        with pytest.raises(njt_auth.NjtAuthError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
        clock.advance(njt_auth.MINT_COOLDOWN_CAP_S)
    assert cache.mints == 0, "no token was ever issued"
    assert cache.mint_requests == 4, "but four getToken POSTs were really sent"
    assert transport.mints == 4


# ---------------------------------------------------------------------------
# 3. One re-mint, then the attempt fails
# ---------------------------------------------------------------------------


async def test_an_expired_token_is_replaced_once_and_the_request_succeeds():
    """ORDINARY EXPIRY: a token held from an earlier attempt, accepted then, is
    refused now. That is the one case a re-mint can cure, so it buys exactly one."""
    transport = RecordingTransport(
        mint_responses=[
            (200, json.dumps({"UserToken": "dead"})),
            (200, json.dumps({"UserToken": "live"})),
        ],
        data_responses=[(200, "warm"), (500, INVALID_TOKEN), (200, "zip-bytes")],
    )
    cache = njt_auth.TokenCache()
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert await njt_auth.njt_post(URL, **kwargs) == b"warm"
    assert await njt_auth.njt_post(URL, **kwargs) == b"zip-bytes"
    assert transport.mints == 2, "one cold mint plus exactly one replacement"
    assert [form["token"] for form in transport.data_calls] == ["dead", "dead", "live"]


async def test_one_remint_then_the_attempt_fails():
    """The upstream keeps saying invalid-token to a held token and to its
    replacement. We must stop after ONE re-mint.

    A retry LOOP here would keep minting for as long as the upstream keeps
    rejecting, which is precisely how an integration exhausts a rate limit it
    cannot measure. The absence of a loop in njt_post is the enforcement; this is
    the proof.
    """
    transport = RecordingTransport(
        mint_responses=[
            (200, json.dumps({"UserToken": "t1"})),
            (200, json.dumps({"UserToken": "t2"})),
        ],
        data_responses=[(200, "warm"), (500, INVALID_TOKEN)],  # then refused forever
    )
    cache = njt_auth.TokenCache()
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert await njt_auth.njt_post(URL, **kwargs) == b"warm"
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 2, f"the held token's one re-mint and no more, got {transport.mints}"
    assert [form["token"] for form in transport.data_calls] == ["t1", "t1", "t2"], (
        "one original request plus exactly one retry"
    )
    assert "rejected a freshly minted token" in str(excinfo.value)


async def test_a_token_minted_within_the_attempt_is_refused_without_a_second_mint():
    """THE FUTILE RE-MINT (Audit 6, A01). The token this attempt first posts was
    minted by this attempt, so NJ Transit refusing it says the next one would be
    refused the same way: a re-mint would spend a getToken POST to learn nothing.
    The refusal goes straight to reject_fresh, so the attempt costs one mint, the
    token is dropped and the ladder's base holds the next caller."""
    clock = _Clock()
    transport = RecordingTransport(data_responses=[(500, INVALID_TOKEN)])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert transport.mints == 1, f"no second mint for a token minted here, got {transport.mints}"
    assert len(transport.data_calls) == 1
    assert "rejected a freshly minted token" in str(excinfo.value)
    assert cache.peek() is None
    assert cache.cooldown_remaining() == 600.0


async def test_a_token_minted_at_the_age_ceiling_is_refused_without_a_second_mint():
    """The same rule when a token WAS held: past the age ceiling the attempt mints
    its replacement before posting, so the token it posts is fresh from this attempt,
    and its refusal buys no second mint either."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[
            (200, json.dumps({"UserToken": "old"})),
            (200, json.dumps({"UserToken": "new"})),
        ],
        data_responses=[(200, "warm"), (500, INVALID_TOKEN)],
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert await njt_auth.njt_post(URL, **kwargs) == b"warm"
    clock.advance(njt_auth.MAX_TOKEN_AGE_S + 1)
    with pytest.raises(njt_auth.NjtAuthError):
        await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 2, "the cold mint and the ceiling's, and no third"
    assert [form["token"] for form in transport.data_calls] == ["old", "new"]
    assert cache.cooldown_remaining() == 600.0


async def test_a_re_issued_token_string_is_still_minted_within_the_attempt():
    """WHAT MAKES A TOKEN FRESH IS THE MINT, NOT THE STRING. If getToken answers the
    age ceiling's re-mint with the very string it issued before, the token posted is
    still one this attempt minted, and its refusal buys no second mint. Told apart by
    comparing strings, it would pass for the held token and buy one."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": "same"}))],
        data_responses=[(200, "warm"), (500, INVALID_TOKEN)],
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert await njt_auth.njt_post(URL, **kwargs) == b"warm"
    clock.advance(njt_auth.MAX_TOKEN_AGE_S + 1)
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 2, f"the cold mint and the ceiling's, got {transport.mints}"
    assert len(transport.data_calls) == 2
    assert "rejected a freshly minted token" in str(excinfo.value)
    assert cache.peek() is None
    assert cache.cooldown_remaining() == 600.0


async def test_a_token_never_accepted_is_fresh_whoever_minted_it():
    """FRESH MEANS NEVER ACCEPTED, NOT MINTED BY THIS CALL. One attempt mints a token
    and meets a genuine 500, so NJ Transit never answers that token with a 200; the
    next attempt is handed it from the cache and is refused. Nothing about the token
    has been proved, so its refusal is a fresh token refused: no re-mint, the ladder's
    base. Read as held because this attempt did not mint it, it bought a second
    getToken POST for one probe of the fault."""
    clock = _Clock()
    transport = RecordingTransport(data_responses=[(500, REAL_500), (500, INVALID_TOKEN)])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    with pytest.raises(njt_auth.NjtUpstreamError):
        await njt_auth.njt_post(URL, **kwargs)
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 1, f"one mint for the one token, got {transport.mints}"
    assert len(transport.data_calls) == 2
    assert "rejected a freshly minted token" in str(excinfo.value)
    assert cache.cooldown_remaining() == 600.0


async def test_a_refusal_landing_late_does_not_step_the_ladder_twice():
    """TWO CONSUMERS, ONE FRESH TOKEN, REFUSED OUT OF ORDER. The first mints and its
    data POST is slow; the second is handed the same token from the cache and is
    refused first. That refusal is the fault's one probe: the token is dropped and
    the base hold starts. The minter's refusal landing after it changes nothing. Had
    the second consumer read the token as held, it would have re-minted, and the
    minter's refusal landing during that mint would have stepped the ladder to 1200
    s for one fault."""
    clock = _Clock()
    release_first = asyncio.Event()
    minted: list[str] = []
    data_seen: list[str] = []

    async def upstream(url, form, timeout_s):
        if url == TOKEN_URL:
            minted.append(f"fresh-{len(minted) + 1}")
            return 200, json.dumps({"UserToken": minted[-1]}).encode()
        data_seen.append(form["token"])
        if len(data_seen) == 1:
            await release_first.wait()
        return 500, INVALID_TOKEN

    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    kwargs = dict(cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL)
    first = asyncio.create_task(njt_auth.njt_post(URL, **kwargs))
    for _ in range(5):
        await asyncio.sleep(0)
    assert minted == ["fresh-1"] and data_seen == ["fresh-1"], "the minter's POST is out"

    with pytest.raises(njt_auth.NjtAuthError) as second:
        await njt_auth.njt_post(URL, **kwargs)
    assert "rejected a freshly minted token" in str(second.value)
    assert cache.cooldown_remaining() == 600.0

    release_first.set()
    with pytest.raises(njt_auth.NjtAuthError):
        await first
    assert minted == ["fresh-1"], f"one mint for one fault, got {minted}"
    assert data_seen == ["fresh-1", "fresh-1"]
    assert cache.cooldown_remaining() == 600.0, "the late refusal stepped nothing"


async def test_a_caller_queued_behind_a_mint_is_handed_a_fresh_token():
    """THE WAITER'S HALF. Two consumers find the cache cold together; one mints, the
    other queues on the lock and is handed what that mint returned. The waiter's
    data POST is the quicker, so its refusal lands first: it minted nothing, and the
    token is still fresh, so it re-mints nothing. Then the minter's refusal lands on
    a token the waiter has already reported, still fresh, and steps nothing. One
    getToken POST, one base hold."""
    clock = _Clock()
    minted: list[str] = []
    data_seen: list[str] = []
    refused_in_order: list[int] = []

    async def upstream(url, form, timeout_s):
        if url == TOKEN_URL:
            await asyncio.sleep(0.02)  # long enough for the second caller to queue
            minted.append(f"fresh-{len(minted) + 1}")
            return 200, json.dumps({"UserToken": minted[-1]}).encode()
        position = len(data_seen)
        data_seen.append(form["token"])
        await asyncio.sleep(0.05 if position == 0 else 0.005)
        refused_in_order.append(position)
        return 500, INVALID_TOKEN

    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    results = await asyncio.gather(
        *(
            njt_auth.njt_post(URL, cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL)
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert refused_in_order == [1, 0], "the waiter's refusal landed first"
    assert minted == ["fresh-1"], f"the waiter re-minted: {minted}"
    assert data_seen == ["fresh-1", "fresh-1"]
    assert all(
        isinstance(r, njt_auth.NjtAuthError) and not isinstance(r, njt_auth.NjtMintCooldownError)
        for r in results
    ), results
    assert cache.cooldown_remaining() == 600.0, "one step for one fault"


def test_fresh_lasts_two_minutes_from_the_mint():
    """THE WINDOW, PINNED. Four requests at REQUEST_TIMEOUT_S is the longest one call
    can last, so a token minted by this attempt or one beside it is inside it."""
    assert njt_auth.FRESH_TOKEN_S == 120.0


async def test_a_token_is_fresh_until_accepted_or_two_minutes_old():
    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    await cache.get(_fixed("T"))
    clock.advance(119)
    assert cache.is_fresh("T")
    assert not cache.is_fresh("other")
    cache.reject_fresh("T", njt_auth.NjtAuthError("refused"))
    assert cache.is_fresh("T"), "a refusal does not end it"
    clock.advance(1)
    assert not cache.is_fresh("T"), "two minutes does"

    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    await cache.get(_fixed("T"))
    cache.confirm("an older token")
    assert cache.is_fresh("T"), "a 200 on any other token leaves it fresh"
    clock.wall += 3600
    assert cache.is_fresh("T"), "and the window runs on the monotonic clock, not the wall"
    cache.confirm("T")
    assert not cache.is_fresh("T"), "a 200 on it ends it"


async def test_a_token_hours_old_and_never_accepted_buys_its_re_mint():
    """FRESH IS BOUNDED IN TIME. A token minted into an NJ Transit outage of genuine
    500s is never accepted; hours later NJ Transit recovers and refuses it, as it may
    well have expired. That is not a fresh token refused, and a re-mint for it is a
    probe rather than a repeat: one getToken POST, and the request succeeds, rather
    than a hold of ten minutes after the upstream came back."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[
            (200, json.dumps({"UserToken": "old"})),
            (200, json.dumps({"UserToken": "new"})),
        ],
        data_responses=[(500, REAL_500), (500, INVALID_TOKEN), (200, "zip-bytes")],
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    with pytest.raises(njt_auth.NjtUpstreamError):
        await njt_auth.njt_post(URL, **kwargs)
    clock.advance(3 * 3600)
    assert await njt_auth.njt_post(URL, **kwargs) == b"zip-bytes"
    assert transport.mints == 2
    assert [form["token"] for form in transport.data_calls] == ["old", "old", "new"]
    assert cache.cooldown_remaining() is None


async def test_an_over_age_token_never_accepted_steps_the_ladder_once():
    """ONE FAULT, ONE STEP. A token never accepted is past the age ceiling; its
    proactive re-mint fails (a step to 600 s) and the token is served through the
    hold; NJ Transit refuses it. It is not fresh, so the refusal is expiry: the
    re-mint it asks for meets the hold, and nothing steps the ladder a second time."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": "old"})), (503, "upstream down")],
        data_responses=[(500, REAL_500), (500, INVALID_TOKEN)],
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    kwargs = dict(cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    with pytest.raises(njt_auth.NjtUpstreamError):
        await njt_auth.njt_post(URL, **kwargs)
    clock.advance(njt_auth.MAX_TOKEN_AGE_S + 1)
    with pytest.raises(njt_auth.NjtMintCooldownError):
        await njt_auth.njt_post(URL, **kwargs)
    assert transport.mints == 2, "the cold mint and the ceiling's failed one"
    assert transport.data_calls[-1]["token"] == "old"
    assert cache.cooldown_remaining() == 600.0, "one step, not two"


async def test_a_real_500_never_mints_twice_and_fails_the_attempt():
    """THE CONTROL, at the unit boundary. Same status as the case above, different
    body, and the behavior must differ completely: no re-mint, no retry, and an
    NjtUpstreamError rather than an NjtAuthError so a caller can tell them apart."""
    transport = RecordingTransport(data_responses=[(500, REAL_500)])
    with pytest.raises(njt_auth.NjtUpstreamError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    assert transport.mints == 1, "a real 500 must not provoke a mint"
    assert len(transport.data_calls) == 1, "a real 500 must not provoke a retry either"
    # The body is quoted, which is the only place NJ Transit says what went wrong.
    assert "unexpected error" in str(excinfo.value)


async def test_a_failed_mint_fails_the_attempt_and_never_quotes_the_body():
    """INVERTED by Audit 4 (F3). This test used to assert the 401 body WAS quoted
    into the message, on the reasoning that the body is the upstream's words. For
    getToken that premise is false: the body may be the token, and a non-200 does
    not change what NJ Transit might put in it. So the message names the status and
    nothing else, and the canary must appear nowhere."""
    transport = RecordingTransport(mint_responses=[(401, json.dumps({"errorMessage": CANARY}))])
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    message = str(excinfo.value)
    assert "HTTP 401" in message
    assert CANARY not in message, "the getToken body must never reach a message"
    assert transport.data_calls == [], "a failed mint must not be followed by a data request"


# ---------------------------------------------------------------------------
# 6. The getToken body is never quoted (Audit 4, F3)
# ---------------------------------------------------------------------------


def test_the_canary_has_the_tokens_shape():
    """If the canary stopped looking like a token, the tests below would still pass
    while proving less; the probe recorded ~21 characters."""
    assert len(CANARY) == 21


@pytest.mark.parametrize(
    ("status", "body", "why"),
    [
        (
            200,
            json.dumps({"accessToken": CANARY}),
            "the token under a key extract_token does not recognize",
        ),
        (200, CANARY, "the token as a bare string that is not JSON"),
        (503, json.dumps({"errorMessage": CANARY}), "a non-200 whose body carries the token"),
    ],
)
async def test_the_gettoken_body_never_reaches_a_mint_failure_message(status, body, why):
    """The three shapes in which a getToken body would carry the live token into a
    message. Each one used to be quoted; each one is now described without content,
    and this is asserted at the mint boundary, where the log line is built."""
    transport = RecordingTransport(mint_responses=[(status, body)])
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.mint(transport=transport, env=ENV, url=TOKEN_URL)
    assert CANARY not in str(excinfo.value), why
    assert transport.mints == 1, "a failed mint is never retried"


def test_an_unrecognized_key_is_reported_by_name_and_never_by_value():
    """The names are what an operator needs to extend the accepted spellings; the
    values are where the token would be."""
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        njt_auth.extract_token(json.dumps({"accessToken": CANARY, "expires": 3600}).encode())
    message = str(excinfo.value)
    assert "carried no token" in message
    assert "accessToken" in message and "expires" in message
    assert CANARY not in message
    assert "3600" not in message, "values are never quoted, not even harmless ones"


def test_a_non_json_body_is_reported_by_length_and_never_by_content():
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        njt_auth.extract_token(CANARY.encode())
    message = str(excinfo.value)
    assert "non-JSON body" in message
    assert f"{len(CANARY)} bytes" in message
    assert CANARY not in message


def test_key_names_are_bounded_so_an_absurd_object_cannot_become_a_log_entry():
    payload = {f"key{i:04d}": CANARY for i in range(200)}
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        njt_auth.extract_token(json.dumps(payload).encode())
    message = str(excinfo.value)
    assert CANARY not in message
    assert len(message) < 400
    assert "..." in message


@pytest.mark.parametrize(
    ("body", "shape"),
    [
        (b"[]", "a JSON array of 0 items"),
        (b'["' + CANARY.encode() + b'"]', "a JSON array of 1 items"),
        (b'""', "an empty JSON string"),
        (b"null", "JSON null"),
        (b"42", "a JSON int"),
        (b"{}", "an empty JSON object"),
    ],
)
def test_every_other_json_shape_is_named_without_content(body, shape):
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        njt_auth.extract_token(body)
    assert shape in str(excinfo.value)
    assert CANARY not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5. A credentialed POST never follows a redirect (Audit 4, F2)
# ---------------------------------------------------------------------------


def _redirecting_transport():
    """A real httpx transport that answers every request with a 307 to a different
    origin and records every request it receives. "The body never went there" is
    then a count and a URL rather than an absence of evidence: a client that
    followed would show a second request whose URL names ELSEWHERE."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(307, headers={"Location": ELSEWHERE})

    return httpx.MockTransport(handler), seen


async def test_a_credentialed_post_never_follows_a_redirect():
    """The default transport, through a real httpx client, sends exactly ONE
    request when the upstream answers 307, at the original URL, and hands the 307
    back as a status. The body carried the password, which is what was at stake."""
    transport, seen = _redirecting_transport()
    status, _body = await njt_auth._httpx_post(
        TOKEN_URL, {"username": "rider", "password": CANARY}, 5.0, transport=transport
    )
    assert status == 307
    assert [str(request.url) for request in seen] == [TOKEN_URL], (
        "exactly one request, at the original URL: a second one would be the body "
        "delivered to whatever host the Location named"
    )
    assert CANARY.encode() in seen[0].content, "the request under test really carried the secret"


async def test_a_redirected_mint_fails_the_attempt_on_the_status_alone():
    """The whole path: the real transport under mint. One request, a NjtAuthError
    naming the 307, no credential and no body in the message."""
    transport, seen = _redirecting_transport()

    async def post(url, form, timeout_s):
        return await njt_auth._httpx_post(url, form, timeout_s, transport=transport)

    env = {njt_auth.USERNAME_VAR: "rider", njt_auth.PASSWORD_VAR: CANARY}
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.mint(transport=post, env=env, url=TOKEN_URL)
    message = str(excinfo.value)
    assert "HTTP 307" in message
    assert CANARY not in message and ELSEWHERE not in message
    assert [str(request.url) for request in seen] == [TOKEN_URL]
    assert CANARY.encode() in seen[0].content, "the one request carried the secret; no other did"


async def test_a_redirect_on_a_data_request_is_an_upstream_error_and_never_mints():
    """A 3xx on a token-bearing data POST is a non-200 like any other: the attempt
    fails as NjtUpstreamError, and the one re-mint is not spent on it."""
    transport = RecordingTransport(data_responses=[(307, "")])
    with pytest.raises(njt_auth.NjtUpstreamError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    assert "HTTP 307" in str(excinfo.value)
    assert transport.mints == 1
    assert len(transport.data_calls) == 1


async def test_a_mint_transport_failure_names_the_type_and_not_the_credentials():
    """A mint POSTs the username and password in its body, so its failure message
    must never be built from the exception's own str (which embeds the request).
    Type only, matching cache._sanitize_upstream's discipline one module over."""

    async def transport(url, form, timeout_s):
        raise ConnectionResetError(f"connection reset while sending {form}")

    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    message = str(excinfo.value)
    assert "ConnectionResetError" in message
    assert "secret" not in message and "rider" not in message


async def test_a_transport_failure_on_the_data_request_is_an_upstream_error():
    async def transport(url, form, timeout_s):
        if url == TOKEN_URL:
            return 200, json.dumps({"UserToken": "t1"}).encode()
        raise TimeoutError("no answer")

    with pytest.raises(njt_auth.NjtUpstreamError):
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )


async def test_a_non_200_data_response_raises_without_minting_again():
    transport = RecordingTransport(data_responses=[(404, "no such endpoint")])
    with pytest.raises(njt_auth.NjtUpstreamError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    assert "404" in str(excinfo.value)
    assert transport.mints == 1


# ---------------------------------------------------------------------------
# 4. Absent credentials reach no socket
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ({}, "nothing set at all"),
        ({njt_auth.USERNAME_VAR: "rider"}, "username without password"),
        ({njt_auth.PASSWORD_VAR: "secret"}, "password without username"),
        ({njt_auth.USERNAME_VAR: "  ", njt_auth.PASSWORD_VAR: "secret"}, "whitespace username"),
        (
            {njt_auth.USERNAME_VAR: "your-njt-username", njt_auth.PASSWORD_VAR: "x"},
            "a .env.example copied but never edited",
        ),
        (
            # THE REALISTIC HALF-EDIT: the real registered username is pasted over
            # the first line and the second is left as shipped. That used to read as
            # configured, skip the not-configured short circuit, and put the app in
            # a retry loop posting a doomed mint on every rung.
            {njt_auth.USERNAME_VAR: "realuser", njt_auth.PASSWORD_VAR: "your-njt-password"},
            "the password left at its shipped placeholder",
        ),
    ],
)
async def test_absent_credentials_never_reach_the_transport(env, why):
    with pytest.raises(njt_auth.NjtNotConfigured):
        await njt_auth.njt_post(
            URL,
            cache=njt_auth.TokenCache(),
            transport=_explodes(),
            env=env,
            token_url=TOKEN_URL,
        )
    assert njt_auth.is_configured(env) is False, why


async def test_mint_itself_refuses_without_credentials():
    """The guard is on BOTH entry points. njt_post checks first, and mint checks
    again: mint is public (the monitor and the fixture generator call it through
    the cache), so a caller that never went through njt_post must not be able to
    POST credentials that are not there."""
    with pytest.raises(njt_auth.NjtNotConfigured):
        await njt_auth.mint(transport=_explodes(), env={}, url=TOKEN_URL)


def test_configured_credentials_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(njt_auth.USERNAME_VAR, "rider")
    monkeypatch.setenv(njt_auth.PASSWORD_VAR, "secret")
    assert njt_auth.is_configured() is True
    assert njt_auth.credentials() == ("rider", "secret")
    monkeypatch.delenv(njt_auth.USERNAME_VAR)
    assert njt_auth.is_configured() is False


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b'{"UserToken":"abc123"}',
        b'{"userToken":"abc123"}',
        b'{"token":"abc123"}',
        b'{"Token":" abc123 "}',
        b'"abc123"',
    ],
)
def test_extract_token_accepts_the_spellings_the_probe_left_open(body):
    """The probe recorded the token's SHAPE (~21 chars) but not the key holding it,
    so the extraction accepts the documented spellings rather than pinning one that
    was never observed. Deliberately tolerant HERE and nowhere else: a wrong guess
    would make every request fail on a field name, while tolerance costs nothing."""
    assert njt_auth.extract_token(body) == "abc123"


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json at all",
        b"{}",
        b'{"UserToken":""}',
        b'{"UserToken":null}',
        b'{"expires":3600}',
        b"[]",
    ],
)
def test_extract_token_rejects_a_response_carrying_no_token(body):
    with pytest.raises(njt_auth.NjtAuthError):
        njt_auth.extract_token(body)


# ---------------------------------------------------------------------------
# 7. The daily mint budget (observed 2026-09-02)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (QUOTA_REFUSAL, "the observed refusal, canary tail and all"),
        (b'{"errorMessage":"Daily usage limit"}', "the prefix alone, with no tail"),
        (b'{"errorMessage":"  Daily usage limit exceeded.  "}', "padded, stripped before compare"),
        (b'{"errorMessage":"DAILY USAGE LIMIT EXCEEDED"}', "shouted"),
        (b'{"errorMessage":"daily usage limit exceeded"}', "lowercased"),
        (b'{"errorMessage":"Daily usage limit.","detail":"x"}', "extra keys are fine"),
    ],
)
def test_the_refusal_shape_is_a_quota_error(body, why):
    """A PREFIX, not a sentence, and the parametrization is the reason. Only the
    first three words were observed; the tail varies and may be reworded upstream at
    any time. Pinning the whole sentence would turn this into a false negative on
    NJ Transit's next copy edit, and a false negative here is a spent budget
    reported as an anonymous HTTP 500 again."""
    assert njt_auth.is_mint_quota_error(500, body) is True, why


@pytest.mark.parametrize(
    ("status", "body", "why"),
    [
        (500, REAL_500, "a genuine NJT 500: THE CONTROL"),
        (500, INVALID_TOKEN, "the OTHER 500 this module knows; the two must not overlap"),
        (500, b"", "a 500 with no body at all"),
        (500, b"<html>Daily usage limit</html>", "the words in an HTML page, not JSON"),
        (500, b'"Daily usage limit exceeded."', "the right words, but a bare JSON string"),
        (500, b'["Daily usage limit exceeded."]', "a JSON array, not an object"),
        (500, b'{"errorMessage":null}', "errorMessage present but not a string"),
        (
            500,
            b'{"errorMessage":"Request refused: daily usage limit exceeded."}',
            "the phrase is present but does not BEGIN the message, so this is some "
            "other refusal that happens to mention the cap",
        ),
        (200, QUOTA_REFUSAL, "a 200 is never a refusal whatever it says"),
        (429, QUOTA_REFUSAL, "the obvious status for a rate limit is NOT the one NJT uses"),
        (503, QUOTA_REFUSAL, "an ordinary outage carrying the same body"),
        (307, QUOTA_REFUSAL, "a redirect is never a quota refusal (F2 keeps 3xx unfollowed)"),
    ],
)
def test_everything_else_is_not_a_quota_error(status, body, why):
    """THE CONTROL SIDE, and it carries a burden the invalid-token controls do not:
    NJ Transit answers a dead token, a real fault AND a spent budget with the same
    status code, so these two sniffs run over the same 500s and must never both be
    true. Loosening this one to `status == 500` reports every NJ Transit outage as a
    spent budget on /healthz, which is a wrong answer for whoever is on call."""
    assert njt_auth.is_mint_quota_error(status, body) is False, why


def test_the_two_sniffs_never_agree_about_the_same_500():
    """Stated once as its own claim rather than left implicit in the two tables
    above, because it is the property that keeps them separable: each body is one
    thing, and a response that reads as both would mean the app re-mints (spending
    a mint) on a refusal that exists because there are no mints left."""
    for body in (INVALID_TOKEN, QUOTA_REFUSAL, REAL_500):
        assert not (njt_auth.is_auth_error(500, body) and njt_auth.is_mint_quota_error(500, body))
    assert njt_auth.is_auth_error(500, INVALID_TOKEN) is True
    assert njt_auth.is_mint_quota_error(500, QUOTA_REFUSAL) is True


async def test_a_refused_mint_raises_the_fixed_string_and_nothing_from_the_body():
    """THE F3 CLAIM ON THE ONE PATH THAT READS THE BODY. is_mint_quota_error is the
    only thing in the module allowed to look at a getToken response, and what comes
    back out is a bool; the message is njt_auth's own constant. So the assertion is
    EQUALITY, not containment: nothing was appended, nothing was interpolated, and
    the canary sitting in the refusal's tail has nowhere to travel.

    The mutation this kills is the tempting one: reporting the body because "the
    upstream is the only thing that knows why". For getToken that premise is false
    at any status, which is what Audit 4 established."""
    transport = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL)])
    with pytest.raises(njt_auth.NjtMintQuotaError) as excinfo:
        await njt_auth.mint(transport=transport, env=ENV, url=TOKEN_URL)
    assert str(excinfo.value) == njt_auth.MINT_QUOTA_MESSAGE
    assert CANARY not in str(excinfo.value), "the getToken body must never reach a message"
    assert transport.mints == 1, "a refused mint is never retried"


async def test_a_refused_mint_is_a_mint_failure_like_any_other():
    """THE 'OTHERWISE NOTHING CHANGES' CLAIM, asserted rather than assumed.

    NjtMintQuotaError is a SUBCLASS of NjtAuthError, so every caller that already
    handles a failed mint keeps handling this one with no new arm: the warmup's rung
    schedule, the poller's NjtAuthError branch, njt_static's lenient empty result.
    And njt_post does what it does for any failed mint: no data request goes out, no
    second getToken follows, and the attempt is over. Retrying harder is the one
    real mistake available on this path, because every attempt is charged to the
    very budget it would be waiting on."""
    transport = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL)])
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    assert isinstance(excinfo.value, njt_auth.NjtMintQuotaError)
    assert transport.mints == 1, "a refused mint must not provoke a second getToken"
    assert transport.data_calls == [], "and no data request follows a mint that failed"


async def test_repeated_attempts_never_retry_into_the_cap():
    """The caller's schedule is what tries again, and since F05 only the FIRST of
    those tries reaches NJ Transit. Four attempts, ONE getToken POST.

    THIS TEST USED TO ASSERT FOUR, and the change is the finding. "Each attempt
    costs exactly one getToken, never eight" was true of njt_post and beside the
    point: nothing bounded the number of ATTEMPTS, so four callers cost four POSTs
    against a budget of ten a day and the refusal we had already been handed
    throttled nothing at all. The absence of a loop in njt_post is still what stops
    an attempt costing two; the cooldown is what stops the fifth attempt costing a
    fifth. Made with the refusal that makes repetition most expensive, because
    every one of these is charged to the very budget it is waiting on.
    """
    clock = _Clock()
    transport = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL)])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    errors = []
    for _ in range(4):
        with pytest.raises(njt_auth.NjtAuthError) as excinfo:
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
        errors.append(excinfo.value)
    assert transport.mints == 1, "one refusal is enough; the other three never left"
    assert cache.mint_requests == 1
    assert cache.mints == 0, "no token was ever issued"
    assert isinstance(errors[0], njt_auth.NjtMintQuotaError), "the first one really asked"
    assert all(isinstance(exc, njt_auth.NjtMintCooldownError) for exc in errors[1:]), (
        "and the rest were refused here, without a request"
    )
    assert all(njt_auth.MINT_QUOTA_MESSAGE in str(exc) for exc in errors), (
        "every one of them still says what NJ Transit said, so the surface reads the same"
    )


async def test_the_cache_records_the_refusal_and_the_next_good_mint_clears_it():
    """WHAT /healthz READS. The flag says one thing only: the most recent mint
    attempt was refused for the cap. It lives on the cache because the cache is what
    every mint in the app goes through, so it cannot be stale the way a flag
    somebody remembered to set could be.

    THE CLEARING IS THE HALF THAT MATTERS AFTER MIDNIGHT. The first mint that
    succeeds after the Eastern reset clears the flag as a side effect of working,
    so /healthz stops publishing the code without anything having to read a
    calendar to decide to stop.

    SINCE F05 THERE IS DATE ARITHMETIC, and it is in the other half. The flag still
    clears itself; what the cooldown added is a reason not to keep ASKING before
    then, so this walks the clock to the reset rather than retrying immediately."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[(500, QUOTA_REFUSAL), (200, json.dumps({"UserToken": "t1"}))]
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    assert cache.mint_quota_refused is False, "a fresh cache has not been refused anything"
    with pytest.raises(njt_auth.NjtMintQuotaError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert cache.mint_quota_refused is True
    clock.advance(TWELVE_HOURS_S + njt_auth.MINT_QUOTA_RESET_MARGIN_S)
    body = await njt_auth.njt_post(
        URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
    )
    assert body == b"zip-bytes"
    assert cache.mint_quota_refused is False, "the mint that worked cleared it"


async def test_an_ordinary_mint_failure_clears_the_flag_too():
    """A LINGERING FLAG WOULD BE A LIE, and this is the shape that would produce
    one: the cap is met at 23:50, the next attempt after midnight fails for some
    entirely different reason, and /healthz would keep reporting a spent budget
    through an actual outage. The flag tracks the MOST RECENT attempt, not the most
    recent refusal.

    THE SHAPE IS NOW LITERALLY THE ONE THE DOCSTRING DESCRIBES: the refusal is met
    before midnight and the unrelated failure lands after it, because since F05 the
    second attempt cannot happen until the hold lapses. That is the same story with
    the clock made explicit rather than a different one."""
    clock = _Clock()
    transport = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL), (503, "upstream down")])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    with pytest.raises(njt_auth.NjtMintQuotaError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert cache.mint_quota_refused is True
    clock.advance(TWELVE_HOURS_S + njt_auth.MINT_QUOTA_RESET_MARGIN_S)
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert not isinstance(excinfo.value, njt_auth.NjtMintQuotaError)
    assert cache.mint_quota_refused is False


async def test_a_cancelled_mint_leaves_the_flag_exactly_as_it_was():
    """A cancellation is this process giving up (a shutdown, an attempt deadline),
    not NJ Transit answering, so it must not clear a refusal that really happened.
    It does space the next mint, since Audit 6 (A01):
    test_a_cancelled_mint_spaces_the_next_one_like_a_failed_one."""

    async def cancelling(url, form, timeout_s):
        raise asyncio.CancelledError

    cache = njt_auth.TokenCache()
    cache.mint_quota_refused = True
    with pytest.raises(asyncio.CancelledError):
        await njt_auth.njt_post(
            URL, cache=cache, transport=cancelling, env=ENV, token_url=TOKEN_URL
        )
    assert cache.mint_quota_refused is True


# ---------------------------------------------------------------------------
# 8. The mint cooldown (Audit 5, F05)
# ---------------------------------------------------------------------------
#
# THE FINDING, as it was measured: a failed mint left nothing behind but the quota
# flag, so the lock shared a success and shared no failure. Twelve concurrent
# callers made twelve getToken POSTs and got zero tokens, and every poll after them
# made another, four in the first minute and 240 in the first hour against a budget
# of ten a day. Every test below is one clause of the fix, and each is written so
# that removing the clause it names turns it red on its own.


async def test_n_concurrent_callers_share_one_failed_mint():
    """THE HEADLINE. However many callers meet a cold cache while the mint is
    failing, exactly ONE getToken POST leaves the process.

    The delay is what makes it a real test rather than a scheduling accident: every
    caller is genuinely inside the mint window when the others arrive, which is the
    same construction test_concurrent_callers_share_exactly_one_mint uses on the
    success path. Before F05 this asserted N and the audit's number was 12.

    THE CALLERS DO NOT ALL RAISE THE SAME THING, and that is correct rather than
    untidy: one of them really asked NJ Transit and gets NJ Transit's answer, and
    the rest are refused here. Both are NjtAuthError, so no caller anywhere needs
    to know which one it got.
    """
    for n in (2, 3, 12):
        clock = _Clock()
        transport = RecordingTransport(mint_responses=[(503, "upstream down")], mint_delay_s=0.02)
        cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
        results = await asyncio.gather(
            *(
                njt_auth.njt_post(
                    URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
                )
                for _ in range(n)
            ),
            return_exceptions=True,
        )
        assert transport.mints == 1, f"{n} concurrent callers sent {transport.mints} getToken POSTs"
        assert cache.mint_requests == 1
        assert cache.mints == 0, "and none of them got a token"
        assert all(isinstance(r, njt_auth.NjtAuthError) for r in results), (
            "every caller must see a failure its existing handler already catches"
        )
        cooled = [r for r in results if isinstance(r, njt_auth.NjtMintCooldownError)]
        assert len(cooled) == n - 1, (
            "exactly one caller owns the real attempt; the rest are refused without one"
        )
        assert all(c.remaining_s > 0 for c in cooled)


async def test_three_polling_consumers_across_a_window_make_no_further_attempts():
    """THE OTHER HALF OF THE FINDING, and the half that actually spent the budget.
    The concurrent burst is one moment; the polls are forever.

    Driven at the real cadences: the feed poller every POLL_INTERVAL_S, the alert
    poller every ALERT_POLL_INTERVAL_S and the static warmup on its own rungs, all
    three through njt_post against one shared cache, for a full hour of simulated
    time. Before F05 that was 240 getToken POSTs; since A01 the ladder walks 600,
    1200, 1800, so three POSTs leave in the hour, at 0, 600 and 1800 s, and the
    fourth at 3600 s is the next hour's.

    Over two hours the two rules part, so both are asserted, each on its own cache,
    and neither can hide behind the other: the ladder alone sends a fifth at 5400 s,
    and the shipped cache stops at DAILY_MINT_ALLOWANCE.
    """
    shipped = await _poll_against_a_failing_mint(njt_auth.TokenCache, 3600)
    assert shipped == 3, f"the shipped cache spent {shipped} getToken POSTs in the hour"
    shipped = await _poll_against_a_failing_mint(njt_auth.TokenCache, 7200)
    assert shipped == njt_auth.DAILY_MINT_ALLOWANCE == 4, (
        f"the shipped cache spent {shipped} getToken POSTs in two hours"
    )
    ladder_alone = await _poll_against_a_failing_mint(
        lambda **clocks: njt_auth.TokenCache(daily_allowance=10_000, **clocks), 7200
    )
    # 0, 600, 1800, 3600, 5400; 7200 is past the window.
    assert ladder_alone == 5, f"the ladder alone spent {ladder_alone} in two hours"


async def _poll_against_a_failing_mint(make_cache, seconds: int) -> int:
    """F05's hour, or longer, driven at the real cadences against a mint that
    always fails: the getToken POSTs it costs, from a cache built by `make_cache`."""
    clock = _Clock()
    transport = RecordingTransport(mint_responses=[(503, "upstream down")])
    cache = make_cache(clock=clock, wall_clock=clock.wall_clock)

    # The real cadences, read from the module that owns them rather than retyped,
    # so a cadence change shows up here as a changed cost. main first: pollers
    # imports it, so importing pollers on its own is the circular direction.
    import main  # noqa: F401
    import pollers

    # NOT deduplicated: two consumers polling at the same instant are two separate
    # get() calls, and the minute they share is exactly where the old code minted
    # twice. 180 feed polls and 60 alert polls is the audit's 240 an hour.
    schedule = sorted(
        list(range(0, seconds, int(pollers.POLL_INTERVAL_S)))
        + list(range(0, seconds, int(pollers.ALERT_POLL_INTERVAL_S)))
    )
    attempts = 0
    for t in schedule:
        clock.now = 1000.0 + t
        clock.wall = NOON_EDT + t
        with pytest.raises(njt_auth.NjtAuthError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
        attempts += 1

    assert attempts == len(schedule) >= 240 * seconds // 3600, (
        "the consumers really did keep polling"
    )
    assert cache.mint_requests == transport.mints
    return transport.mints


async def test_the_backoff_doubles_and_then_stops_at_the_cap():
    """THE LADDER. Each consecutive non-quota failure waits twice as long as the
    last, up to MINT_COOLDOWN_CAP_S and no further.

    THE CAP IS NOT DECORATION. Doubling with no ceiling parks the next attempt a
    day out within a few hours, so a five-minute NJ Transit fault would leave the
    layer dark long after the upstream came back. The cap is the worst residual
    darkness after a real outage ends, which is why it is half an hour and not
    longer.
    """
    clock = _Clock()
    transport = RecordingTransport(mint_responses=[(503, "upstream down")])
    # The allowance lifted: ten rungs spend ten getToken POSTs in one Eastern day,
    # and the allowance would hold the fifth to midnight. That interplay is pinned in
    # section 10; this is the ladder's own shape.
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=100)

    waits = []
    for _ in range(10):
        with pytest.raises(njt_auth.NjtAuthError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
        waits.append(cache.cooldown_remaining())
        clock.advance(waits[-1])

    assert waits[:3] == [600.0, 1200.0, 1800.0], waits
    assert waits[2:] == [1800.0] * 8, "and it stays there rather than doubling past it"
    base, cap = njt_auth.MINT_COOLDOWN_BASE_S, njt_auth.MINT_COOLDOWN_CAP_S
    assert cap < 3600, "the cap must be short enough that an outage heals the hour it ends"
    assert base > 20, "and the base longer than a feed poll, or the cooldown changes nothing"


async def test_a_quota_refusal_holds_past_the_cap_and_releases_at_eastern_midnight():
    """THE OTHER POLICY. A spent budget is not a fault to back off from: NJ Transit
    has told us the answer for the rest of the Eastern day, so the hold runs to the
    reset rather than to a guess, and it outlasts the backoff cap many times over.

    THE MIDNIGHT IS THE INJECTED CLOCK'S, never the wall's. This test would pass at
    any hour of any real day, in any real timezone, because the only calendar in it
    is the timestamp _Clock was constructed with.
    """
    clock = _Clock()  # 2026-09-05 12:00 EDT: twelve hours to run.
    transport = RecordingTransport(
        mint_responses=[(500, QUOTA_REFUSAL), (200, json.dumps({"UserToken": "fresh"}))]
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    with pytest.raises(njt_auth.NjtMintQuotaError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)

    held = cache.cooldown_remaining()
    assert held == TWELVE_HOURS_S + njt_auth.MINT_QUOTA_RESET_MARGIN_S, (
        "noon to the next Eastern midnight, plus the margin that keeps a retry from "
        f"landing a second early; got {held}"
    )
    assert held > njt_auth.MINT_COOLDOWN_CAP_S * 20, "a quota hold is not the backoff ladder"

    # One second short of the reset: still held, and still without a request.
    clock.advance(held - 1)
    with pytest.raises(njt_auth.NjtMintCooldownError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert transport.mints == 1, "nothing asked again while the budget was known to be spent"

    # Past it, and the next attempt goes out.
    clock.advance(2)
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    assert transport.mints == 2
    assert cache.mint_quota_refused is False


async def test_a_quota_hold_across_a_dst_boundary_is_the_length_of_that_day():
    """The zone does the arithmetic, not a hardcoded 86400, and both DST days cost
    something in a different direction. The November day is twenty-five hours, and a
    fixed day would release an hour early into a budget that has not reset yet,
    spending a mint to learn the same refusal again. The March day is twenty-three,
    and a fixed day would hold an hour past a reset that already happened."""
    midnight_before = datetime(2026, 11, 1, 0, 0, tzinfo=EASTERN).timestamp()
    held = njt_auth.seconds_to_next_eastern_midnight(midnight_before)
    assert held == 25 * 3600.0 + njt_auth.MINT_QUOTA_RESET_MARGIN_S, held
    # The March day is the other direction and costs the other way: a fixed 86400
    # would hold an hour PAST the reset, which is an hour of avoidable darkness.
    spring = njt_auth.seconds_to_next_eastern_midnight(
        datetime(2026, 3, 8, 0, 0, tzinfo=EASTERN).timestamp()
    )
    assert spring == 23 * 3600.0 + njt_auth.MINT_QUOTA_RESET_MARGIN_S, spring
    # And an ordinary day is twenty-four, so the two above are the exceptions.
    ordinary = njt_auth.seconds_to_next_eastern_midnight(
        datetime(2026, 9, 5, 0, 0, tzinfo=EASTERN).timestamp()
    )
    assert ordinary == 24 * 3600.0 + njt_auth.MINT_QUOTA_RESET_MARGIN_S, ordinary


async def test_an_accepted_token_resets_the_backoff():
    """The ladder measures CONSECUTIVE failures. A token NJ Transit accepts (the 200
    njt_post confirms) puts the next failure back at the base rather than resuming
    where a long-healed outage left off, which is what keeps a flaky week from
    arriving at the cap and staying there. A mint alone no longer does (Audit 6,
    A01): see test_the_streak_ends_on_acceptance_not_on_issue."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[
            (503, "upstream down"),
            (503, "upstream down"),
            (200, json.dumps({"UserToken": "healed"})),
            (503, "upstream down"),
        ],
        data_responses=[(200, "zip-bytes")],
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)

    async def attempt():
        with pytest.raises(njt_auth.NjtAuthError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
        held = cache.cooldown_remaining()
        clock.advance(held)
        return held

    assert await attempt() == njt_auth.MINT_COOLDOWN_BASE_S
    assert await attempt() == njt_auth.MINT_COOLDOWN_BASE_S * 2

    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    assert cache.cooldown_remaining() is None, "a success ends the window it is in"

    # The token this success cached would answer the next call, and an over-age one
    # would too (that is the test below), so drop it outright: the point here is the
    # LADDER's state and nothing else. invalidate(None) is the unconditional clear
    # no request path uses, which is why a test is the right place for it.
    cache.invalidate(None)
    assert await attempt() == njt_auth.MINT_COOLDOWN_BASE_S, "back to the base, not to four"


async def test_an_over_age_token_outlives_a_failed_re_mint():
    """THE CLAUSE THAT KEEPS THE COOLDOWN FROM BECOMING AN OUTAGE.

    MAX_TOKEN_AGE_S is a BUDGET rule (how long we hold a token without proof it
    still works), not a validity rule: NJ Transit says when a token is dead, and it
    has not said so here. So a failed PROACTIVE re-mint must not throw away a
    working token for a replacement that provably cannot be had, and the data poll
    must still succeed on the old one.

    Discard the token on a failed re-mint and this test goes red while every other
    test in this section stays green, which is the point of writing it separately.
    """
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": "old"})), (503, "upstream down")]
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )

    # Past the ceiling: the next call wants a fresh token and cannot have one.
    clock.advance(njt_auth.MAX_TOKEN_AGE_S + 1)
    body = await njt_auth.njt_post(
        URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
    )
    assert body == b"zip-bytes", "the poll still succeeded"
    assert transport.data_calls[-1]["token"] == "old", "on the token it already had"
    assert transport.mints == 2, "having tried exactly once for a new one"
    assert cache.cooldown_remaining() == njt_auth.MINT_COOLDOWN_BASE_S

    # And it keeps serving for the length of the window rather than for one call.
    clock.advance(njt_auth.MINT_COOLDOWN_BASE_S / 2)
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    assert transport.mints == 2, "and asked for nothing while the window was open"


async def test_a_rejected_token_in_a_cooldown_does_take_njt_off_the_map():
    """THE CONTROL FOR THE TEST ABOVE, and the line between the two states.

    An over-age token is unproven; a REJECTED one is dead, and njt_post invalidates
    it, so there is nothing left to serve and the attempt must fail rather than
    quietly re-posting a token NJ Transit has already refused. "Invalid token."
    honors the cooldown too: the over-age token is served through the window its
    failed re-mint opened, and when it is refused, the re-mint the refusal asks for
    is the one the window is holding, so it is refused here rather than sent.
    """
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": "dead"})), (503, "upstream down")],
        data_responses=[(200, "zip-bytes"), (500, INVALID_TOKEN)],
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    clock.advance(njt_auth.MAX_TOKEN_AGE_S + 1)
    with pytest.raises(njt_auth.NjtMintCooldownError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert transport.mints == 2, "the cold mint, then the ceiling's re-mint that failed"
    assert transport.data_calls[-1]["token"] == "dead", "the over-age token was served"

    with pytest.raises(njt_auth.NjtMintCooldownError) as excinfo:
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert transport.mints == 2, "and the poll after it asked for nothing at all"
    assert excinfo.value.remaining_s > 0
    assert cache.peek() is None, "there is no token left to fall back to"


async def test_a_cancelled_mint_spaces_the_next_one_like_a_failed_one():
    """REVERSED IN AUDIT 6 (A01). F05 left a cancelled mint without a window: a
    cancellation is this process giving up, not NJ Transit answering. But the POST has
    usually gone out and been charged (the allowance counts it), and the commonest
    cancellation is a caller's deadline shorter than the mint's own timeout (the alert
    poll's 20 s against REQUEST_TIMEOUT_S's 30), so a getToken slow enough to outlast
    one deadline would be re-minted on every poll and spend the day's allowance back
    to back. A cancelled mint now takes the ladder's base like a failed one, and the
    next caller is held without a request. The quota flag is still left alone (see
    test_a_cancelled_mint_leaves_the_flag_exactly_as_it_was)."""
    sent = []

    async def cancelling(url, form, timeout_s):
        sent.append(url)
        raise asyncio.CancelledError

    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    with pytest.raises(asyncio.CancelledError):
        await njt_auth.njt_post(
            URL, cache=cache, transport=cancelling, env=ENV, token_url=TOKEN_URL
        )
    remaining, detail = cache.cooldown()
    assert remaining == 600.0, "the cancelled POST is spaced like a failed one"
    # str(CancelledError) is empty, so the reason names the deadline in its place.
    assert "deadline" in detail, detail
    assert cache.allowance()["spent"] == 1, "and it was charged"
    with pytest.raises(njt_auth.NjtMintCooldownError):
        await njt_auth.njt_post(
            URL, cache=cache, transport=cancelling, env=ENV, token_url=TOKEN_URL
        )
    assert len(sent) == 1, "the next poll is held without a request"


async def test_the_cooldown_says_what_started_it_and_how_long_is_left():
    """WHAT /api/status PUBLISHES, pinned at the source. The sentence has to name a
    cooldown (so it is not read as an upstream failure), carry the seconds left (so
    an operator knows whether to wait), and repeat the failure that started it (so
    the two policies are distinguishable on the surface).

    AND IT CARRIES NO getToken BODY, which is the F3 rule holding on a message that
    now outlives the request that produced it: the canary is planted in the refusal
    body and must appear nowhere.
    """
    clock = _Clock()
    quota_body = json.dumps({"errorMessage": f"Daily usage limit reached, {CANARY} was last"})
    transport = RecordingTransport(mint_responses=[(500, quota_body)])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    with pytest.raises(njt_auth.NjtMintQuotaError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)

    remaining, detail = cache.cooldown()
    assert "cooldown" in detail, detail
    assert f"{int(remaining)}s" in detail, detail
    assert njt_auth.MINT_QUOTA_MESSAGE in detail, "a spent budget still says so"
    assert CANARY not in detail, "the getToken body reaches no surface, ever"

    # The backoff arm says its own reason rather than the quota one.
    clock.advance(remaining + 1)
    plain = RecordingTransport(mint_responses=[(503, "upstream down")])
    with pytest.raises(njt_auth.NjtAuthError):
        await njt_auth.njt_post(URL, cache=cache, transport=plain, env=ENV, token_url=TOKEN_URL)
    _, detail = cache.cooldown()
    assert "cooldown" in detail and "HTTP 503" in detail, detail
    assert njt_auth.MINT_QUOTA_MESSAGE not in detail

    # And nothing is published at all once the window lapses.
    clock.advance(njt_auth.MINT_COOLDOWN_CAP_S)
    assert cache.cooldown() is None
    assert cache.cooldown_remaining() is None


# ---------------------------------------------------------------------------
# 9. A refused fresh token (Audit 6, A01)
# ---------------------------------------------------------------------------
#
# THE FINDING, as it was measured: a mint that WORKED cleared the cooldown and reset
# the ladder, and njt_post's second rejection raised without telling the cache. So a
# fresh token NJ Transit refused stayed cached as good, the next call re-posted it,
# re-minted and was refused again: ten calls cost 11 getToken and 20 data POSTs and
# left no cooldown, one mint per poll, F05's pre-fix storm from a mint that never
# failed. Every test below is one clause of the fix.


class _Upstream:
    """NJ Transit for this section: every getToken POST is answered with a new,
    distinct token, and every data POST is refused with the probe's invalid-token
    shape, unless `accept` (a 200) or `real_500` (a genuine fault) is set.

    Keyed on the getToken path rather than on TOKEN_URL, so it also stands behind
    the consumers below, which mint at njt_auth.NJT_TOKEN_URL."""

    def __init__(self, mint_delay_s: float = 0.0, data_delay_s: float = 0.0):
        self.mints = 0
        self.data_tokens: list[str] = []
        self.accept = False
        self.real_500 = False
        self.mint_delay_s = mint_delay_s
        self.data_delay_s = data_delay_s

    async def __call__(self, url, form, timeout_s):
        if url.rstrip("/").endswith("getToken"):
            self.mints += 1
            minted = self.mints
            if self.mint_delay_s:
                await asyncio.sleep(self.mint_delay_s)
            return 200, json.dumps({"UserToken": f"fresh-{minted}"}).encode()
        self.data_tokens.append(form["token"])
        if self.data_delay_s:
            await asyncio.sleep(self.data_delay_s)
        if self.real_500:
            return 500, REAL_500
        if self.accept:
            return 200, b"zip-bytes"
        return 500, INVALID_TOKEN


async def test_ten_calls_against_a_refusing_upstream_cost_one_mint_then_hold():
    """THE AUDIT'S OWN EXPERIMENT, with its numbers inverted. Ten calls on one cache,
    an upstream that mints every time and refuses every token: the audit measured 11
    getToken and 20 data POSTs and no cooldown. Now the first call spends the cold
    mint and its refusal, and no re-mint, since the token it was refused on is one it
    minted itself; every later call is held without a request, on a cache holding
    nothing it could re-post."""
    clock = _Clock()
    upstream = _Upstream()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)

    raised = []
    for _ in range(10):
        with pytest.raises(njt_auth.NjtAuthError) as excinfo:
            await njt_auth.njt_post(
                URL, cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL
            )
        raised.append(excinfo.value)

    assert upstream.mints == 1, f"the cold mint and no re-mint, got {upstream.mints} (was 11)"
    assert len(upstream.data_tokens) == 1, f"got {len(upstream.data_tokens)} data POSTs (was 20)"
    assert upstream.data_tokens == ["fresh-1"]
    assert not isinstance(raised[0], njt_auth.NjtMintCooldownError), "the first call asked"
    assert all(isinstance(r, njt_auth.NjtMintCooldownError) for r in raised[1:]), (
        "every later call is held before any request"
    )
    assert cache.peek() is None, "the refused token is dropped, so nothing re-posts it"
    remaining, detail = cache.cooldown()
    assert remaining == 600.0, "the ladder's base"
    assert "rejected a freshly minted token" in detail, "the hold names what started it"


async def test_the_refused_token_ladder_walks_the_same_ladder_to_the_cap():
    """ONE LADDER, ONE BASE, ONE CAP. A refused fresh token starts at
    MINT_COOLDOWN_BASE_S and doubles to MINT_COOLDOWN_CAP_S like a failed mint, and
    each probe after a hold lapses is a cold call: the mint and its refusal, with no
    re-mint. The allowance is lifted here so the ladder's own shape is all that is
    measured; section 10 pins what the allowance does to it."""
    clock = _Clock()
    upstream = _Upstream()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=100)

    holds = []
    for _ in range(4):
        with pytest.raises(njt_auth.NjtAuthError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL
            )
        holds.append(cache.cooldown_remaining())
        clock.advance(holds[-1])

    assert holds == [600, 1200, 1800, 1800], holds
    assert upstream.mints == 4, "four probes, each one cold mint and no re-mint"


async def test_the_streak_ends_on_acceptance_not_on_issue():
    """A WORKING MINT IS NOT PROOF. The ladder used to reset on every mint that
    returned a token, which is exactly the event a refused fresh token begins with,
    so the ladder could never leave its base in A01's shape. Now a mint ends the hold
    it is in and nothing more; a 200 on the token it issued ends the streak. Four
    getToken POSTs in one Eastern day, so the allowance is lifted here."""
    clock = _Clock()
    upstream = _Upstream()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=100)

    async def post():
        return await njt_auth.njt_post(
            URL, cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL
        )

    with pytest.raises(njt_auth.NjtAuthError):
        await post()
    assert cache.cooldown_remaining() == 600
    clock.advance(600)

    # A mint that works, whose token NJ Transit answers with a genuine 500: issued,
    # never accepted. The hold is over, the streak is not.
    upstream.real_500 = True
    with pytest.raises(njt_auth.NjtUpstreamError):
        await post()
    assert cache.cooldown_remaining() is None, "a working mint ends the hold it was in"
    upstream.real_500 = False
    with pytest.raises(njt_auth.NjtAuthError):
        await post()
    assert cache.cooldown_remaining() == 1200, "the streak outlived a mint nobody accepted"
    clock.advance(1200)

    # A 200 on the token a mint just issued ends it.
    upstream.accept = True
    assert await post() == b"zip-bytes"
    upstream.accept = False
    with pytest.raises(njt_auth.NjtAuthError):
        await post()
    assert cache.cooldown_remaining() == 600, "back to the base after an accepted token"


async def test_a_mint_in_flight_does_not_erase_a_hold_that_started_under_it():
    """A REFUSAL CAN LAND WHILE ANOTHER CALLER'S MINT IS OUT. One consumer holds T2
    fresh from a re-mint; a second consumer, refused on T2 first, is already minting
    T3 when the first consumer's own refusal of T2 starts the 600s hold. When T3
    comes back it is stored and served, but the hold stays: a third consumer refused
    on T3 is held, not handed a fourth mint. A mint that cleared every hold on
    success would erase this one and let that fourth mint out."""
    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=100)
    await cache.get(_fixed("T2"))
    release = asyncio.Event()
    minted = []

    async def slow_mint(token):
        minted.append(token)
        await release.wait()
        return token

    cache.invalidate("T2")
    in_flight = asyncio.create_task(cache.get(lambda: slow_mint("T3")))
    for _ in range(5):
        await asyncio.sleep(0)
    assert minted == ["T3"], "the second consumer's re-mint is out"

    cache.reject_fresh("T2", njt_auth.NjtAuthError("refused"))
    assert cache.cooldown_remaining() == 600.0
    release.set()
    assert await in_flight == "T3"
    assert cache.peek() == "T3", "the token is stored and served"
    assert cache.cooldown_remaining() == 600.0, "the hold that started under the mint stays"

    cache.invalidate("T3")
    with pytest.raises(njt_auth.NjtMintCooldownError):
        await cache.get(lambda: slow_mint("T4"))
    assert minted == ["T3"], "no fourth mint"


async def test_a_refused_token_is_compare_and_cleared_and_steps_the_ladder_once():
    """invalidate()'s rule, extended to the hold. A refusal of a token a concurrent
    caller has already replaced clears nothing and starts nothing, so the newer token
    survives untouched; the refusal of the newest token clears it and starts the
    ladder; a second consumer refused on that same token steps the ladder no
    further."""
    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    await cache.get(_fixed("older"))
    cache.invalidate("older")
    await cache.get(_fixed("newer"))

    cache.reject_fresh("older", njt_auth.NjtAuthError("refused"))
    assert cache.peek() == "newer", "a stale refusal must not clear the newer token"
    assert cache.cooldown_remaining() is None, "nor put it under a hold"

    cache.reject_fresh("newer", njt_auth.NjtAuthError("refused"))
    assert cache.peek() is None
    assert cache.cooldown_remaining() == 600.0
    cache.reject_fresh("newer", njt_auth.NjtAuthError("refused"))
    assert cache.cooldown_remaining() == 600.0, (
        "a second consumer refused on the same token steps the ladder once, not twice"
    )


async def test_concurrent_callers_refused_on_one_fresh_token_step_the_ladder_once():
    """The same rule through the door, with both callers genuinely in flight. Both
    hold a working token when NJ Transit starts refusing; both are refused on it,
    share the one re-mint (single flight), are refused on that fresh token, and
    both reach reject_fresh with it. The hold is the base: one step, not two.

    Latency on both endpoints is what puts the second caller's requests inside the
    first caller's, the way two real requests a few hundred milliseconds long
    overlap; without it the first caller finishes before the second one starts."""
    clock = _Clock()
    upstream = _Upstream(mint_delay_s=0.02, data_delay_s=0.01)
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    upstream.accept = True
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    upstream.accept = False

    results = await asyncio.gather(
        *(
            njt_auth.njt_post(URL, cache=cache, transport=upstream, env=ENV, token_url=TOKEN_URL)
            for _ in range(2)
        ),
        return_exceptions=True,
    )
    assert upstream.mints == 2, "the working token, then one re-mint the two callers shared"
    assert upstream.data_tokens[-2:] == ["fresh-2", "fresh-2"], "both were refused on it"
    assert all(
        isinstance(r, njt_auth.NjtAuthError) and not isinstance(r, njt_auth.NjtMintCooldownError)
        for r in results
    ), "both asked, so both carry NJ Transit's answer"
    assert cache.cooldown_remaining() == 600.0


async def test_the_three_consumers_make_no_request_during_a_refused_token_hold(
    monkeypatch, tmp_path
):
    """THE HOLD AS THE PROCESS SEES IT. The feed poller, the alert poller and the
    static warmup, each through its real path to njt_post against the process-wide
    cache, run together inside a refused-token hold: not one getToken or data POST
    leaves, where each used to buy a mint of its own."""
    from google.transit import gtfs_realtime_pb2

    import main
    import njt_static
    import pollers
    import warmups

    clock = _Clock()
    upstream = _Upstream()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    monkeypatch.setattr(njt_auth, "TOKEN_CACHE", cache)
    monkeypatch.setattr(njt_auth, "_httpx_post", upstream)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(njt_static, "NJT_STATIC_ZIP", tmp_path / "gtfs_njt.zip")

    # The hold, armed through the door the way a poll arms it.
    with pytest.raises(njt_auth.NjtAuthError):
        await njt_auth.njt_post(URL)
    assert (upstream.mints, len(upstream.data_tokens)) == (1, 1)
    assert cache.cooldown_remaining() == 600.0

    app = main.app
    monkeypatch.setattr(app.state, "njt_static_status", "ready", raising=False)
    monkeypatch.setattr(app.state, "njt_stops", {"109": {}}, raising=False)
    monkeypatch.setattr(app.state, "njt_trips", {}, raising=False)
    monkeypatch.setattr(app.state, "feed_cache", {"njt": main._fresh_entry()}, raising=False)
    monkeypatch.setattr(app.state, "alerts_cache", main._fresh_alerts_entry(), raising=False)

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    keyless = httpx.MockTransport(
        lambda request: httpx.Response(200, content=feed.SerializeToString())
    )

    class _OneAttempt(Exception):
        pass

    async def stop_after_one_attempt(*_args, **_kwargs):
        raise _OneAttempt

    monkeypatch.setattr(warmups, "_fail_and_wait", stop_after_one_attempt)

    async with httpx.AsyncClient(transport=keyless) as client:
        outcomes = await asyncio.gather(
            pollers._refresh_njt(app, client),
            pollers._refresh_alerts(app, client),
            warmups._warm_njt_static(app),
            return_exceptions=True,
        )

    assert isinstance(outcomes[2], _OneAttempt), "the warmup really made its attempt"
    assert (upstream.mints, len(upstream.data_tokens)) == (1, 1), (
        "no consumer reached NJ Transit while the hold ran"
    )
    feed_error = app.state.feed_cache["njt"]["error"]
    assert feed_error is not None and "cooldown" in feed_error["detail"], feed_error
    alert_error = app.state.alerts_cache["health"]["njt"]["last_error"]
    assert alert_error is not None and "cooldown" in str(alert_error), alert_error


# ---------------------------------------------------------------------------
# 10. The daily mint allowance (Audit 6, A01)
# ---------------------------------------------------------------------------
#
# THE CEILING THE LADDER IS NOT. The ladder spaces attempts; it never bounded how
# many a sustained fault could send, so the first thing that stopped one was NJ
# Transit refusing the eleventh, after the monitor's four were already gone. The
# allowance counts getToken POSTs SENT per Eastern day and holds to the next Eastern
# midnight plus the margin once it is spent.


def _eastern(*args) -> float:
    return datetime(*args, tzinfo=EASTERN).timestamp()


async def _attempt(cache, transport, *, expect=njt_auth.NjtAuthError):
    """One njt_post that is expected to fail, returning what it raised."""
    with pytest.raises(expect) as excinfo:
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    return excinfo.value


async def test_the_allowance_refuses_the_next_post_of_the_day_without_a_request():
    """THE CEILING. Four getToken POSTs go out across a failing day (the ladder's
    windows stepped over, so it is the allowance and not the ladder that stops the
    fifth); the fifth attempt sends nothing, raises the allowance's own error, and
    holds to the next Eastern midnight plus the margin.

    THE COUNT IS OF POSTS SENT, and a failing transport's attempts count: every one
    of the four below yielded no token and every one was charged, because NJ Transit
    charges a refused attempt exactly as it charges a working one."""
    clock = _Clock()  # 2026-09-05 12:00 EDT
    transport = RecordingTransport(mint_responses=[(503, "upstream down")])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    for _ in range(4):
        await _attempt(cache, transport)
        clock.advance(cache.cooldown_remaining())
    assert transport.mints == cache.mint_requests == 4 and cache.mints == 0

    refused = await _attempt(cache, transport, expect=njt_auth.NjtMintAllowanceError)
    assert transport.mints == 4, "the fifth attempt of the Eastern day sent nothing"
    assert not isinstance(refused, njt_auth.NjtMintCooldownError), "its own error, not a cooldown"
    # Refused at 13:30 EDT (the ladder's 600, 1200, 1800 and 1800 stepped over): ten
    # hours thirty to midnight, plus the minute's margin.
    assert cache.cooldown_remaining() == refused.remaining_s == 37_860.0
    assert cache.mint_allowance_spent is True
    assert cache.allowance() == {
        "day": "2026-09-05",
        "spent": 4,
        "limit": njt_auth.DAILY_MINT_ALLOWANCE,
        "spent_out": True,
    }

    # Every later attempt that day is refused the same way, and none is sent.
    clock.advance(3600)
    await _attempt(cache, transport, expect=njt_auth.NjtMintAllowanceError)
    assert transport.mints == 4

    # Past the next Eastern midnight plus the margin, the new day's first attempt goes.
    clock.advance(cache.cooldown_remaining())
    await _attempt(cache, transport)
    assert transport.mints == 5
    assert cache.mint_allowance_spent is False
    assert cache.allowance()["day"] == "2026-09-06" and cache.allowance()["spent"] == 1


async def test_a_working_mint_and_a_200_do_not_reset_the_allowance():
    """ONLY A NEW DAY RESETS IT. Four working mints, each answered by a 200 on its
    own token (the age ceiling is what asks for each new one): the fifth is still
    refused. A count that a success reset would bound nothing in a day of healthy
    re-mints followed by a fault."""
    clock = _Clock(wall=_eastern(2026, 9, 5, 0, 30))
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": f"t{i}"})) for i in range(1, 7)]
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, max_age_s=600)
    for _ in range(4):
        assert (
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
            == b"zip-bytes"
        )
        clock.advance(600)
    assert transport.mints == 4
    # The fifth mint is wanted (t4 is over the ceiling) and refused, so t4 keeps
    # serving: the allowance holds the mint, not the data.
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    assert transport.mints == 4
    assert transport.data_calls[-1]["token"] == "t4"
    assert cache.mint_allowance_spent is True


async def test_the_allowance_day_is_the_eastern_date_not_the_utc_one():
    """THE BOUNDARY IS AN EASTERN MIDNIGHT. A wall clock at 23:59:30 ET and then
    00:00:30 ET is two days, so the second attempt is charged to a fresh allowance.
    A wall clock at 19:59:30 EDT and then 20:00:30 EDT crosses 00:00 UTC and is ONE
    Eastern day, so the second attempt is refused. Keying the day on the UTC date
    gets both of these wrong."""

    async def two_attempts(first_wall: float, second_wall: float) -> tuple[int, bool]:
        clock = _Clock(wall=first_wall)
        cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
        await cache.get(_fixed("one"))
        cache.invalidate(None)
        clock.advance(second_wall - first_wall)
        minted = []

        async def second():
            minted.append(True)
            return "two"

        try:
            await cache.get(second)
        except njt_auth.NjtMintAllowanceError:
            pass
        return len(minted), cache.mint_allowance_spent

    across_eastern_midnight = await two_attempts(
        _eastern(2026, 9, 5, 23, 59, 30), _eastern(2026, 9, 6, 0, 0, 30)
    )
    assert across_eastern_midnight == (1, False), "a new Eastern day, a new allowance"

    across_utc_midnight = await two_attempts(
        _eastern(2026, 9, 5, 19, 59, 30), _eastern(2026, 9, 5, 20, 0, 30)
    )
    assert across_utc_midnight == (0, True), "still the same Eastern day"


async def test_an_allowance_hold_across_a_dst_boundary_is_the_length_of_that_day():
    """The quota hold's DST rule, applied to the allowance hold: spent at 00:00 ET on
    the November day it runs twenty-five hours plus the margin, on the March day
    twenty-three, because the next midnight is named, not measured."""
    for day, hours in (((2026, 11, 1), 25), ((2026, 3, 8), 23), ((2026, 9, 5), 24)):
        clock = _Clock(wall=_eastern(*day, 0, 0))
        cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
        await cache.get(_fixed("only"))
        cache.invalidate(None)
        with pytest.raises(njt_auth.NjtMintAllowanceError):
            await cache.get(_fixed("refused"))
        assert cache.cooldown_remaining() == hours * 3600.0 + njt_auth.MINT_QUOTA_RESET_MARGIN_S


async def test_an_over_age_token_serves_through_an_allowance_hold():
    """THE ALLOWANCE HOLDS MINTING, NOT DATA, as the cooldown does. An over-age token
    is unproven, not dead, so it keeps serving through the hold; MAX_TOKEN_AGE_S is a
    budget rule, and the allowance is the moment a replacement provably cannot be
    bought."""
    clock = _Clock(wall=_eastern(2026, 9, 5, 8, 0))
    transport = RecordingTransport()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    clock.advance(njt_auth.MAX_TOKEN_AGE_S + 1)  # 20:00 the same Eastern day
    assert (
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
        == b"zip-bytes"
    )
    assert transport.mints == 1, "the re-mint the age ceiling wanted was not sent"
    assert transport.data_calls[-1]["token"] == "t1", "and the old token served"
    assert cache.mint_allowance_spent is True


async def test_with_no_token_an_allowance_hold_raises_its_own_error():
    """A REFUSED TOKEN IS DROPPED, so through an allowance hold there is nothing to
    serve and NJ Transit is dark until the reset, which is the honest state when the
    data endpoint refuses everything. The error is the allowance's, not the
    cooldown's: the three holds are tellable apart wherever they are caught."""
    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
    await cache.get(_fixed("only"))
    cache.reject_fresh("only", njt_auth.NjtAuthError("refused"))
    clock.advance(cache.cooldown_remaining())
    with pytest.raises(njt_auth.NjtMintAllowanceError) as excinfo:
        await cache.get(_fixed("never"))
    assert isinstance(excinfo.value, njt_auth.NjtAuthError)
    assert not isinstance(
        excinfo.value, (njt_auth.NjtMintCooldownError, njt_auth.NjtMintQuotaError)
    )
    assert njt_auth.MINT_ALLOWANCE_MESSAGE in str(excinfo.value)
    assert excinfo.value.remaining_s == cache.cooldown_remaining() > 0


async def test_a_burst_of_waiters_cannot_each_spend_the_allowance():
    """THE CHECK SITS INSIDE THE LOCK, AFTER BOTH RE-CHECKS, immediately before the
    POST is counted. Twelve callers queue behind one slow failing mint with one POST
    of the day's two still unspent: the holder spends it, and the eleven waiters,
    finding the failure it left, are refused by the ladder's hold and charged
    nothing. With the check ahead of the re-checks every waiter would find the
    allowance spent and latch the rest of the day."""
    clock = _Clock()
    transport = RecordingTransport(mint_responses=[(503, "upstream down")], mint_delay_s=0.02)
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=2)
    await _attempt(cache, transport)
    clock.advance(cache.cooldown_remaining())
    results = await asyncio.gather(
        *(
            njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
            for _ in range(12)
        ),
        return_exceptions=True,
    )
    assert transport.mints == 2, "the holder spent the second, and none of the waiters sent"
    assert cache.allowance()["spent"] == 2
    assert cache.mint_allowance_spent is False, "the waiters met the ladder, not the allowance"
    assert sum(isinstance(r, njt_auth.NjtMintCooldownError) for r in results) == 11
    assert not any(isinstance(r, njt_auth.NjtMintAllowanceError) for r in results)


async def test_a_wall_clock_stepped_back_across_midnight_does_not_reopen_the_allowance():
    """ONLY A LATER DATE OPENS A NEW COUNT. An NTP correction that steps the wall
    clock back across midnight names the date before, and resetting on any change of
    date would hand the process a fresh allowance there, and another when the clock
    stepped forward again. The allowance of one is spent just after midnight; the
    wall clock steps back two minutes while the monotonic one runs on; the next
    attempt is still refused, and the count still belongs to the later date."""
    clock = _Clock(wall=_eastern(2026, 9, 6, 0, 1))
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
    await cache.get(_fixed("one"))
    cache.invalidate(None)
    clock.now += 60
    clock.wall = _eastern(2026, 9, 5, 23, 59)
    minted = []

    async def second():
        minted.append(True)
        return "two"

    with pytest.raises(njt_auth.NjtMintAllowanceError):
        await cache.get(second)
    assert minted == [], "the stepped-back date did not reopen the day's allowance"
    assert cache.allowance()["day"] == "2026-09-06"


async def test_a_200_on_an_older_token_does_not_end_the_streak():
    """ONLY THE NEWEST UNACCEPTED TOKEN ENDS THE STREAK. An over-age token served
    through a failed re-mint keeps getting 200s; they prove the old token works and
    nothing about minting, so the ladder its failed re-mint started keeps its place
    and the next failed re-mint waits 1200 s, not 600."""
    clock = _Clock()
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": "old"})), (503, "upstream down")]
    )
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)

    async def post():
        return await njt_auth.njt_post(
            URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
        )

    assert await post() == b"zip-bytes"
    clock.advance(12 * 3600 + 1)
    assert await post() == b"zip-bytes", "the re-mint failed and the old token served"
    assert cache.cooldown_remaining() == 600.0
    clock.advance(600)
    assert await post() == b"zip-bytes"
    assert transport.mints == 3
    assert cache.cooldown_remaining() == 1200.0, "the 200s on the old token did not reset it"


async def test_a_late_refusal_never_shortens_a_longer_hold():
    """A HOLD IS NEVER SHORTENED. reject_fresh follows a POST and runs outside the
    lock, so it can land while a longer hold is running. Here the refused token was
    issued, another caller's mint then drew NJ Transit's quota refusal (a hold to
    midnight), and only then did the refusal of the first token arrive: its 600 s
    step must not cut the midnight hold to ten minutes. The same for the allowance
    hold, which must also stay the allowance's, flag and all."""
    clock = _Clock()  # noon EDT
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    await cache.get(_fixed("T"))
    cache.invalidate("T")
    quota = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL)])
    await _attempt(cache, quota, expect=njt_auth.NjtMintQuotaError)
    assert cache.cooldown_remaining() == 43_260.0
    cache.reject_fresh("T", njt_auth.NjtAuthError("refused"))
    assert cache.cooldown_remaining() == 43_260.0, "the quota hold stands"

    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
    await cache.get(_fixed("T"))
    cache.invalidate("T")
    with pytest.raises(njt_auth.NjtMintAllowanceError):
        await cache.get(_fixed("never"))
    cache.reject_fresh("T", njt_auth.NjtAuthError("refused"))
    assert cache.cooldown_remaining() == 43_260.0, "the allowance hold stands"
    assert cache.mint_allowance_spent is True


async def test_the_allowance_hold_runs_on_the_monotonic_clock():
    """THE HOLD IS MONOTONIC; ONLY THE DATE IS THE WALL CLOCK'S. The allowance of one
    is spent at noon and the hold runs to 00:01. The wall clock then steps thirteen
    hours forward, past that midnight, while the monotonic clock moves one second:
    the hold still has 43,259 s to run, and the next attempt sends nothing."""
    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
    await cache.get(_fixed("one"))
    cache.invalidate(None)
    with pytest.raises(njt_auth.NjtMintAllowanceError):
        await cache.get(_fixed("two"))
    assert cache.cooldown_remaining() == 43_260.0
    clock.now += 1
    clock.wall += 13 * 3600
    minted = []

    async def third():
        minted.append(True)
        return "three"

    with pytest.raises(njt_auth.NjtMintAllowanceError):
        await cache.get(third)
    assert minted == [], "a wall clock step does not lapse a monotonic hold"
    assert cache.cooldown_remaining() == 43_259.0


async def test_the_quota_refusal_and_the_allowance_are_independent_flags():
    """NJ Transit's refusal still latches exactly as before, and it is NJ Transit's
    word, so it sets mint_quota_refused and not the allowance flag; the allowance is
    ours, and sets the other. Neither can be read off the other."""
    clock = _Clock()
    quota = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL)])
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock)
    await _attempt(cache, quota, expect=njt_auth.NjtMintQuotaError)
    assert (cache.mint_quota_refused, cache.mint_allowance_spent) == (True, False)
    assert cache.cooldown_remaining() == 43_260.0, "noon EDT to midnight, plus the margin"

    clock = _Clock()
    cache = njt_auth.TokenCache(clock=clock, wall_clock=clock.wall_clock, daily_allowance=1)
    await cache.get(_fixed("only"))
    cache.invalidate(None)
    with pytest.raises(njt_auth.NjtMintAllowanceError):
        await cache.get(_fixed("refused"))
    assert (cache.mint_quota_refused, cache.mint_allowance_spent) == (False, True)


def test_the_allowance_is_the_budget_line_it_claims_to_be():
    """THE CONSTANT, pinned as the budget arithmetic it is: the monitor's four plus
    production's allowance leaves the operator two of the account's ten."""
    assert njt_auth.DAILY_MINT_ALLOWANCE == 4
    monitor = 4
    assert njt_auth.DAILY_MINT_LIMIT - monitor - njt_auth.DAILY_MINT_ALLOWANCE == 2
    assert njt_auth.TOKEN_CACHE.allowance()["limit"] == njt_auth.DAILY_MINT_ALLOWANCE
