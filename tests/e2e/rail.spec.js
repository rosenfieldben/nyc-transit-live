/* MR3: the commuter rail restyle's own claims.
   ==========================================================================

   What stage 3 of the map redesign promises that no other spec in this suite was written to
   check. The pins next door (pins.spec.js P3a through P3d) say what MR3 must NOT change; this
   says what it must DO. Ids are D3, following D1 for MR1's chrome and D2 for MR2's subway.

   WHAT IS DELIBERATELY NOT HERE, because it is already somewhere better:
   - the state table as arithmetic, one case per row, including the two rows no fixture world
     can reach: frontend/railtag.test.js, at the node tier, where it can be asked directly
   - the tag's type legibility on the block under it, in both themes: a11y.spec.js A1z4
   - the branch code table, the tag's width arithmetic and the bearing's sign: railtag.test.js
   - that the rail POPUPS, the panel and the alerts join did not move: pins.spec.js P3a to P3c
   - that the position ladder's five states kept their counts and their words: P3d
   - that the F01 world's 136 markers each draw the body and head their provenance asks for,
     at scale: smoke.spec.js C2j, which is the acceptance world and now reads both
   - that the rail marks meet the 24px floor without being inflated: layout.spec.js A4b

   WHAT IS HERE is the seven rows on a real page, found the way a rider's screen reader finds
   them; the two shapes never being confused; and the rail label band, which is MR3's own
   number and overlaps the subway's. */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const DESKTOP = { width: 1280, height: 720 };

/* ONE TRAIN PER ROW OF THE BRIEF'S 3.1 TABLE, on one page.

   Six of the seven are reachable from a single served payload; the seventh, `retained`, needs
   two polls, because a retained row is drawn as it was drawn BEFORE retention and the page has
   to have seen the before. So this world carries six and D3b drives the seventh.

   EVERY ROW IS NAMED IN ITS trip_id, which is how the assertions find it: the accessible name
   carries the branch and the position words, and the trip id is what railroads is keyed by, so
   a row that landed in the wrong state fails by name rather than by index. */
const ROWS = [
  // Row 1: reported, unqualified. A fix 5s old on an age-gated railroad.
  { row: "reported-unqualified", system: "LIRR", trip_id: "row1-reported-fresh", observed_at: -5, provenance: "reported" },
  // Row 2: reported, qualified. The same fix at 300s, past OBS_FRESH_S and inside OBS_MAX_S.
  { row: "reported-qualified", system: "LIRR", trip_id: "row2-reported-aged", observed_at: -300, provenance: "reported" },
  // Row 3: estimated. THE ROW THE TABLE EXISTS FOR: an inferred position and a real heading.
  { row: "estimated", system: "LIRR", trip_id: "row3-estimated", observed_at: -5, provenance: "estimated" },
  // Row 4: placed.
  { row: "placed", system: "LIRR", trip_id: "row4-placed", observed_at: -5, provenance: "placed" },
  // Row 6: unknown, age-gated. A `reported` fix with NO clock on a railroad that dates every
  // one of them, which the freshness contract calls an anomaly and says out loud.
  { row: "unknown", system: "LIRR", trip_id: "row6-unknown-gated", observed_at: null, provenance: "reported" },
  // Row 7: unknown, Metro-North. The SAME served row as row 6, on the one system whose
  // positions are not age-gated at all, so it is row 1's answer by policy.
  { row: "mnr-policy", system: "MNR", trip_id: "row7-mnr-undated", observed_at: null, provenance: "reported" },
];

// A served railroad row. Anchors on every one of them, so each has a heading to draw and the
// head's FILL is what distinguishes the rows rather than its shape; row 6 is the exception the
// table makes and D3a asserts it as one.
const railRow = ({ system, trip_id, observed_at, provenance }) => ({
  system,
  trip_id,
  route_id: "1",
  latitude: 40.7,
  longitude: -73.79,
  bearing: null,
  train_num: trip_id.slice(0, 4),
  stop_id: provenance === "reported" ? null : "12",
  stop_name: provenance === "reported" ? null : "Jamaica",
  direction: "Outbound",
  prev_lat: 40.7,
  prev_lon: -73.8,
  prev_time: fx.FROZEN_S - 60,
  next_time: fx.FROZEN_S + 60,
  observed_at: observed_at === null ? null : fx.FROZEN_S + observed_at,
  provenance,
});

