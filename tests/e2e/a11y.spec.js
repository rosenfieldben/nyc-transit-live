// A1: the axe-core scan, SCOPED TO WHAT THIS PHASE BUILT.
//
// WHY SCOPED AND NOT PAGE-WIDE. Before A1 the page had no accessibility markup at
// all, so a page-wide scan fails on the pre-existing map controls, the legend, and
// the alerts list the moment it is switched on. Gating CI on a page-wide scan today
// would mean either a red build on main or a pile of suppressions, and a suppression
// list is how a scan stops meaning anything. So the gate is drawn exactly around the
// new surface, where a violation is genuinely a regression in this phase's work:
// #stations-panel, plus the skip link that is the panel's entry point. Widening it to
// the whole page is A4's job, together with the statement of what the page promises;
// until then the rest of the page is UNMEASURED, and this file does not imply
// otherwise.
//
// WHAT A GREEN SCAN DOES AND DOES NOT MEAN. axe finds machine-checkable violations:
// missing names, bad contrast, broken ARIA references, wrong roles. It cannot tell
// whether the panel is usable by keyboard, whether focus survives closing, or whether
// the live region stays quiet on a countdown tick. Those are the promises this phase
// actually makes, and they are pinned by stations.spec.js. This file is the floor,
// not the ceiling.

const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

// Same hermetic harness as stations.spec.js: axe is injected into the page from
// node_modules at test time, so this needs no network either.
//
// THE CLOCK IS FIXED, NOT PAUSED, and the difference is the whole reason this helper
// is not a copy of the one in stations.spec.js. clock.pauseAt stops the page's timers
// as well as its Date, and axe-core drives its own rule queue through setTimeout, so
// under a paused clock analyze() never resolves and the spec dies on the test timeout
// rather than on a violation. setFixedTime pins Date.now (which is what keeps the
// app's skew calibration at zero and its ages deterministic) while leaving timers
// running, which is exactly the combination a scan needs.
async function open(page) {
  const ctx = await installMocks(page);
  // AN ALERT IS SERVED ON PURPOSE. The banner renders nothing at all when there are no
  // agency-wide alerts, so scanning a page without one would include the selector and
  // examine an empty div: green, and meaningless. The anti-vacuity assertions below
  // check that the banner was actually reached.
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "a11y-1",
        system: "subway",
        header: "Reduced service systemwide while crews clear a disabled train",
        description: null,
        effect: "REDUCED_SERVICE",
        cause: "OTHER_CAUSE",
        routes: [],
        stops: [],
        starts_at: fx.FROZEN_S - 600,
        ends_at: null,
      },
    ];
    return json(route, body);
  };
  await page.clock.setFixedTime(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => (typeof stationRegistry === "undefined" ? 0 : stationRegistry.length)), {
      timeout: 15_000,
    })
    .toBeGreaterThanOrEqual(6);
}

// ONE include() CALL PER ROOT, NEVER include([...]). An ARRAY argument is an iframe
// path in axe's selector grammar ("#stations-panel" then "#stations-skip" INSIDE it),
// not a list of roots, and since neither element is a frame the array form silently
// resolves to nothing at all. The first draft of this file used it and every scan came
// back green having checked zero nodes: 25 rules and 63 nodes with the chained form, 0
// and 0 with the array. That is what the assertions below exist to catch.
//
// A2 GREW THE SCOPE to the surfaces this phase touched: the alert banner, the status
// line, and the map controls a rider actually operates (zoom, the layer toggles, the
// Stations button). Still not the whole page: the legend's rows and the popups remain
// outside, and widening to the page belongs to the later phase that also states what
// the page promises. Scoping deliberately, and saying where the edge is, is what keeps
// a green scan meaningful rather than a suppression list waiting to happen.
const SCAN_ROOTS = [
  "#stations-panel",
  "#stations-skip",
  "#alert-banner",
  "#status",
  ".leaflet-control-zoom",
  "#toggles",
  "#stations-toggle",
  // A3 added the legend disclosure; a new interactive control joins the scanned scope
  // in the same commit that creates it, or the scope quietly falls behind the page.
  "#legend-toggle",
];

function scan(page) {
  return SCAN_ROOTS.reduce((builder, root) => builder.include(root), new AxeBuilder({ page }));
}

