# 15a adversarial review: the adjudication record

Phase 15a (the NJ Transit static foundation and the token door under it) went
through one adversarial round over a 4,268-line diff: five worktree-isolated finder
lenses, triage, then verification (three findings got their own verifier, the rest
were batched). This is every finding raised, with its disposition. Nothing is
dropped; the one finding that was wrong is recorded as wrong, with the evidence
that refutes it.

The same five categories the A4 record uses:

- **Fixed**: real, a change shipped, and a mutation proves the change is load-bearing.
- **Deferred, with reason**: real, not fixed in this phase, and why.
- **Refuted, with evidence**: reported and false; the measurement that refutes it.
- **Refuted then overturned**: refuted at the time and later found real after all.
- **Confirmed but downgraded**: the reported behaviour reproduces, but the severity
  does not survive contact with the rest of the suite.

**17 findings raised, 16 fixed, 1 refuted.**

The lenses were aimed by the author at five named surfaces: mint conservation under
concurrency and under error, the auth sniff's specificity in both directions, the
service-date guard's boundary, the validator against a publication that is valid
GTFS but wrong for this feed, and the contract simulator's fidelity. Four of the
five produced confirmed findings. The fifth (simulator fidelity) produced F13, which
is really a finding about both counters at once.

---

## The two that could reach a rider

| # | Finding | Disposition |
| --- | --- | --- |
| F1 | `check_production`'s `static_fields` was still the pre-15a four-tuple, so the production probe reported "all static groups ready" over `njt_static="failed"`. Verified by execution: a payload with four ready groups and a failed NJT returned `production:statics PASS`. Nothing else could see it, and that is the sharp part: `check_njt_static` probes the RailData API from the RUNNER and says nothing about the deployment, and it WARN-skips when the runner has no credentials, which never fails a run. So revoked production credentials or a stuck publication were invisible to the 6-hourly monitor forever. | **Fixed**. `6f71ce5`. A map from group to acceptable states rather than a longer tuple, because NJT has a legitimate fourth state: `not-configured` must not FAIL, or every deployment that does not run NJT is permanently red. Listing the states beside the group means a sixth group forces the same decision explicitly. Three mutations killed: dropping the njt entry, making `not-configured` fail, and granting `not-configured` to a group that has no such state. |
| F3 | A rejection the exact-shape sniff missed left the dead token in the process-wide cache **forever**. `njt_post` returns through `_body_or_raise` without invalidating (correctly: a real outage must not mint on every attempt), and the cache had no expiry, so the warmup loop re-posted the same dead token until the process restarted. Measured over four attempts with a body one character off the probe's: mints stayed 1, the cached token stayed `tok-1`, the POST sequence was getToken then four getGTFS. The module's own docstring priced a false negative as "costs one failed attempt; the caller's rung schedule tries again", and that was simply untrue. | **Fixed**. `6f71ce5`. `MAX_TOKEN_AGE_S`, a ceiling on how long a token is held *without proof it still works* rather than a claim about when it expires (which is undocumented and may not be guessed). Six hours bounds the worst case at four mints a day per process and puts a self-heal inside one monitor cycle. The sniff also now ignores case, which costs no specificity because a genuine 500 carries a different sentence entirely rather than the same one recased. Two mutations killed: removing the ceiling, and reverting the case fold. |

---

## The five mediums

