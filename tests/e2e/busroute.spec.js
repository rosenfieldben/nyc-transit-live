// A3: the bus route line follows the POPUP, not the click.
//
// The defect these specs close was recorded twice in earlier phases and had no owning
// surface until this one: the route-line draw was bound to the marker's `click` event,
// so it was triggered by a gesture rather than by the state that gesture produced. Any
// other way of opening the popup drew nothing, and a rider got a popup describing a
// route with no route on the map.
//
// Same hermetic harness as the rest of the suite.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const { expectPopupState } = require("./popup");
const fx = require("./fixtures/api");

async function open(page) {
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
      timeout: 15_000,
    })
    .toBeGreaterThan(5);
}

// How many polylines are currently in the bus route layer. Asked of Leaflet rather than
// of the DOM because the lines are canvas-rendered and have no elements to count.
const drawnLines = (page) =>
  page.evaluate(() => {
    let n = 0;
    busRouteLayer.eachLayer(() => { n += 1; });
    return n;
  });

const firstBusId = (page) => page.evaluate(() => [...buses.keys()][0]);

test("A7a. a popup opened WITHOUT a click still draws the route", async ({ page }) => {
  // THE DEFECT, DIRECTLY. openPopup is what every non-mouse path uses: a keyboard
  // activation, the station panel's map sync, anything programmatic. Before this change
  // it opened the popup and drew nothing at all, because only the click handler drew.
  await installMocks(page);
  await open(page);

  expect(await drawnLines(page), "no line before anything is opened").toBe(0);

  const id = await firstBusId(page);
  await page.evaluate((busId) => buses.get(busId).marker.openPopup(), id);
  await expectPopupState(page, { registry: "buses", key: id }, true);

  await expect.poll(async () => drawnLines(page), { timeout: 5_000 }).toBeGreaterThan(0);
  // And the banner that names the route appears with it, since the two are one surface.
  await expect(page.locator("#route-banner")).toBeVisible();
  await expect(page.locator("#route-banner-label")).toContainText("Bus route");
});

test("A7b. closing the popup clears the line and the banner", async ({ page }) => {
  await installMocks(page);
  await open(page);

  const id = await firstBusId(page);
  await page.evaluate((busId) => buses.get(busId).marker.openPopup(), id);
  await expect.poll(async () => drawnLines(page)).toBeGreaterThan(0);

  await page.evaluate((busId) => buses.get(busId).marker.closePopup(), id);
  await expect.poll(async () => drawnLines(page)).toBe(0);
  await expect(page.locator("#route-banner")).toBeHidden();
});

test("A7c. a mouse open draws the line exactly once, and does not draw then clear", async ({ page }) => {
  // THE DOUBLE-FIRE HUNT. Moving the draw to popupopen while leaving the old click
  // handler in place would have drawn twice for a mouse rider, and because a same-bus
  // re-click CLOSES the popup, the pair would have raced a draw against a clear on a
  // single gesture. Counting fetches is the sharp instrument: two draws for one open is
  // two requests for one rider action, whatever the map ends up looking like.
  const ctx = await installMocks(page);
  await open(page);

  const id = await firstBusId(page);
  const before = ctx.counts.busRoute ?? 0;

  // A real click on the marker element, which is the mouse path end to end.
  await page.evaluate((busId) => {
    const el = buses.get(busId).marker.getElement();
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  }, id);
  await expectPopupState(page, { registry: "buses", key: id }, true);
  await expect.poll(async () => drawnLines(page), { timeout: 5_000 }).toBeGreaterThan(0);

  // Settle, then count. One open, one fetch.
  await page.waitForTimeout(300);
  expect(
    (ctx.counts.busRoute ?? 0) - before,
    "one open must produce exactly one route fetch",
  ).toBe(1);

  // AND THE LINE IS STILL THERE. A draw-then-clear race leaves zero lines while the
  // popup is open, which is precisely the state the fetch count alone cannot see.
  expect(await drawnLines(page), "the line must survive the gesture that drew it").toBeGreaterThan(0);
  await expect(page.locator("#route-banner")).toBeVisible();
});