// stampObserved rewrites any non-null observed_at to the poll clock, which would flatten rows
// 1 and 2 into one another, so the body is built and then the two dated rows are restored.
const railWorld = (rows = ROWS) => {
  const built = fx.railroadsWithSystems({ data: rows.map(railRow) });
  built.data = rows.map(railRow);
  return built;
};

async function open(page, before, { rail = ROWS.length, stations = 14 } = {}) {
  await page.setViewportSize(DESKTOP);
  const ctx = await installMocks(page);
  ctx.overrides.railroads = (route) => json(route, railWorld());
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await page.waitForFunction(
    (want) => railroads.size === want.rail && stationRegistry.length === want.stations,
    { rail, stations },
    { timeout: 15_000 },
  );
  await page.clock.runFor(1000);
  return ctx;
}

/* EVERY RAIL TAG ON THE PAGE, read through its ACCESSIBLE NAME, which is what the operator
   asked for and is the right handle: the name is the one string that says which train this is
   and what its position claims, so a mark found by name and then measured cannot be measured on
   the wrong train. The shape is read off the drawn svg rather than off a record's field. */
const tags = (page) =>
  page.evaluate(() =>
    [...railroads.entries()].map(([key, record]) => {
      const el = record.marker.getElement();
      const svg = el.querySelector("svg.rail-tag");
      const head = svg.querySelector("path, circle");
      return {
        key,
        // Off the ELEMENT, which is where labeledMarker writes them; the Leaflet marker is not
        // a DOM node and has no getAttribute.
        name: el.getAttribute("aria-label"),
        role: el.getAttribute("role"),
        body: svg.classList.contains("rail-tag-outlined") ? "outlined" : "solid",
        head: svg.classList.contains("rail-head-filled") ? "filled" : "outlined",
        shape: head.tagName.toLowerCase() === "path" ? "chevron" : "dot",
        rotation: head.getAttribute("transform"),
        opacity: record.marker.options.opacity ?? 1,
        // AND WHAT THE PAGE IS ACTUALLY DRAWN AT, off the element's inline style, which is where
        // Leaflet's setOpacity writes. The option above is what the app INTENDED; this is what a
        // rider sees, and the two are only the same while dimMarker is wired. One of the three
        // shapes this phase's defects took was believing the model over the drawn page.
        drawn: el.style.opacity === "" ? 1 : Number(el.style.opacity),
        code: [...svg.querySelectorAll("text")].map((t) => t.textContent),
      };
    }),
  );

const byTrip = (rows, trip) => rows.find((r) => r.key.endsWith(`|${trip}`));

test("D3a. each row of the brief's 3.1 table renders the mark the table says", async ({ page }) => {
  await open(page);
  const rows = await tags(page);
  expect(rows, "six of the seven rows are on this page").toHaveLength(6);

  /* THE TABLE, ROW BY ROW, on the drawn page. The node oracle asks the same seven rows as
     arithmetic; this asks whether the arithmetic reached the screen, which is a different
     claim and the one a rider cares about. */
  const expected = {
    "row1-reported-fresh": { body: "solid", head: "filled", shape: "chevron", opacity: 1 },
    "row2-reported-aged": { body: "solid", head: "filled", shape: "chevron", opacity: 0.45 },
    "row3-estimated": { body: "outlined", head: "filled", shape: "chevron", opacity: 1 },
    "row4-placed": { body: "outlined", head: "outlined", shape: "chevron", opacity: 1 },
    /* ROW 6 REFUSES THE HEADING it could have drawn: this row carries the same anchors as the
       five above, so the chevron was available and the table declines it. AND IT DIMS, which is
       the operator's ruling on finding N3 and the 6.3 erratum: an observation that should have
       carried a clock and did not is not fresh, so dimming carries not-fresh rather than an
       age. This is the row the whole erratum is about, on the page. */
    "row6-unknown-gated": { body: "outlined", head: "outlined", shape: "dot", opacity: 0.45 },
    "row7-mnr-undated": { body: "solid", head: "filled", shape: "chevron", opacity: 1 },
  };
  for (const [trip, want] of Object.entries(expected)) {
    const got = byTrip(rows, trip);
    expect(got, `${trip} is on the page`).toBeTruthy();
    expect(
      { body: got.body, head: got.head, shape: got.shape, opacity: got.opacity },
      `${trip}: ${got.name}`,
    ).toEqual(want);
  }

  /* AND THE ROWS ARE DISTINGUISHABLE FROM EACH OTHER, which is the claim the table is for and
     which six correct rows do not by themselves establish: before MR3 rows 3 and 4 were the
     same mark. Asserted as the count of distinct (body, head, shape) triples. */
  const marks = new Set(rows.map((r) => `${r.body}/${r.head}/${r.shape}`));
  expect([...marks].sort(), "four distinct marks across the six rows").toEqual([
    "outlined/filled/chevron",
    "outlined/outlined/chevron",
    "outlined/outlined/dot",
    "solid/filled/chevron",
  ]);

  // EVERY TAG IS AN IMAGE WITH A NAME, which is how this spec found them and is the A2 rule.
  for (const r of rows) {
    expect(r.role, `${r.key} role`).toBe("img");
    expect(r.name, `${r.key} name`).toBeTruthy();
  }

  /* THE TWO GLYPHS, AND THE (system, route) KEY THEY PROVE. Every train in this world is on
     route "1" and the two agencies give that id two different branches: the LIRR's route 1 is
     the Babylon Branch and Metro-North's is the Hudson Line. So a code table keyed by route id
     alone would print one of these twice, and the agency letter is what a rider reads first.

     BOTH CODES ARE THE NAME-KEYED ONES rather than the id, which is the fallback: "1" in either
     block would mean the table was not consulted at all. */
  for (const r of rows.filter((row) => row.key.startsWith("LIRR|"))) {
    expect(r.code, `${r.key} glyphs`).toEqual(["L", "BAB"]);
  }
  expect(byTrip(rows, "row7-mnr-undated").code, "the same route id, the other agency").toEqual(["M", "HUD"]);
});

