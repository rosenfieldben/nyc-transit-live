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

  // IT IS NOT A TAB STOP AND NOT IN THE ARROW ROTATION, which is the other half of inert:
  // a rider arrowing along the key does not land on a control that will not act.
  expect(dark.tabIndex).toBe(-1);
  await page.locator('#subway-key .bul[aria-label="Focus route SI"]').focus();
  await page.keyboard.press("ArrowRight");
  // SI is the last enabled bullet in this world, so ArrowRight wraps past the dark L to the
  // first one rather than stopping on it.
  expect(await page.evaluate(() => document.activeElement.getAttribute("aria-label"))).toBe("Focus route 1");

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

     MEASURED AS PIXELS, from the overlay canvas the ribbons are drawn on. At 0.18 a line's
     pixels are still there but far more transparent, so the honest measure is the WEIGHT of
     the ink rather than a count of touched pixels. */
  await open(page, withYellow, { ribbons: 8 });
  const inkWeight = () =>
    page.evaluate(() => {
      const canvas = document.querySelector(".leaflet-overlay-pane canvas");
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
            const want = stationLabelShown(z, (entry.routes ?? []).length, on);
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
