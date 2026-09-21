// MR5: the popup's surface, asserted where the ruling put it.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// THE RULING, AND WHY IT NEEDS A TEST AT ALL. README section 5 draws the popup as
// `color-mix(in srgb, var(--surface) 94%, transparent)` over a backdrop blur. It ships at full
// --surface opacity instead, and the reason is MR1's finding F1 about the same drawing one
// surface out: axe cannot resolve the contrast of text over a translucent surface whose backdrop
// is a tile IMAGE, and opacity took MR1's undecidable set from nine entries to one. Measured on
// this branch at 94%, every popup's text came back `incomplete` at 1280, 375 and 320 in both
// themes, and the dark theme's real color-contrast violation on the head's ink and .popup-sub was
// reported ONLY as undecidable: the translucency hid a serious failure rather than obscuring a
// safe one.
//
// SO THIS IS A GUARD AGAINST THE DRAWING COMING BACK. A later stage reading section 5 and
// "restoring the design's own spelling" would reintroduce a value that takes four popup surfaces
// out of axe's reach, and the comment explaining why is in style.css one scroll from the rule.
// Both the alpha and the two spellings it could arrive in are asserted absent.
//
// AND color-mix IS NAMED SEPARATELY, because it is not only the alpha that was a problem: it
// computes to a `color(srgb ... / 0.94)` value, and every contrast reader in this repository
// parses `rgba()` alone. layout.spec.js A4g's nearest-opaque-ancestor walk skipped the popup and
// measured a route-coloured head against --bg two elements further up; tests/e2e/contrast.js,
// popups.spec.js D6a's first draft and axe's own parser read it no better. A translucent popup
// spelled that way would be undecidable AND invisible to the tests that would have said so.
//
// IN NODE RATHER THAN THE BROWSER, because the claim is about the STYLESHEET. A browser test
// compares two resolved values and passes on a page where the rule was deleted; this fails when
// the declaration changes, which is the direction the regression travels.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const CSS = readFileSync(join(__dirname, "style.css"), "utf8");

/* The two token blocks, by their selectors rather than by position: a block that moves in the
   file must not quietly stop being read. `:root, :root[data-theme="light"]` is one rule with two
   selectors, which is why the light pattern is matched loosely on the selector list. */
function blockFor(theme) {
  const selector = theme === "dark" ? ':root\\[data-theme="dark"\\]' : ':root,\\s*:root\\[data-theme="light"\\]';
  const m = new RegExp(`${selector}\\s*\\{([\\s\\S]*?)\\n\\}`).exec(CSS);
  assert.ok(m, `style.css still declares a ${theme} token block`);
  return m[1];
}