test("D3b. a retained train is drawn as the state it was in, dimmed, and MR3 did not change that", async ({ page }) => {
  /* THE SEVENTH ROW, which needs two polls: retention stamps over a row's provenance, so the
     page has to have seen the before. smoke.spec.js C2n already holds the placement case end to
     end; what is new here is that the HEAD is retained too, which the old square could not say
     because it had no head. */
  const ctx = await open(page);
  const before = byTrip(await tags(page), "row3-estimated");
  expect([before.body, before.head], "the before: an estimate").toEqual(["outlined", "filled"]);

  /* THE SAME ROWS, NOW SERVED RETAINED, AND THE SYSTEM BLOCKS RETAINED WITH THEM, which is the
     shape the backend actually serves and is round 4's correction to this world. It used to carry
     `systems: railWorld().systems`, the FRESH blocks, beside rows stamped `retained`: a payload
     saying at once "this generation could not be refreshed" and "the last successful poll was a
     moment ago". Under that world nothing could dim, which is why the dimming half of this spec's
     own title went unheld for three commits.

     RETENTION AGES THE BLOCK because `fetched_at` is the last SUCCESSFUL poll: a system retained
     for five minutes has been failing for five minutes. So `retained_since` and the block's clock
     move together, and the dimming here is the SYSTEM's age doing its ordinary job rather than a
     rule about retention (helpers.js's systemStaleAts says why opacity is deliberately not moved
     to retention itself). Five minutes is past FEED_STALE_AFTER_S and inside OBS_MAX_S. */
  const RETAINED_SINCE = fx.FROZEN_S - 300;
  const retainedRows = ROWS.map((r) => ({ ...r, provenance: "retained" }));
  ctx.overrides.railroads = (route) => {
    const world = fx.railroadsWithSystems({
      data: retainedRows.map(railRow),
      lirrAt: RETAINED_SINCE,
      lirrRetainedSince: RETAINED_SINCE,
      mnrAt: RETAINED_SINCE,
      mnrRetainedSince: RETAINED_SINCE,
    });
    // stampObserved would flatten rows 1 and 2 into one another, exactly as railWorld guards.
    world.data = retainedRows.map(railRow);
    return json(route, world);
  };
  await page.evaluate(() => refreshAll && refreshAll());
  await page.clock.runFor(2000);

  const after = byTrip(await tags(page), "row3-estimated");
  // AS THE ESTIMATE IT WAS: outlined body, filled head. A retained estimate that lost its head
  // would be claiming less than the page knows, and one that gained a solid body would be
  // claiming more.
  expect([after.body, after.head], "drawn as the estimate it was").toEqual(["outlined", "filled"]);

  /* AND DIMMED, which this spec's own title promised and its body did not check (round 4). The
     word "dimmed" was in the title through three commits over a test that asserted only the two
     shapes, so the whole retained half of the opacity contract was unheld here: a retained train
     drawn at full opacity would have passed.

     0.45 IS STALE_MARKER_OPACITY and the number is asserted rather than `< 1`, because the value
     is the contract's (docs/design/freshness-contract.md) and a marker at 0.9 is not dimmed, it
     is slightly wrong. Read off the ELEMENT's inline style, not off the marker's option: the
     option is what the app meant and the style is what the rider sees.

     THE BEFORE IS ASSERTED TOO, so this cannot pass on a page that was dim all along. */
  expect(before.drawn, "the before, on a fresh estimate, is not dimmed").toBe(1);
  expect(after.drawn, "a retained train is drawn dim").toBeCloseTo(0.45, 5);
  expect(after.opacity, "and the option agrees with the drawn page").toBeCloseTo(0.45, 5);

  /* EVERY retained row, not only the estimate: retention is a property of the payload, so a page
     that dimmed one train and left five bright would be a worse failure than one that dimmed
     none. Row 7 included, and that is the interesting one: Metro-North's positions are not
     age-gated, but RETENTION is not an observation age. The backend is serving a generation it
     could not refresh, which is the system's own staleness, and every system dims for it. */
  for (const row of await tags(page)) {
    expect(row.drawn, `${row.key} is retained and must be dim`).toBeCloseTo(0.45, 5);
  }
});

