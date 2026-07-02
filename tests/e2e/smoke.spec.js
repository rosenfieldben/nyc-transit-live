// Hermetic frontend smoke suite. One chromium browser, all network intercepted
// (mock.js): the two unpkg Leaflet URLs from vendored dist bytes, every /api/*
// from the handcrafted fixtures. A frozen clock (page.clock) makes the arrival
// countdowns and the empty-feed staleness window deterministic; no sleeps.
const { test, expect } = require("@playwright/test");
const fx = require("./fixtures/api");
const { installMocks, json, emptyFeedAt } = require("./mock");

// Common setup: intercept everything, freeze the clock at FROZEN_MS, then load the
// app. Returns the mock ctx so a test can flip overrides / read hit counts.
async function boot(page) {
  const ctx = await installMocks(page);
  // install() alone lets fake time keep flowing; pauseAt() freezes it at FROZEN so
  // the first poll, the clock-skew baseline, and the countdowns are all computed at
  // exactly FROZEN. Tests move time forward explicitly with page.clock.runFor.
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  return ctx;
}

// Buses, subway trains, and railroad trains are divIcon markers, so they show up
// in the DOM and are directly countable. Station dots and route lines are canvas
// (no per-feature DOM node), so those are asserted via map.hasLayer / getLayers in
// page.evaluate. map.js top-level consts (map, stationLayer, ...) live in the
// global lexical scope, which page.evaluate reaches by bare name.
const busMarkers = (page) => page.locator(".bus-marker");
const trainMarkers = (page) => page.locator(".train-marker");
const railroadMarkers = (page) => page.locator(".railroad-marker");
const popup = (page) => page.locator(".leaflet-popup-content");

// Wait until the first poll and the one-shot static loads (stations, route names)
// have all landed, so a test can open popups / toggle layers against a full map.
async function waitForReady(page) {
  await expect(busMarkers(page)).toHaveCount(2);
  await expect(trainMarkers(page)).toHaveCount(2);
  await expect(railroadMarkers(page)).toHaveCount(2);
  await page.waitForFunction(
    () =>
      typeof stationLayer !== "undefined" &&
      stationLayer.getLayers().length === 2 &&
      railroadStationLayer.getLayers().length === 2 &&
      railroadRouteNames.size >= 1 &&
      routeIndex.size === 2,
  );
}

test("1. map boot: every layer populates and the status line shows counts", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);

  // Fully hermetic: nothing escaped interception to a non-local host (basemap
  // tiles, Leaflet, and every /api/* were all served locally).
  expect(ctx.leaks).toEqual([]);

  // Route geometry and station/route-name lookups all built from the fixtures.
  const built = await page.evaluate(() => ({
    subwayStations: stationLayer.getLayers().length,
    railroadStations: railroadStationLayer.getLayers().length,
    subwayRoutes: routeIndex.size,
    mnrName: railroadRouteNames.get("MNR|1"),
    lirrName: railroadRouteNames.get("LIRR|1"),
    subwayLines: map.hasLayer(routeLinesLayer),
    railroadLines: map.hasLayer(railroadRouteLinesLayer),
  }));
  expect(built).toEqual({
    subwayStations: 2,
    railroadStations: 2,
    subwayRoutes: 2,
    mnrName: "Hudson",
    lirrName: "Babylon Branch",
    subwayLines: true,
    railroadLines: true,
  });

  const status = page.locator("#status");
  await expect(status).toContainText("2 buses");
  await expect(status).toContainText("2 trains");
  await expect(status).toContainText("2 railroad");
  await expect(status).toContainText("updated");
  await expect(status).not.toHaveClass(/error/);
});