// Report violations as readable text rather than as a bare count, because "expected 0,
// got 3" sends the next reader to the trace instead of to the markup.
function violations(results) {
  return results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help} [${v.nodes.map((n) => n.target.join(" ")).join(", ")}]`,
  );
}

// THE ANTI-VACUITY CHECK, run on every scan in this file. A scan whose scope matches
// nothing reports zero violations, which is indistinguishable from a clean bill of
// health unless someone asks how much was examined. So each spec asserts both that
// there were no violations AND that real work happened: a floor on rules and nodes
// (well under the ~25/63 observed, so an axe-core bump that retires a rule does not
// fail the build), the specific selectors that must have been reached, and that
// color-contrast actually ran, since a rule with no applicable nodes is filed under
// `inapplicable` and would otherwise vanish without a word.
// WHAT THE SCAN CANNOT DECIDE, RECORDED RATHER THAN HIDDEN. axe reports a third
// category besides pass and violation: "incomplete", meaning it needs a human. A green
// "0 violations" says nothing about those, so if they are never asserted the scan
// quietly certifies less than it appears to.
//
// Everything incomplete here is color-contrast. A2 attributed all of it to one cause
// and was wrong about that; A3 read axe's per-node messages and found three, fixed two,
// and wrote the correction into the inventory comment below. Pinning the shape here
// means a NEW kind of uncertainty, or an old one appearing somewhere new, fails this
// suite instead of blending into a green run.
//
// AN INVENTORY, NOT A PATTERN, and the difference is what the review asked for. The
// first version allowed anything matching /^(#status|#toggles|label|\.leaflet-control-zoom)/,
// which meant a NEW element added under #toggles joined the undecidable set silently and
// the scan went on reporting zero violations. An exact list makes any change to the set
// fail and put a person in front of it.
//
// This is deliberately stricter than the rule and node FLOORS above, which are set well
// below what was observed so an axe-core bump that retires a rule cannot redden the
// build. The two are different kinds of number. A floor guards against the scan quietly
// shrinking; this list is the register of surfaces this project has admitted it cannot
// measure yet, and it growing is exactly the event a human should see.
//
// SAYING WHAT THIS STILL DOES NOT PROVE. Every target below remains UNMEASURED for
// contrast, and tightening the list does not change that: axe cannot decide these, so
// text on them could be unreadable today and this suite would stay green. That is the
// contrast pass's work, not a hole this file can close. What the list does close is the
// hole where a new unmeasured surface joins them without anyone noticing.
// THE INVENTORY SHRINKS BY CONVERSION, NEVER BY EXCLUSION. That is the rule A3 added,
// and it is the whole reason this list is worth keeping. An entry may leave this array
// only by appearing in DECIDABLE_CONTRAST below, which asserts that axe now actively
// PASSES color-contrast on that same target. Narrowing the scan scope, deleting a root,
// or hiding an element would also make an entry disappear, and would make this file a
// record of what stopped being looked at rather than of what got fixed. The paired lists
// are what tell those two apart, and A3's own conversion is the worked example: eight of
// the nine entries here moved across, and every one of them is asserted below.
//
// A3 CORRECTED THIS FILE'S DIAGNOSIS, WHICH IS WORTH RECORDING. A2 wrote that everything
// undecidable was so "because the legend is rgba(255,255,255,0.92) and the Leaflet
// controls sit directly on map tiles". Reading axe's own per-node messages showed three
// distinct causes, not one: seven labels reported "background color could not be
// determined because element contains an image node" (the map showing through the 8%
// alpha), #status reported "partially overlaps other elements" (the panel had grown past
// the bottom of a 720px viewport), and the zoom-out glyph reports "element content
// contains only non-text characters", which is about the character itself and has
// nothing to do with backgrounds at all. The first two were fixable. The third is not.
// AXE'S TARGET SELECTORS ARE NOT IDENTITIES, and this file found that out by breaking.
// axe reports each node as the SHORTEST selector that is unique in the document at scan
// time, so what it calls a node depends on the rest of the page. Adding a close button
// to the station panel pushed that panel's <label> from nth-child(2) to nth-child(3),
// which collided with a toggle label, which made axe requalify a DIFFERENT element as
// "#toggles > label:nth-child(2)". Three specs went red, none of them about a real
// accessibility change, and the inventory below was suddenly describing elements nobody
// had touched.
//
// So the inventories are written in identities the DOCUMENT supplies, and axe's targets
// are resolved to those before anything is compared. An id if the element has one, else
// the id of the control it wraps (which is what a <label> is), else its own first class
// under its parent's. Those survive renumbering because they do not count siblings.
//
// COLLISIONS ARE CHECKED WHERE THEY COULD COST AN ENTRY, which is the whole-list
// comparisons: two axe targets folding onto one identity would compare equal to a
// shorter list and pass by losing an entry, exactly the silent shrink this file exists
// to prevent. Membership checks do not take that check, because they must not: the
// passes list legitimately contains many identical siblings (each station row's route
// chips resolve to the same `.station-row-chips .station-chip`), and rejecting those
// would be rejecting normal markup. What makes membership safe instead is that every
// identity looked up there is anchored to an id, which the document guarantees unique.
// assertConvertedToDecidable enforces that on its own list rather than trusting it.
function canonicalise(page, targets) {
  return page.evaluate((selectors) => {
    const identify = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return `UNRESOLVABLE(${sel})`;
      if (el.id) return `#${el.id}`;
      const labelled = el.querySelector("[id]");
      if (labelled) return `${el.tagName.toLowerCase()}>#${labelled.id}`;
      const own = el.classList.length ? `.${el.classList[0]}` : el.tagName.toLowerCase();
      const parent = el.parentElement;
      // The parent's FIRST class only: Leaflet appends `leaflet-disabled` to a zoom
      // control that has hit the end of its range, and an identity that changed when the
      // rider zoomed in would be no better than the nth-child it replaces.
      const above = !parent ? "" : parent.id ? `#${parent.id}` : parent.classList.length ? `.${parent.classList[0]}` : "";
      return `${above} ${own}`.trim();
    };
    return selectors.map((sel) => [sel, identify(sel)]);
  }, targets);
}

