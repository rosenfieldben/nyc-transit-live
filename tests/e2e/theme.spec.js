/* MR4: the dark theme's release, and the swap that reaches the canvas.
   ==========================================================================

   Ruling R2, met. The toggle shipped hidden in MR1 and the reason was measured rather than
   cautious: in the dark theme every mark on the map carried a colour chosen for light paper,
   so a rider who chose dark would have got a map that was legal in the chrome and illegal
   everywhere else. MR2, MR3 and MR4 are the stages that gave each family its paper casing or
   stroke; this is the stage that hands the rider the control. Ids are D5, following D4 for the
   families, D3 for the commuter rail, D2 for the subway and D1 for MR1's chrome.

   WHAT IS DELIBERATELY NOT HERE, because it is already somewhere better:
   - that the theme PERSISTS across a reload and survives a browser refusing storage:
     chrome.spec.js D1g and D1h, which have driven this button since MR1
   - the token set itself, and the tile filter: chrome.spec.js D1f
   - axe over the whole page in both themes, at three widths, with a popup open: a11y.spec.js
   - the station labels' ink over any tile in both themes: a11y.spec.js A1z3
   - each rail tag's type on the block it is printed on, in both themes: a11y.spec.js A1z4
   - the registry's coverage asserted against the SOURCE rather than against a list:
     frontend/families.test.js

   WHAT IS HERE is the control, the swap reaching every canvas family without a rebuild, and
   G15's arithmetic re-run on the marks themselves off the drawn page. */
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const { measureMarkContrast, bestPerFamily } = require("./contrast");
const fx = require("./fixtures/api");

const DESKTOP = { width: 1280, height: 720 };

async function open(page) {
  await page.setViewportSize(DESKTOP);
  await installMocks(page);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // Every family drawn before anything is asserted: a swap over a half-loaded map would
  // repaint whatever happened to be there and pass.
  await page.waitForFunction(
    () =>
      trains.size === 2 &&
      buses.size === 2 &&
      railroads.size === 2 &&
      pathTrainRecords.size === 2 &&
      ferryBoatRecords.size === 3 &&
      njtTrainRecords.size === 4 &&
      stationRegistry.length === 14 &&
      subwayRibbons.length > 0 &&
      airtrainRouteLinesLayer.getLayers().length > 0,
    null,
    { timeout: 15_000 },
  );
  await page.clock.runFor(1000);
}

const toggle = (page) => page.locator("#theme-toggle");
const theme = (page) => page.locator("html").getAttribute("data-theme");

/* ---------------- the control ---------------- */

test("D5a. the toggle is in a rider's reach, and its name is the action with no second answer", async ({
  page,
}) => {
  await open(page);
  /* THE ATTRIBUTE IS GONE, which is the whole of ruling R2 and is one line of index.html. It
     is asserted as VISIBILITY rather than as the absence of the attribute, because the rule
     that gives `hidden` its effect on this button is an author rule in style.css (`hidden` has
     to beat the author `display`), and a stage that deleted the attribute while leaving a
     `display: none` elsewhere would pass an attribute check and ship an invisible control. */
  await expect(toggle(page)).toBeVisible();
  await expect(toggle(page)).toBeEnabled();

  /* THE NAME IS THE ACTION, AND NOTHING CLAIMS A STATE TO DISAGREE WITH IT. This is ledger
     finding G7: the button carried aria-pressed as well, so in the dark theme it read "Light"
     and reported pressed, and a screen reader said "Light, pressed", which states that the
     light theme is on while the page is dark. The toggle-button pattern aria-pressed belongs
     to is one whose label does NOT change with the state; this label does.

     SO BOTH HALVES ARE ASSERTED AT BOTH STATES: the accessible name is the thing pressing it
     will DO, and the attribute is absent. THIS IS THE MUTATION the operator named (the toggle
     released with G7's defect reintroduced), and it dies here by name in whichever direction
     it is written: an aria-pressed that says true, one that says false, and a label that
     names the current theme instead of the next one. */
  const speaks = async () => ({
    name: await toggle(page).evaluate((el) => el.textContent.trim()),
    label: await toggle(page).getAttribute("aria-label"),
    pressed: await toggle(page).getAttribute("aria-pressed"),
    theme: await theme(page),
  });

  const light = await speaks();
  expect(light.theme).toBe("light");
  expect(light.name, "in the light theme the press leads to dark").toBe("Dark");
  expect(light.pressed, "a label that moves with the state cannot also claim it").toBeNull();
  expect(light.label, "the name is the text; no aria-label second answer").toBeNull();

  await toggle(page).click();
  const dark = await speaks();
  expect(dark.theme).toBe("dark");
  expect(dark.name, "in the dark theme the press leads back to light").toBe("Light");
  expect(dark.pressed).toBeNull();
  expect(dark.label).toBeNull();
  // THE NAME AND THE PAGE AGREE, which is the sentence G7 is about: the two states' names are
  // different from each other and neither one names the theme the page is currently in.
  expect(dark.name).not.toBe(light.name);
  expect(dark.name.toLowerCase()).not.toBe(dark.theme);
  expect(light.name.toLowerCase()).not.toBe(light.theme);

  // AND IT GOES BACK, so the control is a toggle rather than a one-way door.
  await toggle(page).click();
  expect(await theme(page)).toBe("light");
  await expect(toggle(page)).toHaveText("Dark");
});

