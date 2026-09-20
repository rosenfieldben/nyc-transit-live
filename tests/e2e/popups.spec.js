/* MR5: the popup's own claims.
   ==========================================================================

   What stage MR5 promises that no other spec in this suite was written to check. The pins next
   door (pins.spec.js P5a through P5c) say what a popup must NOT stop saying; this file says what
   the popup's chrome and its autopan must DO. Ids are D6, following D5 for the dark theme, D4 for
   the families, D3 for the commuter rail, D2 for the subway and D1 for MR1's top bar.

   WHAT IS DELIBERATELY NOT HERE, because it is already somewhere better:
   - every string a rider reads, per system and per position state: pins.spec.js P5a and P5c
   - that no rider-facing string is uncovered by a pin: pins.spec.js P5b
   - axe over the whole page with a popup open, at three widths in both themes: a11y.spec.js
     A1w's "popup open with cross-link" states
   - the popup close glyph's contrast against its own background: a11y.spec.js A1z
   - the cross-link button's behaviour and its focus: crosslink.spec.js
   - that a closed popup's corpse is not mistaken for an open one: popup.js's own comment
   - the collision solver's geometry, which needs no browser at all: frontend/helpers.test.js

   WHAT IS HERE is section 5's wrapper and metrics in both themes, and ruling S3's clamped
   autopan: the cap firing where the design's recipe is unsatisfiable, the recipe surviving
   untouched where it fits, and the stand-down while the rider owns the view. */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");
const { inPage, closeAllPopups, STOCK_SURFACES } = require("./popup");
const { expectState } = require("./state");

const DESKTOP = { width: 1280, height: 720 };
const PHONE = { width: 375, height: 667 };

async function boot(page, { viewport = DESKTOP, before } = {}) {
  await page.setViewportSize(viewport);
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  // Every system's marks, because this file opens every system's popup: a partially loaded page
  // would silently skip whichever surface had not arrived yet.
  await page.waitForFunction(
    () =>
      trains.size === 2 &&
      buses.size === 2 &&
      railroads.size === 2 &&
      pathTrainRecords.size === 2 &&
      ferryBoatRecords.size === 3 &&
      njtTrainRecords.size === 4 &&
      stationRegistry.length === 14,
    null,
    { timeout: 15_000 },
  );
  await page.clock.runFor(1000);
  return ctx;
}

// Through the app's own toggle rather than by writing the attribute, so MR4's canvas repaint runs
// too and the page is in the state a rider's press actually produces.
const setTheme = (page, theme) =>
  page.evaluate((t) => {
    if (document.documentElement.getAttribute("data-theme") !== t) document.getElementById("theme-toggle").click();
    return document.documentElement.getAttribute("data-theme");
  }, theme);

/* Open one named surface and let it settle. Same settle discipline as pins.spec.js and for the
   same measured reason: the clock is paused, so a station popup's arrivals resolve on the
   DRIVER's round trips and an in-page wait deadlocks. "Loading arrivals" is excluded explicitly,
   because a loading popup has a real box and would answer every geometry question below with the
   wrong height. */
async function openSettled(page, which) {
  await closeAllPopups(page);
  await page.evaluate(inPage("MARKERS[which]().openPopup();"), which);
  let previous = null;
  for (let i = 0; i < 80; i++) {
    const now = await page.evaluate(
      inPage(`
        const el = MARKERS[which]().getPopup()?.getElement();
        const content = el && el.querySelector(".leaflet-popup-content");
        if (!content) return null;
        const box = content.getBoundingClientRect();
        return { text: content.textContent.replace(/\\s+/g, " ").trim(), h: Math.round(box.height) };
      `),
      which,
    );
    const key = now && JSON.stringify(now);
    if (key && key === previous && !/Loading arrivals/.test(now.text)) return now;
    previous = key;
    await page.clock.runFor(100);
  }
  throw new Error(`${which}: the popup never settled (last seen: ${previous})`);
}

