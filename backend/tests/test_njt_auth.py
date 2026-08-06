"""Tests for the NJ Transit token door (backend/njt_auth.py).

No test here touches the network: the transport is injected everywhere, and the
absent-credentials tests inject a transport that FAILS THE TEST IF CALLED, which
is how "no credentials means no network" becomes a proved property rather than a
comment.

Four things are pinned, in the order they can hurt:

  1. THE SNIFF, BOTH WAYS. is_auth_error must be true for exactly the probe's
     shape (HTTP 500 with {"errorMessage":"Invalid token."}) and false for
     everything else, including a genuine 500. A false positive spends a mint
     against an unpublished rate cap; a false negative costs one retry on the
     caller's schedule. The mutation check in the 15a handoff loosens this to
     "any 500" and the control tests below are what must go red.
  2. MINT CONSERVATION UNDER CONCURRENCY. N callers finding an empty cache
     together produce exactly ONE mint.
  3. ONE RE-MINT, THEN THE ATTEMPT FAILS. Never a loop, whatever the upstream
     keeps saying.
  4. ABSENT CREDENTIALS REACH NO SOCKET, from either entry point.
"""

from __future__ import annotations

import asyncio
import json

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


class _Clock:
    """A hand-cranked monotonic clock, so the ceiling is tested by advancing time
    rather than by waiting six hours."""

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
    traffic that spends the unpublished cap. Asserting conservation on `mints` alone
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


async def test_a_failed_mint_fails_the_attempt_with_the_body_quoted():
    transport = RecordingTransport(mint_responses=[(401, '{"errorMessage":"Bad credentials."}')])
    with pytest.raises(njt_auth.NjtAuthError) as excinfo:
        await njt_auth.njt_post(
            URL, cache=njt_auth.TokenCache(), transport=transport, env=ENV, token_url=TOKEN_URL
        )
    assert "Bad credentials." in str(excinfo.value)
    assert transport.data_calls == [], "a failed mint must not be followed by a data request"


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