test("2. empty-feed grace: last-known kept, then cleared past the stale window", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);
  const status = page.locator("#status");

  // First empty poll of the run: within FEED_STALE_AFTER_S, so markers are kept
  // and the status says so. fetched_at drives the frontend's stale window, not the
  // page clock, so we advance the clock only to fire the 15s poll.
  for (const feed of ["buses", "subways", "railroads"]) ctx.overrides[feed] = emptyFeedAt(fx.FROZEN_S + 15);
  await page.clock.runFor(15_000);
  await expect(status).toContainText("showing last known");
  await expect(busMarkers(page)).toHaveCount(2);
  await expect(trainMarkers(page)).toHaveCount(2);
  await expect(railroadMarkers(page)).toHaveCount(2);

  // Later empty poll, fetched_at now well past the run start + FEED_STALE_AFTER_S:
  // the empty set is applied, markers clear, counts read 0.
  for (const feed of ["buses", "subways", "railroads"]) ctx.overrides[feed] = emptyFeedAt(fx.FROZEN_S + 200);
  await page.clock.runFor(15_000);
  await expect(busMarkers(page)).toHaveCount(0);
  await expect(trainMarkers(page)).toHaveCount(0);
  await expect(railroadMarkers(page)).toHaveCount(0);
  await expect(status).not.toContainText("showing last known");
  await expect(status).toContainText("feed empty");
  await expect(status).toContainText("0 buses");
});

test("3. failed poll: a 502 keeps last-known markers and surfaces the error", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);
  const status = page.locator("#status");

  ctx.overrides.buses = (route) => json(route, { detail: "Upstream MTA bus feed error (HTTP 502)" }, 502);
  await page.clock.runFor(15_000);

  // The error is surfaced in the status line (error styling), and the last-known
  // bus markers stay on the map rather than vanishing on a transient failure.
  await expect(status).toContainText("buses: Upstream MTA bus feed error (HTTP 502)");
  await expect(status).toHaveClass(/error/);
  await expect(busMarkers(page)).toHaveCount(2);
  // The feeds that still succeeded are unaffected.
  await expect(trainMarkers(page)).toHaveCount(2);
});

test("4. station popup: arrivals render and a countdown ticks without refetching", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);

  // Open the Times Sq station popup (first subway station in the fixture).
  await page.evaluate(() => stationLayer.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("Times Sq-42 St");
  await expect(popup(page)).toContainText("Northbound");
  await expect(popup(page)).toContainText("2 min"); // first arrival at +90s
  expect(ctx.counts.subwayArrivals).toBe(1);

  // Advance 1s: the 1s render timer repaints the countdown from the cached body
  // (+90s -> +89s crosses the 90s rounding boundary, 2 min -> 1 min). No new fetch.
  await page.clock.runFor(1_000);
  await expect(popup(page)).toContainText("1 min");
  expect(ctx.counts.subwayArrivals).toBe(1);
});

test("5. popup supersession: a later railroad click wins the in-flight race", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);

  // Delay the subway arrivals so the railroad click lands first. The handler bumps
  // its hit count immediately, then holds the response.
  ctx.overrides.subwayArrivals = (route, fixtures) =>
    new Promise((resolve) => setTimeout(() => resolve(json(route, fixtures.subwayArrivals())), 600));

  // Start listening for the (delayed) subway response before triggering it, then
  // open the subway station and immediately the MNR station (index 1: LIRR, MNR).
  const subwayResponse = page.waitForResponse((r) => r.url().includes("/api/subway-arrivals/"));
  await page.evaluate(() => {
    stationLayer.getLayers()[0].openPopup(); // subway: fetch delayed
    railroadStationLayer.getLayers()[1].openPopup(); // railroad: supersedes it
  });

  // Opening the railroad popup closes the subway one, but Leaflet removes a faded
  // popup on a 200ms timer, which the frozen clock holds; advance past it so only
  // the railroad popup remains in the DOM.
  await page.clock.runFor(250);
  await expect(page.locator(".leaflet-popup")).toHaveCount(1);

  // Only the railroad popup content renders (Grand Central, inferred buckets, a
  // train number that subway arrivals never carry).
  await expect(popup(page)).toContainText("Grand Central");
  await expect(popup(page)).toContainText("Inbound");
  await expect(popup(page)).toContainText("#795");

  // Even after the superseded subway fetch resolves, no subway content bleeds in.
  await subwayResponse;
  await expect(popup(page)).not.toContainText("Times Sq");
  await expect(popup(page)).not.toContainText("Northbound");
  await expect(popup(page)).toContainText("Grand Central");
});

