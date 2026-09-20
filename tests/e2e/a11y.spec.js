// A4: THE PAGE-WIDE AXE GATE. The scope is now the DOCUMENT.
//
// WHAT THIS REPLACES, AND WHY THE SCOPING ERA ENDED. A1 through A3 scanned a list of named
// roots, and said so plainly: before A1 the page had no accessibility markup at all, so a
// page-wide scan would have failed on pre-existing map controls, the legend and the alerts
// list, and gating CI on that meant either a red build on main or a pile of suppressions.
// A suppression list is how a scan stops meaning anything, so the gate was drawn exactly
// around each phase's new surface and the rest of the page was left honestly UNMEASURED.
//
// A4 is the phase that pays that off. The arc's guarantees are page-wide now, so the gate
// is too, and the four defects the scoped eras structurally could not see were found and
// fixed rather than excepted: the page had no h1 and no <main>, three or four nodes in
// every state sat outside every landmark, Leaflet's popup close button was an anchor to a
// fragment that never existed, and a popup at 375 opened entirely underneath the legend.
// See the A4 commits for each.
//
// ONE include() CALL PER ROOT, NEVER include([...]), is the rule this file used to open
// with, and it is kept here as history rather than deleted: an ARRAY argument is an iframe
// path in axe's selector grammar, not a list of roots, so the first draft of the scoped
// era measured 25 rules and 63 nodes with the chained form and 0 and 0 with the array.
// There are no include() calls left to get wrong, but the lesson generalises to any future
// re-scoping: assert how much was examined, never just that nothing failed.

const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");
const { danglingCitations, citedPairs } = require("../specids");
const { expectState } = require("./state");

/* THE GATE'S SCAN, IN ONE PLACE, because A1l's whole job is to prove THIS scan has teeth.
   Round 4: A1l built its own AxeBuilder, so it measured axe-core rather than the gate, and
   the two could drift without either noticing. They cannot drift now: re-scoping the gate
   re-scopes the spec that checks the gate. */
const scanPage = (page) => new AxeBuilder({ page }).analyze();

const DESKTOP = { width: 1280, height: 720 };
const PHONE = { width: 375, height: 667 };
// The narrowest width this app supports, which layout.spec.js and mobile.spec.js have
// measured LAYOUT at since A3 and which the accessibility gate had never scanned. F11
// puts new text in the panel, and the panel at 320 is the surface with the least room
// for it, so the state that carries that text is scanned here too. It is opt-in per
// state rather than a third row of the whole cross-product: tripling six states would
// buy coverage of markup the other two widths already scan unchanged, at the cost of
// half the gate's runtime.
const NARROW = { width: 320, height: 640 };

const agencyAlert = (n) => ({
  id: `a11y-${n}`,
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
});

// THE CLOCK IS FIXED, NOT PAUSED, and the difference is why this helper is not a copy of
// the one in stations.spec.js. clock.pauseAt stops the page's timers as well as its Date,
// and axe-core drives its own rule queue through setTimeout, so under a paused clock
// analyze() never resolves and the spec dies on the test timeout rather than on a
// violation. setFixedTime pins Date.now (which keeps the app's skew calibration at zero
// and its ages deterministic) while leaving timers running.
async function open(page, { alerts = 0, stationAlerts = false, staleRailroad = false } = {}) {
  const ctx = await installMocks(page);
  // Metro-North's own poll aged six minutes: enough to raise the status line and dim its
  // markers, and nothing else about the page changes.
  if (staleRailroad) {
    ctx.overrides.railroads = (route, fixtures) =>
      json(route, fixtures.railroadsWithSystems({ mnrAt: fx.FROZEN_S - 360 }));
  }
  // Agency-wide alerts go to the banner; station-scoped ones go to the panel and the
  // popups (F11). A state asks for one kind or the other, never a mix, so that a
  // contrast finding names one surface rather than two.
  ctx.overrides.alerts = (route, fixtures) =>
    json(route, {
      ...fixtures.alerts(),
      alerts: stationAlerts
        ? fixtures.stationAlertList()
        : Array.from({ length: alerts }, (_, i) => agencyAlert(i + 1)),
    });
  await page.clock.setFixedTime(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => (typeof stationRegistry === "undefined" ? 0 : stationRegistry.length)), {
      timeout: 15_000,
    })
    .toBeGreaterThanOrEqual(6);
  return ctx;
}

/* AXE'S TARGET SELECTORS ARE NOT IDENTITIES, which A3 learned by breaking: axe reports each
   node as the shortest selector unique in the document AT SCAN TIME, so adding a close
   button to the panel renumbered a <label> from nth-child(2) to nth-child(3), collided it
   with a toggle label, and reddened three specs about markup nobody had touched.

   A4 met the same lesson in new clothes. Page-wide, three different SVG <text> nodes (two
   subway marker glyphs and a legend swatch) all resolved to the bare identity `text`,
   because an SVG element has an empty classList and its <svg> parent has no id or class,
   so the A3 resolver fell through to a tagName with an empty parent prefix. Two Leaflet
   attribution anchors collided the same way.

   So resolution is ELEMENT-ANCHORED: walk up from the node building a short path of
   tag + first-class + same-tag index, stopping at the first ancestor with an id. That
   distinguishes `.train-marker svg text` from `.legend-row svg text` without depending on
   how many siblings any of them have.

   AND IT NO LONGER THROWS. A3's resolver rejected collisions loudly because the inventory
   was compared as an exact list, where a fold would have passed by losing an entry. This
   gate compares SHAPES instead (see UNDECIDABLE_SHAPES), so two markers legitimately
   sharing one identity is normal rather than an error, and a comparison that can only fail
   is strictly better than one that can throw. */
function identities(page, targets) {
  return page.evaluate((selectors) => {
    const part = (node) => {
      if (node.id) return `#${node.id}`;
      const tag = node.tagName.toLowerCase();
      const cls = node.classList && node.classList.length ? `.${node.classList[0]}` : "";
      const parent = node.parentElement;
      if (!parent) return tag + cls;
      const sameTag = [...parent.children].filter((c) => c.tagName === node.tagName);
      const index = sameTag.length > 1 ? `:${sameTag.indexOf(node) + 1}` : "";
      return tag + cls + index;
    };
    const identify = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return `UNRESOLVABLE(${sel})`;
      if (el.id) return `#${el.id}`;
      // A label is named by the control it wraps, which is stable across every layout
      // change that renumbers siblings.
      const labelled = el.querySelector ? el.querySelector("[id]") : null;
      if (labelled && labelled.id) return `${el.tagName.toLowerCase()}>#${labelled.id}`;
      const chain = [];
      let node = el;
      for (let depth = 0; depth < 4 && node && node !== document.body; depth++) {
        chain.unshift(part(node));
        if (node.id) break;
        node = node.parentElement;
      }
      return chain.join(" ");
    };
    return selectors.map(identify);
  }, targets);
}

/* THE EXCEPTION LIST, AND THE ONLY THING ALLOWED ON IT.
   axe reports a third category besides pass and violation: "incomplete", meaning it needs
   a human. A green "0 violations" says nothing about those, so if they are never asserted
   the scan quietly certifies less than it appears to.
   THE INVENTORY SHRINKS BY CONVERSION, NEVER BY EXCLUSION. An entry may leave this list
   only by becoming decidable and passing; it may never leave by being deleted or by a
   pattern being loosened until it stops matching. That rule is why A3's paired
   decidable-list existed and it is why every shape below carries a DECIDER: a spec that
   answers behaviourally the question axe declined to answer statically. An undecidable
   with no decider is not an exception, it is an unmeasured surface wearing one.
   ASSERTED AS SHAPES, NOT AS A LITERAL LIST, and that is a correctness requirement rather
   than convenience. The incomplete set is FIXTURE-SIZED: one node per rendered marker
   glyph, one per alert row, one per arrival badge. A literal list would encode how many
   trains this fixture happens to serve, so it would fail on fixture growth (which is not a
   regression) while a NEW KIND of uncertainty on an existing node would slip through under
   the old count. The shapes below say what an incomplete may BE; anything outside them
   fails, however many of them there are. */