test("A7d. opening a different bus replaces the line rather than losing it", async ({ page }) => {
  // THE ORDERING TRAP. Leaflet closes the OLD popup before opening the new one, so the
  // close handler for bus A runs after bus B has begun. Without an ownership check, A's
  // close would wipe B's line and the rider would be left with a popup naming a route
  // and no route drawn: the original defect, reintroduced from the other end.
  await installMocks(page);
  await open(page);

  const ids = await page.evaluate(() => {
    const seen = [];
    for (const [id, record] of buses) {
      if (record.latest && record.latest.route_id) seen.push(id);
      if (seen.length === 2) break;
    }
    return seen;
  });
  expect(ids.length, "the fixture must have two buses with routes").toBe(2);

  await page.evaluate((busId) => buses.get(busId).marker.openPopup(), ids[0]);
  await expect.poll(async () => drawnLines(page)).toBeGreaterThan(0);
  const firstLabel = await page.locator("#route-banner-label").textContent();

  await page.evaluate((busId) => buses.get(busId).marker.openPopup(), ids[1]);
  // Through the harness helper, which is the only thing in this suite that knows a
  // closing popup lingers in the DOM. The first draft of this line hand-rolled the
  // workaround and the draft before that died on strict mode; see tests/e2e/popup.js.
  await expectPopupState(page, { registry: "buses", key: ids[1] }, true);
  // And the one it replaced is genuinely closed, which the document could not tell us.
  await expectPopupState(page, { registry: "buses", key: ids[0] }, false);

  // A line is still drawn, and the banner names the SECOND bus's route.
  await expect.poll(async () => drawnLines(page), { timeout: 5_000 }).toBeGreaterThan(0);
  await expect(page.locator("#route-banner")).toBeVisible();
  const secondLabel = await page.locator("#route-banner-label").textContent();
  expect(secondLabel, "the banner must name the newly opened bus's route").not.toBe(firstLabel);
});

test("A7e. Leaflet closes the old popup BEFORE opening the new one", async ({ page }) => {
  // THE ASSUMPTION THE OWNERSHIP CHECK RESTS ON, pinned because the check itself cannot
  // be reached today. Mutation testing said so plainly: removing busRouteOwnedBy leaves
  // all four specs above green, because this ordering means bus A's clear always lands
  // before bus B's draw and an unconditional clear is therefore harmless.
  //
  // Rather than delete a guard that encodes a real invariant, or keep one while claiming
  // it catches something it cannot, the invariant is asserted directly. If a future
  // Leaflet fires popupopen before popupclose, this fails, and on that day the ownership
  // check stops being belt and starts being the only thing standing between a rider and
  // a popup that names a route with no route drawn.
  await installMocks(page);
  await open(page);

  const ids = await page.evaluate(() => {
    const seen = [];
    for (const [id, record] of buses) {
      if (record.latest && record.latest.route_id) seen.push(id);
      if (seen.length === 2) break;
    }
    return seen;
  });

  const order = await page.evaluate(async ([a, b]) => {
    const events = [];
    const first = buses.get(a).marker;
    const second = buses.get(b).marker;
    first.on("popupclose", () => events.push("close:first"));
    second.on("popupopen", () => events.push("open:second"));
    first.openPopup();
    events.length = 0; // ignore the first open; only the transition is under test
    second.openPopup();
    return events;
  }, ids);

  expect(order, "the close must be recorded before the open").toEqual(["close:first", "open:second"]);
});

test("A7f. hiding the Buses layer preserves the route line, and showing it brings it back", async ({ page }) => {
  // THE REGRESSION THE popupopen MOVE INTRODUCED, found by review. Unchecking Buses
  // removes every bus marker, and Leaflet closes any popup on a removed layer, so the
  // close handler read a layer toggle as a rider dismissing the popup and destroyed the
  // line. Re-checking could not bring it back: the geometry was gone and only a fresh
  // fetch would restore it. On the pre-A3 tree the line survived both ways.
  await installMocks(page);
  await open(page);

  const id = await firstBusId(page);
  await page.evaluate((busId) => {
    const el = buses.get(busId).marker.getElement();
    el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  }, id);
  await expect.poll(async () => drawnLines(page), { timeout: 5_000 }).toBeGreaterThan(0);
  const drawn = await drawnLines(page);

  // Hide the layer the way a rider does.
  await page.locator("#toggle-buses").uncheck();
  expect(await drawnLines(page), "hiding the layer must not DESTROY the line").toBe(drawn);
  // The banner is honest about the line being off screen while the layer is hidden.
  await expect(page.locator("#route-banner")).toBeHidden();

  // And show it again.
  await page.locator("#toggle-buses").check();
  expect(await drawnLines(page), "showing the layer brings the same line back").toBe(drawn);
  await expect(page.locator("#route-banner")).toBeVisible();

  // No refetch was needed: the geometry was preserved, not re-requested.
  const shown = await page.evaluate(() => (shownBusRoute ? shownBusRoute.routeId : null));
  expect(shown, "the route is still the one the rider chose").not.toBeNull();
});

