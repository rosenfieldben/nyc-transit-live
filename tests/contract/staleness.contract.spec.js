// contract-e2e: the RENDERING claims, against the real backend and the real page.
//
// This tier asserts only what a rider would see. Every envelope-level truth is
// asserted one layer down in tests/contract/test_contract_api.py, which is faster
// and more precise; a browser is brought in only where the claim is "the page
// shows it". Each spec names the hermetic spec that pins the same behavior against
// mock.js, so a failure localizes: hermetic red means the rendering logic broke,
// hermetic green means the composite does.
//
// THE THRESHOLD IS 25 SECONDS, NOT 5, and the number matters more than it looks.
// The page re-polls every 15s (POLL_INTERVAL_MS) and re-dims on the animation tick
// as ages cross the threshold, so a threshold BELOW the poll interval leaves every
// marker on the page dim for two thirds of every cycle, healthy or not. Under a 5s
// threshold "the down system's markers are dim" is true before anything goes down,
// and the spec passes against a frontend with no per-system logic at all. 25s sits
// above the poll interval with margin, which is what makes the assertions below
// mean something; it is still far under production's 90s, which is the whole
// reason PR 1's threshold seam exists.
//
// Each spec therefore establishes a BRIGHT baseline before breaking anything. That
// step is not ceremony: it is what distinguishes "dimmed because the system is
// down" from "dim all along".

const { test, expect } = require("@playwright/test");

// Ports come from the config, which owns them and hands them to the webServer;
// restating them here would let the two drift the first time either moves.
const { APP_PORT, SIM_PORT } = require("./playwright.contract.config.js").metadata;
const SIM = `http://127.0.0.1:${SIM_PORT}`;
const APP_ORIGIN = `http://127.0.0.1:${APP_PORT}`;
const FEED_STALE_AFTER_S = 25;
// alertsStaleAfterS is deliberately NOT overridden: no spec here asserts alert
// staleness (see tests/contract/README.md), and lowering it below the page's 60s
// alert poll would put the banner's out-of-date marker up permanently.
const PAGE = `/?contract=1&feedStaleAfterS=${FEED_STALE_AFTER_S}`;

// Generous, because a dim assertion has to outlast the threshold plus a page poll
// plus a backend poll. The cost of a too-small deadline here is a flaky suite, and
// rule 3 says a flake is a bug.
const DIM_TIMEOUT_MS = 90_000;

/** Drive the simulator. The specs share one backend, so every spec restores what
 * it changed; see `test.afterEach`. */
async function control(request, body) {
  const response = await request.post(`${SIM}/__control`, { data: body });
  expect(response.ok()).toBeTruthy();
}

async function simState(request) {
  return (await request.get(`${SIM}/__control`)).json();
}

/** Wait until the app has fetched `key` `count` more times. The determinism rule:
 * wait on the app's own behavior, never on the clock. */
async function awaitPolls(request, key, count) {
  const start = (await simState(request)).feeds[key].fetches;
  await expect
    .poll(async () => (await simState(request)).feeds[key].fetches, {
      timeout: 60_000,
    })
    .toBeGreaterThanOrEqual(start + count);
}

/** Open the map page under a DEFAULT-DENY network rule, and record what it blocked.
 *
 * The catch-all matters more than the tile CDN it was written for. An earlier
 * version routed exactly `https://tile.openstreetmap.org/**` and aborted it, which
 * is allow-by-default wearing a hermeticity label: the moment the basemap provider
 * changed, or a font or analytics tag appeared in index.html, the specs would start
 * fetching a public host mid-run with nothing failing. Here EVERY request is seen,
 * anything not same-origin is aborted, and the blocked hosts are handed back so a
 * spec can assert the set it expected. The hermetic tier does the same thing
 * (tests/e2e/mock.js installs `page.route("**\/*")`), for the same reason.
 *
 * Two consequences beyond hermeticity: nothing here asserts on basemap imagery, and
 * a runner that cannot reach the CDN no longer waits out every tile -- which cost a
 * full minute per spec, because Leaflet appends its tiles during initial script
 * execution and they belong to the load event.
 *
 * domcontentloaded rather than load, likewise: the specs wait on their own
 * observables (markers exist, status painted), so waiting on subresources adds
 * nothing but latency.
 */
let blockedHosts = new Set();

