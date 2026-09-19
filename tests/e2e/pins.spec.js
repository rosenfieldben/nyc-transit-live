/* Phase MR pins: what the map redesign is not allowed to change by accident.
   ==========================================================================

   THESE LAND BEFORE ANYTHING IS RESTYLED, and that is the whole point. Stage MR1
   rewrites index.html's chrome and most of style.css. A rewrite that size can move a
   marker, reword a status line or drop a legend row without a single spec going red,
   because nothing in the suite asserted those things: the legend's rows were pinned by
   NO test at all (measured), the status line was pinned only where a spec happened to
   need a particular clause, and no test anywhere compared a marker's HTML against a
   known good. So the invariants are written down FIRST, as goldens, against the
   pre-redesign tree.

   WHAT A PIN IS HERE. Not "this is how it should look" but "this is how it looks today,
   and MR1 is not the stage that changes it". MR1's scope is tokens, the header, the feed
   strip, the Key panel, the alerts strip's position and the control stack. Markers,
   lines, stations, labels, popups, the railroad tables and route focus are MR2 through
   MR5. Every pin below fails if MR1 reaches into one of those.

   WHY THE STATUS PINS ASSERT A CLAUSE AND NOT THE WHOLE LINE. The status line today is
   `counts · clock: problems` in one element. MR1 takes it apart on purpose: the counts
   become the feed strip's per-feed counts, the clock becomes the header's, and the
   problems become the strip's trailing note. So the whole line is NOT invariant, and a
   pin on it would be a pin on the thing being changed. What is invariant is the WORDS:
   staleness() composes them, MR1 renders its output verbatim, and these pins hold that
   text character for character in the two worlds the 6.3 acceptance built.

   REGENERATING. `MR_PINS_REGENERATE=1 npx playwright test --config
   tests/e2e/playwright.config.js tests/e2e/pins.spec.js` rewrites fixtures/mr_pins.json
   from the running page. Do that only with a reason, and say the reason in the commit: a
   regenerated golden is a pin deliberately moved, which is a normal thing to do in MR2
   through MR5 and a defect in MR1. Regeneration writes key by key, so a filtered run
   updates only what it measured instead of truncating the file. */
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const GOLDEN = path.join(__dirname, "fixtures", "mr_pins.json");
const REGENERATE = !!process.env.MR_PINS_REGENERATE;

const readGolden = () => (fs.existsSync(GOLDEN) ? JSON.parse(fs.readFileSync(GOLDEN, "utf8")) : {});

// Workers run in parallel, so a regeneration read-modify-write can lose a sibling's
// entry. Sorted keys on the way out keep the file stable whatever order they land in;
// a truncated file after a parallel regeneration is repaired by running it again, which
// is cheap and visible (the assert run that follows says which key is missing).
function pin(key, measured) {
  const [head, tail] = key.split("/");
  if (REGENERATE) {
    const all = readGolden();
    if (tail) (all[head] ??= {})[tail] = measured;
    else all[head] = measured;
    const sorted = {};
    for (const k of Object.keys(all).sort()) {
      sorted[k] =
        all[k] && !Array.isArray(all[k]) && typeof all[k] === "object"
          ? Object.fromEntries(Object.keys(all[k]).sort().map((k2) => [k2, all[k][k2]]))
          : all[k];
    }
    fs.writeFileSync(GOLDEN, `${JSON.stringify(sorted, null, 1)}\n`);
    return;
  }
  const all = readGolden();
  const expected = tail ? (all[head] ?? {})[tail] : all[head];
  expect(expected, `${key} is missing from ${path.relative(process.cwd(), GOLDEN)}`).toBeDefined();
  expect(measured, `${key} moved; if that is deliberate, regenerate the golden and say why`).toEqual(expected);
}

/* ---------------- boot ---------------- */

// The same frozen-clock boot the rest of the suite uses (fixtures/api.js FROZEN_MS), so
// every age in a popup and every clock in a status line is a constant.
async function boot(page, before, { railroadCount = 2, stationCount = 14 } = {}) {
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // Every system's marks, not "more than five of them": these pins read specific registry
  // keys, and a partially loaded page would quietly pin a shorter list. The counts are the
  // stock fixtures' (mock.js); two are overridable, because two pins change the world they
  // boot into: P1b serves the F01 capture's 136 railroad rows instead of the fixture's 2,
  // and P2c adds F03's Prospect Av to the fourteen stations.
  await page.waitForFunction(
    (want) =>
      trains.size === 2 &&
      buses.size === 2 &&
      railroads.size === want.railroads &&
      pathTrainRecords.size === 2 &&
      ferryBoatRecords.size === 3 &&
      njtTrainRecords.size === 4 &&
      stationRegistry.length === want.stations,
    { stations: stationCount, railroads: railroadCount },
    { timeout: 15_000 },
  );
  // One tick, so the first poll's status write has landed.
  await page.clock.runFor(1000);
  return ctx;
}

/* ---------------- P1 and P2: the status line's words ---------------- */

/* The problems tail of the status line, which is staleness()'s output: map.js composes
   `problems` from every source's error and every source's staleness(), and statusLineText
   puts the counts, then the clock, then that list after a ": ".

   READ SO THAT THE SAME PIN READS THE SAME STRING ON BOTH SIDES OF THE STAGE, which is
   the whole reason this helper exists rather than an indexOf(": ") at the call sites.
   Today #status holds `counts · clock: problems`, or `counts · updated clock` when there
   are none. After MR1 it holds the problems and nothing else, so the counts-and-clock head
   is stripped only when there IS one: the clock is what marks its end, and a line that
   ends at the clock had nothing to say. An indexOf would find the colon inside "trains:
   ACE group …" after the stage and silently pin a shorter string. */
const statusTail = async (page) => {
  const text = (await page.locator("#status").textContent()) ?? "";
  // The narrow no-break space is in the alternation because newer ICU builds put one
  // before the meridiem where older ones put an ordinary space.
  const head = text.match(/^.*?\d{1,2}:\d{2}:\d{2}(?:[\s\u202f][AP]M)?(: |$)/);
  if (!head) return text; // no clock in it: MR1's note, which is the tail itself
  return head[1] === ": " ? text.slice(head[0].length) : "";
};