/* ---------------- the swap ---------------- */

/* EVERY CANVAS FAMILY'S COLOUR, read off the layers Leaflet hands to the 2D context. A canvas
   mark has no element, so its options ARE its paint: there is no computed style to prefer
   here, and the option is not the model standing in for the page.

   THE RAIL CASINGS ARE FOUND BY THEIR RENDERER, which is the care this whole entry needs: each
   rail family's layer group holds the 5px casing and the 2.5px branch line together, so a
   sweep over the group would also repaint the agency's published colour, and a TEST that read
   the group's first layer might read either one. MR3 put the casings on their own canvas and
   that split is what identifies them. */
const canvasPaints = (page) =>
  page.evaluate(() => {
    const casings = [];
    const casingIds = [];
    const branchLines = [];
    for (const group of [lirrRouteLinesLayer, mnrRouteLinesLayer, njtRouteLines]) {
      for (const layer of group.getLayers()) {
        if (layer.options.renderer === railroadCasingRenderer) {
          casings.push(layer.options.color);
          casingIds.push(layer._leaflet_id);
        } else {
          branchLines.push(layer.options.color);
        }
      }
    }
    const subwayCasings = subwayRibbons.filter((r) => r.part === "casing");
    const subwayLines = subwayRibbons.filter((r) => r.part !== "casing");
    const subwayStations = stationRegistry.filter((e) => e.kind === "subway");
    return {
      subwayCasings: [...new Set(subwayCasings.map((r) => r.layer.options.color))],
      subwayLines: [...new Set(subwayLines.map((r) => r.layer.options.color))],
      subwayStationFill: [...new Set(subwayStations.map((e) => e.marker.options.fillColor))],
      subwayStationRing: [...new Set(subwayStations.map((e) => e.marker.options.color))],
      railCasings: [...new Set(casings)],
      railBranchLines: [...new Set(branchLines)],
      pathStations: [...new Set(pathStations.getLayers().map((l) => l.options.fillColor))],
      pathLines: [...new Set(pathRouteLines.getLayers().map((l) => l.options.color))],
      ferryDockRings: [...new Set(ferryDocks.getLayers().map((l) => l.options.color))],
      ferryDockFills: [...new Set(ferryDocks.getLayers().map((l) => l.options.fillColor))],
      ferryLines: [...new Set(ferryRouteLines.getLayers().map((l) => l.options.color))],
      airtrainLines: [...new Set(airtrainRouteLinesLayer.getLayers().map((l) => l.options.color))],
      busRouteLines: [...new Set(busRouteLayer.getLayers().map((l) => l.options.color))],
      // The arrow's own fill, so the two halves of one route's colour can be compared rather
      // than each asserted against a literal.
      busMarkFill: [...new Set(
        [...document.querySelectorAll(".bus-marker svg path, .bus-marker svg circle")].map(
          (el) => getComputedStyle(el).fill,
        ),
      )],
      registered: canvasThemeFamilies.map((f) => f.name),
      // Every family whose paint threw on the last swap. A rider is told nothing; a test is,
      // which is the whole reason the catch records instead of swallowing.
      failures: canvasThemeFailures.map((f) => `${f.name}: ${f.message}`),
      /* Identity, for the no-rebuild half: a setStyle keeps a layer, a rebuild makes a new one.

         THE RAIL CASING'S ID IS READ RATHER THAN LEFT null, and the round found this one in its
         own test: it was written `railCasing: null`, which is the SAME literal on both sides of
         the comparison, so the one family this test names as the mutation target was the one
         family whose identity was never compared. A painter rewritten to remove each casing
         and add a fresh polyline in its place would have passed every assertion here: the
         colours would read the dark paper because the new layers carry it, the marker probes
         are untouched because a casing has no element, and the popup is a PATH train's. */
      ids: {
        pathStation: pathStations.getLayers()[0]._leaflet_id,
        ferryDock: ferryDocks.getLayers()[0]._leaflet_id,
        airtrainLine: airtrainRouteLinesLayer.getLayers()[0]._leaflet_id,
        subwayCasing: subwayCasings[0].layer._leaflet_id,
        railCasing: casingIds[0] ?? null,
        railCasingCount: casingIds.length,
      },
    };
  });

