# 15b adversarial review: the adjudication record

Phase 15b (NJ Transit realtime: the decoder, the poller, alerts family membership,
the contract trap matrix and the watched monitor check) went through one
adversarial round over a ~4,400-line diff, aimed at six named surfaces: decoder-law
edges, mint conservation under concurrency, the arrivals index against the static
baseline, the freshness derivation's honesty, the three-way alerts feed-set split,
and the monitor's result well-formedness.

Every finding below was **reproduced by calling the real code** before it was
fixed. Nothing is dropped; the lenses that found nothing are recorded as finding
nothing, because "we looked here and it was clean" is the half of a review that
usually goes missing.

The same five categories the A4 and 15a records use:

- **Fixed**: real, a change shipped, and a mutation proves the change is load-bearing.
- **Deferred, with reason**: real, not fixed in this phase, and why.
- **Refuted, with evidence**: reported and false; the measurement that refutes it.
- **Refuted then overturned**: refuted at the time and later found real after all.
- **Confirmed but downgraded**: the reported behaviour reproduces, but the severity
  does not survive contact with the rest of the suite.

**7 findings raised, 7 fixed, 0 refuted.** Four of the six lenses produced
findings. Two (mint conservation, monitor well-formedness) produced none, and what
they checked is recorded below.

Separately, and before the round: **the contract tier found a defect review did
not**, which is recorded here too because the ordering is the interesting part.

---

## The two that could reach a rider

| # | Finding | Disposition |
| --- | --- | --- |
| F1 | **A dwelling train vanished from its own departure board.** The arrivals filter read `arrival` first; `feeds.shared._stop_time` takes the LATER of the two times and its docstring names this exact hazard ("a train dwelling or held at a station has arrival in the past but departure in the future"). Reproduced: a call with `arrival = now - 300`, `departure = now + 600` at stop 109 placed the train `at-station 109` and produced **`arrivals = {}`**. So `/api/njt-trains` drew a train standing at Penn while `/api/njt-arrivals/109` omitted a departure ten minutes out, at the station the whole trap matrix is written about. Untested at every tier: the just-passed test used both times in the past, and the simulator hardcoded `departure = arrival + 30`, so no dwell longer than 30s existed anywhere in the suite. | **Fixed**. `4bf4ff2`. `_still_upcoming` is `_stop_time`'s rule applied to this decoder's already-parsed calls, and `_trim_njt_arrivals` sorts on the same key so a dwelling train cannot sort ahead of a sooner departure either. Three mutations killed: reverting the filter, taking `min` instead of `max`, and un-sharing the sort key. |
| F2 | **A train drawn standing at its terminal 91 minutes before it arrived.** `_place` case 4 is documented as "PAST ITS LAST DEPARTURE" and never checked that `now >= last_time`; it was the unconditional fallthrough for cases 1 to 3. A leading `stop_time_update` carrying a `delay` and no absolute time (the minimal legal StopTimeEvent, one field from the bare timeless call this producer emits 35 times a peak poll) made case 2's `first_time` None, so `MAX_FUTURE_FIRST_STOP_S` never ran. Reproduced: `at-station 109, next_time 90.5 minutes out`, where the same trip WITHOUT the timeless call was correctly dropped. A timeless MIDDLE call was the same defect one position along: it broke case 3's consecutive pairing on both sides and teleported a train from between Newark and Penn onto Penn's platform. | **Fixed**. `4bf4ff2`. Cases 2 to 4 walk the calls that carry a time, which interpolates across the gap instead of falling through it, and case 4 requires the trip to be past its last call. See the note below on why this pair needs a double mutation. |

### The redundant pair, recorded rather than hidden

F2's two guards are **redundant with each other**, and this project's standard is
that every guard has a killing mutation. That standard cannot be met individually
here, so the honest statement is made instead:

- With case 2 fixed, an exhaustive search over every one-, two- and three-call
  configuration of `{absent, -600s, -30s, now, +30s, +600s, +5400s}` on both times
  found **zero** configurations where case 4's condition changes the answer.
- Removing either guard alone leaves the suite green.
- Removing **both** resurrects the phantom and fails
  `test_a_timeless_first_call_does_not_park_a_train_at_a_terminal_it_has_not_reached`.

So the PAIR is load-bearing and the double mutation is the one that kills. Both
stay: the phantom escaped through that branch once already, and one line is a cheap
second lock. The comment at case 4 says all of this, including that it is
individually unkillable, so nobody later assumes a test is holding it up.

---

## The three mediums

