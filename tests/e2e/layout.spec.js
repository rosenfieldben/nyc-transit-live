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
const { expectState } = require("./state");

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

// A3 EXTENDS THE SAMPLING TO MOBILE RATHER THAN DUPLICATING IT. A separate mobile copy
// of this spec would drift from this one the first time a control is added, and the
// drifting copy is always the one nobody notices. The list of controls is shared; only
// the viewport changes, and the mobile widths bring two more controls into existence
// (the legend disclosure) and one out of a different layout (the panel rows).
for (const [label, viewport] of [
  ["desktop", { width: 1280, height: 720 }],
  ["375", { width: 375, height: 667 }],
  ["320", { width: 320, height: 640 }],
]) {
test(`A4b. every interactive thing on the map surface meets the 24px floor at ${label}`, async ({ page }) => {
  await page.setViewportSize(viewport);
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
  // A3 added the route-line clear button and the legend disclosure; both were measured
  // sub-floor in the inventory (#route-clear at 59x18) or are new in this phase.
  const controls = [
    "#toggles label",
    "#alert-banner-dismiss",
    ".leaflet-control-zoom-in",
    ".leaflet-control-zoom-out",
    "#stations-toggle",
  ];
  if (viewport.width <= 700) controls.push("#legend-toggle");
  for (const selector of controls) {
    const box = await rect(page, selector);
    expect(box, `${selector} must exist at ${label}`).not.toBeNull();
    expect(box.w, `${selector} width at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);
    expect(box.h, `${selector} height at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);
  }

  // CONTROLS THAT ONLY EXIST IN A STATE, measured in that state. A control nobody can
  // reach yet is not exempt from the floor; it is just harder to sample, and "harder to
  // sample" is how #route-clear stayed at 59x18 through A2's floor work.
  await page.evaluate(() => {
    const [id] = [...buses.keys()];
    buses.get(id).marker.fire("click");
  });
  await expect(page.locator("#route-clear")).toBeVisible();
  const clear = await rect(page, "#route-clear");
  expect(clear.w, `#route-clear width at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);
  expect(clear.h, `#route-clear height at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);

  // The skip link is offscreen until focused, so it is measured focused, which is the
  // only state in which a rider can operate it.
  await page.locator("#stations-skip").focus();
  await expect(page.locator("#stations-skip")).toBeFocused();
  const skip = await rect(page, "#stations-skip");
  expect(skip.w, `#stations-skip width at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);
  expect(skip.h, `#stations-skip height at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);

  // The panel's own rows, which are the mobile reading surface. Opened only if it is not
  // already open: at desktop widths A1 docks it open at load, and clicking the toggle
  // there CLOSES it, which is how the first version of this spec timed out on a search
  // box that was no longer on screen.
  if (!(await page.locator("#stations-panel").isVisible())) {
    await page.locator("#stations-toggle").click();
  }
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-search").fill("times");
  await expect(page.locator("#stations-results button.station-row").first()).toBeVisible();
  const row = await rect(page, "#stations-results button.station-row");
  expect(row.h, `station row height at ${label}`).toBeGreaterThanOrEqual(HIT_FLOOR);

  // NAMED, NOT SILENTLY EXEMPT: Leaflet's attribution link measures 51x14 and stays
  // that way. WCAG 2.2's Target Size (Minimum) has an explicit exception for a target
  // "in a sentence or block of text", which is exactly what this is, and the alternative
  // is forking Leaflet's stylesheet to inflate a licence credit. Asserted as a known
  // shape so that if it ever grows into a real control this stops being true quietly.
  const attribution = await rect(page, ".leaflet-control-attribution a");
  expect(attribution, "the attribution link must still exist").not.toBeNull();
  expect(
    attribution.h < HIT_FLOOR,
    "the inline attribution exception is still the case being made, not a regression",
  ).toBe(true);
});
}

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

for (const [label, viewport] of [
  ["desktop", { width: 1280, height: 720 }],
  ["375", { width: 375, height: 667 }],
]) {
  test(`A4f. the legend panel stays inside the viewport at ${label}`, async ({ page }) => {
    // MEASURED, NOT SUSPECTED. The legend panel is content-sized and had no ceiling, so
    // with every system's legend row and seven layer toggles it measured 771px of
    // content and ran to y=781: 61px off the bottom of a 720px desktop viewport and
    // 114px off a 375x667 phone. The status line was among what fell off, and because
    // nothing scrolled there was no way to reach it. That is the honesty surface for
    // stale feeds disappearing because the legend above it got long.
    //
    // Asserted as geometry rather than as a max-height value, so it keeps meaning if the
    // mechanism changes, and it fails the moment a new system's legend row pushes the
    // panel past the edge again.
    await page.setViewportSize(viewport);
    const ctx = await installMocks(page);
    await open(page, ctx);

    const fit = await page.evaluate(() => {
      const panel = document.getElementById("panel");
      const r = panel.getBoundingClientRect();
      return {
        bottom: Math.round(r.bottom),
        viewportHeight: document.documentElement.clientHeight,
        scrollable: panel.scrollHeight > panel.clientHeight,
        // Everything inside must be REACHABLE, which for a scroll container means the
        // scroll actually goes far enough to bring the last row into view.
        statusReachable: (() => {
          const s = document.getElementById("status");
          const p = panel.getBoundingClientRect();
          const before = panel.scrollTop;
          panel.scrollTop = panel.scrollHeight;
          const sr = s.getBoundingClientRect();
          const ok = sr.bottom <= p.bottom + 1 && sr.top >= p.top - 1;
          panel.scrollTop = before;
          return ok;
        })(),
      };
    });

    expect(fit.bottom, "the panel must not hang off the bottom of the screen").toBeLessThanOrEqual(
      fit.viewportHeight,
    );
    // And nothing was hidden to achieve that: whatever does not fit is scrolled to, not
    // cut away. A panel that fits because its contents were clipped would satisfy the
    // assertion above and lose the rider the status line just the same.
    expect(fit.statusReachable, "the status line must be reachable by scrolling the panel").toBe(true);
  });
}

test("A4g. every rendered route colour meets AA where it carries or is text", async ({ page }) => {
  // THE WIRING CHECK. The node tests prove readableTextOn and readableInk are correct
  // over every palette; they cannot prove the twelve call sites actually call them. A
  // site left on its old hardcoded "#fff" would keep every node test green and still
  // ship unreadable text, so this measures what the browser painted.
  //
  // Compositing is done against the element's nearest OPAQUE ancestor background rather
  // than down the whole chain, because A3's inventory work showed the naive walk gives a
  // false answer for absolutely-positioned overlays.
  //
  // THE ORDER HERE IS THE WHOLE TEST. The first draft sampled the DOM before opening any
  // popup, found nothing, and passed in two seconds having measured zero elements: the
  // vacuity trap this suite keeps re-learning. Everything is opened first, and the sample
  // size is asserted before the ratios are.
  // A DISCRIMINATING ROUTE IS SERVED ON PURPOSE. The stock fixture carries only the 1
  // and the A, whose colours are readable with white ink and readable as headings, so a
  // sample built from them passes whether or not the fix is wired: the first version of
  // this spec measured real elements, asserted a non-empty sample, and still survived
  // both mutations. The N is the sharp case in the palette at #e6b800, which needs DARK
  // ink on a chip and measures 1.87 as heading text on white.
  const ctx = await installMocks(page);
  ctx.overrides.subways = (route, fixtures) => {
    const body = fixtures.subways();
    const sample = body.data[0];
    body.data = [...body.data, { ...sample, trip_id: `${sample.trip_id}-N`, route_id: "N" }];
    return json(route, body);
  };
  // The CHIP half needs the N at a station, not just on a train: chips are built from a
  // stop's routes list. Times Sq serves 1/2/3 in the fixture, all of which are readable
  // with white ink either way, so without this the fill assertions never see a light
  // fill and survive a helper that always returns white.
  ctx.overrides.subwayStops = (route, fixtures) =>
    json(
      route,
      fixtures.subwayStops().map((stop) => (stop.id === "127" ? { ...stop, routes: [...stop.routes, "N"] } : stop)),
    );
  await open(page, ctx);

  // A station popup as well as the vehicle popups, because the chips and the arrival
  // badges live there and they are half the sites under test.
  await page.evaluate(() => {
    for (const [, record] of trains) {
      if (record.latest && record.latest.route_id === "N") { record.marker.openPopup(); return; }
    }
    const entry = stationRegistry.find((row) => row.key.startsWith("subway|"));
    if (entry && entry.marker) entry.marker.openPopup();
  });
  await expect.poll(async () => page.locator(".leaflet-popup-content").count()).toBeGreaterThan(0);
  await page.locator("#stations-search").fill("times");
  await expect(page.locator("#stations-results button.station-row").first()).toBeVisible();

  const measured = await page.evaluate(() => {
    const srgb = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
    const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((m, n) => n - m); return (x + 0.05) / (y + 0.05); };
    const parse = (s) => {
      const m = String(s).match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
      return { rgb: p.slice(0, 3), a: p.length > 3 ? p[3] : 1 };
    };
    const surfaceOf = (el) => {
      for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
        const bg = parse(getComputedStyle(n).backgroundColor);
        if (bg && bg.a === 1) return bg.rgb;
      }
      return [255, 255, 255];
    };
    const out = [];
    const sample = (el, what) => {
      const cs = getComputedStyle(el);
      const fg = parse(cs.color);
      const r = el.getBoundingClientRect();
      if (!fg || r.width === 0 || r.height === 0) return;
      const px = parseFloat(cs.fontSize);
      const bold = parseInt(cs.fontWeight, 10) >= 700;
      const need = px >= 24 || (bold && px >= 18.66) ? 3 : 4.5;
      out.push({
        what,
        text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 24),
        color: cs.color,
        ratio: +ratio(fg.rgb, surfaceOf(el)).toFixed(2),
        need,
      });
    };
    for (const el of document.querySelectorAll(".arr-badge, .station-chip")) sample(el, "fill");
    for (const el of document.querySelectorAll(".leaflet-popup-content b, .arr-dir")) sample(el, "ink");
    return out;
  });

  // NON-VACUITY FIRST, and both KINDS, so a run that rendered only headings cannot
  // certify the chips.
  expect(measured.length, "nothing was measured, so nothing was proved").toBeGreaterThan(0);
  expect(measured.some((m) => m.what === "ink"), "no route-coloured heading was rendered").toBe(true);
  expect(measured.some((m) => m.what === "fill"), "no chip or badge was rendered").toBe(true);
  // AND THE SHARP CASE IS IN IT. Without this the spec can be non-empty, well-formed and
  // still blind, which is exactly what it was before: a sample of readable colours proves
  // only that readable colours are readable.
  expect(
    measured.some((m) => m.what === "ink" && /\bN\b/.test(m.text)),
    `an N heading must be in the sample, got ${JSON.stringify(measured.filter((m) => m.what === "ink").map((m) => m.text))}`,
  ).toBe(true);
  expect(
    measured.some((m) => m.what === "fill" && m.text === "N"),
    `an N chip must be in the sample, got ${JSON.stringify(measured.filter((m) => m.what === "fill").map((m) => m.text))}`,
  ).toBe(true);

  const failures = measured.filter((m) => m.ratio < m.need);
  expect(failures, `route colour text below AA as rendered: ${JSON.stringify(failures)}`).toEqual([]);
});