test("D5b. a swap repaints every canvas family, in place, and rebuilds nothing", async ({ page }) => {
  await open(page);

  /* THE MARKS ARE TAGGED BEFORE THE SWAP, which is how "nothing was rebuilt" is asked of the
     DOM rather than of a count. A count can stay the same across a teardown and a rebuild; a
     data attribute written onto the element cannot survive one. Leaflet's own popups, the
     accessible names labeledMarker writes and the glide's transforms all live on these
     elements, so a rebuild is not a cosmetic difference: it is every open popup closed. */
  const tagged = await page.evaluate(() => {
    const els = [...document.querySelectorAll(".leaflet-marker-icon")];
    els.forEach((el, i) => el.setAttribute("data-swap-probe", String(i)));
    return els.length;
  });
  expect(tagged, "this world draws marks to repaint").toBe(23);

  /* AND A ROUTE LINE A RIDER DREW BY CLICKING A BUS, because the seventh family does not exist
     until they do: `busRouteLayer` is empty on a fresh page, so a swap over it would repaint
     nothing and prove nothing. Round 1 added this family, and this is the world where it has a
     layer to repaint. */
  await page.evaluate(() => showBusRoute([...buses.values()][0].latest));
  await expect
    .poll(() => page.evaluate(() => busRouteLayer.getLayers().length), { timeout: 10_000 })
    .toBeGreaterThan(0);

  // A POPUP A RIDER IS HOLDING OPEN, which is the thing a rebuild would take away.
  await page.evaluate(() => [...pathTrainRecords.values()][0].marker.openPopup());
  await expect(page.locator(".leaflet-popup")).toBeVisible();
  const popupBefore = await page.locator(".leaflet-popup-content").textContent();

  // One route's two halves, read off the drawn page: the line's colour and its own bus's fill.
  const readSameColour = async () =>
    page.evaluate(() => {
      const probe = document.createElement("span");
      probe.style.color = busRouteLayer.getLayers()[0].options.color;
      document.body.append(probe);
      const line = getComputedStyle(probe).color;
      probe.remove();
      const marks = [...document.querySelectorAll(".bus-marker svg path, .bus-marker svg circle")];
      const bus = [...buses.values()][0];
      const mine = marks.find((el) => el.closest(".leaflet-marker-icon") === bus.marker.getElement());
      return { line, mark: mine ? getComputedStyle(mine).fill : null };
    });

  /* THE DRAWN COLOURS BEFORE THE THEME IS TOUCHED AT ALL, which is the only reading that can
     see a wrong DRAW: every press repaints, so a wrong draw is corrected by the first one. */
  const drawnBeforeAnySwap = await readSameColour();
  const before = await canvasPaints(page);
  // THE LIGHT THEME'S TOKENS, so the after has something to differ from.
  expect(before.subwayCasings, "the subway's casings are paper").toEqual(["#f3f2f2"]);
  expect(before.railCasings, "and so are the three rail families'").toEqual(["#f3f2f2"]);
  expect(before.subwayStationFill).toContain("#201e1d");
  expect(before.pathStations).toEqual(["#201e1d"]);
  expect(before.ferryDockRings).toEqual(["#f3f2f2"]);
  expect(before.airtrainLines).toEqual(["#6d6e71"]);
  /* SIX FAMILIES, AND THE COUNT IS ASSERTED so a seventh added later cannot ride along
     unmeasured: families.test.js proves every token-resolving file registers, and this proves
     every registration is exercised by the assertions below. */
  expect(before.registered.length, `registered: ${before.registered.join(", ")}`).toBe(7);
  /* AND THE IDENTITY PROBE HAS A SUBJECT FOR EVERY FAMILY IT NAMES, asserted as a premise
     rather than assumed: a null on both sides of the comparison below would compare nothing,
     which is exactly what this test did for the rail casings until the round that found it. */
  for (const [family, id] of Object.entries(before.ids)) {
    expect(id, `${family} must have an identity to compare`).not.toBeNull();
    expect(typeof id, `${family}'s identity must be a real Leaflet id`).not.toBe("undefined");
  }
  expect(before.ids.railCasingCount, "there are rail casings to keep").toBeGreaterThan(0);

  await toggle(page).click();
  expect(await theme(page)).toBe("dark");
  const after = await canvasPaints(page);

  /* EVERY FAMILY MOVED, one assertion each, named. The dark theme's paper is #201e1d and its
     ink is #f3f2f2: the two swap, which is what makes a mark that reads one of them follow the
     theme rather than invert its meaning. */
  expect(after.subwayCasings, "the subway's casings take the dark paper").toEqual(["#201e1d"]);
  expect(after.railCasings, "THE MUTATION: leave railroad.js out of the registry").toEqual([
    "#201e1d",
  ]);
  expect(after.subwayStationFill, "a local dot is filled with ink").toContain("#f3f2f2");
  expect(after.pathStations, "and a PATH station is the same dot").toEqual(["#f3f2f2"]);
  expect(after.ferryDockRings, "the dock's ring is paper, so a dark map gets no white halo").toEqual([
    "#201e1d",
  ]);
  expect(after.airtrainLines, "the guideway's gray has two values and this is the other").toEqual([
    "#9a9a9a",
  ]);
  /* AND THE CLICKED ROUTE LINE MOVED WITH ITS OWN ARROW, which is the assertion round 1's
     finding is really about: before it, the line was drawn from the raw hashed wheel and the
     mark from the muted one, so a rider clicking a bus got a line in a different colour from
     the thing they clicked, in both themes. The two are compared to EACH OTHER rather than to
     a literal, because "the same colour" is the claim. */
  expect(after.busRouteLines, "the line took the dark theme's lightness").not.toEqual(
    before.busRouteLines,
  );
  const dark = await readSameColour();
  expect(dark.mark, "the bus whose route this is is on the page").not.toBeNull();
  expect(dark.line, "one route, one colour, in the dark theme").toBe(dark.mark);
  /* AND THE SAME BEFORE ANY SWAP, WHICH IS THE HALF THAT CATCHES A WRONG DRAW rather than a
     wrong repaint. The painter runs on every press, so a line DRAWN from the wrong wheel is
     corrected by the first one and a comparison made only afterwards passes: mutation M45 (the
     line back to routeColor's raw hue) survived exactly that, and the only reading that can
     see it is the one taken before the theme is ever touched. `drawnBeforeAnySwap` above is
     that reading. */
  expect(drawnBeforeAnySwap.mark, "the bus is on the page before the swap").not.toBeNull();
  expect(
    drawnBeforeAnySwap.line,
    "one route, one colour, as DRAWN and before any repaint could correct it",
  ).toBe(drawnBeforeAnySwap.mark);
  expect(drawnBeforeAnySwap.line, "and the two themes are not the same colour").not.toBe(dark.line);

  /* AND WHAT MUST NOT MOVE DID NOT. Every colour below is an agency's published one or the
     app's fixed route palette, and a swap that repainted them would erase the identity MR2 and
     MR3 exist to draw. This is the other half of "filtered by renderer": the branch lines sit
     in the same layer groups as the casings. */
  expect(after.railBranchLines, "the agencies' own branch colours").toEqual(before.railBranchLines);
  expect(after.subwayLines, "the subway's trunk palette").toEqual(before.subwayLines);
  expect(after.pathLines, "PATH's feed colours").toEqual(before.pathLines);
  expect(after.ferryLines, "the ferry's feed colours").toEqual(before.ferryLines);
  expect(after.ferryDockFills, "and the dock's own cyan").toEqual(before.ferryDockFills);

  /* NOTHING WAS REBUILT, asked three ways: the canvas layers kept their Leaflet identity, every
     marker element kept the attribute written onto it before the swap, and the popup a rider
     had open is still open with the same content in it. */
  expect(after.ids).toEqual(before.ids);
  /* AND NO FAMILY'S PAINT THREW, which is the half a colour assertion cannot make. The swap
     catches per family so one failure cannot stop the others, and a caught failure used to be
     silent: the page would report the dark theme, the button would read "Light", and one
     family would still be wearing the light theme's colours with nothing anywhere saying so.
     THIS IS THE MUTATION: make any painter throw. */
  expect(after.failures, "no canvas family's paint threw during the swap").toEqual([]);
  const survived = await page.evaluate(() => ({
    icons: document.querySelectorAll(".leaflet-marker-icon").length,
    probed: document.querySelectorAll(".leaflet-marker-icon[data-swap-probe]").length,
  }));
  expect(survived.icons, "the same marks are on the page").toBe(tagged);
  expect(survived.probed, "and they are the same ELEMENTS, not new ones").toBe(tagged);
  await expect(page.locator(".leaflet-popup")).toBeVisible();
  expect(await page.locator(".leaflet-popup-content").textContent()).toBe(popupBefore);
});

