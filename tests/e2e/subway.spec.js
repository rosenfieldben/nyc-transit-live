/* MR2: the subway restyle's own claims.
   ==========================================================================

   What stage 2 of the map redesign promises that no other spec in this suite was written to
   check. The pins next door (pins.spec.js) say what MR2 must NOT change; this says what it
   must DO. Ids are D-for-design, in the two-letter-plus-number grammar tests/specids.js can
   collect, and D2 because D1 is MR1's chrome.

   WHAT IS DELIBERATELY NOT HERE, because it is already somewhere better:
   - the pure arithmetic (draw order, focus opacities, the station predicate, the zoom band):
     frontend/subway.test.js, at the node tier, where it can be asked directly
   - that a station's label is legible over any tile, in both themes: a11y.spec.js A1z3
   - that no other system's marks moved: pins.spec.js P1g through P1n
   - that the subway POPUP did not move: pins.spec.js P1f, which is still an assertion
   - that the chrome still meets the target floor with the bullets in it: layout.spec.js A4b
   - the dimming contract itself: smoke.spec.js C2c, C2h and the C6e series in the contract
     tier. D2d below is the one composition claim MR2 adds on top of them. */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const DESKTOP = { width: 1280, height: 720 };

/* THE FIXTURE WORLD HAS NO YELLOW ROUTE AND NO ONE-ROUTE STATION, which two of these specs
   need and which is exactly the shape of a vacuous test: an assertion about local stations
   in a world where every station is a transfer passes without looking at anything. So they
   are added per spec through the override seam (the same one P2c uses for F03's Prospect Av)
   rather than into the shared fixture, where they would move every other spec's world. */
const withYellow = (ctx) => {
  ctx.overrides.subwayRoutes = (route, fixtures) =>
    json(route, [
      ...fixtures.subwayRoutes(),
      // N and Q are two of the four the yellow trunk is, and they are listed FIRST so the
      // payload order is the opposite of the draw order: a spec over a payload that already
      // happened to end with yellow would pass with the sort deleted.
      { route: "N", polylines: [[[40.7, -74.0], [40.71, -73.99]]] },
      { route: "Q", polylines: [[[40.72, -73.98], [40.73, -73.97]]] },
    ]);
};

const withLocalStation = (ctx) => {
  ctx.overrides.subwayStops = (route, fixtures) =>
    json(route, [
      ...fixtures.subwayStops(),
      // One route, so a dot; and a second with no routes field at all, which is the case the
      // node test calls the direction that matters.
      { id: "L01", name: "Lorimer St", lat: 40.7141, lon: -73.9503, routes: ["L"] },
      { id: "X01", name: "Nowhere", lat: 40.7, lon: -73.95 },
    ]);
};

async function open(page, before, { stations = 14, viewport = DESKTOP } = {}) {
  await page.setViewportSize(viewport);
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await page.waitForFunction(
    (want) => trains.size === 2 && stationRegistry.length === want && routeLinesLayer.getLayers().length > 0,
    stations,
    { timeout: 15_000 },
  );
  await page.clock.runFor(1000);
  return ctx;
}

const ribbons = (page) =>
  page.evaluate(() => subwayRibbons.map((r) => ({ route: r.route, part: r.part, opacity: r.layer.options.opacity })));

const trainOpacities = (page) =>
  page.evaluate(() => Object.fromEntries([...trains.entries()].map(([id, r]) => [id, r.marker.options.opacity ?? 1])));

/* ---------------- the bullets, and what pressing one does ---------------- */

test("D2a. the subway key is twenty-three named buttons, and each one is its own route", async ({ page }) => {
  await open(page);
  const key = await page.evaluate(() =>
    [...document.querySelectorAll("#subway-key .bul")].map((b) => ({
      tag: b.tagName,
      text: b.textContent,
      name: b.getAttribute("aria-label"),
      pressed: b.getAttribute("aria-pressed"),
    })),
  );
  expect(key.length, "the key draws one bullet per route in SUBWAY_KEY_TRUNKS").toBe(23);
  expect([...new Set(key.map((b) => b.tag))], "every bullet is a button now, not a span").toEqual(["BUTTON"]);
  // THE NAME IS ON aria-label AND THE TEXT IS THE BARE ROUTE ID, which is not a style choice:
  // D1l resolves each bullet's expected colour as lineColor(textContent), so a visually hidden
  // label inside the chip would make that check compare the wrong thing.
  for (const bullet of key) {
    expect(bullet.name, `bullet ${bullet.text}`).toBe(`Focus route ${bullet.text}`);
    expect(bullet.pressed, `bullet ${bullet.text} starts unpressed`).toBe("false");
  }
  // And the group is back in the accessibility tree, which MR1 took it out of while it was
  // only a colour swatch and promised to restore "the moment the key does something".
  await expect(page.locator("#subway-key")).not.toHaveAttribute("aria-hidden", "true");
  await expect(page.locator("#subway-key")).toHaveAttribute("aria-label", "Focus a subway route");
});

