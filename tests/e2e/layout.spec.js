// A2: the interaction floor. Two things that are invisible until they bite.
//
// The alert banner used to cover Leaflet's zoom control: same corner, and the banner
// won the stacking contest by exactly one (1001 against 1000). So the map could not be
// zoomed at all while an agency-wide alert was showing, which is exactly when a rider
// wants to look closer at their own neighbourhood.
//
// And every interactive thing on the map was under the 24 CSS px WCAG 2.2 floor: the
// markers by design, the layer toggles at 13px checkboxes in 20px labels, the banner's
// dismiss at 13 by 16. The floor is met with transparent hit area, never by making
// anything bigger to look at, because a 24px subway square would be a blob at city zoom
// and there are several hundred of them.
//
// Same hermetic harness as the rest of the suite.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const HIT_FLOOR = 24;

// The narrowest viewport worth supporting. 320px is the classic small-phone width and
// the point at which a fixed-width banner and a fixed-position control are most likely
// to collide.
const NARROW = { width: 320, height: 640 };

async function open(page, ctx) {
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
      timeout: 15_000,
    })
    .toBeGreaterThan(5);
  return ctx;
}

// Serve one agency-wide alert, which is what makes the banner render at all.
async function withBanner(page) {
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "a-1",
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

const rect = (page, selector) =>
  page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
  }, selector);

// Do two boxes overlap at all? Touching edges do not count as overlap.
function overlaps(a, b) {
  return a.x < b.right && b.x < a.right && a.y < b.bottom && b.y < a.bottom;
}