test("D5c. the divIcon marks follow the swap through the cascade, with no restyle at all", async ({
  page,
}) => {
  await open(page);
  /* THE OTHER POPULATION, AND IT IS OUT OF THE REGISTRY ON PURPOSE. Every MR2 to MR4 vehicle
     and station mark writes its theme-dependent paint as `var(--paper)` or `var(--ink)` inside
     an inline STYLE, so a swap moves it for free and nothing has to remember it exists. This
     asserts that for real, off the COMPUTED style, which is the only form of the claim worth
     making: what a rider sees is the resolved colour, and a token that failed to resolve (a
     misspelled property, a stylesheet rule that beat the mark's own paint) is still in the
     markup for a markup read to find.

     EACH FAMILY NAMES WHICH PAINT CARRIES WHICH TOKEN, because they are not all the same and a
     loop that assumed one shape would be asserting the wrong half. A PATH diamond, a ferry
     hull and a bus mark are a route colour STROKED in paper; a station square and a rail tag's
     body are FILLED with paper and stroked in INK; the subway's plate is paper with no stroke
     at all. The tokens swap with the theme, so a family whose ink and paper were confused would
     pass in one theme and fail in the other, which is what asserting both ends catches. */
  const PAPER = { light: "rgb(243, 242, 242)", dark: "rgb(32, 30, 29)" };
  const INK = { light: "rgb(32, 30, 29)", dark: "rgb(243, 242, 242)" };
  const FAMILIES = [
    { family: "PATH diamond", selector: ".path-marker svg path", paper: "stroke" },
    { family: "ferry hull", selector: ".ferry-marker svg path", paper: "stroke" },
    { family: "bus mark", selector: ".bus-marker svg path, .bus-marker svg circle", paper: "stroke" },
    { family: "rail station square", selector: ".rail-stn-marker svg rect", paper: "fill", ink: "stroke" },
    { family: "subway plate", selector: ".train-marker svg rect:first-of-type", paper: "fill" },
    /* THE OUTLINED TAGS ONLY, and that is MR3's grammar rather than a convenience. A tag's body
       is OUTLINED (paper fill, ink stroke) for a position the feed did not report and SOLID
       (the agency's own branch colour, no stroke) for one it did, so a solid tag's body carries
       no token and asking it for one would be asking the wrong half. The solid body's colour is
       the agency's and must not move with the theme, which D5b asserts from the other side. */
    {
      family: "rail tag body",
      selector: ".rail-tag-marker svg.rail-tag-outlined rect:first-of-type",
      paper: "fill",
      ink: "stroke",
    },
  ];

  const read = () =>
    page.evaluate(
      (families) =>
        families.map(({ family, selector, paper, ink }) => {
          const els = [...document.querySelectorAll(selector)];
          const values = (which) => [
            ...new Set(els.map((el) => getComputedStyle(el)[which])),
          ];
          return { family, count: els.length, paper: values(paper), ink: ink ? values(ink) : null };
        }),
      FAMILIES,
    );

  for (const mode of ["light", "dark"]) {
    if (mode === "dark") {
      await toggle(page).click();
      expect(await theme(page)).toBe("dark");
    }
    for (const row of await read()) {
      expect(row.count, `${mode}: ${row.family} must be on the page to be measured`).toBeGreaterThan(0);
      expect(row.paper, `${mode}: ${row.family}'s paper paint`).toEqual([PAPER[mode]]);
      if (row.ink) expect(row.ink, `${mode}: ${row.family}'s ink paint`).toEqual([INK[mode]]);
    }
  }

  /* AND THE BUS MARK'S FILL MOVES TOO, which is the one FILL in the app that is a token rather
     than a colour. A bus route's colour is a hash of its id, so its legibility is a claim about
     all 360 hues; 38% clears 3:1 on light paper for every one of them and leaves 188 under on
     dark, and 60% is the mirror, so the lightness is `var(--bus-mark-lightness)` and the hue
     stays the route's. helpers.js carries the measurement and families.test.js runs it. The
     page is in the dark theme here, so this reads the dark end. */
  const darkBuses = await page.evaluate(() =>
    [...new Set(
      [...document.querySelectorAll(".bus-marker svg path, .bus-marker svg circle")].map(
        (el) => getComputedStyle(el).fill,
      ),
    )].sort(),
  );
  expect(darkBuses, "two routes are still two colours").toHaveLength(2);
  await page.evaluate(() => applyTheme("light"));
  const lightBuses = await page.evaluate(() =>
    [...new Set(
      [...document.querySelectorAll(".bus-marker svg path, .bus-marker svg circle")].map(
        (el) => getComputedStyle(el).fill,
      ),
    )].sort(),
  );
  expect(lightBuses, "and the two themes draw them at different lightnesses").not.toEqual(darkBuses);
});

