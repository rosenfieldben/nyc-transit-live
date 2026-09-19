/* MR4: the other four families' marks, on a real page.
   ==========================================================================

   What stage 4 of the map redesign promises about PATH, the ferry, AirTrain and the buses.
   The pins next door (pins.spec.js P4a and P4b) say what this stage must NOT change; this
   says what it must DO. Ids are D4, following D1 for MR1's chrome, D2 for MR2's subway and
   D3 for MR3's commuter rail.

   WHAT IS DELIBERATELY NOT HERE, because it is already somewhere better:
   - every mark's geometry as arithmetic, one state at a time, including the states no
     fixture world serves: frontend/families.test.js, at the node tier
   - the muted bus hue's measurement over all 360 hues: families.test.js, same reason
   - the theme swap, the toggle's release and the dark theme's contrast: tests/e2e/theme.spec.js
   - that the popups, the panel and the alerts join did not move: pins.spec.js P4a
   - the ferry's docked-and-stale compound as a golden: pins.spec.js P4b2
   - which shapes mean what across every family: rail.spec.js D3c, the census

   WHAT IS HERE is each family's mark and its dimmed state read off the DRAWN PAGE, the ferry
   dock labels' own band, and the two counts this stage widened.

   HOW THESE READ A MARK, because the four defect shapes this phase keeps producing are all
   about that choice. A vehicle is a divIcon, so its paint is read as COMPUTED STYLE: a value
   the browser rejected is still in the markup for a markup read to find and draws nothing,
   and an unresolvable token (`var(--nope)`, a misspelling) falls back to the property's
   initial value, which is black for a fill. A station dot or a route line is a canvas layer with no element
   at all, so its options are read instead, and that is not the model standing in for the
   page: Leaflet hands those option values to the 2D context verbatim, so the option IS the
   paint. Opacity is always `el.style.opacity`, which is where setOpacity writes, and never
   the option that asked for it. */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const DESKTOP = { width: 1280, height: 720 };

// The light theme's three tokens as the browser serves them back, so a paint compared against
// one of these is compared against the theme rather than against a literal in a spec.
const PAPER = "rgb(243, 242, 242)"; // --paper  #f3f2f2
const INK = "rgb(32, 30, 29)"; //     --ink     #201e1d
const SCHEDULED = "#6d6e71"; //       --scheduled, as a canvas option (a string, not computed)

/* The stock fixture world, with every family's marks drawn before anything is asserted.
   The waits are per family and by COUNT rather than "more than zero", because a partially
   loaded page would let a census quietly agree with a shorter list. */
async function open(page, before, { stations = 14 } = {}) {
  await page.setViewportSize(DESKTOP);
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await page.waitForFunction(
    (want) =>
      buses.size === 2 &&
      pathTrainRecords.size === 2 &&
      ferryBoatRecords.size === 3 &&
      pathStations.getLayers().length === 2 &&
      ferryDocks.getLayers().length === 2 &&
      airtrainStationLayer.getLayers().length === 3 &&
      stationRegistry.length === want.stations,
    { stations },
    { timeout: 15_000 },
  );
  await page.clock.runFor(1000);
  return ctx;
}

/* A FEED THAT IS OLD FROM ITS FIRST BYTE, which is P4b2's lesson applied to every family.
   Reaching into a loaded page to age a source and then polling again does not work: the next
   poll answers with the stock envelope and overwrites the edit, so the "stale" half comes
   back identical to the healthy one and the test cannot fail. The world is stale on arrival
   instead: the envelope's own fetched_at sits 200 s behind its served_at, which is past
   FEED_STALE_AFTER_S and inside OBS_MAX_S, so the contract's dimming is the whole answer. */
const stale = (data, key) => ({
  fetched_at: fx.FROZEN_S - 200,
  feed_timestamp: fx.FROZEN_S - 205,
  served_at: fx.FROZEN_S,
  [key]: data,
});

