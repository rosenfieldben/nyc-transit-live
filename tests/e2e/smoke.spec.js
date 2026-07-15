// Hermetic frontend smoke suite. One chromium browser; the webServer serves the
// static frontend (including the self-hosted Leaflet under vendor/leaflet) and
// mock.js fulfills every /api/* from the handcrafted fixtures and stubs the basemap
// tiles, so nothing leaves the machine. A frozen clock (page.clock) makes the
// arrival countdowns and the empty-feed staleness window deterministic; no sleeps.
const { test, expect } = require("@playwright/test");
const fx = require("./fixtures/api");
const { installMocks, json, emptyFeedAt } = require("./mock");

// Common setup: intercept everything, freeze the clock at FROZEN_MS, then load the
// app. Returns the mock ctx so a test can flip overrides / read hit counts.
async function boot(page, beforeGoto) {
  const ctx = await installMocks(page);
  // beforeGoto runs after the routes are installed but BEFORE navigation, so a test
  // can seed an override (e.g. a non-empty /api/alerts) that the page's very first
  // fetch picks up. Existing scenarios pass no hook and are unaffected.
  if (beforeGoto) beforeGoto(ctx);
  // Capture any Content-Security-Policy violation. serve.js emits the SAME security
  // headers as the production backend (H3), so the browser enforces the real CSP
  // here; a violation means the CSP would break the real app. Chromium logs each as
  // a console error ("Refused to apply inline style ...", etc.); afterEach fails the
  // test if any were seen. The fix for a violation is the CSP (with a comment naming
  // the feature that needed the relaxation), never loosening or skipping the check.
  page.__cspViolations = [];
  page.on("console", (msg) => {
    const t = msg.text();
    if (/content security policy|refused to (?:load|apply|execute|connect|run)/i.test(t)) {
      page.__cspViolations.push(t);
    }
  });
  // install() alone lets fake time keep flowing; pauseAt() freezes it at FROZEN so
  // the first poll, the clock-skew baseline, and the countdowns are all computed at
  // exactly FROZEN. Tests move time forward explicitly with page.clock.runFor.
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  return ctx;
}

// Every scenario runs under the production security headers (serve.js mirrors them),
// so none may trip the CSP. A violation is a CSP bug to fix, not a test to relax.
test.afterEach(async ({ page }) => {
  expect(page.__cspViolations ?? []).toEqual([]);
});

// Buses, subway trains, and railroad trains are divIcon markers, so they show up
// in the DOM and are directly countable. Station dots and route lines are canvas
// (no per-feature DOM node), so those are asserted via map.hasLayer / getLayers in
// page.evaluate. map.js top-level consts (map, stationLayer, ...) live in the
// global lexical scope, which page.evaluate reaches by bare name.
const busMarkers = (page) => page.locator(".bus-marker");
const trainMarkers = (page) => page.locator(".train-marker");
const railroadMarkers = (page) => page.locator(".railroad-marker");
const pathMarkers = (page) => page.locator(".path-marker");
const ferryMarkers = (page) => page.locator(".ferry-marker");
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