test("D2b. focusing a route dims every other ribbon and every other train, and pressing again clears", async ({
  page,
}) => {
  await open(page);
  const before = await ribbons(page);
  expect(before.length, "two shapes, drawn twice each").toBe(4);
  expect(before.every((r) => r.opacity === (r.part === "casing" ? 0.9 : 1)), "nothing dim to start").toBe(true);

  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();

  const focused = await ribbons(page);
  const byKey = Object.fromEntries(focused.map((r) => [`${r.route}|${r.part}`, r.opacity]));
  // THE FOCUSED ROUTE KEEPS ITS RESTING OPACITIES, which for a casing is 0.9 and not 1: the
  // casing is paper at 0.9 whether or not anything is focused, and a spec asserting 1 here
  // would fail correct code.
  expect(byKey["1|casing"]).toBe(0.9);
  expect(byKey["1|line"]).toBe(1);
  // Everything else: the line survives at 0.18 and the casing goes away entirely.
  expect(byKey["A|casing"]).toBe(0);
  expect(byKey["A|line"]).toBe(0.18);

  const opacities = await trainOpacities(page);
  expect(opacities["sub-1"], "the train on the focused route is untouched").toBe(1);
  expect(opacities["sub-2"], "a train off it drops to the focus floor").toBeCloseTo(0.15, 5);

  await expect(page.locator('#subway-key .bul[aria-label="Focus route 1"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('#subway-key .bul[aria-label="Focus route A"]')).toHaveAttribute("aria-pressed", "false");
  // ANNOUNCED ONCE, through the region MR1 already writes one sentence per render into.
  await expect(page.locator("#page-announce")).toHaveText("Focused on the 1; press again to clear.");

  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  expect(await ribbons(page)).toEqual(before);
  expect(await trainOpacities(page)).toEqual({ "sub-1": 1, "sub-2": 1 });
  await expect(page.locator('#subway-key .bul[aria-label="Focus route 1"]')).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#page-announce")).toHaveText("Route focus cleared.");
});

test("D2c. pressing a different bullet moves the focus rather than clearing it", async ({ page }) => {
  await open(page);
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  await page.locator('#subway-key .bul[aria-label="Focus route A"]').click();
  const byKey = Object.fromEntries((await ribbons(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  expect(byKey["A|line"]).toBe(1);
  expect(byKey["1|line"]).toBe(0.18);
  await expect(page.locator('#subway-key .bul[aria-label="Focus route A"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('#subway-key .bul[aria-label="Focus route 1"]')).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator("#page-announce")).toHaveText("Focused on the A; press again to clear.");
});

test("D2d. focus is opacity and nothing else: no layer is added, removed or rebuilt", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "focus re-rendering layers instead of changing opacity". A
     rebuild would look identical in a screenshot and pass every opacity assertion above, so
     the claim is made about IDENTITY: Leaflet stamps every layer with a _leaflet_id, and the
     same object keeps it across a focus and a clear. It is also a correctness claim and not
     only a cost one: setIcon replaces a marker's element, so focus state written onto the DOM
     would be lost the next time a train's route id changed. */
  await open(page);
  const stamp = () =>
    page.evaluate(() => ({
      ribbons: subwayRibbons.map((r) => L.Util.stamp(r.layer)),
      inGroup: routeLinesLayer.getLayers().map((l) => L.Util.stamp(l)),
      trains: [...trains.entries()].map(([id, r]) => `${id}:${L.Util.stamp(r.marker)}`),
      icons: [...trains.values()].map((r) => String(r.marker.getIcon().options.html).length),
    }));
  const before = await stamp();
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  expect(await stamp(), "focusing must not build anything").toEqual(before);
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  expect(await stamp(), "clearing must not build anything either").toEqual(before);
});

test("D2e. a focused route survives the next poll, and the freshness contract still composes", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "the contract's dimming lost under the new icon", and it also
     catches the quieter half nobody presses a button to find: the poll that re-dims every
     marker fifteen seconds later must re-derive the product rather than erase the focus.

     THE WORLD IS C2h's: every group's poll succeeds and ACE has been serving ten minute old
     content, so sub-2 is dimmed by its own observation while sub-1 is current. */
  const ctx = await open(page);
  let pollAt = fx.FROZEN_S;
  ctx.overrides.subways = (route) => {
    pollAt += 15;
    return json(route, fx.subwaysWithSystems({ fetchedAt: pollAt, aceContentAt: pollAt - 600 }));
  };
  await page.clock.runFor(15_000);
  await expect.poll(() => trainOpacities(page)).toEqual({ "sub-1": 1, "sub-2": 0.45 });

  // Focus the route the STALE train is NOT on: it is now dimmed twice.
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  expect(await trainOpacities(page)).toEqual({ "sub-1": 1, "sub-2": 0.45 * 0.15 });

  // A poll lands. Neither dimming is lost and neither is applied twice.
  await page.clock.runFor(15_000);
  await expect.poll(() => trainOpacities(page)).toEqual({ "sub-1": 1, "sub-2": 0.45 * 0.15 });

  // And focusing the stale train's OWN route does not restore it: being looked at is not
  // being current. This is the composition claim in the direction that is easy to get wrong.
  await page.locator('#subway-key .bul[aria-label="Focus route A"]').click();
  expect(await trainOpacities(page)).toEqual({ "sub-1": 0.15, "sub-2": 0.45 });
});

test("D2f. Escape clears the focus, and only once there is nothing else to close", async ({ page }) => {
  /* THE WHOLE LADDER, WALKED, because the claim is not "Escape clears focus" but "Escape
     clears focus LAST". map.js owns the page's only keydown handler and its rule is that a
     rider's own surface closes first; route focus is a map-wide state that nobody stands in,
     so it is the bottom rung. A spec that pressed Escape once in an empty page would pass
     with the rung wired anywhere in the chain, including above the panel, which would mean a
     rider pressing Escape to close the station panel silently lost their route focus instead.

     At 1280 the station panel is docked open, so this page genuinely has all three. */
  await open(page);
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  await page.evaluate(() => trains.get("sub-1").marker.openPopup());
  const state = () =>
    page.evaluate(() => ({
      // ASKED OF THE MAP, NOT OF THE DOM. Leaflet removes a closed popup's element on a fade
      // timer and this suite's clock is paused, so the element outlives the close by however
      // long the test never advances. This is also the question the ladder itself asks.
      popups: openPopupsOnMap().length,
      panel: stationsPanelOpen(),
      focused: document.querySelector('#subway-key .bul[aria-label="Focus route 1"]').getAttribute("aria-pressed"),
    }));
  expect(await state()).toEqual({ popups: 1, panel: true, focused: "true" });

  await page.keyboard.press("Escape");
  expect(await state(), "the popup goes first").toEqual({ popups: 0, panel: true, focused: "true" });

  await page.keyboard.press("Escape");
  expect(await state(), "then the panel, and the focus is still not touched").toEqual({
    popups: 0,
    panel: false,
    focused: "true",
  });

  await page.keyboard.press("Escape");
  expect(await state(), "and only now the route focus").toEqual({ popups: 0, panel: false, focused: "false" });
  expect(await trainOpacities(page)).toEqual({ "sub-1": 1, "sub-2": 1 });
  await expect(page.locator("#page-announce")).toHaveText("Route focus cleared.");

  // A fourth press has nothing to do and must leave the event alone rather than claim it.
  await page.keyboard.press("Escape");
  expect(await state()).toEqual({ popups: 0, panel: false, focused: "false" });
});

/* ---------------- the ribbons ---------------- */

test("D2g. the yellow trunk is drawn last, on the map, in both passes", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "yellow not drawn last". The payload lists N and Q FIRST, so a
     spec over a payload that happened to end with yellow would pass with the sort deleted.
     Asserted on the layer group's own order, which is canvas paint order. */
  await open(page, withYellow);
  const order = await page.evaluate(() => subwayRibbons.map((r) => `${r.route}|${r.part}`));
  const casings = order.filter((k) => k.endsWith("|casing"));
  const lines = order.filter((k) => k.endsWith("|line"));
  // Two passes, each one route-ordered, and every casing before every line.
  expect(order.slice(0, casings.length)).toEqual(casings);
  expect(casings).toEqual(["1|casing", "A|casing", "N|casing", "Q|casing"]);
  expect(lines).toEqual(["1|line", "A|line", "N|line", "Q|line"]);
  // And the layer group agrees, which is what the canvas actually paints from.
  const drawn = await page.evaluate(() =>
    routeLinesLayer.getLayers().map((l) => `${l.options.color}|${l.options.weight}`),
  );
  expect(drawn.length).toBe(8);
  expect(drawn.slice(0, 4).every((d) => d.endsWith("|6.5")), "every casing first").toBe(true);
  expect(drawn.slice(4).every((d) => d.endsWith("|4")), "then every line").toBe(true);
  const yellow = await page.evaluate(() => lineColor("N"));
  expect(drawn[6], "the yellow trunk's lines are the last two drawn").toBe(`${yellow}|4`);
  expect(drawn[7]).toBe(`${yellow}|4`);
});

test("D2h. a ribbon is a casing in paper under a line in the app's own colour", async ({ page }) => {
  await open(page);
  const measured = await page.evaluate(() => {
    const paper = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim();
    return {
      paper,
      layers: routeLinesLayer.getLayers().map((l) => ({
        color: l.options.color,
        weight: l.options.weight,
        cap: l.options.lineCap,
        join: l.options.lineJoin,
        interactive: l.options.interactive,
      })),
      lineColors: { one: lineColor("1"), a: lineColor("A") },
    };
  });
  const casings = measured.layers.filter((l) => l.weight === 6.5);
  const lines = measured.layers.filter((l) => l.weight === 4);
  expect(casings.length).toBe(2);
  expect(lines.length).toBe(2);
  // THE CASING IS THE TOKEN'S VALUE, which is what makes MR4's theme swap a setStyle rather
  // than a rebuild: the canvas cannot read a custom property, so the resolver's answer is
  // asserted to BE the token rather than a literal that happens to match it today.
  expect([...new Set(casings.map((l) => l.color))]).toEqual([measured.paper]);
  expect(new Set(lines.map((l) => l.color))).toEqual(new Set([measured.lineColors.one, measured.lineColors.a]));
  for (const layer of measured.layers) {
    expect(layer.cap).toBe("round");
    expect(layer.join).toBe("round");
    expect(layer.interactive, "a ribbon never takes a click").toBe(false);
  }
});

/* ---------------- the stations ---------------- */

test("D2i. a transfer ring where two or more routes call, a local dot where one does", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "the transfer ring drawn for single-route stations". The stock
     fixture's two stations are BOTH transfers, so this world adds a one-route station and a
     station with no routes field at all: without them the assertion has no subject. */
  await open(page, withLocalStation, { stations: 16 });
  const marks = await page.evaluate(() => {
    const ink = getComputedStyle(document.documentElement).getPropertyValue("--ink").trim();
    const paper = getComputedStyle(document.documentElement).getPropertyValue("--paper").trim();
    const of = (id) => {
      const entry = stationRegistry.find((row) => row.key === `subway|${id}`);
      const o = entry.marker.options;
      return {
        routes: (entry.routes ?? []).length,
        radius: o.radius,
        fillColor: o.fillColor,
        color: o.color,
        weight: o.weight,
        stroke: o.stroke,
        rendererPane: o.renderer.options.pane,
      };
    };
    return { ink, paper, transfer: of("127"), local: of("L01"), routeless: of("X01") };
  });

  expect(marks.transfer.routes).toBe(3);
  expect(marks.transfer.radius).toBe(4.5);
  expect(marks.transfer.fillColor).toBe(marks.paper);
  expect(marks.transfer.color).toBe(marks.ink);
  expect(marks.transfer.weight).toBe(2);
  expect(marks.transfer.stroke).toBe(true);

  expect(marks.local.routes).toBe(1);
  expect(marks.local.radius).toBe(3.5);
  expect(marks.local.fillColor).toBe(marks.ink);
  expect(marks.local.stroke, "a dot has no stroke").toBe(false);

  // A station the backend served no routes for is a DOT, not a ring: a missing value must not
  // become a claim that every station is an interchange.
  expect(marks.routeless.routes).toBe(0);
  expect(marks.routeless.radius).toBe(3.5);
  expect(marks.routeless.stroke).toBe(false);

  // And all three are still drawn by the canvas on stationPane, which is the pane the
  // z-index depends on and the one P2a pins.
  for (const mark of [marks.transfer, marks.local, marks.routeless]) {
    expect(mark.rendererPane).toBe("stationPane");
  }
});

/* ---------------- the labels ---------------- */

test("D2j. station names appear at the right zooms, hubs first, and the Names toggle hides them", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "labels not gated by zoom". Read as COMPUTED display rather
     than as the attribute, so the stylesheet's own selectors are what is being tested; an
     attribute written correctly and a rule that never matched would pass an attribute check
     and show every name in the city at zoom 3. */
  await open(page, withLocalStation, { stations: 16 });
  const painted = () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".leaflet-tooltip")]
        .filter((el) => getComputedStyle(el).display !== "none")
        .map((el) => el.textContent)
        .sort(),
    );
  const setZoom = async (z) => {
    await page.evaluate((zoom) => map.setZoom(zoom, { animate: false }), z);
    await expect(page.locator("html")).toHaveAttribute("data-zoom", String(z));
  };

  await setZoom(11);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "none");
  expect(await painted(), "below 12 no name is drawn").toEqual([]);

  await setZoom(12);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "hubs");
  expect(await painted(), "at 12 the transfer stations only").toEqual(["Canal St", "Times Sq-42 St"]);

  await setZoom(14);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "all");
  expect(await painted(), "at 14 every station").toEqual(["Canal St", "Lorimer St", "Nowhere", "Times Sq-42 St"]);

  // A hub's name is drawn larger, which is the same predicate the ring is.
  // READ AS THREE FACTS RATHER THAN AS ONE className STRING: Leaflet adds and removes its own
  // classes (leaflet-zoom-animated appears after a setZoom), so a whole-string comparison
  // fails on correct code the moment the map has been zoomed, which is every state that
  // matters here.
  const sizes = await page.evaluate(() =>
    Object.fromEntries(
      [...document.querySelectorAll(".leaflet-tooltip")].map((el) => [
        el.textContent,
        {
          size: getComputedStyle(el).fontSize,
          weight: getComputedStyle(el).fontWeight,
          label: el.classList.contains("stn-label"),
          hub: el.classList.contains("hub"),
        },
      ]),
    ),
  );
  expect(sizes["Times Sq-42 St"]).toEqual({ size: "11.5px", weight: "700", label: true, hub: true });
  expect(sizes["Lorimer St"]).toEqual({ size: "10.5px", weight: "600", label: true, hub: false });

  // THE NAMES TOGGLE, which overrides the band in one direction only.
  await page.locator("#names-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-labels", "off");
  await expect(page.locator("#names-toggle")).toHaveAttribute("aria-pressed", "false");
  expect(await painted(), "Names off hides every name at every zoom").toEqual([]);
  await setZoom(12);
  expect(await painted(), "including the hubs' at their own band").toEqual([]);

  await page.locator("#names-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-labels", "on");
  await expect(page.locator("#names-toggle")).toHaveAttribute("aria-pressed", "true");
  expect(await painted()).toEqual(["Canal St", "Times Sq-42 St"]);
});