const UNDECIDABLE_SHAPES = [
  {
    /* MR1. The Key panel scrolls at phone widths, where its sixteen rows are one column and
       do not fit; a row straddling its own scroll boundary is CLIPPED, and axe reports a
       clipped element as one whose background it cannot determine. A rider scrolls and reads
       it, so this is a tool limit rather than a defect, and it is the narrowest kind: the
       rows' ink and the surface behind them are two custom properties with fixed values.
       Scoped to #legend by both the id and the message, so it cannot excuse an obscured
       element anywhere else, and answered by A1x below rather than assumed. */
    name: "a Key panel row clipped by the panel's own scroll boundary",
    rule: "color-contrast",
    message: /partially obscured by another element|obscured by another element/,
    where: (id) => /^#legend /.test(id),
    decider:
      "a11y.spec.js A1x measures every Key panel row's computed ink against the header's " +
      "computed background at 1280, 375 and 320, in both themes, and requires 4.5",
  },
  {
    /* MR4 ROUND 2, AND A DIFFERENT MESSAGE FROM THE SHAPE ABOVE, which is why it is a new
       entry rather than a widened pattern. The Key panel's two commuter train rows draw the
       rail tag, whose type is printed ON the block it belongs to, so a sibling rect inside the
       same SVG covers the glyph's own box. axe reports that as "overlapped" rather than
       "obscured" and declines three of the four glyphs (the solid tag's agency letter it does
       decide). Widening shape 1's message to take "overlapped" would have excused an
       overlapped element ANYWHERE in #legend, which is a loosening; this is scoped to the two
       glyphs that have the property, by class and by tag.

       IT IS A TOOL LIMIT AND NOT A DEFECT, and the decider is not a hope: measured in-page
       with A1z's own selection rule, the four glyphs read 14.86, 4.69, 14.86 and 14.86 against
       the topmost shape under each, all clear of the 4.5 A1z enforces. 4.69 is the branch code
       on Babylon's published green, which is railBranchPaint's recomputed ink and the same pair
       A1z4 asserts on the drawn page. */
    name: "a rail tag's type overlapped by the block it is printed on",
    rule: "color-contrast",
    message: /overlapped by another element/,
    where: (id) => /svg\.key-rail-tag text/.test(id),
    decider:
      "a11y.spec.js A1z measures every rendered SVG glyph, these four included, against the " +
      "fill of the topmost shape drawn under it and requires 4.5, and a11y.spec.js A1x2 " +
      "measures the size that type is actually drawn at.",
  },
  {
    name: "a single-character glyph drawn inside an SVG icon",
    rule: "color-contrast",
    // axe declines these because the visible text is one character: it cannot tell a route
    // letter from a decorative mark. The colours are perfectly determinable, which is why
    // this one has a real decider rather than an excuse.
    message: /too short to determine if it is actual text content|only non-text characters/,
    // Matched on substrings rather than on adjacency: an element-anchored identity is a
    // PATH, so it carries intermediate ancestors and same-tag index suffixes that a rigid
    // "parent then child" pattern would miss for no good reason.
    where: (id) => /svg[^ ]* text/.test(id),
    // ROUND 2 CORRECTED THIS DECIDER. It used to name layout.spec.js A4g, which samples
    // .arr-badge, .station-chip, .leaflet-popup-content b and .arr-dir, and touches no SVG
    // text node anywhere. The exception therefore pointed at a spec that decided a different
    // surface, which is the exact "suppression with a sentence attached" the pairing rule
    // exists to prevent. A1z below now measures these glyphs against their own backing
    // shape, which is what makes this an admission of a tool limit rather than an excuse.
    // ROUND 3 CORRECTED WHICH BACKING SHAPE: the topmost one under the glyph, not the first
    // one in document order, which is the bottom of the stack.
    decider:
      "a11y.spec.js A1z measures every rendered SVG glyph against the fill of the topmost " +
      "shape drawn under it, and the zoom and popup-close glyphs against their control " +
      "backgrounds.",
  },
  {
    name: "a single-character arrival badge",
    rule: "color-contrast",
    // The same tool limit in HTML rather than SVG: a badge reading "2" is one character, so
    // axe will not judge it. Found by a flake rather than by design, which is its own small
    // lesson: this state only sometimes had its popup arrivals rendered at scan time, so the
    // gate reported the badges intermittently and the shape list had never seen them.
    message: /too short to determine if it is actual text content|only non-text characters/,
    where: (id) => /span\.arr-badge/.test(id),
    decider:
      "layout.spec.js A4g computes the contrast of every rendered .arr-badge in-page with the " +
      "same sRGB and relative-luminance formulas as helpers.js, which is exactly this surface.",
  },
  {
    name: "a single-character glyph on a Leaflet control",
    rule: "color-contrast",
    message: /too short to determine if it is actual text content|only non-text characters/,
    where: (id) =>
      /leaflet-control-zoom-out[^ ]* span/.test(id) || /leaflet-popup-close-button[^ ]* span/.test(id),
    decider: "a11y.spec.js A1z measures the zoom glyph and the popup close glyph against their own backgrounds.",
  },
  {
    name: "Leaflet's attribution, which sits directly on map tiles",
    rule: "color-contrast",
    // Genuinely undecidable by machine: the background is live imagery, so there is no
    // single colour to compute against. Decided instead by bounding it (see A1z).
    message: /background color could not be determined because element contains an image node/,
    where: (id) => id.includes("leaflet-control-attribution"),
    decider:
      "a11y.spec.js A1z composites the attribution's own translucent background over BOTH " +
      "extremes a tile can be (black and white) and requires AA against the worse of the two, " +
      "which bounds every possible tile without needing to know which tile is under it.",
  },
  {
    /* MR2. A station's name is a permanent Leaflet tooltip drawn straight onto the basemap,
       so its background is live imagery and there is no single colour to compute against.
       This is the same genuine tool limit as the attribution below it, and it is decided the
       same way: by bounding the answer over every tile a rider could be looking at rather
       than by guessing which one they are.

       SCOPED BY THE ONLY IDENTITY A TOOLTIP HAS. Leaflet gives every tooltip an auto id, and
       identities() returns an element's id when it has one, so the class this page puts on
       the element never reaches here. The pattern is therefore Leaflet's id shape, and A1z3
       closes the hole by asserting that every .leaflet-tooltip on the page IS a station
       label: this app binds tooltips in exactly one place (systems/subway.js), and if a
       second surface ever binds one, that assertion fails rather than this exception
       silently widening to cover it. */
    name: "a station name label, which sits directly on map tiles",
    rule: "color-contrast",
    /* TWO MESSAGES, BECAUSE THE LABEL HAS TWO WAYS OF BEING UNDECIDABLE and they are the same
       element. Over open map it is ink on live imagery ("contains an image node"). With the
       Key panel open it is also UNDER the chrome ("overlapped by another element"), which is
       a different sentence about the same tooltip: axe stops at the overlap before it reaches
       the tile. Both are excused for one element pattern and both are answered by one spec,
       which measures with the chrome closed, where a rider can actually read the label. A
       label a rider cannot see because the Key is over it has no legibility question to
       answer; it has a stacking one, and mobile.spec.js A6k and A6l are where the chrome's
       geometry is held. */
    message: /background color could not be determined because (element contains an image node|it is overlapped by another element)/,
    where: (id) => /^#leaflet-tooltip-\d+$/.test(id),
    decider:
      "a11y.spec.js A1z3 composites each label's halo over BOTH extremes a tile can be " +
      "(black and white), measures the label's own ink against the worse of the two, and " +
      "requires AA, in both themes; it also asserts that every tooltip on the page is a " +
      "station label, which is what keeps this exception from widening.",
  },
  {
    /* MR3. A commuter rail train is a two-part tag with 8px Archivo 800 in each block, and at
       regional zoom the tags OVERLAP each other: a tag is 35 to 45px wide where the square it
       replaced was 16, so with 136 of them on the F01 world's map, axe reaches a neighbouring
       marker div before it reaches the rect the type is printed on and declines to judge.
       Measured on the stock fixture: 37 findings at 1280 and 17 at 375, every one of them this
       message. An overlapped node is one axe cannot decide, not one it failed.

       THE SVG IS aria-hidden AND THAT IS NOT WHAT EXCUSES IT. labeledMarker puts the whole
       train's name on the marker div as role="img" plus aria-label, so the two glyphs are a
       picture of information a screen reader already has better; axe's color-contrast rule
       scans them anyway, correctly, because a sighted rider still sees them. So this is a tool
       limit about overlap, decided by measurement, exactly as the station label above is.

       SCOPED BY THE SVG'S OWN CLASS, which railTagSvg writes and nothing else uses. A1z4
       closes the hole the way A1z3 does for tooltips, by asserting that every `svg.rail-tag`
       on the page is a rail train's tag, so a second surface adopting the class fails there
       rather than widening this silently. */
    name: "a commuter rail tag's type, overlapped by a neighbouring tag at regional zoom",
    rule: "color-contrast",
    message: /background color could not be determined because it is overlapped by another element/,
    where: (id) => /svg\.rail-tag text/.test(id),
    decider:
      "a11y.spec.js A1z4 reads each tag's printed ink and the fill of the block it is printed " +
      "on straight off the drawn page, in both themes, and requires AA; railtag.test.js " +
      "measures the same pair in node over all 31 (route_color, route_text_color) pairs the " +
      "three feeds publish, which is what found that eight of them do not clear as published.",
  },
  {
    name: "the skip link, judged by a static rule that cannot run the page",
    rule: "skip-link",
    // "Skip link target should become visible on activation". The panel is hidden at scan
    // time, and no static rule can know that activating the link opens it.
    message: /Skip link target should become visible on activation/,
    where: (id) => id === "#stations-skip",
    decider:
      "mobile.spec.js A6g presses the link at 375 and asserts focus lands inside the panel that " +
      "was hidden a moment earlier; A6h asserts the desktop behaviour it must not break.",
  },
];

async function assertUndecidablesAreKnown(page, results, label) {
  const entries = [];
  for (const rule of results.incomplete) {
    const ids = await identities(
      page,
      rule.nodes.map((n) => n.target.join(" ")),
    );
    rule.nodes.forEach((node, i) => {
      entries.push({
        rule: rule.id,
        id: ids[i],
        message: (node.any[0] || {}).message || "",
      });
    });
  }
  const unexplained = entries.filter(
    (entry) =>
      !UNDECIDABLE_SHAPES.some(
        (shape) => shape.rule === entry.rule && shape.message.test(entry.message) && shape.where(entry.id),
      ),
  );
  expect(
    unexplained,
    `${label}: an undecidable finding outside every named shape. Each of these is either a ` +
      `defect to fix or a new shape to add WITH a decider spec, never an exception on its own.`,
  ).toEqual([]);
}

