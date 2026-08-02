// A3: the mobile layout, under 700px.
//
// The phase's own subject, so the specs here are deliberately about what a THUMB and a
// keyboard can reach rather than about pixels. The A3 inventory measured the geometry;
// what it could not measure by bounding box is whether a control can actually be
// operated, and the defect these specs exist for was invisible to every box measurement
// on the page: #stations-toggle was the right size, in the viewport, visible, and
// covered by the alert banner.
//
// Same hermetic harness as the rest of the suite.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const PHONE = { width: 375, height: 667 };
const NARROW = { width: 320, height: 640 };
// The breakpoint itself and the first width above it. MOBILE_MAX_WIDTH_PX is 700 and the
// media query is `max-width: 700px`, so 700 is the last MOBILE width and 701 is the
// first roomy one. Both are tested because a handoff defect lives in exactly one of them.
const BOUNDARY_MOBILE = { width: 700, height: 720 };
const BOUNDARY_ROOMY = { width: 701, height: 720 };

// An agency-wide alert, which is the state that produced the defect. Without one the
// banner renders nothing at all and every assertion below would pass vacuously.
async function withBanner(page) {
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "mobile-1",
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
  return ctx;
}

async function open(page) {
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
      timeout: 15_000,
    })
    .toBeGreaterThan(5);
}

for (const [label, viewport] of [
  ["320", NARROW],
  ["375", PHONE],
  ["700, the last mobile width", BOUNDARY_MOBILE],
  ["701, the first roomy width", BOUNDARY_ROOMY],
]) {
  test(`A6a. the Stations button is tappable with an alert showing at ${label}`, async ({ page }) => {
    // THE REPRODUCTION, PINNED AS A REAL CLICK. Measured before the fix at 320 and 375:
    // document.elementFromPoint at the centre of #stations-toggle returned
    // div.alert-banner-row, and a real Playwright click timed out on actionability
    // because the banner sat on top of it. Every bounding-box check on the page passed
    // throughout: the button was 240x29, inside the viewport, visible and enabled.
    //
    // So this spec clicks. Playwright's actionability check is the assertion: it waits
    // for the element to receive the event, and a covered control never does. A test
    // that compared rectangles would have been green for the entire life of the defect,
    // which is exactly how the defect survived A2's 24px floor work.
    //
    // The Stations panel is the whole text path to arrivals, and an agency-wide alert is
    // precisely when a rider needs it, so this is the worst possible pairing and not an
    // edge case.
    await page.setViewportSize(viewport);
    await withBanner(page);
    await open(page);
    await expect(page.locator("#alert-banner-dismiss")).toBeVisible();

    // What is actually on top, reported before the click so a failure says WHY rather
    // than only that a click timed out.
    const onTop = await page.evaluate(() => {
      const el = document.getElementById("stations-toggle");
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      if (!top) return "nothing";
      return top.id ? `#${top.id}` : `${top.tagName.toLowerCase()}.${String(top.className).trim().split(/\s+/)[0]}`;
    });
    expect(onTop, "the Stations button must be the topmost element at its own centre").toBe(
      "#stations-toggle",
    );

    // The click itself, with a short timeout so a covered control fails fast and loudly
    // rather than hanging the suite for thirty seconds.
    await page.locator("#stations-toggle").click({ timeout: 5_000 });
    await expect(page.locator("#stations-panel")).toBeVisible();
    await expect(page.locator("#stations-toggle")).toHaveAttribute("aria-expanded", "true");
  });
}

test("A6b. crossing the 700px boundary does not hand the overlap back", async ({ page }) => {
  // THE HANDOFF. The banner is repositioned by a media query and the panel's layout
  // changes with it, so the interesting moment is the crossing rather than either
  // steady state: a rule that only applies in one direction, or a JS-held state that
  // does not re-evaluate, shows up here and nowhere else. A rider rotating a phone into
  // landscape crosses this line without touching anything.
  await page.setViewportSize(BOUNDARY_MOBILE);
  await withBanner(page);
  await open(page);
  await expect(page.locator("#alert-banner-dismiss")).toBeVisible();

  const topAtToggle = () =>
    page.evaluate(() => {
      const el = document.getElementById("stations-toggle");
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return top && top.id === "stations-toggle";
    });

  // Both directions, and back, because a one-way test cannot see a rule that fails to
  // un-apply.
  expect(await topAtToggle(), "covered at 700 before crossing").toBe(true);
  await page.setViewportSize(BOUNDARY_ROOMY);
  expect(await topAtToggle(), "covered at 701 after crossing up").toBe(true);
  await page.setViewportSize(BOUNDARY_MOBILE);
  expect(await topAtToggle(), "covered at 700 after crossing back down").toBe(true);
  await page.setViewportSize({ width: 1280, height: 720 });
  expect(await topAtToggle(), "covered at 1280, where the panel docks").toBe(true);

  // And the document still does not scroll sideways at any of them, so the banner's
  // repositioning cannot fix the overlap by pushing itself off the screen.
  for (const viewport of [BOUNDARY_MOBILE, BOUNDARY_ROOMY, NARROW, PHONE]) {
    await page.setViewportSize(viewport);
    const overflow = await page.evaluate(() => {
      const de = document.documentElement;
      return de.scrollWidth - de.clientWidth;
    });
    expect(overflow, `horizontal overflow at ${viewport.width}px`).toBe(0);
  }
});

