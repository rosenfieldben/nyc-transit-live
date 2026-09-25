/* Follow-up 1: bus markers are drawn from City zoom (13) and not below it.
   ==========================================================================

   THE FINDING. At the Rail preset (zoom 11) and at Region (zoom 10) the bus layer was hundreds of
   14px arrows and dots across Queens and Brooklyn, each correct and none readable, and together
   the noisiest thing on the map. The design gave the arrow a size and a hue and said nothing
   about zoom. It is follow-up 1 of the phase close-out, and the ledger's entry for it is under
   the phase's follow-ups in docs/reviews/map-redesign-rounds.md.

   THE RULE IS CSS, and these specs are about what that promises: the markers stay in the
   document at every zoom, the registry and the strip's count do not move, the zoomend that
   crosses 13 draws them without a poll, and below 13 a bus is out of the accessibility tree and
   the click path as well as off the screen. pins.spec.js P6 holds what the rule must NOT change
   at each preset; the axe half is a11y.spec.js A1w's, which scans both sides of the band at three
   widths in both themes.

   EVERY READING HERE IS OFF THE DRAWN PAGE: Playwright's own visibility, the computed style, a
   hit test and the element's attributes. None of them asks the root attribute or the band
   function whether a bus is drawn, because a markup read where the drawn page is what matters is
   the first defect shape this phase named, and a test that asked the model would pass with the
   stylesheet rule deleted. */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");
const { pressView } = require("./views");

// The frozen-clock boot the rest of the suite uses, so a poll happens only when a spec runs the
// clock to one. That is what lets D7d say a zoom drew the buses WITHOUT a poll.
async function boot(page) {
  const ctx = await installMocks(page);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await page.waitForFunction(() => buses.size === 2 && trains.size === 2 && pathTrainRecords.size === 2, null, {
    timeout: 15_000,
  });
  await page.clock.runFor(1000);
  return ctx;
}

// The bus markers Playwright itself would call visible: a box, and no display or visibility
// taking it away.
const drawnBuses = (page) => page.locator(".bus-marker").filter({ visible: true }).count();

/* EACH BUS, AS A RIDER AND A SCREEN READER MEET IT. `hit` is what the page returns at the bus's
   own position: the icon is anchored at its centre, so a drawn bus is what a tap there lands on,
   and an undrawn one must not be. The point is projected from the marker's LatLng rather than
   read off the element, because an element that is not drawn has no box to read. */
const busReach = (page) =>
  page.evaluate(() =>
    [...buses.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([id, record]) => {
        const el = record.marker.getElement();
        if (!el) return { id, element: false };
        const style = getComputedStyle(el);
        const box = el.getBoundingClientRect();
        const origin = map.getContainer().getBoundingClientRect();
        const at = map.latLngToContainerPoint(record.marker.getLatLng());
        const x = origin.left + at.x;
        const y = origin.top + at.y;
        const inView = x >= 0 && y >= 0 && x < innerWidth && y < innerHeight;
        const top = inView ? document.elementFromPoint(x, y) : null;
        return {
          id,
          element: true,
          drawn: style.display !== "none" && style.visibility === "visible" && box.width > 0 && box.height > 0,
          ariaHidden: el.getAttribute("aria-hidden"),
          pointerEvents: style.pointerEvents,
          hit: inView ? Boolean(top && top.closest(".bus-marker") === el) : null,
        };
      }),
  );

const HIDDEN = { element: true, drawn: false, ariaHidden: "true", pointerEvents: "none" };
const DRAWN = { element: true, drawn: true, ariaHidden: null, pointerEvents: "auto" };

function expectEveryBus(reach, want, label) {
  expect(reach.length, `${label}: no bus was read`).toBe(2);
  for (const bus of reach) {
    const { id, hit, ...state } = bus;
    expect(state, `${label}: ${id}`).toEqual(want);
    // A bus in the viewport is hit exactly when it is drawn; one outside it cannot be asked.
    if (hit !== null) expect(hit, `${label}: ${id} under a tap at its own position`).toBe(want.drawn);
  }
}

test("D7a. no bus is drawn at Region or Rail and every bus is at City, while the count never moves", async ({
  page,
}) => {
  await boot(page);
  const seen = {};
  const read = async (label) => {
    seen[label] = {
      zoom: await page.evaluate(() => map.getZoom()),
      drawn: await drawnBuses(page),
      inDocument: await page.locator(".bus-marker").count(),
      registry: await page.evaluate(() => buses.size),
      strip: (await page.locator("#toggle-buses .feed-count").textContent()) ?? "",
    };
  };
  await read("open");
  // Region and Rail first, then City from a hidden state, then back: both directions of the
  // band, so neither "hides" nor "draws" can pass by never having been anything else.
  for (const view of ["view-region", "view-rail", "view-city"]) {
    await pressView(page, view);
    await read(view);
  }
  await pressView(page, "view-rail");
  await read("view-rail again");

  expect(seen).toEqual({
    open: { zoom: 12, drawn: 0, inDocument: 2, registry: 2, strip: "2" },
    "view-region": { zoom: 10, drawn: 0, inDocument: 2, registry: 2, strip: "2" },
    "view-rail": { zoom: 11, drawn: 0, inDocument: 2, registry: 2, strip: "2" },
    "view-city": { zoom: 13, drawn: 2, inDocument: 2, registry: 2, strip: "2" },
    "view-rail again": { zoom: 11, drawn: 0, inDocument: 2, registry: 2, strip: "2" },
  });

  // THE RULE IS THE BUSES' ALONE: at Rail every other vehicle is still drawn. pins.spec.js P6b
  // holds every non-bus marker at every view; this is the one line that says so beside the rule.
  const others = await page.locator(".leaflet-marker-icon:not(.bus-marker):not(.rail-stn-marker)").count();
  expect(others).toBeGreaterThan(0);
  await expect(page.locator(".leaflet-marker-icon:not(.bus-marker):not(.rail-stn-marker)").filter({ visible: true }))
    .toHaveCount(others);
});