// One declaration's value, with the comments in the block stripped first: these tokens carry long
// comments that mention other token names, and a naive match would read a comment's words as a
// declaration's value.
function declared(block, name) {
  const stripped = block.replace(/\/\*[\s\S]*?\*\//g, "");
  const m = new RegExp(`(?:^|;|\\n)\\s*${name}\\s*:\\s*([^;]+);`).exec(stripped);
  assert.ok(m, `the block still declares ${name}`);
  return m[1].trim();
}

// The popup's own rule, which styles the wrapper and the tip together.
function popupSurfaceRule() {
  const m = /\.leaflet-popup-content-wrapper,\s*\.leaflet-popup-tip\s*\{([\s\S]*?)\n\}/.exec(CSS);
  assert.ok(m, "style.css still styles the popup wrapper and tip together");
  return m[1];
}

/* EVERY RULE THAT PAINTS A POPUP, and this file's own review finding is why it exists. The
   translucency and the blur are forbidden of the POPUP, but both absences were asserted of the
   shared wrapper+tip rule alone, and the popup has eight rules. `.leaflet-popup-content-wrapper`
   has a second rule of its own two lines below the first (the ink edge), so an `opacity: 0.94` or a
   `backdrop-filter` added THERE draws exactly the regression these tests exist to stop and passed
   both of them. The gap is the fourth defect shape this phase keeps producing, one tier down: an
   assertion over part of a class, passing over the rest of it.

   BY SELECTOR RATHER THAN BY A LIST, so it widens on its own: a ninth popup rule is scanned the day
   it is written, with nothing here to update. The count below is not a census of the popup's rules
   and is not pinned to one; it is there so that a regex which stopped matching fails loudly instead
   of passing over nothing, which is the shape in the paragraph above.

   COMMENTS ARE STRIPPED FIRST, for `declared`'s reason one function up: these rules carry long
   comments that name `backdrop-filter`, `color-mix` and the 94% in prose, and a scan that read them
   would fail on the explanation rather than on the page. The body pattern takes no braces, so a
   rule nested in a media query is found as itself and the `@media` prelude is not mistaken for a
   selector. */
function popupRules() {
  const stripped = CSS.replace(/\/\*[\s\S]*?\*\//g, "");
  const found = [...stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((m) => m[1].includes("leaflet-popup"))
    .map((m) => [m[1].trim().replace(/\s+/g, " "), m[2]]);
  assert.ok(found.length >= 6, `style.css still has the popup's rules (found ${found.length})`);
  return found;
}

test("MR5: the popup's surface is the token at full strength", () => {
  const body = popupSurfaceRule();
  assert.match(body, /background:\s*var\(--surface\);/, "the surface is --surface and nothing else");
  assert.match(body, /color:\s*var\(--ink\);/, "and its ink is the token, not Leaflet's #333");
  assert.match(body, /border-radius:\s*0;/, "section 5 has no radius");
  assert.match(body, /box-shadow:\s*var\(--shadow\);/, "and the shadow is the token");
  /* ONE background declaration IN THE WHOLE POPUP, not two: a second one is how a translucent value
     comes back as a "progressive enhancement" over an opaque fallback, which is exactly the shape
     this forbids, and the fallback and the enhancement are most naturally written in two rules. So
     the count is over every rule that paints a popup rather than over this one. Measured: of the
     popup's eight rules exactly one declares a background, and it is this one. */
  const paints = popupRules().filter(([, rule]) => /(^|\n)\s*background:/.test(rule));
  assert.equal(paints.length, 1, `exactly one popup rule declares a background (${paints.map((p) => p[0])})`);
  assert.equal(paints[0][0], ".leaflet-popup-content-wrapper, .leaflet-popup-tip", "and it is the surface rule");
  assert.equal((body.match(/(^|\n)\s*background:/g) || []).length, 1, "declared once inside it");
});

test("MR5: the design's translucency cannot come back by either spelling, in any of the popup's rules", () => {
  /* BOTH SPELLINGS, because they fail differently. color-mix computes to a color() no contrast
     reader in this repo parses, so a popup spelled that way is undecidable to axe AND invisible
     to the tests that would report it. An rgba is parseable and still undecidable to axe, because
     axe will not composite a background it can see an image through. Neither is allowed.

     IN EVERY POPUP RULE, which is the correction: an `opacity: 0.94` on the wrapper's own edge rule
     is the same undecidable popup, and a scan of the surface rule alone said nothing about it. */
  for (const [selector, rule] of popupRules()) {
    assert.doesNotMatch(rule, /color-mix/, `${selector}: color-mix reintroduces the 94% and is unparseable besides`);
    assert.doesNotMatch(rule, /rgba?\([^)]*\/[^)]*\)/, `${selector}: a slash-alpha rgb() reintroduces the 94%`);
    assert.doesNotMatch(rule, /rgba\(/, `${selector}: so does a four-argument rgba()`);
    assert.doesNotMatch(rule, /opacity:/, `${selector}: and so does an opacity on the box`);
  }
});

test("MR5: the blur went with the translucency, as MR1's F1 took it off the header", () => {
  /* A RULE MEASURED TO PAINT NOTHING IS NOT KEPT WITH A TEST SAYING SO, which is the operator's
     ruling and the correction of this test's own first draft: it used to assert the blur PRESENT
     and assert that the comment above it explained why it was inert. That is a guard on an
     explanation rather than on the page.

     THE MEASUREMENT IS UNCHANGED. backdrop-filter filters what is behind the element and the
     element's own background then paints over it, so at alpha 1 with no radius none of the filtered
     backdrop is ever visible. MR1's finding F1 took the filter off the header along with the
     header's 90%, and this is the same pair one surface out. Asserted as an ABSENCE, in the same
     words helpers.test.js's A3 sweep uses for #panel, so the two surfaces read alike. */
  for (const [selector, rule] of popupRules()) {
    assert.doesNotMatch(rule, /backdrop-filter/, `${selector}: the popup must not blur a backdrop it hides`);
  }
  // And the header's, unchanged since MR1, so this is one rule for both and not a popup exception.
  const headerRule = /#panel \{([\s\S]*?)\n\}/.exec(CSS);
  assert.ok(headerRule, "#panel must still exist in style.css");
  assert.doesNotMatch(headerRule[1], /backdrop-filter/, "the header must not blur its backdrop either");
});

test("MR5: the ink edge is on the wrapper alone, never on the rotated tip", () => {
  /* The tip is one square rotated 45 degrees, so a border-left on it paints a diagonal stripe
     across the arrow rather than a rule down the popup's side. The edge therefore has its own
     rule, and this asserts the shared one does NOT carry it: an edit that tidies the two together
     would look like a simplification and would draw a stripe. popups.spec.js D6a measures the
     rendered result in both directions. */
  assert.doesNotMatch(popupSurfaceRule(), /border-left/, "the shared wrapper+tip rule takes no edge");
  const own = /\.leaflet-popup-content-wrapper\s*\{([\s\S]*?)\n\}/.exec(CSS);
  assert.ok(own, "the wrapper still has a rule of its own");
  assert.match(own[1], /border-left:\s*2px solid var\(--ink\);/);
});

test("MR5: helpers.js's POPUP_SURFACE_FALLBACK is the light theme's own --surface", () => {
  /* THE ONE PLACE THE COLOUR IS WRITTEN TWICE, and the reason is the one paperColor() and
     inkColor() have their literals for: a caller with no stylesheet applied, which is every node
     test in this repo. readableInk needs a background STRING, so a popup head built outside a
     browser has to be given one, and the honest default is the token's own light value rather
     than the `#ffffff` a Leaflet popup used to be. Asserted against style.css here so it cannot
     drift from the token it stands in for. */
  const { POPUP_SURFACE_FALLBACK } = require("./helpers.js");
  assert.equal(POPUP_SURFACE_FALLBACK.toLowerCase(), declared(blockFor("light"), "--surface").toLowerCase());
});

test("MR5: neither theme grew a channel token for an alpha nobody needs now", () => {
  // --surface-rgb existed only to spell the 94% in a form contrast readers could parse. With the
  // surface opaque there is no consumer, and an unused token is a colour written twice waiting to
  // drift. Asserted absent so it cannot come back without its rule coming back too.
  for (const theme of ["light", "dark"]) {
    assert.doesNotMatch(blockFor(theme), /--surface-rgb/, `${theme} declares a channel token nothing reads`);
  }
});