/* ---------------- G15's arithmetic, re-run on the marks ---------------- */

/* RULING Q1a, AS A TABLE: WHICH PAINT CARRIES WHICH FAMILY, AND WHO CHOSE IT.

   The operator's ruling: "Fills stay the agency's published colors; each family's identifying
   paint (letter, stroke, or casing) meets 3:1 on the drawn page in both themes, named per
   family, with P4c as the witness."

   NAMED PER FAMILY IS THE POINT. A floor taken over the best of a mark's paints is a true
   sentence that says nothing about WHICH paint is holding it up, and "which" is exactly what a
   later stage changes without noticing: MR4's own round 1 found P4c recording two families as
   carried by a black no mark paints. So this table names the paint, per family AND per theme,
   and the assertion below reads that paint rather than the maximum.

   AND IT NAMES WHO CHOSE THE COLOUR, which is the other half of the ruling. Where the app
   chooses the paint (an ink outline, an ink fill, the white letter it computes with
   readableInk, the muted bus wheel, the design's dock cyan) the floor is a PROMISE and is
   asserted here. Where the paint is an agency's published route colour the ruling says it
   stays as published, so the floor is not this app's to promise: the value is RECORDED, by
   P4c, and the statement in ACCESSIBILITY.md reports the range rather than claiming it.

   TWO FAMILIES ARE IN THAT SECOND CLASS and both are vehicles whose fill is a feed colour with
   no other paint to carry them: a PATH train and a ferry boat. Measured on this page, the
   ferry's South Brooklyn yellow reads 1.31 against the light paper, and PATH's route 859 blue
   (served, though this world draws only 862) reads 2.76. Their only other paint is the paper
   casing, which is the surface by construction and reads 1.00. Giving them a clearing paint
   means giving them an ink edge inside that casing, which is a change to two marks the stage
   brief draws as "route fill, paper stroke", so it is reported to the operator rather than
   taken here. */
