/* MR1: the top bar's own claims.
   ==========================================================================

   What the redesign's stage 1 promises that no other spec in this suite was written to
   check. The pins next door (pins.spec.js) say what MR1 must NOT change; this file says
   what it must DO. Ids are D-for-design, in the two-letter-plus-number grammar
   tests/specids.js can collect, so a claim anywhere in the repo can cite one of them.

   WHAT IS DELIBERATELY NOT HERE, because it is already somewhere better:
   - the Key disclosure's focus and fold contract: mobile.spec.js A6c
   - the alerts row's geometry at phone widths: mobile.spec.js A6k and A6l
   - the 24px floor on every new control: layout.spec.js A4b
   - the focus ring on every new control: mobile.spec.js A6f
   - the Key panel's rows' legibility in both themes: a11y.spec.js A1x
   - that no marker or popup moved at all: pins.spec.js P1f through P1n */
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

async function open(page, before) {
  const ctx = await installMocks(page);
  if (before) await before(ctx);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await page.waitForFunction(() => typeof trains !== "undefined" && trains.size === 2 && stationRegistry.length === 14);
  await page.clock.runFor(1000);
  return ctx;
}

const PHONE = { width: 375, height: 667 };
const NARROW = { width: 320, height: 640 };

test("D1a. the feed strip is the design's eight feeds, with counts from the app's own registries", async ({
  page,
}) => {
  await open(page);

  // THE ORDER IS THE DESIGN'S (README section 1, row 2), read off the page rather than off
  // the table that built it.
  const strip = await page.evaluate(() =>
    [...document.querySelectorAll("#toggles .feed")].map((b) => ({
      id: b.id,
      name: b.querySelector(".feed-name").textContent,
      count: b.querySelector(".feed-count").textContent,
      dot: b.querySelector(".feed-dot").dataset.state,
      pressed: b.getAttribute("aria-pressed"),
      title: b.title,
    })),
  );
  expect(strip.map((f) => f.name)).toEqual([
    "Subway",
    "Buses",
    "LIRR",
    "Metro-North",
    "NJ Transit",
    "PATH",
    "Ferry",
    "AirTrain",
  ]);

  /* THE COUNTS ARE THE VEHICLE REGISTRIES', which is what makes this the status line moved
     rather than a second count that can drift from it. Compared against the registries in
     the same breath, so a strip that hard-coded the fixture's numbers fails. */
  const registries = await page.evaluate(() => ({
    subway: trains.size,
    buses: buses.size,
    lirr: [...railroads.keys()].filter((k) => k.startsWith("LIRR|")).length,
    mnr: [...railroads.keys()].filter((k) => k.startsWith("MNR|")).length,
    njt: njtTrainRecords.size,
    path: pathTrainRecords.size,
    ferry: ferryBoatRecords.size,
  }));
  for (const [key, expected] of Object.entries(registries)) {
    const feed = strip.find((f) => f.id === `toggle-${key}`);
    expect(feed.count, `${key}'s count`).toBe(String(expected));
  }
  // THE TWO RAILROADS ARE COUNTED APART, which the one "Railroads" checkbox could never do.
  expect(registries.lirr + registries.mnr, "the fixture serves one train per railroad").toBe(2);

  // AIRTRAIN SHOWS NO COUNT, because it has no vehicles: a 0 would be a claim about a fleet
  // that does not exist, and a station count would be a different number wearing the badge.
  const airtrain = strip.find((f) => f.id === "toggle-airtrain");
  expect(airtrain.count).toBe("");
  expect(airtrain.dot, "and no realtime feed behind it, so its dot is the scheduled-only one").toBe("scheduled");
  expect(airtrain.title).toBe("Scheduled · hide AirTrain");

  // A healthy feed's dot is green and its tooltip says so, in the design's words.
  const subway = strip.find((f) => f.id === "toggle-subway");
  expect(subway.dot).toBe("live");
  expect(subway.title).toMatch(/^Live · \d+s · hide Subway$/);
});