test("A7g. a reassignment landing mid-fetch does not draw the route the bus just left", async ({ page }) => {
  // THE OTHER HALF OF THE REASSIGNMENT GUARD. applyBuses already cleared the line when a
  // poll moved the DRAWN bus to a different route, but between the popup opening and the
  // geometry landing there is a fetch in flight and nothing drawn yet. A reassignment
  // arriving in that window passed the old check untouched, the fetch completed, and the
  // rider got the previous route's line under a banner naming it, for a bus that was no
  // longer on it. Measured before the fix: {lines: 1, label: "Bus route M15"}.
  //
  // The delay is what makes the window exist at all. Without it the fetch resolves before
  // any poll could land and this spec would pass on a race it never entered.
  const ctx = await installMocks(page);
  ctx.overrides.busRoute = async (route, fixtures) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop());
    return json(route, { ...fixtures.busRoute(), route: id });
  };
  await open(page);

  const id = await firstBusId(page);
  const wasOn = await page.evaluate((busId) => buses.get(busId).latest.route_id, id);
  // Armed BEFORE the popup opens, because the response is what the assertions below
  // have to outlast and a listener registered afterwards can miss it.
  const geometryArrived = page.waitForResponse((res) => res.url().includes("/api/bus-route/"));
  await page.evaluate((busId) => buses.get(busId).marker.openPopup(), id);
  await expectPopupState(page, { registry: "buses", key: id }, true);

  // The window is real: claimed by this bus, with nothing drawn yet. Asserted rather
  // than assumed, because if the fetch had already landed the rest of this spec would be
  // testing the drawn case that was never broken.
  const inFlight = await page.evaluate(() => ({ pending: pendingBusId, shown: shownBusRoute }));
  expect(inFlight, `the route fetch for ${wasOn} must still be in flight`).toEqual({
    pending: id,
    shown: null,
  });

  // The poll lands, moving this bus to another route.
  await page.evaluate((busId) => {
    applyBuses([{ ...buses.get(busId).latest, route_id: "REASSIGNED" }]);
  }, id);

  // And the superseded geometry never reaches the map.
  //
  // WAITED FOR AND THEN SAMPLED, NOT POLLED, and the first draft of this spec got it
  // wrong in a way worth recording: expect.poll(...).toBe(0) passed on its very first
  // read, 600ms before the response existed, so it stayed green with the fix reverted.
  // The wait is what makes the response part of the test. The sampling is because there
  // is no timer between the body arriving and the draw that would follow it, only a
  // microtask chain, and each read below is a round trip that gives the page room to run
  // it. Under the reverted guard the line appears within the first few samples.
  await geometryArrived;
  for (let i = 0; i < 10; i++) {
    expect(await drawnLines(page), `the superseded geometry must never reach the map (sample ${i})`).toBe(0);
  }
  expect(await page.evaluate(() => shownBusRoute), "nothing may claim the line").toBe(null);
  await expect(page.locator("#route-banner"), "and no banner names the route it left").toBeHidden();
});

test("A7h. hiding the Buses layer mid-fetch discards the route rather than banner-ing it", async ({ page }) => {
  // ROUND 2 CAUGHT THIS IN A7f's OWN FIX. Ownership is two states, not one: drawn, and a
  // fetch in flight with nothing drawn yet. A7f's guard preserved both, and preserving
  // the pending one also skipped the sequence bump that supersedes the fetch, so hiding
  // the layer mid-fetch let the response run to completion against a layer no longer on
  // the map. Measured on that tree: {lines: 1, bannerHidden: false, label: "Bus route
  // M15", busesChecked: false}, a banner naming a route for a popup the rider cannot see.
  //
  // A7f (the drawn case) and this spec (the pending case) are the two halves and must
  // both hold: the first says the geometry SURVIVES a layer toggle, this one says a fetch
  // that was still in the air does NOT come back to life behind it.
  const ctx = await installMocks(page);
  ctx.overrides.busRoute = async (route, fixtures) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop());
    return json(route, { ...fixtures.busRoute(), route: id });
  };
  await open(page);

  const id = await firstBusId(page);
  const geometryArrived = page.waitForResponse((res) => res.url().includes("/api/bus-route/"));
  await page.evaluate((busId) => buses.get(busId).marker.openPopup(), id);
  await expectPopupState(page, { registry: "buses", key: id }, true);

  // The window is real: claimed, with nothing drawn. Without this the spec could run
  // entirely after the fetch and would be testing A7f again rather than its blind spot.
  expect(
    await page.evaluate(() => ({ pending: pendingBusId, shown: shownBusRoute })),
    "the route fetch must still be in flight",
  ).toEqual({ pending: id, shown: null });

  // The rider hides the layer while the fetch is in the air. This is the real control,
  // not a direct call: the defect ran through Leaflet's own `remove: this.closePopup`.
  await page.locator("#toggle-buses").uncheck();

  await geometryArrived;
  for (let i = 0; i < 10; i++) {
    const state = await page.evaluate(() => ({
      lines: (() => { let n = 0; busRouteLayer.eachLayer(() => { n += 1; }); return n; })(),
      shown: shownBusRoute,
      bannerHidden: document.getElementById("route-banner").hidden,
    }));
    expect(state, `a superseded fetch must not draw or announce (sample ${i})`).toEqual({
      lines: 0,
      shown: null,
      bannerHidden: true,
    });
  }

  // And showing the layer again brings back a map with no phantom route on it.
  await page.locator("#toggle-buses").check();
  expect(await drawnLines(page), "nothing reappears when the layer comes back").toBe(0);
  await expect(page.locator("#route-banner")).toBeHidden();
});
