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

  // And the toggle OPENS it with a real click, which hit-tests. The first version of
  // this used locator.press(), which dispatches a key event straight at the element and
  // skips the receives-events check entirely: it stayed green while the overlay made
  // that same toggle untappable, the exact blindness A6a exists in this file to remove.
  // Closing is then driven from the panel's own button, because at this width the
  // toggle is UNDERNEATH the overlay and pressing it is not something a rider can do.
  await page.locator("#stations-toggle").click({ timeout: 5_000 });
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-close").click({ timeout: 5_000 });
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

test("A6i. a phone rider can always get back out of the full-screen panel", async ({ page }) => {
  // THE TRAP, PINNED AS A POINTER PATH. Under 700px the panel is a full-viewport opaque
  // overlay, so it covers #stations-toggle, which was the only control that closed it.
  // Escape still worked and a phone has no Escape key: a rider who tapped Stations had
  // no exit but a page reload. Reproduced by the review at 375, where elementFromPoint
  // at the toggle's centre returned #stations-panel and a real click timed out.
  //
  // So this spec never presses a key. Every step is a click, because the rider this is
  // for has no keyboard at all.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);

  await page.locator("#stations-toggle").click({ timeout: 5_000 });
  await expect(page.locator("#stations-panel")).toBeVisible();

  // The toggle really is covered, so the close button is not redundant with it.
  const toggleCovered = await page.evaluate(() => {
    const el = document.getElementById("stations-toggle");
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
    return !!(top && top !== el && !el.contains(top));
  });
  expect(toggleCovered, "the overlay must actually cover the toggle, or this spec proves nothing").toBe(true);

  // And the way out is tappable, which is the whole finding.
  await page.locator("#stations-close").click({ timeout: 5_000 });
  await expect(page.locator("#stations-panel")).toBeHidden();
  await expect(page.locator("#stations-toggle"), "closing returns focus to the opener").toBeFocused();
});