async function openMap(page) {
  blockedHosts = new Set();
  await page.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (url.origin === APP_ORIGIN) return route.continue();
    blockedHosts.add(url.host);
    return route.abort();
  });
  await page.goto(PAGE, { waitUntil: "domcontentloaded" });
}

/** The only external host the page is allowed to try for. Asserted in afterEach
 * rather than right after goto, and that timing is the point: with
 * domcontentloaded the tiles have not been requested yet when goto returns, so an
 * assertion there would inspect an empty set and pass no matter what. By afterEach
 * the spec has done all its waiting and the page has had every chance to ask.
 *
 * Kept as an assertion rather than a comment so a NEW external dependency -- a font,
 * a CDN script, a switched basemap provider -- fails and names its host, instead of
 * silently becoming an uncontrolled input to a tier whose claim is that it has none. */
const EXPECTED_EXTERNAL_HOSTS = ["tile.openstreetmap.org"];

/** Opacities of every railroad marker belonging to `system`, read from the marker
 * itself rather than from a CSS class: C2 moved the dimming to an inline opacity,
 * and a class assertion would pass while the visual regressed. */
function railroadOpacities(page, system) {
  return page.evaluate(
    (name) =>
      [...railroads.entries()]
        .filter(([key]) => key.startsWith(`${name}|`))
        .map(([, record]) => record.marker.options.opacity ?? 1),
    system,
  );
}

/** Every railroad marker belonging to `system`, as its opacity and the age of its OWN
 * observation on the page's clock, read in one evaluation so the two cannot straddle a
 * tick. The age is computed here from the served values (served_at, the row's
 * observed_at, the page's own skew offset), not by the helper under test. Null for a row
 * with no clock of its own, which is every Metro-North row. */
function railroadMarkers(page, system) {
  return page.evaluate(
    (name) => {
      const now = Date.now() / 1000 - (minClockOffset ?? 0);
      const servedAt = sources.railroads.servedAt;
      return [...railroads.entries()]
        .filter(([key]) => key.startsWith(`${name}|`))
        .map(([, record]) => {
          const at = record.latest.observed_at;
          return {
            opacity: record.marker.options.opacity ?? 1,
            age: typeof at === "number" ? servedAt - at + Math.max(now - servedAt, 0) : null,
          };
        });
    },
    system,
  );
}

// The margin either side of the threshold a per-marker claim leaves: the page re-dims on
// a 100 ms animation tick and the age is read a moment after, so a marker within two
// seconds of FEED_STALE_AFTER_S is in neither set.
//
// IT COVERS THE TICK AND NOTHING ELSE, which is why every per-marker claim below is
// POLLED rather than read once. A marker's age on the page also carries the phase of the
// page's own 15s poll, and the arithmetic is thin: the freshest observation in the
// committed LIRR capture sits 4s behind its header (design 1.2), the simulator re-stamps
// that header by ONE delta per upstream fetch so the 4s survives every poll
// (upstream_sim.py:_restamp), the backend serves a cache up to its compressed 2s poll old
// (conftest.py CONTRACT_TIMING), and the page holds that response until its next poll. So
// the freshest marker reads about 4s to 6s just after a page poll and about 21s just
// before the next one, against a 23s cut, and at the end of a cycle only the two rows 4s
// and 5s behind the header are still inside it: the capture's positioned observations run
// 4, 5, 6, 8, 9, 10, 13, 18, 20, 28s and older. Two seconds of slop anywhere, a late
// setInterval, the fetch's round trip, a slow evaluate, empties the fresh set, and a
// one-shot read then fails a page that is behaving perfectly.
//
// WIDENING THIS MARGIN IS THE WRONG FIX. It shrinks the fresh set from the same end the
// poll phase does: at a margin of 4s the freshest marker has left the set before the next
// poll arrives, so the one-shot read fails MORE often rather than less. Polling waits for
// the moment the claim is about, which is what C6e3 has done since 6.3.
const TICK_MARGIN_S = 2;

/** The two populations a per-marker freshness claim is made of, split off ONE snapshot so
 * an opacity and the age it is judged against can never come from different reads: the
 * markers comfortably inside the threshold, and those comfortably past it. A marker within
 * TICK_MARGIN_S of FEED_STALE_AFTER_S is in neither, and neither is one with no clock of
 * its own (every Metro-North row). Shared by the three specs that make the claim, so the
 * margin arithmetic above is stated once rather than copied per spec. */