/* Every divIcon mark of one family, as the page draws it.

   THE PAINT IS COMPUTED AND THE GEOMETRY IS MARKUP, on purpose. A fill written into an inline
   style that the browser rejected is still in the markup for a markup read to find and draws
   nothing, so the colours are asked of getComputedStyle; the viewBox and the shape's tag are
   what the builder wrote and there is nothing for the cascade to do to them. The rotation is
   read BOTH ways for the same reason: the written angle says what the code meant and the
   computed matrix says the page did it.

   `drawn` IS el.style.opacity, WHICH IS WHERE setOpacity WRITES, and never the marker option
   that asked for it. One of the four defect shapes this phase keeps producing is believing the
   model over the drawn page. */
const marks = (page, family) =>
  page.evaluate((name) => {
    const store =
      name === "path" ? pathTrainRecords : name === "ferry" ? ferryBoatRecords : buses;
    return [...store.entries()].map(([id, record]) => {
      const el = record.marker.getElement();
      const svg = el.querySelector("svg");
      const shape = svg.querySelector("path, circle, rect");
      const style = getComputedStyle(shape);
      return {
        id,
        tag: shape.tagName.toLowerCase(),
        fill: style.fill,
        stroke: style.stroke,
        box: svg.getAttribute("viewBox"),
        rotation: svg.style.transform || "",
        matrix: getComputedStyle(svg).transform,
        className: el.className,
        drawn: el.style.opacity === "" ? 1 : Number(el.style.opacity),
      };
    });
  }, family);

/* ---------------- PATH ---------------- */

test("D4a. PATH draws the design's line, the subway's local dot and a lifted diamond", async ({ page }) => {
  await open(page);

  /* THE LINES. Weight 3.5 at full opacity with round caps, and NO CASING: PATH is the one
     family the design gives no paper casing, so every layer in the group is a line and none
     of them is on the casing canvas MR3 built. The caps are asserted because the design
     names them; Leaflet's default happens to agree, and a design that is only true by
     accident is one a vendor bump can take away. */
  const lines = await page.evaluate(() =>
    pathRouteLines.getLayers().map((l) => ({
      weight: l.options.weight,
      opacity: l.options.opacity,
      cap: l.options.lineCap,
      join: l.options.lineJoin,
      color: l.options.color,
      interactive: l.options.interactive,
      onCasingCanvas: l.options.renderer === railroadCasingRenderer,
      pane: l.options.renderer.options.pane ?? "overlayPane",
    })),
  );
  expect(lines.length, "the fixture serves PATH route geometry").toBeGreaterThan(0);
  for (const line of lines) {
    expect(line.weight).toBe(3.5);
    expect(line.opacity).toBe(1);
    expect(line.cap).toBe("round");
    expect(line.join).toBe("round");
    expect(line.interactive, "clicks fall through to the station dots").toBe(false);
    expect(line.onCasingCanvas, "PATH has no casing").toBe(false);
  }
  /* THE FEED'S OWN COLOURS, WHICH IS WHY THESE LINES ARE IN NO THEME FAMILY, asserted as the
     whole set against what /api/path-routes served rather than as one literal: this world
     serves two routes in two colours, and a line drawn in the wrong one of them would pass a
     one-colour check on half the map. */
  const served = fx.pathRoutes().map((r) => `#${r.color}`).sort();
  expect([...new Set(lines.map((l) => l.color))].sort()).toEqual([...new Set(served)].sort());

  /* ABOVE THE RAIL PANES, which is the design's word ("thin lines above the rail panes") and
     is a claim about z-order rather than about a number. Read as the two panes' computed
     z-indexes so a renumbering that kept the relationship passes and one that inverted it
     fails, which a literal 400 would get backwards. */
  const order = await page.evaluate(() => {
    const z = (name) => Number(getComputedStyle(map.getPane(name)).zIndex);
    return { path: z(pathRouteLines.getLayers()[0].options.renderer.options.pane ?? "overlayPane"), railLine: z("railroadLinePane"), railCasing: z("railroadCasingPane") };
  });
  expect(order.path).toBeGreaterThan(order.railLine);
  expect(order.railLine).toBeGreaterThan(order.railCasing);

  /* THE STATIONS ARE THE SUBWAY'S LOCAL DOT, and the claim is asserted against a subway
     station drawn on the same page rather than against a copy of its numbers. "The same
     mark" is what the design asks for; two literals that happen to match today is not the
     same sentence. The radius is pinned as well, so a stage that changed both families at
     once still moves a number here. */
  const dots = await page.evaluate(() => {
    const opts = (m) => ({
      radius: m.options.radius,
      fillColor: m.options.fillColor,
      fillOpacity: m.options.fillOpacity,
      color: m.options.color,
      weight: m.options.weight,
      stroke: m.options.stroke,
      pane: m.options.renderer.options.pane,
    });
    const subwayLocal = stationRegistry.find(
      (e) => e.kind === "subway" && e.marker.options.radius === 3.5,
    );
    return {
      path: pathStations.getLayers().map(opts),
      subwayLocal: subwayLocal ? opts(subwayLocal.marker) : null,
      airtrainSquares: airtrainStationLayer.getLayers().length,
    };
  });
  expect(dots.subwayLocal, "this world has a subway local station to compare against").not.toBeNull();
  for (const dot of dots.path) {
    expect(dot, "a PATH station is a subway local dot, option for option").toEqual(dots.subwayLocal);
    expect(dot.radius).toBe(3.5);
    expect(dot.fillColor, "the fill is the ink token, not a literal").toBe("#201e1d");
    expect(dot.stroke, "a local dot has no ring; the transfer ring is the subway's alone").toBe(false);
    expect(dot.pane, "on the station pane, above the lines and below the vehicles").toBe("stationPane");
  }

  /* THE TRAINS. A 16x16 diamond in the route's colour with a paper stroke, lifted above the
     station point so the dot underneath keeps its own clicks. The stroke is the token, read
     COMPUTED rather than off the markup: this is the one paint on this mark a theme change has
     to move, it moves through the cascade because the mark is HTML, and what a rider sees is
     the resolved colour rather than the expression that produced it. */
  const trains = await marks(page, "path");
  expect(trains).toHaveLength(2);
  for (const train of trains) {
    expect(train.tag).toBe("path");
    expect(train.box).toBe("0 0 16 16");
    expect(train.fill).toBe("rgb(217, 58, 48)"); // #d93a30, the feed's route colour
    expect(train.stroke, "the stroke is --paper and the cascade resolved it").toBe(PAPER);
    expect(train.drawn, "a fresh train is drawn at full opacity").toBe(1);
  }
  const anchor = await page.evaluate(() => {
    const opts = [...pathTrainRecords.values()][0].marker.getIcon().options;
    return { size: opts.iconSize, anchor: opts.iconAnchor, popup: opts.popupAnchor };
  });
  expect(anchor.size).toEqual([16, 16]);
  expect(anchor.anchor, "lifted, so the station dot beneath keeps its clicks").toEqual([8, 20]);
  expect(anchor.popup).toEqual([0, -20]);
});

