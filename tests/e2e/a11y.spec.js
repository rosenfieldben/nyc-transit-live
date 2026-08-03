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

const DESKTOP = { width: 1280, height: 720 };
const PHONE = { width: 375, height: 667 };

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
async function open(page, { alerts = 0 } = {}) {
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) =>
    json(route, { ...fixtures.alerts(), alerts: Array.from({ length: alerts }, (_, i) => agencyAlert(i + 1)) });
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
    name: "a single-character glyph drawn inside an SVG icon",
    rule: "color-contrast",
    // axe declines these because the visible text is one character: it cannot tell a route
    // letter from a decorative mark. The colours are perfectly determinable, which is why
    // this one has a real decider rather than an excuse.
    message: /too short to determine if it is actual text content|only non-text characters/,
    // Matched on substrings rather than on adjacency: an element-anchored identity is a
    // PATH, so it carries intermediate ancestors and same-tag index suffixes that a rigid
    // "parent then child" pattern would miss for no good reason.
    where: (id) =>
      /svg[^ ]* text/.test(id) ||
      /leaflet-control-zoom-out[^ ]* span/.test(id) ||
      /leaflet-popup-close-button[^ ]* span/.test(id),
    decider:
      "layout.spec.js A4g computes every rendered route colour's contrast in-page with the same " +
      "sRGB/luminance formula as helpers.js, and a11y.spec.js A1z below measures the zoom glyph " +
      "and the popup close glyph against their own control backgrounds.",
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
    },
    targets: ["leaflet-control-zoom", "#toggles", "#status"],
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
      const live = await page.evaluate(() => {
        const running = panelTimer !== null;
        stopPanelArrivals();
        return running;
      });
      expect(live, "the panel countdown must be running for stopping it to mean anything").toBe(true);
    },
    targets: ["#stations-detail", "station-arrivals", "h3"],
  },
  {
    key: "popup open with cross-link",
    alerts: 0,
    async reach(page) {
      if (await page.evaluate(() => !document.getElementById("stations-panel").hidden)) {
        await page.evaluate(() => closeStationsPanel());
      }
      // SELECTED BY THE PROPERTY THAT MAKES IT THE RIGHT TRAIN, not by position. The first
      // draft took `find((r) => r.marker.getPopup())`, meaning "the first railroad with any
      // popup bound", and every railroad has one. Measured, it opened MNR|mnr-gps-1:
      //   {"text":"MNR · HudsonTrain 1797live GPS","hasCrossLink":false,"buttons":[]}
      // so the state named "with cross-link" scanned a popup that has no buttons at all,
      // and the page-wide gate had never examined a cross-link in any state. The reviewer
      // who found it proved the cost by emptying the cross-link's accessible name: the
      // exact button-name defect A1l injects as its canary, and all fifteen tests passed.
      // The registry already carries the app's own derived answer: `placed`, set from
      // isPlacedRailroad when the record is built. Reading the app's flag rather than
      // re-deriving it means the state cannot drift from what the app calls placed. The
      // throw below fails loudly if the fixture ever stops carrying one, rather than
      // silently scanning a different popup, which is how this got past review the once.
      await page.evaluate(() => {
        const placed = [...railroads.values()].find((r) => r.placed);
        if (!placed) throw new Error("the fixture no longer has a placed railroad train to cross-link");
        placed.marker.openPopup();
      });
      await expect(page.locator(".leaflet-popup-content")).toBeVisible();
      await expect(page.locator(".leaflet-popup-content .popup-crosslink")).toBeVisible();
    },
    // The cross-link is named as a target, so the anti-vacuity check fails if the scan
    // stops reaching it. That is the half the first draft was missing: the state reached
    // the wrong popup AND nothing asked whether a cross-link had been examined.
    targets: ["leaflet-popup", "popup-crosslink"],
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

for (const viewport of [DESKTOP, PHONE]) {
  for (const state of STATES) {
    if (state.only && state.only !== viewport) continue;
    test(`A1w. page-wide axe at ${viewport.width}: ${state.key}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, { alerts: state.alerts });
      await state.reach(page);

      // NO include() AT ALL: this is the whole document, which is the deliverable.
      const results = await new AxeBuilder({ page }).analyze();
      const label = `${viewport.width} / ${state.key}`;
      expect(violations(results), `${label}: page-wide axe violations`).toEqual([]);
      await assertUndecidablesAreKnown(page, results, label);
      assertScanned(results, { targets: state.targets, label });
    });
  }
}

test("A1z. the deciders: every named undecidable is answered by measurement", async ({ page }) => {
  // THE OTHER HALF OF THE EXCEPTION LIST. Each shape above says axe cannot decide something;
  // this says what the answer actually is, computed the same way helpers.js computes it.
  // Without this the list would be three excuses rather than three admissions of a TOOL
  // limit, which is the distinction the conversion rule exists to protect.

  // THE PAIRING CHECK, FIRST, because it is the rule the list can lose silently. A shape
  // may be added at any time by anyone chasing a red build, and the only thing standing
  // between "a named exception" and "a suppression with a sentence attached" is that it
  // names the spec that answers the question axe declined. Asserted as a reference to a
  // real spec file and a real test id, so the sentence cannot be prose alone.
  for (const shape of UNDECIDABLE_SHAPES) {
    expect(shape.decider || "", `the undecidable "${shape.name}" must name the spec that decides it`).toMatch(
      /\b[\w.-]+\.spec\.js\s+A\w+/,
    );
  }

  await page.setViewportSize(DESKTOP);
  await open(page, { alerts: 0 });

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

    const zoom = document.querySelector(".leaflet-control-zoom-out");
    const zoomStyle = getComputedStyle(zoom);
    const attribution = document.querySelector(".leaflet-control-attribution");
    const attrStyle = getComputedStyle(attribution);
    const attrBg = parse(attrStyle.backgroundColor);
    const attrAlpha = Number((attrStyle.backgroundColor.match(/[\d.]+\)$/) || ["1)"])[0].replace(")", "")) || 1;

    return {
      // The zoom control is opaque white with its own border, so this one is a plain
      // computation that axe simply refused to run on a one-character glyph.
      zoomGlyph: ratio(parse(zoomStyle.color), parse(zoomStyle.backgroundColor)),
      // The attribution is translucent over live imagery, so it is bounded rather than
      // computed: over BLACK and over WHITE are the two extremes any tile can be, and a
      // ratio that clears AA against the worse of them clears it against every tile.
      attributionOverBlack: over(parse(attrStyle.color), attrBg, attrAlpha, [0, 0, 0]),
      attributionOverWhite: over(parse(attrStyle.color), attrBg, attrAlpha, [255, 255, 255]),
    };
  });

  // 4.5:1 is the AA floor for body text. The zoom glyph is a non-text indicator and owes
  // only 3:1, but it clears the text floor comfortably, so the stricter number is asserted:
  // a regression that dropped it below 4.5 is worth knowing about even while it stays legal.
  expect(measured.zoomGlyph, `zoom glyph contrast (measured ${measured.zoomGlyph.toFixed(2)})`).toBeGreaterThanOrEqual(
    4.5,
  );
  expect(
    Math.min(measured.attributionOverBlack, measured.attributionOverWhite),
    `attribution over the worst possible tile (black ${measured.attributionOverBlack.toFixed(2)}, ` +
      `white ${measured.attributionOverWhite.toFixed(2)})`,
  ).toBeGreaterThanOrEqual(4.5);
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
    const results = await new AxeBuilder({ page }).analyze();
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
      await expect(page.locator("#stations-results button.station-row").first()).toBeVisible();
      await resetFocus(page);
      const overlay = await tabWalk(page);
      assertWalkInvariants(overlay, "375 / overlay open", 3);
      const outside = overlay.stops.filter((s) => !s.inPanel && s.id !== "stations-skip");
      expect(
        outside.map((s) => `${s.tag}${s.id ? "#" + s.id : ""}`),
        "375 / overlay open: nothing behind the overlay may still be reachable",
      ).toEqual([]);
    }
  });
}