function agePopulations(markers) {
  return {
    fresh: markers.filter((m) => m.age != null && m.age < FEED_STALE_AFTER_S - TICK_MARGIN_S),
    aged: markers.filter((m) => m.age != null && m.age > FEED_STALE_AFTER_S + TICK_MARGIN_S),
  };
}

/** Opacities of every NJ Transit marker, read from the marker exactly as
 * railroadOpacities does. No system filter: NJ Transit is one system, so every
 * marker on the layer shares its freshness and a filter would suggest otherwise. */
function njtOpacities(page) {
  return page.evaluate(() => [...njtTrainRecords.values()].map((r) => r.marker.options.opacity ?? 1));
}

test.afterEach(async ({ request }) => {
  const unexpected = [...blockedHosts].filter((host) => !EXPECTED_EXTERNAL_HOSTS.includes(host));
  blockedHosts = new Set();

  // THE RESTORE RUNS FIRST, and the hermeticity assertion goes in the finally. The
  // other order was a trap: a hermeticity failure threw before the restore loop, so
  // the simulator stayed mutated and every later spec in the run measured a backend
  // it did not set up. One real failure would have become a cascade of misleading
  // ones.
  try {
    // Shared backend, sequential workers: leaving a feed down would silently change
    // what the next spec observes, which is the order dependence that makes an
    // integration suite untrustworthy.
    //
    // DERIVED FROM THE SIMULATOR, not from a hand-kept list. A literal naming the
    // four keys today's specs touch is correct only until someone adds a fifth spec
    // that drives an alerts feed or an archive and forgets to extend it; the leak
    // then survives for the rest of the run and the next spec's baseline quietly
    // measures the wrong thing. Restoring everything is cheap and cannot fall behind.
    const state = await simState(request);
    for (const [key, feed] of Object.entries(state.feeds)) {
      if (feed.mode !== "live") await control(request, { key, mode: "live" });
    }
    for (const [key, archive] of Object.entries(state.archives)) {
      if (archive.publication !== "good") await control(request, { key, publication: "good" });
    }
  } finally {
    expect(unexpected, "the page reached for an external host this tier does not control").toEqual(
      [],
    );
  }
});

