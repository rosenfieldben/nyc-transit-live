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
      /* N AND Q COME FIRST IN THE PAYLOAD, and that is the whole reason this world exists.
         The mutation run caught the first version of this fixture appending them instead:
         the payload then already ended with yellow, so D2g passed with trunkDrawOrder
         reduced to `return [...routeIds]` and the spec measured nothing. A draw-order test
         over a payload that happens to arrive in draw order is not a test. */
      { route: "N", polylines: [[[40.7, -74.0], [40.71, -73.99]]] },
      { route: "Q", polylines: [[[40.72, -73.98], [40.73, -73.97]]] },
      ...fixtures.subwayRoutes(),
    ]);
};

/* A WORLD SHAPED LIKE THE REAL STATIC ARCHIVE, which is the only world where round 2's
   findings have subjects. Measured, the archive draws J and not Z, N/Q/R and not W, GS/FS/H
   and not S, and SI; the stock fixture draws two routes and nothing shares track with
   anything, so every membership claim over it would pass without looking. */
const REAL_SHAPED = ["1", "J", "N", "Q", "R", "GS", "FS", "H", "SI"];

const withRealShapedRoutes = (trainRoutes = [], extraRoutes = []) => (ctx) => {
  ctx.overrides.subwayRoutes = (route) =>
    json(route, [
      ...REAL_SHAPED.map((r) => ({ route: r, polylines: [[[40.7, -74.0], [40.71, -73.99]]] })),
      ...extraRoutes,
    ]);
  if (trainRoutes.length) {
    ctx.overrides.subways = (route, fixtures) => {
      const body = fixtures.subwaysWithSystems({});
      trainRoutes.forEach((id, i) => {
        if (body.data[i]) body.data[i] = { ...body.data[i], route_id: id };
      });
      return json(route, body);
    };
  }
};

/* ONE ROUTE THE BACKEND LISTS AND NEVER DRAWS, which is the subject of the disabled-bullet
   claim and does not exist in any other world here. The real endpoint deduplicates shapes
   and has never served an empty list for a route, but it is reachable: a route whose shapes
   all failed to parse comes back with `polylines: []`, and the bullet derived from it can
   light nothing. */
const withDarkRoute = (trainRoutes = []) => withRealShapedRoutes(trainRoutes, [{ route: "L", polylines: [] }]);

const keyBullets = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll("#subway-key .bul-group")].map((g) =>
      [...g.querySelectorAll(".bul")].map((b) => ({
        id: b.textContent,
        disabled: b.getAttribute("aria-disabled") === "true",
        title: b.title,
        tabIndex: b.tabIndex,
      })),
    ),
  );

/* THE STOCK FIXTURE HAS NO INTERCHANGE AND, AFTER ROUND 3, NEVER DID. Its two subway
   stations are ["1","2","3"] and ["A","C","E"], which read as transfers only while the rule
   counted route IDS; counting TRUNKS (F9) makes both of them locals, correctly, because the
   1/2/3 are one line and so are the A/C/E. So a spec about rings has to bring a station that
   really is one, and a spec about the hub label band has to bring a hub. They go in through
   the per-spec override seam rather than into the shared fixture, where they would move every
   other spec's world.

   X05 is the F9 case itself: a skip-stop pair, two ids and one trunk, which must draw as a
   dot. It is the station that would have been a ring before round 3. */
const withLocalStation = (ctx) => {
  ctx.overrides.subwayStops = (route, fixtures) =>
    json(route, [
      ...fixtures.subwayStops(),
      // One route, so a dot; and a second with no routes field at all, which is the case the
      // node test calls the direction that matters.
      { id: "L01", name: "Lorimer St", lat: 40.7141, lon: -73.9503, routes: ["L"] },
      { id: "X01", name: "Nowhere", lat: 40.7, lon: -73.95 },
      // Two TRUNKS, so a ring and a hub label: the only real interchange in this world.
      { id: "X04", name: "Fulton St", lat: 40.7102, lon: -74.0074, routes: ["A", "4", "J"] },
      // Two IDS and one trunk, which is the F9 case: a dot, not a ring.
      { id: "X05", name: "Marcy Av", lat: 40.7083, lon: -73.9578, routes: ["J", "Z"] },
    ]);
};

async function open(page, before, { stations = 14, viewport = DESKTOP, ribbons = 4 } = {}) {
  await page.setViewportSize(viewport);
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // The ribbon count is waited on rather than "more than zero", because the key is derived
  // from the route list and a spec that read it mid-load would see a shorter one.
  await page.waitForFunction(
    (want) =>
      trains.size === 2 && stationRegistry.length === want.stations && routeLinesLayer.getLayers().length === want.ribbons,
    { stations, ribbons },
    { timeout: 15_000 },
  );
  await page.clock.runFor(1000);
  return ctx;
}

const ribbons = (page) =>
  page.evaluate(() => subwayRibbons.map((r) => ({ route: r.route, part: r.part, opacity: r.layer.options.opacity })));

// The same ribbons with their route SETS, which is what round 2's membership claims read.
const ribbonSets = (page) =>
  page.evaluate(() =>
    subwayRibbons.map((r) => ({ route: r.route, routes: r.routes, part: r.part, opacity: r.layer.options.opacity })),
  );

const trainOpacities = (page) =>
  page.evaluate(() => Object.fromEntries([...trains.entries()].map(([id, r]) => [id, r.marker.options.opacity ?? 1])));

/* ---------------- the bullets, and what pressing one does ---------------- */

test("D2a. the key is derived from the loaded route list, grouped by trunk", async ({ page }) => {
  /* ROUND 2 REPLACED A TABLE WITH A DERIVATION. MR1 hard-coded ten trunks and twenty-three
     bullets; measured against the real static archive that table and the network disagreed
     in both directions, and MR2 made the bullets controls, so three of twenty-three controls
     did nothing and four drawn routes could not be reached at all. */
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });
  const groups = await keyBullets(page);
  // One group per trunk, in the data's order, and the shuttles collapsed into one S.
  expect(groups.map((g) => g.map((b) => b.id).join(""))).toEqual(["1", "JZ", "NQR", "S", "SI"]);
  // SI HAS ITS BULLET, which the hard-coded key never gave it although the map draws it.
  expect(groups.flat().map((b) => b.id)).toContain("SI");
  // And every bullet is one the world can draw something for.
  const universe = await page.evaluate(() => subwayRouteUniverse(subwayRouteList(), subwayTrainRoutes()));
  expect(groups.flat().map((b) => b.id).sort()).toEqual([...universe].sort());

  const key = await page.evaluate(() =>
    [...document.querySelectorAll("#subway-key .bul")].map((b) => ({
      tag: b.tagName,
      text: b.textContent,
      name: b.getAttribute("aria-label"),
      pressed: b.getAttribute("aria-pressed"),
    })),
  );
  expect([...new Set(key.map((b) => b.tag))], "every bullet is a button now, not a span").toEqual(["BUTTON"]);
  // THE NAME IS ON aria-label AND THE TEXT IS THE BARE ROUTE ID, which is not a style choice:
  // D1l resolves each bullet's expected colour as lineColor(textContent), so a visually hidden
  // label inside the chip would make that check compare the wrong thing.
  for (const bullet of key) {
    expect(bullet.name, `bullet ${bullet.text}`).toBe(`Focus route ${bullet.text}`);
    expect(bullet.pressed, `bullet ${bullet.text} starts unpressed`).toBe("false");
  }
  // And the group is back in the accessibility tree, which MR1 took it out of while it was
  // only a colour swatch and promised to restore "the moment the key does something". It is
  // a TOOLBAR as of round 2, which is what buys the one tab stop D2r measures.
  await expect(page.locator("#subway-key")).not.toHaveAttribute("aria-hidden", "true");
  await expect(page.locator("#subway-key")).toHaveAttribute("aria-label", "Focus a subway route");
  await expect(page.locator("#subway-key")).toHaveAttribute("role", "toolbar");

  /* AND A PRESSED BULLET PAINTS ITS RING OVER ITS NEIGHBOURS. Round 1 of the review found
     this by looking at the drawn key rather than at the rules: the bullets are siblings in
     a flex group with the design's 2px gap, all `position: relative` with `z-index: auto`,
     and siblings paint in DOM order, so a ring reaching 4px out was drawn into the gap and
     then covered by the next bullet's background for its outer half. On the "2" bullet,
     which had a neighbour on both sides, it came out whole on the left and cut off on the
     right. The stacking level is the fix; this is what says it is still there.

     THE BULLET IS Q, the middle of the N-Q-R group, because this world is shaped like the
     real archive and has no 2 or 3. That is itself the round's point: a spec that names a
     route the data does not have is a spec about a table rather than about a map. */
  await page.locator('#subway-key .bul[aria-label="Focus route Q"]').click();
  const stacking = await page.evaluate(() => ({
    pressed: getComputedStyle(document.querySelector('#subway-key .bul[aria-label="Focus route Q"]')).zIndex,
    plain: getComputedStyle(document.querySelector('#subway-key .bul[aria-label="Focus route R"]')).zIndex,
    ring: getComputedStyle(document.querySelector('#subway-key .bul[aria-label="Focus route Q"]')).boxShadow,
  }));
  expect(stacking.pressed, "a pressed bullet must paint above its neighbours").not.toBe("auto");
  expect(stacking.plain, "and an ordinary one must not, so the header still paints in source order").toBe("auto");
  expect(stacking.ring, "the ring is a surface gap and then ink, measured against the header's surface").toMatch(
    /0px 0px 0px 2px.*0px 0px 0px 4px/,
  );
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