test("P1a. the F03 world's status clause, character for character", async ({ page }) => {
  // F03 is content old behind a poll that keeps succeeding: the ACE group's feed
  // timestamp ten minutes back while its fetch is current, which is the state
  // backend/tests/test_f03_boards.py's world describes and the one 6.1 built the
  // content population for. The line must say so, and must say it in these words.
  await boot(page, (c) => {
    c.overrides.subways = (route, fixtures) =>
      json(route, fixtures.subwaysWithSystems({ aceContentAt: fx.FROZEN_S - 600 }));
  });
  pin("status/f03", await statusTail(page));
  await expect(page.locator("#status")).toHaveClass(/error/);
});

test("P1b. the F01 world's status clause, character for character", async ({ page }) => {
  // F01's committed capture, with Metro-North's own poll aged six minutes so the line is
  // RAISED. That is not decoration: the withheld clause and the undated clause both ride
  // a raised line and never raise one (staleness() says why, and smoke.spec.js's C2j
  // pins the quiet of the healthy world), so a world with nothing else wrong shows
  // neither. This is the world where all three arrive at once, which makes it the one
  // worth pinning: MR1 renders staleness()'s output verbatim, and verbatim has to
  // include the count of trains the position ladder drew nothing for (24, from the
  // capture's own LIRR block) and Metro-North's "no position clock at all" clause.
  await boot(page, (c) => {
    c.overrides.railroads = (route) => {
      const body = JSON.parse(JSON.stringify(require("./fixtures/f01_railroads.json")));
      body.systems.MNR.fetched_at = fx.FROZEN_S - 360;
      return json(route, body);
    };
    // The capture's 136 rows are the ones the ladder DREW: the 24 it withheld are absent
    // from `data` entirely (smoke.spec.js's C2j asserts none of them reaches a marker),
    // and the suppressed count on the LIRR block is what the status line reports instead.
  }, { railroadCount: 136 });
  pin("status/f01", await statusTail(page));
  await expect(page.locator("#status")).toHaveClass(/error/);
});

test("P1c. a healthy day says nothing and is not painted as an error", async ({ page }) => {
  // The common case, and the one the v3 brief singles out ("nothing at all on a healthy
  // day, which is the common case and must not get noisier"). Pinned here so a trailing
  // note that re-derives its text from the freshness index instead of taking
  // staleness()'s answer is caught the moment it invents something to say.
  await boot(page);
  expect(await statusTail(page)).toBe("");
  await expect(page.locator("#status")).not.toHaveClass(/error/);
});

/* ---------------- P1d: the alert banner's rows ---------------- */

test("P1d. the alert banner's rows, byte for byte", async ({ page }) => {
  // MR1 moves this strip inside the header as a full-width row and restyles it. It does
  // not rewrite its markup: renderAlertBanner's row / stale-row / dismiss templates, the
  // ids and classes every other spec selects, and the escaping of the feed's own text
  // are all outside stage 1. So the whole subtree is the pin.
  await boot(page, (c) => {
    c.overrides.alerts = (route, fixtures) =>
      json(route, {
        ...fixtures.alerts(),
        alerts: [1, 2].map((n) => ({
          id: `mr-pin-${n}`,
          system: "subway",
          header:
            n === 1
              ? "Reduced service systemwide while crews clear a disabled train"
              : "Some elevators are out of service across the system",
          description: null,
          effect: "REDUCED_SERVICE",
          cause: "OTHER_CAUSE",
          routes: [],
          stops: [],
          starts_at: fx.FROZEN_S - 600,
          ends_at: null,
        })),
      });
  });
  await expect(page.locator(".alert-banner-row")).toHaveCount(2);
  pin("alertBanner/html", await page.locator("#alert-banner").innerHTML());
  pin(
    "alertBanner/rows",
    await page.locator(".alert-banner-row").evaluateAll((els) => els.map((el) => el.textContent)),
  );
});

/* ---------------- P4: the legend's accessible names ---------------- */

// A row's accessible name is its text with every aria-hidden subtree removed, which for
// these rows means "without the glyph". Read that way rather than as textContent,
// because the subway row's svg carries a literal "A" that a screen reader never speaks:
// textContent says "A Subway train, at/approaching the stop shown" and the name is
// "Subway train, at/approaching the stop shown". A pin on textContent would force the
// Key panel to keep a decorative letter inside an accessible name.
const accessibleNames = (page, selector) =>
  page.evaluate(
    (sel) =>
      [...document.querySelectorAll(sel)].map((el) => {
        const copy = el.cloneNode(true);
        for (const hidden of copy.querySelectorAll('[aria-hidden="true"]')) hidden.remove();
        return copy.textContent.trim().replace(/\s+/g, " ");
      }),
    selector,
  );

test("P1e. every name the legend says today", async ({ page }) => {
  // THE LIST, not the markup, and asserted as a SUPERSET rather than as an equality. MR1
  // replaces these rows' styling and the grid they sit in; MR2 through MR5 replace the
  // glyphs themselves as each stage changes the markers they describe. What may not happen
  // at any stage is that a rider loses a sentence, so the claim is "every name that was here
  // is still here" and additions are allowed: MR1 already makes one, a dimmed-vehicle row
  // that the freshness contract needed and this legend never had.
  //
  // The golden is still the measured seventeen-plus-the-note from before the restyle, so a
  // row dropped in any later stage fails here by name.
  await boot(page);
  const names = await accessibleNames(page, "#legend .legend-row, #legend .legend-note");
  pin("legend/names", names.filter((name) => (readGolden().legend?.names ?? names).includes(name)));
  const missing = (readGolden().legend?.names ?? []).filter((name) => !names.includes(name));
  expect(missing, "the Key panel lost a sentence the legend used to say").toEqual([]);
});

/* ---------------- P1f through P1n: markers and popups, per system ---------------- */