/* ---------------- the ruling ---------------- */

test("D2k. no subway mark is a circle, which is the map's half of ruling R1", async ({ page }) => {
  /* D1l's SIBLING, ON THE MAP. The handoff draws the train marker as the authority's route
     symbol, two concentric circles with a letter in them; the repository's README rules the
     other way and round 3 settled it. D1l holds the Key's bullets; this holds the markers, and
     it holds the ribbon colours against lineColor() at the same time, because the other half
     of the same ruling is that the palette is this app's own.

     THE STATION DOTS AND RINGS ARE CIRCLES AND THAT IS THE POINT: a circle always means
     subway and a square always means regional rail, which is the design's own rule. What may
     not be a circle is the route BULLET, so the claim is scoped to the train markers. */
  await open(page, withYellow);
  const marks = await page.evaluate(() =>
    [...document.querySelectorAll(".train-marker svg")].map((svg) => ({
      circles: svg.querySelectorAll("circle").length,
      rects: [...svg.querySelectorAll("rect")].map((r) => `${r.getAttribute("width")}x${r.getAttribute("height")}@${r.getAttribute("rx")}`),
      letter: svg.querySelector("text")?.textContent ?? null,
      fill: svg.querySelectorAll("rect")[1]?.getAttribute("fill") ?? null,
    })),
  );
  expect(marks.length, "the fixture's two trains are on the map").toBe(2);
  for (const mark of marks) {
    expect(mark.circles, "a route bullet is never a circle").toBe(0);
    expect(mark.rects, "a paper halo behind the app's own rounded rectangle").toEqual(["18x18@4", "15x15@3"]);
  }
  // Every fill is lineColor()'s answer, never a hex from the handoff's palette row.
  const expected = await page.evaluate(() =>
    [...trains.values()].map((r) => lineColor(r.latest.route_id)),
  );
  expect(marks.map((m) => m.fill)).toEqual(expected);

  // And the ribbons take the same palette, which is the other place the ruling lands.
  const lineColors = await page.evaluate(() => ({
    drawn: routeLinesLayer.getLayers().filter((l) => l.options.weight === 4).map((l) => l.options.color),
    want: subwayRibbons.filter((r) => r.part === "line").map((r) => lineColor(r.route)),
  }));
  expect(lineColors.drawn).toEqual(lineColors.want);
});