test("D4b. PATH's diamond dims with the contract and nothing else does", async ({ page }) => {
  await open(page, (c) => {
    c.overrides.path = (route) => json(route, stale(fx.path().trains, "trains"));
  });
  const trains = await marks(page, "path");
  expect(trains).toHaveLength(2);
  for (const train of trains) {
    expect(train.drawn, `${train.id} rides a stale feed and must be dim`).toBeCloseTo(0.45, 5);
    // THE DIMMING IS OPACITY AND NOT PAINT, which is what makes it compose with everything
    // else: the diamond is still the route's colour and still stroked in paper.
    expect(train.fill).toBe("rgb(217, 58, 48)");
    expect(train.stroke).toBe(PAPER);
  }
  /* AND THE STATIONS DID NOT DIM WITH THEM. A station is not an observation: it has no age
     to be stale, and a sweep that reached it would fade the whole network's geography on a
     late poll. Read off the canvas layer's own opacity option, which is the only opacity a
     canvas mark has. */
  const dotOpacity = await page.evaluate(() =>
    pathStations.getLayers().map((l) => l.options.fillOpacity),
  );
  expect(dotOpacity).toEqual([1, 1]);
});

/* ---------------- the ferry ---------------- */

test("D4c. the ferry draws dashed routes, named docks and a hull", async ({ page }) => {
  await open(page);

  /* DASHED, WHICH IS THE FERRY'S SIGNATURE ON THIS MAP. A ferry route is not track: the dash
     says the service crosses water on no fixed way, which is the one thing a solid line of
     any colour cannot say. */
  const lines = await page.evaluate(() =>
    ferryRouteLines.getLayers().map((l) => ({
      weight: l.options.weight,
      opacity: l.options.opacity,
      dash: l.options.dashArray,
      color: l.options.color,
    })),
  );
  expect(lines.length).toBeGreaterThan(0);
  for (const line of lines) {
    expect(line.dash, "a ferry route is dashed").toBe("6 5");
    expect(line.weight).toBe(2);
    expect(line.opacity).toBe(0.9);
  }
  // THE FEED'S COLOURS, more than one of them, so "dashed" is not being asserted over a world
  // where every route is the same line.
  expect(new Set(lines.map((l) => l.color)).size).toBeGreaterThan(1);

  /* THE DOCKS. Radius 4 in the design's cyan with a PAPER ring, which is the half that makes
     this mark theme-aware: a white ring is a light halo on a dark map, and paper resolves to
     the dark theme's own surface instead. */
  const docks = await page.evaluate(() =>
    ferryDocks.getLayers().map((l) => ({
      radius: l.options.radius,
      fillColor: l.options.fillColor,
      color: l.options.color,
      weight: l.options.weight,
      stroke: l.options.stroke,
      pane: l.options.renderer.options.pane,
    })),
  );
  expect(docks).toHaveLength(2);
  for (const dock of docks) {
    expect(dock.radius).toBe(4);
    expect(dock.fillColor, "the design's cyan, which the feed strip has drawn since MR1").toBe("#00839c");
    expect(dock.color, "the ring is the paper token").toBe("#f3f2f2");
    expect(dock.weight).toBe(1.5);
    expect(dock.stroke).toBe(true);
    expect(dock.pane).toBe("stationPane");
  }

  /* THE BOATS ARE HULLS. The file argued for a boat-shaped boat for years and drew a rounded
     rectangle; the trapezoid is that argument carried out. Asserted as the drawn tag rather
     than as markup, and the rect is asserted ABSENT, because "there is a path" would also be
     true of a page that drew both. */
  const boats = await marks(page, "ferry");
  expect(boats).toHaveLength(3);
  for (const boat of boats) {
    expect(boat.tag).toBe("path");
    expect(boat.box).toBe("0 0 22 14");
    expect(boat.stroke).toBe(PAPER);
  }
  expect(
    await page.evaluate(() =>
      [...ferryBoatRecords.values()].some((r) => r.marker.getElement().querySelector("rect")),
    ),
    "no boat is a rectangle any more",
  ).toBe(false);

  /* THE DOCKED RULE ALONE, in a healthy world, which is the half pins.spec.js P4b2 cannot
     show: there every boat is dimmed by the feed as well, so 0.55 only appears multiplied.
     Here the docked boat carries the docked rule and nothing else, and the under-way boats
     carry nothing, so the two rules are visibly separate before they are multiplied. */
  const docked = boats.filter((b) => b.className.includes("ferry-docked"));
  const underWay = boats.filter((b) => !b.className.includes("ferry-docked"));
  expect(docked, "the fixture serves a STOPPED_AT boat").toHaveLength(1);
  expect(docked[0].drawn).toBeCloseTo(0.55, 5);
  for (const boat of underWay) expect(boat.drawn).toBe(1);
});