/* Every vehicle and station mark on the map, and one popup per surface, read from the
   live page. Stage MR1 changes none of it.

   TWO SHAPES, because the app draws in two ways. A divIcon marker is HTML, so the pin is
   its HTML plus the anchor and size that place it. A circleMarker is drawn by a canvas
   renderer and has no element at all, so the pin is the options that decide the drawing:
   radius, stroke, fill and pane. Both are byte-exact and neither is a screenshot.

   Popups are read after opening, from .leaflet-popup-content, so what is pinned is what
   a rider sees rather than what a builder returns. Station popups fetch their arrivals,
   so they are read once the content has settled. */

/* A divIcon's identity is its HTML and the anchors that place it; a canvas circleMarker
   has no element at all, so its identity is the options its renderer draws from.

   NAMED ACCESSORS RATHER THAN EXPRESSIONS, because the page's CSP is `default-src 'self'`
   with no 'unsafe-eval': a pin that reached its markers through eval would be pinning
   something a rider's browser would refuse. page.evaluate sends a function's SOURCE and
   runs it in the page's global scope, so the app's top-level consts (subwayLayer, trains,
   stationRegistry and the rest) are in scope inside these arrows the way they are in the
   console. Nothing here is a closure over this file. */

// Opacity is read for every mark because the freshness contract sets it per observation,
// and a chrome stage must not touch that either.
const captureMarks = (page, spec) =>
  page.evaluate(({ groups, records }) => {
    const GROUPS = {
      subwayTrains: () => subwayLayer,
      subwayStations: () => stationLayer,
      buses: () => busLayer,
      // MR1 SPLIT THIS GROUP PER AGENCY so the feed strip can toggle the two railroads
      // separately. Concatenated LIRR-then-MNR, which is the order the one group held them
      // in, so the golden measured before the split still holds BYTE FOR BYTE: the split
      // changed which group a station is in and nothing about the station.
      railroadStations: () => ({
        getLayers: () => [...lirrStationLayer.getLayers(), ...mnrStationLayer.getLayers()],
      }),
      njtTrains: () => njtTrains,
      njtStations: () => njtStations,
      pathTrains: () => pathTrains,
      pathStations: () => pathStations,
      ferryBoats: () => ferryBoats,
      ferryDocks: () => ferryDocks,
      airtrainStations: () => airtrainStationLayer,
    };
    const RECORDS = {
      lirrTrains: () => [...railroads].filter(([key]) => key.startsWith("LIRR|")),
      mnrTrains: () => [...railroads].filter(([key]) => key.startsWith("MNR|")),
    };
    const mark = (m) =>
      m.getIcon
        ? {
            html: String(m.getIcon().options.html),
            className: m.getIcon().options.className ?? null,
            size: m.getIcon().options.iconSize ?? null,
            anchor: m.getIcon().options.iconAnchor ?? null,
            popupAnchor: m.getIcon().options.popupAnchor ?? null,
            opacity: m.options.opacity ?? 1,
          }
        : {
            radius: m.options.radius ?? null,
            color: m.options.color ?? null,
            weight: m.options.weight ?? null,
            fillColor: m.options.fillColor ?? null,
            fillOpacity: m.options.fillOpacity ?? null,
            pane: m.options.pane ?? null,
            opacity: m.options.opacity ?? 1,
          };
    const out = {};
    for (const name of groups ?? []) out[name] = GROUPS[name]().getLayers().map(mark);
    for (const name of records ?? []) out[name] = RECORDS[name]().map(([key, r]) => [key, mark(r.marker)]);
    return out;
  }, spec);

// What a rider sees, read out of .leaflet-popup-content after opening the mark, rather
// than what a builder returns: three of the eight systems compose their popup inline in
// their own file with no pure helper to call, so the DOM is the only place the whole
// string exists.
// The marker table, sent into the page by name. page.evaluate runs a function's SOURCE in
// the page's global scope, so the app's top-level consts are in scope the way they are in
// the console; nothing here closes over this file.
const MARKER_TABLE = `{
  "subway train": () => trains.get("sub-1").marker,
  "subway station": () => stationRegistry.find((e) => e.key === "subway|127").marker,
  "bus": () => buses.get("MTA NYCT_101").marker,
  "lirr train": () => railroads.get("LIRR|lirr-placed-1").marker,
  "lirr station": () => stationRegistry.find((e) => e.key === "LIRR|12").marker,
  "mnr train": () => railroads.get("MNR|mnr-gps-1").marker,
  "mnr station": () => stationRegistry.find((e) => e.key === "MNR|1").marker,
  "njt train": () => njtTrainRecords.get("NJ_3800").marker,
  "njt station": () => stationRegistry.find((e) => e.key === "NJT|109").marker,
  "path train": () => pathTrainRecords.get("p-1").marker,
  "path station": () => stationRegistry.find((e) => e.key === "PATH|26734").marker,
  "ferry boat": () => ferryBoatRecords.get("H1").marker,
  "ferry dock": () => stationRegistry.find((e) => e.key === "ferry|18").marker,
  "airtrain station": () => stationRegistry.find((e) => e.key === "airtrain|A").marker,
}`;

const inPage = (body) => new Function("which", `const MARKERS = ${MARKER_TABLE}; ${body}`);

/* ONE POPUP AT A TIME, AND READ THROUGH THE MARKER, not through the document. This app
   can hold a vehicle popup and a station popup open together (state.js carries a "two
   popups open" witness for exactly that), so `.leaflet-popup-content` is not a unique
   selector and the previous pin's popup would still be on screen. Asking the marker for
   its own popup element cannot pick up a neighbour's, and map.closePopup() alone cannot
   close the second one because only one of them is the map's "current" popup.

   THE SETTLE LOOP RUNS FROM HERE, NOT IN THE PAGE, and that is not a style choice: the
   boot pauses the clock (page.clock.pauseAt), so a setTimeout inside the page never fires
   and an in-page wait deadlocks until the test times out. Measured, that is exactly what
   happened: every station pin sat for the full timeout. Each round trip below is real
   time on the driver's side, which is what lets a station's arrivals fetch resolve, and
   the clock is advanced between reads so the app's own timers get their turn too. */
const closeAllPopups = (page) =>
  page.evaluate(() => {
    map.closePopup();
    for (const record of [...trains.values(), ...railroads.values(), ...buses.values(),
      ...pathTrainRecords.values(), ...ferryBoatRecords.values(), ...njtTrainRecords.values()]) {
      record.marker.closePopup();
    }
    for (const entry of stationRegistry) if (entry.marker) entry.marker.closePopup();
  });