test("D1b. a stale feed's dot and tooltip follow the feed, not the observations inside it", async ({ page }) => {
  /* The v3 brief, in as many words: the dot "reflects the feed, not the observations inside
     it; a feed can be green while a third of its trains are dimmed, and that is correct".
     Metro-North's poll is aged here and LIRR's is not, which is the split the one "Railroads"
     control could not draw and the split the C6 contract specs exist for. */
  await open(page, (ctx) => {
    ctx.overrides.railroads = (route, fixtures) =>
      json(route, fixtures.railroadsWithSystems({ mnrAt: fx.FROZEN_S - 360 }));
  });

  const dot = (id) => page.evaluate((sel) => document.querySelector(sel).querySelector(".feed-dot").dataset.state, id);
  const title = (id) => page.locator(id).getAttribute("title");

  expect(await dot("#toggle-mnr"), "Metro-North's poll is six minutes old").toBe("stale");
  expect(await title("#toggle-mnr")).toBe("As of 6m ago · hide Metro-North");
  expect(await dot("#toggle-lirr"), "and LIRR's is not, in the same source").toBe("live");
  expect(await title("#toggle-lirr")).toMatch(/^Live · \d+s · hide LIRR$/);
});

test("D1c. toggling a feed flips aria-pressed AND the visible OFF treatment AND the layer", async ({ page }) => {
  /* THE THREE MOVE TOGETHER OR THE CONTROL IS LYING, and each of the three is a mutation:
     M1 stops writing aria-pressed, M2 leaves only the fade, and a toggle that changed neither
     would leave the layer showing. The v3 brief is explicit that the fade alone is not state
     ("hidden state must not be conveyed by opacity alone"), so the strike and the word are
     both asserted as DRAWN rather than as declared. */
  await open(page);
  const button = page.locator("#toggle-ferry");
  const read = () =>
    page.evaluate(() => {
      const el = document.getElementById("toggle-ferry");
      const name = el.querySelector(".feed-name");
      const mark = el.querySelector(".feed-offmark");
      return {
        pressed: el.getAttribute("aria-pressed"),
        strike: getComputedStyle(name).textDecorationLine,
        offMarkShown: getComputedStyle(mark).display !== "none",
        layerShowing: map.hasLayer(ferryBoats) && map.hasLayer(ferryDocks) && map.hasLayer(ferryRouteLines),
        boats: document.querySelectorAll(".ferry-marker").length,
      };
    });

  expect(await read()).toEqual({
    pressed: "true",
    strike: "none",
    offMarkShown: false,
    layerShowing: true,
    boats: 3,
  });

  await button.click();
  expect(await read()).toEqual({
    pressed: "false",
    strike: "line-through",
    offMarkShown: true,
    layerShowing: false,
    boats: 0,
  });
  // And the tooltip now offers the action the button performs, which is the opposite one.
  expect(await button.getAttribute("title")).toMatch(/ · show Ferry$/);

  await button.click();
  expect(await read()).toEqual({
    pressed: "true",
    strike: "none",
    offMarkShown: false,
    layerShowing: true,
    boats: 3,
  });
  expect(await button.getAttribute("title")).toMatch(/ · hide Ferry$/);
});

test("D1d. the trailing note is staleness()'s text, character for character", async ({ page }) => {
  /* THE PIN NEXT DOOR SAYS THE WORDS ARE UNCHANGED; THIS SAYS THE NOTE IS EXACTLY THEM.
     P1b holds the same sentence as the status line's tail in the F01 world, before and after
     the stage. Here the note must EQUAL it: not contain it, not summarise it, not re-derive a
     subset of it. The withheld count and Metro-North's undated clause are the two that would
     go missing first if anything re-derived this from the freshness index (mutation M4),
     because both RIDE a raised line rather than raising one. */
  await open(page, (ctx) => {
    ctx.overrides.railroads = (route) => {
      const body = JSON.parse(JSON.stringify(require("./fixtures/f01_railroads.json")));
      body.systems.MNR.fetched_at = fx.FROZEN_S - 360;
      return json(route, body);
    };
  });
  await expect(page.locator(".rail-lirr, .rail-mnr")).toHaveCount(136);

  const note = page.locator("#status");
  await expect(note).toHaveText(
    "railroad: MNR as of 6m ago; LIRR 24 trains not shown, last seen over 10m ago; " +
      "MNR position age unavailable",
  );
  await expect(note, "and it is painted as trouble, which is what setStatus's flag is for").toHaveClass(/error/);
  // AND NOTHING ELSE IS IN IT. The counts moved to the buttons and the clock to row 1, so a
  // note that still carried either would be saying the same thing twice on one line.
  await expect(note).not.toContainText("updated");
  await expect(note).not.toContainText(" · ");
});