for (const [label, viewport, docked] of [
  ["1280 docked", { width: 1280, height: 720 }, true],
  ["375 overlay", { width: 375, height: 667 }, false],
]) {
  test(`A4h. the document never scrolls sideways at ${label}`, async ({ page }) => {
    // THE A1 DEFECT THIS PHASE INHERITED BY NAME. Docked, the map was displaced by the
    // panel rather than sized to the space beside it, so the document was 360px wider
    // than the window: scrollWidth 1640 against clientWidth 1280, with the attribution
    // control pushed to x1406..1640 where it could be neither read nor clicked.
    //
    // Asserted at BOTH widths on purpose. The overflow only ever appeared docked, so a
    // mobile-only assertion would have passed throughout the defect's life, and a
    // desktop-only one would not notice the mobile layout reintroducing it later.
    await page.setViewportSize(viewport);
    const ctx = await installMocks(page);
    await open(page, ctx);

    // The panel must actually be in the state the label claims, or this measures the
    // wrong layout and passes for the wrong reason.
    const isDocked = await page.evaluate(() => document.body.classList.contains("stations-docked"));
    expect(isDocked, `the panel must be ${docked ? "docked" : "overlaid"} at this width`).toBe(docked);

    const geometry = await page.evaluate(() => {
      const de = document.documentElement;
      const map = document.getElementById("map").getBoundingClientRect();
      const attribution = document.querySelector(".leaflet-control-attribution");
      const a = attribution ? attribution.getBoundingClientRect() : null;
      return {
        scrollWidth: de.scrollWidth,
        clientWidth: de.clientWidth,
        mapRight: Math.round(map.right),
        mapLeft: Math.round(map.left),
        attribution: a ? [Math.round(a.left), Math.round(a.right)] : null,
        attributionInside: a ? a.right <= de.clientWidth + 0.5 && a.left >= -0.5 : false,
      };
    });

    expect(geometry.scrollWidth, "the document must not be wider than the window").toBe(
      geometry.clientWidth,
    );
    // The map ENDS at the window edge, which is what "sized to the space beside the
    // panel" means. Without this, a map that had simply been made narrower and left in
    // the corner would satisfy the scrollWidth check while wasting the screen.
    expect(geometry.mapRight, "the map must reach the right edge of the window").toBe(
      geometry.clientWidth,
    );
    expect(
      geometry.attributionInside,
      `the attribution must be on screen, measured at ${JSON.stringify(geometry.attribution)}`,
    ).toBe(true);
  });
}