async function popupHtml(page, name) {
  await closeAllPopups(page);
  await page.evaluate(inPage("MARKERS[which]().openPopup();"), name);
  const read = () =>
    page.evaluate(
      inPage(`const popup = MARKERS[which]().getPopup(), el = popup && popup.getElement();
              const content = el && el.querySelector(".leaflet-popup-content");
              return content ? content.innerHTML : null;`),
      name,
    );
  // Two equal non-empty readings in a row is the settle test, and a loading line is never
  // one of them. A timeout reports what it last saw, so the failure reads as a diff of the
  // wrong content rather than as a bare timeout with nothing to look at.
  let previous = null;
  let html = null;
  for (let i = 0; i < 80; i++) {
    const now = await read();
    if (now && now === previous) {
      html = now;
      break;
    }
    previous = now;
    await page.clock.runFor(100);
  }
  expect(html, `${name}: the popup never stopped changing (last seen: ${previous})`).not.toBeNull();
  await closeAllPopups(page);
  return html;
}

/* The per-system pins, P1f through P1n.

   THE IDS ARE LITERAL IN THE SOURCE, and that is not formatting. tests/specids.js is the
   one place that knows which test ids exist, and it reads them by pattern out of this
   file: `test("<id>.` or a template literal whose FIRST token is the id. An id it cannot
   collect is worse than a missing one, because a claim citing it resolves against nothing
   and still reads as evidence; specids.js calls that the silent-drop disease and makes an
   unclassifiable id a loud failure. Two shapes got caught writing this: "P5/P6." (its
   token pattern stops at the slash) and a title built as `${system.id}. ...` (the
   interpolation is not a literal first token, so all nine ids vanished while the tests
   still passed). Hence one literal title per system over a shared body. */
const markPin = (name, spec) => async ({ page }) => {
  await boot(page);
  pin(`markers/${name}`, await captureMarks(page, spec));
  if (!spec.popups.length) return;
  const popups = {};
  for (const which of spec.popups) popups[which] = await popupHtml(page, which);
  pin(`popups/${name}`, popups);
};

test(
  "P1f. subway: every mark and every popup, byte for byte",
  markPin("subway", { groups: ["subwayTrains", "subwayStations"], popups: ["subway train", "subway station"] }),
);
test("P1g. buses: every mark and every popup, byte for byte", markPin("buses", { groups: ["buses"], popups: ["bus"] }));
test(
  "P1h. lirr: every mark and every popup, byte for byte",
  markPin("lirr", { records: ["lirrTrains"], popups: ["lirr train", "lirr station"] }),
);
test(
  "P1i. mnr: every mark and every popup, byte for byte",
  markPin("mnr", { records: ["mnrTrains"], popups: ["mnr train", "mnr station"] }),
);
// LIRR and Metro-North share ONE station layer today. MR1 splits the vehicle, line and
// station layer groups per agency so the feed strip can toggle the two feeds separately;
// it does not change how a station is drawn, which is what this holds.
test(
  "P1j. railroad stations: every mark, byte for byte",
  markPin("railroadStations", { groups: ["railroadStations"], popups: [] }),
);
test(
  "P1k. njt: every mark and every popup, byte for byte",
  markPin("njt", { groups: ["njtTrains", "njtStations"], popups: ["njt train", "njt station"] }),
);
test(
  "P1l. path: every mark and every popup, byte for byte",
  markPin("path", { groups: ["pathTrains", "pathStations"], popups: ["path train", "path station"] }),
);
test(
  "P1m. ferry: every mark and every popup, byte for byte",
  markPin("ferry", { groups: ["ferryBoats", "ferryDocks"], popups: ["ferry boat", "ferry dock"] }),
);
test(
  "P1n. airtrain: every mark and every popup, byte for byte",
  markPin("airtrain", { groups: ["airtrainStations"], popups: ["airtrain station"] }),
);

/* ---------------- P2: what stage MR2 is not allowed to change ----------------

   MR2 restyles the subway: two polylines per shape instead of one, a haloed bullet in
   place of the flat rounded square, dot-or-ring stations instead of one white circle,
   permanent name labels, and route focus. Four things sit right beside all of that and
   are NOT its to move, so they are written down here before a line of it is written.

   WHY THESE FOUR AND NOT THE MARKS THEMSELVES. P1f already holds every subway mark byte
   for byte, and MR2 is the stage that deliberately moves it: its golden is regenerated
   and the before-and-after is recorded in docs/reviews/map-redesign-rounds.md, which is
   what "measured" means for a mark a stage exists to change. What may not move is
   everything the restyle touches on its way past: the registry entry a station's identity
   lives in, the panel that reads it, and the qualifiers F03's acceptance put on a board.
   A circleMarker's options and a divIcon's HTML are the two things MR2 rewrites, and both
   are read by surfaces that have nothing to do with how a station looks.

   P1f's subway popup pin is NOT regenerated either. The popups are stage MR5, so a
   subway popup that changes in MR2 is a defect, and that claim stays an assertion. */

// The registry entry a subway station's identity lives in, minus the two object fields,
// which are pinned as the questions actually asked of them: is this still the marker the
// panel syncs to, is it still in the layer the feed strip toggles, and is it still drawn
// by the canvas on stationPane. MR2 rewrites the options of that very marker, so the
// claim is that rewriting them leaves the object, its layer and its pane alone.
const registryEntry = (page, key) =>
  page.evaluate((k) => {
    const entry = stationRegistry.find((row) => row.key === k);
    if (!entry) return null;
    const { marker, layer, ...rest } = entry;
    return {
      ...rest,
      markerIsCircleMarker: marker instanceof L.CircleMarker,
      markerInStationLayer: stationLayer.hasLayer(marker),
      markerLayerIsStationLayer: layer === stationLayer,
      // The OPTIONS pane is overlayPane (Leaflet's default for a vector layer) and the
      // drawing is on stationPane through the renderer, which is the distinction P1f's
      // station pin cannot show and the one the z-index depends on.
      markerOptionsPane: marker.options.pane ?? null,
      markerRendererPane: marker.options.renderer?.options?.pane ?? null,
      hasPopup: !!marker.getPopup(),
    };
  }, key);

