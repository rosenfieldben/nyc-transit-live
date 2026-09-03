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
     keeps saying.
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


class _Clock:
    """A hand-cranked monotonic clock, so the ceiling is tested by advancing time
    rather than by waiting twelve hours."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


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
    is blind to a loop that mints unsuccessfully forever."""
    transport = RecordingTransport(mint_responses=[(503, "upstream down")])
    cache = njt_auth.TokenCache()
    for _ in range(4):
        with pytest.raises(njt_auth.NjtAuthError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
    assert cache.mints == 0, "no token was ever issued"
    assert cache.mint_requests == 4, "but four getToken POSTs were really sent"
    assert transport.mints == 4


# ---------------------------------------------------------------------------
# 3. One re-mint, then the attempt fails
# ---------------------------------------------------------------------------


async def test_an_expired_token_is_replaced_once_and_the_request_succeeds():
    transport = RecordingTransport(
        mint_responses=[
            (200, json.dumps({"UserToken": "dead"})),
            (200, json.dumps({"UserToken": "live"})),
        ],
        data_responses=[(500, INVALID_TOKEN), (200, "zip-bytes")],
    )
    cache = njt_auth.TokenCache()
    body = await njt_auth.njt_post(
        URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
    )
    assert body == b"zip-bytes"
    assert transport.mints == 2, "one cold mint plus exactly one replacement"
    assert [form["token"] for form in transport.data_calls] == ["dead", "live"]


async def test_one_remint_then_the_attempt_fails():
    """The upstream keeps saying invalid-token. We must stop after ONE re-mint.

    A retry LOOP here would keep minting for as long as the upstream keeps
    rejecting, which is precisely how an integration exhausts a rate limit it
    cannot measure. The absence of a loop in njt_post is the enforcement; this is
    the proof.
    """
    transport = RecordingTransport(
        mint_responses=[(200, json.dumps({"UserToken": "t1"}))],
        data_responses=[(500, INVALID_TOKEN)],  # forever
    )
    with pytest.raises(njt_auth.NjtAuthError):
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    assert transport.mints == 2, f"never more than two mints in one attempt, got {transport.mints}"
    assert len(transport.data_calls) == 2, "one original request plus exactly one retry"


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
    """The caller's schedule is what tries again, and each attempt costs exactly one
    getToken. Four attempts, four POSTs, never eight: the absence of a loop in
    njt_post is the enforcement and this is the proof, made with the refusal that
    makes a loop most expensive."""
    transport = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL)])
    cache = njt_auth.TokenCache()
    for _ in range(4):
        with pytest.raises(njt_auth.NjtMintQuotaError):
            await njt_auth.njt_post(
                URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL
            )
    assert transport.mints == 4
    assert cache.mint_requests == 4
    assert cache.mints == 0, "no token was ever issued"


async def test_the_cache_records_the_refusal_and_the_next_good_mint_clears_it():
    """WHAT /healthz READS. The flag says one thing only: the most recent mint
    attempt was refused for the cap. It lives on the cache because the cache is what
    every mint in the app goes through, so it cannot be stale the way a flag
    somebody remembered to set could be.

    THE CLEARING IS THE HALF THAT MATTERS AFTER MIDNIGHT. There is no date
    arithmetic anywhere: the first mint that succeeds after the Eastern reset clears
    the flag as a side effect of working, so /healthz stops publishing the code
    without anything having to notice a clock."""
    transport = RecordingTransport(
        mint_responses=[(500, QUOTA_REFUSAL), (200, json.dumps({"UserToken": "t1"}))]
    )
    cache = njt_auth.TokenCache()
    assert cache.mint_quota_refused is False, "a fresh cache has not been refused anything"
    with pytest.raises(njt_auth.NjtMintQuotaError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert cache.mint_quota_refused is True
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
    recent refusal."""
    transport = RecordingTransport(mint_responses=[(500, QUOTA_REFUSAL), (503, "upstream down")])
    cache = njt_auth.TokenCache()
    with pytest.raises(njt_auth.NjtMintQuotaError):
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert cache.mint_quota_refused is True
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(URL, cache=cache, transport=transport, env=ENV, token_url=TOKEN_URL)
    assert not isinstance(excinfo.value, njt_auth.NjtMintQuotaError)
    assert cache.mint_quota_refused is False


async def test_a_cancelled_mint_leaves_the_flag_exactly_as_it_was():
    """Exception, not BaseException, in the cache's bookkeeping. A cancellation is
    this process giving up (a shutdown, an attempt deadline), not NJ Transit
    answering, so it says nothing either way about the budget and must not clear a
    refusal that really happened."""

    async def cancelling(url, form, timeout_s):
        raise asyncio.CancelledError

    cache = njt_auth.TokenCache()
    cache.mint_quota_refused = True
    with pytest.raises(asyncio.CancelledError):
        await njt_auth.njt_post(
            URL, cache=cache, transport=cancelling, env=ENV, token_url=TOKEN_URL
        )
    assert cache.mint_quota_refused is True