/* ---------------- round 2, G3: focus is membership ----------------
   The four cases the operator named, each in the world that has a subject for it. What
   every one of them is really asserting is that a route with no shape of its own is
   REACHABLE: before round 2 the Z bullet was a coloured span over a map that draws J, and
   pressing it would have dimmed everything and lit nothing. */

test("D2n. a route with no shape rides its trunk-mate's ribbon, and either bullet lights both", async ({ page }) => {
  // THE Z CASE. The archive draws J and not Z, and they share the brown trunk, so J's
  // polylines carry both ids and pressing either one leaves that ribbon alone.
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });

  const tags = await ribbonSets(page);
  const sets = Object.fromEntries(tags.filter((r) => r.part === "line").map((r) => [r.route, r.routes.join("+")]));
  expect(sets.J, "the Jamaica Ave ribbon is J AND Z, which is the whole of G3").toBe("J+Z");
  // and nobody else picked up a passenger: every other drawn route carries itself alone.
  expect(sets["1"]).toBe("1");
  expect(sets.N).toBe("N");
  expect(sets.SI).toBe("SI");

  await page.locator('#subway-key .bul[aria-label="Focus route Z"]').click();
  const byKey = Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  expect(byKey["J|line"], "pressing Z lights the ribbon Z runs on").toBe(1);
  expect(byKey["J|casing"]).toBe(0.9);
  expect(byKey["1|line"], "and every ribbon it does not run on drops").toBe(0.18);
  expect(byKey["1|casing"]).toBe(0);
  expect(byKey["N|line"]).toBe(0.18);

  // The trains, which is the half a ribbon test would miss: sub-2 is the Z train.
  expect(await trainOpacities(page)).toEqual({ "sub-1": 0.15, "sub-2": 1 });
  await expect(page.locator("#page-announce")).toHaveText("Focused on the Z; press again to clear.");

  /* PRESSING J LIGHTS THE SAME RIBBON AND A DIFFERENT TRAIN, and that asymmetry is the
     model rather than a gap in it. The ribbon is shared because the app has no Z shape to
     separate it with; the trains are not shared, because every train carries its own
     route_id. So a rider asking for the J gets the J's track and the J's trains, and the Z
     train beside it dims. sub-2 is that Z train. */
  await page.locator('#subway-key .bul[aria-label="Focus route J"]').click();
  expect(Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]))).toEqual(byKey);
  expect(await trainOpacities(page)).toEqual({ "sub-1": 0.15, "sub-2": 0.15 });
  // Only one bullet is pressed at a time even though both resolve to the same routes: the
  // pressed state is the CONTROL's, not the route set's.
  await expect(page.locator('#subway-key .bul[aria-label="Focus route J"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('#subway-key .bul[aria-label="Focus route Z"]')).toHaveAttribute("aria-pressed", "false");
  // And the title said so before it happened.
  await expect(page.locator('#subway-key .bul[aria-label="Focus route Z"]')).toHaveAttribute(
    "title",
    "Focus Z. Shares track with J.",
  );
});

test("D2o. a W train lights all three yellow ribbons, because no shape tells them apart", async ({ page }) => {
  /* THE W CASE, and the one where membership is a set rather than a pair. N, Q and R are
     each drawn and W is not, so W could be on any of the three and all three light. The
     alternative would be a W bullet that dims the map and lights one marker, which is worse
     than not offering it. */
  await open(page, withRealShapedRoutes(["W", "1"]), { ribbons: 18 });

  const sets = Object.fromEntries(
    (await ribbonSets(page)).filter((r) => r.part === "line").map((r) => [r.route, r.routes.join("+")]),
  );
  expect([sets.N, sets.Q, sets.R]).toEqual(["N+W", "Q+W", "R+W"]);

  await page.locator('#subway-key .bul[aria-label="Focus route W"]').click();
  const byKey = Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  for (const route of ["N", "Q", "R"]) {
    expect(byKey[`${route}|line`], `${route} is track W could be on`).toBe(1);
    expect(byKey[`${route}|casing`]).toBe(0.9);
  }
  for (const route of ["1", "J", "SI", "GS"]) {
    expect(byKey[`${route}|line`], `${route} is not`).toBe(0.18);
    expect(byKey[`${route}|casing`]).toBe(0);
  }
  // sub-1 is the W train and sub-2 is a 1 train.
  expect(await trainOpacities(page)).toEqual({ "sub-1": 1, "sub-2": 0.15 });

  // AND N'S OWN BULLET LIGHTS THE SAME RIBBON, not a different one: the ribbon is one
  // object with one opacity, so the set is on the layer and not on the press.
  await page.locator('#subway-key .bul[aria-label="Focus route N"]').click();
  const afterN = Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  expect(afterN["N|line"]).toBe(1);
  expect(afterN["Q|line"], "Q is a different ribbon and N is not on it").toBe(0.18);
  // The W train drops now, which is the honest answer: it may be on Q's track instead.
  expect(await trainOpacities(page)).toEqual({ "sub-1": 0.15, "sub-2": 0.15 });
});

test("D2p. one S bullet is three shuttles, and SI has a bullet at last", async ({ page }) => {
  /* THE S AND SI CASES. GS, FS and H are three feed ids for what a rider calls the shuttle,
     so they collapse into one control that focuses all three; SI is a drawn route the
     hard-coded key simply never offered. */
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });
  const ids = (await keyBullets(page)).flat().map((b) => b.id);
  expect(ids, "one bullet, not three").toContain("S");
  expect(ids).not.toContain("GS");
  expect(ids).not.toContain("FS");
  expect(ids).not.toContain("H");
  expect(ids, "and the one the table forgot").toContain("SI");

  // THE TOOLTIP SAYS WHICH THREE, which is what makes one control for three routes legible.
  await expect(page.locator('#subway-key .bul[aria-label="Focus route S"]')).toHaveAttribute(
    "title",
    "S is GS, FS, H. Focus S.",
  );

  await page.locator('#subway-key .bul[aria-label="Focus route S"]').click();
  const byKey = Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  for (const route of ["GS", "FS", "H"]) {
    expect(byKey[`${route}|line`], `${route} is one of the three`).toBe(1);
    expect(byKey[`${route}|casing`]).toBe(0.9);
  }
  for (const route of ["1", "J", "N", "SI"]) expect(byKey[`${route}|line`], route).toBe(0.18);
  // The announcement uses the bullet a rider pressed, not the three ids underneath it.
  await expect(page.locator("#page-announce")).toHaveText("Focused on the S; press again to clear.");

  await page.locator('#subway-key .bul[aria-label="Focus route SI"]').click();
  const si = Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  expect(si["SI|line"]).toBe(1);
  expect(si["GS|line"], "SI is its own trunk and takes nothing with it").toBe(0.18);
  await expect(page.locator('#subway-key .bul[aria-label="Focus route SI"]')).toHaveAttribute("title", "Focus SI.");
});