test("D1e. a healthy day's note is empty, and stays empty across polls", async ({ page }) => {
  // The v3 brief singles this out: "nothing at all on a healthy day, which is the common case
  // and must not get noisier". Across two polls, because a note that only starts empty and
  // then finds something to say is the same defect one tick later.
  await open(page);
  const note = page.locator("#status");
  await expect(note).toHaveText("");
  await expect(note).not.toHaveClass(/error/);
  await page.clock.runFor(15_000 * 2 + 1000);
  await expect(note).toHaveText("");
  await expect(note).not.toHaveClass(/error/);
  // The clock is what repaints on a healthy page, so the page was alive for those two polls.
  await expect(page.locator("#clock-time")).not.toHaveText("");
});

for (const [label, viewport] of [
  ["375", PHONE],
  ["320", NARROW],
]) {
  test(`D1f. the alerts strip is on screen at ${label} with the key folded`, async ({ page }) => {
    /* v3.1 POINT 1, WHICH IS THE ONE THING THE FOLD MAY NOT TAKE. Below 700px the subway key
       and the feed strip fold behind the Key button; the service alerts never do. Asserted in
       the folded state, which is the page's default at these widths and the state a rider on a
       phone is actually in when an incident starts. */
    await page.setViewportSize(viewport);
    await open(page, (ctx) => {
      ctx.overrides.alerts = (route, fixtures) =>
        json(route, {
          ...fixtures.alerts(),
          alerts: [
            {
              id: "fold-1",
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
          ],
        });
    });

    // Folded: the eight feed buttons and the subway key are away, and the Key button says so.
    await expect(page.locator("#feed-buttons")).toBeHidden();
    await expect(page.locator("#subway-key")).toBeHidden();
    await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "false");
    // AND THE NOTE IS NOT FOLDED WITH THEM (round 3, by ruling): it is the second carve-out
    // beside this strip, and the two of them are the surfaces that speak only when something
    // is wrong. Asserted as the absence of the fold class, because on a healthy page the note
    // is empty and an empty note draws nothing.
    expect(
      await page.evaluate(() => document.getElementById("status").closest(".hdr-fold") !== null),
      "the trailing note must never be inside the fold",
    ).toBe(false);

    // AND THE ALERT IS THERE ANYWAY: visible, readable, and dismissible.
    await expect(page.locator(".alert-banner-row")).toHaveText(
      "Reduced service systemwide while crews clear a disabled train",
    );
    await expect(page.locator("#alert-banner-dismiss")).toBeVisible();
    const box = await page.locator("#alert-banner").boundingBox();
    expect(box.height, "the strip is drawn, not merely present").toBeGreaterThan(0);
    expect(box.y + box.height, "and it is inside the viewport").toBeLessThanOrEqual(viewport.height);
  });
}

// The theme, driven the way the app drives it. THE BUTTON IS HIDDEN UNTIL MR4 BY RULING
// (index.html says why: the dark theme is chrome, and the MARKS it has to sit behind arrive
// with MR2 through MR4), so these specs go through applyTheme() and the toggle's own click
// handler rather than through a control a rider cannot reach. What is being tested is the
// machinery, which ships now; what is withheld is the way in.
const pressTheme = (page) => page.evaluate(() => document.getElementById("theme-toggle").click());

