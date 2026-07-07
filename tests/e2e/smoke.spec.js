// Hermetic frontend smoke suite. One chromium browser, all network intercepted
// (mock.js): the two unpkg Leaflet URLs from vendored dist bytes, every /api/*
// from the handcrafted fixtures. A frozen clock (page.clock) makes the arrival
// countdowns and the empty-feed staleness window deterministic; no sleeps.
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
const pathMarkers = (page) => page.locator(".path-marker");
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

test("16. PATH churn: wholesale rebuild across disjoint-id and identical-id polls", async ({ page }) => {
  // The pinned 13c behavior: the bridge's trip ids churn 100% on an upstream
  // refresh, and the frontend rebuilds the layer wholesale instead of diffing
  // on trip_id. Count stability alone cannot distinguish a wholesale rebuild
  // from a (forbidden) trip_id-keyed diff, so this pins BOTH regimes: churned
  // ids keep the count with no error, and an IDENTICAL payload still replaces
  // every marker DOM node, which a keyed diff would have reused.
  const genA = fx.path("a").trains.map((t) => t.trip_id);
  const genB = fx.path("b").trains.map((t) => t.trip_id);
  expect(genA.filter((id) => genB.includes(id))).toEqual([]); // fully disjoint ids

  const pageErrors = [];
  const ctx = await boot(page);
  page.on("pageerror", (err) => pageErrors.push(String(err)));
  await waitForPathReady(page);

  // Poll 2: same physical picture, 100% churned trip ids (an upstream refresh).
  ctx.overrides.path = (route, fixtures) => json(route, fixtures.path("b"));
  await page.clock.runFor(15_000);
  await expect(pathMarkers(page)).toHaveCount(2);
  await expect(page.locator("#status")).toContainText("2 PATH");
  await expect(page.locator("#status")).not.toHaveClass(/error/);

  // Poll 3: byte-identical to poll 2 (normal for PATH: the bridge regenerates
  // faster than the upstream refreshes). Tag the current marker elements, then
  // let the poll fire: a wholesale rebuild replaces every element, so no tag
  // survives; a trip_id-keyed diff would keep the tagged elements alive.
  await page.evaluate(() => {
    for (const el of document.querySelectorAll(".path-marker")) el.dataset.stale = "1";
  });
  await page.clock.runFor(15_000);
  await expect(pathMarkers(page)).toHaveCount(2);
  expect(
    await page.evaluate(() => document.querySelectorAll('.path-marker[data-stale="1"]').length),
  ).toBe(0);

  expect(pageErrors).toEqual([]);
  expect(ctx.counts.path).toBe(3); // exactly the three polls
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

  // WTC has a train placed on it in the fixtures (path-a-1 shares its coords).
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