test("D3c. a square always means regional rail and a circle always means subway", async ({ page }) => {
  await open(page);
  /* THE COUNT BOTH WAYS, which is what makes this a claim rather than a slogan. Before MR3 the
     three rail families drew three different station marks: LIRR and Metro-North a 3.5px white
     circle, NJ Transit a 12px filled slate square, and the sentence was not true of any of
     them. */
  const shapes = await page.evaluate(() => {
    const kinds = {};
    for (const entry of stationRegistry) {
      const el = entry.marker.getElement ? entry.marker.getElement() : null;
      const svg = el ? el.querySelector("svg") : null;
      kinds[entry.kind] ??= { square: 0, circle: 0, canvas: 0, total: 0 };
      kinds[entry.kind].total += 1;
      if (!svg) kinds[entry.kind].canvas += 1;
      else if (svg.querySelector("circle")) kinds[entry.kind].circle += 1;
      else if (svg.querySelector("rect")) kinds[entry.kind].square += 1;
    }
    return kinds;
  });

  // EVERY RAIL STATION IS A SQUARE, all three agencies, with none left over.
  for (const kind of ["railroad", "njt"]) {
    expect(shapes[kind], `${kind} stations exist in this world`).toBeTruthy();
    expect(shapes[kind].square, `every ${kind} station is a square`).toBe(shapes[kind].total);
    expect(shapes[kind].circle, `no ${kind} station is a circle`).toBe(0);
  }
  // AND NO SUBWAY STATION IS ONE. A subway station is a canvas circleMarker with no element at
  // all, which is the other half: it cannot be a square, and a change that gave it a divIcon
  // would show up here as a square rather than as nothing.
  expect(shapes.subway.square, "no subway station is a square").toBe(0);
  expect(shapes.subway.canvas, "subway stations are drawn on the shared canvas").toBe(shapes.subway.total);

  /* THE ONE SHAPE IS ONE SHAPE, byte for byte across the three agencies. Three families drawing
     one grammar is this stage's claim, and it is only true if they call one builder. */
  const htmls = await page.evaluate(() =>
    [...new Set(
      stationRegistry
        .filter((e) => e.kind === "railroad" || e.kind === "njt")
        .map((e) => e.marker.getIcon().options.html),
    )],
  );
  expect(htmls, "all three rail agencies draw the identical square").toHaveLength(1);
});

