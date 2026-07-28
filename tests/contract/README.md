# The contract tier

The real backend, the real page, and upstreams a test controls.

```
pytest tests/contract                                              # api scenarios, no browser
npx playwright test --config tests/contract/playwright.contract.config.js   # browser specs
```

Both from the repo root. `cd backend && pytest` still collects only `backend/tests`,
so the hermetic tiers are untouched by anything here.

## Why it exists

Every other suite tests one layer against a stub of its neighbour: pytest injects
an httpx client, Playwright serves `mock.js`. Both are fast, both are hermetic,
and neither can catch the failure the third audit's closing diagnosis named, where
the backend, the envelopes and the frontend are each locally correct and the
composite a rider sees still lies. Catching that needs the real app polling a real
socket with the thing on the other end under a test's control. That is this tier.

It is the SLOW tier by construction and it stays small on purpose. A claim that a
hermetic test can pin belongs in a hermetic test; what lands here is a claim about
the seam BETWEEN layers, or about a poll loop's behavior over time.

## The three files that do the work

- `upstream_sim.py` — one HTTP server standing in for every upstream, one path per
  feed. Bodies are built from the committed golden captures (only time is
  rewritten) and archives from the committed stops fixtures, so entity ids, stop
  ids and route ids agree across the two halves the way they do in production.
  Feeds carry a MODE (`live` / `frozen` / `empty` / `error`); archives carry a
  PUBLICATION (`good` / `headers-only-stops` / `missing-member` / `corrupt-zip`).
- `conftest.py` — launches the real backend as a subprocess with PR 1's env seams
  pointed at the simulator and PR 1's timing knobs compressed, against a fresh
  temp `DATA_DIR`.
- `serve_contract.py` — the same pair as one command, because Playwright's
  `webServer` takes one command and the browser tier needs both processes.

## Hermeticity is asserted, not assumed

`test_smoke.py` checks that every upstream the app actually polls is the
simulator. This is not decoration. A source whose URL lacks a PR 1 seam would
quietly reach the real internet and every scenario would still pass, flakily, at
the mercy of the MTA's uptime. If a future system is added without its seam, that
test fails and says which one.

## The determinism rules

**1. Every wait is a poll-until-predicate on an observable, with a hard deadline.**
Three primitives: `sim.await_polls(key, n)` waits until the app has fetched an
upstream `n` more times, `app.await_status(pred)` and `app.await_railroads(pred)`
wait until a live endpoint satisfies a predicate. All three fail with a message
naming what they were waiting for and dumping the last response, because a bare
timeout in a tier like this is nearly useless. A test that needs "two polls after
I broke MNR" says exactly that, so it stays correct whatever `POLL_INTERVAL_S` is.