test("D4d. a dock's name rides the label pane, the Names toggle, and no subway count", async ({ page }) => {
  await open(page);
  /* THE DESIGN ASKS FOR DOCK NAMES and no dock has ever had one. They join the pane the
     subway's and the rail's names are on, with their OWN class, which is the whole reason
     this stage stopped counting `.stn-label` in the zoom band: a dock is not `.rail`, so
     every `:not(.rail)` sentinel MR3 wrote counted a dock as a subway station. */
  const counts = () =>
    page.evaluate(() => ({
      ferry: [...document.querySelectorAll(".stn-label.ferry")]
        .filter((el) => getComputedStyle(el).display !== "none")
        .map((el) => el.textContent)
        .sort(),
      subway: document.querySelectorAll(".stn-label.subway").length,
      ferryTotal: document.querySelectorAll(".stn-label.ferry").length,
    }));
  const at = async (zoom) => {
    await page.evaluate((z) => map.setZoom(z, { animate: false }), zoom);
    await expect(page.locator("html")).toHaveAttribute("data-zoom", String(zoom));
    return counts();
  };

  // BOTH DOCKS HAVE A NAME BOUND at every zoom; what the band decides is whether it is drawn.
  expect((await counts()).ferryTotal).toBe(2);
  expect((await at(12)).ferry, "below the all band a dock's name is not drawn").toEqual([]);
  expect((await at(13)).ferry, "nor at 13, which is the design's 'names from 14'").toEqual([]);
  expect((await at(14)).ferry, "at 14 every name is drawn").toEqual([
    "South Williamsburg",
    "Wall St/Pier 11",
  ]);
  /* AND THE BAND IS THE FERRY'S OWN, which round 1's review is the reason for. The docks first
     hung on `data-label-band`, the subway's attribute, which carries the subway's DEGRADED
     answer as well as its ordinary one: with no subway station listing a route, that band
     reads "all" from 13 and every dock name came on a zoom early for a reason that has
     nothing to do with the ferry. THIS IS THE MUTATION: point the CSS rule back at
     `data-label-band`. */
  await expect(page.locator("html")).toHaveAttribute("data-ferry-label-band", "all");
  await page.evaluate(() => map.setZoom(13, { animate: false }));
  await expect(page.locator("html")).toHaveAttribute("data-ferry-label-band", "none");
  await page.evaluate(() => map.setZoom(14, { animate: false }));

  // THE NAMES TOGGLE IS A PREFERENCE OVER EVERY BAND, the ferry's included.
  await page.locator("#names-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-labels", "off");
  expect((await counts()).ferry, "the toggle hides dock names too").toEqual([]);
  await page.locator("#names-toggle").click();
  expect((await counts()).ferry).toHaveLength(2);

  /* AND A DOCK IS NOT A SUBWAY STATION TO ANY COUNT, which is this stage's carry-forward
     stated as an assertion rather than as a comment. Two subway labels in this world, and
     they stay two with two dock labels on the same pane. */
  expect((await counts()).subway).toBe(2);
});