test("D3d. rail station names show from zoom 11, never with the hub class, and the Names toggle hides them", async ({
  page,
}) => {
  await open(page);
  const setZoom = async (z) => {
    await page.evaluate((zoom) => map.setZoom(zoom, { animate: false }), z);
    await expect(page.locator("html")).toHaveAttribute("data-zoom", String(z));
  };
  const painted = () =>
    page.evaluate(() =>
      [...document.querySelectorAll(".stn-label.rail")]
        .filter((el) => getComputedStyle(el).display !== "none")
        .map((el) => el.textContent)
        .sort(),
    );

  // A PREMISE: there are rail labels to gate, or every assertion below is vacuous.
  expect(await page.evaluate(() => document.querySelectorAll(".stn-label.rail").length)).toBe(5);
  // AND NONE OF THEM IS A HUB. A hub is a subway transfer station by one predicate in
  // helpers.js, and the hub class is what the subway's middle band reveals.
  expect(await page.evaluate(() => document.querySelectorAll(".stn-label.rail.hub").length)).toBe(0);

  await setZoom(10);
  await expect(page.locator("html")).toHaveAttribute("data-rail-label-band", "none");
  expect(await painted(), "below 11 no rail name is drawn").toEqual([]);

  // ELEVEN IS THE DESIGN'S NUMBER, three zooms before the subway shows all of its names and one
  // before it shows any: about 300 rail stations across the region against 496 subway stations
  // inside the city, so at 11 these are readable where the subway's would be a wall.
  await setZoom(11);
  await expect(page.locator("html")).toHaveAttribute("data-rail-label-band", "all");
  expect(await painted()).toEqual([
    "Grand Central",
    "Hoboken",
    "Jamaica",
    "New York Penn Station",
    "Newark Penn Station",
  ]);
  // AND THE SUBWAY'S BAND IS STILL ITS OWN at this zoom, which is why the two are separate
  // attributes: at 11 the subway shows nothing and the railroads show everything.
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "none");

  // THE NAMES TOGGLE IS A PREFERENCE OVER BOTH BANDS, which is the half a rail-only rule could
  // have broken: it is written last and at matching specificity in style.css.
  await page.locator("#names-toggle").click();
  await expect(page.locator("html")).toHaveAttribute("data-labels", "off");
  expect(await painted(), "the toggle hides rail names too").toEqual([]);
  await page.locator("#names-toggle").click();
  expect(await painted(), "and restores them").toHaveLength(5);
});

test("D3g. rail labels cannot answer a question about the subway's band", async ({ page }) => {
  /* MR3 ROUND 4. `paintZoomBand`'s sentinel asks two things off the DOM: has the subway loaded,
     and does it publish any interchange. MR3 put about 300 commuter-rail labels in the same
     `.stn-label` class, and the sentinel counted them.

     WHAT THAT COSTS, exactly. With rail labels present and subway labels not yet, the counts read
     "labels: many, hubs: zero", which is `LABEL_NO_HUB_ZOOM`'s DEGRADED band: every subway name
     from 13 instead of hubs from 12. That is the right answer to a subway whose route index came
     back empty and the wrong answer to a subway whose stations are merely still in flight, and
     the two are indistinguishable once the rail labels are in the count.

     THE WORLD IS A SUBWAY WITH NO STATIONS AT ALL, which is the shape that separates them: the
     honest reading of zero subway labels is "nothing to judge yet, keep the normal band", and the
     mutated reading is "a whole network with no interchange". At zoom 13 those are different
     answers, "hubs" against "all", so one attribute settles it.

     THIS IS THE MUTATION: drop `:not(.rail)` from either count in paintZoomBand. */
  await open(page, (ctx) => {
    ctx.overrides.subwayStops = (route) => json(route, []);
  }, { stations: 12 });

  const counts = await page.evaluate(() => ({
    all: document.querySelectorAll(".stn-label").length,
    subway: document.querySelectorAll(".stn-label:not(.rail)").length,
    hubs: document.querySelectorAll(".stn-label.hub:not(.rail)").length,
  }));
  // THE PREMISES, both of them, or this spec asserts nothing: there are rail labels on the page
  // and there are no subway labels for them to be mistaken for.
  expect(counts.all, "rail labels are on the page").toBeGreaterThan(0);
  expect(counts.subway, "and no subway label is").toBe(0);
  expect(counts.hubs).toBe(0);

  await page.evaluate(() => map.setZoom(13, { animate: false }));
  await expect(page.locator("html")).toHaveAttribute("data-zoom", "13");
  /* "hubs", NOT "all". The subway has nothing on screen to judge, so the band stays the ordinary
     one; counting the rail labels would make this "all", the degraded band for a network that
     published no interchange at all. The rail band is unaffected either way, which is the other
     half of "two bands, two attributes". */
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "hubs");
  await expect(page.locator("html")).toHaveAttribute("data-rail-label-band", "all");

  // And at 12, where the degraded band says "none" and the ordinary one says "hubs", so the two
  // differ in the other direction and neither assertion can be passing by coincidence.
  await page.evaluate(() => map.setZoom(12, { animate: false }));
  await expect(page.locator("html")).toHaveAttribute("data-zoom", "12");
  await expect(page.locator("html")).toHaveAttribute("data-label-band", "hubs");
});