test("D2q. a bullet that would light nothing is present, disabled, and cannot dim the map", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "a bullet with an empty set dims the map". A route the
     backend lists with no shapes and no train running has nothing to focus, and the naive
     implementation, where a bullet is a control and every control focuses its own id, would
     drop every ribbon to 0.18 and every train to 0.15 and light nothing at all: a blank map
     whose only way back is pressing the same dead bullet a second time.

     IT IS STILL IN THE KEY, and that is the accessibility decision rather than a cosmetic
     one. `disabled` would take it out of the tab order and out of the accessibility tree,
     so a rider who cannot see the key would not learn the route exists; aria-disabled keeps
     it announced with its title saying why. */
  await open(page, withDarkRoute(["1", "Z"]), { ribbons: 18 });

  const all = (await keyBullets(page)).flat();
  const dark = all.find((b) => b.id === "L");
  expect(dark, "the route is listed, so its bullet is drawn").toBeTruthy();
  expect(dark.disabled).toBe(true);
  expect(dark.title, "and it says why rather than just looking grey").toBe("Nothing on the map right now for L.");
  // Every other bullet in this world is live, so "disabled" is a measurement and not a mood.
  expect(all.filter((b) => b.disabled).map((b) => b.id)).toEqual(["L"]);

  const before = await ribbonSets(page);
  const trainsBefore = await trainOpacities(page);
  /* FORCED, and that is the point rather than a workaround. Playwright's actionability check
     treats aria-disabled="true" as not enabled and refuses to click, which is what a mouse
     rider's browser does NOT do: the element is a live <button> with no `disabled` attribute
     and a real click lands on it. Forcing the click is therefore the faithful simulation,
     and the inertness under test is the handler's own early return. */
  await page.locator('#subway-key .bul[aria-label="Focus route L"]').click({ force: true });
  expect(await ribbonSets(page), "pressing it must not dim one single ribbon").toEqual(before);
  expect(await trainOpacities(page), "nor one single train").toEqual(trainsBefore);
  await expect(page.locator('#subway-key .bul[aria-label="Focus route L"]')).toHaveAttribute("aria-pressed", "false");
  // Nothing was announced either, because nothing happened.
  await expect(page.locator("#page-announce")).toHaveText("");

  /* IT IS NOT A TAB STOP AND NOT IN THE ARROW ROTATION, which is the other half of inert: a
     rider arrowing along the key does not land on a control that will not act.

     THE TWO PRESSES THAT MEASURE IT ARE THE ONES EITHER SIDE OF IT (round 3, F13). This spec
     used to press ArrowRight from SI and assert it wrapped to 1, and that answer is the same
     with the aria-disabled filters deleted: with nothing pressed the roving stop is bullets[0]
     either way, and the wrap is (7+1)%8 = 0 against (8+1)%9 = 0, both "1". Deleting both
     filters left 92 specs green. The DOM order here is 1, J, Z, L, N, Q, R, S, SI, so the
     presses that can tell the difference are the step forward from Z and the step back from N,
     which must skip L in both directions. */
  expect(dark.tabIndex).toBe(-1);
  const at = () => page.evaluate(() => document.activeElement.getAttribute("aria-label"));
  expect((await keyBullets(page)).flat().map((b) => b.id), "L sits between Z and N").toEqual([
    "1", "J", "Z", "L", "N", "Q", "R", "S", "SI",
  ]);

  await page.locator('#subway-key .bul[aria-label="Focus route Z"]').focus();
  await page.keyboard.press("ArrowRight");
  expect(await at(), "forward over the dark bullet lands past it, not on it").toBe("Focus route N");
  await page.keyboard.press("ArrowLeft");
  expect(await at(), "and back again skips it too").toBe("Focus route Z");

  await page.locator('#subway-key .bul[aria-label="Focus route SI"]').focus();
  await page.keyboard.press("ArrowRight");
  // SI is the last enabled bullet in this world, so ArrowRight wraps to the first one.
  expect(await at()).toBe("Focus route 1");
  // And no disabled bullet is ever the single tab stop, at any point in the rotation.
  const stops = await page.evaluate(() =>
    [...document.querySelectorAll("#subway-key .bul")]
      .filter((b) => b.tabIndex === 0)
      .map((b) => b.getAttribute("aria-disabled")),
  );
  expect(stops).toEqual(["false"]);

  /* AND THE TREATMENT IS DRAWN, in both themes, which the mutation run had to tell me was
     unguarded: nothing asserted the disabled bullet's painted colours, so keeping the inline
     route chip (and with it the old blanket fade's illegible letter) left every tier green.

     THE CHIP CARRIES THE STATE AND THE LETTER CARRIES THE ROUTE (F17). The first treatment was
     `filter: grayscale(0.7); opacity: 0.55` on the whole button, which took the route letter to
     between 2.17 and 3.80 against its own chip from between 5.00 and 9.30: a state conveyed by
     making a route name unreadable, which MR1 round 2 ruled out twice. */
  for (const theme of ["light", "dark"]) {
    const drawn = await page.evaluate(
      (t) => {
        document.documentElement.setAttribute("data-theme", t);
        const el = document.querySelector('#subway-key .bul[aria-label="Focus route L"]');
        const live = document.querySelector('#subway-key .bul[aria-label="Focus route 1"]');
        const cs = getComputedStyle(el);
        const root = getComputedStyle(document.documentElement);
        return {
          background: cs.backgroundColor,
          color: cs.color,
          wantBackground: root.getPropertyValue("--chip-off").trim(),
          wantInk: root.getPropertyValue("--chip-off-ink").trim(),
          opacity: cs.opacity,
          filter: cs.filter,
          cursor: cs.cursor,
          liveBackground: getComputedStyle(live).backgroundColor,
          // contrastRatio and parseColor are the page's own helpers, so the number asserted
          // here is the one the app would compute rather than a second implementation.
          ratio: contrastRatio(cs.color, cs.backgroundColor),
          isToken:
            JSON.stringify(parseColor(cs.backgroundColor).map(Math.round)) ===
            JSON.stringify(parseColor(root.getPropertyValue("--chip-off").trim()).map(Math.round)),
          inkIsToken:
            JSON.stringify(parseColor(cs.color).map(Math.round)) ===
            JSON.stringify(parseColor(root.getPropertyValue("--chip-off-ink").trim()).map(Math.round)),
        };
      },
      theme,
    );
    // The chip is the token's grey, not the route's colour and not a faded version of it.
    expect(drawn.isToken, `${theme}: the dark chip must be --chip-off (${drawn.background})`).toBe(true);
    expect(drawn.inkIsToken, `${theme}: and its letter --chip-off-ink (${drawn.color})`).toBe(true);
    expect(drawn.background, `${theme}: not the route's own colour`).not.toBe(drawn.liveBackground);
    expect(drawn.opacity, `${theme}: state is not conveyed by opacity`).toBe("1");
    expect(drawn.filter, `${theme}: nor by a filter over the letter`).toBe("none");
    expect(drawn.cursor, `${theme}: and the pointer says it will not act`).toBe("default");
    /* THE LETTER CLEARS 4.5 AGAINST ITS OWN CHIP, which is the whole point: 10px and 12px text
       owes 4.5, and the treatment this replaced measured as low as 2.17. */
    expect(drawn.ratio, `${theme}: the route letter on a dark chip reads ${drawn.ratio}`).toBeGreaterThanOrEqual(
      4.5,
    );
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "light"));

  // A live bullet still works in the same key, so the disabling is per bullet.
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  const byKey = Object.fromEntries((await ribbonSets(page)).map((r) => [`${r.route}|${r.part}`, r.opacity]));
  expect(byKey["1|line"]).toBe(1);
  expect(byKey["J|line"]).toBe(0.18);
});

test("D2r. the key is one tab stop, and the arrow keys move inside it", async ({ page }) => {
  /* F4, FIXED. Twenty-three bullets as twenty-three tab stops put the Stations button 32
     presses from the top of the document where it used to be nine. A toolbar is one stop.

     THE COUNT ITSELF IS NOT MEASURED HERE, because this world serves nine routes and the
     number only means something over the real twenty-four; it was taken with the same
     harness as the other MR2 numbers and both figures are in the ledger. What IS measured
     here is the property the count follows from, which is that the number of tab stops the
     key costs does not grow with the number of routes. */
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });

  const stops = () => page.evaluate(() => [...document.querySelectorAll("#subway-key .bul")].map((b) => b.tabIndex));
  const ids = (await keyBullets(page)).flat().map((b) => b.id);
  expect(ids.length, "eight bullets in this world").toBe(8);
  expect((await stops()).filter((t) => t === 0).length, "and one tab stop between them").toBe(1);
  expect((await stops())[0], "which is the first bullet until a rider moves it").toBe(0);

  // TABBING PAST THE KEY COSTS ONE PRESS. Counted from the bullet itself so the count is
  // about the key and not about whatever precedes it in the header.
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').focus();
  await page.keyboard.press("Tab");
  const after = await page.evaluate(() => document.activeElement.id || document.activeElement.getAttribute("aria-label"));
  expect(after, "one Tab leaves the whole key behind").not.toMatch(/^Focus route/);

  // ARROWS MOVE WITHIN, and the single stop moves with the focus so that returning to the
  // key by Tab returns to where the rider left it.
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').focus();
  const here = () => page.evaluate(() => document.activeElement.getAttribute("aria-label"));
  await page.keyboard.press("ArrowRight");
  expect(await here()).toBe("Focus route J");
  expect(await stops()).toEqual([-1, 0, -1, -1, -1, -1, -1, -1]);
  await page.keyboard.press("ArrowDown");
  expect(await here(), "down is the same as right in a horizontal toolbar, and costs nothing").toBe("Focus route Z");
  await page.keyboard.press("ArrowLeft");
  expect(await here()).toBe("Focus route J");
  await page.keyboard.press("End");
  expect(await here()).toBe("Focus route SI");
  await page.keyboard.press("ArrowRight");
  expect(await here(), "and it wraps rather than stopping").toBe("Focus route 1");
  await page.keyboard.press("ArrowLeft");
  expect(await here(), "in both directions").toBe("Focus route SI");
  await page.keyboard.press("Home");
  expect(await here()).toBe("Focus route 1");

  // A KEY THAT MOVES DOES NOT STRAND THE TAB STOP: pressing a bullet makes the pressed one
  // the stop, so a rider who tabs away and back lands on the route they focused.
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await expect(page.locator('#subway-key .bul[aria-label="Focus route SI"]')).toHaveAttribute("aria-pressed", "true");
  expect(await stops()).toEqual([-1, -1, -1, -1, -1, -1, -1, 0]);
  expect((await stops()).filter((t) => t === 0).length, "still exactly one").toBe(1);
});