test("6. layer toggle: Railroads hides then restores markers, dots and lines", async ({ page }) => {
  await boot(page);
  await waitForReady(page);
  const layerState = () =>
    page.evaluate(() => ({
      stations: map.hasLayer(railroadStationLayer),
      lines: map.hasLayer(railroadRouteLinesLayer),
    }));

  expect(await layerState()).toEqual({ stations: true, lines: true });

  await page.locator("#toggle-railroads").uncheck();
  await expect(railroadMarkers(page)).toHaveCount(0);
  expect(await layerState()).toEqual({ stations: false, lines: false });
  // Other modes are untouched by the railroad toggle.
  await expect(busMarkers(page)).toHaveCount(2);
  await expect(trainMarkers(page)).toHaveCount(2);

  await page.locator("#toggle-railroads").check();
  await expect(railroadMarkers(page)).toHaveCount(2);
  expect(await layerState()).toEqual({ stations: true, lines: true });
});

test("7. bus route: clicking a bus draws the line and banner, clear removes both", async ({ page }) => {
  await boot(page);
  await waitForReady(page);

  await busMarkers(page).first().click();

  const banner = page.locator("#route-banner");
  await expect(banner).toBeVisible();
  await expect(page.locator("#route-banner-label")).toHaveText(/^Bus route \S+$/);
  await expect
    .poll(() => page.evaluate(() => busRouteLayer.getLayers().length))
    .toBe(1); // fixture has one direction

  await page.locator("#route-clear").click();
  await expect(banner).toBeHidden();
  expect(await page.evaluate(() => busRouteLayer.getLayers().length)).toBe(0);
});

test("8. AirTrain: static branches, scheduled popup (not live), toggle", async ({ page }) => {
  await boot(page);
  await waitForReady(page);
  // AirTrain loads independently of waitForReady, so wait for its own layers.
  await page.waitForFunction(
    () =>
      typeof airtrainStationLayer !== "undefined" &&
      airtrainStationLayer.getLayers().length === 3 &&
      airtrainRouteLinesLayer.getLayers().length === 2,
  );
  await expect(page.locator(".airtrain-marker")).toHaveCount(3);

  // Open Federal Circle (fixture order A, B, C -> index 1), served by BOTH branches.
  // The frozen clock (12:00Z == 08:00 America/New_York in July) selects the 7-min band.
  await page.evaluate(() => airtrainStationLayer.getLayers()[1].openPopup());
  await expect(popup(page)).toContainText("Federal Circle");
  await expect(popup(page)).toContainText("no live tracking");
  await expect(popup(page)).toContainText("Jamaica: every ~7 min");
  await expect(popup(page)).toContainText("Howard Beach: every ~7 min");
  await expect(popup(page)).toContainText("(scheduled)");

  // It is a PLAIN popup, not the live-arrivals component: the shared countdown
  // globals stay untouched and none of the live-arrivals markup is used.
  const probe = await page.evaluate(() => ({
    stationTimer,
    openStation,
    hasArrBadge: (document.querySelector(".leaflet-popup-content")?.innerHTML || "").includes("arr-badge"),
  }));
  expect(probe).toEqual({ stationTimer: null, openStation: null, hasArrBadge: false });

  // Toggle hides then restores the AirTrain layers (square markers + route lines).
  await page.locator("#toggle-airtrain").uncheck();
  await expect(page.locator(".airtrain-marker")).toHaveCount(0);
  expect(await page.evaluate(() => map.hasLayer(airtrainRouteLinesLayer))).toBe(false);
  await page.locator("#toggle-airtrain").check();
  await expect(page.locator(".airtrain-marker")).toHaveCount(3);
  expect(await page.evaluate(() => map.hasLayer(airtrainRouteLinesLayer))).toBe(true);
});
