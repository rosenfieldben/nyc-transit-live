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