test("D2v. an off-focus train leaves the reading order and stops taking clicks, and comes back", async ({
  page,
}) => {
  /* THE OPERATOR'S RULING ON F4. Focus keeps multiplying on the freshness contract's own
     opacity and markerOpacity is untouched, so FOCUS_DIM_TRAIN stays 0.15 and an off-focus
     stale train still lands at 0.45 * 0.15 = 0.0675. What is wrong with 0.0675 is not the
     number: Leaflet's setOpacity writes nothing but style.opacity, so that marker was
     invisible and still took the click at its 24px halo and still announced itself to a
     screen reader as a train a rider could choose. A mark the map has deliberately pushed
     into the background must not be the thing a tap lands on.

     BOTH DIRECTIONS, because a focus that left a marker permanently silent and unclickable
     would be worse than the dimming it completes. */
  await open(page);
  const reach = () =>
    page.evaluate(() =>
      Object.fromEntries(
        [...trains.entries()].map(([id, r]) => {
          const el = r.marker.getElement();
          return [
            id,
            {
              hidden: el.getAttribute("aria-hidden"),
              pointer: getComputedStyle(el).pointerEvents,
              haloPointer: getComputedStyle(el, "::before").pointerEvents,
              opacity: r.marker.options.opacity ?? 1,
              named: !!el.getAttribute("aria-label"),
            },
          ];
        }),
      ),
    );

  const before = await reach();
  for (const [id, m] of Object.entries(before)) {
    expect(m.hidden, `${id} starts in the tree`).toBe(null);
    expect(m.pointer, `${id} starts clickable`).not.toBe("none");
    expect(m.named, `${id} keeps its accessible name either way`).toBe(true);
  }

  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  const focused = await reach();
  // sub-1 is on the focused route and sub-2 is not.
  expect(focused["sub-1"].hidden, "the focused route's train is untouched").toBe(null);
  expect(focused["sub-1"].pointer).not.toBe("none");
  expect(focused["sub-2"].hidden, "an off-focus train leaves the accessibility tree").toBe("true");
  expect(focused["sub-2"].pointer, "and stops taking clicks").toBe("none");
  // THE HALO TOO, which is the 24px box that actually swallows the click; it sets no
  // pointer-events of its own, so it inherits, and that is the thing being relied on.
  expect(focused["sub-2"].haloPointer).toBe("none");
  // The dimming is unchanged and still the contract's own product.
  expect(focused["sub-2"].opacity).toBeCloseTo(0.15, 5);

  // A REAL CLICK AT ITS CENTRE MUST NOT OPEN ITS POPUP, which is the claim in the form a
  // rider makes it. Asked of the map rather than the DOM, because the paused clock keeps a
  // closed popup's element alive.
  const at = await page.evaluate(() => {
    const el = trains.get("sub-2").marker.getElement();
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  });
  await page.mouse.click(at.x, at.y);
  expect(await page.evaluate(() => openPopupsOnMap().length), "a dimmed train is not a target").toBe(0);

  // AND IT ALL COMES BACK on clear, attribute for attribute.
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  expect(await reach()).toEqual(before);
  // Including that it is clickable again.
  await page.mouse.click(at.x, at.y);
  expect(await page.evaluate(() => openPopupsOnMap().length), "and is a target once focus clears").toBe(1);
});

test("D2w. the reach survives a poll, and a train arriving mid-focus is out of reach at once", async ({ page }) => {
  /* setIcon REPLACES THE ELEMENT, so both attributes would be dropped silently by any poll
     that relabelled a route, and a train that arrived while a route was focused would be
     drawn dim and left clickable until the next press. Both are re-derived at the poll tail. */
  const ctx = await open(page);
  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  const hidden = () =>
    page.evaluate(() =>
      Object.fromEntries(
        [...trains.entries()].map(([id, r]) => [id, r.marker.getElement().getAttribute("aria-hidden")]),
      ),
    );
  expect(await hidden()).toEqual({ "sub-1": null, "sub-2": "true" });

  await page.clock.runFor(15_000);
  await expect.poll(hidden, { message: "a poll must not restore the reach" }).toEqual({
    "sub-1": null,
    "sub-2": "true",
  });

  // Now a third train arrives on a route that is not focused.
  ctx.overrides.subways = (route, fixtures) => {
    const body = fixtures.subwaysWithSystems({});
    body.data.push({ ...body.data[1], trip_id: "sub-3", vehicle_id: "sub-3", route_id: "L" });
    return json(route, body);
  };
  await page.clock.runFor(15_000);
  await expect
    .poll(hidden, { message: "a train arriving mid-focus is out of reach on its first frame" })
    .toEqual({ "sub-1": null, "sub-2": "true", "sub-3": "true" });
});

/* ---------------- round 3: the guards nothing was measuring ---------------- */

test("D2s. a focused bullet disappearing from the key takes the focus with it", async ({ page }) => {
  /* THE MUTATION THIS KILLS is replacing refreshSubwayKey's rebuild branch
     `if (focusedBullet && !subwayKeyBullets.has(focusedBullet)) clearRouteFocus();`
     with a bare paintRouteFocus(). Measured, that mutation leaves the whole suite green while
     doing this to a rider: focus the Z, let the next poll carry no Z train so the universe
     loses the Z bullet, and every ribbon and every train stays dimmed with NO bullet showing
     aria-pressed and the live region still saying "press again to clear". The state survives
     in a variable pointing at a control that is no longer on the page.

     No spec in the suite shrank the bullet universe while a route was focused, which is the
     only way to reach it: D2e re-serves the same route ids, and the one other spec that changes
     the universe focuses nothing. */
  const ctx = await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });
  await page.locator('#subway-key .bul[aria-label="Focus route Z"]').click();
  const dimmed = (rs) => rs.filter((r) => r.part === "line" && r.opacity === 0.18).length;
  expect(dimmed(await ribbonSets(page)), "eight of the nine ribbons dim").toBe(8);
  await expect(page.locator('#subway-key .bul[aria-label="Focus route Z"]')).toHaveAttribute("aria-pressed", "true");

  // The next poll carries no Z train, so Z stops being a route this map can draw anything for
  // and the bullet goes with it.
  ctx.overrides.subways = (route, fixtures) => json(route, fixtures.subwaysWithSystems({}));
  await page.clock.runFor(15_000);
  await expect
    .poll(() => page.evaluate(() => [...document.querySelectorAll("#subway-key .bul")].map((b) => b.textContent)))
    .not.toContain("Z");

  // AND THE MAP IS BACK, which is the whole claim: no ribbon dim, no train dim, nothing pressed.
  expect(dimmed(await ribbonSets(page)), "nothing may be left dimmed").toBe(0);
  expect(Object.values(await trainOpacities(page)).every((o) => o === 1), "nor any train").toBe(true);
  expect(
    await page.evaluate(() => [...document.querySelectorAll("#subway-key .bul[aria-pressed='true']")].length),
  ).toBe(0);
  expect(await page.evaluate(() => currentFocusBullet())).toBe(null);
  await expect(page.locator("#page-announce")).toHaveText("Route focus cleared.");
});

test("D2t. the key does not rebuild under a rider's hand every fifteen seconds", async ({ page }) => {
  /* THE MUTATION THIS KILLS is replacing refreshSubwayKey's `if (signature !== subwayKeyUniverse)`
     with `if (true)`, so the poll tail rebuilds the toolbar every time. 92 specs stay green,
     and a rider holding keyboard focus on a bullet loses it every fifteen seconds, because
     buildSubwayKey calls replaceChildren() and removes the button they are standing on.

     The guard's own comment claims "a key does not twitch under a rider's hand every fifteen
     seconds" and nothing measured it: no spec advanced a poll with focus inside the key. */
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });
  const bullet = '#subway-key .bul[aria-label="Focus route N"]';
  await page.locator(bullet).focus();
  const stamp = () =>
    page.evaluate(() => ({
      focused: document.activeElement.getAttribute("aria-label"),
      // A rebuild replaces every button, so element identity is the question. Leaflet has no
      // stamp for a DOM node, so a marker attribute is written and looked for afterwards.
      marked: document.querySelector('#subway-key .bul[aria-label="Focus route N"]').dataset.probe ?? null,
    }));
  await page.evaluate(() => {
    document.querySelector('#subway-key .bul[aria-label="Focus route N"]').dataset.probe = "same-node";
  });
  expect(await stamp()).toEqual({ focused: "Focus route N", marked: "same-node" });

  // Two polls, same route ids both times.
  await page.clock.runFor(15_000);
  await page.clock.runFor(15_000);
  expect(await stamp(), "the same button, still focused, after two polls").toEqual({
    focused: "Focus route N",
    marked: "same-node",
  });

  /* AND THE LEGITIMATE REBUILD STILL HAPPENS, which is the other half: a poll that really does
     change the set of bullets must replace them, and that is allowed to cost the focus. A
     guard that never rebuilt would be just as wrong and would pass the assertion above. */
  await page.evaluate(() => {
    for (const b of document.querySelectorAll("#subway-key .bul")) b.dataset.probe = "old";
  });
  const routes = [...REAL_SHAPED, "L"];
  const ctx2 = { overrides: {} };
  await page.evaluate(() => {}); // no-op, keeps the shape of the surrounding code explicit
  await page.evaluate((ids) => {
    // Teach the page a new drawable route the way a poll would: a train on a route the key
    // has never seen. The signature changes, so the toolbar is rebuilt.
    const [first] = trains.values();
    first.latest = { ...first.latest, route_id: "L" };
    refreshSubwayKey();
    return ids;
  }, routes);
  await expect
    .poll(() => page.evaluate(() => [...document.querySelectorAll("#subway-key .bul")].map((b) => b.textContent)))
    .toContain("L");
  expect(
    await page.evaluate(() =>
      [...document.querySelectorAll("#subway-key .bul")].every((b) => b.dataset.probe === undefined),
    ),
    "a real change in the bullet set rebuilds them",
  ).toBe(true);
});