// The wrapper, the tip, the content and the close button, measured off the LIVE element rather
// than read out of the stylesheet: a rule that does not reach the element is a rule that is not
// shipped, and this file exists to catch exactly that.
const chromeOf = (page, which) =>
  page.evaluate(
    inPage(`
      const el = MARKERS[which]().getPopup().getElement();
      const read = (node, props) => {
        if (!node) return null;
        const cs = getComputedStyle(node);
        return Object.fromEntries(props.map((p) => [p, cs.getPropertyValue(p)]));
      };
      const content = el.querySelector(".leaflet-popup-content");
      return {
        wrapper: read(el.querySelector(".leaflet-popup-content-wrapper"),
          ["background-color", "border-radius", "border-left-width", "border-left-color", "backdrop-filter",
           "box-shadow", "color"]),
        tip: read(el.querySelector(".leaflet-popup-tip"),
          ["background-color", "backdrop-filter", "border-left-width"]),
        content: read(content, ["margin-top", "margin-left", "font-size", "line-height", "font-family", "color"]),
        close: read(el.querySelector(".leaflet-popup-close-button"), ["color", "font-size"]),
        contentWidth: content ? Math.round(content.getBoundingClientRect().width) : null,
      };
    `),
    which,
  );

/* The tokens, with the COLOUR ones handed back through the browser's own parser.
   A token is declared as a hex and computes on an element as an `rgb()`, so comparing
   `getPropertyValue("--ink")` to a computed `border-left-color` compares two spellings of one
   colour and fails on the spelling. tests/e2e/contrast.js takes the same escape for the same
   reason: assign the value to a probe and read back what the browser made of it. `--shadow` and
   `--chrome-font` are not colours and are returned as declared. */
const tokens = (page) =>
  page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    const colours = ["--surface", "--ink", "--muted"];
    const plain = ["--shadow", "--chrome-font"];
    const resolve = (value) => {
      const probe = document.createElement("span");
      probe.style.color = value;
      document.body.append(probe);
      const out = getComputedStyle(probe).color;
      probe.remove();
      return out;
    };
    const out = {};
    for (const t of colours) out[t] = resolve(cs.getPropertyValue(t).trim());
    for (const t of plain) out[t] = cs.getPropertyValue(t).trim();
    return out;
  });

/* ---------------- section 5's chrome ---------------- */