test("D7b. an undrawn bus is out of the accessibility tree and the click path, and comes back with the band", async ({
  page,
}) => {
  await boot(page);
  expectEveryBus(await busReach(page), HIDDEN, "open, zoom 12");
  await pressView(page, "view-region");
  expectEveryBus(await busReach(page), HIDDEN, "Region");
  await pressView(page, "view-city");
  const city = await busReach(page);
  expectEveryBus(city, DRAWN, "City");
  // At least one bus must be in the viewport at City, or the hit half of this spec asked nothing.
  expect(city.some((bus) => bus.hit === true), "no bus was under a tap at City").toBe(true);
  await pressView(page, "view-rail");
  expectEveryBus(await busReach(page), HIDDEN, "Rail, after City");
  // THE ROLE IS WHAT A SCREEN READER LISTS, so it is asked too: no bus image at Rail, both at City.
  await expect(page.getByRole("img", { name: /bus, heading/ })).toHaveCount(0);
  await pressView(page, "view-city");
  await expect(page.getByRole("img", { name: /bus, heading/ })).toHaveCount(2);
});

test("D7c. the band holds through everything that builds a bus's element again", async ({ page }) => {
  const ctx = await boot(page);
  await pressView(page, "view-rail");

  /* 1. THE FEED HIDDEN AND SHOWN, which destroys every bus element and builds a new one: the
     class comes back with the icon, and aria-hidden and the inline pointer-events come back only
     because the add hook writes them. The probe proves the element really is new; without it this
     could pass on an element that was never rebuilt. */
  await page.evaluate(() => {
    for (const record of buses.values()) record.marker.getElement().dataset.probe = "before";
  });
  await page.locator("#toggle-buses").click();
  await page.locator("#toggle-buses").click();
  await expect(page.locator("#toggle-buses")).toHaveAttribute("aria-pressed", "true");
  expect(await page.locator(".bus-marker[data-probe]").count(), "the feed toggle did not rebuild the elements").toBe(0);
  expectEveryBus(await busReach(page), HIDDEN, "Rail, after the feed was hidden and shown");

  /* 2. A POLL THAT RE-ICONS A BUS, which is setIcon. Leaflet reuses the element it is handed today,
     so what was written on it survives; this asserts that directly, so the day an upgrade builds a
     new element here and the reach is lost, this fails rather than a rider finding it. And 3. A
     BUS THAT ARRIVES WHILE THE MAP IS AT RAIL, which is born through the add hook. */
  await page.evaluate(() => {
    for (const record of buses.values()) record.marker.getElement().dataset.probe = "before";
  });
  ctx.overrides.buses = (route, fixtures) => {
    const body = fixtures.buses();
    body.data[0] = { ...body.data[0], route_id: "M14A" };
    body.data.push({
      id: "MTA NYCT_103", route_id: "Q58", latitude: 40.73, longitude: -73.87, bearing: 180.0,
      observed_at: fx.FROZEN_S + 10, provenance: "reported",
    });
    return json(route, body);
  };
  await page.clock.runFor(15_000);
  await page.waitForFunction(() => buses.size === 3 && buses.get("MTA NYCT_101").latest.route_id === "M14A");
  const probes = await page.evaluate(() =>
    Object.fromEntries([...buses.entries()].map(([id, r]) => [id, r.marker.getElement().dataset.probe ?? null])),
  );
  expect(probes, "setIcon kept the re-iconed bus's element, and the new bus has a fresh one").toEqual({
    "MTA NYCT_101": "before",
    "MTA NYCT_102": "before",
    "MTA NYCT_103": null,
  });
  const three = await busReach(page);
  expect(three.map((bus) => bus.id)).toEqual(["MTA NYCT_101", "MTA NYCT_102", "MTA NYCT_103"]);
  for (const { id, hit, ...state } of three) expect(state, `Rail, after the poll: ${id}`).toEqual(HIDDEN);

  // And all three come back together at City.
  await pressView(page, "view-city");
  for (const { id, hit, ...state } of await busReach(page)) expect(state, `City: ${id}`).toEqual(DRAWN);
});