| # | Finding | Disposition |
| --- | --- | --- |
| F4 | A publication using `calendar.txt` the ordinary GTFS way was **confidently misdiagnosed**, three ways. Measured against a `calendar.txt` running to 2027-12-31: a removals-only `calendar_dates` was rejected as "schedules no service days"; an exception-style one whose additive rows are all past was rejected as "the schedule has expired"; and one with a single near-future add was ACCEPTED while publishing that add as the feed's end date, so `/api/status`, the warmup log and the monitor's band all reported a number 15 months early. | **Fixed**. `6f71ce5`. The service span is now the later of `calendar.txt`'s `end_date` and the latest added `calendar_dates` day, which gets all three right and asks nothing of a feed that never grows the member. Rejecting the member outright was considered and rejected: it would drop a valid feed, and this costs 15 lines. Mutation killed: ignoring `calendar.txt` in the span. |
| F5 | `check_njt_static` re-derived the credential check as bare truthiness instead of calling `njt_auth.credentials()`, so `.env.example`'s shipped placeholders (and whitespace-only values) reached the live `raildata.njtransit.com` getToken endpoint, where the app itself makes zero requests. Verified: the monitor POSTed `{'username': 'your-njt-username', ...}` while `njt_auth.is_configured()` on the same values was False. | **Fixed**. `6f71ce5`. One call, so the monitor's idea of "configured" is identical to the app's by construction. This is the file's own second guiding principle ("reuse, never reimplement") being load-bearing rather than tidy. Mutation killed on the second attempt: the first fix shipped with no test that could tell it from the bare check, and the parametrize now carries both placeholder forms and the whitespace case. |
| F6 | `_parse_calendar_dates` admitted any eight-digit string as a service day, and the guard compares lexicographically, so `20261301` (month 13) sorts above every real 2026 date. One such row anywhere in an 8,697-row table made a fully expired schedule validate. The monitor could not cover for it: it downgrades an unparseable date to WARN, and a WARN never fails a run. **A guard whose job is staleness returned the healthy answer on exactly the input it could not interpret.** | **Fixed**. `6f71ce5`. The date is really parsed and an impossible one is dropped, exactly as a malformed coordinate is dropped from stops; the max then falls back to a real date, or to None, which already raises. Mutation killed: accepting any eight digits. |
| F7 | `backend/tests/fixtures/njt_gtfs/` is not committed, so six goldens skip locally and hard-fail in CI (`CI=true` gives "43 passed, 6 errors"), and the README described the fixture as already committed. | **Fixed as documentation; the fixture itself deferred, with reason**. `6f71ce5`. The guard is working as designed; what was wrong was that nothing an operator reads said the branch is CI-red. The README now says so explicitly and names the cause: this is the only fixture in the repo that cannot be produced without an account, so no CI job and no agent can generate it. It is the standing capture handoff, carried in the PR body. |
| F8 | `test_njt_static_missing_members_fail` dropped `calendar_dates.txt`, which `_parse_zip` OPENS, so the check short-circuited in the earlier "unparseable" arm on a KeyError and never reached `_check_members`. `NJT_REQUIRED_MEMBERS` was wholly untested while the assertion appeared to pass. | **Fixed**. `6f71ce5`. Drops `agency.txt` instead: required by the monitor, opened by nothing, so it is the one member only the presence check can catch. |
| F9 | `test_njt_static_an_invalid_token_500_is_reported_as_unreachable` served a 500 with an EMPTY body, not the probe's, so it could not tell "does not re-mint on an auth 500" from "does not re-mint on anything". | **Fixed**. `6f71ce5`. Serves the probe's exact body, and additionally pins that the data POST was retried once on the same token (so the retry costs no mint). |
| F10 | `validate_njt_publication` was **equivalent to** `validate_njt_archive`: the light validator already ran `require_parsed` over all four tables plus `calendar_dates`, so the publication gate's extra `_parse_open` could be deleted without failing anything, the promised strength split did not exist, and every download paid a redundant full parse of `stop_times.txt`. | **Fixed**. `6f71ce5`. The light validator now gates the three cheap tables and the publication gate adds the one expensive one, so the difference is exactly `stop_times.txt` and is falsifiable. `load_njt_static` gained the empty-`stop_times` warning that split implies (the PATH precedent). Mutation killed: dropping the publication gate's `stop_times` check. |

---

## The nine lows