test("C6e1. one railroad system down: its markers dim, its sibling's do not", async ({
  page,
  request,
}) => {
  // Hermetic counterpart: tests/e2e/smoke.spec.js "C2a", which drives the same
  // rendering from a stubbed /api/railroads. What only this tier shows is that a
  // REAL backend, polling a REAL socket that stopped answering, produces the
  // envelope that makes the page dim.
  //
  // WHAT A DEGRADED METRO-NORTH SAYS, and the reason this spec matches on a whole clause
  // rather than on the letters MNR. The stale clause is "{system} as of {age} ago"
  // (helpers.js:868), and only a system whose poll age has crossed the threshold earns
  // one: that is the same judgment which dims the markers below, so the two halves of the
  // granularity claim rest on the same fact. A bare /MNR/i is no longer that claim,
  // because 6.3 gave the railroad line two clauses that name MNR with nothing wrong. The
  // UNDATED clause appends "MNR position age unavailable" to any railroad line that
  // renders at all (helpers.js:873), and the WITHHELD clause counts the LIRR rows the
  // position ladder stopped drawing ("LIRR 24 trains not shown, last seen over 10m ago",
  // helpers.js:872): this tier serves the committed capture and does NOT compress
  // OBS_MAX_S, which is a literal rather than a seam for exactly that reason
  // (cache.py:90), so the count is 24 on a perfectly healthy LIRR.
  //
  // The match is deliberately independent of whether those two can raise a line by
  // themselves, which is being settled as this is written: under the riding rule a
  // healthy railroad renders no line at all, under the raising rule it renders one that
  // says MNR twice, and NEITHER ever says "MNR as of". The negative assertion on the
  // bright baseline is the other half, in C6e2's shape.
  const MNR_STALE = /MNR as of \d+[smh]/;
  await openMap(page);
  await expect
    .poll(async () => (await railroadOpacities(page, "MNR")).length, {
      timeout: 60_000,
    })
    .toBeGreaterThan(0);
  expect((await railroadOpacities(page, "MNR")).every((o) => o === 1)).toBe(true);
  await expect(page.locator("#status")).not.toContainText(MNR_STALE);

  await control(request, { key: "MNR", mode: "error" });
  await awaitPolls(request, "MNR", 2);

  // THE PER-SYSTEM CLAIM, stated as a contrast rather than as an absolute: MNR
  // goes dim WHILE LIRR stays bright. A frontend that dimmed the whole railroad
  // layer on any railroad trouble would satisfy the first half and fail here, and
  // that regression is exactly what C2 was written to prevent.
  // The length check is load-bearing: `[].every(...)` is true, so without it this
  // would pass the moment MNR's retention window dropped its trains off the map.
  await expect
    .poll(
      async () => {
        const mnr = await railroadOpacities(page, "MNR");
        return mnr.length > 0 && mnr.every((o) => o < 1);
      },
      { timeout: DIM_TIMEOUT_MS },
    )
    .toBe(true);
  //
  // 6.3 MOVED LIRR'S HALF OF THE CONTRAST, and the reason is F01. Until the age gate a
  // railroad marker dimmed only when its SYSTEM was stale, so "every LIRR marker is
  // bright" was the whole contrast. Since then each marker is also dimmed by its OWN
  // observation's age, and the LIRR this tier serves is the committed capture re-stamped
  // by one delta per poll, so its relative ages survive: fixes from 4 s to 593 s old and
  // placements riding predictions up to hours old, most of them past this tier's 25 s
  // threshold on a perfectly healthy LIRR, by design. "Every LIRR marker is bright" is
  // therefore false with MNR up or down, and the claim becomes one per marker: every LIRR
  // marker whose own observation is fresh stays bright while MNR is down, and there are
  // some; and, F01 against a real backend, every one whose own observation is past the
  // threshold is dimmed although LIRR itself is current.
  //
  // POLLED RATHER THAN READ ONCE, for the arithmetic at TICK_MARGIN_S: the fresh set is
  // two markers wide at the end of a page-poll cycle, so a single read that lands there
  // finds it empty and fails a healthy page. Both halves come off ONE snapshot, so an
  // opacity and the age it is judged against cannot straddle a tick, and each is its own
  // named key: the object diff on a failure says which of the four claims broke, which is
  // what the four separate messages here used to do. someFresh and someAged ARE the
  // `[].every(...)` guard the dim checks state above, since an empty set satisfies every
  // predicate.
  await expect
    .poll(
      async () => {
        const { fresh, aged } = agePopulations(await railroadMarkers(page, "LIRR"));
        return {
          someFresh: fresh.length > 0,
          freshAreLive: fresh.every((m) => m.opacity === 1),
          someAged: aged.length > 0,
          agedAreDim: aged.every((m) => m.opacity < 1),
        };
      },
      {
        message: "LIRR is judged per marker, both ways at once, while MNR is down",
        timeout: DIM_TIMEOUT_MS,
      },
    )
    .toEqual({ someFresh: true, freshAreLive: true, someAged: true, agedAreDim: true });

  // And the status line names the degraded system rather than going generically red,
  // which is the other half of the C2 granularity claim. Matched on the stale clause
  // declared at the top of this spec, so only a DEGRADED Metro-North satisfies it: the
  // bright baseline asserted the same pattern ABSENT, which is what makes this a
  // transition rather than a reading of the standing line.
  await expect(page.locator("#status")).toContainText(MNR_STALE, {
    timeout: DIM_TIMEOUT_MS,
  });
});