function violations(results) {
  return results.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help} [${v.nodes.map((n) => n.target.join(" ")).join(", ")}]`,
  );
}

// THE ANTI-VACUITY CHECK. A scan whose scope matches nothing reports zero violations, which
// is indistinguishable from a clean bill of health unless someone asks how much was
// examined. The floors are set well under what is observed so an axe-core bump that retires
// a rule cannot redden the build; the named targets are what catch a scope that silently
// stopped reaching a surface.
function assertScanned(results, { targets, label }) {
  const checked = results.passes.flatMap((p) => p.nodes.map((n) => n.target.join(" ")));
  expect(results.passes.length, `${label}: axe rules that ran and passed`).toBeGreaterThanOrEqual(15);
  expect(checked.length, `${label}: nodes axe actually examined`).toBeGreaterThanOrEqual(40);
  expect(
    results.passes.map((p) => p.id),
    `${label}: color-contrast must be applicable, or the scan is not measuring contrast at all`,
  ).toContain("color-contrast");
  for (const target of targets) {
    expect(checked.some((t) => t.includes(target)), `${label}: axe must have examined ${target}`).toBe(true);
  }
}

/* THE SIX STATES, AND WHY SOME OF THEM USED TO HIDE WHAT OTHERS SHOWED.
   Before the landmarks were added, `landmark-one-main` and `page-has-heading-one` VIOLATED
   in the desktop docked-panel states and PASSED everywhere else, which looks like a
   width-dependent defect and is not one: the page had no <main> and no <h1> in any state.
   The masking is axe's passForModal option. Both checks pass when axe believes a modal is
   open, and its isModalOpen heuristic samples five points over the middle of the viewport
   and returns true if one absolute-or-fixed element at least 75% of the viewport in both
   dimensions appears in every stack. Leaflet's full-bleed <canvas> qualifies whenever the
   docked panel is not covering the sample points, and at 375 the full-screen overlay
   qualifies. So a canvas map was being read as a dialog, and two page-level defects were
   invisible in most states because of it.
   That is the argument for enumerating states rather than trusting one: a rule that passes
   is not the same as a rule that has nothing to find, and only a state that removes the
   accidental modal reveals the difference. */
const STATES = [
  {
    key: "map alone",
    alerts: 0,
    async reach(page) {
      if (await page.evaluate(() => !document.getElementById("stations-panel").hidden)) {
        await page.evaluate(() => closeStationsPanel());
      }
      /* ROUND 4: THE ONE THING THIS STATE'S NAME PROMISES, ASSERTED. Neutering
         closeStationsPanel so the panel never closes left all fifteen a11y specs green, and
         this instantiation quietly became a second copy of "panel list". The two pages
         genuinely differ to axe (516 nodes with the skip link reported as an incomplete,
         548 without it), so an accessibility defect that exists only on the panel-closed
         page, which is the app's default at 375 and where a 1280 rider lands after closing
         the drawer, could never be caught here. */
      await expectState(page, "panel closed", "the gate's map-alone state");
    },
    /* MR1 MOVED THESE, and the reason is the fold. `#toggles` and `#status` are inside the
       feed strip, which folds behind the Key button below 700px, so at 375 they are not on
       the page for axe to examine and this list would have failed as the vacuity check it is.
       A state that runs at more than one width can only name targets that exist at all of
       them; the strip and the note get their own state below, at all three widths, which is
       MORE coverage than naming them here ever gave (the Key panel itself was never scanned
       open at 375 before, because the disclosure kept it shut and nothing opened it). */
    targets: ["leaflet-control-zoom", "#panel", "#view-stack"],
  },
  {
    /* MR1: THE HEADER WITH EVERYTHING OPEN, at all three widths. The feed strip's eight
       buttons, the trailing note and the Key panel's sixteen rows are the largest block of
       new text this stage adds, and below 700px they are reachable only through this
       disclosure. Run at NARROW too, because 320 is where the header has the least room and
       the most chance of overlapping something. */
    key: "key open",
    alerts: 0,
    viewports: [DESKTOP, PHONE, NARROW],
    // A STALE FEED, SO THE NOTE HAS SOMETHING TO SCAN. #status is one of this state's targets
    // and it is EMPTY on a healthy page by design, which would make the anti-vacuity check
    // pass on an element axe never looked at. A degraded feed also puts the note in its
    // .error colour, which is the one chrome string that changes colour at all.
    staleRailroad: true,
    async reach(page) {
      if (await page.evaluate(() => !document.getElementById("stations-panel").hidden)) {
        await page.evaluate(() => closeStationsPanel());
      }
      await page.locator("#legend-toggle").click();
      await expect(page.locator("#legend")).toBeVisible();
      await expect(page.locator("#toggles")).toBeVisible();
      await expect(page.locator("#toggle-subway")).toBeVisible();
    },
    targets: ["#toggles", "#status", "#legend", "toggle-subway", "legend-row"],
  },
  {
    key: "panel list",
    alerts: 0,
    async reach(page) {
      if (await page.evaluate(() => document.getElementById("stations-panel").hidden)) {
        await page.locator("#stations-toggle").click();
      }
      await page.locator("#stations-search").fill("times");
      await expect(page.locator("#stations-results button.station-row").first()).toBeVisible();
    },
    // #stations-close is examined here for the first time in the project's history: it is
    // display:none above the breakpoint, so the desktop instantiation still does not see
    // it, and the mobile one does. Listing it is what makes that difference visible.
    targets: ["#stations-panel", "#stations-search", "station-row"],
  },
  {
    key: "panel detail",
    alerts: 0,
    async reach(page) {
      if (await page.evaluate(() => document.getElementById("stations-panel").hidden)) {
        await page.locator("#stations-toggle").click();
      }
      await page.locator("#stations-search").fill("times");
      await page.locator("#stations-results button.station-row").first().click();
      await expect(page.locator("#stations-detail h3")).toBeVisible();
      // THE COUNTDOWN TICK IS STOPPED BEFORE THE SCAN, through the app's own door, and the
      // timer is asserted live first so the stop is not vacuous. The panel repaints its
      // countdowns once a second and renderStationDetail replaces the detail subtree
      // wholesale, so a scan straddling a repaint sees the h3 detached and reports
      // heading-order as undecidable: a finding about our timer, not about the markup.
      // Reproduced by sweeping a delay before analyze(), failing at d=700 in one run and
      // d=800 in the next, which is why a longer wait was never the fix.
      //
      // POLLED, NOT SAMPLED, AND THE DIFFERENCE WAS A ONE-IN-FIVE FAILURE. The h3 waited
      // for above renders from the STATION SELECTION, synchronously; panelTimer is armed
      // by startPanelTick, which stations.js reaches only after the arrivals fetch
      // RESOLVES. So the two are not ordered, and reading the timer once at that instant
      // races the mock: measured on unmodified main, 7 of 30 runs of this state failed,
      // then 6 of 30, at both viewports. stations.spec.js does not carry the bug because
      // it waits for text that comes FROM the payload ("Northbound") before it looks;
      // this state has no such text to wait for, because the whole point of it is to scan
      // the detail subtree whatever the arrivals say. Polling is what orders them.
      //
      // The budget is the 5s this spec already gives assertNothingIsMidTransition, and it
      // is a ceiling rather than a wait: the poll returns as soon as the timer is armed,
      // which is why the suite is no slower for it.
      await expect
        .poll(() => page.evaluate(() => panelTimer !== null), {
          timeout: 5_000,
          message: "the panel countdown must be running for stopping it to mean anything",
        })
        .toBe(true);
      // Stopped in its own step, once the poll has proved there was something to stop.
      await page.evaluate(() => stopPanelArrivals());
    },
    targets: ["#stations-detail", "station-arrivals", "h3"],
  },
  {
    // F11 put a rider-facing alerts block in the panel, above the arrivals board: a
    // heading, a bulleted list on a cream ground with an amber rule, and a muted
    // honesty line when the alert source is stale or held. All of it is new text on a
    // new background, and new muted text on a new ground is exactly where this project
    // has found contrast defects before (see the muted-ink note in style.css, where
    // four greys that all looked fine failed four different surfaces).
    //
    // SCANNED AT 320 AS WELL, which nothing here was before. The panel is where the
    // width bites: at 320 the overlay is full screen, the alert headers wrap hardest,
    // and the list indent has the least room to stay inside its box.
    key: "panel detail with a station suspension",
    alerts: 0,
    stationAlerts: true,
    viewports: [DESKTOP, PHONE, NARROW],
    async reach(page) {
      if (await page.evaluate(() => document.getElementById("stations-panel").hidden)) {
        await page.locator("#stations-toggle").click();
      }
      await page.locator("#stations-search").fill("times");
      await page.locator("#stations-results button.station-row").first().click();
      await expect(page.locator("#stations-detail h3")).toBeVisible();
      // The alerts arrive on their own fetch, so the block is waited for rather than
      // assumed: a scan taken before it renders would certify the state without its
      // subject in it, which is the failure mode assertScanned's targets exist for.
      await expect(page.locator("#stations-detail ul.station-alerts li")).toHaveCount(2);
      // Same countdown-tick reasoning as the state above, and the same polled stop.
      await expect
        .poll(() => page.evaluate(() => panelTimer !== null), {
          timeout: 5_000,
          message: "the panel countdown must be running for stopping it to mean anything",
        })
        .toBe(true);
      await page.evaluate(() => stopPanelArrivals());
    },
    targets: ["#stations-detail", "station-alerts", "h4"],
  },
  {
    key: "popup open with cross-link",
    alerts: 0,
    /* MR4 ADDED 320 TO THIS STATE, so the one surface scanned in both themes is scanned at
       every width the rest of the suite uses. A popup at 320 is the tightest text surface on
       the page (the panel at 320 opted in during MR2 for the same reason), and the dark theme
       is the half that had never been measured there at all: six scans now, three widths by
       two themes, which is what the stage that RELEASES the theme owes the theme. */
    viewports: [DESKTOP, PHONE, NARROW],
    async reach(page) {
      if (await page.evaluate(() => !document.getElementById("stations-panel").hidden)) {
        await page.evaluate(() => closeStationsPanel());
      }
      // SELECTED BY THE PROPERTY THAT MAKES IT THE RIGHT TRAIN, not by position. The first
      // draft took `find((r) => r.marker.getPopup())`, meaning "the first railroad with any
      // popup bound", and every railroad has one. Measured, it opened MNR|mnr-gps-1:
      //   {"text":"MNR · HudsonTrain 1797live GPS","hasCrossLink":false,"buttons":[]}
      // (that capture is history: MR5's ruling Q1 retired the popup's "live GPS" on a FRESH fix,
      // so the same draft today would capture "MNR · HudsonTrain 1797". The defect it records is
      // unchanged, and so is the predicate below that fixed it.)
      // so the state named "with cross-link" scanned a popup that has no buttons at all,
      // and the page-wide gate had never examined a cross-link in any state. The reviewer
      // who found it proved the cost by emptying the cross-link's accessible name: the
      // exact button-name defect A1l injects as its canary, and all fifteen tests passed.
      // The app's own predicate answers it: railroadAtItsStation, the cross-link's gate
      // since 6.3, which reads the served provenance and anchors. Asking the app rather
      // than re-deriving it means the state cannot drift from what the app links. The
      // throw below fails loudly if the fixture ever stops carrying one, rather than
      // silently scanning a different popup, which is how this got past review the once.
      await page.evaluate(() => {
        const placed = [...railroads.values()].find((r) => railroadAtItsStation(r.latest));
        if (!placed) throw new Error("the fixture no longer has a railroad train drawn on its station to cross-link");
        placed.marker.openPopup();
      });
      /* AND WAIT FOR IT TO FINISH OPENING, which round 4 found by watching CI fail on a
         state that is green on this machine every time. Leaflet fades a popup in from
         opacity 0 over 200ms, and a contrast scan taken during the fade measures the
         popup's text against the MAP TILE showing through it. Sampled here at 375:
           frame 0  opacity 0     frame 1  opacity 0     frame 2  opacity 0.083
         The near-black body text survives that; the muted ink does not. CI reported
         color-contrast on exactly the two muted nodes at 1280 (.popup-sub #666,
         .popup-crosslink #1d4ed8) and four nodes at 375, while this machine passed the same
         commit 16 runs out of 16. A gate whose verdict depends on how fast the machine is
         is not a gate. */
      await expectState(
        page,
        ["one popup open", "popup finished opening", "popup has a cross-link"],
        "the gate's cross-link state",
      );
    },
    // The cross-link is named as a target, so the anti-vacuity check fails if the scan
    // stops reaching it. That is the half the first draft was missing: the state reached
    // the wrong popup AND nothing asked whether a cross-link had been examined.
    targets: ["leaflet-popup", "popup-crosslink"],
    // BOTH THEMES, by ruling. A popup is the surface a rider reads longest and the one MR1
    // does not restyle: its vocabulary is MR5's. Scanning it in the dark theme now is how
    // "unchanged" stops being an assumption, and it is what will catch MR5 the first time a
    // popup rule reaches for a token.
    themes: ["light", "dark"],
  },
  {
    key: "banner active",
    alerts: 2,
    async reach(page) {
      if (await page.evaluate(() => !document.getElementById("stations-panel").hidden)) {
        await page.evaluate(() => closeStationsPanel());
      }
      await expect(page.locator(".alert-banner-row").first()).toBeVisible();
    },
    targets: ["alert-banner", "#alert-banner-dismiss"],
  },
  {
    key: "mobile overlay open",
    alerts: 0,
    only: PHONE,
    async reach(page) {
      if (await page.evaluate(() => document.getElementById("stations-panel").hidden)) {
        await page.locator("#stations-toggle").click();
      }
      await expect(page.locator("#stations-panel")).toBeVisible();
      // The state that only exists here: the background is inert, so most of the page is
      // out of the accessibility tree on purpose. #stations-close is visible and scanned.
      await expect(page.locator("#stations-close")).toBeVisible();
    },
    targets: ["#stations-panel", "#stations-close"],
  },
];