/* ---------------- what MR2 did not touch ---------------- */

test("D2l. MR1's chrome and the status line are exactly where MR1 left them", async ({ page }) => {
  /* THE SCOPE CLAIM, made in the one form that cannot be argued with. MR2 edits the header
     (the bullets became buttons, the Names toggle joined the stack) and nothing else in it,
     so the things MR1 shipped are read back here rather than assumed to have survived. */
  await open(page);
  const chrome = await page.evaluate(() => ({
    feeds: [...document.querySelectorAll("#feed-buttons .feed")].map((b) => b.querySelector(".feed-name").textContent),
    note: document.getElementById("status").textContent,
    noteIsError: document.getElementById("status").classList.contains("error"),
    alertsFold: document.getElementById("alert-banner").classList.contains("hdr-fold"),
    themeHidden: document.getElementById("theme-toggle").hasAttribute("hidden"),
    legendRows: document.querySelectorAll("#legend .legend-row").length,
    keyExpanded: document.getElementById("legend-toggle").getAttribute("aria-expanded"),
    stationsExpanded: document.getElementById("stations-toggle").getAttribute("aria-expanded"),
  }));
  expect(chrome.feeds).toEqual([
    "Subway",
    "Buses",
    "LIRR",
    "Metro-North",
    "NJ Transit",
    "PATH",
    "Ferry",
    "AirTrain",
  ]);
  expect(chrome.note, "a healthy day still says nothing").toBe("");
  expect(chrome.noteIsError).toBe(false);
  expect(chrome.alertsFold, "the alerts strip still never folds").toBe(false);
  expect(chrome.themeHidden, "the theme toggle is still hidden until MR4").toBe(true);
  // Eighteen rows plus the one note is the nineteen a11y.spec.js A1x counts; MR2 restyled
  // three of these rows' glyphs in place and added none, which is what keeps that literal
  // and P1e's list of names both true without either one being edited.
  expect(chrome.legendRows, "the Key panel's row count is unchanged: MR2 restyled three glyphs in place").toBe(18);
  expect(chrome.keyExpanded).toBe("false");
  expect(chrome.stationsExpanded).toBe("true");

  // And focusing a route says nothing about any of it.
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  const after = await page.evaluate(() => ({
    note: document.getElementById("status").textContent,
    feeds: [...document.querySelectorAll("#feed-buttons .feed")].map((b) => b.getAttribute("aria-pressed")),
  }));
  expect(after.note).toBe("");
  expect([...new Set(after.feeds)], "every feed is still showing").toEqual(["true"]);
});