test("A4i. closing the docked panel gives the map its column back", async ({ page }) => {
  // body.stations-docked reserves a 360px column: #map is offset and narrowed by it,
  // and the alert banner is indented clear of it. Both were keyed on the media query
  // alone, so the reservation outlived the thing it was reserved for. Measured at 1280
  // with the panel closed, before the fix: #map still reported {left: 360, width: 920}
  // and elementFromPoint at (180, 400) returned BODY. A rider who closes the station
  // list is asking for more map, and got a 360px strip of nothing instead.
  //
  // The banner is served here because it is the second rule keyed on that class, and a
  // spec that only checked the map would let the indent rot on its own.
  await page.setViewportSize({ width: 1280, height: 720 });
  const ctx = await withBanner(page);
  await open(page, ctx);
  await expect(page.locator(".alert-banner-row").first()).toBeVisible();

  const probe = () =>
    page.evaluate(() => {
      const de = document.documentElement;
      const m = document.getElementById("map").getBoundingClientRect();
      const at = document.elementFromPoint(180, 400);
      return {
        reserved: document.body.classList.contains("stations-docked"),
        mapLeft: Math.round(m.left),
        mapWidth: Math.round(m.width),
        overflow: de.scrollWidth - de.clientWidth,
        strip: !!(at && document.getElementById("map").contains(at)),
        bannerLeft: getComputedStyle(document.getElementById("alert-banner")).left,
      };
    });

  // Docked and open: the column is reserved, and the point tested below is the panel.
  const open1 = await probe();
  expect(open1.reserved, "the panel is docked open at 1280").toBe(true);
  expect(open1.mapLeft, "the map starts beside the panel").toBe(360);
  expect(open1.strip, "the panel occupies the column while it is open").toBe(false);

  // Closed: the column comes back, in both rules, with no sideways scroll either way.
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeHidden();
  const closed = await probe();
  expect(closed, "closing the docked panel must return the whole width to the map").toEqual({
    reserved: false,
    mapLeft: 0,
    mapWidth: 1280,
    overflow: 0,
    strip: true,
    bannerLeft: "54px",
  });

  // And reopening reserves it again, so this is a property of the panel's state rather
  // than a one-way release that happens to look right once.
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();
  const open2 = await probe();
  expect(open2.reserved, "reopening reserves the column again").toBe(true);
  expect(open2.mapLeft, "and the map steps aside again").toBe(360);
  expect(open2.overflow, "with no sideways scroll in either state").toBe(0);
});