test("A6c. the legend collapses on a phone and is open where there is room", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);

  // Closed by default at phone width, per the phase decision.
  await expect(page.locator("#legend-toggle")).toBeVisible();
  await expect(page.locator("#legend")).toBeHidden();
  await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "false");

  // FOCUS STAYS PUT, both ways. The legend expands in place, so nothing the rider is
  // holding is destroyed and there is nowhere to send focus; moving it would be the rude
  // case this project keeps designing out.
  await page.locator("#legend-toggle").focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#legend")).toBeVisible();
  await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#legend-toggle")).toBeFocused();

  await page.keyboard.press("Enter");
  await expect(page.locator("#legend")).toBeHidden();
  await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator("#legend-toggle")).toBeFocused();

  // AND THE ROTATION CASE. Widening past the breakpoint must open the legend without a
  // click, because a screen with room for it showing a collapsed legend is the bug this
  // listener exists to prevent. aria-expanded has to follow, or a screen reader is told
  // the opposite of what is drawn.
  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(page.locator("#legend")).toBeVisible();
  await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#legend-toggle")).toBeHidden();
});

test("A6d. the stations panel is a full-width overlay, with the A1 focus contract intact", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);

  await page.locator("#stations-toggle").click();
  const box = await page.evaluate(() => {
    const r = document.getElementById("stations-panel").getBoundingClientRect();
    return { left: Math.round(r.left), width: Math.round(r.width), viewport: document.documentElement.clientWidth };
  });
  expect(box.left, "the overlay starts at the left edge").toBe(0);
  expect(box.width, "and spans the full width").toBe(box.viewport);

  // THE A1 CONTRACT IS UNCHANGED, which is the point: A3 moved the geometry and nothing
  // else. The skip link is still the first tab stop.
  await page.keyboard.press("Escape");
  await expect(page.locator("#stations-panel")).toBeHidden();
  await expect(page.locator("#stations-toggle"), "Escape returns focus to the opener").toBeFocused();

  // And the toggle closes it too, from the keyboard, landing focus in the same place.
  await page.locator("#stations-toggle").press("Enter");
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-toggle").press("Enter");
  await expect(page.locator("#stations-panel")).toBeHidden();
  await expect(page.locator("#stations-toggle")).toBeFocused();
});

test("A6e. a popup never exceeds the phone's viewport", async ({ page }) => {
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);

  await page.evaluate(() => {
    const [id] = [...buses.keys()];
    buses.get(id).marker.openPopup();
  });
  await expect(page.locator(".leaflet-popup-content")).toBeVisible();

  const fit = await page.evaluate(() => {
    const r = document.querySelector(".leaflet-popup").getBoundingClientRect();
    return { left: Math.round(r.left), right: Math.round(r.right), viewport: document.documentElement.clientWidth };
  });
  expect(fit.left, "a popup must not start off the left edge").toBeGreaterThanOrEqual(0);
  expect(fit.right, "or end past the right edge").toBeLessThanOrEqual(fit.viewport);
});

