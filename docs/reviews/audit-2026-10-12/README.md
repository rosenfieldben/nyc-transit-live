# Reproductions for the 2026-10-12 audit

Every number in section 2 of [../audit-2026-10-12.md](../audit-2026-10-12.md) comes
from one of the scripts here, or, for the four rows that restate a row of the
2026-09-05 record, from that record's own witness. They exist for the reason the
[2026-09-05 scripts](../audit-2026-09-05/README.md) exist: a claim in a review
document that nobody can re-run is a claim with a shelf life.

```
bash docs/reviews/audit-2026-10-12/run_all.sh      # from the repository root
PY=backend/.venv/bin/python bash docs/reviews/audit-2026-10-12/run_all.sh   # a local venv
```

Eleven scripts, about a minute (forty seconds as filed; A09's validation now reads its
2 GiB member whole, and its arm 1c runs three attempts). Each one exits 0 while its
row holds (the finding still behaving the way the table records it, or for a FIXED row the
repair still in place) and non-zero the moment the code stops matching, so these are
regression checks on the audit record rather than one-off prints. A script measures what
the code DOES and never where it lives: none asserts a line number, a symbol name or the
text of a comment, so a behavior-preserving refactor cannot turn a row red, and a fix
will. One class of refactor is the exception, and it fails closed rather than open: A02,
A02b and A13 observe the static warmup's backoff through `asyncio.sleep`, so a backoff
rewritten to wait some other way turns those three rows red, never green. Run one on its
own with `backend/.venv/bin/python <script>` or `node <script>`.

**Read the tally, not the count.** `run_all.sh` ends with a line like
`11 passed: 8 VERIFIED, 0 PARTLY, 0 REFUTED, 3 FIXED`. Every script here but A01's,
A02's and A02b's records an open defect, so a green run means those defects are all
still there; those three pin their repairs, and A09's pins the repair of its first arm
while its other two still record the defect. The audit makes the point about the
2026-09-05 job ("a green audit-record job is not a clean bill of health"), and it is
right: the count says the record is current, the tally says what the record is. As
fixes land, rows move to FIXED and their scripts are rewritten to pin the repair, the
way the 2026-09-05 scripts were.

CI runs this harness in the `audit-records` job, beside the 2026-09-05 one and under the
same `PY`, and runs it even when the 2026-09-05 step has gone red, so one harness's red
row cannot hide the other's.

## What they need, and what they cannot do

- A Python environment with `backend/requirements-dev.txt` installed. `run_all.sh`
  defaults to `.venv/bin/python`; override with `PY=/path/to/python`.
- Node 20 or newer for the `.mjs` scripts.
- No network, no credential, no live upstream, no running server. Every script drives
  production code over committed fixtures, archives it builds on disk, or an
  explicitly injected fault.
- **No NJ Transit mint can be spent here.** A01 measures a mint storm without making
  one, the way F05 did: its upstream is an in-process fake that refuses any real
  NJ Transit host.

## The rules, by reference