/* ---------------- A4j / A4k: the popup correction, and when it stands down ----------------

   REBUILT IN ROUND 3, BECAUSE THE FIRST VERSION WAS FLAKY AND VACUOUS AT THE SAME TIME.
   Deleting its subject (the `if (riderOwnsTheView) return` line the whole spec is about)
   left it passing five to seven runs in ten. Both halves of that have one cause: it waited
   for the correction with expect.poll on a condition that was ALREADY TRUE before the
   resize, so what it actually measured was whether the ResizeObserver happened to fire
   inside the poll window.

   So the wait is now deterministic rather than timed. A probe ResizeObserver is attached to
   the same popup element AFTER the app's, and observers are delivered in creation order, so
   when the probe fires the app's has already run and any pan it wanted is already on the
   map. No polling, no timeout, no window to lose.

   AND IT IS A PAIR, which is the other half of what was missing. A4j says the correction
   does NOT run after the rider takes over; on its own that is satisfied by a correction
   that never runs at all. A4k is the same sequence with the drag removed, and requires the
   map to move. Together they pin a decision rather than an absence. */

const RIDER_MOVES_THE_MAP = { width: 375, height: 667 };

async function popupOnTheMapAt375(page) {
  await page.setViewportSize(RIDER_MOVES_THE_MAP);
  await installMocks(page);
  /* PAUSED, NOT MERELY FIXED, AND THAT IS THE FLAKE. setFixedTime pins Date and leaves the
     timers running, so the fifteen-second refresh still lands, and a refresh that rebuilds
     the railroad markers closes and reopens the popup, which resets the very flag these two
     specs are about. Measured: one or two runs in ten failed on the correction firing after
     a legitimate takeover, and the takeover had been legitimately forgotten in between.
     With the clock paused nothing polls, and the size change these specs care about is made
     directly rather than waited for. */
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await expect
    .poll(async () => page.evaluate(() => (typeof railroads === "undefined" ? 0 : railroads.size)), { timeout: 15_000 })
    .toBeGreaterThan(0);
  // A PLACED railroad train, the same subject the gate's cross-link state uses: it sits on
  // its station rather than at a GPS fix, which is what makes its popup reproducible.
  await page.evaluate(() => {
    const placed = [...railroads.values()].find((r) => r.placed);
    if (!placed) throw new Error("the fixture no longer has a placed railroad train");
    placed.marker.openPopup();
  });
  await expectState(page, "one popup open", "the popup correction specs need exactly one");
}