test("D2z. a network with no interchange anywhere shows every name from 13, not nothing from 12", async ({
  page,
}) => {
  /* THE BROWSER HALF OF F1. The node tier kills the band rule itself, but nothing asked whether
     paintZoomBand actually passes the hub count, which is the part that can only be answered by
     a page: it counts `.stn-label.hub` in the DOM, so it depends on the labels existing by the
     time it runs, which is why loadStations calls it again.

     THE STOCK FIXTURE IS THIS WORLD ALREADY, and that is worth saying: its two stations are
     ["1","2","3"] and ["A","C","E"], three ids each and one trunk each, so after F9 neither is
     an interchange and this is the no-hub case without anything being contrived. It stands in
     for the degraded backend, where stop_times.txt is missing and every station lists no routes
     at all: same shape, no hub to reveal, and "hubs from 12" correctly showing nothing. */
  await open(page);
  // THE SUBWAY'S NAMES ONLY, for the reason D2j's copy of this helper states: MR3 gave the
  // commuter rail families their own band from zoom 11 on the same pane, so an unscoped count
  // reads their five names here and calls a hubless SUBWAY network broken.
  const painted = () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".stn-label:not(.rail)")]
        .filter((el) => getComputedStyle(el).display !== "none")
        .map((el) => el.textContent)
        .sort(),
    );
  const setZoom = async (z) => {
    await page.evaluate((zoom) => map.setZoom(zoom, { animate: false }), z);
    await expect(page.locator("html")).toHaveAttribute("data-zoom", String(z));
  };
  expect(
    await page.evaluate(() => document.querySelectorAll(".stn-label.hub").length),
    "this world has no interchange, which is the premise",
  ).toBe(0);
  expect(
    await page.evaluate(() => document.querySelectorAll(".stn-label:not(.rail)").length),
    "and it does have SUBWAY station labels, which is what a hubless subway network means",
  ).toBeGreaterThan(0);

  await setZoom(11);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "none");
  expect(await painted()).toEqual([]);

  // 12 is still nothing, because with no hub to thin the field 12 is the zoom the collision
  // measurements found worst.
  await setZoom(12);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "none");
  expect(await painted(), "at 12 a hubless network still shows nothing").toEqual([]);

  // And 13 is everything, one zoom earlier than the all band, so a rider does not have to
  // reach 14 to see any name at all.
  await setZoom(13);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "all");
  expect(await painted(), "at 13 every name").toEqual(["Canal St", "Times Sq-42 St"]);

  /* AND THE TOOLTIP SAYS WHY, in the one state a rider cannot deduce from the screen. Not here:
     this world's stations DO list their routes, they just happen to be one line each, so there
     is nothing to explain. The sentence is for the world where no station lists anything. */
  await expect(page.locator("#names-toggle")).toHaveAttribute("title", "");

  await expect(page.locator("#names-toggle")).toHaveAttribute("aria-pressed", "true");
});

test("D2z1. when no station lists its routes at all, the toggle's tooltip says why", async ({ page }) => {
  /* THE DEGRADED BACKEND ITSELF. stop_times.txt is not a required member of the subway static
     archive, load_subway_station_routes returns {} on any failure, and the endpoint then serves
     routes: [] for every station while the status stays "ready". So this world is reachable and
     it is the one a rider cannot diagnose: no ring anywhere, no name marked as an interchange,
     and no error. The band's fallback keeps the names visible from 13, and the toggle carries
     the sentence that explains the missing rings.

     THE BACKEND HALF IS ITS OWN BRANCH after this one merges, with all three consumers of the
     index named in the ledger. This is the honest frontend behaviour in the meantime. */
  await open(page, (ctx) => {
    ctx.overrides.subwayStops = (route, fixtures) =>
      json(route, fixtures.subwayStops().map(({ routes, ...rest }) => rest));
  });
  expect(
    await page.evaluate(() => stationRegistry.filter((e) => e.kind === "subway").every((e) => !e.routes.length)),
    "no station lists a route, which is the premise",
  ).toBe(true);
  expect(await page.evaluate(() => document.querySelectorAll(".stn-label.hub").length)).toBe(0);
  // No ring anywhere either, which is the same predicate.
  expect(
    await page.evaluate(() =>
      stationRegistry.filter((e) => e.kind === "subway").every((e) => e.marker.options.stroke === false),
    ),
  ).toBe(true);

  await page.evaluate(() => map.setZoom(13, { animate: false }));
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "all");
  await expect(page.locator("#names-toggle")).toHaveAttribute(
    "title",
    "No station lists the routes that call there, so every name shows from zoom 13 and none is marked as an interchange.",
  );
});

test("D2x. a station name is painted below every vehicle, which is the pane it is bound to", async ({ page }) => {
  /* THE MUTATION THIS KILLS is deleting the `pane` option from the bindTooltip call, which puts
     the labels back in Leaflet's default tooltipPane at z-index 650, ABOVE markerPane's 600.
     That is the state this branch shipped until round 3: measured from its own committed
     screenshots, a station name painted across a train bullet and took 44% of its
     route-coloured pixels and its letter with it.

     IT NEEDED ITS OWN SPEC AND THE MUTATION RUN IS WHAT SAID SO. D2u asserts the pane ORDER,
     which stays correct with this mutation applied because the panes are still created; what
     nothing asked was which pane the tooltips actually go INTO. The fix went in unguarded and
     the run caught it, with all 312 node tests and all 41 specs in this file and layout.spec.js
     green. */
  await open(page, withLocalStation, { stations: 18 });
  const where = await page.evaluate(() => {
    const z = (name) => Number(getComputedStyle(map.getPane(name)).zIndex);
    const tips = stationRegistry
      .filter((entry) => entry.kind === "subway" && entry.marker?.getTooltip)
      .map((entry) => {
        const el = entry.marker.getTooltip()?.getElement();
        return {
          name: entry.name,
          option: entry.marker.getTooltip()?.options.pane ?? null,
          paneClass: el?.parentElement?.className ?? null,
          opacity: el ? getComputedStyle(el).opacity : null,
        };
      });
    return {
      tips,
      labelPaneZ: z("stationLabelPane"),
      markerPaneZ: z("markerPane"),
      tooltipPaneZ: z("tooltipPane"),
      stationPaneZ: z("stationPane"),
    };
  });

  expect(where.tips.length, "this world has station labels to place").toBeGreaterThan(3);
  for (const tip of where.tips) {
    expect(tip.option, `${tip.name} is bound to the label pane`).toBe("stationLabelPane");
    expect(tip.paneClass, `${tip.name} is rendered into it`).toContain("leaflet-stationLabel-pane");
    expect(tip.paneClass, `${tip.name} is not in Leaflet's default tooltip pane`).not.toContain(
      "leaflet-tooltip-pane",
    );
    /* AND AT FULL OPACITY (F14). Leaflet's Tooltip defaults to opacity 0.9 and writes it as an
       INLINE style in onAdd, which no stylesheet rule can reach, so every name rendered at 90%
       group alpha while a11y.spec.js A1z3 measured the ink at 100% and reported a ratio about
       23% better than the drawn one. */
    expect(tip.opacity, `${tip.name} draws at the ink the contrast was measured at`).toBe("1");
  }

  /* AND THE RIDER'S OWN QUESTION, ASKED AT THE PIXEL: is anything painted over the bullet that
     says which train this is. elementsFromPoint returns the stack front to back, so a label
     above the marker would appear before it. This is the assertion the original defect would
     have failed and the box-versus-anchor measurement that missed it could not make. */
  const covered = await page.evaluate(() =>
    [...trains.values()].map((record) => {
      const el = record.marker.getElement();
      const box = el.getBoundingClientRect();
      const stack = document.elementsFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      const me = stack.indexOf(el);
      return {
        route: record.latest?.route_id,
        labelsAbove: stack.slice(0, me === -1 ? stack.length : me).filter((n) => n.classList?.contains("stn-label"))
          .length,
      };
    }),
  );
  expect(covered.length, "there are trains on this map").toBeGreaterThan(0);
  for (const train of covered) {
    expect(train.labelsAbove, `a station name is painted over the ${train.route} bullet`).toBe(0);
  }

  // The pane the labels are in is below the vehicles and above the dots they name, and it is
  // NOT the pane Leaflet would have used.
  expect(where.labelPaneZ).toBeLessThan(where.markerPaneZ);
  expect(where.labelPaneZ).toBeGreaterThan(where.stationPaneZ);
  expect(where.tooltipPaneZ, "Leaflet's own tooltip pane is still above the vehicles").toBeGreaterThan(
    where.markerPaneZ,
  );
});