test("C6e2. a poisoned subway group is named in the status line", async ({ page, request }) => {
  // Hermetic counterpart: tests/e2e/smoke.spec.js "C2c". The upstream shape is the
  // C3 one: an empty 200, which decodes "successfully" to zero entities, so every
  // poll-level signal stays green and only the per-system block reports it.
  //
  // The status line rather than the markers, because the simulator serves the same
  // capture on all eight group feeds and the frontend groups a train by its ROUTE:
  // every train on the page belongs to the 1-7+S group whichever feed carried it,
  // so BDFM has no markers of its own to dim. Naming the group is the claim that
  // survives that, and it is the rider-facing one anyway.
  await openMap(page);
  const status = page.locator("#status");
  /* MR1 MOVED THE LIVENESS GUARD, AND ONLY THE LIVENESS GUARD. This line read
     toContainText(/trains/i) on a HEALTHY page, which held because the status line carried
     the subway's COUNT ("142 trains") beside its clock. The map redesign takes that line
     apart: the counts are the feed strip's per-feed counts, the clock is the header's, and
     #status carries the problems and nothing else, so on a healthy page it is empty by
     design. The guard has to key on something the healthy page still says.
     THE CLAIM BELOW IS UNTOUCHED, and it is the whole spec: once BDFM is poisoned, the
     status line names it. What replaces the guard says the same thing the count said, where
     the count now is: the subway feed has decoded and the strip is counting its trains.
     NOT "the note is empty", which was the first attempt and was wrong on a REAL backend:
     this tier boots the app against a simulator and the static archives load behind the
     realtime feeds, so for the first polls the note legitimately carries four "still loading"
     sentences. The line below is what says BDFM is not among them yet. */
  await expect(page.locator("#toggle-subway .feed-count"), "the subway feed has decoded").not.toHaveText("", {
    timeout: 60_000,
  });
  await expect(status).not.toContainText(/BDFM/i);

  await control(request, { key: "subway:BDFM", mode: "empty" });
  await awaitPolls(request, "subway:BDFM", 2);

  await expect(status).toContainText(/BDFM/i, { timeout: DIM_TIMEOUT_MS });
});

test("C6e3. PATH, a single-feed source, dims like any other and recovers", async ({
  page,
  request,
}) => {
  // Hermetic counterpart: tests/e2e/smoke.spec.js "C2e". The claim is the one C2
  // made about single-feed sources specifically: PATH has no systems block to
  // carry per-system freshness, so it gets a SYNTHESIZED system named after the
  // source (ingestSystems) rather than an exemption. A regression here would not
  // turn the page red, it would leave PATH bright and confident on a dead feed,
  // which is the failure mode this tier exists to catch.
  await openMap(page);
  const opacities = () =>
    page.evaluate(() =>
      [...pathTrainRecords.values()].map((record) => record.marker.options.opacity ?? 1),
    );
  // 6.3 MOVED THE TWO "BRIGHT" HALVES OF THIS SPEC, for C6e1's reason in PATH's numbers.
  // Each PATH train is dated by its own trip update (section 3.3), and the capture this
  // tier serves (path_rt_gen_a.pb) carries trip updates 18 s to 63 s behind its header,
  // median 38 s, which the simulator's one-delta re-stamp preserves on every poll. So at
  // this tier's 25 s threshold most PATH markers are dimmed by their own observation on a
  // perfectly healthy PATH, and "every PATH marker is bright" is false before anything
  // breaks. The bright claim becomes one per marker: every PATH marker whose own
  // observation is fresh is at full opacity, and there is at least one. Those are the
  // trains whose trip update trails its header by about 20 s, which are fresh only in the
  // first seconds after each page poll, so the check POLLS for such a moment rather than
  // reading once. The DIM half is untouched: a dead PATH dims every marker, fresh or not,
  // which is the single-feed system claim this spec exists for. The ages are computed
  // here from the served values, not by the helper under test.
  const pathMarkers = () =>
    page.evaluate(() => {
      const now = Date.now() / 1000 - (minClockOffset ?? 0);
      const servedAt = sources.path.servedAt;
      return [...pathTrainRecords.values()].map((record) => {
        const at = record.latest.observed_at;
        return {
          opacity: record.marker.options.opacity ?? 1,
          age: typeof at === "number" ? servedAt - at + Math.max(now - servedAt, 0) : null,
        };
      });
    });
  const freshAllBright = async () => {
    const { fresh } = agePopulations(await pathMarkers());
    return fresh.length > 0 && fresh.every((m) => m.opacity === 1);
  };
  await expect.poll(async () => (await opacities()).length, { timeout: 60_000 }).toBeGreaterThan(0);
  await expect.poll(freshAllBright, { timeout: DIM_TIMEOUT_MS }).toBe(true);

  await control(request, { key: "PATH", mode: "error" });
  // Same `[].every(...)` guard as C6e1: an empty marker set must not read as dim.
  await expect
    .poll(
      async () => {
        const seen = await opacities();
        return seen.length > 0 && seen.every((o) => o < 1);
      },
      { timeout: DIM_TIMEOUT_MS },
    )
    .toBe(true);

  // And it clears: the dimming is driven by the age of the last good poll, so a
  // single successful fetch has to undo it. Without this half the spec would pass
  // against a frontend that dims permanently on the first failure.
  await control(request, { key: "PATH", mode: "live" });
  // The SAME non-empty guard as the dim check above, now inside freshAllBright: a
  // recovery that repopulates nothing -- markers swept off the map and never re-added --
  // has no fresh marker and cannot read as green. And a frontend that dimmed permanently
  // on the first failure leaves a fresh marker dim, which is the regression this half
  // exists to catch.
  await expect.poll(freshAllBright, { timeout: DIM_TIMEOUT_MS }).toBe(true);
});