/* Grow the popup and report what the map did about it, once the app has had its chance.
   The growth is upward, because a Leaflet popup is anchored at its tip: the bottom stays on
   the marker and the top rises, which at 375 is straight into the legend.
   LEAFLET'S OWN AUTOPAN IS REPORTED SEPARATELY. A popup that grows past the top of the
   viewport is Leaflet's business to nudge back in, and that nudge moves the centre without
   the app's correction having run at all. So the centre claim below is made only when no
   autopanstart fired, and the claim that always holds is about where the POPUP ended up,
   which is the fact a rider can see. */
const growAndSettle = (page, by) =>
  page.evaluate(
    (px) =>
      new Promise((resolve) => {
        let autoPanned = false;
        const noteAutoPan = () => {
          autoPanned = true;
        };
        map.on("autopanstart", noteAutoPan);
        const root = openPopupsOnMap()[0].getElement();
        let deliveries = 0;
        const probe = new ResizeObserver(() => {
          deliveries += 1;
          if (deliveries === 1) {
            // The initial observation. The app's observer, created first, has seen the same
            // one; now change the size for real.
            root
              .querySelector(".leaflet-popup-content")
              .appendChild(Object.assign(document.createElement("div"), { style: `height:${px}px` }));
            return;
          }
          probe.disconnect();
          map.off("autopanstart", noteAutoPan);
          const c = map.getCenter();
          const a = root.getBoundingClientRect();
          const b = document.getElementById("panel").getBoundingClientRect();
          const container = document.getElementById("map");
          resolve({
            centre: { lat: c.lat, lng: c.lng },
            autoPanned,
            underTheLegend: a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top,
            // ASKED OF THE APP'S OWN GEOMETRY, which is the only honest way to say "it could
            // have moved this and chose not to". popupClearingShift returns null when no
            // move clears every obstacle AND keeps the popup inside the map, and null is not
            // a decision: it is an impossibility. See the note in A4j.
            clearingMoveExists: !!popupClearingShift(a, popupObstacles(), container.getBoundingClientRect()),
          });
        });
        probe.observe(root);
      }),
    by,
  );