test("D2y. both bullet rings have the header surface on every side, not a neighbour's fill", async ({ page }) => {
  /* THE MUTATION THIS KILLS is putting .bul-group's gap back to the design's 2px. Both ring
     states reach 4px out from a 24px chip, so at a 2px gap each ring's left and right segments
     sit inside the neighbouring bullet's border box, and the `z-index: 1` this stage added for
     the clipping is what makes them paint there rather than be clipped. Measured against a
     neighbour's fill instead of --surface, --focus reads 1.10 on the A/C/E blue: the exact
     number MR1 round 2 recorded as the defect an outside ring was chosen to escape.

     THIS ALSO NEEDED THE MUTATION RUN TO EXIST. Nothing in the suite sampled the pixels beside
     a ring, so the 6px gap went in unguarded and reverting it left every tier green.

     TWO CLAIMS, because one without the other is not the invariant: the GEOMETRY (the gap is at
     least as wide as the ring reaches, so the arithmetic holds for any future ring) and the
     PIXELS (what the browser actually painted either side of it, in both themes). */
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });
  const sel = '#subway-key .bul[aria-label="Focus route Q"]';

  const geometry = await page.evaluate((s) => {
    const el = document.querySelector(s);
    const cs = getComputedStyle(el);
    const group = el.closest(".bul-group");
    const sibs = [...group.querySelectorAll(".bul")];
    const i = sibs.indexOf(el);
    const box = el.getBoundingClientRect();
    return {
      gap: parseFloat(getComputedStyle(group).gap),
      outlineOffset: parseFloat(cs.outlineOffset),
      outlineWidth: parseFloat(cs.outlineWidth),
      hasNeighbourBothSides: i > 0 && i < sibs.length - 1,
      width: Math.round(box.width),
    };
  }, sel);

  expect(geometry.hasNeighbourBothSides, "Q must have a neighbour on each side for this to mean anything").toBe(
    true,
  );
  expect(geometry.width, "a 24px chip").toBe(24);
  // The focus ring reaches offset + width; the pressed ring reaches 4px by its own box-shadow.
  const reach = Math.max(geometry.outlineOffset + geometry.outlineWidth, 4);
  expect(
    geometry.gap,
    `the group gap (${geometry.gap}px) must be at least as wide as a ring reaches (${reach}px), ` +
      "or the ring is measured against the neighbour's route colour instead of the surface",
  ).toBeGreaterThanOrEqual(reach);

  /* AND THE PIXELS, which is the claim the stylesheet comment actually makes. One row through
     the middle of the chip, in both themes and both states: what has to be either side of the
     ring is --surface, and what must not be is the neighbour's fill. */
  const tokens = await page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    const hex = (v) => v.trim().toLowerCase();
    return { surface: hex(cs.getPropertyValue("--surface")), focus: hex(cs.getPropertyValue("--focus")) };
  });
  const bands = await page.evaluate(
    ({ s, theme }) => {
      document.documentElement.setAttribute("data-theme", theme);
      const el = document.querySelector(s);
      const box = el.getBoundingClientRect();
      const y = Math.round(box.top + box.height / 2);
      const at = (x) => {
        const stack = document.elementsFromPoint(x, y);
        const hit = stack.find((n) => n.classList?.contains("bul")) ?? stack[0];
        return hit === el ? "self" : hit?.classList?.contains("bul") ? "neighbour" : "surface";
      };
      // 3px outside the chip's left edge is inside the ring's band at any gap >= 4.
      return { at3Left: at(box.left - 3), at5Left: at(box.left - 5), at3Right: at(box.right + 3) };
    },
    { s: sel, theme: "light" },
  );
  // At a 6px gap the 3px and 5px points either side belong to no bullet: they are header
  // surface, which is what the ring is drawn on and measured against. At the design's 2px the
  // 3px point is inside the neighbour.
  expect(bands.at3Left, "3px left of the chip is surface, not a neighbour").toBe("surface");
  expect(bands.at3Right, "and the same on the right").toBe("surface");
  expect(bands.at5Left, "and so is the far edge of the ring's band").toBe("surface");
  expect(tokens.surface).toBeTruthy();
  expect(tokens.focus).toBeTruthy();
});