test("D3e. every branch line is a casing and a line, in the feed's own colour", async ({ page }) => {
  await open(page);
  const probe = await page.evaluate(() => {
    const out = [];
    for (const system of ["LIRR", "MNR"]) {
      for (const layer of railroadLineLayer(system).getLayers()) {
        out.push({
          system,
          color: layer.options.color,
          weight: layer.options.weight,
          opacity: layer.options.opacity,
          cap: layer.options.lineCap,
          pane: layer.options.renderer?.options?.pane ?? "overlayPane",
        });
      }
    }
    /* AND WHAT EACH CANVAS ACTUALLY HOLDS, walked off the renderers' own lists rather than inferred
       from the layer groups. Leaflet's Canvas keeps an ordered chain (_drawFirst through .next) and
       redraws it in that order; a LayerGroup is a bookkeeping device the renderer never consults,
       which is the whole reason MR2's F6 happened. So the groups above say what each branch asked
       for and this says what the browser paints, and on which canvas.

       BOTH RAIL FAMILIES AND NJ TRANSIT ARE ON THESE TWO, so the chains below are every rail mark
       the page drew and not only the two branches the groups above read. */
    const chain = (renderer) => {
      const out2 = [];
      for (let node = renderer._drawFirst; node; node = node.next) out2.push(node.layer.options.weight);
      return out2;
    };
    return {
      lines: out,
      casingChain: chain(railroadCasingRenderer),
      lineChain: chain(railroadLineRenderer),
      paper: paperColor(),
      panes: {
        casing: Number(getComputedStyle(map.getPane("railroadCasingPane")).zIndex),
        line: Number(getComputedStyle(map.getPane("railroadLinePane")).zIndex),
      },
    };
  });
  const lines = probe.lines;

  // TWO LAYERS PER BRANCH, the casing and the line.
  expect(lines).toHaveLength(4);
  expect(lines.map((l) => l.weight)).toEqual([5, 2.5, 5, 2.5]);

  /* THE CASING IS THE RESOLVED PAPER, NOT THE CUSTOM PROPERTY, and this is the assertion that was
     inverted: it asserted "var(--paper)" and therefore asserted the DEFECT. Canvas2D resolves no
     custom properties. `ctx.strokeStyle = "var(--paper)"` is not an error and not a fallback: the
     assignment is a SILENT no-op that leaves the context holding whatever colour it stroked last,
     so every rail casing was drawn in the previous branch's ink. Measured in-page, which is how
     it was found. shared.js's paperColor() reads the computed token once per draw and is the
     repo's answer to exactly this; subway.js has used it since MR2.

     ASSERTED THREE WAYS, because "not the literal string" alone would pass for any garbage: it
     matches paperColor()'s live answer, it is a real hex, and it contains no "var(". */
  const casings = lines.filter((l) => l.weight === 5);
  expect(casings.map((l) => l.color)).toEqual([probe.paper, probe.paper]);
  for (const casing of casings) {
    expect(casing.color, "a canvas stroke cannot be a custom property").not.toContain("var(");
    expect(casing.color).toMatch(/^#[0-9a-fA-F]{6}$/);
  }
  expect(casings.map((l) => l.opacity)).toEqual([0.9, 0.9]);

  /* EVERY CASING ON ITS OWN CANVAS, BELOW EVERY LINE, read off the two renderers' draw chains.

     Two layers per branch added as a PAIR is enough only within a branch: where the Babylon and
     Montauk branches share the rails out of Jamaica, Montauk's 5px casing is inserted after
     Babylon's 2.5px line and erases it, which is F6 reopened one pane down. Two passes over one
     payload is not enough EITHER, and that is what the chains hold: the railroads and NJ Transit
     arrive from two endpoints in a race, so with one renderer the chain came out
     [5, 5, 2.5, 2.5, 5, 5, 5, 2.5, 2.5, 2.5] and all three NJT casings were stroked after both
     railroad lines. A pane per tier is the only ordering that cannot depend on arrival order.

     ASSERTED AS A PARTITION, not as a sequence: every weight on the casing canvas is 5 and every
     weight on the line canvas is 2.5, so there is no order within either chain that could be
     wrong. THIS IS THE MUTATION: give the casing railroadLineRenderer. Then the casing chain
     empties, the line chain carries both weights, and both assertions below fail. */
  expect(probe.casingChain.length, "no rail casing reached the casing canvas").toBeGreaterThan(0);
  expect(probe.lineChain.length, "no rail line reached the line canvas").toBeGreaterThan(0);
  expect([...new Set(probe.casingChain)], "the casing canvas holds only casings").toEqual([5]);
  expect([...new Set(probe.lineChain)], "the line canvas holds only lines").toEqual([2.5]);
  // The two chains together are every rail mark on the page: two branches here plus NJ Transit's.
  expect(probe.casingChain.length, "one casing per line, always a pair").toBe(probe.lineChain.length);
  // AND THE CASING CANVAS IS UNDER THE LINE CANVAS, which is what makes the partition an ordering.
  expect(probe.panes.casing).toBe(394);
  expect(probe.panes.line).toBe(395);
  expect(probe.panes.casing).toBeLessThan(probe.panes.line);
  // THE LINE TAKES THE AGENCY'S OWN COLOUR, verbatim, with the "#" added where the value
  // becomes a paint instruction: the LIRR's Babylon green and Metro-North's Hudson green, which
  // are two different greens and were one hash of a route id before MR3.
  expect(lines.filter((l) => l.weight === 2.5).map((l) => l.color)).toEqual(["#00985F", "#009B3A"]);
  expect(lines.every((l) => l.cap === "round")).toBe(true);
  /* AND THEY ARE ON THE TWO RAIL PANES, above the subway's 390 and below the shared canvas at
     400, which is the operator's ruling on finding N2: a 5px casing is thicker than PATH's
     3.5px line, the AirTrain's 3px and the ferry's 2px, and on one canvas the later arrival
     wins. D2u holds the ORDER across a shuffled insertion sequence; this is the rail families'
     own row in it, read off the layers this page actually built. */
  expect(casings.every((l) => l.pane === "railroadCasingPane")).toBe(true);
  expect(lines.filter((l) => l.weight === 2.5).every((l) => l.pane === "railroadLinePane")).toBe(true);
  const z = await page.evaluate(() => ({
    subway: Number(getComputedStyle(map.getPane("subwayLinePane")).zIndex),
    casing: Number(getComputedStyle(map.getPane("railroadCasingPane")).zIndex),
    rail: Number(getComputedStyle(map.getPane("railroadLinePane")).zIndex),
    overlay: Number(getComputedStyle(map.getPane("overlayPane")).zIndex),
  }));
  expect(z.rail).toBe(395);
  expect(z.casing).toBe(394);
  expect(z.subway).toBeLessThan(z.casing);
  expect(z.casing).toBeLessThan(z.rail);
  expect(z.rail).toBeLessThan(z.overlay);
});

test("D3f. Metro-North draws solid and live, and its undated policy is said once on the status line", async ({
  page,
}) => {
  /* THE POLICY ROW, END TO END. Metro-North sends no observation clock at all, and the contract
     makes that a statement about the PROVIDER rather than about each train: the markers stay
     solid and bright and the fact is said once, on that system's line. A marker that dimmed
     for it, or a line that did not say it, would each be a different defect and both are
     checked here. */
  await open(page, (ctx) => {
    // Metro-North's own poll aged six minutes, which is the world that RAISES the line: the
    // undated clause rides a raised line and never raises one on its own.
    ctx.overrides.railroads = (route) =>
      json(route, { ...railWorld(), systems: fx.railroadsWithSystems({ mnrAt: fx.FROZEN_S - 360 }).systems });
  });
  const mnr = byTrip(await tags(page), "row7-mnr-undated");
  expect([mnr.body, mnr.head, mnr.shape], "solid and headed, by policy").toEqual(["solid", "filled", "chevron"]);
  // Dimmed by its SYSTEM's age here, which is a different rule from the observation's and is
  // the contract's, untouched: six minutes is past the threshold, so the whole feed dims.
  expect(mnr.opacity).toBe(0.45);
  // AND THE CLAUSE IS ON THE LINE, in the app's own words, said once for the system.
  const status = (await page.locator("#status").textContent()) ?? "";
  expect(status).toContain("MNR position age unavailable");
  // NEVER ON THE MARKER, which is the whole point of putting it on the line: a qualifier a
  // rider sees on all 33 Metro-North trains, always, is one they stop reading.
  expect(mnr.name).not.toContain("age unknown");
});