test("A4j. once the rider moves the map, the popup correction stands down", async ({ page }) => {
  /* THE SAME PRINCIPLE AS THE MOTION SPLIT ONE LEVEL UP. The correction that moves a popup
     out from under the chrome watches the popup for size changes, so it outlives the
     opening: a background arrivals refresh re-ran it on a map the RIDER had since moved and
     threw their position away. Measured at 375 before the fix: the rider dragged to centre
     lat 40.65134, a refresh grew the popup, and the map jumped to 40.72996.

     An adjustment is the app correcting its own fit. The moment the rider takes over, the
     position is theirs and the app has no business tidying it, even when it can see the
     popup is now behind the legend.

     A REAL DRAG, because the stand-down keys on rider INTENT. Round 3 replaced a centre
     comparison with dragstart/zoomstart/movestart precisely because Leaflet's own internals
     move the centre by half a pixel on any viewport parity flip, with no rider anywhere
     near it. Driving this with page.mouse means the spec exercises the signal the app
     actually listens for rather than a proxy for it. */
  await popupOnTheMapAt375(page);

  const before = await page.evaluate(() => {
    const c = map.getCenter();
    return { lat: c.lat, lng: c.lng };
  });

  /* Bottom-left of the viewport is map and only map at this width: the legend is top-right,
     the popup is mid-screen and the station panel is shut.
     DOWN AND TO THE LEFT, and the direction is load-bearing. The first draft dragged right,
     which pushed the popup's right edge from 370 to 390, past the map container's 375, and
     popupClearingShift refuses any move that would leave the popup outside the map. There
     was no clearing move to decline, so deleting the stand-down guard changed nothing and
     the spec passed 20/20 against a build with its subject removed. Dragging left keeps a
     move available (measured: down by 61px), so declining it is a decision. */
  await page.mouse.move(60, 620);
  await page.mouse.down();
  await page.mouse.move(40, 640, { steps: 12 });
  await page.mouse.up();
  const dragged = await page.evaluate(() => {
    const c = map.getCenter();
    return { lat: c.lat, lng: c.lng };
  });
  expect(dragged, "the drag must actually have moved the map, or there is no takeover").not.toEqual(before);

  // THE PREMISE, MEASURED RATHER THAN ASSUMED: the popup is currently clear of the legend
  // and 80px of upward growth will put it underneath. Without this the spec would pass just
  // as well on a popup nowhere near the chrome, which is a spec about nothing.
  const willCollide = await page.evaluate(() => {
    const a = openPopupsOnMap()[0].getElement().getBoundingClientRect();
    const b = document.getElementById("panel").getBoundingClientRect();
    const overlapsNow = a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    return { overlapsNow, willOverlap: a.left < b.right && a.right > b.left && a.top - 80 < b.bottom };
  });
  expect(willCollide, "the growth must be what creates the collision, not the drag").toEqual({
    overlapsNow: false,
    willOverlap: true,
  });

  const after = await growAndSettle(page, 80);
  // THE ASSERTION, in the terms a rider sees: the popup is under the legend and the app left
  // it there. This is the fact Leaflet's own autopan cannot fake: an autopan pushes an
  // overflowing popup back DOWN into the viewport, deeper under the legend, never clear of it.
  expect(after.underTheLegend, "the app must not tidy a position the rider chose").toBe(true);
  // AND IT DECLINED SOMETHING IT COULD HAVE DONE. Without this the assertion above is
  // satisfied by geometry in which no clearing move exists at all, which is how the first
  // rebuild of this spec passed with the stand-down guard deleted.
  expect(
    after.clearingMoveExists,
    "a clearing move must be available for declining it to mean anything: with none, this " +
      "spec passes against a build that has no stand-down guard at all",
  ).toBe(true);
  // And the stricter claim, made only when it is the app's to make. If Leaflet autopanned,
  // the centre moved for a reason that is not this correction and asserting on it would be
  // asserting about Leaflet.
  if (!after.autoPanned) {
    expect(after.centre, "and with no autopan in play, nothing moved the map at all").toEqual(dragged);
  }

  /* A SECOND GROWTH, BECAUSE ONE PROVES ONLY THAT THE FIRST WAS DECLINED. Round 4: making the
     stand-down ONE-SHOT (consumed by the first post-takeover resize and forgotten) passed
     this spec 15 runs out of 15, because the spec only ever grew the popup once. What that
     costs a rider is the original defect delayed by one refresh: they drag the map to their
     own neighbourhood, the next arrivals refresh is correctly declined, and the one fifteen
     seconds later throws their position away exactly as before.
     The takeover is a property of the rider's ownership of THIS popup, and it ends when the
     popup does (popupopen resets it), so it must survive every resize in between. */
  const again = await growAndSettle(page, 40);
  expect(again.underTheLegend, "and it must still be declined on the NEXT refresh, and every one after").toBe(true);
  expect(again.clearingMoveExists, "with a clearing move still available the second time").toBe(true);
});