test("9. alerts: a matching subway alert renders above arrivals; a colliding railroad alert does not leak", async ({ page }) => {
  // sub-1 matches Times Sq (stop "127") under system subway. lirr-collide shares
  // BOTH that stop id and a route id ("1") present in the arrivals, but is system
  // LIRR, so system scoping must keep it out of the subway popup.
  const alertsFixture = {
    fetched_at: fx.FROZEN_S,
    alerts: [
      { id: "sub-1", system: "subway", header: "[2] delays at Times Sq-42 St", description: null,
        effect: "UNKNOWN_EFFECT", cause: "UNKNOWN_CAUSE", routes: ["2"], stops: ["127"],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
      { id: "lirr-collide", system: "LIRR", header: "LIRR alert must not leak into subway", description: null,
        effect: "UNKNOWN_EFFECT", cause: "UNKNOWN_CAUSE", routes: ["1"], stops: ["127"],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
    ],
  };
  await boot(page, (ctx) => {
    ctx.overrides.alerts = (route) => json(route, alertsFixture);
  });
  await waitForReady(page);
  // Wait until the alert is actually indexed before opening (the block renders from
  // whatever the store holds at popup-render time).
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byStop.has("subway|127"),
  );

  await page.evaluate(() => stationLayer.getLayers()[0].openPopup()); // Times Sq (id 127)
  await expect(popup(page)).toContainText("[2] delays at Times Sq-42 St");
  await expect(popup(page)).not.toContainText("LIRR alert must not leak");

  // The alert block sits ABOVE the direction sections.
  const order = await page.evaluate(() => {
    const html = document.querySelector(".leaflet-popup-content").innerHTML;
    return { block: html.indexOf("alert-block"), dir: html.indexOf("arr-dir") };
  });
  expect(order.block).toBeGreaterThanOrEqual(0);
  expect(order.block).toBeLessThan(order.dir);
});

test("10. alerts: a bus alert shows in the bus popup and not on a subway train", async ({ page }) => {
  const alertsFixture = {
    fetched_at: fx.FROZEN_S,
    alerts: [
      { id: "bus-m15", system: "bus", header: "M15 detour via 2 Av", description: null,
        effect: "UNKNOWN_EFFECT", cause: "UNKNOWN_CAUSE", routes: ["M15"], stops: [],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
    ],
  };
  await boot(page, (ctx) => {
    ctx.overrides.alerts = (route) => json(route, alertsFixture);
  });
  await waitForReady(page);
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byRoute.has("bus|M15"),
  );

  // Open the M15 bus popup (find its record by route_id, so we click the right bus).
  await page.evaluate(() => {
    [...buses.values()].find((r) => r.latest.route_id === "M15").marker.openPopup();
  });
  await expect(popup(page)).toContainText("M15 detour via 2 Av");
  await expect(popup(page)).toContainText("Bus MTA NYCT_101"); // it is the bus popup

  // A subway train popup (route "1") must NOT show the bus alert (system + route scoped).
  await page.evaluate(() => {
    [...trains.values()].find((r) => r.latest.route_id === "1").marker.openPopup();
  });
  await page.clock.runFor(250); // flush Leaflet's faded-popup removal timer (frozen clock holds it)
  await expect(page.locator(".leaflet-popup")).toHaveCount(1);
  await expect(popup(page)).toContainText("1 train");
  await expect(popup(page)).not.toContainText("M15 detour");
});

test("11. alerts: agency-wide banner shows, dismisses per id, reopens only for a new id", async ({ page }) => {
  const wide = (id, header) => ({
    id, system: "subway", header, description: null, effect: "UNKNOWN_EFFECT", cause: "UNKNOWN_CAUSE",
    routes: [], stops: [], starts_at: fx.FROZEN_S - 600, ends_at: null,
  });
  const ctx = await boot(page, (c) => {
    c.overrides.alerts = (route) =>
      json(route, { fetched_at: fx.FROZEN_S, alerts: [wide("wide-1", "Systemwide: reduced service")] });
  });
  await waitForReady(page);

  const banner = page.locator("#alert-banner");
  await expect(banner).toContainText("Systemwide: reduced service");

  // Dismiss hides it (empties the banner element).
  await banner.locator("#alert-banner-dismiss").click();
  await expect(banner).toBeEmpty();

  // A later poll re-showing the SAME id must not reopen it (loadAlerts() is what the
  // 60s interval calls; invoking it directly avoids a long fake-clock advance).
  await page.evaluate(() => loadAlerts());
  await expect(banner).toBeEmpty();

  // A poll carrying a NEW id reopens the banner with the new header only; the
  // dismissed id stays hidden.
  ctx.overrides.alerts = (route) =>
    json(route, {
      fetched_at: fx.FROZEN_S,
      alerts: [wide("wide-1", "Systemwide: reduced service"), wide("wide-2", "Systemwide: new incident")],
    });
  await page.evaluate(() => loadAlerts());
  await expect(banner).toContainText("Systemwide: new incident");
  await expect(banner).not.toContainText("reduced service");
});

test("12. cold start: station markers appear without a reload once 503s heal", async ({ page }) => {
  // First two /api/subway-stops requests return the backend's warming 503; the
  // third serves the normal fixture, simulating a static-GTFS warmup finishing
  // after page load. The frozen clock makes the retry backoff deterministic:
  // retryUntil schedules via setTimeout, which only fires on clock.runFor.
  let stopRequests = 0;
  await boot(page, (ctx) => {
    ctx.overrides.subwayStops = (route) => {
      stopRequests += 1;
      if (stopRequests <= 2) return json(route, { detail: "Static subway GTFS is still loading." }, 503);
      return json(route, fx.subwayStops());
    };
  });

  // Live-data markers arrive normally; the subway station dots do not (attempt 1 hit a 503).
  await expect(busMarkers(page)).toHaveCount(2);
  await expect
    .poll(() => page.evaluate(() => stationLayer.getLayers().length))
    .toBe(0);

  // Read the retry base from the app so this test tracks the constant instead of
  // hardcoding the backoff. Attempt 2 fires after baseMs (503 again), attempt 3
  // after 2x baseMs (200): the dots appear with NO reload.
  const baseMs = await page.evaluate(() => STATIC_RETRY_BASE_MS);
  await page.clock.runFor(baseMs); // attempt 2: still 503
  await expect
    .poll(() => page.evaluate(() => stationLayer.getLayers().length))
    .toBe(0);
  await page.clock.runFor(baseMs * 2); // attempt 3: fixture lands
  await expect
    .poll(() => page.evaluate(() => stationLayer.getLayers().length))
    .toBe(2);
  expect(stopRequests).toBe(3); // exactly one request per attempt, then the loader stopped
});

// Wait until the PATH static loaders (stations, route lines + name/color
// tables) and the first /api/path poll have all landed. PATH loads
// independently of waitForReady's layers, so PATH tests wait on its own state.
async function waitForPathReady(page) {
  await expect(pathMarkers(page)).toHaveCount(2);
  await page.waitForFunction(
    () =>
      typeof pathStations !== "undefined" &&
      pathStations.getLayers().length === 2 &&
      pathRouteLines.getLayers().length === 4 && // 2 routes x 2 direction polylines
      pathRouteNames.size === 2,
  );
}

test("13. PATH boot: lines, stations and trains render; the toggle hides all three layers", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);
  await waitForPathReady(page);
  expect(ctx.leaks).toEqual([]); // the four PATH endpoints are all mocked locally

  const status = page.locator("#status");
  await expect(status).toContainText("2 PATH");
  await expect(status).not.toHaveClass(/error/);

  const layerState = () =>
    page.evaluate(() => ({
      lines: map.hasLayer(pathRouteLines),
      stations: map.hasLayer(pathStations),
      trains: map.hasLayer(pathTrains),
    }));
  expect(await layerState()).toEqual({ lines: true, stations: true, trains: true });

  await page.locator("#toggle-path").uncheck();
  await expect(pathMarkers(page)).toHaveCount(0);
  expect(await layerState()).toEqual({ lines: false, stations: false, trains: false });
  // Other modes are untouched by the PATH toggle.
  await expect(trainMarkers(page)).toHaveCount(2);
  await expect(railroadMarkers(page)).toHaveCount(2);

  await page.locator("#toggle-path").check();
  await expect(pathMarkers(page)).toHaveCount(2);
  expect(await layerState()).toEqual({ lines: true, stations: true, trains: true });
});