test("D1g. the theme persists across a reload, and the page renders with storage empty", async ({ page }) => {
  /* BOTH DIRECTIONS OF MUTATION M5. A theme that is applied but not stored is forgotten on
     reload; a page that depends on the store renders half-styled when there is nothing in it,
     which is every first visit and every private window. */
  await open(page);

  /* MR4 PUT THE CONTROL IN REACH, which is ruling R2's condition met: the toggle shipped
     hidden in MR1 because every mark on the map read below the 3:1 floor against the dark
     basemap, and MR2, MR3 and MR4 are the stages that gave each of them its paper casing or
     stroke. The assertion is INVERTED rather than deleted: "a rider can press this" is the
     invariant now, and a later stage that re-hid it should fail here.

     THE SPECS BELOW ALREADY DROVE THE BUTTON, not applyTheme, so nothing else in this test
     changes: pressTheme clicks it, and it was reachable to Playwright while hidden. */
  await expect(page.locator("#theme-toggle")).toBeVisible();
  const theme = () => page.locator("html").getAttribute("data-theme");
  const surface = () => page.evaluate(() => getComputedStyle(document.getElementById("panel")).backgroundColor);

  // With storage empty, the page comes up in the theme its own markup authors.
  expect(await page.evaluate(() => localStorage.getItem("nyc-transit-live.theme"))).toBeNull();
  expect(await theme()).toBe("light");
  const light = await surface();
  await expect(page.locator("#theme-toggle")).toHaveText("Dark");

  await pressTheme(page);
  expect(await theme()).toBe("dark");
  await expect(page.locator("#theme-toggle")).toHaveText("Light");
  const dark = await surface();

  /* THE LABEL IS THE WHOLE ANSWER, AND NOTHING CONTRADICTS IT (round 2). This carried
     aria-pressed too, and the pair said opposite things out loud: in the dark theme the
     button reads "Light" and reported pressed, so a screen reader said "Light, pressed",
     which states that the light theme is on while the page is dark. aria-pressed belongs to
     a toggle whose label does NOT move with the state, and the design's label is the action.
     Asserted as an absence, because the defect was an extra claim rather than a missing one. */
  expect(
    await page.locator("#theme-toggle").getAttribute("aria-pressed"),
    "the label is the action, so nothing may also claim a state",
  ).toBeNull();
  expect(dark, "the two themes must actually paint differently").not.toBe(light);
  expect(await page.evaluate(() => localStorage.getItem("nyc-transit-live.theme"))).toBe("dark");

  // ACROSS A RELOAD, which is the whole claim.
  await page.reload();
  await page.waitForFunction(() => typeof trains !== "undefined");
  expect(await theme()).toBe("dark");
  expect(await surface()).toBe(dark);
  await expect(page.locator("#theme-toggle")).toHaveText("Light");

  // AND AN UNRECOGNISED STORED VALUE IS NO VALUE, rather than something that reaches the root
  // attribute and matches neither token block.
  await page.evaluate(() => localStorage.setItem("nyc-transit-live.theme", "Dark"));
  await page.reload();
  await page.waitForFunction(() => typeof trains !== "undefined");
  expect(await theme()).toBe("light");
  expect(await surface()).toBe(light);
});

test("D1h. the theme survives a browser that refuses localStorage", async ({ page }) => {
  /* The read AND the write are guarded, and the reason is not politeness: localStorage
     THROWS rather than returning null in a private window with site data blocked, and an
     uncaught throw at that point in systems/shared.js takes the file down and with it every
     script after it, which is the whole page. Simulated by making both accessors throw. */
  await page.addInitScript(() => {
    const boom = () => {
      throw new DOMException("The operation is insecure.", "SecurityError");
    };
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get: () => ({ getItem: boom, setItem: boom, removeItem: boom }),
    });
  });
  await open(page);

  // The page is alive: the markers loaded, which means shared.js finished and every file
  // after it ran.
  await expect(page.locator(".train-marker")).toHaveCount(2);
  expect(await page.locator("html").getAttribute("data-theme")).toBe("light");

  // And the toggle still works for this session, it just will not be remembered.
  await pressTheme(page);
  expect(await page.locator("html").getAttribute("data-theme")).toBe("dark");
  await expect(page.locator(".train-marker")).toHaveCount(2);
});

test("D1i. Archivo is served from this origin, and the page asks nobody else for type", async ({ page }) => {
  /* THE REASON THE FONT IS IN THE REPOSITORY. The CSP is `default-src 'self'` and font-src
     falls back to it, so a Google Fonts link and the fonts.gstatic.com files behind it are
     both refused by the browser; self-hosting is what keeps that CSP byte-identical. The
     hermetic harness makes the second half checkable for free: mock.js aborts and RECORDS
     every request that is not to this origin, so a stylesheet that reached for a CDN shows
     up as a leak rather than as a silent fallback to system-ui. */
  const ctx = await open(page);
  expect(ctx.leaks, "the page must make no third-party request at all").toEqual([]);

  const font = await page.evaluate(async () => {
    await document.fonts.ready;
    return {
      // The real question: can the browser lay out the weights the design uses?
      regular: document.fonts.check('400 12px "Archivo"'),
      semibold: document.fonts.check('600 10px "Archivo"'),
      extrabold: document.fonts.check('800 15px "Archivo"'),
      // And is it what the chrome is actually set in?
      brand: getComputedStyle(document.getElementById("brand")).fontFamily,
      loaded: [...document.fonts].map((f) => `${f.family} ${f.weight} ${f.status}`),
    };
  });
  expect(font.regular, `400 must be available (loaded: ${font.loaded.join(", ")})`).toBe(true);
  expect(font.semibold, "600 must be available").toBe(true);
  expect(font.extrabold, "800 must be available").toBe(true);
  expect(font.brand, "and the chrome must be set in it").toContain("Archivo");

  // Served from here, with a type the browser will accept under nosniff.
  const response = await page.request.get("/fonts/archivo-latin.woff2");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("font/woff2");
});