const CARRIED_BY = {
  "subway train": {
    // The route square in the light theme and the white letter printed on it in the dark: the
    // one family whose carrying paint CHANGES with the theme, because neither of its two
    // colours moves and the surface under them does.
    light: { kind: "rect fill", chosen: "published" },
    dark: { kind: "text fill", chosen: "app" },
  },
  "subway station dot": { light: { kind: "fill", chosen: "app" }, dark: { kind: "fill", chosen: "app" } },
  "PATH station dot": { light: { kind: "fill", chosen: "app" }, dark: { kind: "fill", chosen: "app" } },
  "rail station square": {
    light: { kind: "rect stroke", chosen: "app" },
    dark: { kind: "rect stroke", chosen: "app" },
  },
  "AirTrain station square": {
    light: { kind: "rect stroke", chosen: "app" },
    dark: { kind: "rect stroke", chosen: "app" },
  },
  // The tag's ink outline, which every one of its five states carries: an outlined body is
  // stroked in ink and a solid one is FILLED with it.
  "rail tag": { light: { kind: "rect", chosen: "app" }, dark: { kind: "rect", chosen: "app" } },
  "bus": { light: { kind: "fill", chosen: "app" }, dark: { kind: "fill", chosen: "app" } },
  "ferry dock": { light: { kind: "fill", chosen: "app" }, dark: { kind: "fill", chosen: "app" } },
  "PATH train": { light: { kind: "path fill", chosen: "published" }, dark: { kind: "path fill", chosen: "published" } },
  "ferry boat": { light: { kind: "path fill", chosen: "published" }, dark: { kind: "path fill", chosen: "published" } },
};

