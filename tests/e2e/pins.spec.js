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
const { measureMarkContrast, bestPerFamily, alphaPaints } = require("./contrast");
// MR5: the marker table, the popup-closing sweep and the fourteen stock surfaces moved into
// popup.js when popups.spec.js needed the same three; their comments moved with them.
const { inPage, closeAllPopups, STOCK_SURFACES } = require("./popup");

const GOLDEN = path.join(__dirname, "fixtures", "mr_pins.json");
const REGENERATE = !!process.env.MR_PINS_REGENERATE;

const readGolden = () => (fs.existsSync(GOLDEN) ? JSON.parse(fs.readFileSync(GOLDEN, "utf8")) : {});

// Workers run in parallel, so a regeneration read-modify-write can lose a sibling's
// entry. Sorted keys on the way out keep the file stable whatever order they land in;
// a truncated file after a parallel regeneration is repaired by running it again, which
// is cheap and visible (the assert run that follows says which key is missing).
function pin(key, measured) {
  /* REGENERATION IS A DEVELOPER'S ACT AND NEVER CI'S. Measured while writing MR5's pins:
     nothing anywhere asserted this variable was unset, and the regenerate branch returns
     before `expect` ever runs. A shell with MR_PINS_REGENERATE exported therefore saw every
     pin in this file "pass", including MR5's premises and its coverage test, and a CI job
     that ever inherited it would have been green forever with an empty golden. */
  expect(
    !(process.env.CI && REGENERATE),
    "MR_PINS_REGENERATE is set in CI: every pin in this file would pass without asserting",
  ).toBe(true);
  const [head, tail] = key.split("/");
  /* AND THE KEY IS TWO LEVELS. `split("/")` destructures exactly two, so a three-level key
     writes to its second segment and the third is silently dropped: two such keys overwrite
     each other and the failure reads as an unrelated diff. Depth goes inside the value. */
  expect(key.split("/").length, `${key}: a pin key is at most two levels; nest depth in the value`)
    .toBeLessThanOrEqual(2);
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

test("P1e. every name the legend says, and only those", async ({ page }) => {
  /* THE LIST, not the markup, and now asserted as an EQUALITY IN ORDER rather than as a
     superset. It was a superset for four stages and the asymmetry was deliberate: MR1 restyled
     these rows and MR2 through MR4 replaced their glyphs, so the claim was "every name that was
     here is still here" and an addition was free (MR1 made one, the dimmed-vehicle row the
     freshness contract needed and this legend never had).

     THE RULING THAT OWNS THIS PANEL STRENGTHENED IT RATHER THAN RELAXING IT, and the reason is
     what a superset could not see. MR3 gave three railroads one tag and one commuter square, and
     six rail rows went on describing marks the app had stopped drawing; "additions only" made
     REPLACING them the one thing a stage could not do, so nothing did, for two stages. MR4 round 2
     replaces them by ruling, which means the golden moves once, here, with the five names it drops
     written into the ledger as the round's before.

     THE SUPERSET LIVED IN THE `.filter(...)`, NOT IN THE ASSERTION, and that is worth saying
     because it is the opposite of where a reader looks. This used to pin
     `names.filter((n) => golden.includes(n))`, so the golden only ever held the intersection of
     the panel and the list MR1 measured, and `pin` compared that filtered list to itself.
     `pin` has ALWAYS been an ordered deep equality (see its body above); the filter is what
     emptied it of meaning. Dropping the filter is therefore the whole strengthening, and it also
     repairs a trap: regenerating the old form under MR_PINS_REGENERATE would have written
     `page INTERSECT old golden`, which for this round is the twelve survivors, silently leaving
     the panel's four newest sentences pinned by nothing at all.

     THE EXPLICIT EQUALITY BELOW IS FOR THE MESSAGE, not for the claim. `pin` already fails on any
     difference, but it says "legend/names moved; regenerate the golden and say why", which does
     not name the sentence that left. This one does, and mutation M61 records that `pin` alone
     catches a reordered row even with this line reverted.

     WHAT AN EQUALITY BUYS over the superset: a row removed AND replaced by a new one used to pass
     (the count was A1x's problem and the name was nobody's), a row quietly reworded used to pass
     as an addition, and two rows swapping places used to pass. A superset could only ever catch a
     row that left without a successor. */
  await boot(page);
  const names = await accessibleNames(page, "#legend .legend-row, #legend .legend-note");
  pin("legend/names", names);
  const golden = readGolden().legend?.names ?? null;
  if (golden) {
    expect(names, "the Key panel's rows are this list, in this order, and nothing else").toEqual(golden);
  }
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
// string exists. The marker table and the closing sweep this reads through are in popup.js.
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
  /* THE POPUP HALF IS RETIRED HERE, and the `spec.popups` lists are kept because they are
     still the roster of surfaces P5 opens. MR1 through MR4 pinned each popup's HTML byte for
     byte and said three times that "a popup that moves here is a defect"; that held for four
     stages and is what let them restyle the map without touching the words. MR5 changes this
     markup on purpose, so the same pin would now be a pin on the thing being changed. What
     replaces it is P5: the same surfaces, read as TEXT. The retired goldens are in the ledger
     as this round's before. */
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
    const { marker, layer, nameFor, colorFor, ...rest } = entry;
    const firstRoute = (entry.routes ?? [])[0] ?? "1";
    return {
      ...rest,
      // nameFor is a closure over the route-name index; what is pinned is the answer it
      // gives, because the panel's sentences are built from that and not from the function.
      nameForFirstRoute: typeof nameFor === "function" ? (nameFor(firstRoute) ?? null) : null,
      /* AND colorFor's ANSWER, for the same reason and because ruling R1 is what put it here: the
         railroad's panel chip used to hash its route id while the map and the popups read the
         agency's published colour, and this is the only place on the live page where that resolver's
         answer is measured. A function spread into a pin serialises as nothing, so the golden would
         have grown a key that says nothing; the value is the point. */
      colorForFirstRoute: typeof colorFor === "function" ? (colorFor(firstRoute) ?? null) : null,
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

/* ---------------- P5: the popup pins, INVERTED ----------------

   MR1 through MR4 pinned every popup's HTML byte for byte, and said so three times over:
   "the popups are stage MR5, so a popup that moves here is a defect". That claim has held
   through four stages and it is what let those stages restyle the map without touching the
   words. MR5 is the stage that changes the markup ON PURPOSE, so a markup pin is now a pin
   on the thing being changed, which is the error the header of this file exists to warn
   about.

   SO THE PINS INVERT. What is pinned from here is what a rider READS: every string, per
   system, per state, extracted as TEXT rather than as markup, taken before any restyle and
   held after. The retired `popups/*` goldens are kept in the ledger as this round's before,
   and the diff that removes them is the record that they were removed deliberately.

   AND THE INVERSION IS MORE DANGEROUS THAN THE PINS IT REPLACES, for one structural reason
   worth stating at the top: in MR1 through MR4 these goldens were ASSERT-ONLY, so a broken
   reader FAILED. MR5 regenerates them by design, so every emptiness that used to be loud
   becomes silent the moment it is written into the golden and matches itself forever. Every
   premise assertion below exists for that, and each one is outside `pin()` on purpose. */

/* THE READER. Three views of one subtree, and not one of them is `textContent` or
   `innerText`.

   NOT `textContent`: it inserts nothing at element boundaries. The subway train popup is a
   flat chain of `<br>`-separated fragments, so its textContent is
   "1 trainNext stop: Times Sq-42 StNorthbound...": a dropped `<br>` is invisible and two
   words fuse into a run-on that a pin cannot tell from the real thing.

   NOT `innerText`: it is computed from rendered boxes, so it moves when `display` moves.
   MR5's whole job is changing `display` (the .kv grid, the .arr grid, the .pk flex row), so
   a pin on innerText would be a pin on the thing being changed, again.

   SO: a walk we control. Every text node in document order, normalised per node, joined by
   exactly one space. Re-wrapping, re-nesting and re-ordering ELEMENTS that keeps the word
   order gives an identical string; a lost, added or altered WORD does not.

   THREE VIEWS, because a popup says things in three ways:
     seen    every text node an EYE reads: the walk with `.visually-hidden` subtrees removed,
             including a mark's decorative letter, which an eye does read.
     spoken  every text node a SCREEN READER reads: the walk with aria-hidden subtrees removed
             instead. MR5 puts the map's own marks inside `.pt`, and railTagSvg carries
             aria-hidden, so the two diverge in this stage and the divergence is the content.
     labels  text that exists ONLY in an attribute. Today that is exactly one string, the
             dock's title="Wheelchair accessible": the glyph is in `seen`, the WORDS are in
             neither view, and a reader of text nodes alone would lose them silently.

   THE SECONDS ARE REDACTED, and this is the one normalisation past whitespace. An age under
   120s renders in SECONDS (humanizeAge), and the settle loop advances a paused clock, so a
   live feed's age is "1s" or "2s" depending on how many round trips an arrivals fetch took.
   A raw pin on it is flaky from the first run. `smoke.spec.js` already takes this exact
   escape for the same reason (a regex on ", as of \d+s ago" rather than a literal), so the
   digits become {n} here and the SHAPE is asserted separately below. Minutes are left alone:
   they are stable under the frozen clock and they are most of the vocabulary. */
/* THE READER ITSELF, AS ONE STRING WITH TWO LOCATORS IN FRONT OF IT.

   IT WAS TWO IMPLEMENTATIONS AND THAT COST A WRONG GOLDEN. P5a reads a surface by its MARKERS name
   and P5c reads a railroad by its registry key, so P5c had its own inlined copy of the walk, the
   normaliser and the clones. When MR5 taught the reader to tell an eye's view from a screen reader's,
   only one copy learned: P5c regenerated four goldens whose `seen` contained "Live · {n}s", a string
   no rider sees, and it looked exactly like a real capture. One reader, two ways of finding its
   `content`, and neither can drift from the other now.

   `content` IS THE CONTRACT between the locator and the body: the locator declares it and returns
   null if there is no popup, the body below reads it. */
const POPUP_READER = `
  const norm = (s) =>
    String(s).replace(/[\\u00a0\\u202f]/g, " ").replace(/\\s+/g, " ").trim()
      .replace(/\\b\\d+s\\b/g, "{n}s");
  const walk = (root) => {
    const out = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = norm(n.nodeValue);
      if (t) out.push(t);
    }
    return out.join(" ");
  };
  /* TWO CLONES, ONE PER READER, and MR5 is the stage that made the difference real. The seen view
     used to be the raw walk, which was harmless while nothing in a popup was visually hidden. Ruling
     Q2's freshness footer changes that on purpose: in the LIVE state it has no visible words and says
     "Live, 12s" through A1's visually-hidden class, because this app has no word for live on any
     surface (memo D9) and a screen reader still needs the state. A raw walk put that string in the
     seen view, and the golden read as though a rider saw it, which is the one sentence memo D9
     forbids recorded as though it shipped.
     So each view removes what ITS reader cannot get: seen drops visually-hidden, spoken drops
     aria-hidden, and the footer is the first thing in this app that lands in exactly one of them.
     NO BACKTICKS IN HERE: this block is inside one, and a pair of them closed it early and broke the
     file's parse while this very comment was being written. */
  const visible = content.cloneNode(true);
  for (const h of visible.querySelectorAll(".visually-hidden")) h.remove();
  const quiet = content.cloneNode(true);
  for (const h of quiet.querySelectorAll('[aria-hidden="true"]')) h.remove();
  const labels = [...content.querySelectorAll("[title],[aria-label]")]
    .map((e) => norm(e.getAttribute("title") ?? e.getAttribute("aria-label")))
    .filter(Boolean);
  return { seen: walk(visible), spoken: walk(quiet), labels };
`;

const CONTENT_OF_MARKER = `
  const popup = MARKERS[which]().getPopup(), el = popup && popup.getElement();
  const content = el && el.querySelector(".leaflet-popup-content");
  if (!content) return null;
`;

const CONTENT_OF_RAILROAD = `
  const popup = railroads.get(which).marker.getPopup(), el = popup && popup.getElement();
  const content = el && el.querySelector(".leaflet-popup-content");
  if (!content) return null;
`;

const popupStrings = (page, name) => page.evaluate(inPage(CONTENT_OF_MARKER + POPUP_READER), name);

// The same reader, for a railroad read by its registry key: P5c's ladder world names its surfaces
// by position KIND and looks each one up in `railroads`, because the F01 capture's ids are not
// stable across states and MARKERS cannot hold them.
const railroadStrings = (page, key) => page.evaluate(new Function("which", CONTENT_OF_RAILROAD + POPUP_READER), key);

/* THE ROOTS OF THE POPUP CALL GRAPH, READ OFF LIVE OBJECTS rather than off a list.

   A vehicle popup's content is the function `bindPopup(() => ...)` stored on the popup; a
   station popup's renderer is the descriptor `openStation.render`, built on popupopen. Both
   are harvested from the objects the app actually built, during the very run that captures
   the text, so a renderer that arrives in a later stage is discovered rather than declared.
   A LIST here would be the circularity the coverage test exists to avoid. */
const popupRoots = (page, name) =>
  page.evaluate(
    inPage(`
      const out = [];
      const popup = MARKERS[which]().getPopup();
      const bound = popup && popup._content;
      if (typeof bound === "function") out.push(String(bound));
      if (typeof openStation?.render === "function") out.push(String(openStation.render));
      return out;
    `),
    name,
  );

/* One surface: open it, let it settle, read the three views AND the roots, close it.

   THE SETTLE LOOP EXCLUDES THE LOADING LINE EXPLICITLY. `popupHtml` above settles on two
   equal non-empty reads and survives only because the loading HTML differs from the settled
   HTML on the next read. Extracted TEXT is shorter and more stable than HTML, so a loading
   state is more likely to read equal twice; "Loading arrivals" would then become the pin. */
/* MR5: AND `close` IS AN OPTION, because P5d reads a SECOND view of the same open popup. The
   residue assertion compares a tree walk against a selector read, and the two have to be of one
   render: closing in between leaves a detached container whose content is no longer the one the
   walk saw. Everything else still closes, because a world with two popups open is a world where
   `.leaflet-popup-content` is not unique (the note at the top of tests/e2e/popup.js). */
async function popupTextOf(page, name, { close = true } = {}) {
  await closeAllPopups(page);
  await page.evaluate(inPage("MARKERS[which]().openPopup();"), name);
  let previous = null;
  let settled = null;
  for (let i = 0; i < 80; i++) {
    const now = await popupStrings(page, name);
    const key = now && JSON.stringify(now);
    if (key && key === previous && !/Loading arrivals/.test(now.seen)) {
      settled = now;
      break;
    }
    previous = key;
    await page.clock.runFor(100);
  }
  expect(settled, `${name}: the popup never settled (last seen: ${previous})`).not.toBeNull();
  const roots = await popupRoots(page, name);
  if (close) await closeAllPopups(page);
  return { ...settled, roots };
}

/* Every surface of a world, with the premise assertions that keep an empty read from
   becoming the golden. T4 in the round's trap list: `expect({}).toEqual({})` passes and
   `expect("").toEqual("")` passes, so a reader whose selectors all miss regenerates nothing
   and then matches nothing forever. MR5 renames every selector in this panel at the same
   moment it regenerates these, which is exactly when that happens. */
async function captureWorld(page, surfaces) {
  const out = {};
  const roots = new Set();
  for (const which of surfaces) {
    const m = await popupTextOf(page, which);
    expect(m.seen.length, `${which}: nothing was read from the popup`).toBeGreaterThan(12);
    expect(m.spoken.length, `${which}: nothing was read with aria-hidden removed`).toBeGreaterThan(8);
    for (const r of m.roots) roots.add(r);
    // `?? null` on every field: a missing key and an `undefined` one are EQUAL to
    // toEqual (measured), and JSON.stringify drops undefined, so a slot that silently
    // failed to read would regenerate as absent and then match forever. null does fail.
    out[which] = { seen: m.seen ?? null, spoken: m.spoken ?? null, labels: m.labels ?? [] };
  }
  return { surfaces: out, roots: [...roots] };
}

test("P5a. every string a rider reads in the stock world, per system", async ({ page }) => {
  await boot(page);
  const world = await captureWorld(page, STOCK_SURFACES);
  pin("popupText/stock", world.surfaces);

  /* AND THE PREMISES THE PIN CANNOT CARRY, outside pin() so regeneration cannot swallow
     them. The first is the one that matters: a matrix whose states are all identical is a
     matrix that enumerated nothing, and P4b2 is the round where this repo paid for exactly
     that (a pin whose two halves came back byte-identical because the page overwrote the
     mutation). Fourteen surfaces that read the same string would be a broken reader. */
  const seen = STOCK_SURFACES.map((s) => world.surfaces[s].seen);
  expect(new Set(seen).size, "fourteen surfaces must not read as one string").toBe(STOCK_SURFACES.length);
  // The dock's wheelchair note lives only in a title attribute; a reader of text nodes
  // alone loses it, so its presence is the witness that `labels` is doing work at all.
  expect(world.surfaces["ferry dock"].labels).toContain("Wheelchair accessible");
  // And the seconds redaction is a claim about the reader, so it is asserted rather than
  // trusted: no surface may carry a raw seconds age into the golden.
  for (const s of STOCK_SURFACES) {
    expect(world.surfaces[s].seen, `${s}: a raw seconds age reached the golden`).not.toMatch(/\b\d+s\b/);
  }

  /* AND EVERY STATION BOARD'S KICKER DRAWS THE ROUTES CALLING THERE (ruling R3), asserted here
     because a pin cannot carry it: an empty right-hand slot regenerates as an empty string and then
     matches forever, which is trap T4 in this file's own words. Three of these five boards had no
     routes in the fixtures to draw when the ruling landed (the railroad's and PATH's stops payloads
     carried no `routes` field, though both endpoints serve one), so without this assertion the new
     pins would have been pins of nothing at all.

     A COUNT PER BOARD AND NOT A TOTAL, so a board that stops drawing them cannot be hidden by
     another that draws three. The AirTrain station is deliberately absent: it has branches rather
     than routes, and the ruling names five boards. */
  const kickerMarks = async (which) => {
    // THROUGH THE SETTLING READER, not a bare openPopup: a station popup opens on "Loading
    // arrivals" and its board arrives a fetch later, and the loading state has no kicker at all.
    // Measured, as this assertion failing with 0 on a board whose golden carries the tag.
    await popupTextOf(page, which, { close: false });
    const count = await page.locator(".leaflet-popup-content .pk .pmark").count();
    await closeAllPopups(page);
    return count;
  };
  for (const which of ["subway station", "lirr station", "mnr station", "njt station", "path station", "ferry dock"]) {
    expect(await kickerMarks(which), `${which}: its kicker draws no route marks`).toBeGreaterThan(0);
  }
  await closeAllPopups(page);
});

/* THE SECOND WORLD, AND THE ONE THE BRIEF'S "per state" ACTUALLY ASKS FOR. The stock world
   has one position state per system, because it has one train per system. F01 is the ladder
   world: 136 railroad trains across every step of the backend's position ladder, which is
   where `positionQualifier`'s whole vocabulary is on screen at once. P3d already pins the
   ladder's COUNTS and the words as a model; this pins what the POPUPS of those states read,
   which is a different claim: a restyle can keep the model and stop rendering it.

   ONE TRAIN PER KIND, CHOSEN BY THE APP rather than by key. The fixture's ids are not stable
   across states, so the surface roster is built in the page by asking each record what kind
   its position is and taking the first of each. A kind that stops appearing changes the
   roster and fails the pin by absence, which is the right failure. */
const f01Surfaces = (page) =>
  page.evaluate(() => {
    const now = Date.now() / 1000 - (minClockOffset ?? 0);
    const first = {};
    for (const [key, record] of railroads) {
      const kind = railroadPosition(record.latest, now).kind || "unqualified";
      if (!first[kind]) first[kind] = key;
    }
    /* AND ONE MORE SURFACE, BY SYSTEM RATHER THAN BY KIND, which MR5 needed and P5b found. This
       world already backdates Metro-North's fetched_at by six minutes, so MNR's FEED is stale in it
       while LIRR's is live, and the roster above is LIRR-only by accident of iteration order: it
       takes the first train of each position kind and LIRR's come first. Ruling Q2's footer prints
       the feed's state, so without an MNR surface here no pinned world rendered a STALE footer at
       all, and P5b reported "As of" as a rider-visible literal that no pin covered. Pinning a world
       that renders it is the option that map's own instructions prefer over a waiver.
       KEYED SEPARATELY, not folded into `first`, because it is a different axis: the four above
       vary the POSITION's provenance on one live feed, and this one varies the FEED under an
       otherwise ordinary train. Naming it for the axis keeps the golden readable. */
    for (const [key, record] of railroads) {
      if (record.latest.system === "MNR") {
        first["mnr, stale feed"] = key;
        break;
      }
    }
    return Object.fromEntries(Object.keys(first).sort().map((k) => [k, first[k]]));
  });

test("P5c. the F01 ladder's popups, one per position state", async ({ page }) => {
  await boot(page, (c) => {
    c.overrides.railroads = (route) => {
      const body = JSON.parse(JSON.stringify(require("./fixtures/f01_railroads.json")));
      body.systems.MNR.fetched_at = fx.FROZEN_S - 360;
      return json(route, body);
    };
  }, { railroadCount: 136 });

  const roster = await f01Surfaces(page);
  expect(Object.keys(roster).length, "the ladder world must show more than one position state")
    .toBeGreaterThanOrEqual(5);
  // AND THE SECOND AXIS IS REALLY IN IT, because a roster that silently lost the stale-feed surface
  // would take "As of" out of the golden and P5b would report it uncovered again, one stage later.
  expect(Object.keys(roster), "the stale-feed surface is what pins a stale footer").toContain("mnr, stale feed");

  const out = {};
  const roots = new Set();
  for (const [kind, key] of Object.entries(roster)) {
    await closeAllPopups(page);
    await page.evaluate((k) => railroads.get(k).marker.openPopup(), key);
    let previous = null;
    let settled = null;
    for (let i = 0; i < 40; i++) {
      // THE SHARED READER, not a second copy of it. This loop used to inline its own walk and its
      // own normaliser, and when the reader learned to tell an eye's view from a screen reader's,
      // only the other copy learned: four goldens here regenerated with a string no rider sees.
      const now = await railroadStrings(page, key);
      const stamp = now && JSON.stringify(now);
      if (stamp && stamp === previous) { settled = now; break; }
      previous = stamp;
      await page.clock.runFor(100);
    }
    expect(settled, `${kind}: the ladder popup never settled`).not.toBeNull();
    expect(settled.seen.length, `${kind}: nothing was read`).toBeGreaterThan(12);
    const bound = await page.evaluate((k) => {
      const c = railroads.get(k).marker.getPopup()?._content;
      return typeof c === "function" ? String(c) : null;
    }, key);
    if (bound) roots.add(bound);
    out[kind] = { seen: settled.seen ?? null, spoken: settled.spoken ?? null };
  }
  await closeAllPopups(page);

  // The states must READ differently, or the roster enumerated one thing five times.
  const distinct = new Set(Object.values(out).map((v) => v.seen));
  expect(distinct.size, "every ladder state read the same string").toBe(Object.keys(out).length);
  pin("popupText/f01", out);
});

/* THE COVERAGE TEST, and it is the hardest claim in the stage: that every rider-visible
   string in a popup is pinned.

   IT IS NOT A LIST CHECKING ITSELF. The inventory is built by walking the popup call graph
   from roots read off LIVE OBJECTS (popupRoots above), taking each function's own source
   through Function.prototype.toString, and extracting the literals from it. That works here
   and only here because this app is BUILDLESS: package.json says so in its own first line,
   the page is plain ordered <script> tags, and every popup builder is therefore a top-level
   function on globalThis. `String(fn)` is the real source of the real function.

   WHAT IT PROVES: no literal in the popup call graph is absent from every pin without a
   written reason. WHAT IT CANNOT PROVE, stated here rather than discovered later: a lost
   DATA string (a train number, a station name, a headsign) is `${...}` and invisible to it.
   Only the pins themselves see those. This is a supplement to P5a, never a substitute. */
/* TWO MAPS, NOT ONE, BECAUSE THEY MEAN DIFFERENT THINGS and a single list would hide the
   difference. The first is "this is not text a rider reads". The second is "this IS text a
   rider reads, and no world pinned here renders it" - a backlog with reasons, visible in
   every diff, and the honest answer to a coverage claim that cannot yet be total. A literal
   in neither map and in no pin fails the test. */
const NOT_RIDER_TEXT = {
  "America/New_York": "an Intl.DateTimeFormat timeZone argument",
  "en-US": "an Intl.DateTimeFormat locale argument",
  "scheduled position, no GPS":
    "positionQualifier's SPOKEN form, which reaches a marker's accessible name (positionClause) " +
    "and never a popup. a11y's name specs are its witness, not a popup pin",
  /* MR5, ruling Q1. positionQualifier's COMPACT form, which after Q1 has no reader at all: the
     railroad popup was its only one, and it now renders `.words` through positionWords like
     every other popup. So no rider reads this string on any surface, which is why it is here rather
     than in UNREACHED_STATES: that map is for text a rider WOULD read in a state no world reaches,
     and there is no such state left for this one.
     THE FIELD IS NOT REMOVED AND THAT IS DELIBERATE. `.compact` is one of the three forms section
     3.2 of the freshness contract defines, positions.test.js holds the difference between it and
     `.words`, and deleting a contract form is an amendment to that contract rather than a stage's
     tidying. Recorded here so the next stage finds it named rather than guesses. */
  "scheduled (no GPS)":
    "positionQualifier's COMPACT form. After MR5's ruling Q1 it has no reader: the railroad popup " +
    "was the only surface that rendered it and now takes .words through positionWords. The " +
    "contract still defines the form and positions.test.js still pins it; no popup prints it",
  /* MR5: A CLASS NAME THAT READS AS PROSE, which is the one shape the candidate filter cannot tell
     from rider text. popupArrRowsHtml writes `class="n now"` on the countdown cell of a row that
     reads "now", and two words with a space in them is exactly what the filter looks for.
     NOT SPELLED AROUND IN THE BUILDER. The alternative was `class="n${...}"` with " now" as the
     literal, which the filter would drop for having no space and no capital: that is hiding a
     string from the inventory by formatting, and the next class name with a space in it would be
     back. A waiver with a reason is the honest form, and it is cheap: the rider's word "now" is
     formatCountdown's and IS pinned, on every board world that has a row under 30 seconds. */
  "n now": "the countdown cell's class attribute when a row reads now, not a string anyone reads",
  /* RULING R1's SIDE EFFECT, and the honest way to read this block of five: they were always in the
     popup call graph and the crawler could not see them, because it deduplicated a walked function
     by its NAME AND SOURCE LENGTH while every root is named "<root>" (the note at the crawler has
     the measurement). Two of the app's seven vehicle popups were therefore never walked. Nothing
     below is new code; they are strings this test has been silent about since it was written. */
  STOPPED_AT: "a GTFS current_status value the ferry code switches on. The rider reads ferryStatusText's answer",
  IN_TRANSIT_TO: "the same enum, read by ferryStatusText and ferrySpeedKnots",
  INCOMING_AT: "the same enum",
  NJT: "railBranchCode's system key. The tag prints the branch CODE and the kicker prints POPUP_SYSTEM_WORDS' \"NJ Transit\"",
  "NJT|": "the station registry's key prefix, which the cross-link resolves a station by",
};

const UNREACHED_STATES = {
  "No trains": "an empty board. Every fixture station serves at least one arrival",
  "No boats": "an empty ferry board, same reason",
  "No AirTrain branch serves this station.": "an AirTrain station the branch table does not name",
  "schedule unavailable": "an AirTrain headway band the clock never lands in",
  "Metro-North": "railroadSystemLabel's fallback for a system with no label; the fixtures name both",
  Railroad: "railroadSystemLabel's last-resort label, same reason",
  "PATH route": "formatPathHead's fallback for a PATH route with no name; the fixture names all of them",
  "age unknown": "a prediction or position with no clock on a gated system",
  "live GPS, age unknown": "a reported fix with no clock on a gated system",
  ", age unknown": "the same clause, composed",
  "showing last known": "a retained row with neither its own clock nor a poll age",
  "showing last known, as of": "a retained row, which the F01 ladder world does not currently draw",
  "alerts may be out of date": "the stale-alerts hedge; P3c pins the alerts join, not this hedge",
  "prediction age unavailable": "boardSystemLine's second clause, on an undated contributor",
  /* MR5 (ruling Q2): the two footer states no pinned world reaches, and each for its own reason.
     The other two ARE pinned: "Live" by every stock surface, and "As of {age} ago" by the ladder
     world's "mnr, stale feed" surface, which exists because P5b reported that string uncovered. */
  "Not reporting":
    "the footer's state for a feed with no age at all, which is a feed that has never decoded. " +
    "Every fixture feed decodes on its first poll, so no world here reaches it",
  /* AND FOUR MORE FROM THE SAME REPAIR (see the five in NOT_RIDER_TEXT above): the ferry boat and
     the NJ Transit train popups were the two roots the crawler's length-keyed dedup dropped, so
     every state THEY have that no pinned surface renders was invisible here. Each names the spec
     that does draw it, because "no world here reaches it" is only honest when something else does. */
  "At dock":
    "a STOPPED_AT boat. The pinned ferry surface is H1, under way; smoke.spec.js 20 opens H2 and " +
    "asserts this string, and the contract tier's C6e5 measures the docked compound",
  "Arriving at dock": "an INCOMING_AT boat. No fixture boat carries that status at all",
  Unassigned:
    "a boat whose route_id joins nothing. The pinned boat has a route; smoke.spec.js 20 renders " +
    "this one and asserts the word",
  "NJ Transit route":
    "formatNjtHead's fallback for a route the route table cannot name. The pinned NJ Transit train " +
    "is route 9, which has a name; markers.spec.js reads \"NJ Transit route 17\" off route 17's " +
    "marker label, which is the same fallback on the surface that does render it",
  /* MR5: FOUND BY THE NEW SCANNER, not by a reviewer. The old extractor read a template's
     literal pieces and stopped at its interpolations, so a string inside one was invisible;
     this one scans an interpolation as code, and the first thing it found was a rider-visible
     fallback that has never been pinned in six stages. */
  "Unknown route":
    "busPopup's title for a bus the feed served with no route_id. Every bus in every fixture " +
    "carries one, so no world here reaches it; the marker's own name has the same gap " +
    "(busName's bare \"Bus\") and takes the same waiver by being the same state",
  Scheduled:
    "the footer's schedule-only state. The footer is a VEHICLE popup's line (it is " +
    "vehicleStaleLine restyled) and the only schedule-only feed is AirTrain, which has no " +
    "vehicles. A rider reads this word on the feed strip's tooltip, where chrome.spec.js D1a " +
    "holds it; no popup can render it",
};

/* ---------------- P5d: direction A of the coverage claim, the residue assertion ----------------

   THE CLAIM: every word a rider reads in a popup belongs to a NAMED SLOT of section 5's vocabulary.
   P5b is direction B (every literal in the call graph is pinned or declared); this is direction A,
   and the brief asks for both because either alone is circular.

   WHY IT IS NOT CIRCULAR. `seen` is the UNIVERSE: every text node in the popup's subtree, harvested
   by a tree walk that knows no class and no list. The slots are a PARTITION, read through named
   selectors. The assertion is that the partition is total, so a string rendered into a popup that no
   slot claims shows up as residue and fails. It cannot be satisfied by regenerating a golden,
   because it is not a golden comparison at all: nothing here is pinned.

   AND IT IS THE TEST THE VOCABULARY MADE POSSIBLE. Before MR5 a popup was a run of `<br>`-joined
   sentences with three classes between them, so "which slot is this word in" had no answer for most
   of the text. Now every word is in a kicker, a title, a label, a value, a bucket heading, a row
   cell, the footer, an alerts block, a cross-link, a board line, a muted note or an empty-board
   notice, and a thirteenth kind of text is a thing this test reports rather than a thing a reader
   has to notice.

   THE HIDDEN WORDS ARE OUT OF BOTH SIDES. `seen` drops `.visually-hidden` (ruling Q2's live footer
   says "Live · 12s" to a screen reader and nothing to an eye, unless ruling R2's suppression has
   taken the words out of the tree as well, which is what happens where the Position row already
   stated an age that old), so the slots drop it too: this is about what a rider READS. */
const SLOT_READER = `
  const norm = (s) =>
    String(s).replace(/[\\u00a0\\u202f]/g, " ").replace(/\\s+/g, " ").trim().replace(/\\b\\d+s\\b/g, "{n}s");
  /* EVERY BACKSLASH HERE IS DOUBLED, and that is not decoration. This block is a TEMPLATE LITERAL
     interpolated into another one by inPage, so a single backslash-s arrives in the page as a bare
     "s" (and NO BACKTICKS IN HERE either, for the reason the reader above says: a pair of them
     closed this literal early while this very comment was being written, twice in one stage):
     measured, the first draft of this reader read "Next stop" as "Next  top" and "Position" as
     "Po ition", because /s+/g replaced every letter s with a space. The same defect is recorded in
     the MR5 ledger against popups.spec.js D6i, whose first draft split colours on "s" and compared
     NaN to 4.5 forever. The residue report is what caught it here, which is the coverage test
     working on itself.
     THE NAMED SLOTS, which are section 5's classes plus the four the app has always had (a board's
     freshness line, a muted note, an empty-board notice, a cross-link). NO BACKTICKS IN HERE: this
     block is inside one. */
  const SLOTS = {
    kicker: ".pk > span",
    title: ".pt",
    label: ".kv .k",
    value: ".kv .v",
    bucket: ".dir",
    cell: ".arr > span",
    footer: ".fresh",
    alert: ".alert-block",
    crosslink: ".xlink",
    board: ".popup-stale",
    note: ".popup-sub",
    empty: ".arr-none",
  };
  /* THE SLOT'S TEXT IS ITS TEXT NODES, WALKED, not its textContent, and the rail tag is why: its
     agency glyph and its branch code are two adjacent <text> elements, so textContent reads "LBAB"
     where the universe's walk reads "L" and "BAB". Both sides harvest the same way and differ only
     in WHICH nodes they take, which is what makes this a partition of the universe rather than a
     second reading of it. */
  const walk = (root) => {
    const out = [];
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const t = norm(n.nodeValue);
      if (t) out.push(t);
    }
    return out.join(" ");
  };
  const visible = content.cloneNode(true);
  for (const h of visible.querySelectorAll(".visually-hidden")) h.remove();
  const out = {};
  for (const name of Object.keys(SLOTS)) {
    out[name] = [...visible.querySelectorAll(SLOTS[name])].map(walk).filter(Boolean);
  }
  return out;
`;

const popupSlots = (page, name) => page.evaluate(inPage(CONTENT_OF_MARKER + SLOT_READER), name);

/* The residue, as a WORD MULTISET rather than a string comparison, because the slots are read in
   selector order and a popup's text nodes in document order, and the two differ wherever a slot
   nests (a train number inside a row's middle cell, a badge inside its first). What the claim needs
   is that no word is left over, which is a counting question. */
function wordResidue(seen, claimed) {
  const pool = new Map();
  for (const w of claimed.split(" ").filter(Boolean)) pool.set(w, (pool.get(w) ?? 0) + 1);
  const left = [];
  for (const w of seen.split(" ").filter(Boolean)) {
    const n = pool.get(w) ?? 0;
    if (n > 0) pool.set(w, n - 1);
    else left.push(w);
  }
  return left;
}

test("P5d. every word a rider reads in a popup belongs to a named slot (direction A)", async ({ page }) => {
  await boot(page);
  // THE COMPARATOR FIRST, because a residue function that always returns [] would make every
  // assertion below vacuous, and "a test that cannot fail" is one of the four shapes this phase
  // keeps producing. One word injected into the universe must come back as residue, and a word
  // claimed twice but read once must not.
  expect(wordResidue("a b c", "a b c")).toEqual([]);
  expect(wordResidue("a zzz b", "a b")).toEqual(["zzz"]);
  expect(wordResidue("a a", "a")).toEqual(["a"], "counting, not membership: two reads need two claims");
  expect(wordResidue("a", "a a")).toEqual([]);

  for (const which of STOCK_SURFACES) {
    // BOTH VIEWS OF ONE RENDER, which is why the popup is left open across the two reads.
    const read = await popupTextOf(page, which, { close: false });
    const slots = await popupSlots(page, which);
    await closeAllPopups(page);
    expect(slots, `${which}: the slot reader found no popup`).not.toBeNull();
    const claimed = Object.values(slots).flat().join(" ");
    // A PREMISE PER SURFACE: at least two slots read something. A reader whose selectors all
    // missed would claim nothing, and then every word would be residue, which fails loudly; a
    // reader that claimed everything through one catch-all slot would pass and prove nothing.
    const filled = Object.entries(slots).filter(([, v]) => v.length);
    expect(filled.length, `${which}: only ${filled.length} slots read anything`).toBeGreaterThanOrEqual(2);
    expect(
      wordResidue(read.seen, claimed),
      `${which}: text in the popup that no named slot claims. Either it belongs in one of section ` +
        "5's classes, or the vocabulary needs a slot for it and this test needs to know its name.",
    ).toEqual([]);
  }

  /* AND THE WHOLE THING PROVED AGAINST A REAL POPUP, not only against the comparator. Fourteen
     empty residues read exactly like fourteen readers that missed, which is the shape this phase
     keeps finding, so one unclaimed string is INJECTED into a rendered popup and the machinery has
     to report it. The injection goes in through the DOM rather than through a builder, so nothing
     in the app has to be broken to prove the test works.

     INJECTED AND READ IN ONE EVALUATE, WHICH IS A FLAKE FIXED RATHER THAN RECORDED. As three
     separate calls this failed twice in four full parallel runs, with the residue coming back EMPTY:
     the app rebuilds an open popup on its fifteen-second poll (`popup.update()`, and a re-skinned bus
     marker is re-bound outright), the poll is triggered by a clock this suite advances in the settle
     loops above, but the mocked fetch that answers it resolves in REAL time, so under worker
     contention it lands between the injection and the read and takes the injected node with it.
     Reproduced deterministically by forcing `getPopup().update()` between the two calls: the same
     assertion, the same empty array, the same message CI printed. Nothing can intervene inside one
     evaluate, so the race is gone rather than retried.

     AND BOTH READERS ARE THE SAME TWO SOURCES, wrapped in an IIFE each so their `norm` and `walk`
     do not collide. A third copy of either reader is the fifth defect shape this phase named, and it
     does not get created to fix a timing bug. The node is removed before the evaluate returns, so no
     later read in this file can see it. */
  const injected = await page.evaluate(
    inPage(`
    MARKERS[which]().openPopup();
    const el = MARKERS[which]().getPopup().getElement();
    const content = el.querySelector(".leaflet-popup-content");
    const planted = Object.assign(document.createElement("div"), { textContent: "zzq unclaimed" });
    content.appendChild(planted);
    const seen = (() => { ${POPUP_READER} })().seen;
    const slots = (() => { ${SLOT_READER} })();
    planted.remove();
    return { seen, slots };
  `),
    "bus",
  );
  expect(
    wordResidue(injected.seen, Object.values(injected.slots).flat().join(" ")),
    "a string in a popup that no slot claims must be reported, or this test proves nothing",
  ).toEqual(["zzq", "unclaimed"]);
  await closeAllPopups(page);
});

test("P5b. every rider-visible literal in the popup call graph is pinned, or has a reason", async ({ page }) => {
  await boot(page);
  const world = await captureWorld(page, STOCK_SURFACES);

  /* THE NON-VACUITY PREMISE, and it is the single most important line in this test. A door
     that recorded nothing leaves the closure empty, the inventory empty and this test
     passing with nothing in it, which reads as the strongest evidence in the file. */
  expect(world.roots.length, "no popup renderer was discovered: the coverage test would be vacuous")
    .toBeGreaterThanOrEqual(8);

  /* THE SCANNER'S OWN CASES, written here and executed in the page, because a heuristic is only
     as good as the constructs it has been shown. Each is a shape this scanner has been WRONG about
     once: the first two are the false positives that retired its regex-plus-stripper predecessor
     (a template nested in an interpolation, and a regex carrying a quote), the next two are the
     reviewer's reproduction of the keyword bug (a regex after `return` and after `case`, each with
     a quote in it, which lost the prose around them), and the last is an ordinary division, which
     must NOT be read as a regex. \u0001 is where an interpolation was. */
  const SCANNER_CASES = [
    [
      "function t(x) { return `<p>${x ? `<b>${x}</b>` : \"ok now\"}</p>`; }",
      ["<b>\u0001</b>", "ok now", "<p>\u0001</p>"],
    ],
    ['function m(s) { return s.replace(/\\s(?:width|height)="[^"]*"/g, ""); }', [""]],
    ['function f(x) { return /["]/.test(x) ? "ok now" : "not ok"; }', ["ok now", "not ok"]],
    ['function h(x) { switch (x) { case /a"b/.test(x): return "yes sir"; } }', ["yes sir"]],
    ['function d(a, b) { const r = a / b; return "divide fine " + r; }', ["divide fine "]],
  ];

  const scanned = await page.evaluate(({ roots, cases }) => {
    /* ONE SCANNER THAT READS THE SOURCE MODE BY MODE, and it replaced a comment stripper plus a
       regex in MR5, because that pair could not read the code this stage wrote.

       WHAT IT GOT WRONG, both measured on this run. A template literal nested inside another
       template's `${...}` ended the outer match at the INNER's opening backtick, so
       popupTitleHtml reported the fragment "${text ?" as rider prose. And a regex literal
       carrying a quote (popupMarkHtml's /\s(?:width|height)="[^"]*"/g) opened a string that ran
       to the next quote in the file, reporting "); return <span class=" as prose. Both are the
       same false-positive shape as the apostrophe in railroadPopup's comment that this test was
       written for ("station's"), which is why the answer is not another special case: a scanner
       that tracks which mode it is in reads all four correctly, and a regex over a stripped
       string will always be one construct behind the code.

       WHAT IT EMITS: one entry per string or template literal, with \u0001 where an
       interpolation was, so prose is broken at the boundaries a value fills rather than glued
       across them. An interpolation's contents are scanned as CODE, so a literal inside one is
       found rather than skipped (which is how "Unknown route" is reached at all: it lives in
       `${esc(b.route_id ?? "Unknown route")}`).

       THE REGEX-OR-DIVISION HEURISTIC is the standard one and it is honest about being one: a
       `/` opens a regex when the last significant character was an operator, an opening bracket
       or a comma, and divides otherwise. IT ALSO READS THE PREVIOUS WORD, which is a reviewer's
       correction and the reason the self-tests below exist: after a KEYWORD (`return /x/.test(s)`
       is the shape this app has, in pathColor and njtColor) the last significant character is a
       letter, so the class alone called it a division and scanned the regex body as code. When
       such a regex carries a quote, that opens a string frame and the literals AROUND it are lost
       rather than merely mis-reported: a silent hole in the direction that claims totality.

       SO THE SCANNER IS MEASURED ON ITS OWN CONSTRUCTS, not only on today's source. The cases are
       written in the test below, run through this function here, and compared there; each one is a
       shape that has been got wrong once (a nested template, a regex with a quote after `(`, a
       regex after a keyword, and a real division). */
    /* The words a `/` may follow and still open a regex. `return` is the one this app uses; the
       rest are free, and a `/` after any other identifier is a division. */
    const OPENS_A_REGEX = new Set(
      "return typeof case in of new delete void instanceof do else yield await throw".split(" "),
    );
    const literals = (src) => {
      const out = [];
      const frames = [{ kind: "code", brace: 0, interp: false }];
      const top = () => frames[frames.length - 1];
      let prev = "";
      for (let i = 0; i < src.length; i++) {
        const c = src[i], d = src[i + 1];
        const f = top();
        if (f.kind === "line") { if (c === "\n") frames.pop(); continue; }
        if (f.kind === "block") { if (c === "*" && d === "/") { frames.pop(); i++; } continue; }
        if (f.kind === "regex" || f.kind === "class") {
          if (c === "\\") { i++; continue; }
          if (f.kind === "regex" && c === "[") { f.kind = "class"; continue; }
          if (f.kind === "class" && c === "]") { f.kind = "regex"; continue; }
          if (f.kind === "regex" && c === "/") { frames.pop(); prev = "x"; }
          continue;
        }
        if (f.kind === "str") {
          if (c === "\\") { f.buf += src[++i] ?? ""; continue; }
          if (c === f.quote) { out.push(f.buf); frames.pop(); prev = "x"; continue; }
          f.buf += c;
          continue;
        }
        if (f.kind === "tpl") {
          if (c === "\\") { f.buf += src[++i] ?? ""; continue; }
          if (c === "$" && d === "{") { f.buf += "\u0001"; frames.push({ kind: "code", brace: 0, interp: true }); i++; continue; }
          if (c === "`") { out.push(f.buf); frames.pop(); prev = "x"; continue; }
          f.buf += c;
          continue;
        }
        // code
        if (c === "/" && d === "/") { frames.push({ kind: "line" }); i++; continue; }
        if (c === "/" && d === "*") { frames.push({ kind: "block" }); i++; continue; }
        if (c === "/") {
          /* THE PREVIOUS WORD, bounded: a keyword is at most ten characters, so a window is read
             rather than the whole prefix (this runs once per character of every function in the
             call graph). No match means the previous significant character was not a letter, and
             then the operator class decides as it always did. */
          const before = /([A-Za-z_$][\w$]*)\s*$/.exec(src.slice(i < 24 ? 0 : i - 24, i));
          const opens = before
            ? OPENS_A_REGEX.has(before[1])
            : /[(,=:[!&|?{};+\-*%~^<>]/.test(prev) || prev === "";
          if (opens) frames.push({ kind: "regex" });
          prev = "/";
          continue;
        }
        if (c === '"' || c === "'") { frames.push({ kind: "str", quote: c, buf: "" }); continue; }
        if (c === "`") { frames.push({ kind: "tpl", buf: "" }); continue; }
        if (c === "{") { f.brace++; prev = "{"; continue; }
        if (c === "}") {
          if (f.brace > 0) { f.brace--; prev = "}"; continue; }
          if (f.interp) { frames.pop(); prev = "x"; continue; }
          prev = "}";
          continue;
        }
        if (!/\s/.test(c)) prev = c;
      }
      return out;
    };
    /* The closure: from each root's source, every identifier called as a function that
       resolves to a function on globalThis, recursively. Depth is bounded only by the
       graph, and `seen` makes it terminate on cycles. */
    const seen = new Map();
    const queue = roots.map((src) => ["<root>", src]);
    const callees = (src) => [...src.matchAll(/\b([A-Za-z_$][\w$]*)\s*\(/g)].map((m) => m[1]);
    /* KEYED BY THE SOURCE AND NOT BY ITS LENGTH, which is a defect ruling R1 uncovered and worth
       stating plainly: this map used `name + src.length`, and EVERY root is named "<root>", so two
       roots whose sources happen to be the same number of characters collided and the second was
       never walked. Measured: `() => njtTrainPopup(newRecord)` and `() => pathTrainPopup(record)`
       are both 30 characters, so of the app's seven vehicle popups, one was crawled and the other
       was silently absent from the graph this test calls total. It surfaced because R1 put
       `njtBranch` into a STATION root as well, and its literal appeared for the first time in a
       stage that changed no string.
       A LENGTH IS NOT AN IDENTITY. The key is the whole source now, which is what "this function,
       already walked" actually means; the cost is memory in a test that already holds every one of
       these strings. */
    const key = (name, src) => `${name}\u0000${src}`;
    while (queue.length) {
      const [name, src] = queue.shift();
      if (seen.has(key(name, src))) continue;
      seen.set(key(name, src), { name, src });
      for (const id of callees(src)) {
        const fn = globalThis[id];
        if (typeof fn !== "function") continue;
        const s = String(fn);
        if (!seen.has(key(id, s))) queue.push([id, s]);
      }
    }
    /* The literals: strip template interpolations, then tags and attribute values, keeping
       title= and aria-label= values because those ARE rider text. What is left and carries
       two or more letters is a candidate. */
    const out = {};
    for (const { name, src } of seen.values()) {
      for (const raw of literals(src)) {
        // The scanner already marked every interpolation with \u0001, so the split below breaks
        // prose at those boundaries and at every tag.
        for (const piece of raw.split(/<[^>]*>|\u0001/)) {
          /* ENTITIES ARE DECODED, because the builder writes `&middot;` and the rider reads
             `\u00b7`: a literal compared against rendered text has to be in the rider's
             alphabet or every entity-bearing string reports as uncovered. */
          const decoded = piece.replace(/&(#\d+|#x[0-9a-f]+|[a-z]+);/gi, (m, e) => {
            const d = document.createElement("textarea");
            d.innerHTML = m;
            return d.value;
          });
          const t = decoded.replace(/\s+/g, " ").trim();
          /* A CANDIDATE IS PROSE OR A LABEL, never an identifier. Two letters together, and
             then either a SPACE (multi-word, which is what rider prose is) or a capital
             (a label like "Railroad" or a proper noun). That filter is what separates
             "No trains" and "prediction age unavailable" from `number`, `object`, `placed`,
             `hm` and the two dozen other enum values and field names the call graph compares
             against. Measured on the first run: without it the report was 72 lines, 50 of
             them identifiers, and a waiver list that long IS the escape hatch. */
          if (!/[A-Za-z]{2}/.test(t)) continue;
          if (!/ /.test(t) && !/[A-Z]/.test(t)) continue;
          /* AND AN ATTRIBUTE FRAGMENT IS NOT PROSE, which MR5's mark wrapper is why. The tag
             split above removes whole tags, and it cannot remove a tag whose own opening is an
             interpolation: popupMarkHtml writes ` width="${w}" height="${h}"` against an <svg>
             tag that arrives as a value, so the piece `" height="` survived with a space in it
             and read as two words. No string a rider reads contains `="`. */
          if (/="/.test(t)) continue;
          (out[t] ??= new Set()).add(name);
        }
      }
    }
    return {
      inventory: Object.fromEntries(Object.entries(out).map(([k, v]) => [k, [...v].sort()])),
      selfTests: cases.map(([src]) => literals(src)),
    };
  }, { roots: world.roots, cases: SCANNER_CASES });

  const { inventory, selfTests } = scanned;

  // THE SCANNER BEFORE ITS ANSWER: five constructs, read in the page by the same function that
  // read the call graph, compared to what a reader of that source says each one contains.
  SCANNER_CASES.forEach(([src, want], i) => {
    expect(selfTests[i], `the literal scanner misread: ${src}`).toEqual(want);
  });

  expect(Object.keys(inventory).length, "the literal extractor found nothing").toBeGreaterThan(20);

  /* THE HAYSTACK IS THE GOLDEN, not this run's capture, because the claim is "is this
     literal PINNED" and the golden is what pinned means. Every popupText world counts, so a
     state covered by the ladder world satisfies a literal the stock world never renders. */
  const pinned = readGolden().popupText ?? {};
  const textOf = (v) => `${v.seen ?? ""} ${v.spoken ?? ""} ${(v.labels ?? []).join(" ")}`;
  const haystack = Object.values(pinned)
    .flatMap((w) => Object.values(w))
    .map(textOf)
    .join(" \u0000 ");
  expect(haystack.length, "no pinned popup text to check coverage against").toBeGreaterThan(500);

  /* AND IT IS KEYED BY SYSTEM WHERE THE LITERAL NAMES ONE, which is a reviewer's correction: the
     comment here used to promise that "a phrase pinned in one system does not silently cover
     another's" while the check was a plain `includes` over every world's text joined together, and
     the attribution was carried in the failure message alone. So a literal that moved into a state
     of one system that no world here reaches was covered by a coincidental occurrence of the same
     words in another system's pinned text, which is the laundering the comment forbade.

     ONLY WHEN EVERY FUNCTION IT CAME FROM IS THE SAME SYSTEM'S. A literal inside a shared builder
     (popupRowsHtml, positionWords, vehicleStaleLine) is legitimately pinned wherever it renders, so
     those fall back to the whole golden; narrowing them would be a false failure rather than a
     stronger test. What is narrowed is exactly the case the promise was about: prose that only
     `busPopup` or only `ferryArrivalsHtml` can reach.

     THE TWO TABLES ARE SMALL AND THEY FAIL LOUDLY. A function's system is its name's own prefix,
     which is this app's naming and not a list to maintain. A surface's system is its key's first
     word, with the two railroad systems folded together (they are one renderer) and the ladder
     world folded into them (every surface in it is an MNR popup). The premise below is what keeps
     the second table honest: a world or a surface renamed takes a system out of this map, and the
     assertion says so rather than quietly checking a literal against an empty string. */
  const SYSTEM_OF_FUNCTION = [
    ["subway", /^subway/], ["bus", /^bus/], ["njt", /^njt/], ["path", /^path/],
    ["ferry", /^ferry/], ["airtrain", /^airtrain/], ["rail", /^railroad/],
  ];
  const systemOfFunction = (name) => (SYSTEM_OF_FUNCTION.find(([, re]) => re.test(name)) ?? [null])[0];
  const systemOfSurface = (world, key) => {
    if (world === "f01") return "rail";
    const first = key.split(/[\s,]/)[0];
    return first === "lirr" || first === "mnr" ? "rail" : first;
  };
  const perSystem = {};
  for (const [world, surfaces] of Object.entries(pinned)) {
    for (const [key, v] of Object.entries(surfaces)) {
      const system = systemOfSurface(world, key);
      perSystem[system] = `${perSystem[system] ?? ""} \u0000 ${textOf(v)}`;
    }
  }
  expect(
    Object.keys(perSystem).sort(),
    "the golden's surface keys no longer name the systems this coverage check keys on",
  ).toEqual(["airtrain", "bus", "ferry", "njt", "path", "rail", "subway"]);

  // A literal is covered if it appears in the pinned text it could have come from, or is declared
  // not to be rider text. The attribution is in the failure message too, so a report names the
  // function to look in as well as the words.
  const waived = { ...NOT_RIDER_TEXT, ...UNREACHED_STATES };
  const uncovered = Object.entries(inventory)
    .filter(([lit]) => !waived[lit])
    .filter(([, from]) => from.length > 0)
    .filter(([lit, from]) => {
      const systems = new Set(from.map(systemOfFunction));
      const only = systems.size === 1 ? [...systems][0] : null;
      return !(only ? perSystem[only] : haystack).includes(lit);
    })
    .map(([lit, from]) => `${JSON.stringify(lit)} (from ${from.join(", ")})`)
    .sort();

  /* WHAT THE WAIVER LIST IS ALLOWED TO BE. A key with a reason, never a bare list, so a
     waiver cannot be padded silently. And a waiver nobody extracts any more is itself a
     failure, so dead ones cannot accumulate behind the live ones. */
  const dead = Object.keys(waived).filter((k) => !inventory[k]);
  expect(dead, "a waiver for a literal the extractor no longer finds: delete it").toEqual([]);

  expect(
    uncovered,
    "a rider-visible literal in a popup that no pin covers. Either pin a world that renders " +
      "it, or declare it: NOT_RIDER_TEXT if a rider never reads it, UNREACHED_STATES if they " +
      "do and no world here reaches that state.",
  ).toEqual([]);
});

/* P5e: THE KICKER'S OVERFLOW RULE, IN A WORLD THAT REACHES IT (ruling R3).

   NO HERMETIC FIXTURE SERVES A STATION MORE THAN THREE ROUTES, measured across every stops payload:
   the subway's Times Sq and Canal serve three, NJ Transit's stations two, one and two, the ferry's
   docks three and one, the railroad's and PATH's one or two. So the cap, the count and the withheld
   routes are unreachable from every pinned world, and a rule no world runs is a rule that ships
   broken and green. frontend/popupvocab.test.js holds the arithmetic one builder at a time; this is
   the half that can only be asked of a browser, which is whether the drawn row stays ONE row.

   WHY THAT IS THE QUESTION. The ledger recorded that twelve plates "wrap to two rows" inside the
   popup's 220px floor, and that measurement was wrong: Leaflet sizes a popup to its own nowrap
   content up to maxWidth 320, so twelve plates make the popup 267px WIDE on one row instead. The cap
   exists because of the family whose marks are widest (an NJ Transit tag reading MNBTN is 66.69
   units where a subway plate is 17), and at four of those the row does wrap at phone widths. Three
   is what never wraps anywhere, and this is where "anywhere" is checked. */
test("P5e. a kicker with more routes than it can draw shows three, counts the rest, and stays one row", async ({
  page,
}) => {
  // Times Sq's real dozen, which is what the fixture's three stand in for: the overflow rule has to
  // hold for the station that made it necessary rather than for a number invented here.
  const twelve = { id: "127", name: "Times Sq-42 St", lat: 40.7557, lon: -73.9865,
    routes: ["1", "2", "3", "7", "A", "C", "E", "N", "Q", "R", "W", "S"] };
  await boot(page, (ctx) => {
    ctx.overrides.subwayStops = (route, fixtures) =>
      json(route, fixtures.subwayStops().map((stop) => (stop.id === "127" ? twelve : stop)));
  });
  await page.waitForFunction(
    () => typeof stationRegistry !== "undefined" && stationRegistry.some((row) => row.key === "subway|127"),
  );

  const measured = {};
  for (const width of [1280, 375]) {
    await page.setViewportSize({ width, height: 667 });
    await closeAllPopups(page);
    await page.clock.runFor(300);
    await page.evaluate(inPage("MARKERS[which]().openPopup();"), "subway station");
    await expect(page.locator(".leaflet-popup-content .pk .pmark")).toHaveCount(3);
    measured[width] = await page.evaluate(() => {
      const content = document.querySelector(".leaflet-popup-content");
      const slot = content.querySelector(".pk > span:last-child");
      const marks = [...slot.querySelectorAll(".pmark")];
      const row = (el) => Math.round(el.getBoundingClientRect().height);
      return {
        marks: marks.length,
        more: (slot.querySelector(".pmore") || {}).textContent ?? null,
        spoken: (slot.querySelector(".visually-hidden") || {}).textContent ?? null,
        // ONE ROW is the whole point: every mark's top edge is the same, and the slot is no taller
        // than a mark. A wrapped row would double the second number and stagger the first.
        distinctTops: new Set(marks.map((m) => Math.round(m.getBoundingClientRect().top))).size,
        slotHeight: row(slot),
        markHeight: row(marks[0]),
        contentWidth: Math.round(content.getBoundingClientRect().width),
      };
    });
  }

  /* THE PREMISES, per width, outside pin() so a regeneration cannot swallow them: three marks and
     nine withheld (the rule applied), one row (the reason the rule has that number), and the popup
     inside the cap it is allowed (D6c's 320, and at 375 the viewport rule's own narrower cap). */
  for (const [width, m] of Object.entries(measured)) {
    expect(m.marks, `${width}: three marks, whatever the station serves`).toBe(3);
    expect(m.more, `${width}: the nine it did not draw`).toBe("+9");
    expect(m.spoken, `${width}: every route in the words`).toBe("1, 2, 3, 7, A, C, E, N, Q, R, W, S");
    expect(m.distinctTops, `${width}: the marks are on ONE row`).toBe(1);
    expect(m.slotHeight, `${width}: and the row is no taller than a mark`).toBeLessThanOrEqual(m.markHeight + 1);
    expect(m.contentWidth, `${width}: inside the popup's cap`).toBeLessThanOrEqual(width === "375" ? 315 : 320);
  }
  pin("kicker/overflow", measured);
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
      stnLabelsSubway: n(".stn-label.subway"),
      stnLabelsRail: n(".stn-label.rail"),
      stnLabelsHub: n(".stn-label.hub"),
      stnLabelsHubSubway: n(".stn-label.subway.hub"),
      // The per-family marker classes specs reach for by name.
      trainMarkers: n(".train-marker"),
      busMarkers: n(".bus-marker"),
      pathMarkers: n(".path-marker"),
      ferryMarkers: n(".ferry-marker"),
      // MR4 moved AirTrain's stations onto the commuter square, so the family left
      // `.airtrain-marker` for `rail-stn-marker rail-airtrain-stn`. BOTH are counted: the old
      // class must read zero rather than simply stop being asked, because a selector that
      // quietly matches nothing is how a count over a widened class goes wrong in the first
      // place (smoke.spec.js had a toHaveCount(0) here that would have passed vacuously).
      airtrainMarkers: n(".airtrain-marker"),
      airtrainStationMarkers: n(".rail-airtrain-stn"),
      /* MR5: THE SIX MARK CLASSES, COUNTED IN BOTH PLACES THEY CAN NOW BE DRAWN, and a reviewer's
         finding is why they are here at all. This census existed for exactly one defect shape, a
         count over a class a later stage widened, and MR5 widened six of them without moving a
         number in it: section 5 copies a marker's own SVG into a popup title, so `svg.rail-tag` and
         its five siblings are no longer "a marker's mark" by construction. a11y.spec.js A1z4 is
         where the rail tag's own scope closure is asserted (its axe exception depends on it); this
         is where a class crossing into a THIRD place (a station popup, the Key panel, a tooltip)
         shows up as one line of diff.

         FIVE CLASSES AND A PLATE. Five builders write a class on the svg they return; the subway
         plate writes none, because pins.spec.js P1f pins its markup byte for byte and a class added
         for a test to count would be markup nobody draws for. Inside a popup it is therefore
         counted as an unclassed svg, which is unambiguous there (a popup's only svg is its mark)
         and is not asked of the map at all. */
      marks: Object.fromEntries(
        ["rail-tag", "rail-stn", "path-diamond", "ferry-hull", "bus-mark"].map((cls) => [
          cls,
          { map: n(`svg.${cls}:not(.leaflet-popup-content svg)`), popup: n(`.leaflet-popup-content svg.${cls}`) },
        ]),
      ),
      popupPlates: n(".leaflet-popup-content .pmark svg:not([class])"),
      popupMarks: n(".leaflet-popup-content .pmark svg"),
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

  /* AND THE SAME CENSUS WITH A POPUP OPEN, which is the state MR5 created. In the stock state every
     `popup` column reads 0, and a column that only ever reads 0 is the vacuous half of the shape
     this census exists for: it would still read 0 after a stage moved a mark into a second popup.

     TWO POPUPS, ONE AT A TIME, because the two cases fail differently. A rail train's mark carries
     a CLASS, so it lands in a named column and a third surface adopting `svg.rail-tag` moves that
     number; the subway plate carries none, so the only thing that says it is drawn at all is
     `popupMarks` reading one while every named column reads zero. One state would leave the other
     case unwitnessed. They are separate reads rather than two popups open at once, because a second
     open popup makes `.leaflet-popup-content` non-unique (the note at the top of tests/e2e/popup.js). */
  const popups = {};
  for (const which of ["mnr train", "subway train"]) {
    /* AND THE CLOCK IS RUN AFTER CLOSING, which cost a measurement to learn: Leaflet removes a
       closed popup's element on a 200ms fade timer, and this suite's clock is PAUSED, so a closed
       popup stays in the document until something advances it. Without this the second state
       counted both popups and `.leaflet-popup-content` stopped being unique. */
    await closeAllPopups(page);
    await page.clock.runFor(300);
    await expect(page.locator(".leaflet-popup-content")).toHaveCount(0);
    await page.evaluate(inPage("MARKERS[which]().openPopup();"), which);
    await expect(page.locator(".leaflet-popup-content .pmark svg")).toHaveCount(1);
    popups[which] = await classCensus(page);
  }
  await closeAllPopups(page);
  // THE PREMISES, or the second table merely exists: the tag is drawn in both places at once, the
  // plate is drawn as a mark that no named column claims, and neither popup moved the map's own.
  expect(popups["mnr train"].marks["rail-tag"], "a rail popup borrows the tag the map still draws").toEqual({
    map: 6,
    popup: 1,
  });
  expect(popups["subway train"].popupMarks, "a subway popup draws one mark").toBe(1);
  expect(popups["subway train"].popupPlates, "and it is the unclassed plate").toBe(1);
  for (const [which, census] of Object.entries(popups)) {
    const named = Object.values(census.marks).reduce((sum, m) => sum + m.popup, 0);
    expect(named, `${which}: a popup mark is either named by a class or counted as a plate`).toBe(
      census.popupMarks - census.popupPlates,
    );
  }
  pin("census/popups", popups);
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

/* ---------------- P4c: what every mark reads in the dark theme ---------------- */

test("P4c. every marker family's contrast in BOTH themes, paint by paint", async ({ page }) => {
  /* THE NUMBER LEDGER FINDING G15 ASKED FOR, RECORDED RATHER THAN SUMMARISED. G15 measured the
     Key panel's glyphs in the dark theme at 1.11 to 2.63 against the surface and is the reason
     `#theme-toggle` shipped hidden; MR4 releases it, so the same arithmetic has to be run on the
     MAP's marks, and this is where its answers live.

     A GOLDEN AND NOT ONLY A FLOOR. tests/e2e/theme.spec.js D5d asserts the floor: every family
     clears 3:1 on the best paint it has. What a floor cannot say is WHICH paint is carrying a
     family, and that is the interesting half, because two families clear it on their type or
     their outline rather than on their fill:

       - a subway train's route square reads 2.73 against the dark paper for the A trunk, and the
         mark is carried by the white letter printed on it;
       - a rail tag's body is the agency's own branch colour, and the darkest of them are carried
         by the type and the outline the same way.

     Neither is a defect of this stage and neither is this stage's to fix: those fills are
     published colours, and MR3's finding N1 already set the principle that the feed's colour is
     preferred and moved only where nothing else can work. What they ARE is a sentence a later
     stage could change without noticing, so the numbers are a golden. A stage that moves a
     published fill, or that changes which paint carries a mark, moves a row here and has to say
     why.

     BOTH THEMES, because the light theme's numbers are the control: a change that improved dark
     by ruining light would move a row here rather than pass a dark-only floor.

     THE MEASUREMENT IS tests/e2e/contrast.js, which states its own definition: the surface is
     the theme's `--paper` (a tile is an image and no mark clears 3:1 against every possible
     pixel), and a family is reported at its WORST mark rather than its average. */
  await boot(page);
  const measured = {};
  for (const theme of ["light", "dark"]) {
    await page.evaluate((want) => applyTheme(want), theme);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const run = await measureMarkContrast(page);
    // THE PREMISES, or this pin records an empty table: the surfaces are the theme's and every
    // family is on the page.
    expect(run.paper, `${theme}: the paper token`).toBe(
      theme === "dark" ? "rgb(32, 30, 29)" : "rgb(243, 242, 242)",
    );
    const best = bestPerFamily(run);
    expect(Object.keys(best).length, `${theme}: every family is measured`).toBe(10);
    measured[theme] = best;
  }
  pin("contrast/marks", measured);
});

test("P4d. every non-opaque paint on the page, composited and not, on the map and in a popup", async ({ page }) => {
  /* MR47's DETERMINATION, AND THE GUARD ITS REPAIR NEVER HAD.

     THE HISTORY, IN ONE PARAGRAPH. Round 1 of MR4 found that the contrast measurement composited a
     COLOUR's alpha (which no mark on this map has) and ignored the one alpha that is here, an
     ELEMENT's `opacity` attribute. R15 fixed it and MR4 recorded the fix as UNGUARDED, because
     mutation M47 reverts it and survives: the two alphas are both a --paper backing behind
     something else (the subway plate's at 0.95, the rail tag's at 0.9), a paper backing measured
     against paper reads about the same either way, and `bestPerFamily` only ever records a
     family's STRONGEST paint.

     WHAT MR5 CHANGES, which is the determination the stage brief asked for. Section 5 draws a
     popup's title with the map's own mark, so those two alphas are now also drawn INSIDE A POPUP,
     over `--surface` rather than over `--paper`. In the dark theme those are two different greys,
     so compositing stops being a no-op and the repair starts changing numbers. This pin is where
     they are recorded: each non-opaque paint, composited and as if it were opaque, on both
     surfaces, from the map and from an open popup.

     SO M47 NOW MOVES NUMBERS rather than surviving. The premises below are what make that true of a
     revert as well as of a rewrite: each place must have a row whose composited value DIFFERS from
     its opaque one, or the compositing is doing nothing there and this pin is a table of duplicates.

     PER PLACE, AND A REVIEWER'S CORRECTION IS WHY. The difference used to be asserted of the table
     as a whole, and every row carries `...OnSurface` numbers whether or not it is on that surface:
     `--paper` and `--surface` are two different greys in BOTH themes (light `#f3f2f2` against
     `#eae9e9`, dark `rgb(32,30,29)` against `#2d2b2b`), so a map-drawn plate with no popup open at
     all satisfies "compositing changed a number". The sentence above attributes the end of the
     no-op to section 5 drawing the mark inside a popup, and now the premise measures that claim
     where it is made rather than somewhere the claim is not about.

     WHAT ACTUALLY KILLS M47's REVERT is the three-place premise rather than this one: with
     `alphaOf` returning 1, `effective < 1` is false for every shape, the `alpha` array comes back
     empty, and the loop below fails on the first place with nothing in it. Recorded here because a
     premise that is not the one doing the work should not be read as if it were. */
  await boot(page);
  // A rail train's popup, because its tag carries the 0.9 backing: this is the mark section 5 moved
  // onto a new surface, and a popup has to be open for the measurement to see it.
  await page.evaluate(() => {
    const [record] = [...railroads.values()];
    record.marker.openPopup();
  });
  await expect(page.locator(".leaflet-popup-content .pmark svg")).toHaveCount(1);

  const measured = {};
  for (const theme of ["light", "dark"]) {
    await page.evaluate((want) => applyTheme(want), theme);
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    // The popup survives a theme swap by being rebuilt (rebuildOpenPopupsForTheme), which D6h
    // measures; here it only has to still be open, or the popup rows would vanish from the pin.
    await expect(page.locator(".leaflet-popup-content .pmark svg")).toHaveCount(1);
    const rows = alphaPaints(await measureMarkContrast(page));
    // THE PREMISES. Both places are in the table, or a pin of map-only rows would read as though
    // the popup carried no alpha at all; and the compositing has to change something.
    const keys = Object.keys(rows);
    // All three places, so a table of one is never mistaken for the whole page: the map's marks, the
    // popup's borrowed one, and the Key panel's glyphs, which carry the same 0.9 backing in H3's
    // light-theme literals because the panel keeps one surface in both themes.
    for (const place of ["map ", "popup ", "chrome "]) {
      const there = keys.filter((k) => k.startsWith(place));
      expect(there.length, `${theme}: no ${place.trim()} alpha`).toBeGreaterThan(0);
      expect(
        there.filter((k) => rows[k].compositedOnSurface !== rows[k].opaqueOnSurface).length,
        `${theme}: compositing changed no ${place.trim()} number, so this pin cannot see that alpha`,
      ).toBeGreaterThan(0);
    }
    measured[theme] = rows;
  }
  pin("contrast/alpha", measured);
});

/* ---------------- P6: follow-up 1, the bus layer's zoom rule ---------------- */

/* THE PHASE'S FIRST FOLLOW-UP, AND ITS PINS COME FIRST FOR THE REASON EVERY STAGE'S DID. The
   rule it brings is a stylesheet rule keyed on the zoom: bus markers are drawn from City zoom
   (13) and not below it. A rule keyed on the zoom reaches things at zooms no pin here looked at,
   because P1f to P1n all read the map at the one zoom it opens at, which is 12. So these read it
   at the opening view AND at each of the three presets, and each says what the rule may NOT
   change there:

     P6a  the bus count: the feed strip's, the registry's and the document's. Every bus counted
          and every bus marker in the document at every view, drawn or not, because a hidden
          marker is still a marker and the strip counts the fleet rather than the viewport.
     P6b  every marker that is not a bus: its markup, its name, whether it is drawn, whether it
          takes the pointer, and whether it is exposed. The rule is scoped to one class, and
          this is the pin that says so at every zoom the rule can see.
     P6c  the bus marker's icon and both bus popups, byte for byte. The rule is CSS, so neither
          moves with the zoom; a rule implemented by swapping the icon or rebuilding the popup
          below 13 is exactly what this catches.

   The views are asked of views.js, which reads the presets out of the app's own table. */
const busZoomViews = require("./views");
const BUS_ZOOM_VIEWS = ["open", ...busZoomViews.PRESET_IDS];

test("P6a. the bus count in the strip, the registry and the document, at the opening view and each preset", async ({
  page,
}) => {
  await boot(page);
  const measured = {};
  for (const view of BUS_ZOOM_VIEWS) {
    const zoom = view === "open" ? await page.evaluate(() => map.getZoom()) : await busZoomViews.pressView(page, view);
    measured[view] = await page.evaluate((z) => ({
      zoom: z,
      strip: document.querySelector("#toggle-buses .feed-count").textContent,
      registry: buses.size,
      document: document.querySelectorAll(".bus-marker").length,
    }), zoom);
  }
  // Read against each other as well as against the golden: the strip's number is the fleet at
  // every view, which is the half of the rule the tooltip exists to explain.
  expect(new Set(Object.values(measured).map((m) => m.strip)).size, "the strip's bus count moved with the zoom").toBe(1);
  pin("busZoom/counts", measured);
});

/* WHAT A MARKER IS, READ OFF THE DRAWN PAGE. Its classes and its name are markup; `drawn`,
   `pointerEvents` and `ariaHidden` are the three things the rule changes for a bus and must not
   change for anything else, read from the computed style and the element rather than from the
   root attribute or the stylesheet, because a markup read where the drawn page is what matters
   is the first of this phase's defect shapes. The inline opacity is the freshness contract's
   dimming, and it rides along so a rule that faded rather than hid would show here too. */
const readMarkersExceptBuses = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll(".leaflet-marker-icon:not(.bus-marker)")]
      .map((el) => {
        const style = getComputedStyle(el);
        return {
          cls: [...el.classList].filter((c) => c !== "leaflet-zoom-animated").sort().join(" "),
          name: el.getAttribute("aria-label"),
          role: el.getAttribute("role"),
          ariaHidden: el.getAttribute("aria-hidden"),
          drawn: style.display !== "none" && style.visibility === "visible",
          pointerEvents: style.pointerEvents,
          opacity: el.style.opacity,
          html: el.innerHTML,
        };
      })
      .sort((a, b) => `${a.cls}|${a.name}|${a.html}`.localeCompare(`${b.cls}|${b.name}|${b.html}`)),
  );

test("P6b. every marker that is not a bus, at the opening view and each preset", async ({ page }) => {
  await boot(page);
  const measured = {};
  for (const view of BUS_ZOOM_VIEWS) {
    if (view !== "open") await busZoomViews.pressView(page, view);
    measured[view] = await readMarkersExceptBuses(page);
    // A pin over an empty list is a pin that cannot fail, which is the third defect shape.
    expect(measured[view].length, `${view}: no marker other than a bus was read`).toBeGreaterThan(10);
  }
  /* THE MARKUP IS PINNED ONCE AND EVERY VIEW IS HELD TO IT, because at the base of this branch it
     is the same at all four (measured: 21 markers, identical bytes), so four stored copies would
     be four goldens that could each be regenerated alone. The per-view state is pinned per view,
     because that is the half a zoom rule could move. */
  const markup = (list) => list.map(({ cls, name, html }) => ({ cls, name, html }));
  for (const view of BUS_ZOOM_VIEWS) {
    expect(markup(measured[view]), `${view}: a marker that is not a bus changed its markup`).toEqual(markup(measured.open));
  }
  pin("busZoom/othersMarkup", markup(measured.open));
  pin(
    "busZoom/others",
    Object.fromEntries(BUS_ZOOM_VIEWS.map((view) => [view, measured[view].map(({ html, ...state }) => state)])),
  );
});

/* THE ICON IS READ FROM LEAFLET'S OPTIONS, which is what P1g's `markers/buses` golden reads, so
   the two cannot disagree about what "the bus marker HTML" is. THE POPUP IS READ FROM THE
   MARKER'S OWN POPUP ELEMENT once the route line it opens has landed, because opening a bus
   popup fetches that line and refreshes the popup when it arrives, and a read taken before
   then is a read of a popup a rider sees for one frame.

   placeView AND NOT pressView, because the footer carries the feed's age in seconds and the fly
   runs the clock: four presses would pin four ages. views.js says so at more length. */
test("P6c. the bus marker's icon and both bus popups, byte for byte, at the opening view and each preset", async ({
  page,
}) => {
  await boot(page);
  const measured = {};
  for (const view of BUS_ZOOM_VIEWS) {
    if (view !== "open") await busZoomViews.placeView(page, view);
    const icons = await page.evaluate(() =>
      [...buses.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([id, record]) => {
          const o = record.marker.options.icon.options;
          return {
            id,
            html: o.html,
            className: o.className,
            size: o.iconSize,
            anchor: o.iconAnchor,
            popupAnchor: o.popupAnchor,
            opacity: record.marker.options.opacity,
          };
        }),
    );
    const popups = {};
    for (const id of ["MTA NYCT_101", "MTA NYCT_102"]) {
      await closeAllPopups(page);
      const route = await page.evaluate((key) => {
        buses.get(key).marker.openPopup();
        return buses.get(key).latest.route_id;
      }, id);
      await expect(page.locator("#route-banner-label")).toHaveText(`Bus route ${route}`);
      popups[id] = await page.evaluate(
        (key) => buses.get(key).marker.getPopup().getElement().querySelector(".leaflet-popup-content").innerHTML,
        id,
      );
    }
    await closeAllPopups(page);
    measured[view] = { icons, popups };
  }
  // Every view against the opening one, so the golden holds one answer rather than four copies
  // of it that could each be regenerated separately.
  for (const view of BUS_ZOOM_VIEWS) {
    expect(measured[view], `${view}: the bus marker or popup moved with the zoom`).toEqual(measured.open);
  }
  pin("busZoom/bus", measured.open);
});