test("D1j. the view presets fly the map and stand down when the rider takes over", async ({ page }) => {
  /* The design's three centres and zooms (README section 3), and the one thing it does not
     say: the active preset has to CLEAR when the rider moves away, or a highlighted "City"
     sits over a map showing New Jersey. */
  await open(page);
  const pressed = () =>
    page.evaluate(() =>
      [...document.querySelectorAll("#view-stack button")].map((b) => `${b.id}:${b.getAttribute("aria-pressed")}`),
    );
  const view = () =>
    page.evaluate(() => {
      const c = map.getCenter();
      return { lat: +c.lat.toFixed(3), lng: +c.lng.toFixed(3), zoom: map.getZoom() };
    });

  /* MR2 ADDED A FOURTH BUTTON TO THIS STACK, and it is in these lists deliberately rather
     than filtered out: the Names toggle is a #view-stack button, it carries aria-pressed for
     the same reason the presets do, and a list that quietly excluded it would stop noticing
     if it lost its state. It starts PRESSED because the station names start shown, and since
     follow-up 1's landing ruling City starts pressed for the same reason: the map LANDS at the
     City preset, so "the map is here now" is true at load for City and for nothing else.
     BEFORE THE RULING this line read all three presets false ("nothing is active until the rider
     asks for a view"), because the map opened at a zoom of its own; the ledger keeps that as the
     before. buszoom.spec.js D7i is the ruling's own test. */
  expect(await pressed(), "the map lands at the City preset, and only City says so").toEqual([
    "view-city:true",
    "view-rail:false",
    "view-region:false",
    "names-toggle:true",
  ]);

  /* THE CLOCK IS DRIVEN, BECAUSE THE PRESET FLIES. flyTo is a 0.8s animation and this suite
     pauses the clock, so the fly starts and never lands unless the time it needs is given to
     it. Driving it rather than switching to a running clock keeps this spec deterministic and
     keeps the animation itself in the test: a preset that jumped instead of flying would be
     a design change nobody asked for. */
  await page.locator("#view-region").click();
  await page.clock.runFor(1200);
  await expect.poll(async () => (await view()).zoom, { timeout: 5_000 }).toBe(10);
  expect(await view()).toEqual({ lat: 40.79, lng: -73.9, zoom: 10 });
  expect(await pressed()).toEqual(["view-city:false", "view-rail:false", "view-region:true", "names-toggle:true"]);

  await page.locator("#view-city").click();
  await page.clock.runFor(1200);
  await expect.poll(async () => (await view()).zoom, { timeout: 5_000 }).toBe(13);
  expect(await view()).toEqual({ lat: 40.729, lng: -73.99, zoom: 13 });
  expect(await pressed()).toEqual(["view-city:true", "view-rail:false", "view-region:false", "names-toggle:true"]);

  // THE RIDER MOVES, AND THE PRESET STOPS CLAIMING THE VIEW.
  await page.evaluate(() => map.panBy([160, 160], { animate: false }));
  await expect
    .poll(async () => (await pressed()).join(","), { timeout: 5_000 })
    .toBe("view-city:false,view-rail:false,view-region:false,names-toggle:true");
});