test("D5d. each family's identifying paint is named, and the app's own clear 3:1 in both themes", async ({
  page,
}) => {
  /* LEDGER FINDING G15, RE-RUN WHERE IT MATTERS, AND NOW IN BOTH THEMES BY RULING Q1a. G15
     measured the KEY PANEL's glyphs in the dark theme and found them between 1.11 and 2.63
     against the surface, because they carry the map's own marker colours and the panel was
     drawn for opaque white. MR1's answer for the panel was `--glyph-plate`, which stays LIGHT
     in both themes, so those glyphs keep their light-theme arithmetic whatever theme a rider
     chooses, and this spec measures the MAP rather than the panel. (Round 2 redrew seven of
     those glyphs, and that changes nothing here: the plate did not move, so their arithmetic
     did not either, and what holds them is a11y.spec.js A1z plus the node tier's
     frontend/keyglyphs.test.js.) The MAP had no such answer, and that is what held the toggle
     back.

     THE DEFINITION IS IN tests/e2e/contrast.js, with the argument for each half of it: the
     surface is the theme's own `--paper` because a tile is an image and no mark clears 3:1
     against every possible pixel, and a paint is only measured if the browser will take it as
     a colour and the shape actually paints it.

     WHAT THIS ASSERTS. For every family, the paint the table above names is present in both
     themes, and where the APP chose that colour it clears 3:1 in both. Where an agency
     published it, the value is read and reported and no floor is claimed, which is ruling Q1a
     in one line. pins.spec.js P4c is the witness for every number. */
  await open(page);

  const measured = {};
  for (const theme of ["light", "dark"]) {
    await page.evaluate((want) => applyTheme(want), theme);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    measured[theme] = await measureMarkContrast(page);
  }
  expect(measured.light.paper, "the light theme's paper").toBe("rgb(243, 242, 242)");
  expect(measured.dark.paper, "the dark theme's paper").toBe("rgb(32, 30, 29)");

  // EVERY FAMILY IN THE TABLE IS ON THE PAGE AND EVERY FAMILY ON THE PAGE IS IN THE TABLE, so a
  // family that stopped drawing and a family that arrived without being named both fail here.
  for (const theme of ["light", "dark"]) {
    expect(
      [...new Set(measured[theme].rows.map((r) => r.family))].sort(),
      `${theme}: the table and the page must name the same families`,
    ).toEqual(Object.keys(CARRIED_BY).sort());
  }

  const reported = [];
  for (const [family, perTheme] of Object.entries(CARRIED_BY)) {
    for (const theme of ["light", "dark"]) {
      const { kind, chosen } = perTheme[theme];
      // Every MARK of the family, because a family is only as findable as its worst mark: the
      // ferry draws three boats and the rail tag five states.
      for (const row of measured[theme].rows.filter((r) => r.family === family)) {
        const named = row.paints.filter((p) => p.kind.includes(kind));
        expect(
          named.length,
          `${theme}: ${family} must still carry a "${kind}" paint, which is what the table says finds it`,
        ).toBeGreaterThan(0);
        // The best of the named kind: a subway train has two rect fills, its paper plate and
        // its route square, and the square is the one a rider finds it by.
        const best = named.reduce((a, b) => (b.onPaper > a.onPaper ? b : a));
        reported.push(`${theme} ${family} ${best.kind} ${best.css} ${best.onPaper.toFixed(2)}`);
        if (chosen !== "app") continue;
        expect(
          best.onPaper,
          `${theme}: ${family} is carried by a paint this app chooses (${best.kind} ${best.css}) ` +
            `at ${best.onPaper.toFixed(2)} against the paper, and the floor for a mark is 3`,
        ).toBeGreaterThanOrEqual(3);
      }
    }
  }
  // A measurement over nothing decides nothing: ten families, two themes, and the rail tag and
  // the ferry draw more than one mark each.
  expect(reported.length, `measured ${reported.length} family-marks`).toBeGreaterThan(24);

  /* AND THE FOUR FAMILIES THIS STAGE DREW, asserted by name, because they are the ones MR4 is
     answerable for. Three of them are carried by a paint this app chose; the ferry's boat is
     the one the ruling exempts, and the exemption is stated here rather than left to a reader
     of the table. */
  const bestOf = (theme, family) => {
    const rows = measured[theme].rows.filter((r) => r.family === family);
    return Math.min(...rows.map((r) => Math.max(...r.paints.map((p) => p.onPaper))));
  };
  for (const theme of ["light", "dark"]) {
    expect(bestOf(theme, "bus"), `${theme}: the bus mark`).toBeGreaterThanOrEqual(3);
    expect(bestOf(theme, "ferry dock"), `${theme}: the ferry dock`).toBeGreaterThanOrEqual(3);
    expect(bestOf(theme, "AirTrain station square"), `${theme}: the AirTrain square`).toBeGreaterThanOrEqual(3);
  }
  /* THE EXEMPTION, MEASURED RATHER THAN ASSUMED. If a future stage gives the ferry's hull an
     ink edge, or the feed stops publishing a yellow, this fails and the statement in
     ACCESSIBILITY.md can stop reporting a range and start promising a floor. */
  expect(
    bestOf("light", "ferry boat"),
    "the ferry boat is carried by the feed's own colour in the light theme (ruling Q1a), and " +
      "NYC Ferry's South Brooklyn yellow is the one under the floor",
  ).toBeLessThan(3);
  expect(bestOf("dark", "ferry boat"), "and it clears in the dark theme").toBeGreaterThanOrEqual(3);
});
