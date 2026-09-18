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
async function boot(page, before, { railroadCount = 2 } = {}) {
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // Every system's marks, not "more than five of them": these pins read specific registry
  // keys, and a partially loaded page would quietly pin a shorter list. The counts are the
  // stock fixtures' (mock.js); railroadCount is the one a spec overrides, because P2
  // serves the F01 capture's 136 rows instead of the fixture's 2.
  await page.waitForFunction(
    (want) =>
      trains.size === 2 &&
      buses.size === 2 &&
      railroads.size === want &&
      pathTrainRecords.size === 2 &&
      ferryBoatRecords.size === 3 &&
      njtTrainRecords.size === 4 &&
      stationRegistry.length === 14,
    railroadCount,
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

/* ---------------- P3: the alert banner's rows ---------------- */

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

/* ---------------- P5 and P6: markers and popups, per system ---------------- */

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