for (const [label, viewport] of [
  ["375", PHONE],
  ["320", NARROW],
]) {
  test(`D1k. the trailing note is readable at ${label} with the key folded`, async ({ page }) => {
    /* THE POSITIVE HALF OF RULING 3, and the reason the carve-out exists. D1f says the note is
       not inside the fold; this says what that buys a rider: on the day something is wrong,
       on the smallest screen, with the key folded and the eight feed buttons away, the note
       is on the page and legible without a tap. It carries staleness()'s whole sentence, not
       a truncation, which is the never-truncate rule made visual at a width where truncating
       would be the tempting thing to do. */
    await page.setViewportSize(viewport);
    await open(page, (ctx) => {
      ctx.overrides.railroads = (route, fixtures) =>
        json(route, fixtures.railroadsWithSystems({ mnrAt: fx.FROZEN_S - 360 }));
    });

    await expect(page.locator("#feed-buttons"), "the buttons are folded at this width").toBeHidden();
    await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "false");

    const note = page.locator("#status");
    await expect(note).toBeVisible();
    // The whole sentence, including the undated clause that RIDES it: Metro-North dates none
    // of its positions on any day, so that clause is there whenever the railroad line is
    // raised, and a note that dropped it at a narrow width would be truncating by another name.
    await expect(note).toHaveText("railroad: MNR as of 6m ago; MNR position age unavailable");
    await expect(note).toHaveClass(/error/);

    const box = await note.boundingBox();
    expect(box.width, "the note is drawn").toBeGreaterThan(0);
    expect(box.x + box.width, "and it fits the screen rather than running off it").toBeLessThanOrEqual(
      viewport.width + 1,
    );
    // WHOLE, NOT CLIPPED. A note that wrapped is fine and a note the box cut off is not, so
    // the element's scroll width must not exceed what is drawn.
    const clipped = await note.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
    expect(clipped, "the note wraps rather than truncating").toBe(false);
  });
}

test("D1l. the subway key is the app's own mark, not the MTA's roundel", async ({ page }) => {
  /* BY RULING, and the ruling is this repository's own: "The MTA's logos, official map, and
     route symbols require a license. Use your own colors and markers rather than official MTA
     branding" (README, Notes). The handoff draws these as circles, which is the route symbol;
     the colours were already the app's, because lineColor() exists for the same reason, and
     the shape now follows it. The radius is the subway marker's own, so the key and the map
     are the same mark. */
  await open(page);
  const bullets = await page.evaluate(() =>
    [...document.querySelectorAll("#subway-key .bul")].map((b) => ({
      route: b.textContent,
      radius: getComputedStyle(b).borderTopLeftRadius,
      background: getComputedStyle(b).backgroundColor,
    })),
  );
  /* THE COUNT IS NO LONGER A LITERAL, and that is MR2 round 2's correction. MR1 wrote ten
     trunks and twenty-three bullets into a table, and measured against the real static
     archive the table and the network disagreed in both directions: three of its bullets
     drew nothing and four drawn routes had no bullet. The key is derived from the loaded
     route list now, so the honest claim here is that it carries a bullet for what the world
     it booted into can draw, which in the stock fixture is the two routes it serves. The
     derivation itself is subway.spec.js D2a and the node tier. */
  const drawable = await page.evaluate(() => subwayRouteUniverse(subwayRouteList(), subwayTrainRoutes()));
  expect(bullets.map((b) => b.route).sort(), "the key carries the routes the app can draw").toEqual(
    [...drawable].sort(),
  );
  expect(bullets.length, "and the scan must find bullets, or it decides nothing").toBeGreaterThan(0);
  for (const b of bullets) {
    expect(b.radius, `${b.route} must not be a circle`).not.toBe("50%");
    expect(parseFloat(b.radius), `${b.route}'s radius`).toBeLessThan(11);
    expect(parseFloat(b.radius), `${b.route}'s radius`).toBeGreaterThan(0);
  }
  // AND THE COLOURS ARE THE APP'S, read back against lineColor() itself rather than against a
  // table copied into this spec.
  const agree = await page.evaluate(() =>
    [...document.querySelectorAll("#subway-key .bul")].every((b) => {
      // An ALIAS bullet's text is not a feed route id (S stands for GS, FS and H), so the
      // colour it owes is its first target's. bulletRouteIds is the one place that knows.
      const want = lineColor(bulletRouteIds(b.textContent)[0]);
      const el = document.createElement("span");
      el.style.color = want;
      document.body.append(el);
      const normalised = getComputedStyle(el).color;
      el.remove();
      return getComputedStyle(b).backgroundColor === normalised;
    }),
  );
  expect(agree, "every bullet is lineColor()'s answer for its route").toBe(true);
});