for (const theme of ["light", "dark"]) {
  test(`D6a. the popup wears the design's own surface, edge and metrics (${theme})`, async ({ page }) => {
    await boot(page);
    expect(await setTheme(page, theme)).toBe(theme);
    await openSettled(page, "subway train");
    const got = await chromeOf(page, "subway train");
    const tok = await tokens(page);

    /* THE SURFACE IS THE TOKEN AT FULL STRENGTH, AND THE ALPHA IS THE CLAIM IN REVERSE.
       Section 5 draws the popup at 94%; it ships opaque, by ruling, for MR1's F1 reason about
       the same drawing one surface out: axe cannot resolve the contrast of text over a
       translucent surface whose backdrop is a tile IMAGE. Measured at 94%, every popup's text
       came back `incomplete` at all three widths in both themes, and the dark theme's REAL
       contrast violation on the head's ink and .popup-sub was reported only as undecidable. So
       an alpha below 1 here is a regression, and this is where it is caught.

       AN UNPARSEABLE VALUE THROWS RATHER THAN READING AS OPAQUE, which is this stage's own lesson
       about itself. The design's color-mix computes to `color(srgb 0.917647 ... / 0.94)`, and the
       first draft of this very assertion returned 1 for it and certified a translucent popup as
       solid. So did layout.spec.js A4g's ancestor walk. A parser that silently answers "opaque"
       for a value it does not understand is how the translucency would have shipped, so `alpha`
       refuses anything but an rgb/rgba. */
    const alpha = (value) => {
      const m = /^rgba?\(([^)]*)\)$/.exec(String(value).trim());
      if (!m) throw new Error(`the popup's background is not an rgb/rgba and cannot be read: ${value}`);
      const parts = m[1].split(/[\s,/]+/).filter(Boolean);
      return parts.length === 4 ? Number(parts[3]) : 1;
    };
    const rgb = (value) =>
      String(value).replace(/^rgba?\(/, "").split(/[\s,/]+/).filter(Boolean).slice(0, 3).join(",");
    expect(alpha(got.wrapper["background-color"]), "the wrapper's surface is opaque, per the ruling").toBe(1);
    expect(alpha(got.tip["background-color"]), "and so is the tip's").toBe(1);
    expect(rgb(got.wrapper["background-color"]), "which is the token, not Leaflet's white").toBe(rgb(tok["--surface"]));
    expect(rgb(got.tip["background-color"]), "the same colour under both, so the two read as one").toBe(
      rgb(got.wrapper["background-color"]),
    );

    /* THE BLUR IS DECLARED AND IT PAINTS NOTHING, asserted because it was RULED rather than
       because it shows: backdrop-filter filters what is behind the element and the element's own
       background then paints over it, so at alpha 1 with no radius none of the filtered backdrop
       is ever visible. MR1 dropped the filter along with the header's translucency for exactly
       this reason. frontend/tokens.test.js holds the sentence in style.css that says so, so the
       declaration cannot quietly come to be read as doing something. */
    expect(got.wrapper["backdrop-filter"], "section 5's blur, kept by ruling").toBe("blur(14px)");
    expect(got.tip["backdrop-filter"], "on both surfaces, so the rule stays one rule").toBe("blur(14px)");
    expect(got.wrapper["border-radius"], "section 5 has no radius").toBe("0px");
    /* THE SHADOW IS THE TOKEN, compared as its PARTS rather than as its text: a computed
       box-shadow is normalised to "colour x y blur spread", so `0 3px 10px rgba(...)` comes back
       as `rgba(...) 0px 3px 10px 0px` and a string equality fails on formatting rather than on
       the shadow. Both are reduced to a colour plus the lengths in order, which still fails when
       Leaflet's own `0 3px 14px rgba(0,0,0,0.4)` survives. */
    const shadowParts = (value) => {
      const colour = (String(value).match(/rgba?\([^)]*\)/) || [""])[0].replace(/\s+/g, "");
      const lengths = String(value)
        .replace(/rgba?\([^)]*\)/g, "")
        .split(/\s+/)
        .filter(Boolean)
        .map((n) => parseFloat(n))
        .filter((n) => Number.isFinite(n) && n !== 0);
      return { colour, lengths };
    };
    expect(shadowParts(got.wrapper["box-shadow"]), "the shadow is the token, not Leaflet's own").toEqual(
      shadowParts(tok["--shadow"]),
    );

    /* THE INK EDGE IS ON THE WRAPPER AND NOT ON THE TIP, which is a decision rather than an
       oversight: the tip is one square rotated 45 degrees, so a border-left on it paints a
       diagonal stripe across the arrow. Asserted in BOTH directions, so a later edit that
       "tidies" the rule by moving it onto the shared selector fails here. */
    expect(got.wrapper["border-left-width"]).toBe("2px");
    expect(got.wrapper["border-left-color"]).toBe(tok["--ink"]);
    expect(got.tip["border-left-width"], "the rotated tip takes no edge").toBe("0px");

    // The content metrics, and the type is the app's own face rather than a popup-local stack.
    expect(got.content["margin-top"]).toBe("14px");
    expect(got.content["margin-left"]).toBe("16px");
    expect(got.content["font-size"]).toBe("13px");
    expect(parseFloat(got.content["line-height"]), "13px at 1.35").toBeCloseTo(13 * 1.35, 1);
    // The quotes around a family name are a spelling too: the token declares `"Archivo"` and a
    // computed font-family drops the quotes. Compared as the list of families.
    const families = (value) =>
      String(value)
        .split(",")
        .map((f) => f.trim().replace(/^["']|["']$/g, ""));
    expect(families(got.content["font-family"]), "the popup's type is the app's own face").toEqual(
      families(tok["--chrome-font"]),
    );
    expect(got.content["color"], "the popup's ink follows the theme").toBe(tok["--ink"]);

    // The close button, which Leaflet paints #757575 on white and which is a 2.4 on the dark
    // surface. a11y.spec.js A1z measures the ratio; this asserts the paint is the token.
    expect(got.close["color"]).toBe(tok["--muted"]);
    expect(got.close["font-size"]).toBe("16px");
  });
}

test("D6b. maxWidth 320 and the 220px floor reach every bind site in the app", async ({ page }) => {
  await boot(page);
  /* THE OPTION IS READ OFF EVERY POPUP THE APP BOUND, not off the seven call sites: a bind site
     added in a later stage without POPUP_OPTIONS is exactly the defect this catches, and a list
     of seven names here would pass while an eighth went without. */
  const bound = await page.evaluate(() => {
    const out = [];
    const note = (label, marker) => {
      const popup = marker && marker.getPopup && marker.getPopup();
      if (popup) {
        out.push({ label, maxWidth: popup.options.maxWidth ?? null, minWidth: popup.options.minWidth ?? null });
      }
    };
    for (const [key, r] of trains) note(`subway ${key}`, r.marker);
    for (const [key, r] of buses) note(`bus ${key}`, r.marker);
    for (const [key, r] of railroads) note(`railroad ${key}`, r.marker);
    for (const [key, r] of njtTrainRecords) note(`njt ${key}`, r.marker);
    for (const [key, r] of pathTrainRecords) note(`path ${key}`, r.marker);
    for (const [key, r] of ferryBoatRecords) note(`ferry ${key}`, r.marker);
    for (const e of stationRegistry) note(`station ${e.key}`, e.marker);
    return out;
  });
  expect(bound.length, "every system's marks are bound, or this proves nothing").toBeGreaterThanOrEqual(28);
  /* LEAFLET'S OWN DEFAULT minWidth IS 50, measured: every popup reports 50 whether or not anyone
     set one, so "no minWidth" cannot be spelled as null here. What the claim actually is: no bind
     site names one, so all of them report the SAME number and that number is the library's. The
     station popups used to name 170, which is below section 5's floor and would write an inline
     width that fights the CSS, so it is the odd value out that this catches. */
  const minWidths = new Set(bound.map((row) => row.minWidth));
  expect([...minWidths], "one bind site still names a minWidth of its own").toEqual([50]);
  for (const row of bound) {
    expect(row.maxWidth, `${row.label}: bound without POPUP_OPTIONS`).toBe(320);
  }

  // And the floor is real on the shortest popup the fixtures can produce.
  const settled = await openSettled(page, "bus");
  expect(settled.h, "a settled popup has a box at all").toBeGreaterThan(0);
  const got = await chromeOf(page, "bus");
  expect(got.contentWidth, "the 220px floor reaches a three-line popup").toBeGreaterThanOrEqual(220);
  expect(got.contentWidth, "and maxWidth 320 still bounds it").toBeLessThanOrEqual(320);
});

test("D6c. every system's popup opens, reads and closes in both themes, at desktop and 375", async ({ page }) => {
  /* THE BREADTH CLAIM, and it is about the CHROME rather than the words: P5a holds the strings.
     What this catches is a popup that throws on open, opens with no box, or opens with a box the
     restyle never reached, on a surface nobody thought to check. Fourteen surfaces times two
     themes times two widths. */
  test.slow();
  await boot(page);
  for (const viewport of [DESKTOP, PHONE]) {
    await page.setViewportSize(viewport);
    for (const theme of ["light", "dark"]) {
      expect(await setTheme(page, theme)).toBe(theme);
      for (const which of STOCK_SURFACES) {
        const label = `${which} @ ${viewport.width} ${theme}`;
        const settled = await openSettled(page, which);
        expect(settled.text.length, `${label}: the popup opened empty`).toBeGreaterThan(8);
        expect(settled.h, `${label}: the popup has no height`).toBeGreaterThan(8);
        const got = await chromeOf(page, which);
        expect(got.wrapper["border-radius"], `${label}: the restyle did not reach this surface`).toBe("0px");
        expect(got.contentWidth, `${label}: below section 5's floor`).toBeGreaterThanOrEqual(220);
        await closeAllPopups(page);
        const gone = await page.evaluate(inPage("return MARKERS[which]().isPopupOpen();"), which);
        expect(gone, `${label}: the popup would not close`).toBe(false);
      }
    }
  }
});

test("D6h. a popup open across a theme swap re-inks its head, rather than keeping the old theme's", async ({
  page,
}) => {
  /* THE ONE THING IN A POPUP THAT DOES NOT FOLLOW THE CASCADE. A popup is HTML, so everything in
     it that says `var(--x)` swaps for free. The six route-coloured heads do not: readableInk needs
     a background as a STRING, so popupSurfaceColor() resolves the token when the popup is BUILT
     and the answer is baked into an inline style. Without applyTheme rebuilding open popups, a
     popup built in the light theme keeps light-theme ink on a dark surface, which for a vehicle is
     until the next fifteen-second poll and for a STATION is until the rider closes it.

     ASSERTED AS A CHANGE AND AS AN EQUALITY, because either alone is weak. That the colour moved
     says the rebuild ran; that it equals what the helper returns for the DARK surface says it
     rebuilt against the right background rather than merely re-running. The expected value is
     computed in the page from the app's own helpers, so this is not a second copy of the
     arithmetic. */
  await boot(page);
  expect(await setTheme(page, "light")).toBe("light");
  await openSettled(page, "subway train");

  const headInk = () =>
    page.evaluate(
      inPage(`
        const el = MARKERS[which]().getPopup().getElement();
        const head = el.querySelector(".leaflet-popup-content b");
        if (!head) return null;
        const route = trains.get("sub-1").latest.route_id;
        return {
          painted: getComputedStyle(head).color,
          wantForThisTheme: readableInk(lineColor(route), popupSurfaceColor()),
          surface: popupSurfaceColor(),
        };
      `),
      "subway train",
    );
  // The probe resolves the expected hex through the browser so the two are one spelling.
  const resolve = (value) =>
    page.evaluate((v) => {
      const probe = document.createElement("span");
      probe.style.color = v;
      document.body.append(probe);
      const out = getComputedStyle(probe).color;
      probe.remove();
      return out;
    }, value);

  const before = await headInk();
  expect(before, "the subway popup still has a route-coloured head to measure").not.toBeNull();
  expect(before.painted).toBe(await resolve(before.wantForThisTheme));

  expect(await setTheme(page, "dark")).toBe("dark");
  // The swap is synchronous in applyTheme, but the popup's own content function runs inside
  // popup.update(); poll rather than assume an ordering this spec does not own.
  await expect
    .poll(async () => (await headInk()).surface)
    .not.toBe(before.surface);
  const after = await headInk();
  expect(after.surface, "the dark theme resolved a different popup surface").not.toBe(before.surface);
  expect(after.painted, "the head was re-inked against the dark surface").toBe(
    await resolve(after.wantForThisTheme),
  );
  expect(after.painted, "and it is not the light theme's ink still sitting there").not.toBe(before.painted);
});

/* ---------------- ruling S3: the clamped autopan ---------------- */

// What the app decided, asked OF the app rather than re-derived here: the padding it wrote onto
// the popup's options, the edge it measured, and where the popup actually sits on the map.
const padOf = (page, which) =>
  page.evaluate(
    inPage(`
      const popup = MARKERS[which]().getPopup();
      const tl = popup.options.autoPanPaddingTopLeft ?? null;
      const br = popup.options.autoPanPaddingBottomRight ?? null;
      const box = popup.getElement().querySelector(".leaflet-popup-content-wrapper").getBoundingClientRect();
      const mapBox = document.getElementById("map").getBoundingClientRect();
      return {
        topLeft: tl ? [tl.x, tl.y] : null,
        bottomRight: br ? [br.x, br.y] : null,
        chromeBottom: Math.round(pageChromeBottom()),
        want: popupAutoPanWant(pageChromeBottom()),
        map: { width: Math.round(mapBox.width), height: Math.round(mapBox.height) },
        popup: { width: Math.round(box.width), height: Math.round(box.height) },
        onScreen: {
          top: Math.round(box.top - mapBox.top),
          bottom: Math.round(box.bottom - mapBox.top),
          left: Math.round(box.left - mapBox.left),
          right: Math.round(box.right - mapBox.left),
        },
      };
    `),
    which,
  );

test("D6d. the cap fires at 375 with the Key open, where the recipe's own padding cannot fit", async ({ page }) => {
  await boot(page, { viewport: PHONE });
  // The Key open is the state the erratum measured: #panel is capped at calc(100% - 72px), so its
  // bottom edge is most of the viewport and the derived top padding is most of the map.
  await page.evaluate(() => document.getElementById("legend-toggle").click());
  // The witness first, asserted before anything is measured: a click that silently did nothing
  // would leave a short header and every number below would still be internally consistent,
  // which is state.js's whole lesson.
  await expect(page.locator("#legend")).toBeVisible();
  await openSettled(page, "lirr station");
  const got = await padOf(page, "lirr station");

  expect(got.chromeBottom, "the open Key really does reach down the map").toBeGreaterThan(400);
  expect(
    got.want.top + got.want.bottom + got.popup.height,
    "the recipe's own pair is unsatisfiable here, which is the finding",
  ).toBeGreaterThan(got.map.height);

  // The cap, and the arithmetic it is capped to: the bottom padding survives whole and the
  // derived top takes exactly the slack, which is what makes the pair satisfiable.
  expect(got.topLeft, "a padding was applied at all").not.toBeNull();
  expect(got.bottomRight[1], "the small fixed padding below is kept whole").toBe(got.want.bottom);
  expect(got.topLeft[1], "and the derived one is cut to what is left").toBe(
    got.map.height - got.popup.height - got.want.bottom,
  );
  expect(got.topLeft[1], "which is strictly less than the recipe asked for").toBeLessThan(got.want.top);
  expect(got.topLeft[1] + got.bottomRight[1] + got.popup.height, "and the clamped pair fits").toBeLessThanOrEqual(
    got.map.height,
  );

  /* AND THE POPUP IS ON THE MAP, which is the defect the erratum records: unclamped, Leaflet puts
     this popup 51px past the bottom edge, because its top branch assigns second and wins. This is
     the assertion the whole ruling exists for. */
  expect(got.onScreen.top, "the popup's top is on the map").toBeGreaterThanOrEqual(0);
  expect(got.onScreen.bottom, "and so is its bottom").toBeLessThanOrEqual(got.map.height);
});

test("D6e. the measured edge is the header AND the alert strip, with an alert present", async ({ page }) => {
  /* THE STRIP HAS TO BE THERE, or this passes on a page where the second obstacle does not exist.
     An agency-wide alert is what puts the banner on screen: it belongs to no route or station, so
     the banner is its only surface. */
  await boot(page, {
    viewport: PHONE,
    before: (ctx) => {
      // Keyed by the endpoint's NAME, which is how mock.js registers overrides, and agency-wide
      // (no routes, no stops) because that is what puts an alert in the BANNER rather than only
      // in a station popup. Same staging as announce.spec.js A2h.
      ctx.overrides.alerts = (route, fixtures) => {
        const body = fixtures.alerts();
        body.alerts = [
          {
            id: "mr5-strip",
            system: "subway",
            header: "Weekend work between Brooklyn and Manhattan",
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
    },
  });
  await page.waitForFunction(
    () => {
      const el = document.getElementById("alert-banner");
      return el && !el.hidden && el.getBoundingClientRect().height > 0;
    },
    null,
    { timeout: 15_000 },
  );
  await expectState(page, ["banner showing"]);

  await openSettled(page, "subway train");
  const got = await padOf(page, "subway train");
  const boxes = await page.evaluate(() => {
    const mapBox = document.getElementById("map").getBoundingClientRect();
    const read = (id) => {
      const el = document.getElementById(id);
      if (!el || el.hidden) return null;
      const b = el.getBoundingClientRect();
      return b.height > 0 ? { top: b.top - mapBox.top, bottom: b.bottom - mapBox.top } : null;
    };
    return { panel: read("panel"), "alert-banner": read("alert-banner") };
  });

  expect(boxes.panel, "the header is on screen").not.toBeNull();
  expect(boxes["alert-banner"], "and so is the alert strip").not.toBeNull();
  // The measured edge is the LOWER of the two, which is what "the header and alert strip's bottom
  // edge" means and what a single-obstacle measurement would get wrong.
  expect(got.chromeBottom).toBe(Math.round(Math.max(boxes.panel.bottom, boxes["alert-banner"].bottom)));

  /* AND THE POPUP IS CLEAR OF BOTH. Asserted on the RENDERED boxes rather than on the padding,
     because the padding is only the request: panPopupClearOfChrome is the authority for the real
     rects, and the two are supposed to compose into this one fact. */
  for (const [name, box] of Object.entries(boxes)) {
    const overlapping = got.onScreen.top < box.bottom && got.onScreen.bottom > box.top;
    expect(overlapping, `the popup is still under #${name}`).toBe(false);
  }
});

test("D6f. the padding stands down the moment the rider owns the view, and stays down", async ({ page }) => {
  await boot(page, { viewport: PHONE });
  await openSettled(page, "subway train");
  const armed = await padOf(page, "subway train");
  expect(armed.topLeft, "the padding is armed on open").not.toBeNull();

  /* THE RIDER TAKES OVER through the event that carries the intent, rather than by setting a flag:
     dragstart is what the app listens for, and a test that wrote riderOwnsTheView itself would
     pass with the listener deleted. */
  await page.evaluate(() => map.fire("dragstart"));
  const stood = await padOf(page, "subway train");
  expect(stood.topLeft, "the padding is DISARMED, not merely skipped").toBeNull();
  expect(stood.bottomRight, "both ends of it").toBeNull();

  /* AND THE FIFTEEN-SECOND POLL DOES NOT RE-ARM IT. This is the second break the erratum records:
     popup.update() re-ran Leaflet's autopan against whatever padding was on the options, so a
     stand-down that only skipped the next OPEN would still let the poll pull the map out from
     under a rider who had chosen their position.

     THE CENTRE HALF IS NOW OVER-DETERMINED AND THE PADDING HALF IS NOT, which is worth saying so
     this is not read as proving more than it does. POPUP_OPTIONS binds every popup with
     `autoPan: false` and the app runs the only pan there is, so a poll could not move the map here
     even with the padding armed: that assertion is held up by two things at once. The
     `topLeft === null` assertion is held up by one, the disarm in noteRiderTookOver, and mutation
     M62 removing it dies here. Both are kept: the centre is the fact a rider would notice, and
     belt and braces on a guard that has already been got wrong twice is not waste. */
  const centre = () =>
    page.evaluate(() => {
      const c = map.getCenter();
      return [c.lat, c.lng];
    });
  const before = await centre();
  await page.clock.runFor(20_000);
  const after = await centre();
  const still = await padOf(page, "subway train");
  expect(still.topLeft, "a poll re-armed the padding").toBeNull();
  expect(after[0]).toBeCloseTo(before[0], 6);
  expect(after[1]).toBeCloseTo(before[1], 6);

  // A NEW popup is a new placement, so the rider's ownership does not carry: the padding returns.
  await openSettled(page, "bus");
  const rearmed = await padOf(page, "bus");
  expect(rearmed.topLeft, "the next popup is padded again").not.toBeNull();
});

test("D6g. at desktop the recipe's own numbers survive, so the cap cannot be hiding a bug", async ({ page }) => {
  /* THE COUNTERWEIGHT TO D6d. A clamp that fired everywhere would have REPLACED the design rather
     than bounded it, and every assertion in D6d would still pass. At 1280 the header is a strip,
     so the pair fits and the padding is the recipe verbatim. */
  await boot(page);
  await openSettled(page, "subway train");
  const got = await padOf(page, "subway train");
  expect(got.want.top + got.want.bottom + got.popup.height).toBeLessThanOrEqual(got.map.height);
  expect(got.topLeft, "the design's own pair, untouched").toEqual([got.want.left, got.want.top]);
  expect(got.bottomRight).toEqual([got.want.right, got.want.bottom]);
});
