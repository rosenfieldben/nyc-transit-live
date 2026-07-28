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

const SIM = "http://127.0.0.1:5175";
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

/** Open the map page.
 *
 * TILES ARE ABORTED, for two reasons and in that order of importance. First,
 * hermeticity: this tier's whole claim is that every byte the app and the page
 * receive comes from something a test controls, and a basemap fetched from a
 * public CDN would quietly break that. Nothing here asserts on imagery. Second,
 * speed: Leaflet appends its tile images during initial script execution, so they
 * are part of the load event, and a runner that cannot reach the CDN waits out
 * every one of them before goto returns.
 *
 * domcontentloaded rather than load for the same reason: the specs wait on their
 * own observables (markers exist, status painted), so waiting on subresources adds
 * nothing but latency.
 */
async function openMap(page) {
  await page.route("https://tile.openstreetmap.org/**", (route) => route.abort());
  await page.goto(PAGE, { waitUntil: "domcontentloaded" });
}

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
  // Shared backend, sequential workers: leaving a feed down would silently change
  // what the next spec observes, which is the order dependence that makes an
  // integration suite untrustworthy.
  for (const key of ["MNR", "LIRR", "subway:BDFM", "PATH"]) {
    await control(request, { key, mode: "live" });
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
  await expect
    .poll(async () => (await opacities()).every((o) => o === 1), {
      timeout: DIM_TIMEOUT_MS,
    })
    .toBe(true);
});