/* ---------------- AirTrain ---------------- */

test("D4e. AirTrain draws a gray dashed guideway and the commuter square", async ({ page }) => {
  await open(page);

  /* THE GUIDEWAY. Gray, dashed, weight 3, and the gray is the `--scheduled` token rather than
     a constant: it has two values (light and dark), which style.css has carried since MR1
     with no canvas able to read either. The magenta it replaces could not move with a theme.
     This line IS in the theme registry, unlike PATH's and the ferry's, because its colour is
     the app's rather than a feed's. */
  const lines = await page.evaluate(() =>
    airtrainRouteLinesLayer.getLayers().map((l) => ({
      color: l.options.color,
      weight: l.options.weight,
      dash: l.options.dashArray,
      opacity: l.options.opacity,
    })),
  );
  expect(lines.length).toBeGreaterThan(0);
  for (const line of lines) {
    expect(line.color, "the scheduled gray, resolved at draw time").toBe(SCHEDULED);
    expect(line.weight).toBe(3);
    expect(line.dash).toBe("8 5");
  }
  expect(
    lines.some((l) => /magenta|#e|#f0f/i.test(String(l.color))),
    "the magenta is gone",
  ).toBe(false);

  /* THE STATIONS ARE THE COMMUTER SQUARE, byte for byte the same markup the three rail
     families draw, which is what "the fourth family to draw it" means. Compared against a
     rail station on the same page rather than against a copy of the square's markup. */
  const squares = await page.evaluate(() => {
    const html = (m) => m.getIcon().options.html;
    const cls = (m) => m.getIcon().options.className;
    const air = airtrainStationLayer.getLayers();
    const rail = stationRegistry.find((e) => e.kind === "railroad");
    return {
      airHtml: [...new Set(air.map(html))],
      airClass: [...new Set(air.map(cls))],
      railHtml: rail ? html(rail.marker) : null,
      railClass: rail ? cls(rail.marker) : null,
      count: air.length,
    };
  });
  expect(squares.count).toBe(3);
  expect(squares.airHtml, "one square, drawn by one builder").toHaveLength(1);
  expect(squares.airHtml[0], "the same square the railroads draw").toBe(squares.railHtml);
  /* AND IT JOINS `rail-stn-marker`, WHICH IS A COUNT AND NOT A COSMETIC. Six specs count
     `.leaflet-marker-icon:not(.rail-stn-marker)` as "the vehicles"; an AirTrain station has
     never been a vehicle and was counted as one by every one of them. The system's own class
     rides alongside so a family can still be asked for by name. */
  expect(squares.airClass).toEqual(["rail-stn-marker rail-airtrain-stn"]);
  expect(squares.railClass).toMatch(/^rail-stn-marker /);
  const sentinels = await page.evaluate(() => {
    const vehicles = [...document.querySelectorAll(".leaflet-marker-icon:not(.rail-stn-marker)")];
    return {
      vehicles: vehicles.length,
      // WHAT IS IN THAT BUCKET, not only how many: a family that joined the marker pane
      // without joining rail-stn-marker while not being a vehicle would inflate the count
      // again, and a number alone would not say which family did it.
      vehicleClasses: [
        ...new Set(
          vehicles.map(
            (el) =>
              [...el.classList]
                .filter((c) => c !== "leaflet-marker-icon" && c !== "leaflet-zoom-animated" && c !== "leaflet-interactive")
                .sort()
                .join(" "),
          ),
        ),
      ].sort(),
      stations: document.querySelectorAll(".rail-stn-marker").length,
      oldClass: document.querySelectorAll(".airtrain-marker").length,
    };
  });
  expect(sentinels.oldClass, "the old airtrain-marker class is retired").toBe(0);
  expect(sentinels.stations, "five rail stations and three AirTrain squares").toBe(8);
  /* AND THE SENTINEL SIX SPECS SHARE IS ASSERTED, which the first draft of this test computed
     and then dropped on the floor: a value read and never asked about is the smallest version
     of a test that cannot fail, in the spec written to close exactly this carry-forward. The
     bucket is 15 in this world and every class in it is a vehicle's. */
  expect(sentinels.vehicles, "AirTrain's three stations left the vehicle bucket").toBe(15);
  expect(sentinels.vehicleClasses).toEqual([
    "bus-marker",
    "ferry-active ferry-marker",
    "ferry-docked ferry-marker",
    "path-marker",
    "rail-lirr rail-tag-marker rail-tag-outlined",
    "rail-mnr rail-tag-marker rail-tag-solid",
    "rail-njt rail-tag-marker rail-tag-outlined",
    "train-marker",
  ]);

  /* AIRTRAIN HAS NO DIMMED STATE AND THAT IS THE POINT OF THE FAMILY. It serves no realtime
     feed at all, so there is no observation to age and nothing to dim; its popups say so.
     Asserted rather than left out, because "no test" and "no state" look the same later. */
  expect(
    await page.evaluate(() =>
      airtrainStationLayer.getLayers().every((l) => (l.getElement()?.style.opacity ?? "") === ""),
    ),
    "a scheduled family is never dimmed",
  ).toBe(true);
});

/* ---------------- the buses ---------------- */

test("D4f. a bus is an arrow when a heading is served and a dot when one is not", async ({ page }) => {
  await open(page);
  const rows = await marks(page, "bus");
  expect(rows).toHaveLength(2);
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));

  /* THE FIXTURE SERVES BOTH STATES, which is what makes this a test rather than a slogan:
     M15 carries bearing 90 and B46 carries null. */
  const arrow = byId["MTA NYCT_101"];
  const dot = byId["MTA NYCT_102"];
  expect(arrow.tag, "a served heading draws the arrow").toBe("path");
  expect(arrow.rotation, "rotated to the bearing the feed served").toBe("rotate(90deg)");
  expect(dot.tag, "no heading draws the dot").toBe("circle");
  expect(dot.rotation, "a dot has no direction to be rotated to").toBe("");
  /* AND THE PAGE ACTUALLY TURNED IT. The angle above is what the builder wrote; this is the
     matrix the browser resolved it to, which is the difference between a transform that
     applies and one a stylesheet quietly overrode. rotate(90deg) is that matrix exactly. */
  expect(arrow.matrix).toBe("matrix(0, 1, -1, 0, 0, 0)");
  expect(dot.matrix, "nothing is transformed for a dot").toBe("none");
  // ONE BOX FOR BOTH, so a bus gaining or losing a heading does not move under the pointer.
  expect(arrow.box).toBe("0 0 14 14");
  expect(dot.box).toBe("0 0 14 14");
  for (const row of [arrow, dot]) expect(row.stroke, "stroked in paper").toBe(PAPER);

  /* THE MUTED HUE, ON THE PAGE, AND ITS LIGHTNESS IS A TOKEN. The route's hue is kept (two
     buses on one route are one colour and two routes are two), the saturation is the design's
     45%, and the LIGHTNESS is `var(--bus-mark-lightness)` because a bus route's colour is a
     hash of its id and the answer differs per theme: 38% clears 3:1 for all 360 hues on light
     paper and leaves 188 of them under on dark, and 60% is the mirror. helpers.js carries the
     measurement and frontend/families.test.js runs it; what this asserts is that the page
     draws the token form and that the light theme resolves it to the README's own value. */
  const hues = await page.evaluate(() => ({
    mark: busMarkColor("M15"),
    route: routeColor("M15"),
    lightEnd: busMarkColorAt("M15", 38),
    // The dot's bus is B46, a different route and so a different hue: comparing it against
    // M15's colour would be comparing two routes and calling the difference a defect.
    dotLightEnd: busMarkColorAt("B46", 38),
  }));
  expect(hues.mark).toBe("hsl(329, 45%, var(--bus-mark-lightness, 38%))");
  expect(hues.mark, "muted, not the raw hashed hue").not.toBe(hues.route);
  // THE HUE IS THE SAME HUE, so a rider who learned a route's colour from its popup still
  // recognises its arrow. routeColor is untouched and still paints the popup and the route line.
  expect(/^hsl\((\d+),/.exec(hues.mark)[1]).toBe(/^hsl\((\d+),/.exec(hues.route)[1]);
  /* AND THE DRAWN FILL IS THAT COLOUR RESOLVED, compared against the LIGHT END's literal
     rather than against the token expression: if the custom property failed to resolve, the
     fill would come back as the var's fallback or as nothing at all, and comparing the token
     against itself could not tell. The probe is a throwaway span, so the comparison is the
     browser's own parse of the same colour rather than a hex written in this file. */
  const resolved = async (css) =>
    page.evaluate((value) => {
      const probe = document.createElement("span");
      probe.style.color = value;
      document.body.append(probe);
      const out = getComputedStyle(probe).color;
      probe.remove();
      return out;
    }, css);
  expect(arrow.fill, "the drawn arrow is the token resolved by the light theme").toBe(
    await resolved(hues.lightEnd),
  );
  expect(dot.fill, "and so is the dot, in its own route's hue").toBe(
    await resolved(hues.dotLightEnd),
  );
  expect(arrow.fill, "two routes are two colours").not.toBe(dot.fill);
  for (const row of rows) expect(row.drawn, "a fresh bus is bright").toBe(1);
});

test("D4g. a bus on a stale feed dims, and never trades its glyph for the other one", async ({ page }) => {
  await open(page, (c) => {
    c.overrides.buses = (route) => json(route, stale(fx.buses().data, "data"));
  });
  const rows = await marks(page, "bus");
  expect(rows).toHaveLength(2);
  for (const row of rows) {
    expect(row.drawn, `${row.id} rides a stale feed and must be dim`).toBeCloseTo(0.45, 5);
  }
  /* AND THE GLYPHS ARE STILL THE ONES THE PROVENANCE ASKS FOR. Dimming is about age and the
     glyph is about whether a direction was served; a sweep that rebuilt the icon while
     dimming could quietly hand the headingless bus an arrow, which is a direction invented
     out of a missing field. The two rules are orthogonal and this is where they meet. */
  const byId = Object.fromEntries(rows.map((r) => [r.id, r]));
  expect(byId["MTA NYCT_101"].tag).toBe("path");
  expect(byId["MTA NYCT_102"].tag).toBe("circle");
});