| # | Finding | Disposition |
| --- | --- | --- |
| F11 | `_parse_stops` promoted every row to a marker with no `location_type` handling, so a publication that added parent stations or entrances would place a station pin and a street-entrance pin a few metres from the platform that carries the routes, each route-less. Nothing could catch it: the monitor's stop count is a lower bound, and the identity check passes because a parent keeps the station's name. | **Fixed, in two places**. `6f71ce5`. The parser skips rows whose `location_type` is not blank or `0` (an entrance is never a stop, whatever the feed does), and the monitor WARNs when the columns appear at all, because "should the marker set become the PARENTS" is a design decision a human owes an answer to rather than something to infer at parse time. Mutation killed: placing non-boardable rows. |
| F12 | The placeholder guard covered only `NJT_USERNAME` while `.env.example` ships placeholders for both, so pasting a real username over the first line and leaving the second read as configured and entered the retry loop against the real mint endpoint. | **Fixed**. `6f71ce5`. Each field checked against its own placeholder. Mutation killed: checking the username only. |
| F13 | Both mint counters counted tokens ISSUED, not getToken requests SENT, so every "exactly one/two mints" assertion in both tiers was blind to failed-mint traffic, which spends the unpublished cap identically. Measured: five attempts against a 401ing getToken sent five POSTs while `cache.mints` read 0. | **Fixed**. `6f71ce5`. `TokenCache.mint_requests` and the simulator's `mint_requests` count POSTs; every conservation assertion in both tiers now reads the request counter. |
| F14 | `shapes.txt` was in `NJT_REQUIRED_MEMBERS` and a missing required member is a FAIL, so upstream dropping a member the loader deliberately does not require turned the 6-hourly run red and exited non-zero while the app served normally. Above what the tuple's own comment asked for ("worth a human look"), and against this file's stated band rule. | **Fixed**. `6f71ce5`. A separate `NJT_WATCHED_MEMBERS` tuple at WARN; `_check_members` gained a `status` argument because only the caller knows whether the app can serve without the member. Mutation killed: moving `shapes.txt` back to the FAIL band. |
| F15 | `REQUEST_TIMEOUT_S` (120.0) exactly equalled `static_shared.DOWNLOAD_DEADLINE_S` (120), which `_download_via_token` wraps around the whole call, so the per-request guard could never fire (the outer timeout always starts strictly earlier) and up to four requests had to fit inside one request's budget. | **Fixed**. `6f71ce5`. 30s, so four requests fit the outer budget exactly, and still more than three times the worst latency the probe measured. |
| F16 | The token-expiry scenario's "must not count as a failed download" assertions **could not fail**: `static_shared._record` clears `failed_downloads` and `last_download_error` on every promotion, so both read zero after any successful attempt, re-minted or not. | **Fixed**. `6f71ce5`. The falsifiable claim is on the wire: exactly two getGTFS POSTs is what "recovered inside one attempt" looks like, and a loader that read the 500 as an outage would climb that count while the mint count stayed at 1. The archive block is kept as corroboration with the reason it cannot be the assertion stated inline. |
| F17 | `test_golden_the_fixture_ships_no_calendar_and_no_feed_info` asserted a property `gen_njt_fixture.py`'s hardcoded six-file write list guarantees by construction, so it could never go red on the upstream drift its docstring named. Escaping mutation: delete the generator's `EXPECTED_ABSENT` loop and the golden stays green. | **Fixed**. `6f71ce5`. Rewritten to pin the TRIM CONTRACT (exactly these six members) and to say that upstream drift is the generator's job, asked against the live archive's own member list, which is the only place the question can be asked. |

---

## Refuted, with evidence

| # | Finding | Disposition |
| --- | --- | --- |
| F2 | `_warm_njt_static`'s generic `except Exception` treats `NjtAuthError` like a transient outage, so credentials the upstream will never accept are retried on the rung schedule forever, ~288 getToken POSTs/day at the 300s rung. | **Refuted, with evidence**. Two independent reasons. (1) **The anchored arm never fires.** `load_njt_static` catches everything but `NjtNotConfigured` and returns `{}`, so the warmup takes the EMPTY-RESULT arm, not the exception arm. Measured with a 401ing getToken, no cached zip, the real warmup and the real loader: 5 attempts, 5 getToken POSTs, and the `except Exception` arm fired **zero** times. An `except NjtAuthError` arm added there would be dead code. (2) **The premise is false.** `mint()` converts *every* transport failure into `NjtAuthError`, so a ConnectError, a ReadTimeout, a 503 and a 429 all arrive as that type; a terminal arm keyed on it would take NJT down for the process lifetime on a transient token-endpoint outage. The one-POST-per-attempt cadence the finding measured is the specified behaviour, stated in the module docstring. |

---

## What the round cost, and the three things it changed about the method

12 agents, ~1.44M tokens, 390 tool calls, 51 minutes. 29 candidates triaged to 17
verdicts; 16 confirmed, 1 refuted, 0 unverified.

**1. Three fixes shipped with no test behind them, and only a second mutation pass
found it.** After fixing all 16 findings, the standing two-part standard (kill the
motivating mutation, then invent a different one of the same class) was run over
every new guard. Eight of eleven mutations died immediately. **Three survived**: the
production-probe map (F1, the highest-severity finding in the round), its
`not-configured` branch, and the monitor's credential guard (F5). All three were
correct fixes with nothing asserting them. The tests that close them were written
after the fact, and re-running the same mutations killed all four.

The lesson is not "write tests", which everyone already believes. It is that a fix
derived from a review finding feels covered *because the finding described the
failure*, and the finding is not a test. Every fix in this round now has a mutation
recorded against it.

**2. A review can be right about the defect and wrong about the price.** F3's
mechanism, measurement and consequence were all correct, and the fix it implied
(invalidate on any failure) would have been wrong: it spends a mint per attempt on
a real outage, which is the exact hazard the sniff's narrowness exists to avoid. The
fix that shipped bounds the damage in time instead of in shape. **A finding names a
defect; it does not get to name the remedy.**

**3. The dropped-in-triage list is worth reading.** Seven candidate groups were
dropped as duplicates or merges, and two of them (the monitor's placeholder guard,
and the parametrize that tested only `None` and `""`) turned out to be the *same*
defect as a confirmed finding seen from the other side. Triage merged them
correctly; what nearly went missing was that fixing the confirmed half left the
dropped half live. F5's surviving mutation is exactly that shape.
