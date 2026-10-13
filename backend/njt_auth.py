"""The one door every NJ Transit RailData request goes through (15a).

NJ Transit's RailData API is unlike every other upstream this app talks to. The
differences are not cosmetic, and each one is a way a conventional poller gets
this wrong, so they are stated here rather than discovered later:

  1. EVERY endpoint is POST multipart/form-data, never GET. The token rides as a
     FORM FIELD, not an Authorization header and not a query parameter.
  2. A token is minted by exchanging a username and password at getToken. Its
     LIFETIME IS UNDOCUMENTED: the vendor's own docs template carries a literal
     blank where the number should be. So nothing here may assume an expiry
     window; the only reliable signal that a token died is the upstream saying so.
     MAX_TOKEN_AGE_S is not that assumption in disguise: it is a ceiling on how
     long we will hold a token without proof it still works, which is what stops a
     rejection the sniff fails to recognise from wedging the cache permanently.
  3. Minting is rate-limited BELOW the data cap, at TEN A DAY PER ACCOUNT, and
     the day is an Eastern one. Observed 2026-09-02: an eleventh getToken comes
     back HTTP 500 with a JSON body whose errorMessage begins "Daily usage
     limit", and EVERY ATTEMPT COUNTS, refused ones included. Tokens are also
     product-scoped, so a GTFSRT token is rejected by the Usage API and we cannot
     read our own counters; the cap is now a known number, but our position
     against it is still unmeasurable. What that budget is spent on, and why
     six of the ten are already committed on a quiet day, is at DAILY_MINT_LIMIT
     below.
  4. AUTH FAILURE IS HTTP 500, NOT 401 OR 403. Probed 2026-08-05 (overnight
     02:37 EDT and rush 18:15 EDT): the body is {"errorMessage":"Invalid token."}
     under a 500 status. This is the most dangerous fact in the probe. A poller
     that classifies 500 as "server error" backs off forever while the actual fix
     is a single re-mint, and a poller that treats ALL 500s as auth failures burns
     mints against a ten-a-day cap every time NJ Transit has a real outage.
     is_auth_error below is the exact-shape sniff that separates the two.

WHAT THIS MODULE DOES NOT DO. It never retries on a schedule. A second auth
failure, a failed mint, or any non-auth upstream failure surfaces as the
attempt's failure and the CALLER's schedule (the C-era warmup rungs) decides when
to try again. That is what keeps our own error handling from exhausting the mint
cap: there is no path here that can call getToken twice in a row without a full
attempt boundary in between.

WHAT IT DOES DO, SINCE AUDIT 5 (F05): IT REFUSES. Not retrying was never the same
as not being retried, and the difference cost the whole budget. The callers all
have schedules of their own (the warmup's rungs, a 20s feed poll, a 60s alert
poll), so a mint that failed at t=0 was attempted again at t=20 by somebody else,
four times in the first minute and 240 times in the first hour, none of it a
retry any single caller could see itself making. A failed mint now leaves a
COOLDOWN on the shared TokenCache, and every get() inside that window raises
NjtMintCooldownError without calling getToken at all: a quota refusal holds to
the next Eastern midnight because that is when the answer changes, and every
other failure takes bounded exponential backoff because its length is unknown.
The caller's schedule still decides when to ASK. This decides how often asking
can reach NJ Transit, which is the only one of the two that a rate limit counts.

AND SINCE AUDIT 6 (A01), TWO MORE THINGS COUNT AS FAILING. A mint that returns a
token NJ Transit then refuses is a failed mint for the budget: njt_post tells the
cache (TokenCache.reject_fresh), the token is dropped, and the same ladder starts,
because before that a mint that worked ended every hold and each poll bought another
refused token. A fresh token NJ Transit refuses, one it has never accepted and that
was minted moments ago, is not re-minted either: the next one would be refused the
same way. And the ladder only
spaces attempts, so one process also stops itself at DAILY_MINT_ALLOWANCE getToken
POSTs per Eastern day and holds to the next Eastern midnight, which is what keeps any
fault inside the process from reaching the monitor's share of the ten.

CONSUMED BY STATIC ONLY IN 15a. njt_static fetches its archive through njt_post,
so the token path is exercised from birth rather than landing untested alongside
the realtime work that will need it in 15b.

TWO RULES THAT FOLLOW FROM THE SECRET RIDING IN A BODY (Audit 4, F2 and F3). The
credentials and the token travel in the multipart body of every request, which is
a shape none of the other upstreams have, and it breaks two habits that are safe
everywhere else in this app:

  - A credentialed POST NEVER FOLLOWS A REDIRECT. httpx re-sends a POST body on a
    307 or 308, to whatever host the Location names, so following would hand the
    username and password (or the token) to any origin a redirect pointed at. A
    3xx from RailData therefore arrives here as a non-200 and fails the attempt.
  - THE getToken RESPONSE BODY IS NEVER QUOTED, at any status. Every other body in
    this system is the upstream's words (a feed, an error page) and safe to quote
    into a message; the getToken body IS the token. A message about it reports the
    status, the byte length, or the key names, never the content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from datetime import time as time_of_day
from zoneinfo import ZoneInfo

import httpx

import env_seams

logger = logging.getLogger(__name__)

# Verified 2026-08-05: POST multipart/form-data, ~21-char token in a JSON body.
# Overridable (C6), used whole, so the contract tier can stand a simulator in
# front of it: the loader still mints and POSTs, only the host changes.
NJT_TOKEN_URL = env_seams.url("NJT_TOKEN_URL", "https://raildata.njtransit.com/api/GTFSRT/getToken")

# The two credential names. Deliberately NOT env seams: a seam is a wiring
# override the contract monitor refuses to run with (env_seams.assert_unset), and
# the monitor needs these SET to check the real upstream. Same treatment as
# BUS_TIME_API_KEY, which is also read as a plain environment variable.
USERNAME_VAR = "NJT_USERNAME"
PASSWORD_VAR = "NJT_PASSWORD"

# The placeholders .env.example ships, treated as absent so a copied-but-unedited
# .env reads as "not configured" rather than sending a doomed mint. Mirrors
# feeds.shared._api_key's "your-key-here" guard.
#
# BOTH NAMES, not just the username, and the asymmetric case is the realistic one:
# someone pastes their real registered username over the first line and does not
# notice the second is still the shipped example. That reads as configured, takes
# the app out of its not-configured short circuit, and puts it in a retry loop
# posting a doomed mint on every rung. Checking each field against its own
# placeholder costs one line and closes it.
_PLACEHOLDERS = {
    USERNAME_VAR: "your-njt-username",
    PASSWORD_VAR: "your-njt-password",
}

# THE DAILY MINT BUDGET. Ten getToken calls per ACCOUNT per EASTERN DAY, observed
# 2026-09-02, and EVERY ATTEMPT COUNTS against it whether or not it yields a token.
# The eleventh comes back HTTP 500 with a JSON body whose errorMessage begins
# "Daily usage limit"; is_mint_quota_error below is the sniff for it.
#
# THE NUMBER IS NOT ENFORCED HERE AND CANNOT BE. Tokens are product-scoped, so the
# Usage API rejects a GTFSRT token and we cannot read our own counter, and one
# process has no idea what the account's other consumers spent today. This
# constant exists so the arithmetic below has a name to hang on and so every
# "conserve mints" comment in this repo points at one number, not to gate anything.
# What one PROCESS may spend is gated, by DAILY_MINT_ALLOWANCE below (Audit 6, A01).
#
# WHERE THE TEN GO ON A QUIET DAY, and six are committed before anything unusual
# happens at all:
#
#     4   the contract monitor. Six-hourly (.github/workflows/contract-monitor.yml),
#         minting ONCE per run and sharing that token across njt-static and
#         njt-realtime's two feeds. See contract_monitor._NjtToken.
#     2   production, from MAX_TOKEN_AGE_S alone: the ceiling below is twelve hours,
#         so a process that stays up through the day re-mints twice.
#   ---
#     6   committed. Four spare.
#
# AND WHERE THEY CAN GO AT WORST, since the allowance: production's share is a
# ceiling of four per process per Eastern day, not a forecast of two, so the
# monitor's four and two more are out of any one process's reach:
#
#     4   the contract monitor, as above
#     4   production, AT MOST (DAILY_MINT_ALLOWANCE)
#   ---
#     8   the most the two can spend. Two left for an operator.
#
# What spends the four is ordinary work rather than an incident: a deploy (a cold
# TokenCache mints on the first NJT request), a manual workflow_dispatch of the
# monitor (one more run, one more mint), a fixture pull (gen_njt_fixture.py and
# gen_njt_rt_fixture.py mint one each), and a genuine token expiry (njt_post buys
# exactly one re-mint per attempt). Four of those on one day take NJ Transit dark
# in PRODUCTION until Eastern midnight, because production and the monitor share
# the account. That is why every mint in this repo is conserved structurally rather
# than by convention, and why a loop that re-mints on failure is the one bug this
# module is built to make unwritable. The allowance bounds what one process spends
# on its own failures; it cannot bound a deploy, which is a fresh process with a
# fresh count, nor a fixture pull or a monitor run, which are processes of their own.
DAILY_MINT_LIMIT = 10

# THE ALLOWANCE: HOW MANY OF THE TEN THIS PROCESS MAY SPEND (Audit 6, A01). Counted in
# getToken POSTs SENT per Eastern day, the quantity mint_requests counts and the
# account's ten are spent by, so a refused or failed POST costs one exactly as a
# working one does. Once it is spent, the next attempt sends nothing and holds the
# cache until the next Eastern midnight plus MINT_QUOTA_RESET_MARGIN_S, keeps serving
# a token it already holds, and says so (TokenCache.mint_allowance_spent, /healthz
# njt-mint-allowance). The count itself turns over at the Eastern midnight, without
# the margin: the day is the date of the attempt.
#
# WHY FOUR. What production spends on an ordinary day is two (MAX_TOKEN_AGE_S below:
# a process that spans the day re-mints twice); a real expiry buys one re-mint per
# attempt; a deploy's cold mint is one. Four covers each of those once over, and it
# turns the budget above into a ceiling instead of a forecast:
#
#     4   the contract monitor, six-hourly, one mint per run
#     4   production, AT MOST, per process per Eastern day (this allowance)
#   ---
#     8   the most the two of them can spend
#     2   left for an operator's own work: a fixture pull, a manual monitor dispatch
#
# THE LADDER STILL SPACES THE ATTEMPTS; THIS BOUNDS THEIR NUMBER. Neither replaces the
# other. Without this, a fault the ladder only slows (a mint that keeps failing, a
# fresh token that keeps being refused) still reached into the monitor's four, and
# the first thing to stop it was NJ Transit's own refusal of the eleventh.
#
# IN MEMORY, PER PROCESS, AND THAT IS ITS LIMIT. The count is not persisted: a deploy
# is a fresh process, it starts at zero, and its cold mint is its own first. So this
# bounds what ONE process can spend in one Eastern day. A deploy storm is the
# operator's own doing and is not bounded here; nothing in a process can see the
# account's count (see DAILY_MINT_LIMIT), and a counter that survived a restart would
# need a store this app does not have.
#
# OVERRIDABLE through a strict seam (env_seams.count): a positive whole number or the
# app refuses to start. The contract tier lowers it to see the hold in real time.
DAILY_MINT_ALLOWANCE = env_seams.count("NJT_DAILY_MINT_ALLOWANCE", 4)

# The reason an allowance hold carries, ours and fixed, like MINT_QUOTA_MESSAGE.
MINT_ALLOWANCE_MESSAGE = "this process's daily NJ Transit mint allowance is spent"

# The fixed message every quota refusal is reported with, here and in the contract
# monitor. A CONSTANT MATCHED AGAINST A KNOWN PHRASE IS NOT A QUOTE (Audit 4, F3):
# is_mint_quota_error compares the body against our own literal prefix and returns
# a bool, and this string, also ours, is what gets raised and logged. No byte of
# the response travels with it, so a refusal whose body happened to carry a live
# token still cannot reach a log line.
MINT_QUOTA_MESSAGE = "NJ Transit daily mint limit reached"

# The prefix observed on that refusal, lowercased for the case-insensitive compare
# is_auth_error already uses. A PREFIX because a prefix is all that was observed:
# the message continues past it, and pinning the whole sentence would turn the
# sniff into a false negative the first time NJ Transit reworded its tail.
_QUOTA_PREFIX = "daily usage limit"

# Whole-request ceiling for one NJT POST. The static endpoint answered in 428 ms
# overnight and 8.9 s at peak on 2026-08-05, so this is a wedge guard rather than
# a latency budget.
#
# WHY 30 AND NOT THE 120 THE OTHER TRANSFERS USE. The enclosing budget for the
# static load is static_shared.DOWNLOAD_DEADLINE_S (120s), which _download_via_token
# wraps around the WHOLE njt_post call, and one call is up to four requests on the
# invalid-token path this module exists for: mint, POST, re-mint, POST. A 120s
# per-request ceiling inside a 120s whole-call ceiling can never fire (the outer one
# always starts strictly earlier), so it was a guard in name only, and one slow-but-
# healthy request could eat the entire budget that three others still had to fit in.
# 30s times four is exactly the outer budget, and it is more than three times the
# worst latency the probe measured, so a genuinely wedged request is cut off with
# time left for the rest of the sequence. Since Audit 6 (A01) a call is ordinarily
# three requests at most, POST, re-mint, POST, because a fresh token buys no re-mint;
# four still bound it (njt_post says when four happen), so the arithmetic stands.
REQUEST_TIMEOUT_S = 30.0

# HOW LONG A TOKEN NJ TRANSIT HAS NEVER ACCEPTED STAYS FRESH (Audit 6, A01). A fresh
# token that is refused buys no re-mint: NJ Transit has just refused a token it issued,
# and the next would be refused the same way. Fresh is bounded twice. NEVER ACCEPTED,
# because a token NJ Transit has answered with a 200 and now refuses has expired, and
# expiry is what the one re-mint is for. And YOUNGER THAN THIS, the longest one
# njt_post call can last (four requests at REQUEST_TIMEOUT_S): a token minted by this
# attempt, or by one running beside it, is inside it, while one minted hours ago that
# has only ever drawn genuine 500s or deadlines may simply have expired by the time it
# is refused, and its re-mint is a probe rather than a repeat.
FRESH_TOKEN_S = 4 * REQUEST_TIMEOUT_S

# The longest this process will keep using a token before minting a fresh one.
#
# NOT A CLAIM ABOUT WHEN THE TOKEN DIES. The lifetime is undocumented (the vendor's
# own docs template has a literal blank there), so nothing here may assert one. This
# is a ceiling on how long we will hold a token WITHOUT PROOF IT STILL WORKS, and it
# exists because the alternative is unbounded.
#
# THE FAILURE IT CLOSES: is_auth_error matches one exact response shape, so a
# rejection that drifts by one character is a false negative. The module used to
# price that as "costs one failed attempt; the caller's rung schedule tries again",
# and that was wrong: the non-auth path returns without invalidating, the cache has
# no expiry, and the warmup loop then re-posts the SAME dead token forever, so a
# false negative was terminal until the process restarted. A ceiling makes the
# documented price true. Any rejection this module fails to recognise now heals by
# itself within one ceiling, without widening the sniff toward real 500s.
#
# WHY TWELVE HOURS. It bounds the worst case at TWO mints a day per process from
# this rule alone, which is 2 of the ten at DAILY_MINT_LIMIT: with the monitor's 4
# that is 6 committed and 4 spare. At six hours this line was 4, the budget was 8
# committed with 2 spare, and a deploy on the same day as a fixture pull took NJ
# Transit dark for riders.
#
# THIS CEILING IS NOT WHAT CARRIES AN EXPIRY, which is what makes twelve safe rather
# than reckless. A token that actually dies is recognised by is_auth_error and
# replaced INSIDE the same attempt, for exactly one extra mint: njt_post buys one
# re-mint and no more, and the contract tier measures that at one across the NJT
# consumers polling on the token when it dies. So lengthening this trades at most one
# REACTIVE mint per real expiry against two PROACTIVE mints saved every day, and a
# real expiry is not a daily event.
#
# WHAT IT COSTS, stated rather than glossed: a rejection the sniff fails to
# recognise now heals in up to twelve hours instead of six, so the self-heal spans
# two contract-monitor cycles rather than landing inside one. The monitor still sees
# the state on the first of those cycles, which is what an operator needs; a dark
# layer for one extra cycle is the price of four spare mints instead of two.
MAX_TOKEN_AGE_S = 12 * 3600.0

# THE COOLDOWN AFTER A FAILED MINT (Audit 5, F05). Two numbers, because a failed
# mint has two causes and only one of them has a known end.
#
# WHAT THEY REPLACE. Before these, a failed mint left nothing behind: the cache
# recorded the quota flag and no more, so N concurrent callers each made their own
# attempt and every later poll made another. Measured against the running cadences
# that is four getToken POSTs in the first minute and 240 in the first hour, which
# is 24 times the whole daily budget every hour. A refusal we had already been
# given throttled nothing at all.
#
# THE BASE, AND WHY IT IS TEN MINUTES (Audit 6, A01; F05 set it at 60 s). One base for
# both kinds of failure: a mint that fails, and a fresh token NJ Transit refuses,
# which for the budget is the same thing (TokenCache.reject_fresh). Under sustained
# failure of either kind, from a cold cache, the probes fall at 0, 600, 1800 and
# 3600 s, one getToken POST each, and those four are DAILY_MINT_ALLOWANCE: the day's
# allowance spans the first hour of a fault, so an outage of under an hour at deploy
# time heals at the next probe rather than costing the rest of the Eastern day. A
# process that has already minted on that Eastern date has fewer: with one spent (the
# held token's own mint, say) its last probe is at 1800 s, with two at 600 s. At F05's
# 60 s the four fell at 0, 60, 180 and 420 s, and a seven-minute outage on a cold
# cache spent the day. The price is at the other end: a single failed mint on a cold
# cache now waits ten minutes for its retry, which a six-hourly contract monitor never
# sees, while the feed cache keeps serving what it already has. Ten minutes is still
# longer than any cadence that can drive a mint (the 20 s feed poll, the 60 s alert
# poll), which is the one thing a base must be: a hold shorter than the next poll
# changes nothing.
#
# OVERRIDABLE through a strict seam (env_seams.positive_seconds) for one reader, the
# contract tier, which runs in real time and has to see a hold lapse inside a scenario.
# A value that would switch the ladder off refuses to start.
MINT_COOLDOWN_BASE_S = env_seams.positive_seconds("NJT_MINT_COOLDOWN_BASE_S", 600.0)

# THE CAP, AND WHY IT IS HALF AN HOUR. Doubling with no ceiling would eventually
# park the retry a day out, so a five-minute NJ Transit outage would leave the
# layer dark long after the upstream came back. The cap is the longest we will
# ever wait, so it is also the WORST RESIDUAL DARKNESS after a real outage ends:
# thirty minutes means the heal lands inside the same hour the outage stopped, and
# inside the same six-hourly contract-monitor cycle, so an operator reading the
# next run sees a healthy deployment rather than the tail of a fixed problem.
#
# THAT BOUND HOLDS WHILE THE ALLOWANCE LASTS, and no longer past it (Audit 6, A01).
# Once DAILY_MINT_ALLOWANCE is spent with no token held, the darkness runs to the next
# Eastern midnight however soon the fault clears. From a cold cache the four probes
# take the first hour (0, 600, 1800 and 3600 s), so a fault that ends inside it heals
# at the next probe, and one still running at the fourth leaves the tokenless process
# dark for the rest of the Eastern day; for a process that already minted that day the
# last probe comes sooner (1800 s with one mint spent, 600 s with two). A held token,
# over-age or not, keeps serving through it. That is the allowance's price, measured
# in arm 5 of docs/reviews/audit-2026-10-12/a01_njt_fresh_token_rejected.py.
#
# WHAT THE PAIR COSTS IN ATTEMPTS, which is the number this exists to space. A day of
# unbroken failure walks 600, 1200, 1800, 1800, ... which is 49 getToken POSTs in 24
# hours (52 at F05's 60 s base, 5760 before F05), still more than DAILY_MINT_LIMIT:
# the ladder spaces attempts and cannot bound them, and until Audit 6 the first thing
# that stopped it was NJ Transit's own quota refusal, after the monitor's share was
# gone. DAILY_MINT_ALLOWANCE now stops it first, at four in an Eastern day (0, 600,
# 1800 and 3600 s, then held to midnight), and the quota arm below is left for the
# case the allowance cannot see: an account spent elsewhere.
MINT_COOLDOWN_CAP_S = 1800.0

# A BASE OUTSIDE ONE SECOND TO THE CAP IS REFUSED, NOT CLAMPED OR HONOURED. Above the
# cap, _start_cooldown takes min(step, cap), so an override of 7200 would quietly probe
# every 1800 s, four times as often as it says. Below a second the ladder is off in all
# but name: a base of 1e-9 needs thirty doublings to reach one second, and nothing but
# the allowance spaces the attempts meanwhile. Checked here, beside the cap, because
# env_seams has no cap to compare against.
if not 1.0 <= MINT_COOLDOWN_BASE_S <= MINT_COOLDOWN_CAP_S:
    raise ValueError(
        "NJT_MINT_COOLDOWN_BASE_S must be between 1s and MINT_COOLDOWN_CAP_S "
        f"({MINT_COOLDOWN_CAP_S:g}s), got {MINT_COOLDOWN_BASE_S:g}"
    )

# HOW FAR PAST MIDNIGHT A QUOTA HOLD REACHES. The reset is an Eastern midnight
# (observed 2026-09-03), and a retry aimed exactly at it is aimed at a boundary we
# do not own the clock for: a second early, NJ Transit refuses again, and the arm
# below holds until the NEXT midnight, which is a full day of darkness bought for
# one second of impatience. A minute of margin makes that trade impossible and
# costs a minute.
MINT_QUOTA_RESET_MARGIN_S = 60.0

# The reset's own timezone, named once. zoneinfo rather than a fixed -5/-4 offset
# because the boundary is a LOCAL midnight and the offset changes twice a year;
# a hardcoded one would be an hour wrong for a third of the year and would move the
# hold across the boundary in the wrong direction each March.
_EASTERN = ZoneInfo("America/New_York")


# How much of an upstream body a failure message may quote. Enough to carry
# {"errorMessage":"..."} whole, short enough that an HTML error page does not
# become a log entry.
_BODY_QUOTE_CHARS = 200


class NjtNotConfigured(RuntimeError):
    """No NJT credentials in the environment.

    A DISTINCT STATE, never an error to retry. Raised before any network I/O, so
    an unconfigured deployment makes zero requests: no mint, no data fetch, no
    retry loop hammering an endpoint that would reject it anyway. The warmup turns
    this into its own status string, which /api/status publishes separately from
    "failed" so an operator can tell "nobody gave me credentials" from "the
    upstream is broken".
    """


class NjtAuthError(RuntimeError):
    """Credentials or a token were rejected by the upstream.

    Covers a mint that failed, a request that still read as invalid-token after
    exactly one re-mint, and one that read as invalid-token on a fresh token (see
    FRESH_TOKEN_S), which buys no re-mint. Each way the attempt is over; the caller's
    schedule decides the next one.
    """


class NjtMintQuotaError(NjtAuthError):
    """getToken refused because the account's daily mint budget is spent.

    A SUBCLASS, NOT A SIBLING, AND THAT IS THE DESIGN RATHER THAN A SHORTCUT.
    Every caller that already handles a failed mint keeps handling this one
    unchanged: the warmup's rung schedule, the poller's NjtAuthError arm,
    njt_static's lenient empty result. NJ Transit alone degrades, the attempt is
    over, and nothing anywhere retries harder. The distinct type buys exactly one
    thing, the ability to SAY SO: TokenCache.mint_quota_refused records it and
    /healthz publishes it, so an operator can tell a spent budget from an NJ
    Transit outage without reading a log.

    RETRYING HARDER IS THE ONE REAL MISTAKE AVAILABLE HERE, and it is worth naming
    because it is tempting: this refusal is not the upstream failing, so it reads
    as recoverable. It is not, for the rest of the Eastern day, and every attempt
    made while waiting is spent against the very budget it is waiting on.
    """


class NjtMintCooldownError(NjtAuthError):
    """A mint was NOT attempted, because a recent one failed and the cooldown holds.

    THE ONLY ERROR IN THIS MODULE THAT REPORTS SOMETHING WE DID NOT DO. Every other
    failure here is the upstream's answer to a request we made; this one is raised
    before any request exists, which is the entire point of it. Audit 5 (F05)
    measured the alternative: a failed mint left no shared state, so N concurrent
    callers each made their own attempt and every later poll made another, four in
    the first minute and 240 in the first hour against a budget of ten a day.

    A SUBCLASS OF NjtAuthError FOR THE REASON NjtMintQuotaError IS ONE. Every
    caller that already handles a failed mint keeps handling this one unchanged:
    the warmup rungs, the poller arms, njt_static's lenient empty result. The
    distinct type buys the ability to SAY SO, and `remaining_s` is what it says
    with, so /api/status can name a cooldown and its remaining seconds instead of
    reporting a credential problem nobody has.

    THE MESSAGE CARRIES THE FAILURE THAT STARTED THE COOLDOWN, and carrying it is
    safe: every message this module raises from a mint is built from a status code,
    an exception type name or one of its own constants, never from a getToken body
    (Audit 4, F3). So the reason can be repeated for the whole window without the
    body it never contained becoming quotable now.
    """

    def __init__(self, message: str, *, remaining_s: float) -> None:
        super().__init__(message)
        # The exact seconds left, unrounded, beside the message's whole-second
        # rendering: a surface that wants to do arithmetic on it (a status block, a
        # test asserting the ladder doubled) should not have to parse prose.
        self.remaining_s = remaining_s


class NjtMintAllowanceError(NjtAuthError):
    """A mint was NOT attempted, because this process has spent its daily allowance.

    THE SECOND ERROR THAT REPORTS SOMETHING WE DID NOT DO, and deliberately not a
    subclass of NjtMintCooldownError: a cooldown is a fault being waited out, this is
    a budget decision that holds until the next Eastern midnight whatever NJ Transit
    is doing, and the two (and the quota refusal, which is NJ Transit's own word)
    must be tellable apart everywhere they are caught. A subclass of NjtAuthError for
    the reason the other two are: every caller that handles a failed mint already
    handles this one.

    `remaining_s` is the seconds until the hold lapses, unrounded, as on
    NjtMintCooldownError.
    """

    def __init__(self, message: str, *, remaining_s: float) -> None:
        super().__init__(message)
        self.remaining_s = remaining_s


class NjtUpstreamError(RuntimeError):
    """A non-auth failure from the NJT API: a real 5xx, a 4xx, an unusable body.

    Kept separate from NjtAuthError precisely because the probe's 500 makes the two
    look alike on the wire. A NjtUpstreamError never triggers a mint.
    """


# A transport takes (url, form fields, timeout) and returns (status, body). Injected
# everywhere so tests exercise the whole auth dance without a socket, and so the
# monitor and the loader share one shape.
Transport = Callable[[str, dict[str, str], float], Awaitable[tuple[int, bytes]]]


def credentials(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(username, password) from the environment, or None when NJT is unconfigured.

    Read through os.environ (or an injected mapping) rather than captured at import
    so a test can set them without reloading the module. env_seams is imported
    above, and importing it runs load_dotenv, so a .env value is visible here
    without depending on which module happened to import first (the same
    import-order trap env_seams' docstring describes).
    """
    source = os.environ if env is None else env
    username = (source.get(USERNAME_VAR) or "").strip()
    password = (source.get(PASSWORD_VAR) or "").strip()
    if not username or not password:
        return None
    if username == _PLACEHOLDERS[USERNAME_VAR] or password == _PLACEHOLDERS[PASSWORD_VAR]:
        return None
    return username, password


def is_configured(env: dict[str, str] | None = None) -> bool:
    """Whether NJT has credentials at all. The warmup's short-circuit reads this
    before it starts a retry loop, so an unconfigured deployment never enters one."""
    return credentials(env) is not None


def is_auth_error(status: int, body: bytes) -> bool:
    """Is this response the probe's invalid-token shape, and ONLY that shape?

    THE EVIDENCE (probe, 2026-08-05, both the 02:37 EDT overnight run and the
    18:15 EDT rush run): a request carrying a dead token comes back as

        HTTP 500  {"errorMessage":"Invalid token."}

    not 401 and not 403. So the sniff cannot key on the status code alone, and it
    must not: NJ Transit also serves genuine 500s, and treating those as auth
    failures would spend a mint out of ten a day, against a counter we cannot
    read, on every real outage.

    THE ASYMMETRY IS DELIBERATE AND IT POINTS THIS WAY ON PURPOSE. A false
    POSITIVE (a real outage read as an auth failure) costs a MINT, repeatedly,
    against a cap we cannot measure and cannot raise. A false NEGATIVE costs one
    failed attempt, and the caller's rung schedule tries again while the loader
    keeps serving its cached archive. So the match is narrow: status 500, a body
    that parses as a JSON object, and an errorMessage of exactly "Invalid token."
    after stripping surrounding whitespace. Anything else is not this.

    CASE IS IGNORED, and that is the one place narrowness buys nothing. The probe
    recorded a capital I and a lowercase t twice, but "Invalid Token." is the same
    rejection typed differently, and matching it costs no specificity at all: a
    genuine NJ Transit 500 carries an entirely different sentence, not the same one
    in another case. Every other part of the shape stays exact.

    THE FALSE-NEGATIVE PRICE IS ONLY TRUE BECAUSE OF MAX_TOKEN_AGE_S. Before that
    ceiling existed, a rejection this missed left the dead token in the
    process-wide cache with nothing to expire it, and the retry loop re-posted it
    forever: the attempt failed, but it failed the same way on every rung until the
    process restarted. The ceiling is what makes "the schedule tries again" mean
    "and eventually succeeds". Narrowing this sniff without that ceiling in place
    would be reintroducing that bug.

    A CONTROL PINS BOTH DIRECTIONS. tests/contract/test_contract_api.py runs a
    same-class 500 with a different body and asserts it neither mints nor heals,
    beside the scenario where the probe's exact body does both; the hermetic
    sniff tests do the same at the function boundary. Loosening this to "any 500"
    must fail that control, and the mutation check in the 15a handoff verifies it
    does.
    """
    if status != 500:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        # A 500 whose body is not JSON at all (an HTML error page, a proxy's
        # plaintext) is an ordinary outage, not this.
        return False
    if not isinstance(payload, dict):
        return False
    message = payload.get("errorMessage")
    if not isinstance(message, str):
        return False
    return message.strip().casefold() == "invalid token."


def is_mint_quota_error(status: int, body: bytes) -> bool:
    """Is this getToken response the daily-cap refusal, and ONLY that?

    THE EVIDENCE (observed 2026-09-02): the eleventh mint of an Eastern day comes
    back as

        HTTP 500  {"errorMessage":"Daily usage limit ..."}

    the same status an ordinary NJ Transit fault carries and the same status the
    invalid-token rejection carries, which is why this has to read the body at all.
    Without it a spent budget is reported as "getToken returned HTTP 500", which is
    indistinguishable from the endpoint being down and sends an operator looking
    for an outage that is not there.

    THE ONE PLACE THE getToken BODY MAY BE READ, AND IT IS STILL NOT A QUOTE (F3).
    The comparison is against _QUOTA_PREFIX, a literal in this file; what comes out
    is a bool; the caller then raises MINT_QUOTA_MESSAGE, also a literal in this
    file. Nothing from the response reaches a message, so a refusal whose body
    happened to carry a live token past that prefix cannot leak it, which is the
    property the F3 canary tests hold.

    NARROW, THE WAY is_auth_error IS NARROW, though the asymmetry points elsewhere.
    A false positive here costs no mint: it misreports a real NJ Transit outage as
    a spent budget, which is a wrong /healthz code and a wrong answer for whoever
    is on call. A false negative costs nothing at all beyond today's behavior, a
    mint failure named by its status. So the match stays exact: status 500, a body
    that parses as a JSON object, an errorMessage that is a string, and that string
    beginning with the observed prefix after stripping and case-folding.
    """
    if status != 500:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    message = payload.get("errorMessage")
    if not isinstance(message, str):
        return False
    return message.strip().casefold().startswith(_QUOTA_PREFIX)


def seconds_to_next_eastern_midnight(wall_now: float) -> float:
    """How long from `wall_now` (a Unix timestamp) until the next America/New_York
    midnight, plus MINT_QUOTA_RESET_MARGIN_S.

    ONE OF TWO PLACES A WALL CLOCK IS READ IN THIS MODULE, and it is read once per
    hold that runs to midnight (a quota refusal, or a spent allowance), to measure a
    LENGTH. The other is TokenCache._spend_allowance, which reads it once per mint
    attempt to name the Eastern DATE. Everything that then asks "is the hold over"
    compares monotonic instants, so a container whose clock steps at boot or under an
    NTP correction cannot shorten a hold that is already running; the worst it can do
    is make one hold the wrong length at the moment it starts. Monotonic time cannot
    do this job on its own: it has no idea what a midnight is.

    THE NEXT MIDNIGHT IS NAMED, NOT MEASURED. The local DATE plus one day, combined
    with the zone, is the instant we want by construction; adding a fixed 86400 to
    the timestamp would be the same thing only on days that are 24 hours long. Two
    a year are not, and both directions are wrong in a way that costs: on the March
    day the hold would run an hour past the reset (an hour of avoidable darkness),
    and on the November day it would fall an hour short and spend a mint to be told
    the same refusal again. A fall-back day is 25 hours and a hold across it is 25
    hours; the March day is 23, and both directions are pinned by
    test_a_quota_hold_across_a_dst_boundary_is_the_length_of_that_day.
    """
    now = datetime.fromtimestamp(wall_now, tz=_EASTERN)
    midnight = datetime.combine(now.date() + timedelta(days=1), time_of_day(0, 0), tzinfo=_EASTERN)
    return max(midnight.timestamp() - wall_now, 0.0) + MINT_QUOTA_RESET_MARGIN_S


def _quote(body: bytes) -> str:
    """A short, printable slice of an upstream body for a failure message.

    Bodies are the upstream's words, never ours: the request's credentials live in
    the multipart body we SEND, which never appears here. Truncated so an HTML
    error page cannot become a log entry, and decoded with replacement so a binary
    body cannot raise inside an error path.

    THE ONE EXCEPTION TO THAT PREMISE IS THE getToken BODY, and this must never be
    called on it (Audit 4, F3). A data endpoint's body is a feed or an error page;
    the getToken body is the secret itself, under whatever key or shape NJ Transit
    chooses, and a message built from it would put the live token in the logs the
    moment the upstream answered in a shape extract_token does not recognize. The
    mint path reports the status, the byte length or the key names instead; see
    _token_body_shape.
    """
    text = body[: _BODY_QUOTE_CHARS * 4].decode("utf-8", errors="replace").strip()
    text = " ".join(text.split())
    return text[:_BODY_QUOTE_CHARS] if text else "(empty body)"


async def _httpx_post(
    url: str,
    form: dict[str, str],
    timeout_s: float,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int, bytes]:
    """The default transport: one POST as multipart/form-data.

    files= RATHER THAN data=, and it is load-bearing. httpx sends a `data` mapping
    as application/x-www-form-urlencoded; the RailData endpoints want
    multipart/form-data, which is what the probe used and what they answer. Passing
    each field as (None, value) is httpx's documented way to put a plain text field
    into a multipart body without inventing a filename.

    The status is returned rather than raised on, because the caller has to INSPECT
    a 500 before deciding what it is (see is_auth_error); raise_for_status here
    would throw away the body that distinguishes an auth failure from an outage.

    follow_redirects=False, AND IT IS THE POINT, NOT A DEFAULT (Audit 4, F2). Every
    body this sends carries a secret: the username and password on a mint, the
    token on every other call. httpx re-sends a POST body on a 307 or 308, to a
    different origin included, so following a redirect would deliver the credential
    to whatever host the Location named. The static loaders follow redirects on
    their GETs because two sources 30x to their zip; that reasoning is about a body
    the upstream SENDS and does not extend to one we send. A 3xx is returned as the
    status it is, and the caller treats it as a non-200: NjtAuthError from mint,
    NjtUpstreamError from _body_or_raise. is_auth_error can never read a 3xx as an
    auth failure (it keys on status 500), so a redirect never spends the one re-mint.

    `transport` is an injection seam so a test can prove the redirect rule against a
    real httpx client without a socket. None means the real network.
    """
    async with httpx.AsyncClient(
        timeout=timeout_s, follow_redirects=False, transport=transport
    ) as client:
        resp = await client.post(url, files={key: (None, value) for key, value in form.items()})
        return resp.status_code, resp.content


def _token_body_shape(payload: object) -> str:
    """What a getToken body that carried no recognizable token looked like, WITHOUT
    any of its content: the JSON type, and for an object its sorted key NAMES.

    Key names only, never values, because a value under an unrecognized key is the
    likeliest place the real token would be sitting. The names are what an operator
    needs to extend the accepted spellings in extract_token, and they are bounded so
    an absurd object cannot become a log entry either.
    """
    if isinstance(payload, dict):
        if not payload:
            return "an empty JSON object"
        names = ", ".join(sorted(str(key) for key in payload))
        if len(names) > _BODY_QUOTE_CHARS:
            names = names[:_BODY_QUOTE_CHARS] + "..."
        return f"a JSON object with keys [{names}]"
    if isinstance(payload, list):
        return f"a JSON array of {len(payload)} items"
    if isinstance(payload, str):
        return "an empty JSON string"
    if payload is None:
        return "JSON null"
    return f"a JSON {type(payload).__name__}"


def extract_token(body: bytes) -> str:
    """The token out of a getToken response body.

    The probe recorded the token's SHAPE (~21 characters) but not the JSON key
    holding it, so this accepts the documented spellings case-insensitively rather
    than pinning one that was never observed on the wire. A bare JSON string body
    is accepted too, for the same reason. Everything else raises NjtAuthError,
    which is the honest outcome: we asked for a token and did not get something we
    recognize as one.

    THE BODY IS NEVER QUOTED INTO THE ERROR (Audit 4, F3), because on exactly these
    two paths it may BE the token: under a key this function does not recognize, or
    as a bare string that is not valid JSON. The non-JSON message carries the byte
    length only; the no-key message carries the JSON shape and the key names only.
    Both messages reach the Railway log through pollers and njt_static, and the
    monitor's job summary through _NjtToken, so this is the line that decides
    whether a live token lands there.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise NjtAuthError(
            f"getToken returned a non-JSON body ({len(body)} bytes, not quoted)"
        ) from exc
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in ("usertoken", "token"):
                if isinstance(value, str) and value.strip():
                    return value.strip()
    raise NjtAuthError(
        f"getToken response carried no token: {_token_body_shape(payload)} (values not quoted)"
    )


async def mint(
    *,
    transport: Transport | None = None,
    env: dict[str, str] | None = None,
    url: str | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> str:
    """Exchange the configured username and password for a fresh token.

    CALLED ONLY THROUGH A TokenCache, never directly by a request path. That is the
    whole conservation story: the cache's single-flight lock is what turns N
    concurrent callers needing a token into exactly ONE mint, and a direct call
    would route around it. It is public because the monitor mints too (once, under
    the same discipline) and because a test needs to count invocations.
    """
    creds = credentials(env)
    if creds is None:
        raise NjtNotConfigured(
            f"{USERNAME_VAR}/{PASSWORD_VAR} are not set; NJ Transit is not configured"
        )
    username, password = creds
    post = transport or _httpx_post
    target = url if url is not None else NJT_TOKEN_URL
    try:
        status, body = await post(target, {"username": username, "password": password}, timeout_s)
    except Exception as exc:
        # TYPE ONLY, never str(exc), for the same reason cache._sanitize_upstream
        # exists: an httpx error's str embeds the request, and this one carried
        # credentials. The type name is enough to tell a refused connection from a
        # timeout, which is all an operator needs from a mint failure.
        raise NjtAuthError(f"getToken transport failed ({type(exc).__name__})") from exc
    if status != 200:
        # THE QUOTA ARM FIRST, because the refusal it recognizes is an HTTP 500 and
        # would otherwise leave here as "getToken returned HTTP 500", the same
        # sentence a dead endpoint produces. The message raised is the module's own
        # constant and carries nothing from the body, so this stays inside the F3
        # rule below rather than being an exception to it.
        if is_mint_quota_error(status, body):
            raise NjtMintQuotaError(MINT_QUOTA_MESSAGE)
        # STATUS ONLY, never the body (Audit 4, F3). A getToken body is the one
        # response in this system that may be the secret itself, and a non-200
        # does not change that: nothing here knows what NJ Transit puts under a
        # 3xx, a 4xx or a 5xx from this endpoint, so none of it is quoted. A 3xx
        # lands here too, because _httpx_post never follows a redirect (F2).
        raise NjtAuthError(f"getToken returned HTTP {status}")
    token = extract_token(body)
    logger.info("Minted an NJ Transit RailData token (%d chars)", len(token))
    return token


class TokenCache:
    """One token, and the single-flight lock that mints it.

    THE INVARIANT: however many callers find the cache empty at once, exactly one
    mint happens and every caller gets that token. The double check around the lock
    is what does it. Without the second check inside the lock, every waiter would
    mint in turn as it acquired, which is the exact shape that exhausts a ten-a-day
    cap the first time the app restarts under load.

    invalidate() is a COMPARE-AND-CLEAR rather than a plain clear, and that matters
    under concurrency: caller A can meet an invalid-token response for token T1
    while caller B has already minted T2 and stored it. A blind clear would throw
    away B's brand-new token and force a third mint. Clearing only if the cached
    token is still the one that failed keeps the arithmetic honest.

    A TOKEN IS ALSO DROPPED ONCE IT REACHES MAX_TOKEN_AGE_S, which is what makes the
    sniff's documented false-negative price true rather than aspirational. Nothing
    else expires a token: the non-auth path returns without invalidating (correctly,
    or a real outage would mint on every attempt), so without this ceiling a
    rejection is_auth_error does not recognise would leave the dead token here for
    the life of the process while the retry loop re-posted it forever. See the
    constant for why twelve hours and what it does and does not claim.

    A FAILED MINT IS SHARED, EXACTLY AS A SUCCESSFUL ONE IS (Audit 5, F05). The
    lock always serialized callers; until F05 it shared only a TOKEN, so the second
    check inside it found nothing after a failure and every waiter minted in turn.
    N concurrent callers therefore made N attempts, and so did every poll after
    them. The cache now records the failure and a NOT-BEFORE instant, and until
    that instant every get() raises NjtMintCooldownError WITHOUT calling the mint
    callback, so a burst of any size costs one getToken POST and a window of polls
    costs none. The waiters are covered by the same re-check inside the lock: a
    caller that queued while the holder's mint was in flight sees the shared
    failure the holder just recorded, not an empty cache.

    TWO POLICIES, CHOSEN BY THE EXCEPTION TYPE, because a failed mint has two
    causes and only one of them has a known end. A quota refusal
    (NjtMintQuotaError) is NJ Transit telling us the answer for the rest of the
    Eastern day, so the hold runs to the next Eastern midnight and there is nothing
    to discover before then. Everything else (a transport failure, a non-200, a
    body carrying no token we recognise) is a fault of unknown length, so it takes
    bounded exponential backoff from MINT_COOLDOWN_BASE_S to MINT_COOLDOWN_CAP_S,
    and a token NJ Transit ACCEPTS resets it (confirm). NJ TRANSIT'S ANSWER TO BAD
    CREDENTIALS IS NOT KNOWN and is not guessed at here: no probe has ever sent a
    wrong password, so whatever shape it wears falls in the second arm and is
    retried on the ladder rather than latched on a rule invented for it.

    A REFUSED FRESH TOKEN IS A FAILED MINT, AND THE STREAK ENDS ON ACCEPTANCE
    (Audit 6, A01). A mint that returns a token proves only that getToken answered.
    When NJ Transit then refuses that token, njt_post calls reject_fresh, which drops
    it (compare-and-clear, as invalidate) and starts the same ladder from the same
    base, MINT_COOLDOWN_BASE_S. A working mint still ends the hold it is in, but
    only confirm(), on a 200 for the token it issued, ends the streak: a ladder reset
    by every mint could never climb past its base when every token is refused.

    THE DAILY ALLOWANCE BOUNDS WHAT THE LADDER ONLY SPACES (Audit 6, A01). Inside the
    lock, immediately before a POST is counted, the attempt is charged to the
    Eastern date it belongs to; once DAILY_MINT_ALLOWANCE are spent the attempt makes
    no request and the cache holds to the next Eastern midnight plus the margin, with
    its own error (NjtMintAllowanceError) and its own flag (mint_allowance_spent).

    AN OVER-AGE TOKEN OUTLIVES A FAILED RE-MINT, and this is the half that keeps a
    cooldown from becoming an outage. MAX_TOKEN_AGE_S is a BUDGET rule (how long we
    will hold a token without proof it still works), not a validity rule: NJ
    Transit itself says when a token is dead, through the "Invalid token." shape.
    So when a proactive re-mint fails, the cached token keeps being served for the
    length of the cooldown instead of being thrown away for a fresh one that cannot
    be had. What takes NJ Transit off the map is a REJECTED token (njt_post
    invalidates it, so there is nothing left to serve) or a hold that begins with
    no token at all, such as a cold start. Both of those raise; a merely over-age
    one does not. The allowance hold serves the same way: a held token, over-age or
    not, keeps serving until the reset, and with none NJ Transit is dark until then.

    THE LOCK IS REBOUND WHEN THE RUNNING LOOP CHANGES. asyncio.Lock binds itself to
    the loop that first awaits it and raises if awaited from another, and this
    module holds a process-wide cache. The app has one loop for its whole lifetime,
    so this only matters where a process runs asyncio.run more than once: the test
    suite, which gets a fresh loop per case. The rebind is safe because the check
    is synchronous (no await between reading and replacing), so nothing can be
    waiting on the lock being replaced, and the TOKEN is deliberately kept across
    the rebind because a token is not loop-bound.
    """

    def __init__(
        self,
        max_age_s: float = MAX_TOKEN_AGE_S,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        daily_allowance: int = DAILY_MINT_ALLOWANCE,
    ) -> None:
        self._token: str | None = None
        self._minted_at: float = 0.0
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_age_s = max_age_s
        # MONOTONIC, not the wall clock: a container whose clock steps backwards at
        # boot (or forwards under an NTP correction) must not be able to make a
        # token look arbitrarily fresh or arbitrarily stale.
        self._clock = clock
        # THE SECOND CLOCK, AND IT ANSWERS TWO CALENDAR QUESTIONS AND NOTHING ELSE:
        # how far away the next Eastern midnight is (read once when a quota refusal
        # or a spent allowance starts a hold), and which Eastern date a mint
        # attempt belongs to (read once per attempt, for the allowance). A monotonic
        # clock cannot answer either. So the wall clock measures a hold's LENGTH and
        # names a DATE; every "is the hold over" comparison below is monotonic, so a
        # clock step cannot cut a hold short. Injected for the same reason `clock`
        # is: a test asserts the midnight boundary by naming a timestamp, never by
        # waiting for one.
        self._wall_clock = wall_clock
        # THE COOLDOWN, on the monotonic timeline. None means no hold has started
        # since the last working mint; a value in the past means the window has
        # lapsed and the next caller may attempt. Deliberately NOT cleared when it
        # lapses: clearing is a write, reading is not, and the only writers here are
        # a mint attempt's outcomes, a refused fresh token (reject_fresh) and a spent
        # allowance.
        self._cooldown_until: float | None = None
        # WHY the cooldown is running, in the failing mint's own words, so the
        # window's error can say what started it rather than only that something
        # did. Safe to repeat: every message this module raises from a mint is
        # built from a status code, an exception type name or one of its own
        # constants, never from a getToken body (Audit 4, F3).
        self._cooldown_reason = ""
        # THE BACKOFF LADDER, as the last delay rather than a step count, so the
        # doubling and the cap are one expression and nothing can overflow while a
        # process sits in a long failure. None means no streak: the next non-quota
        # failure starts at MINT_COOLDOWN_BASE_S.
        #
        # A QUOTA REFUSAL LEAVES IT ALONE, neither advancing nor resetting it. Only
        # a token NJ Transit ACCEPTED proves the ladder's streak is over (see
        # confirm), and a refusal is not a fault to back off from; carrying a streak
        # across one is the conservative direction, which is the right one for a
        # budget.
        self._backoff_s: float | None = None
        # THE NEWEST TOKEN ISSUED THAT NJ TRANSIT HAS NOT YET ACCEPTED, or None. Two
        # rules key on it (Audit 6, A01). confirm() ends the streak only for this
        # token, so a 200 on an older token held through a failed re-mint cannot
        # reset a ladder its own mint never climbed. reject_fresh() starts a hold
        # only for this token, so two consumers refused on the same token step the
        # ladder once, and a stale refusal never puts a newer token under a hold.
        self._unconfirmed: str | None = None
        # THE NEWEST TOKEN ISSUED, AND WHEN, UNTIL NJ TRANSIT ACCEPTS IT (Audit 6, A01):
        # what is_fresh reads. Unlike _unconfirmed, a refusal does not clear it, so a
        # second consumer refused on a token the first has already reported is still
        # refused on a fresh token, and does not re-mint.
        self._fresh: tuple[str, float] | None = None
        # TWO COUNTERS, AND THE DISTINCTION IS THE WHOLE POINT OF HAVING BOTH.
        # `mints` counts tokens successfully ISSUED; `mint_requests` counts getToken
        # POSTS ACTUALLY SENT. A failed mint raises before the token is stored, so it
        # advances the second and not the first, and it is the second that spends
        # DAILY_MINT_LIMIT. Asserting conservation on `mints` alone is blind to
        # exactly the worst path: a loop that mints unsuccessfully forever.
        self.mints = 0
        self.mint_requests = 0
        # WHETHER THE MOST RECENT MINT ATTEMPT WAS REFUSED FOR THE DAILY CAP, and
        # nothing more than that: not a count, not a timestamp, not a prediction
        # about the rest of the day. /healthz turns it into a degraded code, which
        # is what lets an operator tell a spent budget from an NJ Transit outage.
        #
        # HERE RATHER THAN ON app.state, because THIS is the object every mint in
        # the app goes through (see the class docstring and mint's), so recording
        # it here is complete by construction. Plumbing it through the warmup and
        # the poller instead would mean two places to remember and a third the day
        # a new consumer appears.
        #
        # SELF-CLEARING, WITH NO DATE ARITHMETIC. It is set on a quota refusal and
        # cleared by the next mint attempt that answers anything else, so the
        # Eastern-midnight reset needs no clock here: the first mint that succeeds
        # after it clears the flag as a side effect of working.
        self.mint_quota_refused = False
        # THE DAILY ALLOWANCE (Audit 6, A01; see DAILY_MINT_ALLOWANCE). A count of
        # getToken POSTs sent, keyed by the America/New_York date of the attempt that
        # sent them. Only a new day resets it: not a working mint, not a 200, not a
        # hold lapsing. An instance argument so a test can isolate the ladder from it
        # and pin the two separately; the process-wide cache takes the seam's value.
        self._allowance = daily_allowance
        self._allowance_day: date | None = None
        self._allowance_spent = 0
        # WHICH HOLD IS RUNNING, because two of them raise different errors through
        # the same window: "allowance" while the allowance holds, "" otherwise.
        # Written wherever _cooldown_until is, so the two cannot disagree.
        self._hold_kind = ""
        # HOW MANY HOLDS THIS CACHE HAS STARTED, so a mint can tell whether one began
        # while it was in flight. reject_fresh runs outside the lock (it follows a
        # POST, not a mint attempt), so a refusal can land while another caller's
        # mint is out, and that mint returning must not erase the hold the refusal
        # has just started: the next consumer would buy a re-mint the hold refuses.
        self._holds_started = 0

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    def _live_token(self) -> str | None:
        """The cached token if there is one and it has not reached the age ceiling.

        Read on every get(), so an over-age token is treated as absent rather than
        expired on a timer: this costs nothing while the app is idle and re-mints
        lazily on the next request that actually needs one.
        """
        if self._token is None:
            return None
        if self._clock() - self._minted_at >= self._max_age_s:
            return None
        return self._token

    def peek(self) -> str | None:
        """The cached token without minting, ignoring the age ceiling. For tests and
        for logging: a caller deciding whether to USE a token wants _live_token."""
        return self._token

    def invalidate(self, stale: str | None = None) -> None:
        """Drop the cached token, but only if it is still `stale` (see the class
        docstring). Passing None clears unconditionally, which is what a reset
        wants and what no request path should use."""
        if stale is None or self._token == stale:
            self._token = None

    def cooldown_remaining(self) -> float | None:
        """Seconds left before a mint may be attempted again, or None if one may be
        attempted now.

        A PURE READ, which is why it does not clear a lapsed window. /api/status
        calls this on every snapshot, and a reader that mutated the cache would
        make the act of looking part of the behaviour being looked at.
        """
        if self._cooldown_until is None:
            return None
        remaining = self._cooldown_until - self._clock()
        return remaining if remaining > 0 else None

    def cooldown(self) -> tuple[float, str] | None:
        """The running cooldown as (seconds remaining, the same thing in words), or
        None when a mint may be attempted now.

        WHAT /api/status PUBLISHES. Without it the surface has only the poll error
        of whichever consumer happened to ask last, which says "NJ Transit is
        failing" for a window in which nothing was asked of NJ Transit at all, and
        says nothing at all during a cold start where no poller is running yet.

        THE PAIR COMES FROM ONE CLOCK READ on purpose: published separately they
        could round either side of a second apart and have the snapshot disagree
        with its own prose.
        """
        remaining = self.cooldown_remaining()
        if remaining is None:
            return None
        return remaining, self._cooldown_message(remaining)

    def _cooldown_message(self, remaining: float) -> str:
        """One sentence: what failed, and how much longer we are not asking again.

        Rounded UP, so a window with four tenths of a second left reads as one
        second rather than zero, which would say the opposite of what is true.
        """
        reason = self._cooldown_reason or "a getToken attempt failed"
        return f"{reason}; minting is in cooldown for another {math.ceil(remaining)}s"

    def _serve_through_cooldown(self, remaining: float) -> str:
        """The cached token if there is any, else the cooldown as an error.

        THE OVER-AGE TOKEN IS RETURNED DELIBERATELY, past the ceiling _live_token
        enforces. See the class docstring: the ceiling is a budget rule, and a
        budget rule cannot be worth going dark for at the exact moment we have
        proved we cannot buy a replacement. What is NOT returned is a token NJ
        Transit rejected, because njt_post invalidated it and there is nothing here
        to serve.
        """
        if self._token is not None:
            return self._token
        if self._hold_kind == "allowance":
            raise NjtMintAllowanceError(self._cooldown_message(remaining), remaining_s=remaining)
        raise NjtMintCooldownError(self._cooldown_message(remaining), remaining_s=remaining)

    def _start_cooldown(self, exc: BaseException) -> float:
        """Record a failure so nothing attempts a mint until it lapses, and return
        how long that is. One ladder, one base, one cap, for a failed mint and a
        refused fresh token alike (Audit 6, A01)."""
        if isinstance(exc, NjtMintQuotaError):
            # THE ONE FAILURE WITH A KNOWN END. NJ Transit has told us the answer
            # for the rest of the Eastern day, so the hold runs to the reset rather
            # than to a guess, and the ladder is left untouched (see __init__).
            self.mint_quota_refused = True
            hold_s = seconds_to_next_eastern_midnight(self._wall_clock())
        else:
            # EVERYTHING ELSE, INCLUDING WHATEVER BAD CREDENTIALS LOOK LIKE. Nobody
            # has ever probed that answer, so it is not special-cased on a guess:
            # an unknown fault of unknown length takes the ladder like any other.
            # A streak keeps doubling from where it is, whichever kind of failure
            # extends it: a refused token after two failed mints waits as long as a
            # third failed mint would have.
            step = MINT_COOLDOWN_BASE_S if self._backoff_s is None else self._backoff_s * 2
            hold_s = min(step, MINT_COOLDOWN_CAP_S)
            self._backoff_s = hold_s
        until = self._clock() + hold_s
        # A HOLD IS NEVER SHORTENED HERE. Only reject_fresh can arrive while one is
        # running (it follows a POST, not a mint attempt, so the gates in get() do
        # not stand in front of it), and a refusal landing inside a longer hold says
        # nothing that makes the longer hold wrong. Nor can a mint that was already
        # in flight erase one it did not see start (_holds_started).
        if self._cooldown_until is None or until >= self._cooldown_until:
            self._cooldown_until = until
            self._cooldown_reason = str(exc)
            self._hold_kind = ""
            self._holds_started += 1
        return hold_s

    def _spend_allowance(self) -> float | None:
        """Charge one getToken POST to today's allowance, or refuse it.

        Returns None when the attempt may go out (and has been counted), or the
        length of the hold it started when the allowance for this Eastern day is
        already spent. Called inside the lock, immediately before the POST is
        counted, so a burst of waiters queued behind one mint cannot each spend one.

        THE WALL CLOCK IS READ ONCE, HERE, AND ONLY FOR TWO CALENDAR ANSWERS: which
        Eastern date this attempt belongs to, and how far away the next Eastern
        midnight is. The hold itself runs on the monotonic clock like every other.
        """
        wall = self._wall_clock()
        day = datetime.fromtimestamp(wall, tz=_EASTERN).date()
        # ONLY A LATER DATE OPENS A NEW COUNT. A wall clock stepped backwards across
        # midnight (an NTP correction, a container that booted with a bad clock)
        # names an earlier date, and resetting on that would hand the process a fresh
        # allowance, and another when the clock stepped forward again. An earlier
        # date keeps counting against the later one, which only ever spends less. A
        # step FORWARD across midnight still opens the next date early, once per
        # step: a calendar question put to a wrong clock has no right answer, and
        # that error is bounded at one allowance per step.
        if self._allowance_day is None or day > self._allowance_day:
            self._allowance_day = day
            self._allowance_spent = 0
        if self._allowance_spent < self._allowance:
            self._allowance_spent += 1
            return None
        hold_s = seconds_to_next_eastern_midnight(wall)
        self._cooldown_until = self._clock() + hold_s
        self._cooldown_reason = (
            f"{MINT_ALLOWANCE_MESSAGE} ({self._allowance_spent} of {self._allowance} "
            f"getToken POSTs sent on {day.isoformat()}, Eastern)"
        )
        self._hold_kind = "allowance"
        self._holds_started += 1
        return hold_s

    @property
    def mint_allowance_spent(self) -> bool:
        """Whether the allowance hold is running: true from the attempt it refused
        until the next Eastern midnight plus the margin, and false again after, with
        nothing to reset. Read off the monotonic hold, so it clears when the hold
        lapses, which is the start of the next Eastern day for the allowance."""
        return self._hold_kind == "allowance" and self.cooldown_remaining() is not None

    def allowance(self) -> dict:
        """The allowance as /api/status publishes it: the Eastern day the count
        belongs to (None before the first attempt), the getToken POSTs sent that
        day, the limit, and whether the hold is running. A pure read: a day that has
        ended shows its own count until the next attempt opens the new one."""
        return {
            "day": self._allowance_day.isoformat() if self._allowance_day else None,
            "spent": self._allowance_spent,
            "limit": self._allowance,
            "spent_out": self.mint_allowance_spent,
        }

    def reject_fresh(self, token: str, exc: BaseException) -> None:
        """NJ Transit refused `token`, which this cache issued and NJ Transit never
        accepted: for the budget, that is a failed mint (Audit 6, A01).

        Compare-and-clear first, the rule invalidate() follows, so a newer token a
        concurrent caller minted survives. Then the ladder, never the midnight latch:
        a refused token is a fault of unknown length, and NJ Transit has said nothing
        about the day.

        ONLY FOR THE NEWEST UNACCEPTED TOKEN. Two consumers refused on the same
        fresh token both land here, and the second must not step the ladder again;
        a refusal of a token already superseded says nothing about the newer one.
        The mint flags are not touched: the mint that issued `token` worked.
        """
        self.invalidate(token)
        if token != self._unconfirmed:
            return
        self._unconfirmed = None
        self._start_cooldown(exc)

    def confirm(self, token: str) -> None:
        """NJ Transit answered a POST carrying `token` with a 200: the streak ends.

        THE STREAK ENDS ON ACCEPTANCE, NOT ON ISSUE (Audit 6, A01). A mint that
        returns a token proves only that getToken answered; a token NJ Transit then
        refuses is the failure the ladder exists to space out, and a ladder reset by
        every successful mint could never climb past its base in that shape.

        Only the newest unaccepted token ends it. A 200 on an older token, served
        through a failed re-mint's window because MAX_TOKEN_AGE_S is a budget rule,
        proves the old token works and nothing about minting, so the ladder it is
        waiting out keeps its place.
        """
        if token == self._unconfirmed:
            self._unconfirmed = None
            self._backoff_s = None
        if self._fresh is not None and self._fresh[0] == token:
            self._fresh = None

    def is_fresh(self, token: str) -> bool:
        """Whether `token` is fresh: the newest token this cache issued, not yet
        answered by NJ Transit with a 200, and minted under FRESH_TOKEN_S ago. A
        refusal does not end it. njt_post asks this of a refused token, because a
        fresh token's refusal buys no re-mint, whoever minted it (Audit 6, A01)."""
        if self._fresh is None or self._fresh[0] != token:
            return False
        return self._clock() - self._fresh[1] < FRESH_TOKEN_S

    async def get(self, mint_token: Callable[[], Awaitable[str]]) -> str:
        """The cached token, minting at most once per cooldown window.

        Raises NjtMintCooldownError, without calling `mint_token`, while a recent
        failure's window is still open and there is no token left to serve, and
        NjtMintAllowanceError the same way once the day's allowance is spent.
        """
        cached = self._live_token()
        if cached is not None:
            return cached
        held = self.cooldown_remaining()
        if held is not None:
            # BEFORE THE LOCK, so a window of polls does not even queue: the
            # cooldown is process-wide state and reading it needs no exclusion.
            return self._serve_through_cooldown(held)
        async with self._get_lock():
            # THE SECOND CHECK. Another caller may have minted while this one waited
            # for the lock; returning its token is the entire point of the lock. It
            # re-reads through the age ceiling too, so a token that aged out while
            # this caller queued is not handed back as fresh.
            live = self._live_token()
            if live is not None:
                return live
            # THE OTHER SECOND CHECK, and F05 is the whole reason it exists. The
            # caller ahead of this one may have FAILED rather than succeeded, and
            # before this line that left nothing behind: each waiter found an empty
            # cache in turn and minted its own, which is how twelve concurrent
            # callers became twelve getToken POSTs against a cap of ten a day.
            held = self.cooldown_remaining()
            if held is not None:
                return self._serve_through_cooldown(held)
            # THE ALLOWANCE (Audit 6, A01), the last thing before the POST is counted.
            held = self._spend_allowance()
            if held is not None:
                return self._serve_through_cooldown(held)
            holds_before = self._holds_started
            self.mint_requests += 1
            try:
                token = await mint_token()
            except asyncio.CancelledError:
                # A CANCELLED MINT STILL SPACES THE NEXT ONE (Audit 6, A01). The caller
                # gave up, usually on a deadline shorter than REQUEST_TIMEOUT_S (the
                # alert poll's is 20 s), but the POST had most likely gone out and been
                # charged, which is why the allowance counted it. Without a hold the
                # next poll would mint again at once, and a getToken slow enough to
                # outlast one deadline spends the whole allowance back to back. The
                # quota flag is left alone: nothing was answered. The reason is ours,
                # since str(CancelledError) is empty and the hold must say what began it.
                self._start_cooldown(
                    NjtAuthError(
                        "a getToken attempt was cancelled by its caller's deadline before "
                        "NJ Transit answered"
                    )
                )
                raise
            except Exception as exc:
                # The quota flag records what the most recent mint ATTEMPT was
                # answered, so it is settled here, at the attempt, and not in
                # _start_cooldown, which reject_fresh also reaches after a mint
                # that worked.
                self.mint_quota_refused = isinstance(exc, NjtMintQuotaError)
                hold_s = self._start_cooldown(exc)
                if self._token is None:
                    raise
                # THE CALLER WHOSE OWN RE-MINT FAILED FALLS BACK TOO, not just the
                # ones that arrive later and meet the window. This is the caller
                # that came here because the token aged out, and the age ceiling is
                # a budget rule: throwing a working token away for a replacement we
                # have just proved cannot be had would turn a failed mint into an
                # outage. The failure is not lost, it is published as the cooldown
                # (/api/status njt_mint_cooldown) and logged here, which is the
                # only place it would otherwise be silent.
                logger.warning(
                    "NJ Transit mint failed; still serving the token already held. %s",
                    self._cooldown_message(hold_s),
                )
                return self._token
            self.mint_quota_refused = False
            # A WORKING MINT ENDS THE HOLD; IT NO LONGER ENDS THE STREAK (Audit 6,
            # A01). The ladder measures consecutive failures, and a token NJ Transit
            # then refuses is one, so the streak is ended by confirm() when a POST
            # carrying this token comes back 200, not here. In F05's own path the
            # difference is nil (the mint, then the POST it was for, microseconds
            # apart); the one drift is conservative: a working mint followed by a
            # real 500 keeps the streak until a later 200, so a failed mint after it
            # continues the ladder rather than restarting it.
            #
            # ONLY THE HOLD IT WAS ATTEMPTED UNDER. A refused fresh token that landed
            # while this mint was in flight started a hold this mint knows nothing
            # about; it stays, and the token is still stored, so the consumers get
            # it and the hold refuses only a re-mint.
            if self._holds_started == holds_before:
                self._cooldown_until = None
                self._cooldown_reason = ""
                self._hold_kind = ""
            self._unconfirmed = token
            self.mints += 1
            self._token = token
            self._minted_at = self._clock()
            self._fresh = (token, self._minted_at)
            return token


# The process-wide cache. One per app, because the token is a property of the
# credentials rather than of any one caller: the static loader and (from 15b) the
# realtime pollers must share it, or each would mint on its own schedule.
TOKEN_CACHE = TokenCache()


async def njt_post(
    url: str,
    form: dict[str, str] | None = None,
    *,
    cache: TokenCache | None = None,
    transport: Transport | None = None,
    env: dict[str, str] | None = None,
    token_url: str | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> bytes:
    """POST `form` to `url` with a token attached, re-minting at most ONCE.

    The one door. Every NJT request in this repo goes through here, so the
    invalid-token dance is written once and cannot be forgotten at a new call site.

    The order, and why each step is where it is:

    1. NO CREDENTIALS, NO NETWORK. The check is first, before the cache and before
       any transport call, so an unconfigured deployment provably makes zero
       requests. NjtNotConfigured is a distinct type all the way up to the warmup.
    2. Take a token from the cache (minting once if it is cold, under the
       single-flight lock).
    3. POST. If the response is not the probe's invalid-token shape, we are done:
       a 200 returns its body, anything else raises NjtUpstreamError. A REAL 500
       LANDS HERE, and mints nothing.
    4. If it IS the invalid-token shape, whether the token is FRESH decides
       (FRESH_TOKEN_S): never accepted by NJ Transit, and minted within the span one
       call can last, by this attempt or by one beside it. A fresh token is refused
       for good: a second mint would be refused the same way, so the cache is told
       (reject_fresh) and the attempt fails without one (Audit 6, A01). Any other
       token is ordinary expiry: invalidate it, take a new one, and POST exactly
       once more.

    ONE RE-MINT PER ATTEMPT, ENFORCED STRUCTURALLY. There is no loop in this
    function. Step 4 exists once, in straight-line code, and a second invalid-token
    response after it raises NjtAuthError rather than trying again. A convention
    ("remember not to retry twice") would be one refactor away from a mint storm
    against a budget of ten a day; the absence of a loop is not.

    Worst case per attempt is therefore two mints: get() is called twice at most.
    Since Audit 6 an attempt ordinarily sends one getToken POST, and this is when. A
    fresh token refused buys no re-mint, so the one, if any, is the first get()'s.
    An expired token refused buys the one re-mint, and the first get() sent nothing
    for it, so the one is the second's; the token-expiry contract scenario asserts
    that case. Two are sent only when the first get() sends a POST and the token it
    hands back is no longer fresh when it is refused, which takes one of three
    things. NJ Transit accepts this attempt's new token for a concurrent caller and
    then refuses it here, which reads as expiry. Or the POST fails, an over-age token
    is served through it, and the hold the failure opened lapses during the data
    POST: at the shipped base only a quota hold can be that short, in the last minute
    before Eastern midnight. Or, under a base override shorter than one request (one
    contract scenario runs at 1 s), a newer mint supersedes the token inside the
    attempt. The retry CADENCE after a failed attempt is the caller's schedule,
    never a tight loop here.

    ACROSS ATTEMPTS, since Audit 6 (A01): when a fresh token is refused, the
    attempt's own or the re-mint's, the cache is told (reject_fresh), drops it and
    holds, so the attempts after it are refused without a request until the ladder
    lapses, and two consumers refused on the same token step the ladder once.
    However the attempts fall, one process sends at most DAILY_MINT_ALLOWANCE
    getToken POSTs in an Eastern day; an attempt whose mint the allowance refuses
    raises NjtMintAllowanceError instead.
    """
    if not is_configured(env):
        raise NjtNotConfigured(
            f"{USERNAME_VAR}/{PASSWORD_VAR} are not set; NJ Transit is not configured"
        )
    fields = dict(form or {})
    token_cache = cache if cache is not None else TOKEN_CACHE
    post = transport or _httpx_post

    async def _mint() -> str:
        return await mint(transport=post, env=env, url=token_url, timeout_s=timeout_s)

    def _refused_fresh(token: str, status: int, body: bytes) -> NjtAuthError:
        # Audit 6, A01: for the budget a refused fresh token is a failed mint, so the
        # next poll is held instead of buying another one.
        refused = NjtAuthError(
            f"NJ Transit rejected a freshly minted token: HTTP {status}: {_quote(body)}"
        )
        token_cache.reject_fresh(token, refused)
        return refused

    token = await token_cache.get(_mint)
    status, body = await _post(post, url, fields, token, timeout_s)
    if not is_auth_error(status, body):
        accepted = _body_or_raise(status, body)
        token_cache.confirm(token)
        return accepted
    if token_cache.is_fresh(token):
        # THE FUTILE RE-MINT, NOT MADE. NJ Transit refused a fresh token, so the next
        # one would be refused the same way: one probe of the fault, one getToken
        # POST, then the ladder.
        #
        # FRESH, NOT MINTED BY THIS CALL. A token another attempt minted moments ago
        # and nobody has seen accepted is just as fresh; read as held, it bought the
        # futile re-mint, and the minter's own refusal landing during it stepped the
        # ladder twice for one fault. A consumer refused on a token another has
        # already reported is refused on a fresh token too, and reject_fresh steps
        # nothing for it a second time.
        raise _refused_fresh(token, status, body)

    # THE ONE RE-MINT, for a token that has expired: accepted before, or too old to
    # be fresh. Not a loop, not a counter, not a retry helper: one branch.
    logger.info("NJ Transit rejected the cached token; re-minting once and retrying")
    token_cache.invalidate(token)
    token = await token_cache.get(_mint)
    status, body = await _post(post, url, fields, token, timeout_s)
    if is_auth_error(status, body):
        raise _refused_fresh(token, status, body)
    accepted = _body_or_raise(status, body)
    token_cache.confirm(token)
    return accepted


async def _post(
    post: Transport, url: str, fields: dict[str, str], token: str, timeout_s: float
) -> tuple[int, bytes]:
    """One POST with the token attached as a form field (never a header: the probe
    showed the header form is simply ignored). Transport failures become
    NjtUpstreamError so a caller can tell a dead socket from a rejected token."""
    try:
        return await post(url, {**fields, "token": token}, timeout_s)
    except Exception as exc:
        raise NjtUpstreamError(f"NJ Transit request failed ({type(exc).__name__})") from exc


def _body_or_raise(status: int, body: bytes) -> bytes:
    """A 200's bytes, or a NjtUpstreamError naming the status and quoting the body.

    The URL is deliberately NOT interpolated into the message. It carries no secret
    (NJT credentials ride in the multipart body we send, never in the address), but
    static_shared.describe_failure publishes what reaches /api/status and the house
    rule at that boundary is shape, never upstream address.
    """
    if status == 200:
        return body
    raise NjtUpstreamError(f"NJ Transit returned HTTP {status}: {_quote(body)}")