| # | Finding | Disposition |
| --- | --- | --- |
| F3 | **The watched ratio counted differently on each side.** `_njt_straddling_trips` (the denominator) dropped only `SKIPPED`; the decoder (the numerator) drops `SKIPPED`, `NO_DATA`, **and** any call at a stop the static table lacks. Both mismatches are reachable on real bytes: `njt_static` drops a `stops.txt` row whose coordinates will not parse, and this producer's habit is relationships that still carry times (238 SKIPPED-with-times a peak poll), so a `NO_DATA` with times is its shape rather than a hypothetical. Either one pushes `placed/straddling` under the floor and reports "the feed stopped retaining passed stops" when nothing of the kind happened. `NJT_PLACEMENT_FLOOR`'s own derivation claims the healthy value is 1.0 "by construction", which is only true if both sides filter identically. | **Fixed**. `4bf4ff2`. Two mutations killed. **A consequence worth naming:** with the sides aligned there is no well-formed feed that straddles `now` and fails to place, so that branch now guards a future regression rather than an input. Its test says exactly that and drives it by monkeypatching the decoder, which is the only honest way to exercise a branch whose triggering input cannot exist. |
| F4 | **A refresh killed by `REFRESH_DEADLINE_S` left the C2 block reporting `ok: true`.** The classified failure paths had just been fixed for precisely this; a timeout is caught by `_bounded_refresh` one layer out, which only recorded the error, so `_mark_all_systems_failed` never ran and retained trains kept a block claiming health. NJ Transit is the most exposed source to that path **by construction**: `njt_auth.njt_post`'s worst case is four requests at `REQUEST_TIMEOUT_S` each (mint, POST, re-mint, POST) = 120s against a 45s deadline, so a slow-but-alive RailData reliably lands there rather than at `_refresh_njt`'s own handler. | **Fixed**. `4bf4ff2`. Shared with subways and railroads, so pre-existing rather than introduced by 15b, and fixed for all three. Two tests: the timeout marks the block, and it stays a no-op for a source (PATH) that publishes no such block. One mutation killed. |
| F5 | **The freshness derivation was wrong, in the comment that says to re-check it first.** `_poll_feeds` sleeps `POLL_INTERVAL_S` **after** its TaskGroup joins, so the fetch-to-fetch period is 20s plus the cycle, not 20s. The block claimed a 43s worst age and "a little over 2x" headroom against the 90s budget. | **Fixed**. `4bf4ff2`. Both numbers now stated: typical is 23 + 22 = 45s (2x, as claimed), worst case is 23 + 20 + `REFRESH_DEADLINE_S` = 88s, which leaves nothing. And what crossing 90 actually does, which the original omitted: it reports the feed STALE, and during a cycle that took 45 seconds to finish that is a true statement. **The failure mode at that edge is a noisy staleness flag, never a ghost**, so the response to it firing is to find out why a refresh took 45 seconds rather than to raise the threshold. Every other number in the block was checked and holds. |

---

## The two lows