async function identities(page, targets, { distinct = false } = {}) {
  const pairs = await canonicalise(page, targets);
  if (distinct) {
    const seen = new Map();
    for (const [sel, id] of pairs) {
      if (seen.has(id) && seen.get(id) !== sel) {
        throw new Error(`two axe targets share one identity (${seen.get(id)} and ${sel} both -> ${id})`);
      }
      seen.set(id, sel);
    }
  }
  return pairs.map(([, id]) => id);
}

const UNDECIDABLE_CONTRAST = [
  // THE ONE GENUINE SURVIVOR, and it is not a contrast problem. Leaflet's zoom-out
  // control contains a single "−" glyph, which axe classifies as non-text and
  // therefore declines to judge as text. No background, opacity or colour change can
  // convert it, because the rule never reached the colours: measured at 21:1 against its
  // own opaque white control, so it comfortably clears the 3:1 a non-text indicator
  // owes. It stays stock per the phase decision, named here rather than excused.
  ".leaflet-control-zoom-out span",
];

// EVERY ENTRY A3 REMOVED FROM THE LIST ABOVE, asserted as a live pass rather than an
// absence. This is the other half of the conversion rule: if one of these ever stops
// passing color-contrast it fails here, whether it regressed into a violation, back into
// undecidability, or out of the scanned scope entirely.
const DECIDABLE_CONTRAST = [
  "#status",
  "label>#toggle-buses",
  "label>#toggle-subways",
  "label>#toggle-stations",
  "label>#toggle-railroads",
  "label>#toggle-airtrain",
  "label>#toggle-path",
  "label>#toggle-ferries",
];

async function assertIncompletesAreKnown(page, results, { alsoAllow = [] } = {}) {
  const kinds = [...new Set(results.incomplete.map((entry) => entry.id))];
  expect(kinds.sort(), "a new kind of undecidable finding appeared").toEqual(
    kinds.length ? ["color-contrast"] : [],
  );
  const nodes = results.incomplete.flatMap((entry) => entry.nodes.map((node) => node.target.join(" ")));
  expect(
    (await identities(page, nodes, { distinct: true })).sort(),
    "the set of surfaces whose contrast axe cannot decide has changed",
  ).toEqual([...UNDECIDABLE_CONTRAST, ...alsoAllow].sort());
}