test("14. PATH station popup: bucketed arrivals in order, ticking without refetching", async ({ page }) => {
  // Seed alerts whose selectors would match this station if PATH ever joined
  // the alerts store: one under a hypothetical PATH system tag, and one whose
  // subway stop id collides with the PATH station's numeric id. Without seeded
  // matching data the no-alert-block assertion below would be vacuous (an
  // empty alerts index can never render a block regardless of the render).
  const alertsFixture = {
    fetched_at: fx.FROZEN_S,
    alerts: [
      { id: "path-1", system: "PATH", header: "PATH alert must never render", description: null,
        effect: "UNKNOWN_EFFECT", cause: "UNKNOWN_CAUSE", routes: ["862", "859"], stops: ["26734"],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
      { id: "sub-collide", system: "subway", header: "Colliding subway stop id must not leak", description: null,
        effect: "UNKNOWN_EFFECT", cause: "UNKNOWN_CAUSE", routes: ["859"], stops: ["26734"],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
    ],
  };
  const ctx = await boot(page, (c) => {
    c.overrides.alerts = (route) => json(route, alertsFixture);
  });
  await waitForPathReady(page);
  // The alerts must be indexed BEFORE the popup renders, or the absence
  // assertions pass trivially against a not-yet-loaded store.
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byStop.has("PATH|26734"),
  );

  // Open World Trade Center (first PATH station in the fixture).
  await page.evaluate(() => pathStations.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("World Trade Center");
  await expect(popup(page)).toContainText("To New York");
  await expect(popup(page)).toContainText("To New Jersey");
  await expect(popup(page)).toContainText("2 min"); // the +90s To New York arrival
  await expect(popup(page)).toContainText("5 min"); // the +300s To New Jersey arrival
  expect(ctx.counts.pathArrivals).toBe(1);

  // Buckets render in the fixed order regardless of the fixture's key order
  // (the fixture lists To New Jersey first), with the rider-facing route name.
  const html = await popup(page).innerHTML();
  expect(html.indexOf("To New York")).toBeGreaterThanOrEqual(0);
  expect(html.indexOf("To New York")).toBeLessThan(html.indexOf("To New Jersey"));
  await expect(popup(page)).toContainText("Hoboken - 33rd");
  // PATH has no alerts feed, so no alert block may prepend even though the
  // store now holds alerts that WOULD match this station under a PATH system
  // tag or a colliding subway stop id. This fails if a PATH render ever grows
  // a stationAlertsBlock join.
  expect(html).not.toContain("alert-block");
  expect(html).not.toContain("PATH alert must never render");
  expect(html).not.toContain("Colliding subway stop id must not leak");

  // The shared 1s tick repaints from the cached body: +90s -> +89s crosses the
  // rounding boundary (2 min -> 1 min) with no new fetch.
  await page.clock.runFor(1_000);
  await expect(popup(page)).toContainText("1 min");
  expect(ctx.counts.pathArrivals).toBe(1);
});

test("15. PATH cold start: dots and lines appear without a reload once 503s heal", async ({ page }) => {
  // Mirror of the subway-stops warming spec for BOTH PATH static loaders: the
  // first two requests to each return the backend's warming 503, the third
  // serves the fixture. retryUntil's setTimeout backoff fires on clock.runFor.
  const warmed = { pathStops: 0, pathRoutes: 0 };
  const warming = (key, healed) => (route) => {
    warmed[key] += 1;
    if (warmed[key] <= 2) return json(route, { detail: "Static PATH GTFS is still loading." }, 503);
    return json(route, healed());
  };
  await boot(page, (ctx) => {
    ctx.overrides.pathStops = warming("pathStops", () => fx.pathStops());
    ctx.overrides.pathRoutes = warming("pathRoutes", () => fx.pathRoutes());
  });

  // Live PATH trains arrive normally; the static dots and lines do not (503s).
  await expect(pathMarkers(page)).toHaveCount(2);
  const layerCounts = () =>
    page.evaluate(() => ({
      stations: pathStations.getLayers().length,
      lines: pathRouteLines.getLayers().length,
    }));
  await expect.poll(layerCounts).toEqual({ stations: 0, lines: 0 });

  const baseMs = await page.evaluate(() => STATIC_RETRY_BASE_MS);
  await page.clock.runFor(baseMs); // attempt 2: still 503
  await expect.poll(layerCounts).toEqual({ stations: 0, lines: 0 });
  await page.clock.runFor(baseMs * 2); // attempt 3: fixtures land
  await expect.poll(layerCounts).toEqual({ stations: 2, lines: 4 });
  expect(warmed).toEqual({ pathStops: 3, pathRoutes: 3 }); // one request per attempt, then stopped
});

test("16. PATH identity: markers persist across polls, an advance glides, popups survive", async ({ page }) => {
  // 13c rebuilt this layer wholesale every poll because only unstable bridge
  // hashes existed; 13d's backend serves a stable `id`, so this test pins the
  // INVERSION: the same DOM nodes survive the next poll, an anchored advance
  // GLIDES along the route polyline instead of teleporting or churning, an
  // open popup lives through the poll, and the anchorless train stays placed.
  const pageErrors = [];
  const ctx = await boot(page);
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await waitForPathReady(page);

  // Tag the current marker DOM nodes so reuse (not rebuild) is observable.
  await page.evaluate(() => {
    for (const el of document.querySelectorAll(".path-marker")) el.dataset.original = "1";
  });
  // Open the anchorless WTC train's popup; it must survive the next poll.
  await page.evaluate(() => {
    pathTrainRecords.get("p-1").marker.openPopup();
  });
  await expect(popup(page)).toContainText("scheduled position (no GPS)");

  // Next poll: p-2 advanced Newark -> WTC with the matcher's anchor pair;
  // p-1 unchanged (still anchorless).
  ctx.overrides.path = (route, fixtures) => json(route, fixtures.pathAdvanced());
  await page.clock.runFor(15_000);

  // No add/remove churn: same count, every original DOM node still present.
  await expect(pathMarkers(page)).toHaveCount(2);
  expect(
    await page.evaluate(() => document.querySelectorAll('.path-marker[data-original="1"]').length),
  ).toBe(2);
  // The popup survived the poll (in 13c popups died every poll by design).
  expect(await page.evaluate(() => pathTrainRecords.get("p-1").marker.isPopupOpen())).toBe(true);

  const latLng = (id) =>
    page.evaluate((trainId) => {
      const pos = pathTrainRecords.get(trainId).marker.getLatLng();
      return [pos.lat, pos.lng];
    }, id);

  // The anchored train sits strictly between Newark and WTC right after the
  // poll (f = 0.25 of its 60s segment)...
  const between = ([lat, lng]) =>
    lat > 40.71271 && lat < 40.73454 && lng > -74.16375 && lng < -74.01193;
  const before = await latLng("p-2");
  expect(between(before)).toBe(true);

  // ...and keeps moving as the fake clock advances (animateTrains runs on
  // rAF, which page.clock drives). At the glide midpoint (+30s, f = 0.5) the
  // 862 POLYLINE position is still on the long western segment (lat ~40.734),
  // while the straight chord's midpoint would be at lat ~40.723: asserting
  // lat stays high proves the marker follows the route geometry admitted by
  // the PATH slice cap, not the chord.
  await page.clock.runFor(15_000);
  const mid = await latLng("p-2");
  expect(between(mid)).toBe(true);
  expect(mid[1]).toBeGreaterThan(before[1]); // progressing east toward WTC
  expect(mid[0]).toBeGreaterThan(40.73); // on the polyline, not the chord

  // The anchorless train never moved: placed exactly at its station.
  expect(await latLng("p-1")).toEqual([40.71271, -74.01193]);
  expect(pageErrors).toEqual([]);
});


test("17. PATH click targets: the station dot opens arrivals, the diamond above it opens the train", async ({ page }) => {
  // Regression pin for the occlusion bug: every PATH train is placed at
  // exactly its station's coordinates, so before the pin-style icon offset a
  // real click on an occupied station hit the train diamond (marker pane sits
  // above the station pane) and the arrivals popup was unreachable. This test
  // clicks with the MOUSE, not openPopup(), because programmatic opening is
  // exactly what masked the bug in test 14.
  await boot(page);
  await waitForPathReady(page);

  // WTC has a train placed on it in the fixtures (p-1 shares its coords).
  // Zoom in so neighboring fixture markers cannot straddle the click point.
  await page.evaluate(() => {
    map.setView([40.71271, -74.01193], 14);
  });
  // Container-point lookups are recomputed before EACH click: opening a popup
  // auto-pans the map, so a point captured earlier goes stale and a click at
  // it lands on empty canvas.
  const stationPoint = () =>
    page.evaluate(() => {
      const p = map.latLngToContainerPoint([40.71271, -74.01193]);
      return { x: p.x, y: p.y };
    });
  const box = await page.locator("#map").boundingBox();

  // A click AT the station point lands on the dot: bucketed arrivals open.
  const first = await stationPoint();
  await page.mouse.click(box.x + first.x, box.y + first.y);
  await expect(popup(page)).toContainText("World Trade Center");
  await expect(popup(page)).toContainText("To New York");
  await expect(popup(page)).not.toContainText("scheduled position (no GPS)");

  // Close the arrivals popup first: its box opens upward over the diamond, so
  // the second click would land on the popup instead of the marker beneath.
  await page.evaluate(() => {
    map.closePopup();
  });
  await page.clock.runFor(250); // flush Leaflet's faded-popup removal + autoPan animation

  // A click on the diamond hovering above the dot (icon box spans 4..20px
  // above the anchor) opens the train popup instead.
  const second = await stationPoint();
  await page.mouse.click(box.x + second.x, box.y + second.y - 12);
  await expect(page.locator(".leaflet-popup")).toHaveCount(1);
  await expect(popup(page)).toContainText("scheduled position (no GPS)");
  await expect(popup(page)).toContainText("To New York"); // the train's direction line
});

// Wait until the ferry static loaders (docks, route lines + name/color tables)
// and the first /api/ferry poll have all landed. Ferry loads independently of
// waitForReady's layers, so ferry tests wait on its own state.
async function waitForFerryReady(page) {
  await expect(ferryMarkers(page)).toHaveCount(3);
  await page.waitForFunction(
    () =>
      typeof ferryDocks !== "undefined" &&
      ferryDocks.getLayers().length === 2 &&
      ferryRouteLines.getLayers().length === 2 && // 2 routes x 1 modal polyline
      ferryRouteNames.size === 2,
  );
}

test("18. Ferry boot: lines, docks and boats render; the toggle hides all three layers", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);
  await waitForFerryReady(page);
  expect(ctx.leaks).toEqual([]); // the four ferry endpoints are all mocked locally

  const status = page.locator("#status");
  await expect(status).toContainText("3 ferries");
  await expect(status).not.toHaveClass(/error/);

  const layerState = () =>
    page.evaluate(() => ({
      lines: map.hasLayer(ferryRouteLines),
      docks: map.hasLayer(ferryDocks),
      boats: map.hasLayer(ferryBoats),
    }));
  expect(await layerState()).toEqual({ lines: true, docks: true, boats: true });

  await page.locator("#toggle-ferries").uncheck();
  await expect(ferryMarkers(page)).toHaveCount(0);
  expect(await layerState()).toEqual({ lines: false, docks: false, boats: false });
  // Other modes are untouched by the ferry toggle.
  await expect(busMarkers(page)).toHaveCount(2);

  await page.locator("#toggle-ferries").check();
  await expect(ferryMarkers(page)).toHaveCount(3);
  expect(await layerState()).toEqual({ lines: true, docks: true, boats: true });
});