test("D7d. zooming in draws the buses on the zoomend, with no poll and no new element", async ({ page }) => {
  const ctx = await boot(page);
  await pressView(page, "view-rail");
  expect(await drawnBuses(page)).toBe(0);
  await page.evaluate(() => {
    for (const record of buses.values()) record.marker.getElement().dataset.probe = "rail";
  });
  const polls = ctx.counts.buses;
  await pressView(page, "view-city");
  expect(await drawnBuses(page)).toBe(2);
  // THE SAME ELEMENTS, NOW DRAWN, and the same number of bus fetches: nothing was re-rendered and
  // nothing was asked of the backend.
  await expect(page.locator(".bus-marker[data-probe='rail']")).toHaveCount(2);
  expect(ctx.counts.buses, "a poll ran between Rail and City").toBe(polls);
});

test("D7e. the Buses button's tooltip and the Key's bus row both say the buses are shown from City zoom", async ({
  page,
}) => {
  await boot(page);
  const words = await page.evaluate(() => BUS_ZOOM_WORDS);
  expect(words).toBe("shown from City zoom");
  const button = page.locator("#toggle-buses");
  // At every view, so it cannot be a sentence that appears only where it is least needed.
  for (const view of ["view-region", "view-rail", "view-city"]) {
    await pressView(page, view);
    await expect(button).toHaveAttribute("title", /^Live · \d+s · shown from City zoom · hide Buses$/);
  }
  // And while the feed is hidden, where the action flips and the note stays.
  await button.click();
  await expect(button).toHaveAttribute("title", /^Live · \d+s · shown from City zoom · show Buses$/);
  await button.click();
  // No other feed says it.
  const others = await page
    .locator("#feed-buttons button.feed:not(#toggle-buses)")
    .evaluateAll((els, w) => els.filter((el) => el.title.includes(w)).map((el) => el.id), words);
  expect(others).toEqual([]);

  // THE KEY, AS A RIDER READS IT: the row's own text, whitespace collapsed, and the phrase inside
  // it is the constant the tooltip is built from.
  await page.locator("#legend-toggle").click();
  const rows = await page
    .locator("#legend .legend-row")
    .evaluateAll((els) => els.map((el) => el.textContent.replace(/\s+/g, " ").trim()));
  const busRows = rows.filter((row) => row.startsWith("Bus"));
  expect(busRows).toEqual(["Bus (arrow points where it's heading); shown from City zoom", "Bus, heading unknown"]);
  expect(busRows[0]).toContain(words);
});

test("D7f. what the rule does not govern: the clicked bus's route line, its popup, and route focus", async ({ page }) => {
  await boot(page);
  await pressView(page, "view-city");
  // THE RIDER'S PATH NOW THAT THE BUS IS DRAWN: a click on the marker itself.
  await page.locator(".bus-marker").first().click();
  await expect(page.locator("#route-banner")).toBeVisible();
  await expect.poll(() => page.evaluate(() => busRouteLayer.getLayers().length)).toBe(1);
  const opened = await page.evaluate(() => [...buses.values()].find((r) => r.marker.isPopupOpen())?.latest.id ?? null);
  expect(opened).not.toBeNull();

  /* A LINE IS NOT A MARKER. Zooming out to Rail hides the bus, and the line it drew, the banner
     naming it and the popup the rider opened all stay: closing the popup would clear the line
     (popupclose is what releases it), so leaving it open is what "the line is unaffected" costs,
     and it is the right cost: the rider asked for that route and did not ask for it back. */
  await pressView(page, "view-rail");
  expect(await drawnBuses(page)).toBe(0);
  expect(
    await page.evaluate((id) => ({
      lines: busRouteLayer.getLayers().length,
      onMap: map.hasLayer(busRouteLayer),
      popupOpen: buses.get(id).marker.isPopupOpen(),
    }), opened),
  ).toEqual({ lines: 1, onMap: true, popupOpen: true });
  await expect(page.locator("#route-banner")).toBeVisible();
  await expect(page.locator("#route-banner-label")).toHaveText(/^Bus route \S+$/);
  /* AND THE POPUP'S OWN COPY OF THE MARK IS NOT A MARKER EITHER. The title draws the bus's arrow
     beside its route (an svg.bus-mark, one letter from the marker's class), so a rule written
     against the wrong one of the two would hide the popup's glyph at exactly the zoom the rider
     is reading it. */
  await expect(page.locator(".leaflet-popup .pmark svg.bus-mark")).toBeVisible();

  /* ROUTE FOCUS IS THE SUBWAY'S, and the two rules write aria-hidden on different elements. So a
     focus pressed and cleared while the buses are undrawn must leave them exactly as the zoom has
     them, and draw nothing. */
  const bullet = page.locator("#subway-key button:not([aria-disabled='true'])").first();
  await bullet.click();
  await expect(bullet).toHaveAttribute("aria-pressed", "true");
  expectEveryBus(await busReach(page), HIDDEN, "Rail, a route focused");
  await bullet.click();
  await expect(bullet).toHaveAttribute("aria-pressed", "false");
  expectEveryBus(await busReach(page), HIDDEN, "Rail, the focus cleared");
});
