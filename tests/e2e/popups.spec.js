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

    /* AND NO BLUR, which went with the translucency exactly as MR1's F1 took it off the header. A
       backdrop filter filters what is behind the element and the element's own background then
       paints over it, so at alpha 1 with no radius none of the filtered backdrop is ever visible.
       Measured off the rendered element rather than the stylesheet, because that is what this file
       is for: a declaration that reaches the element is shipped whatever the rule says. */
    expect(got.wrapper["backdrop-filter"], "an opaque popup blurs a backdrop it hides").toBe("none");
    expect(got.tip["backdrop-filter"], "on both surfaces, so the rule stays one rule").toBe("none");
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
        // MR5: the head is section 5's title, whose WORDS carry the route ink (the row does not,
        // so the mark beside them keeps its own paints). popupTitleHtml says why.
        const head = el.querySelector(".leaflet-popup-content .pt span[style]");
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

test("D6j. a popup's title mark is the mark its own marker is wearing, and the canvas families have none", async ({
  page,
}) => {
  /* THE CLAIM THE NODE TIER CANNOT MAKE, and a reviewer proved it could not: replacing
     markerMarkHtml's body with a constant PATH diamond, so every popup title wore the same wrong
     mark, left all 392 node tests green. popupvocab.test.js asserts that popupMarkHtml COPIES the
     string it is given, which is a claim about that function; it says nothing about which string
     any popup asks for. Five of the six marks carry no text at all, so the pins cannot see it
     either: a bus, a PATH train, a ferry boat, a rail station and an AirTrain station would all
     read identically with the wrong picture beside the words.

     SO THIS ASKS THE PAGE, per family: the svg inside the popup's `.pmark` and the svg inside the
     marker's own icon must be the same markup. Not "the same builder called twice" and not "a
     mark of the right family": the same bytes, which is what "the mark in the popup is the mark on
     the map" means when the map has already resolved this vehicle's colour, code, body and
     bearing.

     AND THE THREE CANVAS FAMILIES MUST HAVE NONE. A subway station, a PATH station and a ferry
     dock are circleMarkers on a shared canvas with no element and no icon, so their titles carry
     words alone; asserting that is what keeps "no mark" a rule rather than an oversight. */
  await boot(page);
  const WITH_MARKS = [
    "subway train", "bus", "lirr train", "mnr train", "njt train", "path train", "ferry boat",
    "lirr station", "mnr station", "njt station", "airtrain station",
  ];
  const WITHOUT = ["subway station", "path station", "ferry dock"];

  for (const which of WITH_MARKS) {
    await openSettled(page, which);
    const read = await page.evaluate(
      inPage(`
        const marker = MARKERS[which]();
        const iconEl = marker.getElement();
        const markerSvg = iconEl && iconEl.querySelector("svg");
        const content = marker.getPopup().getElement().querySelector(".leaflet-popup-content");
        const popupSvg = content.querySelector(".pmark svg");
        return {
          hasMarker: !!markerSvg,
          hasPopup: !!popupSvg,
          sameBody: !!markerSvg && !!popupSvg && markerSvg.innerHTML === popupSvg.innerHTML,
          markerClass: markerSvg ? markerSvg.getAttribute("class") : null,
          popupClass: popupSvg ? popupSvg.getAttribute("class") : null,
          hidden: popupSvg ? popupSvg.closest(".pmark").getAttribute("aria-hidden") : null,
        };
      `),
      which,
    );
    expect(read.hasMarker, `${which}: the marker draws an svg to borrow`).toBe(true);
    expect(read.hasPopup, `${which}: the popup's title carries a mark`).toBe(true);
    expect(read.sameBody, `${which}: the popup's mark is not the marker's own markup`).toBe(true);
    expect(read.popupClass, `${which}: and it kept the mark's class`).toBe(read.markerClass);
    // The wrapper hides it, because the title says the same thing in words.
    expect(read.hidden, `${which}: the mark must be hidden from the accessibility tree`).toBe("true");
  }

  for (const which of WITHOUT) {
    await openSettled(page, which);
    const marks = await page.evaluate(
      inPage(`
        const content = MARKERS[which]().getPopup().getElement().querySelector(".leaflet-popup-content");
        return { title: !!content.querySelector(".pt"), marks: content.querySelectorAll(".pt .pmark").length };
      `),
      which,
    );
    expect(marks.title, `${which}: it still has a title`).toBe(true);
    expect(marks.marks, `${which}: a canvas family has no icon to borrow, so its title carries none`).toBe(0);
  }
  await closeAllPopups(page);
});