test("19. Ferry dock popup: route buckets, a dwelling boat shown departing, wheelchair marker, ticking", async ({ page }) => {
  const ctx = await boot(page);
  await waitForFerryReady(page);

  // Open Wall St/Pier 11 (first dock in the fixture, accessible, two route buckets).
  await page.evaluate(() => ferryDocks.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("Wall St/Pier 11");
  await expect(popup(page)).toContainText("NYC Ferry");
  await expect(popup(page)).toContainText("East River");
  await expect(popup(page)).toContainText("South Brooklyn");
  // East River boat arrives in +90s ("2 min"); the South Brooklyn boat is dwelling
  // (arrival 30s past, departure +90s ahead), so its row reads "departs 2 min".
  await expect(popup(page)).toContainText("departs 2 min");
  expect(ctx.counts.ferryArrivals).toBe(1);

  const html = await popup(page).innerHTML();
  // Buckets are alphabetical and the accessible dock shows the marker. Ferry alert
  // rendering (dock stop-scoped, boat route-scoped) has its own test below.
  expect(html.indexOf("East River")).toBeLessThan(html.indexOf("South Brooklyn"));
  expect(html).toContain("popup-access");

  // The shared 1s tick repaints from the cached body with no new fetch: the
  // arriving row crosses +90s -> +89s (2 min -> 1 min).
  await page.clock.runFor(1_000);
  await expect(popup(page)).toContainText("1 min");
  expect(ctx.counts.ferryArrivals).toBe(1);
});

test("20. Ferry boats: STOPPED_AT renders docked, a null-route boat reads Unassigned and shows knots under way", async ({ page }) => {
  await boot(page);
  await waitForFerryReady(page);

  // Status-aware rendering: H2 is STOPPED_AT -> ferry-docked (dimmed); H1 is under
  // way -> ferry-active. This is the current_status field earning its passage.
  const classes = await page.evaluate(() => ({
    h2: ferryBoatRecords.get("H2").marker.getElement().className,
    h1: ferryBoatRecords.get("H1").marker.getElement().className,
  }));
  expect(classes.h2).toContain("ferry-docked");
  expect(classes.h1).toContain("ferry-active");

  // The null-route boat (H3) is kept on the map (14b deliberately) and reads
  // "Unassigned"; under way at 4.0 m/s it shows its speed in knots (H4): 7.8 kn.
  await page.evaluate(() => ferryBoatRecords.get("H3").marker.openPopup());
  await expect(popup(page)).toContainText("Unassigned");
  await expect(popup(page)).toContainText("Boat H099");
  await expect(popup(page)).toContainText("Under way");
  await expect(popup(page)).toContainText("7.8 kn");
  const h3Html = await popup(page).innerHTML();
  expect(h3Html).not.toContain("4.0"); // the raw m/s value is never surfaced

  // The docked boat (H2, STOPPED_AT) shows no speed: dock jitter is noise, not
  // motion. Read H2's own popup element directly rather than the shared
  // .leaflet-popup-content locator: opening a second popup under the frozen clock
  // leaves the first one's fade-out node briefly in the DOM.
  const h2Html = await page.evaluate(() => {
    const rec = ferryBoatRecords.get("H2");
    rec.marker.openPopup();
    return rec.marker.getPopup().getElement().querySelector(".leaflet-popup-content").innerHTML;
  });
  expect(h2Html).toContain("At dock");
  expect(h2Html).not.toContain("kn");
});

test("21. Ferry boats: a boat moves between polls without remove/add churn (id-keyed)", async ({ page }) => {
  const ctx = await boot(page);
  await waitForFerryReady(page);

  // Tag the current boat DOM nodes so reuse (not rebuild) is observable.
  await page.evaluate(() => {
    for (const el of document.querySelectorAll(".ferry-marker")) el.dataset.original = "1";
  });
  const before = await page.evaluate(() => {
    const p = ferryBoatRecords.get("H1").marker.getLatLng();
    return [p.lat, p.lng];
  });

  // Next poll: H1 moved to a new GPS position; H2/H3 unchanged.
  ctx.overrides.ferry = (route, fixtures) => json(route, fixtures.ferryMoved());
  await page.clock.runFor(15_000);

  // No add/remove churn: same count, every original DOM node still present.
  await expect(ferryMarkers(page)).toHaveCount(3);
  expect(
    await page.evaluate(() => document.querySelectorAll('.ferry-marker[data-original="1"]').length),
  ).toBe(3);
  // H1's SAME marker moved to the reported position (railroad GPS precedent).
  const after = await page.evaluate(() => {
    const p = ferryBoatRecords.get("H1").marker.getLatLng();
    return [p.lat, p.lng];
  });
  expect(after).toEqual([40.708, -73.985]);
  expect(after[0]).not.toEqual(before[0]);
});

test("22. Ferry poll split: an empty 200 clears boats, a 502 keeps last-known", async ({ page }) => {
  const ctx = await boot(page);
  await waitForFerryReady(page);
  const status = page.locator("#status");

  // A 502 (transient failure) keeps the last-known boats and surfaces the error,
  // exactly like the other feeds.
  ctx.overrides.ferry = (route) =>
    json(route, { detail: "Upstream NYC Ferry feed error (HTTP 502)" }, 502);
  await page.clock.runFor(15_000);
  await expect(ferryMarkers(page)).toHaveCount(3); // failure retains last-known
  await expect(status).toContainText("ferries: Upstream NYC Ferry feed error (HTTP 502)");
  await expect(status).toHaveClass(/error/);

  // A successful EMPTY poll (the boats went home) clears them IMMEDIATELY: the one
  // deliberate divergence from the other feeds' transient-blip grace, mirroring
  // 14b's server-side empty-replaces / failure-retains split. No stale window walk.
  ctx.overrides.ferry = (route, fixtures) => json(route, fixtures.ferryEnvelope([], fx.FROZEN_S + 60));
  await page.clock.runFor(15_000);
  await expect(ferryMarkers(page)).toHaveCount(0); // empty-success replaces
  await expect(status).toContainText("0 ferries");
});

test("23. Ferry cold start: docks and lines appear without a reload once 503s heal", async ({ page }) => {
  // Mirror of the PATH cold-start spec for BOTH ferry static loaders: the first
  // two requests to each return the backend's warming 503, the third serves the
  // fixture. retryUntil's setTimeout backoff fires on clock.runFor.
  const warmed = { ferryStops: 0, ferryRoutes: 0 };
  const warming = (key, healed) => (route) => {
    warmed[key] += 1;
    if (warmed[key] <= 2) return json(route, { detail: "Static NYC Ferry GTFS is still loading." }, 503);
    return json(route, healed());
  };
  await boot(page, (ctx) => {
    ctx.overrides.ferryStops = warming("ferryStops", () => fx.ferryStops());
    ctx.overrides.ferryRoutes = warming("ferryRoutes", () => fx.ferryRoutes());
  });

  // Live boats arrive normally; the static docks and lines do not (503s).
  await expect(ferryMarkers(page)).toHaveCount(3);
  const layerCounts = () =>
    page.evaluate(() => ({
      docks: ferryDocks.getLayers().length,
      lines: ferryRouteLines.getLayers().length,
    }));
  await expect.poll(layerCounts).toEqual({ docks: 0, lines: 0 });

  const baseMs = await page.evaluate(() => STATIC_RETRY_BASE_MS);
  await page.clock.runFor(baseMs); // attempt 2: still 503
  await expect.poll(layerCounts).toEqual({ docks: 0, lines: 0 });
  await page.clock.runFor(baseMs * 2); // attempt 3: fixtures land
  await expect.poll(layerCounts).toEqual({ docks: 2, lines: 2 });
  expect(warmed).toEqual({ ferryStops: 3, ferryRoutes: 3 }); // one request per attempt, then stopped
});

test("24. Ferry boat that docks mid-session re-icons and refreshes its held-open popup", async ({ page }) => {
  // The cross-poll update path: a boat that changes status between polls must
  // re-icon (ferry-active -> ferry-docked), and a popup a rider left open must
  // re-render from the boat's newest status. Neither is exercised by the move-only
  // fixture pair (ferry -> ferryMoved hold every status constant), so a regression
  // dropping the setIcon or getPopup().update() call would otherwise pass green.
  const ctx = await boot(page);
  await waitForFerryReady(page);

  // H1 is under way; open its popup and confirm the live status text and active icon.
  await page.evaluate(() => ferryBoatRecords.get("H1").marker.openPopup());
  await expect(popup(page)).toContainText("Under way");
  const classOf = (id) =>
    page.evaluate((bid) => ferryBoatRecords.get(bid).marker.getElement().className, id);
  expect(await classOf("H1")).toContain("ferry-active");

  // Next poll: H1 has docked (IN_TRANSIT_TO -> STOPPED_AT), H2/H3 unchanged.
  ctx.overrides.ferry = (route, fixtures) => json(route, fixtures.ferryDocked());
  await page.clock.runFor(15_000);

  // The SAME marker re-iconed to the docked state...
  await page.waitForFunction(() =>
    ferryBoatRecords.get("H1").marker.getElement().className.includes("ferry-docked"),
  );
  // ...and the popup the rider left open re-rendered to the new status with no reopen
  // (ferryBoatPopup reads record.latest, refreshed via getPopup().update()).
  await expect(popup(page)).toContainText("At dock");
});

test("25. Ferry boat color self-heals once routes load after the boat is first seen", async ({ page }) => {
  // The cold-load color race: the small live-boats payload can resolve before
  // /api/ferry-routes (a backend cold start, or just losing the race), so a boat is
  // first seen while ferryRouteColors is still empty and is created with the neutral
  // fallback. It must recolor itself on the first poll after the routes land, NOT
  // stay gray forever. The re-icon guard keys on the RESOLVED color precisely for
  // this: the boat's route_id never changes across these polls, only the color the
  // now-loaded routes table resolves it to. Serve ferry-routes a warming 503 twice,
  // then heal, while live boats poll normally throughout.
  let routeCalls = 0;
  const ctx = await boot(page, (c) => {
    c.overrides.ferryRoutes = (route) => {
      routeCalls += 1;
      if (routeCalls <= 2) return json(route, { detail: "Static NYC Ferry GTFS is still loading." }, 503);
      return json(route, fx.ferryRoutes());
    };
  });

  // Boats render before the routes: H1 (route ER) wears the neutral fallback color.
  await expect(ferryMarkers(page)).toHaveCount(3);
  const fillOf = (id) =>
    page.evaluate(
      (bid) => ferryBoatRecords.get(bid).marker.getElement().querySelector("rect").getAttribute("fill"),
      id,
    );
  await expect.poll(() => fillOf("H1")).toBe("#78909c"); // FERRY_FALLBACK_COLOR, routes not loaded

  // Let the ferry-routes retry backoff heal exactly as the cold-start spec (test 23)
  // does: attempt 2 at +base (still 503), attempt 3 at +2*base serves the fixture.
  // Separate runFor calls let each attempt's fetch settle before the next is scheduled.
  const baseMs = await page.evaluate(() => STATIC_RETRY_BASE_MS);
  await page.clock.runFor(baseMs); // attempt 2: still 503
  await page.clock.runFor(baseMs * 2); // attempt 3: routes fixture lands
  await page.waitForFunction(() => ferryRouteColors.has("ER"));
  await page.clock.runFor(15_000); // next ferry poll re-icons H1 with the now-known color

  // Same boat, same route_id "ER" the whole time, but it recolored to the real ER
  // color: the guard keyed on the resolved color, not the unchanged id.
  await expect.poll(() => fillOf("H1")).toBe("#00839c"); // ER route_color, self-healed
  expect(routeCalls).toBe(3); // two 503s then one healed load, then the loader stopped
});

test("26. Ferry alerts: a dock joins stop AND its served-route alerts; a boat joins by route", async ({ page }) => {
  // /api/alerts carries a STOP-scoped ferry alert (dock 18) and a ROUTE-scoped one
  // (route ER). Ferries join the shared alert pipeline: a dock renders the UNION of
  // its stop-scoped alerts and the route-scoped alerts for every route serving it
  // (H5, from the routes-per-station index on the stop); a boat renders its own
  // route's alerts.
  const ctx = await boot(page, (c) => {
    c.overrides.alerts = (route, fixtures) => json(route, fixtures.ferryAlerts());
  });
  await waitForFerryReady(page);
  // Index the alerts before opening popups, or the assertions race a not-yet-loaded
  // store (the popups render from alertsIndex as of open time).
  await page.waitForFunction(
    () =>
      typeof alertsIndex !== "undefined" &&
      alertsIndex.byStop.has("ferry|18") &&
      alertsIndex.byRoute.has("ferry|ER") &&
      alertsIndex.byRoute.has("ferry|SV"),
  );

  // Dock 18 (Wall St/Pier 11) is served by ER, SB, and SV (its routes-per-station
  // list), so BOTH the stop-scoped alert AND the route-scoped alerts for its routes
  // render here (H5, the acceptance test: this used to be a deliberate scope limit).
  // The SV alert is the load-bearing one: SV has no boat or arrival in the fixtures,
  // so it can reach the dock ONLY through the static routes-per-station index, which
  // proves the join is NOT reading the arrivals' route ids.
  await page.evaluate(() => ferryDocks.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("Wall St/Pier 11 landing closed");
  await expect(popup(page)).toContainText("East River route reroute");
  await expect(popup(page)).toContainText("Soundview route suspended"); // static-only route
  const dockHtml = await popup(page).innerHTML();
  expect(dockHtml).toContain("alert-block");

  // Close the dock popup before opening a boat popup. A real boat-marker click fires
  // the map's closePopupOnClick; the programmatic openPopup below does not, so without
  // this both popups stay open and the popup locator would match two elements. The
  // clock flush clears Leaflet's fade-out removal so the closed popup leaves the DOM.
  await page.evaluate(() => map.closePopup());
  await page.clock.runFor(250);

  // Boat H1 is on route ER: its popup renders the ROUTE-scoped alert...
  await page.evaluate(() => ferryBoatRecords.get("H1").marker.openPopup());
  await expect(page.locator(".leaflet-popup")).toHaveCount(1);
  await expect(popup(page)).toContainText("East River route reroute");
  const boatHtml = await popup(page).innerHTML();
  expect(boatHtml).toContain("alert-block");
  // ...and neither the stop-scoped dock alert nor a different route's alert (SV)
  // leaks onto the boat: a boat joins by its own route only.
  expect(boatHtml).not.toContain("Wall St/Pier 11 landing closed");
  expect(boatHtml).not.toContain("Soundview route suspended");

  // A null-route boat (H3) matches no route alert, so no block at all.
  await page.evaluate(() => map.closePopup());
  await page.clock.runFor(250);
  await page.evaluate(() => ferryBoatRecords.get("H3").marker.openPopup());
  await expect(page.locator(".leaflet-popup")).toHaveCount(1);
  const nullBoatHtml = await popup(page).innerHTML();
  expect(nullBoatHtml).not.toContain("alert-block");
});

test("27. alerts: a route-scoped alert reaches a station it serves with no imminent train (H5)", async ({ page }) => {
  // Times Sq (127) serves routes 1/2/3 (its routes-per-station list), but the
  // arrivals fixture only has imminent 1 and 2 trains. A route-scoped alert on
  // route "3" must still surface: this is the case the static routes-per-station
  // join closes (before H5, a route with no imminent train dropped out). A route
  // "Z" alert that does NOT serve the station must NOT appear.
  const alertsFixture = {
    fetched_at: fx.FROZEN_S,
    alerts: [
      { id: "route-3", system: "subway", header: "[3] suspended overnight", description: null,
        effect: "NO_SERVICE", cause: "MAINTENANCE", routes: ["3"], stops: [],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
      { id: "route-Z", system: "subway", header: "[Z] alert not serving Times Sq", description: null,
        effect: "DETOUR", cause: "CONSTRUCTION", routes: ["Z"], stops: [],
        starts_at: fx.FROZEN_S - 600, ends_at: null },
    ],
  };
  await boot(page, (ctx) => {
    ctx.overrides.alerts = (route) => json(route, alertsFixture);
  });
  await waitForReady(page);
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byRoute.has("subway|3"),
  );

  await page.evaluate(() => stationLayer.getLayers()[0].openPopup()); // Times Sq (id 127)
  // Route 3 serves the station (static index) even with no imminent 3 train: shown.
  await expect(popup(page)).toContainText("[3] suspended overnight");
  // Route Z does not serve this station: absent.
  await expect(popup(page)).not.toContainText("[Z] alert not serving Times Sq");
});

test("28. honest freshness: a backend cache 200s old on first load reads stale, countdowns unshifted (R1)", async ({ page }) => {
  // THE AUDIT SCENARIO end-to-end. The first poll hits a backend whose cache is
  // already 200s old: served_at is FROZEN_S (the response was just built) but
  // fetched_at is FROZEN_S - 200 (the last successful upstream poll). Before R1 the
  // frontend calibrated its clock off fetched_at, which (a) made the poll-age term
  // read ~zero so stale looked fresh, and (b) shifted every countdown by 200s.
  //
  // ALL FIVE vehicle feeds are served stale, not just buses: minClockOffset is the
  // MINIMUM offset across the feeds, so a single fresh feed would pin it to ~0 and
  // make the countdown assertion pass regardless of which timestamp calibration used.
  // With every feed 200s stale, the old fetched_at calibration would drive
  // minClockOffset to 200 and shift the asserted subway countdown to "5 min"; only
  // the served_at calibration keeps it "2 min".
  const stale = (data, key) => ({
    fetched_at: fx.FROZEN_S - 200, feed_timestamp: fx.FROZEN_S - 205, served_at: fx.FROZEN_S, [key]: data,
  });
  await boot(page, (c) => {
    c.overrides.buses = (route) => json(route, stale(fx.buses().data, "data"));
    c.overrides.subways = (route) => json(route, stale(fx.subways().data, "data"));
    c.overrides.railroads = (route) => json(route, stale(fx.railroads().data, "data"));
    c.overrides.path = (route) => json(route, stale(fx.path().trains, "trains"));
    c.overrides.ferry = (route) => json(route, stale(fx.ferry().boats, "boats"));
  });
  await waitForReady(page);

  const status = page.locator("#status");
  // (a) fixed: the 200s server cache age (served_at - fetched_at) is surfaced
  // immediately on the first load and takes the error styling ("trains" is the
  // subway source's status-line label).
  await expect(status).toContainText("trains: as of 3m ago");
  await expect(status).toHaveClass(/error/);

  // (b) fixed: calibrating off served_at keeps minClockOffset ~0, so the subway
  // countdown is UNSHIFTED. Times Sq's first Northbound arrival is at +90s, which
  // must still read "2 min" (the old 200s clock shift would have made it "5 min").
  await page.evaluate(() => stationLayer.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("Times Sq-42 St");
  await expect(popup(page)).toContainText("2 min");
});

test("29. honest freshness: an alerts outage past the threshold shows the banner marker (R1)", async ({ page }) => {
  // The alerts loop swallows failures by design (no error state). R1's honesty
  // marker is the one signal that the alert index has stopped updating. Boot with a
  // fresh empty alerts response (served_at = FROZEN_S) so the last-success timestamp
  // is set, then take the alerts feed down and let time cross ALERTS_STALE_AFTER_S.
  const ctx = await boot(page, (c) => {
    c.overrides.alerts = (route, fixtures) => json(route, fixtures.alerts());
  });
  await waitForReady(page);
  // Drive the first alerts poll and wait for its served_at to register (the boot
  // poll is fire-and-forget), so the outage has a last-success baseline.
  await page.evaluate(() => loadAlerts());
  await page.waitForFunction(() => typeof alertsServedAt !== "undefined" && alertsServedAt !== null);

  const banner = page.locator("#alert-banner");
  await expect(banner).toBeEmpty(); // fresh, no alerts, no marker

  // The alerts feed goes down: every later poll fails, so the last successful
  // served_at (FROZEN_S) stops advancing.
  ctx.overrides.alerts = (route) => json(route, { detail: "Alerts upstream error (HTTP 502)" }, 502);
  // Jump the clock past ALERTS_STALE_AFTER_S (300s), so the alert index is now 310s
  // old. A failed poll keeps the last success (the swallow-failures design), then the
  // 15s refreshAll drives tickAlertBanner in production; invoke both directly here,
  // as test 11 drives loadAlerts directly, to keep the test deterministic.
  await page.clock.fastForward(310_000);
  await page.evaluate(() => loadAlerts()); // fails (502): keeps the last-success served_at
  await page.evaluate(() => tickAlertBanner());
  await expect(banner).toContainText("alerts may be out of date");
});

test("30. a wedged feed no longer freezes the loop: healthy feeds keep polling, the hung one times out (R2)", async ({ page }) => {
  // Wedge /api/buses so its fetch never lands. Under the OLD whole-cycle `refreshing`
  // lock this kept `refreshing === true` forever, so every later 15s tick early-
  // returned and the WHOLE map froze. With per-source inFlight only the bus feed is
  // affected; the others keep polling, and the wedged fetch is cut off by its own
  // AbortSignal.timeout (FETCH_DEADLINE_MS) instead of hanging indefinitely.
  const ctx = await boot(page);
  await waitForReady(page);
  const status = page.locator("#status");

  ctx.overrides.buses = () => new Promise(() => {}); // never fulfills -> hangs forever

  // Tick 1: buses hangs; the subway feed drops to a single train (a visible change).
  ctx.overrides.subways = (route, fixtures) =>
    json(route, fixtures.envelope(fixtures.subways().data.slice(0, 1), fx.FROZEN_S + 15));
  await page.clock.runFor(15_000);
  // The healthy subway feed updated (2 -> 1) even though buses is wedged: the loop
  // did NOT freeze. The bus markers stay last-known (their fetch never resolved).
  await expect(trainMarkers(page)).toHaveCount(1);
  await expect(busMarkers(page)).toHaveCount(2);
  const subwayPolls = ctx.counts.subways;

  // Tick 2: buses still wedged; subways back to two trains. The bus fetch started at
  // tick 1 now hits FETCH_DEADLINE_MS (15s) and aborts, surfacing the hung feed's
  // error (its R1 error state) in the status line while last-known markers stay put.
  ctx.overrides.subways = (route, fixtures) => json(route, fixtures.subways());
  await page.clock.runFor(15_000);
  await expect(trainMarkers(page)).toHaveCount(2); // a later tick still polled the healthy feed
  await expect(status).toContainText("timed out"); // the wedged feed timed out and surfaced it
  await expect(status).toHaveClass(/error/);
  await expect(busMarkers(page)).toHaveCount(2); // last-known kept, never blanked
  expect(ctx.counts.subways).toBeGreaterThan(subwayPolls); // the healthy feed kept being polled
});

test("31. a station popup whose endpoint is wedged bounds the fetch instead of hanging on Loading (R2)", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);

  // Wedge the arrivals endpoint BEFORE opening the popup so its very first fetch never
  // lands. Without a fetch deadline the popup would sit on "Loading arrivals…" forever;
  // the AbortSignal.timeout aborts the fetch at FETCH_DEADLINE_MS so the popup resolves
  // to an error. This is the NON-VACUOUS timeout assertion: revert the signal from
  // openStationArrivals and this test hangs on Loading, so it genuinely exercises the
  // R2 deadline rather than pre-existing behavior.
  ctx.overrides.subwayArrivals = () => new Promise(() => {}); // never fulfills

  await page.evaluate(() => stationLayer.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("Loading arrivals"); // pending: the fresh-open loading state

  await page.clock.runFor(15_000); // the AbortSignal.timeout fires
  await expect(popup(page)).toContainText("Arrivals unavailable"); // bounded to an error, not wedged
  await expect(popup(page)).not.toContainText("Loading arrivals");
});

test("32. a hung BACKGROUND popup refresh is swallowed and keeps the last-known arrivals (R2)", async ({ page }) => {
  const ctx = await boot(page);
  await waitForReady(page);

  // Open the popup successfully so it holds arrivals data (a +90s Northbound train).
  await page.evaluate(() => stationLayer.getLayers()[0].openPopup());
  await expect(popup(page)).toContainText("Times Sq-42 St");
  await expect(popup(page)).toContainText("Northbound");

  // Wedge the endpoint. The 15s tick fires a BACKGROUND refresh (refresh:true) whose
  // fetch times out at FETCH_DEADLINE_MS. A background timeout is swallowed on the
  // refresh path, so the popup KEEPS its last-known arrivals rather than blanking,
  // reverting to Loading, or showing an error. (If the refresh catch dropped its
  // `if (!refresh)` guard and surfaced the timeout, the "unavailable" assertion fails.)
  ctx.overrides.subwayArrivals = () => new Promise(() => {}); // never fulfills
  await page.clock.runFor(15_000); // fire the background refresh (it hangs)
  await page.clock.runFor(15_000); // its fetch hits the deadline and aborts

  await expect(popup(page)).toContainText("Times Sq-42 St"); // still the same station
  await expect(popup(page)).toContainText("Northbound"); // arrivals kept, not blanked
  await expect(popup(page)).not.toContainText("Loading"); // never reverted to the loading state
  await expect(popup(page)).not.toContainText("unavailable"); // a timed-out background refresh stays quiet
});