/* NOTHING MAY BE MID-TRANSITION WHEN THE SCAN RUNS, AND THIS IS A CONTRAST RULE.

   ROUND 4 FOUND THIS BY WATCHING CI FAIL ON A STATE THAT IS GREEN HERE EVERY TIME. Leaflet
   fades a popup in from opacity 0 over 200ms. A translucent element shows whatever is behind
   it, so axe measures the popup's text against a MAP TILE, and the near-black body text
   survives that while the muted ink does not. CI reported color-contrast on exactly the two
   muted nodes at 1280 and four nodes at 375; this machine passed the same commit sixteen
   runs out of sixteen. A gate whose verdict depends on how fast the machine is is not a gate.

   AND IT IS NOT A POPUP PROBLEM, which is why the wait lives here rather than in that one
   state's reach(). The same-class mutation (a three-second fade-in on the ALERT BANNER,
   which does not animate today) reddens the banner state exactly the same way. Any surface
   that ever animates in would silently start being measured against whatever is behind it.

   So the gate waits for the document to stop animating, and says what it was waiting for.
   Asked of document.getAnimations() rather than of opacity, because a designed translucent
   element (the legend's route-line glyphs are opacity 0.6 by intent) is not a thing to wait
   for, and a running transition is. */
async function assertNothingIsMidTransition(page, label) {
  const running = () =>
    page.evaluate(() =>
      document
        .getAnimations()
        .filter((a) => a.playState === "running")
        /* MR1: AN ENDLESS ANIMATION IS NOT A TRANSITION, and waiting for one is waiting
           forever. This asked for an empty list, which is the right question about a
           TRANSITION: it is a move from one settled state to another, so "still running"
           means the scan is early. The header's clock carries a dot that pulses on a 2s loop
           for as long as the page is open, and it never settles by design; measured, every
           one of the seventeen scans died on its five-second wait rather than on anything it
           found. What the wait protects is a measurement taken between two states, so the
           filter is on the thing that HAS two states. The pulse is still gated and still
           asserted, at motion.spec.js A5b and A5d, which is where a claim about the
           preference belongs; and the element it animates is a 5px square carrying no text,
           so its opacity is nothing axe measures contrast against. */
        .filter((a) => !(a.effect && a.effect.getComputedTiming().iterations === Infinity))
        .map((a) => a.transitionProperty || a.animationName || "an animation")
        .sort(),
    );
  await expect
    .poll(running, { timeout: 5_000 })
    .toEqual([])
    .catch(async () => {
      throw new Error(
        `${label}: the page was still animating when the scan was due (${(await running()).join(", ")}). ` +
          `A translucent element is measured against whatever is behind it, so this would be ` +
          `reported as a contrast defect in the surface rather than as a scan taken too early.`,
      );
    });
}

/* MR1: A THEME AXIS, AND ONLY ONE STATE OPTS INTO IT SO FAR. The page has two themes as of
   that stage and every scan above ran in one of them, so a token that failed only in the dark
   set would ship unseen; the popup state opts in by ruling, because a popup is the surface a
   rider spends the longest reading and it is the one MR1 did NOT restyle. MR4 added 320 to
   that state, and MR4 is also the stage that makes the dark theme reachable at all.

   STILL SET THROUGH applyTheme() AND NOT THROUGH THE BUTTON, and the reason has changed with
   the stage rather than gone away. MR1's reason was that the button was hidden, so a spec that
   clicked it would be testing a control no rider could reach; MR4 released it, and the reason
   now is separation: this is an axe gate over a themed page, not a test of the control. That
   the control works, that its name is the action and that it carries no aria-pressed are
   tests/e2e/theme.spec.js D5a's, and the swap it performs is D5b's. Going through the app's own
   function rather than writing the attribute keeps this on the same path the rider takes. */
async function setTheme(page, theme) {
  await page.evaluate((want) => applyTheme(want), theme);
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

for (const state of STATES) {
  // Two widths unless a state asks for more. The loop used to be viewport-outer with a
  // fixed pair; it is state-outer now so one state can opt into a third width without
  // every other state paying for it.
  for (const viewport of state.viewports ?? [DESKTOP, PHONE]) {
    if (state.only && state.only !== viewport) continue;
    for (const theme of state.themes ?? ["light"]) {
      const label = `${viewport.width} / ${state.key}${theme === "light" ? "" : ` / ${theme}`}`;
      test(`A1w. page-wide axe at ${viewport.width}: ${state.key}${theme === "light" ? "" : ` (${theme})`}`, async ({
        page,
      }) => {
        await page.setViewportSize(viewport);
        await open(page, {
          alerts: state.alerts,
          stationAlerts: state.stationAlerts,
          staleRailroad: state.staleRailroad,
        });
        if (theme !== "light") await setTheme(page, theme);
        await state.reach(page);
        await assertNothingIsMidTransition(page, label);

        // NO include() AT ALL: this is the whole document, which is the deliverable.
        const results = await scanPage(page);
        expect(violations(results), `${label}: page-wide axe violations`).toEqual([]);
        await assertUndecidablesAreKnown(page, results, label);
        assertScanned(results, { targets: state.targets, label });
      });
    }
  }
}

test("A1x. the Key panel's rows are legible, at every width and in both themes", async ({ page }) => {
  /* THE ANSWER TO THE SHAPE ABOVE. axe declines a row that its own panel has clipped, so the
     question is asked here instead, and asked of the COMPUTED values rather than of the
     stylesheet: a row's ink and the header's background are both custom properties, and a
     token edited without measuring is exactly what this catches.

     EVERY ROW, not a sample. The Key panel is the app's one long list of chrome text, the
     rows that fail are by construction the ones a rider has to scroll to, and a spec that
     measured the first three would be measuring the ones axe could already decide.

     BOTH THEMES, because MR1 is the stage that gave this page a second one and nothing in
     this suite had ever scanned it. AND THREE WIDTHS, because the panel is two columns at
     1280 and one below, and the row that straddles the scroll boundary differs at each. */
  for (const viewport of [DESKTOP, PHONE, NARROW]) {
    await page.setViewportSize(viewport);
    await open(page, { alerts: 0 });
    await page.locator("#legend-toggle").click();
    await expect(page.locator("#legend")).toBeVisible();

    for (const theme of ["light", "dark"]) {
      await setTheme(page, theme);

      const measured = await page.evaluate(() => {
        const srgb = (c) => {
          c /= 255;
          return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        };
        const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
        const parse = (value) => {
          const m = String(value).match(/rgba?\(([^)]+)\)/);
          return m ? m[1].split(/[\s,/]+/).filter(Boolean).map(Number).slice(0, 3) : null;
        };
        // The header's surface, read off the element that paints it rather than assumed: if
        // it ever stops being opaque this returns the transparent value and the ratio below
        // goes null, which is a failure rather than a pass.
        const panel = getComputedStyle(document.getElementById("panel")).backgroundColor;
        const surface = /rgba?\([^)]*,\s*0?\.\d+\)/.test(panel) ? null : parse(panel);
        const rows = [...document.querySelectorAll("#legend .legend-row, #legend .legend-note")];
        return {
          surface,
          panel,
          rows: rows.map((row, i) => {
            const ink = parse(getComputedStyle(row).color);
            if (!ink || !surface) return { i, ink: getComputedStyle(row).color, ratio: null };
            const [hi, lo] = [lum(ink), lum(surface)].sort((a, b) => b - a);
            return { i, ink: getComputedStyle(row).color, ratio: +((hi + 0.05) / (lo + 0.05)).toFixed(2) };
          }),
        };
      });

      const label = `${viewport.width} / ${theme}`;
      expect(measured.surface, `${label}: the header surface must be opaque (got ${measured.panel})`).not.toBeNull();
      /* SEVENTEEN, WHICH IS SIXTEEN ROWS PLUS THE ONE NOTE, and the number MOVED rather than
         being relaxed (the ruling that owns the Key panel says so in as many words). MR4 round 2
         merged three regional rail station rows into the one commuter square the map draws and
         three commuter train rows into the two tag bodies it draws, and split the subway station
         row so the transfer ring gets the name finding F16 asked for: 18 - 2 - 1 + 1 = 16, plus
         the note. A literal rather than a range, because a count that tolerated drift would not
         have caught any of the six rows MR3 left describing marks the app had stopped drawing.
         MR5 adds two more for finding F17, the tag's head in its two axes, so eighteen rows plus
         the note is nineteen. */
      expect(measured.rows.length, `${label}: the scan must find rows, or it decides nothing`).toBe(19);
      const dim = measured.rows.filter((r) => r.ratio === null || r.ratio < 4.5);
      expect(dim, `${label}: every Key panel row must clear AA on the header's surface`).toEqual([]);
    }
  }
});