test("P2a. a subway station's registry entry, field by field", async ({ page }) => {
  await boot(page);
  pin("registry/subway", await registryEntry(page, "subway|127"));
});

/* The station panel's whole rendering for one subway station, read from the DOM after a
   search and a selection, exactly as a rider reaches it. Four surfaces in one pin because
   they are one act: the results list, the detail heading, the arrivals rows and the words
   spoken into #stations-announce. MR2 changes the station MARKER; the panel reads the
   registry entry beside it, and this is what says the two did not get confused. */
const panelState = (page) =>
  page.evaluate(() => {
    const text = (el) => (el?.textContent ?? "").trim().replace(/\s+/g, " ");
    return {
      results: [...document.querySelectorAll("#stations-results button.station-row")].map(text),
      detailHeading: text(document.querySelector("#stations-detail h3, #stations-detail .station-detail-name")),
      arrivals: [...document.querySelectorAll("#stations-detail ul.station-arrivals li")].map(text),
      spoken: text(document.getElementById("stations-announce")),
    };
  });

async function selectStation(page, query, rowText = null) {
  await page.locator("#stations-search").fill(query);
  // WHICH ROW, NAMED, when a query matches more than one station. "jamaica" matches the
  // AirTrain's Jamaica and the LIRR's, and the AirTrain row sorts first: taking .first()
  // pinned "Jamaica (AirTrain)" under the key panel/lirr, a pin that would have survived
  // any change to the surface it exists to watch. Measured before it was fixed, which is
  // the only reason this argument exists.
  const rows = page.locator("#stations-results button.station-row");
  const wanted = rowText ? rows.filter({ hasText: rowText }) : rows;
  await expect(wanted.first(), `no station row matching ${rowText ?? query}`).toBeVisible();
  await wanted.first().click();
  // The board is fetched, so the rows arrive a round trip later; the clock is paused, so
  // the driver's side is what advances it (the note at popupHtml says why).
  for (let i = 0; i < 80; i++) {
    const rows = await page.locator("#stations-detail ul.station-arrivals li").count();
    if (rows > 0) return;
    await page.clock.runFor(100);
  }
}

test("P2b. the station panel for a subway station, as a rider reaches it", async ({ page }) => {
  await boot(page);
  await selectStation(page, "times sq");
  pin("panel/subway", await panelState(page));
});

/* F03's qualifiers, on both surfaces, as a MEASURED golden.

   C2i already holds this world and holds it harder: it asserts twelve hand-written rows
   and the popup's exact markup, and it is the browser half of an acceptance whose other
   half is a backend test. So this pin is not there to catch something C2i would miss. It
   is a second witness of a different KIND: C2i says what the rows should read and would
   have to be edited to accept a change, and this says what they DID read before MR2 and
   fails without anyone editing anything. A stage that restyles the subway has no business
   near either one. */
test("P2c. F03's board qualifiers, on the panel and in the popup", async ({ page }) => {
  const board = require("./fixtures/f03_board_219.json");
  const prospect = { id: "219", name: "Prospect Av", lat: 40.8196, lon: -73.9015, routes: ["2", "5"] };
  await boot(
    page,
    (ctx) => {
      ctx.overrides.subwayStops = (route, fixtures) => json(route, [...fixtures.subwayStops(), prospect]);
      ctx.overrides.subwayArrivals = (route, fixtures) =>
        route.request().url().endsWith("/219") ? json(route, board) : json(route, fixtures.subwayArrivals());
    },
    { stationCount: 15 },
  );
  await selectStation(page, "prospect");
  const state = await panelState(page);
  pin("board/f03", {
    arrivals: state.arrivals,
    spoken: state.spoken,
    // The panel's own stale line, which each row speaking for itself is supposed to make
    // empty; a pin on its ABSENCE, because an element that reappears is the failure.
    panelStaleLines: await page.locator(".station-detail-stale").count(),
    qualifiersInPopup: await page.locator(".leaflet-popup-content .arr-qualifier").count(),
    popup: await page.locator(".leaflet-popup-content").innerHTML(),
  });
});

/* ---------------- P3: what stage MR3 is not allowed to change ----------------

   MR3 restyles the COMMUTER RAIL: casing plus line per branch in the feeds' own colours,
   the 10x10 paper square in place of the white circle, names from zoom 11, and railTag
   with the brief's 3.1 provenance states in place of the flat 16x16 rounded square. Three
   things sit right beside all of that and are NOT its to move, so they are written down
   here before a line of it is written.

   WHY THESE THREE AND NOT THE MARKS THEMSELVES, the same reason P2 gives: P1h, P1i, P1j
   and P1k already hold every rail mark byte for byte, and MR3 is the stage that
   deliberately moves them. Those goldens are regenerated and the before-and-after is
   recorded in docs/reviews/map-redesign-rounds.md, which is what "measured" means for a
   mark a stage exists to change. THE POPUP HALVES OF P1h, P1i AND P1k ARE NOT
   REGENERATED: the popups are stage MR5, so a rail popup that changes in MR3 is a defect,
   and that claim stays an assertion.

   What may not move is everything the restyle touches on its way past: the registry entry
   a rail station's identity lives in, the panel that reads it, the alerts that join to it,
   and the position ladder's five states in the F01 world. A divIcon's HTML and a
   circleMarker's options are what MR3 rewrites, and all four of those surfaces read the
   registry entry and the served row instead. */