test("A6j. rotating a docked desktop panel down to a phone leaves a way out", async ({ page }) => {
  // THE UNPROMPTED ARRIVAL. A tablet docked at 1280 with the panel open, narrowed to
  // 375, keeps the panel open and it becomes the full-screen overlay without the rider
  // touching anything. If the exit were painted only on open, this is the path that
  // would miss it.
  await page.setViewportSize({ width: 1280, height: 720 });
  await installMocks(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible();

  await page.setViewportSize(PHONE);
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-close").click({ timeout: 5_000 });
  await expect(page.locator("#stations-panel")).toBeHidden();
});

// A6k: the legend panel and the alert banner share the bottom of a phone screen, and
// this is the spec that keeps them out of each other's way.
//
// WHAT WENT WRONG AND WHAT IT COST. A3 moved the banner to the bottom so it would stop
// covering the Stations toggle. The legend panel grows DOWN from the top, and with the
// legend expanded at 375x667 it ran to y=657 while the banner sat at 595..645. The alert
// text itself was never hidden (the banner is z-index 1001 to the panel's 1000, and
// elementFromPoint returned the alert row at every sample across it), so this is not the
// occlusion defect it first looked like. What it cost was the other direction: the panel
// scrolls its own overflow, so its END is what lands behind the banner, and its end is
// the status line. On a phone during a systemwide incident the rider could not reach the
// line that says whether the data they are looking at is current.
//
// Both widths, both legend states, because the panel is only tall enough to reach the
// banner in one of them and a spec that only tried the collapsed state would pass
// without touching the defect.
for (const [label, viewport] of [
  ["320", NARROW],
  ["375", PHONE],
]) {
  test(`A6k. the legend panel stops above the alert banner at ${label}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await withBanner(page);
    await open(page);
    await expect(page.locator(".alert-banner-row").first()).toBeVisible();

    for (const state of ["collapsed", "expanded"]) {
      if (state === "expanded") {
        await page.locator("#legend-toggle").click();
        await expect(page.locator("#legend")).toBeVisible();
      }
      const boxes = await page.evaluate(() => {
        const rect = (sel) => {
          const r = document.querySelector(sel).getBoundingClientRect();
          return { top: Math.round(r.top), bottom: Math.round(r.bottom) };
        };
        return { panel: rect("#panel"), banner: rect("#alert-banner") };
      });
      expect(
        boxes.panel.bottom,
        `${state}: the panel must end above the banner (panel ${JSON.stringify(boxes.panel)}, banner ${JSON.stringify(boxes.banner)})`,
      ).toBeLessThanOrEqual(boxes.banner.top);
    }

    // And the consequence, asserted as the rider experiences it rather than as geometry:
    // scrolled to the end of the expanded panel, the status line is on screen and on top.
    await page.evaluate(() => {
      const panel = document.getElementById("panel");
      panel.scrollTop = panel.scrollHeight;
    });
    const statusReachable = await page.evaluate(() => {
      const el = document.getElementById("status");
      const r = el.getBoundingClientRect();
      const top = document.elementFromPoint(Math.round(r.left + r.width / 2), Math.round(r.top + r.height / 2));
      return { inView: r.bottom <= window.innerHeight && r.top >= 0, onTop: !!(top && (top === el || el.contains(top))) };
    });
    expect(statusReachable, "the status line must be reachable at the end of the panel's scroll").toEqual({
      inView: true,
      onTop: true,
    });
  });
}

test("A6l. the reserved strip follows the banner's height when the viewport changes", async ({ page }) => {
  // The height is published by systems/shared.js when the banner RENDERS, and
  // renderAlertBanner returns early on an unchanged key, so a viewport change alone would
  // never republish it. That matters because the same header wraps to one line on a
  // tablet and more than one on a phone: a rider who rotates would leave the panel sized
  // against a height that is no longer true. The listener is what keeps them apart, and
  // this is the spec that fails if it is removed.
  await page.setViewportSize({ width: 1280, height: 720 });
  await withBanner(page);
  await open(page);
  await expect(page.locator(".alert-banner-row").first()).toBeVisible();
  const wide = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--alert-banner-height").trim(),
  );

  await page.setViewportSize(NARROW);
  // The panel is docked open at 1280 and becomes the full-screen overlay on the way
  // down, so it is covering the legend disclosure until the rider dismisses it. Closing
  // it here is the rider's own first move, not a workaround: A6j is the spec for that
  // path, and this one is about what the layout does afterwards.
  await page.locator("#stations-close").click({ timeout: 5_000 });
  await expect(page.locator("#stations-panel")).toBeHidden();
  await page.locator("#legend-toggle").click();
  await expect(page.locator("#legend")).toBeVisible();
  const narrow = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--alert-banner-height").trim(),
  );
  // Not a fixed pair of numbers: what is being asserted is that the published height
  // TRACKS the layout, and pinning the exact pixels would fail on a font metric change
  // that broke nothing. The banner is genuinely taller at 320 than at 1280 with this
  // header, which is what makes the comparison meaningful rather than tautological.
  expect(
    parseFloat(narrow),
    `the banner is taller on a phone, so the reservation must grow (1280: ${wide}, 320: ${narrow})`,
  ).toBeGreaterThan(parseFloat(wide));

  const boxes = await page.evaluate(() => {
    const rect = (sel) => {
      const r = document.querySelector(sel).getBoundingClientRect();
      return { top: Math.round(r.top), bottom: Math.round(r.bottom) };
    };
    return { panel: rect("#panel"), banner: rect("#alert-banner") };
  });
  expect(
    boxes.panel.bottom,
    `after rotating down the panel must still end above the banner (panel ${JSON.stringify(boxes.panel)}, banner ${JSON.stringify(boxes.banner)})`,
  ).toBeLessThanOrEqual(boxes.banner.top);
});

test("A6m. dismissing the banner gives the reserved strip back", async ({ page }) => {
  // ROUND 2 FOUND THIS AS A COVERAGE HOLE, and it is the honest kind: the fix had three
  // publish sites and only two of them were pinned. Deleting the publish on the
  // empty-banner branch left the whole 121-spec suite green, so half of the height fix
  // was unmutated. That branch is not an edge case: it is the dismiss button, and it is
  // also every standing incident that simply ends on a later poll.
  //
  // Measured with that line deleted, at 375x667 with the legend expanded: after
  // dismissing, --alert-banner-height stays at 49.59px instead of dropping to 0, the
  // panel keeps max-height 577px instead of 627px, and 50px of phone screen stays
  // reserved for a banner that is gone, for the rest of the session. Nothing heals it
  // until the next banner render or a resize.
  await page.setViewportSize(PHONE);
  await withBanner(page);
  await open(page);
  await expect(page.locator(".alert-banner-row").first()).toBeVisible();
  await page.locator("#legend-toggle").click();
  await expect(page.locator("#legend")).toBeVisible();

  const reserved = async () =>
    page.evaluate(() => ({
      published: getComputedStyle(document.documentElement).getPropertyValue("--alert-banner-height").trim(),
      panelBottom: Math.round(document.getElementById("panel").getBoundingClientRect().bottom),
    }));

  const showing = await reserved();
  expect(parseFloat(showing.published), "a banner is showing, so it reserves height").toBeGreaterThan(0);

  await page.locator("#alert-banner-dismiss").click();
  await expect(page.locator(".alert-banner-row")).toHaveCount(0);

  const dismissed = await reserved();
  expect(parseFloat(dismissed.published), "a dismissed banner reserves nothing").toBe(0);
  // And the panel actually grows into the strip, which is the rider-facing half: the
  // property alone could be right while nothing consumed it.
  expect(
    dismissed.panelBottom,
    `the panel must take the strip back (showing ${showing.panelBottom}, dismissed ${dismissed.panelBottom})`,
  ).toBeGreaterThan(showing.panelBottom);
});

test("A6n. the keyboard exit from the full-screen panel is where the comment says it is", async ({ page }) => {
  // ROUND 2 CAUGHT stations.js CLAIMING the close button was "the last stop inside the
  // panel". It is the first. That sentence was the stated mitigation for shipping an
  // opaque overlay with no focus trap, so a maintainer reading it would have believed
  // forward-tabbing ended at a visible exit, when forward-tabbing in fact leaves the
  // overlay entirely.
  //
  // The comment is now corrected, and this spec is why it stays corrected: the ordering
  // is asserted rather than described. It pins the exit that exists (Shift+Tab from the
  // input the panel opens on, plus Escape) AND the wart that also exists (forward-tab
  // leaves), because a spec that only pinned the good half would be the same kind of
  // half-true the comment was.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  const focusableIds = () =>
    page.evaluate(() => {
      const panel = document.getElementById("stations-panel");
      const focusable = "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])";
      return [...panel.querySelectorAll(focusable)]
        .filter((el) => !el.disabled && el.offsetParent !== null)
        .map((el) => el.id || el.tagName + "." + (el.className || "").split(" ")[0]);
    });
  expect(await focusableIds(), "the close button is the FIRST focusable in the panel, not the last").toEqual([
    "stations-close",
    "stations-search",
  ]);

  // Opening lands on the search input, which is the A1 contract.
  await expect(page.locator("#stations-search")).toBeFocused();

  // The exit is one Shift+Tab away, and it works.
  await page.keyboard.press("Shift+Tab");
  await expect(page.locator("#stations-close"), "one Shift+Tab reaches the exit").toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#stations-panel")).toBeHidden();
  await expect(page.locator("#stations-toggle"), "and closing returns focus to the opener").toBeFocused();

  // The wart, recorded rather than papered over: tabbing FORWARD leaves the overlay onto
  // controls the rider cannot see. If a later phase adds a focus trap this fails, which
  // is the right way to find out that this comment needs rewriting again.
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-search")).toBeFocused();

  // A4: FORWARD-TAB NO LONGER REACHES A COVERED CONTROL, which is the half of this spec
  // that flipped. A3 pinned the wart here: one Tab from the search input landed on
  // #stations-toggle, outside the panel and underneath the overlay. A4 makes everything
  // outside the panel `inert`, so those stops stop being stops.
  //
  // WHAT IT DOES NOT DO, MEASURED RATHER THAN ASSUMED: it does not cycle. Tab from the
  // panel's last control lands on BODY, because inert removes the background from the tab
  // order without wrapping the order around the panel. Wrapping is what a focus TRAP does,
  // and this phase forbids traps by name: a trap with no reliable exit is how A3's
  // untappable overlay was created. So the property asserted here is the one inert
  // actually provides and the one the rider actually needs, which is that no control they
  // cannot see is reachable. Leaving to the body and tabbing back in through the browser
  // is the normal, escapable behaviour of a page with an inert background.
  const sweep = [];
  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    sweep.push(
      await page.evaluate(() => {
        const el = document.activeElement;
        return {
          id: el.id || el.tagName,
          inPanel: document.getElementById("stations-panel").contains(el),
        };
      }),
    );
  }
  const escaped = sweep.filter((stop) => !stop.inPanel && stop.id !== "BODY");
  expect(escaped, `no covered control may be reachable by Tab (sweep: ${JSON.stringify(sweep)})`).toEqual([]);

  // The background is genuinely inert, not merely visually covered: every body child
  // except the panel and the live region carries the attribute, and the control A3
  // measured as reachable-but-invisible is now unfocusable AND out of the a11y tree.
  const background = await page.evaluate(() => {
    // ASKED AS A PROPERTY, NOT AS A LIST OF BODY CHILDREN. The first version of this
    // enumerated document.body.children, which was only ever right while the panel
    // happened to be a body child; A4 then wrapped the map and the panel in <main> and the
    // list changed without any accessibility behaviour changing. What matters is which
    // SUBTREES a rider can reach, so that is what is asked.
    const out = { inert: [], notInert: [], covered: {} };
    const panel = document.getElementById("stations-panel");
    for (const id of ["stations-panel", "page-announce", "app-title", "map", "alert-banner", "panel"]) {
      const el = document.getElementById(id);
      if (!el) continue;
      (el.closest("[inert]") ? out.inert : out.notInert).push(id);
    }
    out.panelReachable = panel.closest("[inert]") === null;
    // The controls A3 measured as reachable-but-invisible are not body children (they
    // live inside #panel, the legend), so membership in the list above cannot speak for
    // them. Their inertness is INHERITED, and the only honest way to ask about inherited
    // inertness is to try to use them: focus() on an element inside an inert subtree is
    // specified as a no-op. That is also the property the rider actually has.
    for (const id of ["stations-toggle", "legend-toggle", "toggle-buses"]) {
      const el = document.getElementById(id);
      el.focus();
      // ownInert is recorded to make the inheritance VISIBLE rather than implied: it is
      // false on every one of these, because the attribute sits on #panel and the
      // property reflects only the element's own attribute. A spec that asserted el.inert
      // here would fail while the rider was perfectly protected, which is the same
      // declared-value-versus-effective-value trap A3 hit twice with outline-width.
      out.covered[id] = { focusable: document.activeElement === el, ownInert: el.inert };
    }
    return out;
  });
  expect(background.notInert.sort(), "only the overlay and the three exempt surfaces stay reachable").toEqual([
    "app-title",
    "page-announce",
    "stations-panel",
  ]);
  expect(background.inert.sort(), "the map and the page chrome are all inert").toEqual([
    "alert-banner",
    "map",
    "panel",
  ]);
  expect(background.covered, "the covered controls inherit inertness and cannot take focus").toEqual({
    "stations-toggle": { focusable: false, ownInert: false },
    "legend-toggle": { focusable: false, ownInert: false },
    "toggle-buses": { focusable: false, ownInert: false },
  });

  // AND THE STATE A RIDER IS ACTUALLY IN. Round 3 of A3 caught the empty-query half being
  // asserted as if it were the whole story: with result rows rendered, forward-tab walks
  // the rows. That is the common state, because the query survives closing and the panel
  // reopens with its rows already drawn.
  await page.locator("#stations-search").fill("times");
  await expect(page.locator("#stations-results button.station-row").first()).toBeVisible();
  const rows = await page.locator("#stations-results button.station-row").count();
  expect(await focusableIds(), "result rows join the panel's tab order, after the search input").toEqual([
    "stations-close",
    "stations-search",
    ...Array(rows).fill("BUTTON.station-row"),
  ]);

  await page.locator("#stations-search").focus();
  await page.keyboard.press("Tab");
  expect(
    await page.evaluate(() => ({
      isRow: document.activeElement.classList.contains("station-row"),
      inPanel: document.getElementById("stations-panel").contains(document.activeElement),
    })),
    "with rows showing, the next stop is a result row and it is still inside the panel",
  ).toEqual({ isRow: true, inPanel: true });

  // Past the last row, the same property in the state that used to be the exception to it.
  const rowSweep = [];
  for (let i = 0; i < rows + 3; i++) {
    await page.keyboard.press("Tab");
    rowSweep.push(
      await page.evaluate(() => {
        const el = document.activeElement;
        return {
          id: el.id || el.tagName,
          inPanel: document.getElementById("stations-panel").contains(el),
        };
      }),
    );
  }
  const rowEscaped = rowSweep.filter((stop) => !stop.inPanel && stop.id !== "BODY");
  expect(
    rowEscaped,
    `with rows rendered, still no covered control is reachable (sweep: ${JSON.stringify(rowSweep)})`,
  ).toEqual([]);
});

test("A6o. closing un-inerts the background BEFORE it restores focus", async ({ page }) => {
  // THE SHARP EDGE OF DELIVERABLE 1, and the reason the release is unconditional and
  // first. #stations-toggle is outside the panel, so while the overlay is up it is inert,
  // and .focus() on an element inside an inert subtree is specified as a no-op: the call
  // succeeds, returns nothing, throws nothing, and the rider stays where they were. Close
  // the panel in the wrong order and the A1 focus-return contract silently becomes "focus
  // falls to the body", with every existing spec still green because they assert the
  // panel closed rather than where focus went.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  // The precondition, asserted so this cannot pass on a tree where nothing was inert.
  expect(
    await page.evaluate(() => document.getElementById("panel").inert),
    "the background must actually be inert before the close, or this proves nothing",
  ).toBe(true);

  // Close through the panel's own control, which is the mobile rider's route out.
  await page.locator("#stations-close").click();
  await expect(page.locator("#stations-panel")).toBeHidden();
  expect(
    await page.evaluate(() => ({
      inert: document.getElementById("panel").inert,
      active: document.activeElement.id,
    })),
    "the background is released and focus lands on the opener, not the body",
  ).toEqual({ inert: false, active: "stations-toggle" });

  // Escape is the other closing path and takes the same door, so it gets the same claim.
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-search").focus();
  await page.keyboard.press("Escape");
  await expect(page.locator("#stations-panel")).toBeHidden();
  expect(
    await page.evaluate(() => ({
      inert: document.getElementById("panel").inert,
      active: document.activeElement.id,
    })),
    "Escape releases the background and returns focus too",
  ).toEqual({ inert: false, active: "stations-toggle" });
});

test("A6q. the page live region stays out of the inert set, so it can still speak", async ({ page }) => {
  // THE COLLISION DELIVERABLE 1 CREATES WITH DELIVERABLE 2, pinned because nothing else
  // would notice it. `inert` removes a subtree from the ACCESSIBILITY TREE, not just from
  // the tab order, so an inert live region is a silent one. #page-announce is a body child
  // and would go inert with everything else, and the overlay is exactly the state in which
  // a rider is most likely to be reading the panel when a marker they were following
  // disappears behind it. Without this exemption A4's vanishing-focus announcements would
  // be mute in A4's own new state, with every other spec still green.
  await page.setViewportSize(PHONE);
  await installMocks(page);
  await open(page);
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  // The precondition: the background really is inert, so the exemption is doing work.
  expect(
    await page.evaluate(() => document.getElementById("panel").inert),
    "the background must be inert, or the exemption below proves nothing",
  ).toBe(true);

  // Asked of the ACCESSIBILITY TREE rather than of the attribute, because "not inert" and
  // "actually announceable" are different claims and only the second one matters. An
  // element inside an inert subtree is excluded from the a11y tree entirely.
  const speakable = await page.evaluate(() => {
    const region = document.getElementById("page-announce");
    region.textContent = "The train you were following left the feed";
    return {
      ownInert: region.inert,
      inInertSubtree: region.closest("[inert]") !== null,
      live: region.getAttribute("aria-live"),
      text: region.textContent,
    };
  });
  expect(speakable, "the page live region must remain announceable under the overlay").toEqual({
    ownInert: false,
    inInertSubtree: false,
    live: "polite",
    text: "The train you were following left the feed",
  });

  // The panel's OWN region is inside the panel, so it was never at risk; asserted so a
  // future refactor that moves either region out of its container fails here.
  expect(
    await page.evaluate(() => document.getElementById("stations-announce").closest("[inert]") !== null),
    "the panel's own live region is inside the panel and equally unaffected",
  ).toBe(false);
});

test("A6p. inertness follows the 700px boundary in both directions", async ({ page }) => {
  // The overlay is a function of width AND openness, so the state has to be recomputed on
  // a crossing that the rider never touched: a docked desktop panel narrowed to a phone
  // becomes an overlay by itself (the A6j path), and widening it back must give the page
  // behind it back. A one-way test cannot see a listener that fails to un-apply.
  await page.setViewportSize({ width: 1280, height: 720 });
  await installMocks(page);
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible();

  const state = () =>
    page.evaluate(() => ({
      panelOpen: !document.getElementById("stations-panel").hidden,
      backgroundInert: document.getElementById("panel").inert,
      mapInert: document.getElementById("map").inert,
    }));

  // POLLED, NOT READ ONCE, and this is a fact about the mechanism rather than a hedge.
  // Inertness is re-applied from a matchMedia CHANGE listener, the same door the legend
  // disclosure and the dock use, and that event is dispatched on a later task than the
  // one setViewportSize resolves on. Reading immediately passed in isolation and failed
  // under load, which is the signature of asserting a synchronous answer to an
  // asynchronous contract. The contract is "on the breakpoint change", so the spec waits
  // for the change; a listener that never fires still fails, on the timeout.
  const settles = async (expected, message) => {
    await expect.poll(async () => state(), { timeout: 5_000, message }).toEqual(expected);
  };

  // Docked is not an overlay: the panel sits beside the map and nothing is covered.
  await settles(
    { panelOpen: true, backgroundInert: false, mapInert: false },
    "docked at 1280, nothing is inert",
  );

  await page.setViewportSize(PHONE);
  await settles(
    { panelOpen: true, backgroundInert: true, mapInert: true },
    "narrowed to a phone, the same open panel is now an overlay",
  );

  await page.setViewportSize({ width: 1280, height: 720 });
  await settles(
    { panelOpen: true, backgroundInert: false, mapInert: false },
    "widened back, the page behind it is returned",
  );

  // And the exact breakpoint, both sides, because 700 is the last mobile width.
  await page.setViewportSize(BOUNDARY_MOBILE);
  await settles(
    { panelOpen: true, backgroundInert: true, mapInert: true },
    "700 is still an overlay width",
  );
  await page.setViewportSize(BOUNDARY_ROOMY);
  await settles(
    { panelOpen: true, backgroundInert: false, mapInert: false },
    "701 is not",
  );
});