test("A1x2. the Key panel's rail tags are drawn at map scale, so their type is readable", async ({ page }) => {
  /* THE ONE CLAIM IN THIS PANEL THAT NO OTHER SPEC CAN SEE. Every other Key glyph is a SHAPE,
     and A1x above measures a row's ink while A1z measures a glyph's type against what is behind
     it. Neither measures SIZE. The two commuter train rows MR4 round 2 added are the only glyphs
     here that carry type, and their whole design decision is a size: the tag's viewBox is cropped
     to the tag and `.legend-row svg.key-rail-tag { width: 42px }` lets the cell match it, which
     puts the scale at exactly 1.00 and the type at the map's own 8px.

     WITHOUT THIS SPEC THAT RULE IS UNTESTED. Measured: deleting the 42px declaration falls back
     to the shared 16px cell, where a 42-wide viewBox scales to 0.38 and the 8px Archivo renders at
     3.05px, an illegible smudge. Every existing gate stays green through it, and the reason is
     worth stating: the colours do not move (A1z reads computed fill, which is scale-invariant),
     the row count does not move (A1x), the accessible names do not move (P1e, which strips the
     glyph precisely because it is decorative), axe sees an aria-hidden subtree, and no capture is
     diffed byte for byte. A key whose glyph is a smudge is the same defect as a key whose glyph is
     wrong, and this is the assertion that can tell.

     SCALE FROM getScreenCTM RATHER THAN FROM A BOUNDING BOX, because a text element's box is its
     INK and changes with the glyphs in it: "BAB" and "NEC" would give two different answers to one
     question. The CTM is the mapping from user units to CSS pixels, so `a` and `d` ARE the scale,
     and 8 user units times `d` is what a rider's eye gets. */
  await page.setViewportSize(DESKTOP);
  await open(page, { alerts: 0 });
  await page.locator("#legend-toggle").click();
  await expect(page.locator("#legend")).toBeVisible();

  const measured = await page.evaluate(() => {
    const svgs = [...document.querySelectorAll("#legend .legend-row svg.key-rail-tag")];
    return svgs.map((svg) => {
      const box = svg.viewBox.baseVal;
      const rect = svg.getBoundingClientRect();
      return {
        viewBox: [box.width, box.height],
        // The 1px padding on .legend-row svg is on the OUTSIDE of the content box (box-sizing
        // content-box), so the drawn area is the client rect less two pixels each way.
        drawn: [+(rect.width - 2).toFixed(2), +(rect.height - 2).toFixed(2)],
        // The class the map's own tags carry must NOT be here: a11y.spec.js A1z4 counts
        // svg.rail-tag and asserts each belongs to a .rail-tag-marker.
        classes: svg.getAttribute("class"),
        texts: [...svg.querySelectorAll("text")].map((text) => {
          const ctm = text.getScreenCTM();
          const declared = parseFloat(text.getAttribute("font-size") || getComputedStyle(text).fontSize);
          return {
            content: text.textContent,
            declared,
            scale: +ctm.d.toFixed(3),
            renderedPx: +(declared * ctm.d).toFixed(2),
            weight: getComputedStyle(text).fontWeight,
          };
        }),
      };
    });
  });

  // TWO ROWS, because the pair is the claim: one solid body and one outlined.
  expect(measured.length, "the Key panel draws exactly two rail tags, one body state each").toBe(2);

  for (const tag of measured) {
    expect(tag.classes, "a Key glyph must not take the map's rail-tag class (A1z4 counts those)").toBe(
      "key-rail-tag",
    );
    // THE CELL MATCHES THE viewBox, which is what makes the scale 1 rather than a coincidence of
    // two numbers that happen to agree today.
    expect(tag.drawn, `the cell must be the viewBox's own size, got ${tag.drawn} for ${tag.viewBox}`).toEqual(
      tag.viewBox,
    );
    expect(tag.texts.length, "each tag prints an agency glyph and a branch code").toBe(2);
    for (const text of tag.texts) {
      expect(text.declared, `"${text.content}" is declared at the map's 8 user units`).toBe(8);
      expect(text.scale, `"${text.content}" is drawn at scale 1, which is map scale`).toBeCloseTo(1, 2);
      expect(
        text.renderedPx,
        `"${text.content}" renders at ${text.renderedPx}px, and the map draws this type at 8`,
      ).toBeGreaterThanOrEqual(8);
      // Archivo is variable 100 to 900, so 800 is a real weight rather than a synthesised bold.
      expect(text.weight, `"${text.content}" keeps the tag's 800`).toBe("800");
    }
  }
});

test("A1z. the deciders: every named undecidable is answered by measurement", async ({ page }) => {
  // THE OTHER HALF OF THE EXCEPTION LIST. Each shape above says axe cannot decide something;
  // this says what the answer actually is, computed the same way helpers.js computes it.
  // Without this the list would be three excuses rather than three admissions of a TOOL
  // limit, which is the distinction the conversion rule exists to protect.

  /* THE PAIRING CHECK, FIRST, because it is the rule the list can lose silently. A shape
     may be added at any time by anyone chasing a red build, and the only thing standing
     between "a named exception" and "a suppression with a sentence attached" is that it
     names the spec that answers the question axe declined.
     RESOLVED, NOT PATTERN-MATCHED. The first version asserted the sentence MATCHED
     /\w+\.spec\.js A\w+/, which asks only whether it looks like a citation. The
     adversarial round rewrote a decider to "nosuchfile.spec.js A0z composites..." and it
     passed. A citation that resolves to nothing is worse than no citation, because it
     reads as evidence. */
  for (const shape of UNDECIDABLE_SHAPES) {
    expect(
      citedPairs(shape.decider || ""),
      `the undecidable "${shape.name}" must name the spec that decides it, as file and test id`,
    ).not.toEqual([]);
    expect(
      danglingCitations(shape.decider || ""),
      `the undecidable "${shape.name}" cites a test that does not exist. Either the spec was ` +
        `renamed and this decider must follow it, or this exception has lost its decider and ` +
        `must become a defect to fix.`,
    ).toEqual([]);
  }

  await page.setViewportSize(DESKTOP);
  await open(page, { alerts: 0 });

  /* AND THE KEY PANEL IS OPENED, FOR THE SAME REASON THE POPUP IS BELOW. MR1 made the Key a
     disclosure at every width (it used to be unconditionally open above 700px), and shape 1
     of UNDECIDABLE_SHAPES excuses every single-character glyph drawn inside an SVG icon,
     which is exactly what the legend's subway "A" is. A closed panel renders none of them,
     so this spec would go on passing while the one class of glyph it was written to decide
     had left the page. */
  await page.locator("#legend-toggle").click();
  await expect(page.locator("#legend")).toBeVisible();

  /* AND A POPUP IS OPENED, BECAUSE THE DECIDER SAYS SO. Round 4: shape 3 of
     UNDECIDABLE_SHAPES excuses BOTH `.leaflet-control-zoom-out span` and
     `.leaflet-popup-close-button span`, and its decider sentence names both. But this spec
     never opened a popup, so the close glyph did not exist while it ran. Measured: the popup's
     only Close control recoloured to #f2f2f2 (1.09:1 against the popup, effectively invisible)
     left all 162 specs green, because axe downgrades a one-character glyph to `incomplete`,
     the shape list excuses that incomplete, and the spec named as its decider was not looking.
     Degrading the OTHER glyph the same sentence names was caught, which is what made this a
     half-covered exception rather than an uncovered one. */
  await page.evaluate(() => {
    const placed = [...railroads.values()].find((r) => railroadAtItsStation(r.latest));
    if (!placed) throw new Error("the fixture no longer has a railroad train drawn on its station to open");
    placed.marker.openPopup();
  });
  await expectState(page, ["one popup open", "popup finished opening"], "A1z measures the popup close glyph");

  const measured = await page.evaluate(() => {
    const srgb = (c) => {
      const v = c / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
    const ratio = (a, b) => {
      const [hi, lo] = lum(a) >= lum(b) ? [lum(a), lum(b)] : [lum(b), lum(a)];
      return (hi + 0.05) / (lo + 0.05);
    };
    const parse = (css) => (css.match(/[\d.]+/g) || []).slice(0, 3).map(Number);
    const over = (fg, bg, alpha, base) => {
      const composited = bg.map((c, i) => c * alpha + base[i] * (1 - alpha));
      return ratio(fg, composited);
    };

    /* EVERY RENDERED SVG GLYPH, MEASURED AGAINST THE SHAPE IT SITS ON. Round 2 found the
       old decider for this shape named a spec that samples HTML badges and no SVG at all,
       so the glyphs were excepted from axe and decided by nothing. A legend swatch recoloured
       to 1.14:1 passed the whole suite.

       WHICH SHAPE, THOUGH. Round 3: this read querySelector("rect, circle, path, polygon"),
       which is FIRST IN DOCUMENT ORDER, and document order is paint order in SVG, so it
       reads the BOTTOM shape while a rider sees the top one. It is right today only because
       every marker in this app happens to be one shape and one glyph; the moment a marker
       gains a halo, a shadow or a second plate, the decider silently starts measuring a
       colour nobody sees. Measured under mutation: a subway icon drawn as a black plate
       under a #eee plate under white text reads 21:1 by document order and 1.13:1 by what
       is actually behind the character.
       So: the candidates are the shapes whose box CONTAINS the glyph's centre, and the
       answer is the LAST of them, because later siblings paint over earlier ones.

       AND NOTHING HERE IS ALLOWED TO GO QUIET. A glyph with no shape under it, a backing
       painted fill="none", a colour this parser cannot read: each used to produce NaN,
       and `NaN < 4.5` is false, so an unmeasurable glyph passed as if it had been measured.
       Every one of them now returns a null ratio with a `why`, and null is a failure. */
    const glyphs = [...document.querySelectorAll("svg text")]
      // MR1: A GLYPH THAT IS NOT RENDERED HAS NO CONTRAST, and reporting it as unmeasurable
      // is a false positive rather than the vigilance the rest of this scan is. A hidden
      // element's rect is all zeros, so its centre is (0, 0) and nothing is under it: before
      // this filter, closing any disclosure that contains a glyph failed this spec with "no
      // painted shape sits under this glyph's centre". The Key panel is such a disclosure
      // now, and the spec OPENS it (see the caller) so its glyphs are still in the sample;
      // this filter is for the ones a state genuinely does not render.
      .filter((text) => {
        const b = text.getBoundingClientRect();
        return b.width > 0 && b.height > 0;
      })
      .map((text) => {
      const svg = text.closest("svg");
      const shapes = svg ? [...svg.querySelectorAll("rect, circle, ellipse, path, polygon")] : [];
      const box = text.getBoundingClientRect();
      const cx = box.left + box.width / 2;
      const cy = box.top + box.height / 2;
      const under = shapes.filter((el) => {
        const b = el.getBoundingClientRect();
        return b.width > 0 && b.height > 0 && cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom;
      });
      const shape = under.length ? under[under.length - 1] : null;
      const show = (el) =>
        el ? `${el.tagName}[${under.indexOf(el) + 1} of ${under.length} under the glyph]` : "none";

      // ROUND 4: THE PAINTED FILL, WHICH IS THE COMPUTED ONE. This read the presentation
      // ATTRIBUTE first, and CSS beats a presentation attribute in the cascade, so any
      // stylesheet rule setting `fill` changed what the rider sees while the decider went on
      // reporting the old attribute. Measured: `.legend-row svg text { fill: #2a6ac8 }` drives
      // the legend glyph to 1.16:1 on its #1f5fbf plate and left all 162 specs green, while
      // the identical regression written into the attribute was caught. getComputedStyle
      // resolves the attribute too when no rule wins, so nothing is lost by asking it alone.
      const fill = (el) => (el ? getComputedStyle(el).fill : null);
      const hex = (v) => {
        if (!v || v === "none" || v === "transparent" || v === "currentColor") return null;
        if (v.startsWith("#")) {
          const h = v.slice(1);
          const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
          const rgb = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
          return rgb.some(Number.isNaN) ? null : rgb;
        }
        const rgb = parse(v);
        return rgb.length === 3 && rgb.every(Number.isFinite) ? rgb : null;
      };
      const rawFg = fill(text);
      const rawBg = fill(shape);
      const fg = hex(rawFg);
      const bg = hex(rawBg);
      const why = !shape
        ? "no painted shape sits under this glyph's centre, so there is nothing to measure it against"
        : !fg
          ? `the glyph's own fill (${rawFg}) is not a colour this decider can read`
          : !bg
            ? `the backing ${show(shape)} is filled ${rawBg}, which is not a colour, so what the rider sees behind the glyph is whatever is under the SVG`
            : null;
      return {
        text: (text.textContent || "").trim(),
        backing: show(shape),
        ratio: why ? null : ratio(fg, bg),
        why,
      };
    });

    const zoom = document.querySelector(".leaflet-control-zoom-out");
    const zoomStyle = getComputedStyle(zoom);

    /* The popup close button paints on the popup's own wrapper rather than on a background of
       its own, and Leaflet appends it to `.leaflet-popup` rather than to that wrapper, so no
       ANCESTOR of it has a background at all: the white it visually sits on belongs to a
       sibling it overlaps. Walking parents returns null; asking what is painted under its own
       centre returns the wrapper. */
    const closeBtn = document.querySelector(".leaflet-popup-close-button");
    const surfaceOf = (el) => {
      const r = el.getBoundingClientRect();
      const stack = document.elementsFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      for (const n of stack) {
        const bg = getComputedStyle(n).backgroundColor;
        // The alpha channel exists only in the rgba() form; asking a plain rgb() for its
        // "last number" returns the blue channel, which is how the first draft of this walk
        // rejected an opaque white surface for having an alpha of 255.
        const alpha = bg.startsWith("rgba(") ? Number(bg.slice(0, -1).split(",").pop()) : 1;
        if (bg && bg !== "transparent" && alpha === 1) return parse(bg);
      }
      return null;
    };
    const closeSurface = closeBtn ? surfaceOf(closeBtn) : null;
    const closeInk = closeBtn ? parse(getComputedStyle(closeBtn).color) : null;
    const attribution = document.querySelector(".leaflet-control-attribution");
    const attrStyle = getComputedStyle(attribution);
    const attrBg = parse(attrStyle.backgroundColor);
    /* THE ALPHA, READ AS AN ALPHA. This took the last number before the ")" and called it the
       alpha, which is true of `rgba(r, g, b, a)` and false of `rgb(r, g, b)`: on an opaque
       background it returned the BLUE CHANNEL. It went unnoticed for as long as Leaflet's own
       translucent default was in force, and MR1 made the attribution opaque (var(--surface)),
       at which point the blend below produced ratios of 2118582 over black and -0.11 over
       white. A four-component colour has an alpha and a three-component one is opaque. */
    const attrParts = (String(attrStyle.backgroundColor).match(/rgba?\(([^)]+)\)/) || [, ""])[1]
      .split(/[\s,/]+/)
      .filter(Boolean)
      .map(Number);
    const attrAlpha = attrParts.length > 3 ? attrParts[3] : 1;

    return {
      // The zoom control is opaque white with its own border, so this one is a plain
      // computation that axe simply refused to run on a one-character glyph.
      zoomGlyph: ratio(parse(zoomStyle.color), parse(zoomStyle.backgroundColor)),
      // The other glyph the same exception excuses. Null when the button is missing or its
      // surface cannot be resolved, and null is asserted as a failure rather than skipped.
      closeGlyph: closeInk && closeSurface ? ratio(closeInk, closeSurface) : null,
      // The attribution is translucent over live imagery, so it is bounded rather than
      // computed: over BLACK and over WHITE are the two extremes any tile can be, and a
      // ratio that clears AA against the worse of them clears it against every tile.
      attributionOverBlack: over(parse(attrStyle.color), attrBg, attrAlpha, [0, 0, 0]),
      attributionOverWhite: over(parse(attrStyle.color), attrBg, attrAlpha, [255, 255, 255]),
      glyphs,
    };
  });

  // 4.5:1 is the AA floor for body text. The zoom glyph is a non-text indicator and owes
  // only 3:1, but it clears the text floor comfortably, so the stricter number is asserted:
  // a regression that dropped it below 4.5 is worth knowing about even while it stays legal.
  expect(measured.zoomGlyph, `zoom glyph contrast (measured ${measured.zoomGlyph.toFixed(2)})`).toBeGreaterThanOrEqual(
    4.5,
  );
  // The popup's Close control, on the same non-text floor and asserted at the text floor for
  // the same reason as the zoom glyph: it clears it comfortably, so a regression below 4.5 is
  // worth knowing about while it is still legal.
  expect(
    measured.closeGlyph,
    `popup close glyph contrast (measured ${measured.closeGlyph === null ? "UNMEASURABLE" : measured.closeGlyph.toFixed(2)})`,
  ).toBeGreaterThanOrEqual(4.5);
  // 3:1 is the non-text floor and these are single characters carrying route identity, which
  // is the same call A4g makes for the badges: they are read, so the text floor is the honest
  // one, and every glyph on this page clears it.
  expect(measured.glyphs.length, "the scan must find rendered SVG glyphs, or it decides nothing").toBeGreaterThan(0);
  const dimGlyphs = measured.glyphs.filter((g) => g.ratio === null || g.ratio < 4.5);
  expect(dimGlyphs, "every SVG glyph must clear AA against the shape it is drawn on").toEqual([]);

  expect(
    Math.min(measured.attributionOverBlack, measured.attributionOverWhite),
    `attribution over the worst possible tile (black ${measured.attributionOverBlack.toFixed(2)}, ` +
      `white ${measured.attributionOverWhite.toFixed(2)})`,
  ).toBeGreaterThanOrEqual(4.5);
});