| # | Finding | Disposition |
| --- | --- | --- |
| F6 | **The fixture generator misdiagnosed a stale static fixture as a decoder invariant breaking.** Its cross-check denominator counted every entity with an id, so a trip absent from the committed static yielded no `short_name` and read as a mismatch. One such trip (an ADDED one, a train added since the capture, or a static fixture one publication out of date, which the script's own docstring says to expect) dropped the rate below 100% and aborted with *"a real drift here is worth a human decision, not a regenerated fixture"*. The check written for exactly that condition sat after the gate and was unreachable whenever it would fire. | **Fixed**. `4bf4ff2`. Counts the way the decoder counts, over joined trips only, and the stale-fixture check now runs BEFORE the gate whose message it explains. |
| F7 | **The alert health map is seeded once; the active feed set is read per poll.** `njt_auth.credentials` reads `os.environ` live, so the two disagree if credentials change in-process, and the rest of the module reads the health map's keys as the authoritative system list. Credentials disappearing leaves a system that is neither fetched nor failed, so `_apply_alert_generation` stamps `fresh_at = now, last_error = None` on it every poll while `merge_alert_generations` deletes its alerts as neither fresh nor retained: **silent thinning under a green health surface**, which is the one thing that map exists to prevent. Credentials appearing leaves a system with no health key, so the retention clock threaded out of that map is never persisted and `ALERT_RETENTION_MAX_S` can never fire. | **Fixed**. `4bf4ff2`. Reconciled once per poll. Reported as a structural gap rather than a live bug (`load_dotenv` runs before the cache is built, so cold start was always safe), and fixed anyway because the rest of the module's "take the system list from health's own keys" rule should be true rather than true-while-the-environment-holds-still. Four mutations killed, one of which is the lesson below. |

---

## What the round changed about the method

**1. A mutation that deletes the CALL survives every test of the FUNCTION.** F7's
fix was correct, and `_reconcile_alert_health` had three direct tests. Deleting its
call from `_refresh_alerts` left all three green. This is the coupling-test lesson
from 15b's own poll registry arriving from the other direction: there, two lists
that could not disagree needed a test; here, a helper that was right and unreachable
needed one. The test now drives `_refresh_alerts` and asserts the reconcile
happened, and the same question is worth asking of every helper extracted during a
fix.

**2. The contract tier found what the review did not, and found it first.** Before
the round, `test_njt_realtime_outage_degrades_only_njt` failed on `systems.njt.ok`
still being `true` through an outage, because every classified failure in
`_refresh_njt` recorded the cache error and the `feed_health` dict and left the C2
block alone. That is a rider-facing defect (retained trains at full opacity with no
staleness marker) that four lenses aimed at the decoder would not have looked at.
The review then found the SAME defect one layer out, on the timeout path (F4), which
the contract tier could not reach because nothing there wedges a refresh for 45
seconds. **The two tiers found the two halves of one defect, and neither would have
found both.**

**3. A finding can be right about the defect and wrong about which fix is the
fix.** F2 named case 4's missing condition, and that condition turns out to be
individually unkillable: the real repair was case 2 walking the timed calls. Both
shipped, and the record says which is which. This is 15a's "a finding names a
defect; it does not get to name the remedy" holding a second time.

---

## Lenses that found nothing, and what they checked

Recorded because a clean lens is a result, and an unrecorded one gets re-run.

**Mint conservation under concurrency.** `TokenCache.get`'s double-checked lock plus
`invalidate`'s compare-and-clear survives every traced interleaving between
`_refresh_njt` (a TaskGroup child) and `_poll_alerts` (a separate task on the same
loop): whichever caller re-mints first, the other's `invalidate(T1)` no-ops because
`_token` is already `T2` or `None`, and its second `get()` returns the fresh token.
All three consumers use the default process-wide cache. `_NjtToken` sets `_done`
before minting and catches every exception, so one mint per monitor run.
`_fetch_retrying` re-sends the held token and never mints. **One bounded hole named
and not fixed**: a cancellation landing inside `await mint_token()` increments
`mint_requests` without caching a token, so a getToken slower than the remaining
deadline budget re-spends a mint per cycle. Deferred: it needs a getToken slower
than 45s to be reachable at all, and the fix (shielding the mint from cancellation)
trades a bounded overspend for an unbounded shutdown delay.

**Monitor result well-formedness.** Every `statuses.append` in `check_njt_realtime`
is paired with a `details.append` on the adjacent line, so `_njt_rt_result` is not
reachable with non-empty statuses and empty details and the empty-detail string
cannot be produced. All five early returns are well-formed. The alerts failure path
appends and falls through to the trip-updates fetch rather than returning. There is
no path where the token is minted and neither feed is fetched.

**Other decoder-law edges**, all verified correct rather than fixed: a CANCELED trip
whose stops are NOT marked SKIPPED is dropped at the trip level before either
product is built; a trip whose last stop is SKIPPED loses only that call; a
zero-second dwell falls to case 3 and `_interpolate`'s `span <= 0` guard returns the
previous stop's coordinates rather than dividing by zero; `stop_sequence` is used
only as a sort key; an ADDED trip with no `route_id` degrades to `headsign =
train_num` without raising; duplicate `stop_id`s produce independent rows without
breaking placement.

**Arrivals index against the static baseline.** `_ordered_calls` filters to
`stop_id in stops`, so the index is a subset of the static stops at decode time; a
stop later removed 404s at the gate rather than serving orphaned rows; a stop
present but not in the index returns `200 []`. `entry["fetched_at"]` and
`app.state.njt_arrivals` are written in the same success block and neither is
touched on any failure path, so a failed poll cannot serve arrivals under an
advanced timestamp.

**The three-way alerts split.** `ALERT_FEED_URLS` (every feed), `active_alert_feeds()`
(the ones polled), `KEYLESS_ALERT_FEEDS` (the ones a GET can reach). No caller reads
the wrong one. The only defect here was the seeding asymmetry (F7).

---

## Round 4: the empty-`trip_id` decoder audit

Ordered after the third capture refused itself and reported the reason: 164
trip_updates, 128 joined, 36 ADDED, unmatched sample `['']`. The question put to
the audit was whether anything keyed by `trip_id` collapses when 36 of them are the
empty string. Three lenses (the decoder itself, everything downstream of it, and
the tests), each finding independently re-measured by a second agent instructed to
refute it.

**The headline answer was no, and the reason is worth writing down: the keying was
already correct at HEAD.** `"trip_id": trip_id or f"{SYSTEM}:{entity_id}"` predated
the capture, so 36 extras with distinct `entity.id`s always decoded to 36 distinct
trains. Verified by mutation against the pristine baseline rather than by reading:
strip the fallback and the ENTIRE baseline suite stays green — 1185 backend passed,
27 contract passed — while 36 trains collapse to one key. The correctness was luck.
Nothing observed it, because every synthetic ADDED trip in the repo carried a
fabricated NONEMPTY id (`"T-UNKNOWN"`, `"T-BARE"`, `"ADDED-A9"`) and no test
anywhere built more than one. A collision cannot be seen where a collision cannot
occur; the tests were not weak, they were incapable. Three separate guesses at a
field, all wrong in the same direction, is the A4g lesson in its purest form.

**F8 (real, fixed). Two entities sharing one `entity.id` collapse silently.** The
one case the fallback chain cannot inspect: every step reasons about a single
entity, so none of them notices a second arriving at the same key. Measured at
HEAD: 36 ADDED with `trip_id=""` and one shared `entity.id` produce 36 trains, ONE
distinct id, and ZERO warnings. That is the exact failure the capture-night commit
was written to prevent, surviving in the corner it could not see. The asymmetry is
what makes it worth code rather than a comment: an entity with NO identity already
warned loudly per entity, while two entities claiming ONE identity said nothing,
and silence is the shape that loses a train. GTFS-RT requires `FeedEntity.id` to be
unique within a message, so this is a producer violation — and so was the empty
`trip_id`. Fixed with a `seen` set threaded through `_identity`: the first claimant
keeps the clean key, a repeat is separated by position and announced. The generator
reports the shape at capture time as well, since a capture containing it would be
NJ Transit's third broken promise in this feed and should not wait for a golden to
say so.

**F9 (real, fixed). The arrivals tie-break preferred rows with no identity.**
`_trim_njt_arrivals` sorted on `(_still_upcoming(row), row["train_num"] or "")`. A
train carrying neither a `trip_id` nor an `entity.id` has `train_num` None, and `""`
precedes every real train number, so on a stop where departures share a minute the
nameless rows sweep the six-row cap: four unidentified rows measured against eight
scheduled ones served 4 of 4 nameless and 2 of 8 named, costing Penn six real
departures. Not a collapse, and the trains themselves survive — it is a loss of
served rows traceable to empty identity fields, which is the same defect wearing
different clothes. The sort key is now total and puts both unknowns last, which
also closes a latent `TypeError` (a timeless row and a timed row at one stop) that
was unreachable by construction and would have crashed rather than misordered.

**F10 (real, fixed). The only real-bytes ADDED assertion could go silent.**
`test_golden_captured_added_trips_survive_as_distinct_trains` skips a capture with
no extras. Defensible on a quiet night, wrong as the last word: an ADDED-free
recapture would retire decoder law 3's evidence and report nothing, which is the
dormant-golden failure that burned 13a and 13b. `added_trips >= 1` is now a
generator refusal and a golden non-vacuity assertion, in step so a fixture cannot
pass one and fail the other.

**Corrected in the reports themselves, and worth recording because the correction
is the finding.** One lens dramatized the defect as "35 trains vanish"; the
verifier measured that `trains` is a plain list and the mutated decoder returns 36
trains sharing one id. The vanish is real but PROJECTED — it lands in the first
consumer that keys a map by id, which is the established house pattern
(`path.js:258`, `buses.js:284`) and does not exist for NJT yet. This matters
concretely: `assert len(trains) == 36` is non-discriminating, and the set
cardinality line next to it is the whole assertion.

**Checked and clean.** No structure between the decoder and the HTTP response keys
by trip identity: `arrivals` and the trim key on `stop_id`, `trains` is a list,
`_refresh_njt` does no carry-forward or merge, and pydantic dedups nothing (36
identical-id trains serialize to 36 rows). `njt_static` skips blank `trip_id`s, so
`trips.get("")` can never falsely join. An extra whose `entity.id` equals a
scheduled trip's `trip_id` does not collide, because the synthesized key is
prefixed. Cross-poll identity survives feed reordering.
