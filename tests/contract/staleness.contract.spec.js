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
  await openMap(page);
  await expect
    .poll(async () => (await railroadOpacities(page, "MNR")).length, {
      timeout: 60_000,
    })
    .toBeGreaterThan(0);
  expect((await railroadOpacities(page, "MNR")).every((o) => o === 1)).toBe(true);

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
  const lirr = await railroadOpacities(page, "LIRR");
  expect(lirr.length).toBeGreaterThan(0);
  expect(lirr.every((o) => o === 1)).toBe(true);

  // And the status line names the degraded system rather than going generically
  // red, which is the other half of the C2 granularity claim.
  await expect(page.locator("#status")).toContainText(/MNR/i, {
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
  await expect(status).toContainText(/trains/i, { timeout: 60_000 });
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
  await expect.poll(async () => (await opacities()).length, { timeout: 60_000 }).toBeGreaterThan(0);
  expect((await opacities()).every((o) => o === 1)).toBe(true);

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
  // The SAME non-empty guard as the dim check above. Without it this is the one
  // assertion in the file `[].every(...)` satisfies: a recovery that repopulates
  // nothing -- markers swept off the map and never re-added -- would read as green,
  // and "PATH recovers" would be reported against a page showing no PATH trains.
  await expect
    .poll(
      async () => {
        const seen = await opacities();
        return seen.length > 0 && seen.every((o) => o === 1);
      },
      { timeout: DIM_TIMEOUT_MS },
    )
    .toBe(true);
});