test("A1z3. the station name labels are legible over any tile, in both themes", async ({ page }) => {
  /* MR2'S OWN UNDECIDABLE, ANSWERED. A station's name is a permanent Leaflet tooltip drawn
     straight onto the basemap: transparent background, ink text, and four stacked
     text-shadows in --halo that give the ink something to sit on. axe reports exactly what
     it should ("background color could not be determined because element contains an image
     node"), so the answer is bounded here the way the attribution's is, over BOTH extremes a
     tile can be rather than over whichever tile happened to load.

     WHAT THIS DOES NOT CLAIM. A text-shadow is a spread, not a fill: between the strokes of
     a character the tile is closer to the surface than the halo is. So this measures the
     halo as the effective background, which is the right answer at the glyph's edge (where
     legibility is decided) and optimistic in the counters of an "o". Stating that is the
     point of a decider: it says what was measured rather than that something was checked.

     BOTH THEMES, although only one is reachable by a rider until MR4 (ruling R2). The dark
     theme's tokens ship now and are what MR4 will unhide, and nothing a rider can see would
     catch a mistake in them in the meantime, which is precisely why a measurement has to. */
  await page.setViewportSize(DESKTOP);
  await open(page, { alerts: 0 });
  /* Zoom 12 is the map's opening view and the band where hub labels show, so the fixture's two
     SUBWAY stations are drawn; asserted rather than assumed, because a spec measuring nothing
     passes.

     NINE NOW: TWO SUBWAY, FIVE RAIL AND TWO FERRY DOCKS, and the count has grown once per
     stage because each stage puts another family's names on this pane. MR3 added five (LIRR
     Jamaica, Metro-North Grand Central and three NJ Transit stations, gated from zoom 11 by
     their own band, so at 12 they are all on) and MR4 adds the ferry's two docks, which the
     design asks for and which no dock has ever had.

     EVERY FAMILY IS MEASURED BY THE SAME LOOP RATHER THAN EXCUSED FROM IT, because a dock's
     name is ink on a tile exactly as a subway station's is and neither MR3 nor MR4 gave any of
     them a different treatment to be trusted about. None carries `hub`: that is a subway
     transfer station by one predicate, which is why allAreLabels below still holds.

     THE THREE COUNTS ARE POSITIVE CLASSES AND THAT IS MR4's CARRY-FORWARD. This spec could
     have asked for "seven, of which five are rail"; what it asks now is how many each family
     has, so the next family to join this pane changes one number rather than silently
     inflating someone else's. */
  await expect(page.locator(".leaflet-tooltip")).toHaveCount(9);
  await expect(page.locator(".leaflet-tooltip.subway")).toHaveCount(2);
  await expect(page.locator(".leaflet-tooltip.rail")).toHaveCount(5);
  await expect(page.locator(".leaflet-tooltip.ferry")).toHaveCount(2);

  for (const theme of ["light", "dark"]) {
    if (theme !== "light") await setTheme(page, theme);
    const measured = await page.evaluate(() => {
      const srgb = (c) => {
        const v = c / 255;
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      };
      const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
      const ratio = (a, b) => {
        const [hi, lo] = lum(a) >= lum(b) ? [lum(a), lum(b)] : [lum(b), lum(a)];
        return (hi + 0.05) / (lo + 0.05);
      };
      // A four-component colour has an alpha and a three-component one is opaque, which is
      // the bug the attribution's own parser above records having had.
      const rgba = (css) => {
        const parts = (String(css).match(/rgba?\(([^)]+)\)/) || [, ""])[1]
          .split(/[\s,/]+/)
          .filter(Boolean)
          .map(Number);
        if (parts.length < 3) return null;
        return { rgb: parts.slice(0, 3), alpha: parts.length > 3 ? parts[3] : 1 };
      };
      const over = (colour, base) => colour.rgb.map((c, i) => c * colour.alpha + base[i] * (1 - colour.alpha));

      const labels = [...document.querySelectorAll(".leaflet-tooltip")];
      return {
        // THE SCOPE CLOSURE the exception depends on: this app binds tooltips in exactly one
        // place, so every tooltip is a station label. If a second surface ever binds one,
        // this fails rather than the exception quietly widening to cover it.
        allAreLabels: labels.every((el) => el.classList.contains("stn-label")),
        // And they are out of the reading order, which is the other half of the statement:
        // 496 bare place names whose only information is where they are would be noise, and
        // the station panel is the text surface that carries them properly.
        allAriaHidden: labels.every((el) => el.getAttribute("aria-hidden") === "true"),
        rows: labels.map((el) => {
          const style = getComputedStyle(el);
          const ink = rgba(style.color);
          // The halo is the shadow's colour; the shadow shorthand puts it first in Chromium.
          const halo = rgba(style.textShadow);
          // Null rather than a NaN that would slip past a `< 4.5` comparison, which is the
          // mistake the glyph measurement above records having made and fixed.
          if (!ink || !halo) {
            return { name: el.textContent, overBlack: null, overWhite: null, why: "no halo to measure against" };
          }
          return {
            name: el.textContent,
            overBlack: ratio(ink.rgb, over(halo, [0, 0, 0])),
            overWhite: ratio(ink.rgb, over(halo, [255, 255, 255])),
            // A background of its own would make all of this moot, and the design says there
            // is none; a rule that put one back would be measured here as a pass and is
            // therefore asserted separately.
            background: style.backgroundColor,
            pointerEvents: style.pointerEvents,
          };
        }),
      };
    });

    expect(measured.allAreLabels, `${theme}: every tooltip on this page must be a station label`).toBe(true);
    expect(measured.allAriaHidden, `${theme}: every station label must be out of the reading order`).toBe(true);
    expect(measured.rows.length, `${theme}: the scan must find labels, or it decides nothing`).toBeGreaterThan(0);

    for (const row of measured.rows) {
      expect(row.why ?? null, `${theme}: "${row.name}" could not be measured`).toBe(null);
      const worst = Math.min(row.overBlack, row.overWhite);
      expect(
        worst,
        `${theme}: "${row.name}" over the worst possible tile ` +
          `(black ${row.overBlack.toFixed(2)}, white ${row.overWhite.toFixed(2)})`,
      ).toBeGreaterThanOrEqual(4.5);
      // The halo IS the background; a real one would mean the label had stopped being the
      // thing this measurement describes.
      expect(row.background, `${theme}: "${row.name}" must have no background of its own`).toMatch(
        /rgba\(0, 0, 0, 0\)|transparent/,
      );
      // And a name never swallows a click meant for the dot under it.
      expect(row.pointerEvents, `${theme}: "${row.name}" must not take pointer events`).toBe("none");
    }
  }
});