test("A6f. every control this app owns shows a focus ring, at 3:1 or better", async ({ page }) => {
  // A1 wrote the focus-ring rule for the station panel and called it the pattern to
  // spread; until A3 everything else fell back to whatever the browser drew, which over
  // Leaflet's controls is a thin default ring on live map imagery. This samples the
  // controls a rider actually operates, in the focused state, and measures the ring
  // rather than trusting the stylesheet.
  await page.setViewportSize(PHONE);
  await withBanner(page);
  await open(page);

  // Open what needs opening so every sampled control exists.
  await page.evaluate(() => {
    const [id] = [...buses.keys()];
    buses.get(id).marker.openPopup();
  });
  await expect(page.locator(".leaflet-popup-close-button")).toBeVisible();

  const controls = [
    "#legend-toggle",
    "#stations-toggle",
    "#alert-banner-dismiss",
    "#toggles input",
    ".leaflet-control-zoom-in",
    ".leaflet-popup-close-button",
  ];

  const measured = [];
  for (const selector of controls) {
    // focus() rather than a click: :focus-visible is exactly the distinction being
    // tested, and a mouse click is the case that must NOT ring.
    await page.locator(selector).first().focus();
    measured.push(
      await page.evaluate((sel) => {
        const srgb = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
        const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
        const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
        const parse = (s) => {
          const m = String(s).match(/rgba?\(([^)]+)\)/);
          if (!m) return null;
          const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
          return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 };
        };
        const el = document.querySelector(sel);
        // The element that actually carries the ring may be an ancestor: the layer
        // toggles ring their LABEL via :focus-within, because the label is the target a
        // rider sees while the checkbox is what receives focus.
        const ringed = [el, el.closest("label")].filter(Boolean).find((n) => {
          const cs = getComputedStyle(n);
          return cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0;
        });
        if (!ringed) return { sel, width: 0, ratio: 0 };
        const cs = getComputedStyle(ringed);
        const ring = parse(cs.outlineColor);
        // Composited against the nearest opaque ancestor, the same rule A4g uses.
        let surface = [255, 255, 255];
        for (let n = ringed; n && n !== document.documentElement; n = n.parentElement) {
          const bg = parse(getComputedStyle(n).backgroundColor);
          if (bg && bg.a === 1) { surface = bg.rgb; break; }
        }
        return {
          sel,
          on: ringed === el ? "self" : "label",
          width: parseFloat(cs.outlineWidth),
          ratio: ring ? +ratio(ring.rgb, surface).toFixed(2) : 0,
        };
      }, selector),
    );
  }

  for (const m of measured) {
    expect(m.width, `${m.sel} must draw a focus ring (got ${JSON.stringify(m)})`).toBeGreaterThanOrEqual(2);
    expect(m.ratio, `${m.sel} ring contrast (got ${JSON.stringify(m)})`).toBeGreaterThanOrEqual(3);
  }

  // AND A MOUSE CLICK DOES NOT RING, which is the whole reason the rule is
  // :focus-visible rather than :focus. Without this the spec would pass just as well
  // against a rule that rings everything all the time.
  //
  // MEASURED AS THE DRAWN OUTLINE, not as element.matches(":focus-visible"). The first
  // version asked the element whether it matched that pseudo-class, which queries the
  // BROWSER's heuristic and is completely independent of this app's stylesheet: swapping
  // the rule to plain :focus left that assertion green. Reading the computed outline is
  // the only form that can tell the two rules apart.
  await page.locator("#legend-toggle").click();
  const afterClick = await page.evaluate(() => {
    const cs = getComputedStyle(document.getElementById("legend-toggle"));
    return {
      style: cs.outlineStyle,
      width: parseFloat(cs.outlineWidth) || 0,
      // DRAWN, not declared. outline-width keeps reporting its declared value while
      // outline-style is "none", so a width check alone calls the correct code a
      // failure: the passing state here measures {style: "none", width: 3}.
      drawn: cs.outlineStyle !== "none" && (parseFloat(cs.outlineWidth) || 0) > 0,
      focused: document.activeElement === document.getElementById("legend-toggle"),
    };
  });
  expect(afterClick.focused, "the click must actually have left focus on the button").toBe(true);
  expect(
    afterClick.drawn,
    `a mouse click must not leave a ring behind (got ${JSON.stringify(afterClick)})`,
  ).toBe(false);
});

test("A6g. the skip link opens the panel it skips to", async ({ page }) => {
  // WHAT AXE CANNOT DECIDE, DECIDED HERE. The mobile scan reports skip-link as
  // undecidable ("Skip link target should become visible on activation") because at
  // scan time the panel is closed and a static rule cannot know that activating the
  // link opens one. That report is what put this under the light, and what it found was
  // real: the link was a bare anchor with no script, so it worked only where A1 happens
  // to dock the panel open.
  //
  // Measured at 375 before the fix: Tab reached the link, Enter did nothing observable,
  // and the next Tab landed on #stations-toggle. The first tab stop on a phone promised
  // to skip TO the stations list and delivered the button that opens it, which is where
  // the rider would have arrived with one more Tab and no link at all.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);

  await expect(page.locator("#stations-panel"), "the panel starts closed on a phone").toBeHidden();

  await page.keyboard.press("Tab");
  await expect(page.locator("#stations-skip"), "the skip link is still the first tab stop").toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.locator("#stations-panel")).toBeVisible();
  // FOCUS IS INSIDE THE PANEL, which is the promise the link's own words make. Landing
  // on the toggle, or on the body, would satisfy "the panel opened" and still leave the
  // rider where they started.
  const landed = await page.evaluate(() => {
    const el = document.activeElement;
    return {
      id: el.id || el.tagName,
      inPanel: document.getElementById("stations-panel").contains(el),
    };
  });
  expect(landed.inPanel, `focus must land inside the panel, got ${JSON.stringify(landed)}`).toBe(true);
});

test("A6h. at desktop the skip link keeps the native behaviour A1 shipped", async ({ page }) => {
  // The other half, and the reason the handler returns early when the panel is open. At
  // desktop widths A1 docks the panel, the anchor's own fragment navigation is correct,
  // and A3 must not replace it with a script that does the same thing differently.
  // Native skip-link behaviour leaves document.activeElement on the body and sets the
  // sequential navigation point, so it is the NEXT Tab that lands in the panel.
  await page.setViewportSize({ width: 1280, height: 720 });
  await installMocks(page);
  await open(page);

  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.locator("#stations-skip")).toBeFocused();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");

  const landed = await page.evaluate(() => {
    const el = document.activeElement;
    return { id: el.id || el.tagName, inPanel: document.getElementById("stations-panel").contains(el) };
  });
  expect(landed.inPanel, `one Tab after activating must be in the panel, got ${JSON.stringify(landed)}`).toBe(true);
});
