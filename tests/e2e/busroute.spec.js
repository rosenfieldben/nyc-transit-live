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
const { installMocks } = require("./mock");
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