test("A1z4. every commuter rail tag's type is legible on the block it is printed on, in both themes", async ({
  page,
}) => {
  /* MR3'S OWN UNDECIDABLE, ANSWERED, and answered off the DRAWN page rather than off the
     arithmetic that built it. The tags overlap each other at regional zoom, so axe stops at a
     neighbouring marker and cannot find the rect under the type; there is nothing wrong with
     the contrast and nothing axe can do about it. This reads both colours out of the rendered
     SVG and computes the ratio.

     WHY BOTH HALVES OF THE TAG. The agency block prints paper on ink (or ink on paper on an
     outlined body), which is a THEME pair and moves with data-theme; the branch block prints
     the feed's ink on the feed's colour, which does not. Two different ways to be wrong, and
     only one of them is what the node test covers, so both are measured here.

     WHAT THIS DOES NOT CLAIM. It measures the type against the block it sits on, which is
     opaque, so unlike A1z3 there is no tile to bound and no optimism to declare. What it
     cannot see is a tag a NEIGHBOURING tag covers: that is a density question rather than a
     contrast one, it is what the zoom presets and the feed toggles exist for, and it is
     recorded as a finding in docs/reviews/map-redesign-rounds.md rather than smuggled in here.

     BOTH THEMES, although only one is reachable until MR4 (ruling R2), for the reason A1z3
     gives: the dark tokens ship now and nothing a rider can see would catch a mistake in them. */
  await page.setViewportSize(DESKTOP);
  await open(page, { alerts: 0 });
  // The stock fixture's rail trains: two railroad and four NJ Transit. Asserted, because a
  // spec measuring nothing passes.
  await expect(page.locator("svg.rail-tag")).toHaveCount(6);

  for (const theme of ["light", "dark"]) {
    if (theme !== "light") await setTheme(page, theme);
    const measured = await page.evaluate(() => {
      const srgb = (c) => {
        const v = c / 255;
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      };
      const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
      const ratio = (a, b) => {
        const [hi, lo] = lum(a) >= lum(b) ? [lum(a), lum(b)] : [lum(b), lum(a)];
        return (hi + 0.05) / (lo + 0.05);
      };
      const rgb = (css) => {
        const parts = (String(css).match(/rgba?\(([^)]+)\)/) || [, ""])[1]
          .split(/[\s,/]+/)
          .filter(Boolean)
          .map(Number);
        return parts.length >= 3 ? parts.slice(0, 3) : null;
      };
      const tags = [...document.querySelectorAll("svg.rail-tag")];
      const rows = [];
      for (const svg of tags) {
        const outlined = svg.classList.contains("rail-tag-outlined");
        const rects = [...svg.querySelectorAll("rect")];
        const texts = [...svg.querySelectorAll("text")];
        texts.forEach((text, i) => {
          /* WHICH RECT EACH GLYPH SITS ON, read by geometry rather than by index, because the
             two bodies have different rect counts (a solid body has a paper backing, two
             blocks and no stripe; an outlined one has a box and a stripe). The glyph's own x
             is inside exactly one block, so the block is found by containing it. */
          const x = Number(text.getAttribute("x"));
          const under = rects
            .filter((r) => {
              const rx = Number(r.getAttribute("x"));
              const rw = Number(r.getAttribute("width"));
              const rh = Number(r.getAttribute("height"));
              // The stripe is 2.5 tall and sits under the type rather than behind it, so a
              // block is a rect as tall as the tag; that is what excludes it.
              return x >= rx && x <= rx + rw && rh >= 10;
            })
            .pop();
          const ink = rgb(getComputedStyle(text).fill);
          const paint = under ? rgb(getComputedStyle(under).fill) : null;
          rows.push({
            block: i === 0 ? "agency" : "branch",
            body: outlined ? "outlined" : "solid",
            glyph: text.textContent,
            ratio: ink && paint ? ratio(ink, paint) : null,
            why: ink && paint ? null : "no block found under the glyph",
          });
        });
      }
      return {
        // THE SCOPE CLOSURE this exception depends on, the same one A1z3 makes for tooltips:
        // railTagSvg is the only thing that writes this class, so every svg.rail-tag is a rail
        // train's tag. A second surface adopting it fails here rather than widening the
        // exception quietly.
        allAreTags: tags.every((svg) => svg.closest(".rail-tag-marker") !== null),
        // And every one is out of the reading order, which is the other half of the statement.
        allAriaHidden: tags.every((svg) => svg.getAttribute("aria-hidden") === "true"),
        rows,
      };
    });

    expect(measured.allAreTags, `${theme}: every svg.rail-tag must belong to a rail tag marker`).toBe(true);
    expect(measured.allAriaHidden, `${theme}: every tag must be out of the reading order`).toBe(true);
    // Two glyphs per tag, six tags: twelve measurements, or the loop below decides nothing.
    expect(measured.rows.length, `${theme}: the scan must find type to measure`).toBe(12);

    for (const row of measured.rows) {
      expect(row.why ?? null, `${theme}: ${row.body} ${row.block} "${row.glyph}"`).toBe(null);
      expect(
        row.ratio,
        `${theme}: the ${row.block} glyph "${row.glyph}" on a ${row.body} body reads ` +
          `${row.ratio?.toFixed(2)} against the block under it`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  }
});

test("A1l. the gate has teeth: a defect anywhere on the page IS caught", async ({ page }) => {
  // This spec tests the GATE, not the markup, and it is here because the gate silently lost
  // its teeth once already in the scoped era (the include() array). Now that the scope is
  // the document, the second half of the old version of this spec is gone: it asserted that
  // a defect in the LEGEND was deliberately NOT reported, which was the scoping decision of
  // its time and is exactly what A4 abolished. The legend is now in scope, so the same
  // injection must be caught there too, and that is what the second half asserts instead.
  await page.setViewportSize(DESKTOP);
  await open(page, { alerts: 0 });

  const injectInto = async (containerId) => {
    await page.evaluate((id) => {
      const b = document.createElement("button");
      b.id = "a11y-canary";
      document.getElementById(id).appendChild(b);
    }, containerId);
    const results = await scanPage(page);
    await page.evaluate(() => document.getElementById("a11y-canary").remove());
    return results.violations.map((v) => v.id);
  };

  expect(await injectInto("stations-panel"), "an unnamed button in the panel must be reported").toContain(
    "button-name",
  );
  expect(await injectInto("panel"), "and one in the legend, which used to be out of scope").toContain("button-name");
});

/* KEYBOARD INVARIANTS, PAGE-WIDE, AND WHY THEY ARE NOT AN ORDER LIST.
   The obvious way to pin a tab order is to write the expected sequence down and compare.
   That spec fails the day a control is added, and it fails identically whether the addition
   was a defect or an improvement, so it teaches everyone to update the literal without
   reading it. Worse, it says nothing about the properties that actually matter to a rider
   driving this page from the keyboard.
   So the walk is real (Tab is pressed, activeElement is read) and what it asserts are
   PROPERTIES OF EVERY STOP plus one property of the walk itself. A new control passes for
   free if it is named, painted, out of the marker layer and in DOM order; a new control
   that is none of those things fails whichever rung it broke.
   The rank assertion that remains is about ONE element and is a contract in its own right:
   the skip link must be first or it cannot be a skip link. */
const TAB_LIMIT = 80;

/* THE WALK HAS TO START FROM THE TOP OF THE DOCUMENT, and neither obvious way gets it
   there. body.focus() is a no-op because <body> is not focusable, and blur() clears
   activeElement without clearing the document's SEQUENTIAL FOCUS NAVIGATION STARTING POINT,
   which is the separate piece of state Tab actually consults. Both were measured against
   the overlay-open state, where focus starts inside the panel by the A1 contract: each one
   left the starting point where it was, the first Tab walked straight out of the document,
   and the walk reported ZERO stops on a page that has plenty.
   Making <body> temporarily focusable and focusing it is the move that works, because the
   starting point becomes an element that precedes everything else. tabindex="-1" keeps it
   out of the tab order while it is there, and it is removed immediately afterwards, which
   does not disturb a starting point already set.
   The assertion is inside the helper on purpose: a reset that silently stopped working
   would turn every invariant below into a tautology about an empty walk, which is exactly
   the failure this phase keeps finding in its own tests. */
async function resetFocus(page) {
  await page.evaluate(() => {
    document.body.setAttribute("tabindex", "-1");
    document.body.focus();
    document.body.removeAttribute("tabindex");
  });
  expect(
    await page.evaluate(() => document.activeElement === document.body),
    "the walk must start from the top of the document",
  ).toBe(true);
}

async function tabWalk(page) {
  const stops = [];
  for (let i = 0; i < TAB_LIMIT; i++) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body || el === document.documentElement) return null;
      const text = (node) => (node ? (node.textContent || "").replace(/\s+/g, " ").trim() : "");
      // A pragmatic accessible name rather than the full W3C computation: the sources this
      // page actually uses, in the order the algorithm consults them.
      //
      // NAME FROM CONTENT IS NOT UNIVERSAL, and the first draft of this walk treated it as
      // if it were. Mutation caught it: deleting the map container's aria-label left all
      // fifteen tests green, because the container's descendant text (Leaflet's own
      // attribution) stood in for a name it does not actually have. ARIA gives a name from
      // content only to roles that take one, and a region is not among them, so descendant
      // text is a fallback ONLY for the tags below. Everything else must be named out loud.
      const NAME_FROM_CONTENT = new Set(["a", "button", "summary", "option", "h1", "h2", "h3", "h4", "h5", "h6"]);
      const labelled = el.getAttribute("aria-labelledby");
      const wrapping = el.closest("label");
      const associated = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
      const named = labelled
        ? labelled
            .split(/\s+/)
            .map((id) => text(document.getElementById(id)))
            .join(" ")
        : "";
      const explicit =
        el.getAttribute("aria-label") || named || text(associated) || text(wrapping) || el.getAttribute("title") || "";
      const name = explicit || (NAME_FROM_CONTENT.has(el.tagName.toLowerCase()) ? text(el) : "");
      const box = el.getBoundingClientRect();
      return {
        id: el.id || null,
        tag: el.tagName.toLowerCase(),
        cls: typeof el.className === "string" ? el.className.split(" ")[0] : null,
        name: (name || "").trim(),
        // Measured WHILE FOCUSED, which is the only moment that matters: the skip link is
        // visually hidden until exactly then, so a check taken any other time would either
        // fail it wrongly or have to except it by name.
        painted: box.width > 0 && box.height > 0,
        inPanel: !!el.closest("#stations-panel"),
        inertly: !!el.closest("[inert]"),
      };
    });
    if (stop === null) return { stops, escaped: true };
    stops.push(stop);
  }
  return { stops, escaped: false };
}