// The registry entry a RAIL station's identity lives in. Shaped like P2a's but pinning the
// pane as DRAWN rather than as configured, because that is the invariant and the
// configuration is the thing MR3 changes: today the marker is a canvas circleMarker whose
// options.pane is Leaflet's overlayPane default and whose RENDERER puts it on stationPane;
// MR3 makes it an L.marker with pane "stationPane" and no renderer. Either way the station
// draws on stationPane, which is what the click order and the z-index depend on, so the
// pin holds the answer and not the route to it. The marker's CLASS is deliberately not
// pinned here: circleMarker to Marker is the restyle itself, and a pin on it would be a
// pin on the thing being changed (P2a could pin it because MR2 kept the subway a
// circleMarker).
const railRegistryEntry = (page, key) =>
  page.evaluate((k) => {
    const entry = stationRegistry.find((row) => row.key === k);
    if (!entry) return null;
    const { marker, layer, nameFor, ...rest } = entry;
    return {
      ...rest,
      // nameFor is a closure over the route-name index; what is pinned is the answer it
      // gives, because the panel's sentences are built from that and not from the function.
      nameForFirstRoute: typeof nameFor === "function" ? (nameFor((entry.routes ?? [])[0] ?? "1") ?? null) : null,
      hasPopup: !!marker.getPopup(),
      markerInItsLayer: !!layer && layer.hasLayer(marker),
      drawnOnPane: marker.options.renderer?.options?.pane ?? marker.options.pane ?? "overlayPane",
    };
  }, key);

test("P3a. a railroad station's registry entry, field by field", async ({ page }) => {
  await boot(page);
  // One per rail family, because the three reach registerStation from two different files
  // and MR3 rewrites both: LIRR and Metro-North from systems/railroad.js, NJ Transit from
  // systems/njt.js. Metro-North is in with LIRR to hold the systemLabel that spells the
  // agency out ("Metro-North", not "MNR"), which the panel says aloud.
  pin("registry/lirr", await railRegistryEntry(page, "LIRR|12"));
  pin("registry/mnr", await railRegistryEntry(page, "MNR|1"));
  pin("registry/njt", await railRegistryEntry(page, "NJT|109"));
});

test("P3b. the station panel for a railroad station, as a rider reaches it", async ({ page }) => {
  await boot(page);
  // The LIRR's Jamaica, named: the AirTrain has one too and its row comes first.
  await selectStation(page, "jamaica", "LIRR");
  const state = await panelState(page);
  // A PREMISE ASSERTION, because this pin is only about a rail station if it is a rail
  // station: the golden's own heading would read "(AirTrain)" otherwise and still look
  // like a filled-in pin.
  expect(state.detailHeading).toContain("LIRR");
  pin("panel/lirr", state);
});

/* THE ALERTS JOIN FOR A RAIL STATION, on both surfaces, in one pin.

   F11's rule is that a route-scoped alert reaches a station through the routes that call
   there, and that the map popup and the station panel say the SAME thing about the same
   station. Both halves are pinned here for LIRR Jamaica, with its own alert list rather
   than the shared stationAlertList (which many specs read and none of them expect to
   grow). Two alerts, one per path into the join:

     the STOP-scoped one reaches Jamaica directly, by its own id in the LIRR id space;
     the ROUTE-scoped one reaches it only through route 1, which no static routes list on
     this fixture carries (railroadStops serves no routes field at all, so the station's
     routes are []) and which railroadArrivalsLirr's board does carry. So it can arrive
     only through the arrivals side of F11's union, which is the half a restyle of the
     station MARKER could plausibly break by rebuilding the descriptor around it.

   A third alert on a route that does NOT serve Jamaica is in the list as the negative:
   the join has to leave it out, and a pin that only ever saw alerts it wanted would not
   notice a join that had started matching everything. */
const LIRR_STATION_ALERTS = [
  { id: "lirr-stop", system: "LIRR", header: "Jamaica platforms C and D are closed", description: null,
    effect: "NO_SERVICE", cause: "MAINTENANCE", routes: [], stops: ["12"],
    starts_at: fx.FROZEN_S - 600, ends_at: null },
  { id: "lirr-route-1", system: "LIRR", header: "[1] Babylon Branch is single-tracking", description: null,
    effect: "SIGNIFICANT_DELAYS", cause: "MAINTENANCE", routes: ["1"], stops: [],
    starts_at: fx.FROZEN_S - 600, ends_at: null },
  { id: "lirr-route-9", system: "LIRR", header: "[9] Port Washington is suspended", description: null,
    effect: "NO_SERVICE", cause: "MAINTENANCE", routes: ["9"], stops: [],
    starts_at: fx.FROZEN_S - 600, ends_at: null },
];

test("P3c. the alerts join for a railroad station, on the panel and in the popup", async ({ page }) => {
  await boot(page, (ctx) => {
    ctx.overrides.alerts = (route, fixtures) =>
      json(route, { ...fixtures.alerts(), alerts: LIRR_STATION_ALERTS });
  });
  await selectStation(page, "jamaica", "LIRR");
  const state = await panelState(page);
  expect(state.detailHeading).toContain("LIRR"); // the same premise P3b states
  const popup = await popupHtml(page, "lirr station");
  const measured = {
    // The panel writes its alerts as ELEMENTS (stations.js), the popup as HTML
    // (alertsBlockHtml), so the two selectors differ and both are read: F11's claim is
    // that the two surfaces say the same thing, and a pin on one of them could not see
    // the other stop saying it.
    panelAlerts: await page.locator("#stations-detail ul.station-alerts li").allInnerTexts(),
    panelArrivals: state.arrivals,
    spoken: state.spoken,
    popupAlerts: await page.locator(".leaflet-popup-content .alert-block .alert-row").allInnerTexts(),
    popup,
  };
  // PREMISE ASSERTIONS. Both alerts must be there and the third must not, or this pin is
  // measuring a join that matched nothing (or everything) and would hold either way.
  expect(measured.panelAlerts).toHaveLength(2);
  expect(measured.panelAlerts.join(" ")).toContain("platforms C and D");
  expect(measured.panelAlerts.join(" ")).toContain("single-tracking");
  expect(measured.panelAlerts.join(" ")).not.toContain("Port Washington");
  pin("alerts/lirr", measured);
});