test("A4m. a rider who pans with the arrow keys owns the view too", async ({ page }) => {
  /* THE RIDER THIS PHASE IS FOR, and until round 4 the one whose takeover was never measured.
     Every stand-down spec in this phase drives page.mouse. Deleting the movestart handler,
     one of the guard's three intent producers and the only one a keyboard pan reaches (since
     Leaflet's Keyboard handler goes through panBy and fires neither dragstart nor zoomstart),
     left ALL 162 e2e specs green. Measured: the rider's centre thrown from 40.71955 to
     40.74036 by the next popup growth, with a clearing move available, so the app really did
     choose to move.

     THE PRESS IS THE ASSERTION, not the centre. Under this suite's paused clock Leaflet's
     keyboard pan starts and never completes, so the centre does not change; what does happen,
     and what the guard listens for, is movestart. That is asserted directly below rather than
     inferred from a position, which is the same lesson round 3 wrote into the guard itself:
     intent is an event, not a float. */
  await popupOnTheMapAt375(page);

  await page.evaluate(() => {
    window.__moveStarts = 0;
    map.on("movestart", () => {
      window.__moveStarts += 1;
    });
  });
  await page.locator("#map").focus();
  await expect(page.locator("#map")).toBeFocused();
  await page.keyboard.press("ArrowRight");

  expect(
    await page.evaluate(() => window.__moveStarts),
    "the arrow key must actually reach Leaflet's keyboard pan, or this spec presses nothing",
  ).toBeGreaterThan(0);

  const after = await growAndSettle(page, 80);
  expect(after.underTheLegend, "a keyboard pan is a rider taking over, exactly like a drag").toBe(true);
  expect(after.clearingMoveExists, "with a clearing move available, so declining it is a decision").toBe(true);
});

test("A4k. with no rider takeover, that same growth DOES move the popup clear", async ({ page }) => {
  // THE CONTROL, and A4j means nothing without it. Same fixture, same growth, same
  // deterministic wait; the only difference is that nobody dragged. If this ever goes green
  // by the correction simply not running, A4j is satisfied by an absence rather than by a
  // decision, which is exactly the failure round 3 found.
  await popupOnTheMapAt375(page);

  const before = await page.evaluate(() => {
    const c = map.getCenter();
    return { lat: c.lat, lng: c.lng };
  });
  const after = await growAndSettle(page, 80);

  // THE SAME OBSERVABLE AS A4j, IN THE OPPOSITE DIRECTION. A pair that asserts one fact each
  // way pins a decision; A4j alone would be satisfied by a correction that never runs.
  expect(after.underTheLegend, "the app corrects its own fit when the position is still its own").toBe(false);
  expect(after.centre, "which it does by moving the map").not.toEqual(before);
  await expectState(page, "one popup open", "A4k: still exactly one popup after the correction");
});