**Containment** is the 2026-09-05 rule, stated once in
[that README](../audit-2026-09-05/README.md#the-containment-rule-and-why-a-scrub-is-not-one)
and not restated here: every script that imports a backend module calls
`_hermetic.contain()` before the import and `_hermetic.verify()` after it. The
scripts import `_hermetic` from `../audit-2026-09-05/` by putting that directory on
`sys.path`. They do not copy it, because two copies drift and
`_hermetic_selftest.py` covers one.

A01 is the one script here that must look CONFIGURED for NJ Transit, because the
consumers it drives check that before they try. `verify(expect_configured=True)`
accepts a fabricated username only with the prefix of one of the two 2026-09-05
scripts that needed this (F05 and F07), so A01 fabricates an `f05-` pair rather than
widen that allow-list from a branch that does not edit the 2026-09-05 harness. The
address wall is what keeps it safe either way.

**Scan scope.** Any tree scan is scoped away from `docs/reviews/`, for the reason the
2026-09-05 README gives under F13: these files quote the text they look for, and an
unscoped scan matches itself the moment it is committed.

## Every script has been seen to fire

A guard nobody has seen fire is indistinguishable from one that cannot. So every
script here was run against a scratch copy of the tree (never the live tree) with a
plausible fix for its finding applied, and it went red. The **killed by** column
records that fix and the exit observed. The fixes are descriptions of a mechanism,
not patches to carry anywhere: the branch that repairs each finding writes its own.
Beyond its own fix, each script was put to an independent reviewer whose job was to
find a second, different fix the script would NOT notice; where one was found, the
script was tightened until it did, and the row says so. Then the whole branch went
through the repository's adversarial review (`.claude/workflows/adversarial-review.js`),
which found six more such escapes in five scripts (A01, A02, A03, A06, A09); each was
closed the same way, every earlier fix was re-run against the tightened script, and
the cells mark them **Adversarial round**. What a script still cannot see is stated in
its docstring and, where it matters, in its cell as a **known limit**.

## The scripts

| Script | Finding | What it drives | Killed by (change made in a scratch copy, exit observed) |
| --- | --- | --- | --- |
| `a01_njt_fresh_token_rejected.py` | A01 (**FIXED**, `claude/a01-mint-allowance`) | Five arms, every request going out through njt_auth's own `_httpx_post` and transport and answered at httpx's socket layer beneath them. Arm 1: the real `TokenCache` and `njt_post` against an in-process fake RailData that mints every time and refuses every token, the ladder across a refused fresh token, and the allowance under sustained rejection from a cold cache. Arm 2: the real `_refresh_njt` and `_refresh_alerts` at the production cadences on a hand-cranked clock from noon Eastern across the next midnight, alone and in flight together. Arm 3: F05's failed-mint cooldown, the control. Arm 4: in a fresh interpreter, the real `main.lifespan` and poll loops over the `TOKEN_CACHE` the module builds at import. Arm 5: an over-age token, then a revoked one, through the allowance hold. | **Now a regression check on the repair**, each kill in a scratch copy, each **exit 1**: reverting `reject_fresh` in `njt_post` turns arm 1 red at 1 getToken and 10 data POSTs (not the record's 11 and 20: a refused fresh token buys no re-mint, so without `reject_fresh` the dead token stays cached and is re-posted by every call, with no hold) and arms 2 and 4 red with it, arm 2's first date spent by 6 min; lifting the allowance (its default raised to 100000) turns arm 2 red at 11 getToken POSTs on its first date, the account's ten gone at 3.5 h and NJ Transit's refusal drawn at 4.0 h, and arms 1 and 5 red; and the named mutations each go red as the ledger row records, M1 to M8 and M10 here, and M9, M11 and M12 (the freshness rule's three edges) in unit tests. **As the record of an open defect** it was killed by the spec fix and six second fixes, and an adversarial round moved its fake down to httpx's socket layer; that history is this cell at `d5f6d70`. |
| `a02_unreadable_trips_promoted.py` | A02 (**FIXED**, `claude/a02-publication-validation`), and F08 | The real staged download, validate and promote path and the real subway warmup over archives built on disk, the socket layer faked under httpx, on a virtual clock that moves by each backoff the warmup asks for. | **Now a regression check on the repair**, each kill in a scratch copy, each **exit 1**: the `trips.txt` gate removed from `validate_subway_archive` (nine checks red, among them the mechanism's five-member table, arm 1's three refusals at staging and arm 4's first attempt, which promotes the bad publication again); the `stop_times.txt` gate removed (seven, arm 1b's); the `shapes.txt` gate removed (the mechanism's five); the residual arm removed (A1.7 and A1b.7: with validation bypassed, the index loader's raise no longer discards the cache); and D2 reverted whole, the three gates removed (thirteen; with the residual arm removed as well, twenty-one). **Exit 0, by design:** the drain's floor of one row dropped, which a script about unreadable bytes does not reach (F08's header-only phase and `test_static_data.py`'s floor tests fail instead); and the loader's trip consumer reading the CSV itself, behavior unchanged, which `test_static_data.py`'s generator test fails. **As the record of an open defect** it was killed by the spec fix and seven second fixes, and an adversarial round moved `datetime` onto its virtual clock; that history is this cell at `d5f6d70`. |
| `a02b_njt_shapes_zlib_error.py` | A02b (**FIXED**, `claude/a02-publication-validation`) | The real `validate_njt_publication`, staged path, `load_njt_static` and NJ Transit static warmup over the committed `njt_gtfs` fixture with one byte of `shapes.txt`'s DEFLATE stream damaged, on a virtual clock across the cache's 30-day life. | **Now a regression check on the repair**, each kill in a scratch copy, each **exit 1**: `zlib.error` removed from both NJ Transit tuples (five checks red: no ready on attempt 1, 0 of 164 stations, the 29-day restart, and both residual-arm checks), and from the residual arm's tuple alone (the two residual-arm checks: the attempt raises `zlib.error` and the damaged cache is kept). **As the record of an open defect** it was killed by the spec fix and thirteen second fixes, and an adversarial round made a `.env` override of either retry seam stop the run; that history is this cell at `d5f6d70`. |
| `a03_empty_board_hides_failed_feed.py` | A03 | The acceptance world of `backend/tests/test_f03_boards.py` through the real refresh path and the ASGI app, then the whole page (every script `index.html` loads) booted in a `node:vm` over the bodies the app served. | **Spec, backend:** station coverage carried per group independently of indexed arrivals: **exit 1**, backend checks red and frontend checks green. **Spec, frontend:** the board line warns on content age or an unavailable contributor for an empty board: **exit 1**, frontend checks red and backend checks green. **Ten second fixes, each exit 1:** keep an expired station's key; name the failing groups on an empty board; a new board field for expected systems; the two renderers rewording their own empty line; a caveat joined from the vehicle feed's groups; a caveat in the fetch layer; popup-only and panel-only repairs; a coverage field on `/api/subways` read by the page; a 503 for an unservable board. **Controls, exit 0:** sixteen renames across the tree. **Adversarial round, each exit 1:** a per-station `/api/subway-station-health/{station_id}` endpoint the popup and panel read for an empty board; the same coverage behind a query string on the board's own URL; the same coverage in a header on the board response, read by the page; the same coverage asked for by POST, which the harness cannot put to the app and so fails a request check that names the URL. The page's templated and query-string requests are now answered by the app, and any request the harness cannot answer is a checked quantity that must be 0. The driver now honours `NODE`. **Controls, exit 0:** a cache-busting query string on the board URL; `run_all.sh` with node off `PATH` and `NODE` set. **Known limit, exit 0:** a repair that speaks only through a synthesized service alert, because this world has no alert index and the app answers `/api/alerts` with an error. |
| `a04_clock_offset_minimum.mjs` | A04 | The production page booted in a `node:vm` with an injected device clock and a fake event loop on monotonic time, so `map.js`'s own timers drive the polls for two hours after the correction. | **Spec:** reset the stored minimum when a sample exceeds it by more than 60 s: **exit 1**. **Thirteen second fixes, each exit 1:** a jump detector on the poll path; the minimum of the last 8 samples; the audit's monotonic anchor; four step detectors (a heartbeat timer, every frame, frames up to 2 s apart, a timer chain); 600 s and 3600 s windows; 10 and 30 minute re-baselines; the latest sample; trusting `performance.timeOrigin`. **Controls, exit 0:** renames of the offset, the train registry and the source table. |
| `a06_frontend_static_once.mjs` | A06 (frontend half) | The production page booted in a `node:vm`, all 11 frontend static loaders driven through three refusals, one usable answer, then 25 simulated hours of a changed publication, a backend restart, a hidden tab and its returns. | **Spec:** an hourly re-read of the PATH routes after success: **exit 1**. **Twenty-four second fixes, each exit 1**, among them a refreshing `retryUntil`, re-reads on visibility, focus, page show, coming back online, a new static generation (in a body key or a header), a missing route, a server-day rollover, a backend restart or an expired `Cache-Control`; a scheduled or generation-driven page reload, a meta refresh and `history.go(0)`; property handlers in place of listeners. **Controls, exit 0:** renames, the kickoff split into its own file, `defer` on every script tag, window-qualified globals. **Adversarial round, each exit 0 before and exit 1 after:** an hourly re-read of all eleven loaders deferred through `window.requestIdleCallback`, `self.requestIdleCallback` or `window.queueMicrotask`, and one keyed on a `static_generation` body key no fixture carried; and twenty-one more of the same kind, among them `scheduler.postTask`, the `top`, `globalThis` and `document.defaultView` spellings, an API the script does not model, a `MessageChannel`, `navigator.locks`, Leaflet's `L.Util.requestAnimFrame` and `L.DomEvent.on`, four other generation keys, a header parsed as a number, extra `setInterval` arguments, a message the page posts itself, and a reload timed from `performance.timeOrigin`. The script now runs `requestIdleCallback`, `queueMicrotask`, `scheduler` and `postMessage` on its clock, gives every live body seven announced keys, and fails a setup check when a function is handed to a `window`, `self`, `navigator`, `L.Util` or `L.DomEvent` member it does not model. **One more control, exit 0:** modelled globals reached through `self`, `top`, `globalThis` and `window`. **Known limit, exit 0:** an API reached only after a `typeof` test on a bare name the page's context lacks. |
| `a09_unbounded_upstream.py` | A09 (arm 1 closed, `claude/a02-publication-validation`) | The real `staged_fetch`, default transfer, subway validator and subway warmup, the socket layer faked under httpx; the large bodies generated as they are served. | **Arm 1 pins the repair since `claude/a02-publication-validation`; each exit 1:** `staged_fetch` validating inline again (all eight of arm 1's checks red: no `TimeoutError`, the stage promoted, the loop held); the same with a bare `sleep(0)` after validation, this cell's control while arm 1 recorded the finding and still not a fix (the same eight). **Known limit, exit 0:** the subway's cached-archive check put back inline with `staged_fetch` left off the loop; arm 1 starts from a cold cache and never runs that check, so `test_a_slow_validation_does_not_hold_the_serving_loop` in `backend/tests/test_static_data.py` pins it instead, and fails. **Arms 2 and 3, still the finding. Spec, each exit 1:** arm 2, a byte ceiling in the transfer; arm 3, a member ceiling at validation. **Seven second fixes, each exit 1:** size gates after the transfer at 48 MiB and 128 MiB, and a declared-length check at 128 MiB; member ceilings at 64 and 65,535 (the 65,535 one re-run on the branch, exit 1); expanded-size limits at 48 MiB and 1 GiB. **Controls, exit 0:** two private renames. **Adversarial round, each exit 1:** a 256 MiB or 200:1 ceiling on each required member, in `validate_subway_archive` and in `require_members`, and each half alone; a 1,000:1 ratio ceiling; a 256 MiB ceiling on every member. The reviewer's version had left the script green while arm 3's 2 GiB sat on an extra member nothing reads, so the expansion moved to `stop_times.txt`, a member the validator requires and the routes loader inflates whole. **A known limit no longer, exit 1 since the branch:** a 256 MiB ceiling in `parse_member` alone. While validation parsed only `stops.txt` and `transfers.txt` it bounded what validation read and left `stop_times.txt` promoted at 2 GiB (exit 0); validation now parses `stop_times.txt` through `parse_member`, so the ceiling refuses arm 3's archive (3.3 and 3.4 red). **Arm 1's kills at `d5f6d70`, each exit 1:** its spec, `staged_fetch` suspending once after validation, and three second fixes, the validator, the whole publication or the warmup moved off the loop. |
| `a10_headerless_lirr_clock.py` | A10 | The real railroad and subway decoders and the real refresh and block-building path through the ASGI app, over generations built from the committed captures with headers stamped and stripped. | **Spec:** the railroad producer records LIRR with `None` for a decoded, undated generation: **exit 1**. **Nine second fixes, each exit 1:** the reader keys retention on the failure list; the call site fills absent keys, from the failure list or from the systems that decoded; date an undated generation by its newest entity clock (with or without an explicit `None`), by its fresh rows, by `Last-Modified` or by the poll time; refuse it as a failure. **Controls, exit 0:** renames, and the `None` filter moved from the producer to the call site. One more control is **exit 1 on purpose**: reverting contract 6.1's reader to a value read, which shows this script is that reader's only witness. |
| `a11_estimated_lirr_train_numbers.py` | A11 | The committed LIRR capture through the real decoders, `fetch_railroad_trains`, and the ASGI `/api/railroads` and arrivals routes. | **Spec:** the existing trip-to-label join applied to placement rows: **exit 1**. **Two second fixes, each exit 1:** the train number passed into the placement builder; an identity memory across polls in the poller. **Control, exit 0:** renames and an extracted helper. |
| `a12_path_fallback_color.mjs` | A12 | `index.html`'s scripts in a `node:vm` with a stub DOM that computes an SVG shape's fill the way CSS does (cascade, `var()`, `currentColor`), around PATH trains drawn before the route table lands. | **Spec:** the re-icon gate keyed on route and resolved colour: **exit 1**. **Twenty-six second fixes, each exit 1**, among them re-icons at table load, in the stale sweep and after the loader resolves; in-place repaints through `style.fill`, `cssText`, `setAttribute`, `!important`, a presentation attribute, `innerHTML` and `outerHTML`; custom properties on the root, the map container and the marker; a class with a page stylesheet or an adopted one; remove and re-add. **Controls, exit 0:** renames, `currentColor` markup, the fill moved to a stylesheet, a file split, changed timings, and two non-fixes the cascade outranks. |
| `a13_env_seams_accepts_nonpositive.py` | A13 | The real `env_seams.seconds`, every call `import main` makes to it replayed as made, then `main.app` started through the ASGI lifespan protocol, as `uvicorn main:app` starts it, under each value. | **Spec:** `seconds()` raises on a nonfinite or nonpositive value: **exit 1**. **Ten second fixes, each exit 1, four of them run again from the verifier's own patches:** fall back to the default with a warning; refuse at startup in `main.lifespan`, in an app wrapper, in a router's lifespan or at import; a per-seam keyword; a new parser at the call sites; clamps at the poll sleep, the retry rung and the retention cap. **Controls, exit 0:** renames of every private helper it drives. |

Four rows of section 2 have no script here because they restate a row of the
2026-09-05 record that is still VERIFIED, and that record's witness is theirs: A05 is
`f06_partial_startup_no_recovery.py`, A06's backend half is
`f07_static_never_refreshes.py`, A07 is `f04_airtrain_historical_reference.py`, and A08
is `f14_accessibility_gaps.py`. The 2026-09-05 `run_all.sh` re-derives each of them on
every commit, so their rows lean on it rather than re-proving it.

Two scripts carry a choice their rows should not hide.

- **A06 exists, so this harness runs eleven scripts, not ten.** The rule for adding it
  was that the production loaders could be driven in a `node:vm` in under a hundred
  lines of harness, the way F14 drives rider actions. They can: the driving core was
  98 lines (counted as code lines, not blank and not comments, over the preamble and
  the harness sections). But that core stayed green under genuine repairs (a page
  reload on a new static generation, a handler set as a property, a re-read deferred
  through an idle callback), and closing them, first in the script's own tightening and
  then in the adversarial round, took a modelled environment (a restart, the tab's
  lifecycle, navigation, the browser's deferral APIs) and more harness: counted the
  same way, the harness as committed is 187 lines. If the rule is read as a ceiling on
  the whole harness, delete the script and its `run_all.sh` line, and A06's frontend
  half becomes UNMEASURED with this paragraph as the reason.
- **A09's arm 2 streams 512 MiB**, past the "tens of megabytes" first planned for it.
  At 64 MiB the bound sat at under twice the roughly 40 MB live publication the
  repository describes, so a ceiling with ordinary headroom (128 MiB) left the script
  green under a genuine fix. The body is generated as it is served and the stage file
  is removed, so a run costs about 140 MB of memory, 0.5 GB of transient disk and
  about eight seconds. The row states 512 MiB as the bound measured, as it states
  70,005 members and 2 GiB expanded in `stop_times.txt` for arm 3.
