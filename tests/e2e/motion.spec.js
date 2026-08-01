// A2: reduced motion.
//
// THE PRINCIPLE THESE SPECS DEFEND: reduced motion changes HOW a position updates,
// never WHAT is shown. A gliding train and a stepping train are at the same place at
// the same time. So every spec here pairs "it stopped moving between polls" with "it
// still arrived at the right place when the poll landed", because a gate that quietly
// froze the data would satisfy the first half and betray the rider entirely.
//
// The preference is emulated with page.emulateMedia rather than the reducedMotion
// context option, for two reasons. It must be set BEFORE goto, because Leaflet reads
// its animation options once when the map is constructed and a preference applied
// afterwards would arrive too late for exactly the thing being tested. And the context
// option did not reach the page in this harness (matchMedia reported false inside a
// test that declared it), while emulateMedia demonstrably does; both were checked
// directly rather than assumed. Same media feature either way, so the page takes the
// real path a rider's system preference would put it on.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const POLL_MS = 15_000;

async function open(page, { reduce = false } = {}) {
  if (reduce) await page.emulateMedia({ reducedMotion: "reduce" });
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
      timeout: 15_000,
    })
    .toBeGreaterThan(5);
}

// One subway train's screen position, which is what the glide moves between polls.
const trainAt = (page) =>
  page.evaluate(() => {
    const record = [...trains.values()][0];
    const ll = record.marker.getLatLng();
    return { lat: ll.lat, lng: ll.lng };
  });

test.describe("with reduced motion requested", () => {
  test("A5a. positions step to the truth per poll instead of tweening between them", async ({ page }) => {
    await installMocks(page);
    await open(page, { reduce: true });

    const start = await trainAt(page);
    // Advance well inside one poll interval. The glide would have moved the train
    // several times over by now: the animation tick runs every TRAIN_TICK_MS and
    // interpolates toward the next stop on every one of them.
    await page.clock.runFor(POLL_MS - 3000);
    const midInterval = await trainAt(page);
    expect(midInterval, "no interpolation between polls under reduced motion").toEqual(start);

    // AND THE DATA STILL ARRIVES. This is the half that matters: the gate must not have
    // frozen anything, so when the next poll lands the train is where the feed says.
    // Without this assertion a completely broken map would pass the spec above.
    await page.clock.runFor(POLL_MS + 2000);
    await expect
      .poll(async () => (await trainAt(page)).lat !== start.lat || (await trainAt(page)).lng !== start.lng)
      .toBe(true);
  });

  test("A5b. the marker transitions and Leaflet's own animations are off", async ({ page }) => {
    await installMocks(page);
    await open(page, { reduce: true });

    // The class the whole css gate hangs on.
    await expect(page.locator("html")).toHaveClass(/reduced-motion/);

    const transitions = await page.evaluate(() => {
      const out = {};
      for (const cls of ["bus-marker", "ferry-marker"]) {
        const el = document.querySelector(`.${cls} svg`);
        if (el) out[cls] = getComputedStyle(el).transitionDuration;
      }
      return out;
    });
    for (const [cls, duration] of Object.entries(transitions)) {
      expect(parseFloat(duration), `${cls} must not transition`).toBe(0);
    }

    // Leaflet's own zoom and pan animation, read from the map it was constructed with.
    const options = await page.evaluate(() => ({
      zoom: map.options.zoomAnimation,
      fade: map.options.fadeAnimation,
      markerZoom: map.options.markerZoomAnimation,
    }));
    expect(options).toEqual({ zoom: false, fade: false, markerZoom: false });
  });

  test("A5c. the station panel appears without animating, and says the same things", async ({ page }) => {
    await installMocks(page);
    await open(page, { reduce: true });

    // The panel is docked open at this width. Close and reopen it, which is the
    // transition a rider would see.
    await page.locator("#stations-toggle").click();
    await expect(page.locator("#stations-panel")).toBeHidden();
    await page.locator("#stations-toggle").click();
    await expect(page.locator("#stations-panel")).toBeVisible();

    const style = await page.evaluate(() => {
      const el = document.getElementById("stations-panel");
      const cs = getComputedStyle(el);
      return { transition: cs.transitionDuration, animation: cs.animationDuration };
    });
    expect(parseFloat(style.transition), "the panel must not transition").toBe(0);
    expect(parseFloat(style.animation), "the panel must not animate").toBe(0);

    // AND IT STILL WORKS. Reduced motion changes how things move, not what they say:
    // the search, the results and the arrivals are all exactly as they were.
    await page.locator("#stations-search").fill("times");
    await page.locator("#stations-results button.station-row").first().click();
    await expect(page.locator("#stations-detail")).toContainText("Northbound");
    await expect(page.locator("#stations-detail")).toContainText("in 2 minutes");
  });
});

test("A5d. without the preference, the map still glides", async ({ page }) => {
  // The control. Without this, every assertion above would also pass on a map whose
  // animation had simply been deleted, and the suite would be pinning a regression
  // rather than a preference.
  await installMocks(page);
  await open(page);

  await expect(page.locator("html")).not.toHaveClass(/reduced-motion/);
  expect(await page.evaluate(() => map.options.zoomAnimation)).toBe(true);

  const start = await trainAt(page);
  await page.clock.runFor(3000); // well inside one poll interval
  const moved = await trainAt(page);
  expect(
    moved.lat !== start.lat || moved.lng !== start.lng,
    "between polls the train should be interpolating toward its next stop",
  ).toBe(true);
});