// The conversion half. Asked of the color-contrast rule specifically, not of "did any
// rule pass here", because an element can pass a dozen unrelated rules while its
// contrast stays unjudged, which is the exact state this phase set out to leave behind.
async function assertConvertedToDecidable(page, results, { expect: expected = DECIDABLE_CONTRAST } = {}) {
  // The membership check below skips collision detection (see identities), so what makes
  // it safe is that each entry is anchored to an id and the document guarantees ids are
  // unique. Enforced rather than assumed, because a future entry written as a bare class
  // would be satisfiable by an element nobody meant, which is a green with no meaning.
  for (const target of expected) {
    expect(/(^#|>#)/.test(target), `${target} must be anchored to an id to be looked up safely`).toBe(true);
  }
  const rule = results.passes.find((p) => p.id === "color-contrast");
  const passed = new Set(rule ? await identities(page, rule.nodes.map((node) => node.target.join(" "))) : []);
  for (const target of expected) {
    expect(
      passed.has(target),
      `${target} left the undecidable inventory, so contrast must now actively PASS on it`,
    ).toBe(true);
  }
}

function assertScanned(results, { targets }) {
  const passes = results.passes;
  const checked = passes.flatMap((p) => p.nodes.map((n) => n.target.join(" ")));
  expect(passes.length, "axe rules that ran and passed").toBeGreaterThanOrEqual(10);
  expect(checked.length, "nodes axe actually examined").toBeGreaterThanOrEqual(20);
  expect(
    passes.map((p) => p.id),
    "color-contrast must be applicable, or the scan is not measuring contrast at all",
  ).toContain("color-contrast");
  for (const target of targets) {
    expect(checked.some((t) => t.includes(target)), `axe must have examined ${target}`).toBe(true);
  }
}

test("A1i. axe: the station panel and the skip link, in the list state", async ({ page }) => {
  await open(page);
  // A query typed, so the scan sees real result rows rather than the empty prompt: the
  // rows are the part with the ARIA and the accessible names on them.
  await page.locator("#stations-search").fill("times");
  await expect(page.locator("#stations-results button.station-row").first()).toBeVisible();
  const results = await scan(page).analyze();
  expect(violations(results), "axe violations in the list state").toEqual([]);
  await assertIncompletesAreKnown(page, results);
  await assertConvertedToDecidable(page, results);
  assertScanned(results, {
    targets: [
      "#stations-panel",
      "#stations-search",
      "station-row",
      "#stations-skip",
      // The surfaces A2 added to the scope, asserted individually so a root that
      // silently matched nothing is a failure rather than a smaller green run.
      "alert-banner",
      "leaflet-control-zoom",
      "#toggle-buses",
      "#stations-toggle",
    ],
  });
});

test("A1j. axe: the station panel with a station selected and arrivals rendered", async ({ page }) => {
  await open(page);
  // The detail state has strictly more to get wrong than the list state: a heading
  // level, the visually-hidden accessibility wording, the live region, and the
  // arrivals lists. Scanning only the list state would miss all of it.
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator("#stations-detail h3")).toBeVisible();
  await expect(page.locator("#stations-detail .station-arrivals").first()).toBeVisible();

  // THE COUNTDOWN TICK IS STOPPED BEFORE THE SCAN, and this is the fix for a transient
  // that a review reproduced rather than a precaution against one it imagined. The panel
  // repaints its countdowns once a second, renderStationDetail begins by replacing the
  // detail subtree wholesale, and axe walks the tree over several frames. A scan that
  // straddles a repaint sees the h3 detached from the document and reports heading-order
  // as UNDECIDABLE, so assertIncompletesAreKnown below fails on a finding about OUR
  // timer rather than about the markup. Sweeping a delay before analyze() reproduced it
  // twice, at d=700 in one run and d=800 in the next: a race, not a fixed point, which
  // is why a wait of any length would not have fixed it.
  //
  // Stopped through the app's own door rather than by clearing the handle from the test,
  // so the spec cannot drift from what the app actually does when it stops a tick. And
  // the timer is asserted LIVE first, because a door that silently stopped working (a
  // rename, a refactor that moves the interval elsewhere) would otherwise leave this
  // looking fixed while the race quietly came back.
  const tickWasLive = await page.evaluate(() => {
    const live = panelTimer !== null;
    stopPanelArrivals();
    return live;
  });
  expect(tickWasLive, "the panel countdown must be running for stopping it to mean anything").toBe(true);

  const results = await scan(page).analyze();
  expect(violations(results), "axe violations in the detail state").toEqual([]);
  await assertIncompletesAreKnown(page, results);
  await assertConvertedToDecidable(page, results);
  assertScanned(results, {
    targets: ["#stations-panel", "#stations-announce", "station-arrivals", "h3", "leaflet-control-zoom"],
  });
});

test("A1k. axe: the skip link is scanned while FOCUSED, which is when it is visible", async ({ page }) => {
  await open(page);
  // A visually-hidden-focusable link is offscreen until focused, and axe skips hidden
  // nodes, so an unfocused scan proves nothing about it. Tabbing to it first is what
  // makes the contrast and naming checks actually run on it. It is also the one element
  // whose contrast is measured against the page rather than against the panel's own
  // background, which is why it gets a state of its own.
  await page.keyboard.press("Tab");
  await expect(page.locator("#stations-skip")).toBeFocused();
  const results = await scan(page).analyze();
  expect(violations(results), "axe violations with the skip link focused").toEqual([]);
  await assertConvertedToDecidable(page, results);
  // The panel HEADING joins the undecidable set in this state and only this one,
  // because a focused skip link is drawn over the top-left of the page and the docked
  // panel's heading is underneath it. Measured: the link occupies x 8..190 y 8..44 and
  // the heading x 14..345 y 12..32, so they genuinely overlap. That is what a skip link
  // is supposed to do (appear above the content while focused, vanish when it is not),
  // and the rider whose focus is on the link is not reading the heading behind it. So
  // it is allowed HERE, by name, rather than added to the global allowance where it
  // would also excuse a heading that was unreadable in the ordinary states.
  await assertIncompletesAreKnown(page, results, { alsoAllow: ["#stations-heading"] });
  assertScanned(results, { targets: ["#stations-skip"] });
});

// THE MOBILE UNDECIDABLE SET, which is not the desktop one and is not allowed to be a
// looser version of it. Two entries, both named, both with the reason a machine cannot
// decide them and the thing that decides them instead.
//
// IT NOW CHECKS THE NODES AND NOT ONLY THE RULE IDS, which is what the review asked for
// and what the earlier version could not do. Checking rule ids alone meant "some
// color-contrast finding is undecidable somewhere", and something WAS hiding behind that:
// with the legend expanded at 375 the alert banner's own row had joined the undecidable
// set, and the kinds check could not see it because color-contrast was already an allowed
// kind. The reason it could not check nodes was that the desktop entries were nth-child
// selectors against a desktop tree; now that both lists are written as identities the
// document supplies, the mobile list can be exact too.
const MOBILE_UNDECIDABLE = [
  // Same glyph, same reason, same non-fix as on desktop.
  ".leaflet-control-zoom-out span",
  // "Skip link target should become visible on activation". The panel is CLOSED at this
  // width, so at scan time the link points at a hidden element and a static rule cannot
  // know that activating it opens one. Decided behaviourally instead: A6g presses the
  // link and asserts focus lands inside the panel that was hidden a moment earlier.
  "#stations-skip",
];

async function assertMobileIncompletesAreKnown(page, results) {
  const nodes = results.incomplete.flatMap((entry) => entry.nodes.map((node) => node.target.join(" ")));
  expect(
    (await identities(page, nodes, { distinct: true })).sort(),
    "the set of surfaces undecidable at mobile width has changed",
  ).toEqual([...MOBILE_UNDECIDABLE].sort());
  const kinds = [...new Set(results.incomplete.map((entry) => entry.id))].sort();
  expect(kinds, "a new KIND of undecidable finding appeared at mobile width").toEqual(
    // color-contrast: the zoom-out glyph, exactly as on desktop, for the same reason
    //   (axe declines to judge an element whose content is only non-text characters).
    // skip-link: "Skip link target should become visible on activation". The panel is
    //   CLOSED at this width, so at scan time the link points at a hidden element and a
    //   static rule cannot know that activating it opens one. It is genuinely
    //   undecidable by inspection, and it is decided behaviourally instead: A6g in
    //   mobile.spec.js presses the link and asserts focus lands inside the panel that
    //   was hidden a moment earlier. That spec is why this entry is an admission of a
    //   TOOL limit rather than of an unfixed defect.
    ["color-contrast", "skip-link"],
  );
  const skip = results.incomplete.filter((entry) => entry.id === "skip-link");
  for (const entry of skip) {
    expect(
      entry.nodes.map((node) => node.target.join(" ")),
      "only the stations skip link may be undecidable for skip-link",
    ).toEqual(["#stations-skip"]);
  }
}

// The alert rows are counted rather than looked up, and that is not a stylistic choice.
// They are siblings with no ids, so they all share one identity, and asking whether "an"
// alert row passed would be answered yes by any one of several while the others failed.
// Asking whether ALL of them passed cannot be answered that way.
async function bannerRowsDecided(page, results) {
  const rule = results.passes.find((entry) => entry.id === "color-contrast");
  const ids = rule ? await identities(page, rule.nodes.map((node) => node.target.join(" "))) : [];
  return ids.filter((id) => id === ".alert-banner-rows .alert-banner-row").length;
}

// A3: THE SAME SCOPE, SCANNED ON A PHONE. The mobile layout moves the banner, collapses
// the legend behind a disclosure and turns the station panel into a full-width overlay,
// so it is a genuinely different tree from the desktop one and a desktop-only scan
// certifies nothing about it.
//
// The mobile scan keeps its OWN undecidable list, because the two layouts genuinely
// differ, but it is now exact rather than a rule-id check: see MOBILE_UNDECIDABLE. It
// also runs the desktop conversion list, since every entry there (#status and the seven
// toggle labels) exists at this width too and there is no reason a phone should be
// allowed to lose a contrast guarantee a desktop keeps.
test("A1m2. axe: the mobile layout, at 375, with the legend collapsed and expanded", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 });
  await open(page);

  // Collapsed, which is the state a rider lands on.
  await expect(page.locator("#legend")).toBeHidden();
  const rowCount = await page.locator(".alert-banner-row").count();
  expect(rowCount, "the fixture must render an alert row, or the row assertions are vacuous").toBeGreaterThan(0);
  let results = await scan(page).analyze();
  expect(violations(results), "axe violations on the phone, legend collapsed").toEqual([]);
  await assertMobileIncompletesAreKnown(page, results);
  await assertConvertedToDecidable(page, results);
  expect(await bannerRowsDecided(page, results), "every alert row's contrast, legend collapsed").toBe(rowCount);
  assertScanned(results, { targets: ["#stations-panel", "#legend-toggle", "#alert-banner", "#stations-toggle"] });

  // And expanded, because the disclosure's open state is a different tree again: the
  // fourteen legend rows and the note only exist here. THIS is the state that was
  // hiding a finding: the taller panel reached down into the banner's strip, and axe
  // stopped being able to decide the alert row's contrast at all. A3's review found it
  // and the fix bounds the panel above the banner, so the row is asserted DECIDED here
  // rather than admitted to the list above.
  await page.locator("#legend-toggle").click();
  await expect(page.locator("#legend")).toBeVisible();
  results = await scan(page).analyze();
  expect(violations(results), "axe violations on the phone, legend expanded").toEqual([]);
  await assertMobileIncompletesAreKnown(page, results);
  await assertConvertedToDecidable(page, results);
  expect(await bannerRowsDecided(page, results), "every alert row's contrast, legend expanded").toBe(rowCount);
  assertScanned(results, { targets: ["#legend-toggle", "#toggles"] });
});