test("C6e4. NJT down: its markers dim, the railroads' do not", async ({ page, request }) => {
  // Hermetic counterpart: tests/e2e/smoke.spec.js "C2f", which drives the same
  // rendering from a stubbed /api/njt-trains. What only this tier shows is that a
  // REAL backend, polling a REAL socket that stopped answering, produces the
  // envelope that makes the page dim.
  //
  // NOT A HYPOTHETICAL. NJ Transit caps getToken at TEN MINTS PER ACCOUNT PER
  // EASTERN DAY (learned 2026-09-02), and production shares that one budget with
  // every developer who runs the fixture generator or the contract monitor. A
  // budget-exhausted NJ Transit is therefore a RECURRING PRODUCTION STATE, not an
  // outage scenario: NJT stops answering while the subway, the railroads, PATH and
  // the ferries all keep decoding, for hours, on an ordinary day. What a rider must
  // see then is exactly this: New Jersey dim and honest about its age, and every
  // other mode untouched.
  await openMap(page);
  await expect.poll(async () => (await njtOpacities(page)).length, { timeout: 60_000 }).toBeGreaterThan(0);
  expect((await njtOpacities(page)).every((o) => o === 1)).toBe(true);

  await control(request, { key: "njt:tripupdate", mode: "error" });
  await awaitPolls(request, "njt:tripupdate", 2);

  // THE PER-SYSTEM CLAIM, stated as a contrast rather than as an absolute, exactly
  // as C6e1 states it for MNR against LIRR: NJ Transit goes dim WHILE the railroads
  // stay bright. A frontend whose stale sweep dimmed every layer on any trouble
  // would satisfy the first half and fail here.
  // The length check is load-bearing: `[].every(...)` is true, so without it this
  // would pass the moment NJT's retention window dropped its trains off the map.
  await expect
    .poll(
      async () => {
        const njt = await njtOpacities(page);
        return njt.length > 0 && njt.every((o) => o < 1);
      },
      { timeout: DIM_TIMEOUT_MS },
    )
    .toBe(true);
  // 6.3 MOVED THE RAILROADS' HALF, for the reason C6e1 gives. Metro-North dates none of
  // its positions, so its markers dim only with its system and every one must stay
  // bright. LIRR's markers are dimmed by their own observations as well, many of them on
  // a perfectly current LIRR, so LIRR's claim is per marker: every one whose own
  // observation is fresh stays bright while NJ Transit is down, and there are some.
  const mnr = await railroadOpacities(page, "MNR");
  expect(mnr.length, "MNR must still have markers to be bright").toBeGreaterThan(0);
  expect(mnr.every((o) => o === 1), "MNR must stay live while NJT is down").toBe(true);
  // POLLED, for the arithmetic at TICK_MARGIN_S and the reason C6e1 gives: the fresh set
  // is two markers wide at the end of a page-poll cycle, so a one-shot read is a coin
  // flip on a healthy page. The non-empty guard rides inside the predicate rather than
  // beside it, because `[].every(...)` is true.
  await expect
    .poll(
      async () => {
        const { fresh } = agePopulations(await railroadMarkers(page, "LIRR"));
        return fresh.length > 0 && fresh.every((m) => m.opacity === 1);
      },
      {
        message: "an LIRR marker whose own observation is fresh must stay live while NJT is down",
        timeout: DIM_TIMEOUT_MS,
      },
    )
    .toBe(true);

  // And the status line names NJ Transit rather than going generically red, which is
  // the other half of the C2 granularity claim. MATCHED ON THE PROBLEMS CLAUSE, not
  // on the words alone: map.js builds the counts from every source's label
  // unconditionally, so "5 NJ Transit" is in the line at all times and a bare
  // /NJ Transit/ would have passed against the healthy baseline measured at the top
  // of this spec.
  await expect(page.locator("#status")).toContainText(/NJ Transit: /, { timeout: DIM_TIMEOUT_MS });
  await expect(page.locator("#status")).toHaveClass(/error/);
});