/* THE POSITION LADDER'S FIVE STATES IN THE F01 WORLD, by count and by words.

   The ladder is the backend's (feeds/railroad._position_ladder, five steps: reported
   unqualified, estimated, reported qualified, placed, and nothing at all), and every step
   above the fifth arrives on this page as a marker whose words positionQualifier chooses.
   MR3 redraws every one of those markers. What it may not do is change how MANY trains
   are in each state, or what any of them SAYS, and the two are pinned together because a
   restyle that broke the reading of `provenance` could keep the counts and change the
   words, or keep the words and move a train between states.

   THE WORDS ARE COLLECTED AS A SET PER STATE, not per train: the ages inside them are the
   capture's own and there are many, so the set is what is invariant and the pin stays
   readable. The fifth state has no marker to read, so it is pinned as the count the status
   line reports, which is the only place it exists on this page (24, from the capture's own
   LIRR block).

   This is a SECOND WITNESS beside smoke.spec.js's C2j, and of a different kind, exactly as
   P2c is beside C2i: C2j asserts the acceptance and would have to be edited to accept a
   change, this says what the world DID read before MR3 and fails without anyone editing
   anything. */
const f01Ladder = (page) =>
  page.evaluate(() => {
    const now = Date.now() / 1000 - (minClockOffset ?? 0);
    const byKind = {};
    for (const record of railroads.values()) {
      const t = record.latest;
      const position = railroadPosition(t, now);
      // The ladder's own vocabulary, keyed by the kind positionQualifier answers with
      // ("" for an unqualified reported fix) plus the provenance that reached it, so a
      // train that changed step would land in a different bucket rather than blend in.
      const key = `${t.provenance}/${position.kind || "unqualified"}`;
      (byKind[key] ??= { count: 0, words: new Set(), systems: new Set() });
      byKind[key].count += 1;
      byKind[key].words.add(position.words);
      byKind[key].systems.add(t.system);
    }
    return Object.fromEntries(
      Object.keys(byKind)
        .sort()
        .map((k) => [
          k,
          {
            count: byKind[k].count,
            words: [...byKind[k].words].sort(),
            systems: [...byKind[k].systems].sort(),
          },
        ]),
    );
  });

test("P3d. the F01 world's ladder: how many trains are in each state, and what each one says", async ({ page }) => {
  await boot(page, (c) => {
    c.overrides.railroads = (route) => {
      const body = JSON.parse(JSON.stringify(require("./fixtures/f01_railroads.json")));
      // Metro-North's own poll aged six minutes, the same world P1b pins the status line
      // in: it is the one world where the withheld count and the undated clause are both
      // on the line, so the fifth state's count can be read off it.
      body.systems.MNR.fetched_at = fx.FROZEN_S - 360;
      return json(route, body);
    };
  }, { railroadCount: 136 });
  pin("ladder/f01", {
    drawn: await page.evaluate(() => railroads.size),
    states: await f01Ladder(page),
    // The fifth state, which has no marker: the count the status line carries for the
    // trains the ladder drew nothing for.
    statusTail: await statusTail(page),
  });
});

/* ---------------- P4: what stage MR4 is not allowed to change ----------------

   MR4 restyles the four families that are left: PATH (lines, station dots, the diamond),
   the ferry (dashed routes, dock dots, the hull), AirTrain (the gray dashed guideway and
   the commuter square) and the buses (the arrow and the dot at a muted hue). Then it
   releases the dark theme: `#theme-toggle` loses its `hidden` attribute and every canvas
   mark drawn from a RESOLVED token gets restyled in place on a swap.

   WHAT IS ALREADY PINNED AND STAYS PINNED, held by the pins above rather than repeated
   here, because a second copy of a golden is a second thing to keep true:

     every popup's HTML, all eight systems   P1f through P1n, the `popups/*` keys
     the subway and rail marker HTML          P1f, P1h, P1i, P1j, P1k, the `markers/*` keys
     the F01 world's counts and words         P1b (the status clause) and P3d (the ladder)
     the C6-series dimming, PATH              tests/contract/staleness.contract.spec.js C6e3

   THE POPUPS ARE THE SHARP ONE. MR4 changes the MARK of four families and none of their
   popups, which are stage MR5's; `markPin` already writes `markers/<system>` and
   `popups/<system>` as separate golden keys, so the four mark goldens are deliberately
   regenerated by this stage and all eight popup goldens must not move. A regeneration that
   takes the popups with it would be MR4 quietly doing MR5's work, and the split is what
   makes that visible in the diff.

   WHAT IS NEW HERE, because nothing held it before:

     P4a  the census: every class a sentinel counts, and what it counts TODAY.
     P4b  the ferry's docked-and-dimmed compound, read off the drawn element.

   P4a EXISTS BECAUSE MR3 PAID FOR IT TWICE. That stage put ~300 markers and ~300 labels
   into classes other code was counting, and two sentinels silently changed meaning:
   `paintZoomBand` counted the new rail labels as subway ones (caught by a spec written for
   it), and six e2e specs counted the new rail STATION markers as vehicles (caught by CI,
   after the push, on a premise assertion that had nothing to do with the cross-link it was
   in). Both were the same defect: a count over a class the stage had widened. MR4 widens
   more of them, so the counts are written down BEFORE rather than reconstructed after. */

// EVERY SELECTOR A SENTINEL USES, AND THE FAMILY SPLIT UNDER IT. The counts alone would say
// a number moved; the split says which family moved it, which is the question the sentinel
// was actually asking. Canvas populations are counted through their layer groups, because a
// circleMarker has no element to select.
const classCensus = (page) =>
  page.evaluate(() => {
    const n = (sel) => document.querySelectorAll(sel).length;
    const group = (fn) => {
      try {
        const g = fn();
        return g && g.getLayers ? g.getLayers().length : null;
      } catch {
        return null;
      }
    };
    // The class each marker element actually carries, tallied, so a family arriving in or
    // leaving a shared class is one diff line rather than an arithmetic puzzle.
    const byClass = {};
    for (const el of document.querySelectorAll(".leaflet-marker-icon")) {
      const key = [...el.classList].filter((c) => c !== "leaflet-marker-icon" && c !== "leaflet-zoom-animated")
        .sort()
        .join(" ") || "(none)";
      byClass[key] = (byClass[key] ?? 0) + 1;
    }
    return {
      // The six specs' vehicle sentinel and the two halves it is made of.
      markerIcons: n(".leaflet-marker-icon"),
      vehicleSentinel: n(".leaflet-marker-icon:not(.rail-stn-marker)"),
      railStationMarkers: n(".rail-stn-marker"),
      markerIconsByClass: byClass,
      // paintZoomBand's two counts, and the rail band beside them.
      stnLabels: n(".stn-label"),
      stnLabelsSubway: n(".stn-label:not(.rail)"),
      stnLabelsRail: n(".stn-label.rail"),
      stnLabelsHub: n(".stn-label.hub"),
      stnLabelsHubSubway: n(".stn-label.hub:not(.rail)"),
      // The per-family marker classes specs reach for by name.
      trainMarkers: n(".train-marker"),
      busMarkers: n(".bus-marker"),
      pathMarkers: n(".path-marker"),
      ferryMarkers: n(".ferry-marker"),
      airtrainMarkers: n(".airtrain-marker"),
      // The canvas populations, which carry no element at all.
      canvas: {
        subwayStations: group(() => stationLayer),
        pathStations: group(() => pathStations),
        ferryDocks: group(() => ferryDocks),
        pathRouteLines: group(() => pathRouteLines),
        ferryRouteLines: group(() => ferryRouteLines),
        airtrainRouteLines: group(() => airtrainRouteLinesLayer),
        subwayRibbons: (() => {
          try {
            return subwayRibbons.length;
          } catch {
            return null;
          }
        })(),
      },
    };
  });