for (const [label, viewport] of [
  ["desktop", { width: 1280, height: 720 }],
  ["320px", NARROW],
]) {
  test(`A4a. the alert banner leaves the zoom control clickable at ${label}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await withBanner(page);
    await open(page);
    await expect(page.locator(".alert-banner-strip")).toBeVisible();

    const banner = await rect(page, ".alert-banner-strip");
    const zoomIn = await rect(page, ".leaflet-control-zoom-in");
    const zoomOut = await rect(page, ".leaflet-control-zoom-out");

    // Disjoint boxes, asserted as geometry rather than as a z-index value: the bug was
    // never that the z-index was wrong, it was that two things wanted the same corner.
    expect(overlaps(banner, zoomIn), `banner ${JSON.stringify(banner)} vs zoom-in ${JSON.stringify(zoomIn)}`).toBe(
      false,
    );
    expect(overlaps(banner, zoomOut)).toBe(false);

    // And the control is genuinely reachable: what is painted at its centre is the
    // control itself, not something covering it. This is the assertion that would have
    // caught the original defect, which a bounding-box check alone can miss when a
    // transparent ancestor is in the way.
    const hit = await page.evaluate(() => {
      const r = document.querySelector(".leaflet-control-zoom-in").getBoundingClientRect();
      const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
      // Reported with the tag and the parent's classes as well, so a failure names
      // what is covering the button instead of only saying that something is.
      return el
        ? [el.tagName, el.className.toString(), el.parentElement ? el.parentElement.className.toString() : ""].join("|")
        : null;
    });
    expect(hit, "the zoom-in button must be what is on top of itself").toContain("leaflet-control-zoom-in");

    // Clicking it actually zooms, which is the thing the rider could not do.
    const before = await page.evaluate(() => map.getZoom());
    await page.locator(".leaflet-control-zoom-in").click();
    await page.clock.runFor(1000); // the zoom is animated and the clock is paused
    expect(await page.evaluate(() => map.getZoom()), "the map must actually zoom").toBeGreaterThan(before);
  });
}

test("A4b. every interactive thing on the map surface meets the 24px floor", async ({ page }) => {
  await withBanner(page);
  await open(page);

  // Markers are sampled by system rather than exhaustively: they share one rule, and
  // naming them individually says which system regressed.
  const markers = await page.evaluate((floor) => {
    const out = {};
    for (const cls of ["bus-marker", "train-marker", "railroad-marker", "path-marker", "ferry-marker", "airtrain-marker"]) {
      const el = document.querySelector(`.${cls}`);
      if (!el) continue;
      const style = getComputedStyle(el, "::before");
      out[cls] = {
        icon: [Math.round(el.getBoundingClientRect().width), Math.round(el.getBoundingClientRect().height)],
        hit: [parseFloat(style.width), parseFloat(style.height)],
      };
    }
    out.__floor = floor;
    return out;
  }, HIT_FLOOR);

  for (const [cls, sizes] of Object.entries(markers)) {
    if (cls.startsWith("__")) continue;
    expect(sizes.hit[0], `${cls} hit width`).toBeGreaterThanOrEqual(HIT_FLOOR);
    expect(sizes.hit[1], `${cls} hit height`).toBeGreaterThanOrEqual(HIT_FLOOR);
    // AND THE VISUAL SIZE IS UNCHANGED. The floor is met with transparent hit area, not
    // by inflating the drawing: if a marker's own box grew to 24px this assertion fails
    // and the fix went the wrong way.
    expect(
      Math.min(sizes.icon[0], sizes.icon[1]),
      `${cls} must not have been visually inflated to meet the floor`,
    ).toBeLessThan(HIT_FLOOR);
  }

  // The controls, which meet the floor by padding rather than by pseudo-element.
  for (const selector of [
    "#toggles label",
    "#alert-banner-dismiss",
    ".leaflet-control-zoom-in",
    ".leaflet-control-zoom-out",
    "#stations-toggle",
  ]) {
    const box = await rect(page, selector);
    expect(box, `${selector} must exist`).not.toBeNull();
    expect(box.w, `${selector} width`).toBeGreaterThanOrEqual(HIT_FLOOR);
    expect(box.h, `${selector} height`).toBeGreaterThanOrEqual(HIT_FLOOR);
  }
});

test("A4c. the enlarged hit areas did not hand the station dots back to the trains", async ({ page }) => {
  // THE REGRESSION THIS ITEM COULD EASILY HAVE CAUSED, and did in its first draft. The
  // subway and PATH markers are anchored above their point precisely so they stop
  // covering the station dot underneath. A hit halo centred on the icon undid that: the
  // subway's reached to one pixel above its anchor and PATH's landed exactly ON it.
  //
  // ASSERTED BY HIT TESTING, NOT BY ARITHMETIC. The first version of this spec measured
  // the marker's getBoundingClientRect, which does NOT include an absolutely positioned
  // pseudo-element, so it was measuring the icon and would have passed with the halo
  // rule deleted. Mutation testing caught that. What matters is what the browser says is
  // at that pixel, so that is what is asked.
  await installMocks(page);
  await open(page);

  for (const system of ["subway", "path"]) {
    const result = await page.evaluate((which) => {
      const record = which === "subway" ? [...trains.values()][0] : [...pathTrainRecords.values()][0];
      // Centre the map on the vehicle so the legend and the docked panel cannot sit
      // over the point being probed and make this pass for the wrong reason.
      map.setView(record.marker.getLatLng(), map.getZoom(), { animate: false });
      const container = document.getElementById("map").getBoundingClientRect();
      const point = map.latLngToContainerPoint(record.marker.getLatLng());
      const x = container.left + point.x;
      const y = container.top + point.y;
      // Probe the anchor AND the two pixels just above it. The anchor alone is not
      // enough: a halo centred on the icon stops within a pixel of the anchor without
      // quite touching it, so an anchor-only assertion passes in both configurations
      // and proves nothing. The clearance is what matters, so the clearance is what is
      // measured.
      const el = record.marker.getElement();
      const covered = [0, 1, 2].filter((dy) => document.elementsFromPoint(x, y - dy).includes(el));
      return {
        covered,
        stack: document.elementsFromPoint(x, y).map((node) => (node.className || node.tagName).toString()),
      };
    }, system);

    // The marker must not be painted at its own anchor point or immediately above it.
    // That band belongs to the station dot underneath.
    expect(
      result.covered,
      `${system}: hit area reaches its own anchor band, where the station is. Stack: ${JSON.stringify(result.stack)}`,
    ).toEqual([]);
  }
});

test("A4d. focus parked on the banner's dismiss survives the banner being rebuilt", async ({ page }) => {
  // RAISED BY THE REVIEW, DROPPED BY MY OWN REVIEW SCRIPT, then reproduced. The banner
  // is the page's other rebuilt-in-place surface: renderAlertBanner reassigns innerHTML,
  // which destroys the dismiss button along with everything else. A rider parked on it
  // was dropped to document.body, measured, with the button visibly back on screen.
  //
  // The trigger is not exotic. The MTA revises an ongoing incident IN PLACE under one
  // id, which is the whole reason the render key hashes the header text; that revision
  // is exactly what rebuilds the strip under the rider's fingers.
  const ctx = await installMocks(page);
  let header = "Reduced service systemwide while crews clear a disabled train";
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "a-revised",
        system: "subway",
        header,
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
  await open(page, ctx);

  await page.locator("#alert-banner-dismiss").focus();
  await expect(page.locator("#alert-banner-dismiss")).toBeFocused();

  // Same id, new wording: the alerts poll is 60s.
  header = "All subway service suspended while crews clear a disabled train";
  await page.clock.runFor(65_000);
  await expect(page.locator("#alert-banner")).toContainText("All subway service suspended");

  await expect(page.locator("#alert-banner-dismiss"), "a rebuilt control must keep focus").toBeFocused();

  // And it still works, which is the assertion that matters: focus on a right-looking
  // element is not the same as the rider being able to act.
  await page.keyboard.press("Enter");
  await expect(page.locator("#alert-banner-dismiss")).toHaveCount(0);
});

test("A4e. a banner rebuild does not GRAB focus from somewhere else", async ({ page }) => {
  // The other half, and the one that makes A4d a restore rather than a focus trap. The
  // popup helper needed exactly this guard and shipped without it once: restoring
  // unconditionally is how a background refresh starts yanking focus off whatever the
  // rider was actually using. Here the rider is in the Stations panel and the banner
  // must leave them alone.
  const ctx = await installMocks(page);
  let header = "Reduced service systemwide while crews clear a disabled train";
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    body.alerts = [
      {
        id: "a-revised",
        system: "subway",
        header,
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
  await open(page, ctx);

  await page.locator("#stations-search").focus();
  await expect(page.locator("#stations-search")).toBeFocused();
  header = "All subway service suspended while crews clear a disabled train";
  await page.clock.runFor(65_000);
  await expect(page.locator("#alert-banner")).toContainText("All subway service suspended");
  await expect(page.locator("#stations-search"), "the rider was not in the banner").toBeFocused();
});
