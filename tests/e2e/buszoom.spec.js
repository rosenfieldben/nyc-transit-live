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
const { pressView, placeView } = require("./views");

/* The frozen-clock boot the rest of the suite uses, so a poll happens only when a spec runs the
   clock to one. That is what lets D7d say a zoom drew the buses WITHOUT a poll.

   INSTALLED ONE SECOND EARLY, AND THAT IS A RACE FIXED RATHER THAN A STYLE. The suite's boots
   install the clock AT the frozen time and then pause at that same time, and an installed clock
   runs: if a millisecond passes between the two calls, pauseAt is asked to go backwards and throws
   "Cannot fast-forward to the past" before the page has even loaded. Measured on this machine at
   load averages of 28 to 56, it failed D7b and D7d in one run of this file and six specs across
   stations.spec.js and subway.spec.js in one full run. Installing earlier makes the pause always
   a step forward, which is the order Playwright's own clock examples use; the page loads after
   the pause either way, so it sees exactly the frozen time. The other boots are recorded in the
   ledger's flake list rather than edited here. */
async function boot(page) {
  const ctx = await installMocks(page);
  await page.clock.install({ time: new Date(fx.FROZEN_MS - 1000) });
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
  // band, so neither "hides" nor "draws" can pass by never having been anything else. The map
  // LANDS drawn since the operator's ruling (it opens at the City preset); before it, this row
  // read {zoom: 12, drawn: 0}, which the ledger keeps as the before.
  for (const view of ["view-region", "view-rail", "view-city"]) {
    await pressView(page, view);
    await read(view);
  }
  await pressView(page, "view-rail");
  await read("view-rail again");

  expect(seen).toEqual({
    open: { zoom: 13, drawn: 2, inDocument: 2, registry: 2, strip: "2" },
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
  /* AND NOT FADED. Playwright's visibility ignores opacity, so a rule widened as a fade rather than a
     hide passed the line above; the review of this follow-up measured it with every other vehicle at
     opacity 0 below 13. The COMPUTED opacity is read, because a stylesheet fade never touches the
     inline one the freshness contract writes, and the floor is the contract's own lowest dimming
     (a docked boat on a stale feed, 0.2475, which P4b2 holds), not zero. */
  const faded = await page.evaluate(() =>
    [...document.querySelectorAll(".leaflet-marker-icon:not(.bus-marker):not(.rail-stn-marker)")]
      .map((el) => Number(getComputedStyle(el).opacity))
      .filter((opacity) => !(opacity >= 0.2)),
  );
  expect(faded, "every other vehicle is drawn at an opacity a rider can see").toEqual([]);
});

test("D7b. an undrawn bus is out of the accessibility tree and the click path, and comes back with the band", async ({
  page,
}) => {
  await boot(page);
  // The landing is the City preset (the operator's ruling), so the buses start drawn; before the
  // ruling this line asserted HIDDEN at zoom 12.
  expectEveryBus(await busReach(page), DRAWN, "landing, zoom 13");
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
     class comes back with the icon, and aria-hidden and the inline pointer-events are written
     twice over, by the add hook and by the band repaint applyFeedVisibility runs after it. So this
     half cannot tell the two apart (mutation M6 removes the hook and this half passes); it is here
     because a rebuilt element is a place the reach could be lost, and it holds the result. The
     probe proves the element really is new; without it this could pass on an element that was
     never rebuilt. */
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
     BUS THAT ARRIVES WHILE THE MAP IS AT RAIL, with no zoomend after it, which only the add hook
     reaches: this is the half that fails when the hook is removed (mutation M6). */
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

  /* THE KEY, AS A RIDER READS IT: the open panel's RENDERED text (innerText), whitespace collapsed.
     textContent was the first draft, and the review of this follow-up showed it was a markup read:
     wrapping the clause in <span hidden> left it in textContent, took it away from every rider and
     every screen reader, and passed. innerText drops what is not rendered, and the panel is asserted
     open first because innerText of a hidden panel is its textContent again. */
  await page.locator("#legend-toggle").click();
  await expect(page.locator("#legend")).toBeVisible();
  const rows = await page
    .locator("#legend .legend-row")
    .evaluateAll((els) => els.map((el) => el.innerText.replace(/\s+/g, " ").trim()));
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

  /* AND AT CITY, WHICH IS THE HALF THAT CAN TELL THEM APART. At Rail the band already has every bus
     hidden, so a focus that hid buses too would pass the two lines above; the review of this
     follow-up measured exactly that, with a focus that wrote aria-hidden and pointer-events on every
     bus element, drawn on screen and unreachable, and the suite green. At City a focus pressed must
     leave every bus drawn and reachable, and so must clearing it. */
  await pressView(page, "view-city");
  await bullet.click();
  await expect(bullet).toHaveAttribute("aria-pressed", "true");
  for (const { id, hit, ...state } of await busReach(page)) expect(state, `City, a route focused: ${id}`).toEqual(DRAWN);
  await bullet.click();
  await expect(bullet).toHaveAttribute("aria-pressed", "false");
  for (const { id, hit, ...state } of await busReach(page)) expect(state, `City, the focus cleared: ${id}`).toEqual(DRAWN);
});

/* THE FAILURE POLICY, WHICH THREE COMMENTS STATED AND NOTHING TESTED until the mutation table
   was being written and a row for it had nothing to die on. The stylesheet hides a bus only when
   the root SAYS "hidden", and buses.js takes a bus out of reach only on the same word, so a root
   whose script never wrote the band draws every bus and leaves every one reachable, as the map
   did before this rule: too many buses is noise, and none at all while the strip counts them is
   a map that is wrong. Asked at Rail, where the band would otherwise hide them, and through both
   readers: the stylesheet by the drawn page, buses.js by calling its own sweep. */
test("D7g. a root with no band on it draws every bus and leaves every one reachable", async ({ page }) => {
  await boot(page);
  await pressView(page, "view-rail");
  expect(await drawnBuses(page)).toBe(0);
  await page.evaluate(() => {
    document.documentElement.removeAttribute("data-bus-band");
    paintBusBand();
  });
  expect(await drawnBuses(page)).toBe(2);
  for (const { id, hit, ...state } of await busReach(page)) expect(state, `no band: ${id}`).toEqual(DRAWN);
});

/* A FLY CUT SHORT, which the review of this rule found and measured: a drag during a preset's fly
   stops it through Leaflet's _stop(), which fires no zoomend, so the map rests at a fractional zoom
   while the root keeps the zoom the fly left from. From City to Rail that left every bus drawn at
   11.8; from Rail to City, every bus hidden at 12.6. The drag that interrupted it ends in a moveend,
   and that is what repaints the band now (shared.js).

   SIMULATED THE WAY THE DRAG DOES IT: map.stop() is the public face of the _stop() a drag calls, and
   an unanimated panBy is the moveend the drag's end fires. Each direction first asserts its own
   premise, that the map really is resting between two integers on the far side of the band from
   where it started, because a fly stopped before it had moved would make this pass over nothing. */
test("D7h. a fly cut short leaves the band on the zoom the map actually rests at", async ({ page }) => {
  await boot(page);
  /* EACH DIRECTION STARTS FROM placeView, NOT A PRESS, because of something this spec found at the
     base and does not own: after a fly is cut short, the NEXT preset press fires a stray zoomend and
     moveend at zoom 12 before it lands, which clears the pressed state, and the button stays dark
     at the preset it named (measured at d49e9a7 as well as here; the ledger records it for the
     operator). pressView asserts that pressed state, so it cannot be the way into the second cut. */
  const cutShort = async (from, to, ms) => {
    await placeView(page, from);
    await page.locator(`#${to}`).click();
    await page.clock.runFor(ms);
    return page.evaluate(() => {
      map.stop();
      map.panBy([60, 0], { animate: false });
      const zoom = map.getZoom();
      return { zoom, rounded: Math.round(zoom), dataZoom: document.documentElement.getAttribute("data-zoom") };
    });
  };

  // City to Rail: stopped below 12.5, so the band must say hidden where it said drawn.
  const down = await cutShort("view-city", "view-rail", 300);
  expect(Number.isInteger(down.zoom), `the fly was stopped between two zooms (${down.zoom})`).toBe(false);
  expect(down.rounded, "and on the hidden side of the band").toBeLessThan(13);
  expect(down.dataZoom).toBe(String(down.rounded));
  expect(await drawnBuses(page)).toBe(0);
  expectEveryBus(await busReach(page), HIDDEN, `City to Rail, stopped at ${down.zoom}`);

  // Rail to City: stopped at or above 12.5, so the band must say drawn where it said hidden.
  const up = await cutShort("view-rail", "view-city", 650);
  expect(Number.isInteger(up.zoom), `the fly was stopped between two zooms (${up.zoom})`).toBe(false);
  expect(up.rounded, "and on the drawn side of the band").toBeGreaterThanOrEqual(13);
  expect(up.dataZoom).toBe(String(up.rounded));
  expect(await drawnBuses(page)).toBe(2);
  for (const { id, hit, ...state } of await busReach(page)) expect(state, `Rail to City, stopped at ${up.zoom}: ${id}`).toEqual(DRAWN);
});

/* THE LANDING, BY THE OPERATOR'S RULING. The map used to open at a zoom of its own, 12, one below
   the band, so a rider landed on a strip counting every bus and a map drawing none. The ruling moved
   the landing to the City preset (the design's own default) and kept the band at 13, and asked for
   exactly this: the landing view draws the buses, and the City button reads pressed.

   READ AFTER THE FIRST POLL AND THE STATION LOAD, because both repaint things on the root and a
   pressed state that did not survive them would be a pressed state for one frame. The zoom, the
   root's attribute and the drawn page are all asked, so none of them can pass for another. */
test("D7i. the map lands on the City preset: every bus drawn, and the City button pressed", async ({ page }) => {
  await boot(page);
  expect(
    await page.evaluate(() => ({
      zoom: map.getZoom(),
      dataZoom: document.documentElement.getAttribute("data-zoom"),
      busBand: document.documentElement.getAttribute("data-bus-band"),
      atCity: mapIsAt(VIEW_PRESETS.find((preset) => preset.id === "view-city")),
    })),
  ).toEqual({ zoom: 13, dataZoom: "13", busBand: "drawn", atCity: true });
  await expect(page.locator("#view-city")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#view-rail")).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#view-region")).toHaveAttribute("aria-pressed", "false");
  expect(await drawnBuses(page)).toBe(2);
  for (const { id, hit, ...state } of await busReach(page)) expect(state, `landing: ${id}`).toEqual(DRAWN);
});