test("P4a. the census: every class a sentinel counts, and what it counts today", async ({ page }) => {
  await boot(page);
  pin("census/stock", await classCensus(page));
});

/* THE FERRY'S COMPOUND, WHICH IS TWO RULES MULTIPLIED AND NOT ONE STATE.

   A docked boat rides at FERRY_DOCKED_OPACITY (0.55) to read as parked, and the freshness
   contract dims a stale marker to STALE_MARKER_OPACITY (0.45). They COMPOUND: dimMarker
   takes the docked value as its `base` and markerOpacity multiplies, so a docked boat on a
   stale feed draws at 0.2475 and not at either number alone. The design's ferry paragraph
   says "docked boats opacity 0.55 (existing rule)", which is an instruction to keep the
   rule rather than to restate it, and MR4 rewrites the hull that rule is applied to.

   READ OFF THE ELEMENT, not off `marker.options.opacity`. The option is what the app
   intended and the inline style is what a rider sees, and the two are only the same while
   dimMarker is wired; MR3 learned that distinction the hard way in D3b. Both are captured
   so a divergence between them is a diff rather than a silence.

   THE FIXTURE ALREADY HAS ONE OF EACH: H2 is STOPPED_AT and H1 is under way, which is what
   makes "docked dims and under-way does not" assertable in the same breath as the compound. */
test("P4b. the ferry's docked-and-dimmed compound on a healthy feed", async ({ page }) => {
  await boot(page);
  const read = () =>
    page.evaluate(() =>
      Object.fromEntries(
        [...ferryBoatRecords.entries()].map(([id, record]) => {
          const el = record.marker.getElement();
          return [
            id,
            {
              status: record.latest.status ?? null,
              docked: (record.marker.getIcon().options.className ?? "").includes("ferry-docked"),
              option: record.marker.options.opacity ?? 1,
              drawn: el && el.style.opacity !== "" ? Number(el.style.opacity) : 1,
            },
          ];
        }),
      ),
    );
  const healthy = await read();
  // A PREMISE, because the whole pin turns on it: the fixture really does serve one docked
  // boat and two under way, so "docked rides lower" is a comparison and not a tautology.
  expect(Object.values(healthy).filter((b) => b.docked)).toHaveLength(1);
  expect(Object.values(healthy).filter((b) => !b.docked).length).toBeGreaterThan(0);
  pin("ferry/compound-healthy", healthy);
});

test("P4b2. the ferry's compound on a stale feed, which is where the two rules multiply", async ({ page }) => {
  /* A SECOND BOOT RATHER THAN A MUTATED PAGE, and that is round 4 of MR3's lesson applied
     before it could cost anything. The first draft of this pin reached into `sources.ferry`
     and moved `fetchedAt` back by ten minutes, then called refreshAll(); the next poll
     answered with the stock envelope and overwrote it, so the "stale" half came back byte
     for byte identical to the healthy half. A pin whose two halves cannot differ is a pin
     that cannot fail, which is one of the four defect shapes this phase is watching for, and
     it was caught only because the golden was READ after it was written.

     So the world is stale from the first byte: the envelope's own fetched_at is 200 s behind
     its served_at, which is the shape smoke.spec.js already uses to age a feed, and every
     source is aged together because a lone stale ferry would also change the status line. */
  const stale = (data, key) => ({
    fetched_at: fx.FROZEN_S - 200,
    feed_timestamp: fx.FROZEN_S - 205,
    served_at: fx.FROZEN_S,
    [key]: data,
  });
  await boot(page, (c) => {
    c.overrides.ferry = (route) => json(route, stale(fx.ferry().boats, "boats"));
  });
  const read = () =>
    page.evaluate(() =>
      Object.fromEntries(
        [...ferryBoatRecords.entries()].map(([id, record]) => {
          const el = record.marker.getElement();
          return [
            id,
            {
              status: record.latest.status ?? null,
              docked: (record.marker.getIcon().options.className ?? "").includes("ferry-docked"),
              option: record.marker.options.opacity ?? 1,
              drawn: el && el.style.opacity !== "" ? Number(el.style.opacity) : 1,
            },
          ];
        }),
      ),
    );
  const measured = await read();
  /* THE COMPOUND ITSELF, ASSERTED AND NOT ONLY RECORDED. A golden says "this is what it
     was"; these two lines say what it MEANS, so a future reader does not have to multiply
     0.55 by 0.45 to see that the docked boat is carrying both rules and the under-way boat
     only one. This is the half that dies if MR4 lets the docked rule fall out. */
  const docked = Object.values(measured).find((b) => b.docked);
  const underWay = Object.values(measured).find((b) => !b.docked);
  expect(docked, "the fixture must serve a docked boat").toBeDefined();
  expect(docked.drawn, "docked AND stale is both rules multiplied").toBeCloseTo(0.55 * 0.45, 5);
  expect(underWay.drawn, "under way AND stale is the contract's dimming alone").toBeCloseTo(0.45, 5);
  pin("ferry/compound-stale", measured);
});