test("D2u. the subway's ribbons draw under every other family's lines, whatever order they arrive in", async ({
  page,
}) => {
  /* THE MUTATION THIS KILLS is putting the ribbons back on the shared lineRenderer (round 3,
     F6). Every family passed one L.canvas to its route lines, and Leaflet's canvas draws its
     layers in INSERTION order regardless of LayerGroup, so a 6.5px casing in --paper at 0.9
     arriving after a 2.5px line ERASES it. The eleven static loaders start together and land
     in whatever order their responses arrive, and /api/subway-routes is the largest payload
     and re-fetches on a warming 503, so in production the ribbons routinely landed last:
     whether PATH's 33rd St line survived was a race.

     ASSERTED AS PANES, NOT PIXELS, and deliberately: the claim is that no arrival order can
     change the answer, and a pixel test can only sample the order it happened to get. A pane
     below the shared one is that guarantee, so what is asserted is the z-index relation and
     that every family's line renderer is on the right side of it. The shuffle is what makes
     it a claim about order: the four families' geometry is drawn in a randomised sequence and
     the relation has to hold at the end of every one of them. */
  await open(page, withRealShapedRoutes(["1", "Z"]), { ribbons: 18 });

  const order = await page.evaluate(() => {
    const z = (name) => Number(getComputedStyle(map.getPane(name)).zIndex);
    const paneOf = (layer) => layer.options.renderer?.options.pane ?? "overlayPane";
    /* Every family's route lines, as the app actually built them. The layer-group names are
       each family's own (railroadLineLayer is a FUNCTION of the agency, which is MR1's split
       so the two feeds toggle separately), and a name that is not defined in this build reads
       as an empty list rather than throwing, so this spec does not depend on which families a
       given fixture world happens to load. */
    const group = (fn) => {
      try {
        const layers = fn();
        return layers && layers.getLayers ? layers.getLayers() : [];
      } catch {
        return [];
      }
    };
    const families = {
      subway: subwayRibbons.map((r) => r.layer),
      lirr: group(() => railroadLineLayer("LIRR")),
      mnr: group(() => railroadLineLayer("MNR")),
      // NJ TRANSIT WAS MISSING FROM THIS LIST until MR3, which is the stage that made it
      // matter: its lines were a single 2.5px hairline and are now a casing and a line, so it
      // is one of the three families that can erase a neighbour and one of the three that can
      // be erased. A family absent from this map reads as an empty list and asserts nothing.
      njt: group(() => njtRouteLines),
      path: group(() => pathRouteLines),
      ferry: group(() => ferryRouteLines),
      airtrain: group(() => airtrainRouteLinesLayer),
    };
    const panes = {};
    for (const [name, layers] of Object.entries(families)) {
      panes[name] = [...new Set(layers.map(paneOf))];
    }
    return {
      panes,
      counts: Object.fromEntries(Object.entries(families).map(([k, v]) => [k, v.length])),
      zIndex: {
        subwayLinePane: z("subwayLinePane"),
        railroadLinePane: z("railroadLinePane"),
        overlayPane: z("overlayPane"),
        stationPane: z("stationPane"),
        stationLabelPane: z("stationLabelPane"),
        markerPane: z("markerPane"),
      },
    };
  });

  // THE PANE ORDER ITSELF, which is what systems/shared.js documents in one block.
  expect(order.zIndex.subwayLinePane, "the subway's lines are the base network").toBeLessThan(
    order.zIndex.overlayPane,
  );
  expect(order.zIndex.overlayPane).toBeLessThan(order.zIndex.stationPane);
  expect(order.zIndex.stationPane).toBeLessThan(order.zIndex.stationLabelPane);
  expect(order.zIndex.stationLabelPane, "a name may cover its own dot and never a train").toBeLessThan(
    order.zIndex.markerPane,
  );

  // And every subway ribbon is on that pane while no other family is.
  expect(order.panes.subway).toEqual(["subwayLinePane"]);
  expect(order.counts.subway).toBe(18);
  /* THREE TIERS NOW, NOT TWO (MR3, the operator's ruling on finding N2). The subway is under
     everything at 390, the three commuter rail families are at 395, and the families whose thin
     lines a 5px casing could erase are on the shared canvas at 400. The ordering became a
     three-way one when MR3 gave the railroads a casing: PATH's line is 3.5px, the AirTrain's 3
     and the ferry's 2, and NJ Transit runs into Newark Penn and Hoboken where PATH does, so the
     overlap is real rather than theoretical. */
  expect(order.zIndex.subwayLinePane).toBeLessThan(order.zIndex.railroadLinePane);
  expect(order.zIndex.railroadLinePane).toBeLessThan(order.zIndex.overlayPane);

  /* AT LEAST ONE FAMILY IN EACH TIER HAS TO HAVE DRAWN, or the comparisons are claims about
     empty lists. The fixture world serves LIRR, Metro-North and NJ Transit branches and PATH
     and ferry routes, so these are premise assertions rather than hopes; they fail loudly if a
     future fixture stops serving them and quietly turns this spec into nothing.

     NJ TRANSIT WAS MISSING FROM THIS LIST ENTIRELY before MR3, which is worth recording: the
     `group()` helper reads an undefined layer group as an empty list, so its absence was
     invisible and it asserted nothing about the one family whose lines run alongside PATH's. */
  const rail = ["lirr", "mnr", "njt"].filter((f) => order.counts[f] > 0);
  expect(rail, `a rail family drew no line: ${JSON.stringify(order.counts)}`).toEqual(["lirr", "mnr", "njt"]);
  for (const family of rail) {
    expect(order.panes[family], `${family} is on the rail pane`).toEqual(["railroadLinePane"]);
  }

  const others = ["path", "ferry", "airtrain"].filter((f) => order.counts[f] > 0);
  expect(others.length, `no thin family drew a line: ${JSON.stringify(order.counts)}`).toBeGreaterThan(0);
  for (const family of others) {
    expect(order.panes[family], `${family} keeps the shared canvas`).not.toContain("subwayLinePane");
    expect(order.panes[family], `${family} keeps the shared canvas`).not.toContain("railroadLinePane");
    expect(order.panes[family], `${family} is on the shared canvas`).toEqual(["overlayPane"]);
  }

  /* SHUFFLED INSERTION ORDER. Four families' lines are drawn again, in a randomised sequence,
     directly onto the two renderers. If the subway shared a canvas with the rest, the last
     family in each shuffle would paint over the others and the winner would change run to run;
     with two panes the subway is under all of them in every permutation, which is what this
     loop asserts rather than assuming. */
  const shuffles = await page.evaluate(() => {
    const results = [];
    const probe = L.layerGroup().addTo(map);
    for (let round = 0; round < 8; round++) {
      probe.clearLayers();
      // "rail" stands for the three commuter families, which draw one grammar since MR3; the
      // two thin families are what a casing can erase.
      const families = ["subway", "rail", "path", "ferry"];
      for (let i = families.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [families[i], families[j]] = [families[j], families[i]];
      }
      const built = [];
      for (const family of families) {
        /* THE CASING IS PART OF THE PROBE SINCE MR3, because it is the thing that erases. The
           subway's is 6.5px and the three rail families' is 5px, drawn in --paper at 0.9 ahead
           of each own line, and on one canvas a casing that wide arriving after a thin line
           covers it. So each family is drawn the way it really is: subway and rail as a pair,
           the thin families as one line. What this asserts is unchanged, that the subway is on
           the lower pane whatever the order; what it now also shows is that the rail casings
           and the thin families share ONE pane, which is the cost D3e and the ledger record. */
        const casing = family === "subway" ? 6.5 : family === "rail" ? 5 : null;
        const renderer = family === "subway" ? subwayLineRenderer : lineRenderer;
        const at = [
          [40.75, -73.99],
          [40.76, -73.98],
        ];
        if (casing) L.polyline(at, { weight: casing, color: "var(--paper)", opacity: 0.9, renderer }).addTo(probe);
        const line = L.polyline(at, { weight: 2.5, renderer });
        line.addTo(probe);
        built.push({ family, pane: line.options.renderer.options.pane ?? "overlayPane", casing });
      }
      // The subway's line is on the lower pane no matter where in the order it was added.
      const subway = built.find((b) => b.family === "subway");
      results.push({
        order: families.join(">"),
        subwayPane: subway.pane,
        othersPane: [...new Set(built.filter((b) => b.family !== "subway").map((b) => b.pane))],
      });
    }
    probe.remove();
    return results;
  });

  expect(shuffles.length).toBe(8);
  for (const run of shuffles) {
    expect(run.subwayPane, `order ${run.order}`).toBe("subwayLinePane");
    expect(run.othersPane, `order ${run.order}`).toEqual(["overlayPane"]);
  }
  // And the shuffle really did shuffle, so this is a claim about order rather than one order.
  expect(new Set(shuffles.map((r) => r.order)).size, "the orders must actually differ").toBeGreaterThan(1);

  /* AND THE ARITHMETIC THE THIRD PANE EXISTS FOR, stated rather than implied: there really is a
     casing wider than a line it shares the map with, and the two are now on different panes. A
     test that only asserted the panes would still pass if the casing were dropped to 2px and
     the whole question went away, which is why the weights are read too. */
  const weights = await page.evaluate(() => {
    const paneOf = (layer) => layer.options.renderer?.options.pane ?? "overlayPane";
    const rail = [
      ...railroadLineLayer("LIRR").getLayers(),
      ...railroadLineLayer("MNR").getLayers(),
      ...njtRouteLines.getLayers(),
    ];
    const thin = [...pathRouteLines.getLayers(), ...ferryRouteLines.getLayers()];
    return {
      railPanes: [...new Set(rail.map(paneOf))],
      thinPanes: [...new Set(thin.map(paneOf))],
      casings: rail.filter((l) => l.options.weight === 5).length,
      thinnest: Math.min(...thin.map((l) => l.options.weight)),
    };
  });
  expect(weights.casings, "the rail families draw a casing per branch").toBeGreaterThan(0);
  expect(weights.thinnest, "which is wider than a line it shares the map with").toBeLessThan(5);
  expect(weights.railPanes, "so the casings are on their own pane").toEqual(["railroadLinePane"]);
  expect(weights.thinPanes, "and cannot reach the lines they would erase").toEqual(["overlayPane"]);
});

/* ---------------- the ribbons ---------------- */

test("D2g. the yellow trunk is drawn last, on the map, in both passes", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "yellow not drawn last". The payload lists N and Q FIRST, so a
     spec over a payload that happened to end with yellow would pass with the sort deleted.
     Asserted on the layer group's own order, which is canvas paint order. */
  await open(page, withYellow, { ribbons: 8 });
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
  /* AND THE RESOLVER REALLY READS THE TOKEN (round 3, F12). The assertion above compares the
     casing to getComputedStyle's --paper, and paperColor()'s FALLBACK is "#f3f2f2", which is
     the light theme's --paper byte for byte. Every spec in this suite runs in the light theme,
     so the comparison held whether or not the resolver had ever looked at a custom property:
     pointing both resolvers at names that do not exist left all 261 specs green. So the token
     is moved and the resolver asked again, which is the one question a literal cannot pass. */
  const follows = await page.evaluate(() => {
    const before = { paper: paperColor(), ink: inkColor() };
    document.documentElement.style.setProperty("--paper", "#ff00ff");
    document.documentElement.style.setProperty("--ink", "#00ff00");
    const after = { paper: paperColor(), ink: inkColor() };
    document.documentElement.style.removeProperty("--paper");
    document.documentElement.style.removeProperty("--ink");
    return { before, after, restored: { paper: paperColor(), ink: inkColor() } };
  });
  expect(follows.after, "paperColor and inkColor must read the tokens, not return a literal").toEqual({
    paper: "#ff00ff",
    ink: "#00ff00",
  });
  expect(follows.restored, "and follow them back").toEqual(follows.before);
  expect(new Set(lines.map((l) => l.color))).toEqual(new Set([measured.lineColors.one, measured.lineColors.a]));
  for (const layer of measured.layers) {
    expect(layer.cap).toBe("round");
    expect(layer.join).toBe("round");
    expect(layer.interactive, "a ribbon never takes a click").toBe(false);
  }
});