test("D6i. every route colour a popup prints as TEXT clears AA on the popup's own surface", async ({
  page,
}) => {
  /* FOUND BY A SURVIVING MUTATION, WHICH IS WHAT THEY ARE FOR. M66 removes popupSurfaceColor()
     from the ferry boat popup's arguments, so its head inks against helpers.js's light-theme
     fallback and is then printed on the dark surface. It SURVIVED the whole suite. The reason is
     coverage, not a sleeping assertion: a11y.spec.js A1w's popup states open a SUBWAY train popup,
     and layout.spec.js A4g opens the subway and NJ Transit ones. The bus, railroad, PATH and ferry
     heads, and every dock board's route-coloured bucket heading, were never measured anywhere. Five
     of the six route-coloured heads could have been inked against the wrong surface with every gate
     green, which is the same shape as the defect the head's ink was in the first place.

     SO THIS SWEEPS ALL FOURTEEN SURFACES IN BOTH THEMES and measures every INLINE colour a popup
     prints. Inline, specifically: a colour that came through the cascade is a token and follows the
     theme for free, and the ones that cannot are exactly the ones a builder resolved to a string.
     That is the set at risk and it is found by looking rather than by listing, so a seventh
     route-coloured surface added later is swept without anyone remembering to add it here.

     THE OBLIGATION, NOT THE MECHANISM. Asserting each value equals readableInk(colour, surface)
     would mean re-deriving six different route-colour resolvers here and would break the moment one
     of them legitimately changed. What a rider is owed is 4.5 against the surface the text is
     actually on, so that is what is asserted, and it is what M66 fails. */
  test.slow();
  await boot(page);
  const measured = [];
  for (const theme of ["light", "dark"]) {
    expect(await setTheme(page, theme)).toBe(theme);
    for (const which of STOCK_SURFACES) {
      await openSettled(page, which);
      const rows = await page.evaluate(
        inPage(`
          /* NO BACKSLASH ESCAPES IN HERE, AND THAT IS NOT STYLE. This block is a template literal
             that inPage hands to new Function, so every escape has to survive two readings: a
             regex written /[\\s,/]+/ in the file arrives as /[s,/]+/ in the page. The first draft
             of this spec did exactly that and it split colours on the letter "s", which turned
             every ratio into NaN, and NaN < 4.5 is FALSE, so the spec written to close a vacuity
             trap passed while measuring nothing. Parsing by indexOf and splitting on a literal
             comma needs no escapes at all, so there is nothing left to get wrong. The finiteness
             assertion below is the belt to this brace. */
          const channels = (value) => {
            const text = String(value);
            const open = text.indexOf("("), close = text.lastIndexOf(")");
            if (open < 0 || close < open) return null;
            const parts = text.slice(open + 1, close).split(",").map((p) => Number(p.trim()));
            if (parts.length < 3 || parts.slice(0, 3).some((n) => !isFinite(n))) return null;
            return parts.slice(0, 3);
          };
          const srgb = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
          const lum = (rgb) => 0.2126 * srgb(rgb[0]) + 0.7152 * srgb(rgb[1]) + 0.0722 * srgb(rgb[2]);
          const ratio = (a, b) => {
            const hi = Math.max(lum(a), lum(b)), lo = Math.min(lum(a), lum(b));
            return (hi + 0.05) / (lo + 0.05);
          };
          const probe = document.createElement("span");
          probe.style.color = popupSurfaceColor();
          document.body.append(probe);
          const surfaceText = getComputedStyle(probe).color;
          const surface = channels(surfaceText);
          probe.remove();
          /* WHAT A COLOUR IS PRINTED ON, WHICH IS TWO DIFFERENT QUESTIONS. Most inline colours in a
             popup are ink on the popup's own surface: the six route-coloured heads and the ferry
             dock's bucket headings. An .arr-badge is NOT: it is text on its own route-coloured fill,
             which is readableTextOn's job and is theme-independent, so measuring it against the
             popup surface asks the wrong question and answers 1.21. layout.spec.js A4g draws the
             same line between its "ink" and "fill" samples. Found by measurement: the first working
             version of this reader reported twelve arrival badges as failures.
             A transparent background is detected by its ALPHA rather than by a keyword, because the
             CSS keyword computes to rgba(0, 0, 0, 0) and never appears as itself. And no backticks
             in here either: this whole block is inside one, and a pair of them closed the literal
             early and broke the file's parse. */
          const ownFill = (el) => {
            const raw = getComputedStyle(el).backgroundColor;
            const open = raw.indexOf("("), close = raw.lastIndexOf(")");
            if (open < 0) return null;
            const parts = raw.slice(open + 1, close).split(",").map((x) => Number(x.trim()));
            if (parts.length > 3 && !(parts[3] > 0)) return null;
            return channels(raw);
          };
          const content = MARKERS[which]().getPopup().getElement().querySelector(".leaflet-popup-content");
          const out = [];
          // Every element carrying an INLINE color, which is every colour a builder resolved to a
          // string rather than leaving to the cascade.
          for (const el of content.querySelectorAll("[style*='color']")) {
            if (!el.style.color) continue;
            const box = el.getBoundingClientRect();
            if (box.width === 0 || box.height === 0) continue;
            const fg = channels(getComputedStyle(el).color);
            const cs = getComputedStyle(el);
            const px = parseFloat(cs.fontSize);
            const bold = parseInt(cs.fontWeight, 10) >= 700;
            // Its own fill if it has one, the popup's surface otherwise.
            const fill = ownFill(el);
            const base = fill || surface;
            out.push({
              tag: el.tagName.toLowerCase(),
              cls: String(el.className || ""),
              kind: fill ? "on its own fill" : "on the popup surface",
              text: (el.textContent || "").trim().split(/[ \\t\\n\\r]+/).join(" ").slice(0, 28),
              colour: cs.color,
              base: base ? "rgb(" + base.join(", ") + ")" : null,
              surface: surfaceText,
              ratio: fg && base ? +ratio(fg, base).toFixed(2) : null,
              need: px >= 24 || (bold && px >= 18.66) ? 3 : 4.5,
            });
          }
          return out;
        `),
        which,
      );
      for (const row of rows) measured.push({ ...row, where: `${which} @ ${theme}` });
      await closeAllPopups(page);
    }
  }

  /* NON-VACUITY FIRST, AND IN THREE WAYS, because this whole spec exists because something passed
     while measuring nothing. There must be inline colours at all; they must span more than one
     system, or a sweep that only ever found the subway is the gap again under a new name; and they
     must not all be one value, which is what measuring var(--ink) six times would look like. */
  expect(measured.length, "no popup printed an inline colour, so nothing was measured").toBeGreaterThan(10);
  /* EVERY RATIO IS A FINITE NUMBER, which is the assertion this spec's own first draft needed and
     did not have. A NaN ratio compares false against any threshold, so an unreadable measurement
     is indistinguishable from a passing one at the point of comparison; the only place to catch it
     is here, before the comparison. Measured: the first draft produced NaN for all 42 rows and
     reported success. */
  const unreadable = measured.filter((m) => typeof m.ratio !== "number" || !Number.isFinite(m.ratio));
  expect(unreadable, `a colour could not be measured at all: ${JSON.stringify(unreadable)}`).toEqual([]);
  // And the text really is text, which is how the NaN was spotted: the mangled regex had eaten
  // every letter "s" out of the labels ("Ea t River"), and the ratios were nonsense for the same
  // reason. A label that cannot survive normalisation is a reader that cannot be trusted.
  expect(
    measured.some((m) => /East River|Babylon|Corridor/.test(m.text)),
    `the labels came back mangled: ${JSON.stringify(measured.slice(0, 4).map((m) => m.text))}`,
  ).toBe(true);
  const systems = new Set(measured.map((m) => m.where.split(" @ ")[0]));
  expect(systems.size, `only ${[...systems]} carried an inline colour`).toBeGreaterThanOrEqual(6);
  expect(new Set(measured.map((m) => m.colour)).size, "every inline colour was the same value").toBeGreaterThan(4);
  // BOTH KINDS are in the sample, so a reader that had silently classified every node one way cannot
  // certify the other half. This is the distinction the first working draft of this reader got wrong.
  for (const kind of ["on the popup surface", "on its own fill"]) {
    expect(measured.some((m) => m.kind === kind), `nothing was measured ${kind}, so that half is uncertified`).toBe(
      true,
    );
  }

  const failures = measured.filter((m) => m.ratio < m.need);
  expect(failures, `a popup's own colour is below AA on the popup's surface: ${JSON.stringify(failures)}`).toEqual([]);
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