test("A4l. Leaflet's own autopan is not the rider taking over", async ({ page }) => {
  /* THE DEFECT THE STAND-DOWN GUARD CREATED, found by measuring the guard rather than by
     reading it. The guard tells the rider's hand from the app's own adjustment by watching
     movestart and ignoring the ones that happen while Leaflet is autopanning. The flag that
     says "Leaflet is autopanning" was cleared one line too early, before the panBy that
     fires the movestart, so it was already false when the handler asked, and every autopan
     was filed as the rider taking over.

     What that costs a rider: Leaflet nudges a popup that overflows the viewport back into
     view, entirely on its own; from then on the app believes the position is the rider's and
     declines to move that popup out from under the legend, however far under it goes.
     Measured at 375 before the fix, with a clearing move available the whole time:

       after the autopan   popup at y 5..131, clear move exists
       after a refresh     popup at y -35..131, still under the legend, still a move available
       with the fix        popup at y 292..458, clear of the legend

     THE SETUP DRIVES LEAFLET, NOT THE APP. The marker moves to 60px below the top of the
     map, which puts its popup's top off-screen, and popup.update() is Leaflet's own path to
     _adjustPan. The rider's hand is nowhere in it. */
  await popupOnTheMapAt375(page);

  const staged = await page.evaluate(() => {
    const placed = [...railroads.values()].find((r) => r.placed);
    const popup = placed.marker.getPopup();
    let autoPanned = false;
    const note = () => {
      autoPanned = true;
    };
    map.on("autopanstart", note);
    placed.marker.setLatLng(map.containerPointToLatLng([map.getSize().x / 2, 60]));
    popup.update();
    map.off("autopanstart", note);
    const a = popup.getElement().getBoundingClientRect();
    return {
      autoPanned,
      clearingMoveExists: !!popupClearingShift(a, popupObstacles(), document.getElementById("map").getBoundingClientRect()),
    };
  });
  // Both halves of the premise, because either one silently missing makes the rest vacuous:
  // Leaflet must really have autopanned, and the app must really have somewhere to move the
  // popup to. A popup with no clearing move is declined for geometry, not for policy.
  expect(staged.autoPanned, "the setup must actually make Leaflet autopan").toBe(true);
  expect(staged.clearingMoveExists, "and a clearing move must exist after it").toBe(true);

  const after = await growAndSettle(page, 40);
  expect(
    after.underTheLegend,
    "an autopan is the app's own adjustment; treating it as a takeover leaves the rider's " +
      "popup buried under the legend with a move available and nobody willing to make it",
  ).toBe(false);

  /* AND THE OTHER DIRECTION, WHICH THE SAME ONE FLAG DECIDES. Round 4: never clearing
     leafletAutoPanning at all (the opposite lifetime error to the round-3 one this spec was
     written for) left all 162 e2e specs green. Once Leaflet has autopanned even once, every
     subsequent movestart is filed as the app's own adjustment forever, so a rider who then
     pans is not registered as taking over and the next growth jumps the map off their
     position (measured: 40.76221 -> 40.83680).
     A flag that must be true during one pan and false after it needs both halves asserted;
     asserting only "an autopan does not read as the rider" leaves the half that says "and
     afterwards the rider still does" to nobody. */
  await page.evaluate(() => {
    window.__moveStarts = 0;
    map.on("movestart", () => {
      window.__moveStarts += 1;
    });
  });
  await page.locator("#map").focus();
  await page.keyboard.press("ArrowRight");
  expect(
    await page.evaluate(() => window.__moveStarts),
    "the arrow key must reach Leaflet, or the second half of this spec presses nothing",
  ).toBeGreaterThan(0);

  const afterRider = await growAndSettle(page, 40);
  expect(
    afterRider.underTheLegend,
    "after an autopan, a REAL rider move must still read as the rider taking over",
  ).toBe(true);
  expect(afterRider.clearingMoveExists, "with a clearing move available, so declining it is a decision").toBe(true);
});