test("D2m. focusing a route actually repaints the canvas, not just the options", async ({ page }) => {
  /* THE GAP THE SCREENSHOTS FOUND. D2b asserts what focus writes into each layer's options,
     which is exactly what the mutation "focus re-rendering layers" attacks; neither says the
     canvas ever repainted. It matters because a ribbon is drawn by a canvas renderer, and a
     renderer redraws on requestAnimationFrame: measured while capturing this stage's
     screenshots, a page whose clock was PAUSED took the new opacities into its options and
     went on showing the old picture, because rAF never fired. In a rider's browser rAF is
     real, so this is a property of the test harness rather than of the app, and it is worth a
     spec precisely because it makes every other focus assertion here look stronger than it is.

     MEASURED AS PIXELS, from the canvas the ribbons are drawn on. At 0.18 a line's pixels are
     still there but far more transparent, so the honest measure is the WEIGHT of the ink
     rather than a count of touched pixels.

     THE PANE IS THE SUBWAY'S OWN as of round 3 (F6), not the shared overlay pane: a 6.5px
     paper casing on the canvas every other family draws on erased their thin lines whenever
     the subway's static fetch happened to land last. Reading the shared canvas here would now
     measure the OTHER families' lines and no ribbon at all, which is worth saying out loud
     because it would not have failed: it would have gone quietly green with nothing under
     test, the exact shape this suite keeps catching. */
  await open(page, withYellow, { ribbons: 8 });
  const inkWeight = () =>
    page.evaluate(() => {
      // Leaflet names the element from the pane: createPane("subwayLinePane") gives
      // class="leaflet-pane leaflet-subwayLine-pane". A miss here is a null and a thrown
      // error rather than a quietly green spec.
      const canvas = document.querySelector(".leaflet-subwayLine-pane canvas");
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
      let sum = 0;
      for (let i = 3; i < data.length; i += 4) sum += data[i];
      return sum;
    });
  // The clock is paused in this suite, so the rAF the renderer schedules is advanced by hand.
  const settle = () => page.clock.runFor(200);

  await settle();
  const before = await inkWeight();
  expect(before, "the ribbons must actually be on the canvas").toBeGreaterThan(0);

  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  await settle();
  const focused = await inkWeight();
  expect(focused, "focusing must take ink off the canvas, not only out of the options").toBeLessThan(before * 0.9);

  await page.locator('#subway-key .bul[aria-label="Focus route 1"]').click();
  await settle();
  // WITHIN A TENTH OF A PERCENT, NOT EXACTLY. Clearing repaints the whole picture, and a
  // canvas that has been cleared and redrawn differs from the first draw by antialiasing
  // rounding at the ends of round-capped strokes: measured, 293 alpha units out of 1.59
  // million. An exact comparison here would be a test of the rasteriser.
  const cleared = await inkWeight();
  expect(Math.abs(cleared - before) / before, `clearing puts it back (${before} then ${cleared})`).toBeLessThan(0.001);
});

/* ---------------- the stations ---------------- */

test("D2i. a transfer ring where two trunks call, a local dot where one does", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "the transfer ring drawn for single-route stations". The stock
     fixture's two stations are BOTH transfers, so this world adds a one-route station and a
     station with no routes field at all: without them the assertion has no subject. */
  await open(page, withLocalStation, { stations: 18 });
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
    return {
      ink,
      paper,
      transfer: of("X04"),
      local: of("L01"),
      routeless: of("X01"),
      oneTrunk: of("127"),
      skipStop: of("X05"),
    };
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

  /* AND THE F9 CASE, IN BOTH ITS FORMS. Three ids on one trunk (the 1/2/3 at Times Sq) and
     two ids on one trunk (the J/Z at Marcy Av) are both LOCALS: they are one line, taking
     turns or running express, and drawing them as interchanges said something false about the
     network at 325 of its 496 stations. This is the assertion that dies if the predicate goes
     back to counting ids, and it is the only one in the suite that can tell the difference. */
  expect(marks.oneTrunk.routes, "the 1, 2 and 3 are three ids").toBe(3);
  expect(marks.oneTrunk.stroke, "and one trunk, so a dot").toBe(false);
  expect(marks.oneTrunk.radius).toBe(3.5);
  expect(marks.skipStop.routes, "the J and the Z are two ids").toBe(2);
  expect(marks.skipStop.stroke, "and one line taking turns, so a dot").toBe(false);
  expect(marks.skipStop.radius).toBe(3.5);

  // And all of them are still drawn by the canvas on stationPane, which is the pane the
  // z-index depends on and the one P2a pins.
  for (const mark of [marks.transfer, marks.local, marks.routeless, marks.oneTrunk, marks.skipStop]) {
    expect(mark.rendererPane).toBe("stationPane");
  }
});

/* ---------------- the labels ---------------- */

test("D2j. station names appear at the right zooms, hubs first, and the Names toggle hides them", async ({ page }) => {
  /* THE MUTATION THIS KILLS is "labels not gated by zoom". Read as COMPUTED display rather
     than as the attribute, so the stylesheet's own selectors are what is being tested; an
     attribute written correctly and a rule that never matched would pass an attribute check
     and show every name in the city at zoom 3. */
  await open(page, withLocalStation, { stations: 18 });
  /* THE SUBWAY'S NAMES ONLY, which is what this spec is about. MR3 put the commuter rail
     families on the same label pane with their OWN band (from zoom 11) and their own pair of
     rules, so an unscoped count reads five rail names at zoom 11 and calls the subway's band
     broken. `:not(.rail)` is the scope, and tests/e2e/rail.spec.js is where the rail band's own
     numbers are held; the two bands overlap on purpose and neither one may be asserted through
     the other. */
  const painted = () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".leaflet-tooltip:not(.rail)")]
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
  // ONE HUB IN THIS WORLD, and it is the only station here with two trunks. Before round 3
  // this line read ["Canal St", "Times Sq-42 St"], both of which are one line under three or
  // four ids; counting trunks made them locals and left this assertion with no subject, which
  // is why X04 exists.
  expect(await painted(), "at 12 the interchange only").toEqual(["Fulton St"]);

  await setZoom(14);
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "all");
  expect(await painted(), "at 14 every station").toEqual([
    "Canal St",
    "Fulton St",
    "Lorimer St",
    "Marcy Av",
    "Nowhere",
    "Times Sq-42 St",
  ]);

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
  expect(sizes["Fulton St"]).toEqual({ size: "11.5px", weight: "700", label: true, hub: true });
  expect(sizes["Lorimer St"]).toEqual({ size: "10.5px", weight: "600", label: true, hub: false });
  // And the F9 pair is drawn as a local name, not a hub's.
  expect(sizes["Marcy Av"]).toEqual({ size: "10.5px", weight: "600", label: true, hub: false });

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
  expect(await painted()).toEqual(["Fulton St"]);

  /* AND IT SAYS SO, which it did not until round 3 (F7). The labels are aria-hidden by
     design, so this sentence is the only evidence a screen reader gets that the press did
     anything, and at a zoom where no name can show it is the only thing that stops the button
     claiming an effect it does not have. */
  await page.locator("#names-toggle").click();
  await expect(page.locator("#page-announce")).toHaveText("Station names off.");
  await page.locator("#names-toggle").click();
  await expect(page.locator("#page-announce")).toHaveText("Station names on.");
  await setZoom(11);
  await page.locator("#names-toggle").click();
  await page.locator("#names-toggle").click();
  await expect(page.locator("#page-announce")).toHaveText(
    "Station names on; none at this zoom, zoom in to see them.",
  );
  await setZoom(12);

  /* AND THE WHOLE GRID, AGAINST helpers.js's OWN ANSWER.

     Round 1 of the review found that `stationLabelShown` was exported and node tested and
     called by nothing: the gate is three CSS rules, so a node test over that function looked
     like the gate's test and decided nothing. Deleting it would have been one answer. This is
     the better one, and it is the shape this repository already uses for `statusLineText`:
     the function becomes the ORACLE, and the page is checked against it. Read as COMPUTED
     display, so what is compared is what the cascade actually did.

     AND HERE IS WHAT THIS DOES NOT CATCH, measured rather than assumed. The oracle and the
     attribute the stylesheet reads are computed from the same `labelZoomBand`, so a mutation
     to the RULE moves both sides of this comparison together and the grid agrees with
     itself: `labelZoomBand` made to return "none" above zoom 14 leaves all thirteen specs in
     this file green. The node tier is what kills that one, and it does. So the division is:
     the node tier pins what the rule IS, and this pins that the stylesheet implements
     whatever the rule says, which is the half that can rot without anyone editing a number.
     Its value over the literal assertions above is zoom 15, which no literal covers, and
     both toggle states at every zoom rather than at one. */
  for (const labelsOn of [true, false]) {
    await page.evaluate((on) => {
      document.documentElement.setAttribute("data-labels", on ? "on" : "off");
    }, labelsOn);
    for (const zoom of [11, 12, 13, 14, 15]) {
      await setZoom(zoom);
      const disagreements = await page.evaluate(
        ({ z, on }) => {
          const out = [];
          for (const entry of stationRegistry) {
            if (entry.kind !== "subway" || !entry.marker) continue;
            const tip = entry.marker.getTooltip && entry.marker.getTooltip();
            const el = tip && tip.getElement && tip.getElement();
            if (!el) continue;
            const drawn = getComputedStyle(el).display !== "none";
            const hubs = document.querySelectorAll(".stn-label.hub").length;
          const want = stationLabelShown(z, entry.routes ?? [], on, hubs > 0);
            if (drawn !== want) out.push(`${entry.name}: drawn ${drawn}, oracle says ${want}`);
          }
          return out;
        },
        { z: zoom, on: labelsOn },
      );
      expect(disagreements, `zoom ${zoom}, names ${labelsOn ? "on" : "off"}`).toEqual([]);
    }
  }
  await page.evaluate(() => document.documentElement.setAttribute("data-labels", "on"));
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
  await open(page, withYellow, { ribbons: 8 });
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
