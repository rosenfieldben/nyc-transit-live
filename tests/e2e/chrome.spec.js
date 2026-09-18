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
  await expect(page.locator(".railroad-marker")).toHaveCount(136);

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

    // Folded: the key and the strip are away, and the Key button says so.
    await expect(page.locator("#toggles")).toBeHidden();
    await expect(page.locator("#subway-key")).toBeHidden();
    await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "false");

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

test("D1g. the theme persists across a reload, and the page renders with storage empty", async ({ page }) => {
  /* BOTH DIRECTIONS OF MUTATION M5. A theme that is applied but not stored is forgotten on
     reload; a page that depends on the store renders half-styled when there is nothing in it,
     which is every first visit and every private window. */
  await open(page);
  const theme = () => page.locator("html").getAttribute("data-theme");
  const surface = () => page.evaluate(() => getComputedStyle(document.getElementById("panel")).backgroundColor);

  // With storage empty, the page comes up in the theme its own markup authors.
  expect(await page.evaluate(() => localStorage.getItem("nyc-transit-live.theme"))).toBeNull();
  expect(await theme()).toBe("light");
  const light = await surface();
  await expect(page.locator("#theme-toggle")).toHaveText("Dark");
  await expect(page.locator("#theme-toggle")).toHaveAttribute("aria-pressed", "false");

  await page.locator("#theme-toggle").click();
  expect(await theme()).toBe("dark");
  await expect(page.locator("#theme-toggle")).toHaveText("Light");
  await expect(page.locator("#theme-toggle")).toHaveAttribute("aria-pressed", "true");
  const dark = await surface();
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
  await page.locator("#theme-toggle").click();
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

  expect(await pressed(), "nothing is active until the rider asks for a view").toEqual([
    "view-city:false",
    "view-rail:false",
    "view-region:false",
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
  expect(await pressed()).toEqual(["view-city:false", "view-rail:false", "view-region:true"]);

  await page.locator("#view-city").click();
  await page.clock.runFor(1200);
  await expect.poll(async () => (await view()).zoom, { timeout: 5_000 }).toBe(13);
  expect(await view()).toEqual({ lat: 40.729, lng: -73.99, zoom: 13 });
  expect(await pressed()).toEqual(["view-city:true", "view-rail:false", "view-region:false"]);

  // THE RIDER MOVES, AND THE PRESET STOPS CLAIMING THE VIEW.
  await page.evaluate(() => map.panBy([160, 160], { animate: false }));
  await expect
    .poll(async () => (await pressed()).join(","), { timeout: 5_000 })
    .toBe("view-city:false,view-rail:false,view-region:false");
});