**2. No fixed sleeps as synchronization.** The only sleeps are the 50ms poll
granularity inside those primitives, and the deliberate waits a scenario is ABOUT
(a retention window elapsing, an alert's active period closing) — waits on a
compressed clock the test itself configured, not guesses about the app.

**3. Zero retries in CI.** A flaky contract test is a bug to fix, not to paper
over: this tier's entire value is that a failure means the composite lies, and a
retry converts exactly that signal into noise. `retries: 0` in the Playwright
config says so in the file, not by inheritance.

**4. One backend process per scenario, in the api tier.** The `contract_app`
fixture is function-scoped. A shared process would let scenario A's failed feed or
promoted archive change what scenario B observes, and that order dependence is the
classic way an integration suite becomes untrustworthy. It costs a few seconds of
startup per scenario and buys isolation that cannot be reasoned away.

The browser tier cannot afford that — `webServer` is per-RUN, not per-spec — so it
pays for the shared process with an explicit `test.afterEach` that restores every
feed it touched. That is a weaker guarantee, and it is the reason the browser tier
carries only the three claims that genuinely need a browser.

**5. A browser spec establishes its healthy baseline before breaking anything.**
Not ceremony. The page re-polls every 15 seconds and re-dims as ages cross the
threshold, so a staleness threshold set below that interval leaves every marker on
the page dim for most of every cycle, healthy or not — and "the down system's
markers are dim" becomes true before anything goes down. The specs use 25 seconds
for that reason, and each asserts BRIGHT first so a dim assertion can only mean the
transition happened. The same trap has a second mouth: `[].every(...)` is `true`,
so every "all markers dim" check also asserts the marker set is non-empty.

## Budget

Both halves are meant to finish well under four minutes, as separate CI jobs. As
committed: 14 api scenarios in about 2m30s, 3 browser specs in about 1m40s. What
makes that reachable is PR 1's timing knobs, compressed in `CONTRACT_TIMING`: a
scenario that has to outlive the retention window waits 20 seconds instead of ten
minutes, a static retry walks 1s/2s/3s instead of 15s/30s/60s/300s, and the page
dims after 25 seconds instead of 90. `POLL_INTERVAL_S` is 2 rather than lower on
purpose — below about a second the poll loop and the assertions start racing, and
rule 3 says a flake is a bug.

One more thing was load-bearing for the browser budget: the specs ABORT basemap
tile requests and open the page with `domcontentloaded`. Leaflet appends its tiles
during initial script execution, so they belong to the load event, and waiting them
out cost a full minute per spec on a runner that cannot reach the tile CDN. Aborting
them is also what makes the browser half honestly hermetic — nothing here asserts on
imagery, and a basemap from a public CDN would be one uncontrolled input.

The browser tier overrides one of those back UP: `serve_contract.py` raises
`FEED_RETENTION_MAX_S` past the length of the run. The api tier's 20s cap exists so
a pytest scenario can watch a window expire; the browser tier's claims are about
markers that are still on the map AND dimmed, and a 20s cap would drop them before
the 25s threshold could dim them. Nothing in the browser tier asserts the cap.

## Deliberately NOT here

- **C4's orphan and overlap invariants.** They are properties of a static dataset,
  fully decided by the archive bytes. A real socket adds nothing, and they already
  run in milliseconds in `backend/tests`.
- **The contract monitor.** `backend/scripts/contract_monitor.py` watches the same
  publications from the UPSTREAM side; pointing it at the simulator would make the
  two vantage points share a fiction, which is the opposite of why it exists.
- **`mock.js` and the existing `tests/e2e` specs.** Not migrated, not deprecated.
  They pin rendering against stubbed payloads in seconds, and this tier names its
  hermetic counterpart in every spec precisely so a failure localizes: hermetic red
  means the rendering logic broke, hermetic green means the composite did.
- **The bus route index and AirTrain.** Neither has a partial-outage story yet.
  See the standing note below.
- **The alerts staleness marker, in the browser.** The composite half (a real
  one-feed alerts outage produces a per-system degraded block) is asserted in
  `test_one_alert_feed_down_is_visible_per_system`; the rendering half is asserted
  hermetically in `tests/e2e/smoke.spec.js` "C2d". Joining them in a browser would
  cost more than a minute of wall clock: the page polls alerts every 60 seconds, so
  `alertsStaleAfterS` has to sit above that to mean anything, and the marker cannot
  appear before it. Two cheap tiers beat one expensive one here.
- **A stuck upstream, as a rider-facing claim.** `feeds/path.py` states the
  decision: the bridge re-serves identical generations routinely, so content
  sameness is never treated as staleness "here or anywhere downstream". A frozen
  upstream therefore keeps every liveness signal green and the page keeps its
  markers bright. That is pinned as a DECISION in
  `test_a_frozen_upstream_leaves_every_liveness_signal_green`, with the one signal
  that does move (`feed_age_s` outrunning `age_s`) asserted, because that is the
  evidence any future content-staleness heuristic would key on.

## Two things the seams cannot reach

`MAX_AGE_DAYS` is deliberately not a PR 1 seam, so "upstream publishes garbage over
a good cache" cannot be reached inside one process lifetime. `ContractHarness`
expresses it rather than approximating it: run the app once against a good
publication so a real archive lands in `DATA_DIR`, backdate that file
(`age_archives()`, exactly what the passage of time would do), then run the app
again while upstream serves garbage. The second boot re-downloads for real, rejects
for real, and falls back to the archive the first boot wrote.

The browser tier's staleness thresholds come from PR 1's flag-gated query seam
(`?contract=1&feedStaleAfterS=5`), which can only ever LOWER them — the bound is in
`frontend/helpers.js` and the inertness proof pins it.

## Standing note for future systems

Every system phase that adds an upstream (Amtrak, NJ Transit, whatever follows)
must add its partial-outage scenario here, and its seam to `env_seams.py`. The
smoke test will fail loudly if the seam is missing. Nothing will fail if the
scenario is missing, which is why this note exists.