function assertWalkInvariants(walk, label, floor) {
  const { stops, escaped } = walk;
  const show = (s) => `${s.tag}${s.id ? "#" + s.id : s.cls ? "." + s.cls : ""}`;

  // NO TRAP. The walk must run out of stops and hand focus back to the document rather than
  // cycle forever. This is the invariant behind A4's choice of inert over a focus trap, and
  // it is the one property a rider cannot work around if it is wrong.
  expect(escaped, `${label}: Tab must eventually leave the page, not cycle (${stops.length} stops, no exit)`).toBe(
    true,
  );
  expect(
    stops.length,
    `${label}: the walk must find real stops (found ${stops.map(show).join(", ")})`,
  ).toBeGreaterThanOrEqual(floor);

  const unnamed = stops.filter((s) => !s.name);
  expect(unnamed.map(show), `${label}: every keyboard stop must announce itself`).toEqual([]);

  const unpainted = stops.filter((s) => !s.painted);
  expect(unpainted.map(show), `${label}: a stop with no box is a stop a sighted rider loses`).toEqual([]);

  // The A2 policy, re-asserted from the walk rather than from a DOM query: markers are not
  // the keyboard path, the station panel is.
  const markers = stops.filter((s) => s.cls && s.cls.endsWith("-marker"));
  expect(markers.map(show), `${label}: no vehicle marker may be a keyboard stop`).toEqual([]);

  const inert = stops.filter((s) => s.inertly);
  expect(inert.map(show), `${label}: focus must never land inside an inert subtree`).toEqual([]);

  return stops;
}

/* THE OTHER HALF: THE CONTROLS THAT MUST BE STOPS ARE STILL STOPS.
   Every invariant in assertWalkInvariants is a property of whatever the walk HAPPENS to
   find, so all of them hold vacuously for a control that has quietly left the tab order.
   Measured by mutation in round 1: tabindex="-1" on all seven layer toggles removed every
   layer control from keyboard reach and all 153 e2e tests stayed green.
   THE LIST IS EXPLICIT, AND THE FIRST VERSION OF THIS COMMENT CLAIMED OTHERWISE. It said the
   list was "derived from the page", which it is not. A genuinely derived list is not
   available, because "every focusable thing" includes Leaflet's own controls and the map's
   attribution links, which this app does not own and cannot promise. What is available is an
   explicit list checked for BEING REACHED, plus the discipline that adding a control means
   adding it here.
   VISIBLE AND LIVE, KEYED THE WAY THE WALK KEYS ITS STOPS. Controls that exist in some states
   and not others are filtered by their box rather than named twice, so this is honest in
   whichever state it is called from; and round 3 found the two sides keyed differently, the
   check pushing a literal selector while the walk recorded ids, so ".station-row" could only
   ever fail. Both sides now produce id-or-first-class.
   INERT IS SUBTRACTED, and it has to be, because unreachable is what inert MEANS. With the
   375 overlay up, twelve owned controls are still painted behind it and every one of them is
   deliberately out of the tab order; requiring them would be requiring the bug A4 fixed.
   Same predicate the walk records its stops with, so the two halves cannot drift.
   AND WHAT THE FILTER REMOVES, THE CALLER MUST STILL VOUCH FOR. Two filters (a box, an inert
   ancestor) sit between the owned list and the requirement, so a control can leave the
   requirement silently and the check still passes, which is precisely how ".station-row"
   spent a round in the list without ever being required of anything. `must` is the caller's
   claim about which controls this state genuinely offers; a name in it that the filters drop
   is a loud failure rather than an invisible subtraction. */
async function assertOwnedControlsReachable(page, walk, label, must = []) {
  const required = await page.evaluate(() => {
    const owned = [
      "#stations-skip",
      "#map",
      "#stations-toggle",
      "#legend-toggle",
      "#stations-search",
      "#stations-close",
      "#alert-banner-dismiss",
      // MR1: the layer toggles are BUTTONS in the feed strip now, not checkboxes in labels,
      // and the header brought two more controls this list owes a reachability claim for:
      // the theme toggle and the view presets.
      "#toggles button",
      "#theme-toggle",
      "#view-stack button",
      // MR2: the subway key's bullets are controls now (route focus), and the Names toggle
      // joined #view-stack above. A bullet has no id, so they collapse to one key here and
      // the claim is that the key is reachable at all; chrome.spec.js D2a is what says all
      // twenty-three are buttons with names.
      "#subway-key button",
      "#route-clear",
      ".station-row",
    ];
    const key = (el) => (el.id ? el.id : (el.className || "").toString().split(" ")[0]);
    const seen = new Set();
    for (const sel of owned) {
      for (const el of document.querySelectorAll(sel)) {
        const box = el.getBoundingClientRect();
        if (box.width > 0 && box.height > 0 && !el.closest("[inert]")) seen.add(key(el));
      }
    }
    return [...seen];
  });

  const missing = must.filter((k) => !required.includes(k));
  expect(
    missing,
    `${label}: this state was declared to offer these controls and does not, so the ` +
      `reachability check below would have been silently narrower than it claims. Either the ` +
      `state is not the one the spec set up, or the control is hidden or inert here.`,
  ).toEqual([]);

  const walked = new Set(walk.stops.flatMap((s) => [s.id, s.cls].filter(Boolean)));
  const unreachable = required.filter((k) => !walked.has(k));
  expect(
    unreachable,
    `${label}: these controls are visible and live but not reachable by Tab. A control that ` +
      `leaves the tab order is invisible to every other invariant in this spec.`,
  ).toEqual([]);
}

for (const viewport of [DESKTOP, PHONE]) {
  test(`A1y. keyboard invariants at ${viewport.width}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await open(page, { alerts: 2 });
    await expect(page.locator(".alert-banner-row").first()).toBeVisible();

    // ORDER FOLLOWS THE DOM, asserted once for the whole document. A positive tabindex is
    // the one construct that can reorder the walk invisibly, and it does so across the
    // entire page rather than locally, so it is checked as a page property rather than
    // per stop.
    const positive = await page.evaluate(() =>
      [...document.querySelectorAll("[tabindex]")]
        .filter((el) => Number(el.getAttribute("tabindex")) > 0)
        .map((el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "")),
    );
    expect(positive, `${viewport.width}: a positive tabindex reorders the whole page`).toEqual([]);

    /* ROUND 3: .station-row WAS ADDED TO THE OWNED LIST AND WAS INERT, because no walk ever
       had rows on screen when the check ran. An entry in a required-controls list that cannot
       exist is worse than no entry: it reads as coverage.
       AT DESKTOP THE PANEL IS DOCKED, so searching it adds rows to the page the default walk
       already crosses. At 375 opening the panel IS the overlay state, and doing it here would
       turn this into a second overlay walk: measured, the default walk fell from sixteen
       stops to three. So the rows are covered at 375 by the overlay walk below, which is
       where they exist, and the visible-only filter makes this honest in both. */
    if (viewport === DESKTOP) {
      await page.locator("#stations-search").fill("times");
      await expectState(page, ["panel open", "panel results listed"], "A1y default walk at 1280");
      /* AND A BUS ROUTE IS DRAWN, so #route-clear EXISTS. Round 4 found it in exactly the
         position ".station-row" had been in a round earlier: on the owned list, 0x0 in every
         state this spec walks, therefore dropped by the visible filter and required of
         nothing. tabindex="-1" on it left all 162 e2e specs green. #route-clear is the only
         control that removes a drawn route line (buses.js holds its sole listener), so a
         rider who draws one with a pointer and then drives from the keyboard would have the
         line on their map until reload.
         Opened through the marker rather than clicked, which is the keyboard path A7a pins:
         the popup, the line, the banner and this button all arrive together. */
      await page.evaluate(() => {
        const [id] = [...buses.keys()];
        buses.get(id).marker.openPopup();
      });
      await expect(page.locator("#route-clear")).toBeVisible();
    }

    await resetFocus(page);
    const walk = await tabWalk(page);
    // The floors are anti-vacuity guards, set to what each state genuinely offers rather
    // than to a round number: a walk that quietly found nothing would otherwise satisfy
    // every invariant above by having nothing to violate.
    assertWalkInvariants(walk, `${viewport.width} / default`, 8);
    // A RANK ASSERTION ABOUT ONE ELEMENT, which is a contract rather than an order literal:
    // a skip link that is not the first stop is not a skip link.
    expect(
      walk.stops[0] && walk.stops[0].id,
      `${viewport.width}: the skip link must be the first stop or it skips nothing`,
    ).toBe("stations-skip");

    // WHAT EACH DEFAULT STATE GENUINELY OFFERS, claimed out loud, and the claim was wrong
    // the first time in a way worth keeping: #stations-close is display:none above the
    // breakpoint, because a docked drawer covers nothing and has nothing to exit. It is
    // required of the overlay walk instead, which is the state it exists for. At 375 the
    // panel is shut, so rows do not exist and the layer toggles sit behind the legend
    // disclosure.
    await assertOwnedControlsReachable(
      page,
      walk,
      `${viewport.width} / default`,
      viewport === DESKTOP
        ? ["stations-skip", "map", "stations-search", "station-row", "toggle-buses", "route-clear"]
        : ["stations-skip", "map", "stations-toggle", "legend-toggle"],
    );

    // THE STATE INERT EXISTS FOR gets its own walk at the width where it exists. With the
    // overlay open the background is inert, so the same invariants must hold AND the walk
    // must stay inside the panel: that is what inertness buys, and A6n already measured
    // that it buys it without trapping anyone.
    if (viewport === PHONE) {
      await page.locator("#stations-toggle").click();
      await expect(page.locator("#stations-panel")).toBeVisible();
      // Searched rather than empty, so the walk crosses the result buttons the panel builds
      // at runtime and not only the two controls the markup ships with.
      await page.locator("#stations-search").fill("times");
      await expectState(page, ["panel open", "panel results listed"], "A1y overlay walk");
      await resetFocus(page);
      const overlay = await tabWalk(page);
      assertWalkInvariants(overlay, "375 / overlay open", 3);
      // The overlay is the ONLY 375 state where a station row exists, so this is the call
      // that makes ".station-row" mean something at this width. Everything else the page
      // owns is inert underneath and correctly absent from the requirement.
      await assertOwnedControlsReachable(page, overlay, "375 / overlay open", [
        "stations-search",
        "stations-close",
        "station-row",
      ]);
      const outside = overlay.stops.filter((s) => !s.inPanel && s.id !== "stations-skip");
      expect(
        outside.map((s) => `${s.tag}${s.id ? "#" + s.id : ""}`),
        "375 / overlay open: nothing behind the overlay may still be reachable",
      ).toEqual([]);
    }
  });
}