test("A1l. the gate has teeth: a defect inside the scope IS caught", async ({ page }) => {
  await open(page);
  // This spec tests the gate, not the markup, and it is here because the gate silently
  // lost its teeth once already (the include() array above). The counted assertions in
  // assertScanned catch a scope that matches nothing; this catches the subtler case
  // where the scope matches but the rules are somehow not being enforced. An unlabeled
  // button is the cheapest unambiguous violation there is, and it is injected into the
  // live panel rather than into a fixture so the thing under test is the real scope.
  await page.evaluate(() => {
    const b = document.createElement("button");
    b.id = "a11y-canary";
    document.getElementById("stations-panel").appendChild(b);
  });
  const results = await scan(page).analyze();
  expect(
    results.violations.map((v) => v.id),
    "an unnamed button inside #stations-panel must be reported",
  ).toContain("button-name");

  // And the boundary is real in the other direction: the same defect in the legend,
  // which is outside the scope, is NOT reported. That is the scoping decision at the
  // top of this file, asserted rather than asserted-in-a-comment. When A4 widens the
  // gate to the page, this half of the spec is what has to change.
  await page.evaluate(() => {
    document.getElementById("a11y-canary").remove();
    const b = document.createElement("button");
    b.id = "a11y-canary-outside";
    document.getElementById("panel").appendChild(b);
  });
  const after = await scan(page).analyze();
  expect(violations(after), "the legend is out of scope and stays out of scope").toEqual([]);
});
