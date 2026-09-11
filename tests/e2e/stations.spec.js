// A1: the accessible station surface, in a browser.
//
// THE SIGNATURE SPEC OF THE ACCESSIBILITY ARC is "keyboard only, end to end":
// there is not a single mouse event in it. Everything else here pins a specific
// promise the panel makes, and two of them pin focus behavior that is invisible
// until it breaks and miserable when it does.
//
// Same hermetic harness as smoke.spec.js: mock.js intercepts every /api/* request
// and the basemap tiles, so nothing leaves the machine.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

// The dock breakpoint from style.css / stations.js. Desktop Chrome (1280) sits
// above it, which is why the legacy specs now run with the panel open.
const DOCK_MIN_WIDTH = 1100;

// Wait for the station registry to fill. The loaders resolve asynchronously, so a
// spec that types immediately can race an empty registry; this waits on the app's
// own state rather than on a sleep.
async function awaitRegistry(page, atLeast = 6) {
  await expect
    .poll(async () => page.evaluate(() => (typeof stationRegistry === "undefined" ? 0 : stationRegistry.length)), {
      timeout: 15_000,
    })
    .toBeGreaterThanOrEqual(atLeast);
}

// THE FROZEN CLOCK IS NOT OPTIONAL, and leaving it out was the first thing these
// specs caught. Every fixture timestamp is relative to fx.FROZEN_S, and the app
// calibrates its clock-skew offset from the vehicle feeds' served_at, so against a
// real wall clock the offset becomes however far the present is from FROZEN (weeks)
// and every age and countdown in the panel is computed against a clock that far
// off. Installing and pausing the clock at FROZEN, exactly as smoke.spec.js does,
// makes the offset zero and the countdowns exactly (arrival - FROZEN_S). Any
// timestamp a spec invents must therefore be expressed in FIXTURE time too.
async function open(page, { install = true } = {}) {
  const ctx = install ? await installMocks(page) : null;
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await awaitRegistry(page);
  return ctx;
}

// What has focus, as something a failure message can show: the id when there is
// one, else the tag, and the literal string "BODY" for the stranded case, which is
// the whole point of asserting on it.
function activeDescriptor(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return "BODY";
    return el.id ? `#${el.id}` : el.tagName;
  });
}

test("A1a. keyboard only: skip link, search, select, arrivals, Escape", async ({ page }) => {
  // ZERO MOUSE EVENTS. Every interaction below is a key press, because the claim
  // is that a rider who cannot use a pointer gets the whole surface.
  await open(page);

  // The skip link is the FIRST thing Tab reaches on the page.
  await page.keyboard.press("Tab");
  expect(await activeDescriptor(page)).toBe("#stations-skip");

  // Activating it lands in the panel. The panel is already docked open at this
  // viewport, so the link's job here is to move focus past the map to the list.
  await page.keyboard.press("Enter");
  await expect(page.locator("#stations-panel")).toBeVisible();

  // Type into the search box. Focus it the way a keyboard user would, by tabbing
  // from the skip target rather than by clicking.
  await page.locator("#stations-search").focus();
  await page.keyboard.type("times");
  const rows = page.locator("#stations-results button.station-row");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Times Sq-42 St");
  await expect(rows.first()).toContainText("Subway");

  // Tab from the search box reaches the result row, and Enter activates it. Native
  // button semantics are what make this work without any custom key handling.
  await page.keyboard.press("Tab");
  expect(await activeDescriptor(page)).toBe("BUTTON");
  await page.keyboard.press("Enter");

  // The arrivals render as TEXT, from the stubbed payload, with real structure.
  const detail = page.locator("#stations-detail");
  await expect(detail.locator("h3")).toContainText("Times Sq-42 St");
  await expect(detail.locator("h4").first()).toHaveText("Northbound");
  // The sentence shape: route, noun, spoken countdown, clock time. "2 min" in the
  // popup is "in 2 minutes" here, because a screen reader reads "min" as "min".
  // Exact, because the clock is frozen at FROZEN and the fixture's first Northbound
  // arrival is FROZEN_S + 90: the popup renders "2 min" and the panel speaks the
  // same decision as "in 2 minutes". The two wordings sharing countdownParts is
  // what makes both of those true of the same instant.
  await expect(detail.locator("ul.station-arrivals li").first()).toHaveText(
    /^1 train in 2 minutes, \d+:\d\d (AM|PM) arrival$/,
  );

  // Escape closes and returns focus to the toggle.
  await page.keyboard.press("Escape");
  await expect(page.locator("#stations-panel")).toBeHidden();
  expect(await activeDescriptor(page)).toBe("#stations-toggle");
});

test("A1b. closing never strands focus on the body, on any closing path", async ({ page }) => {
  // REQUIRED ASSERTION (b). Hiding a subtree that contains the focused element
  // drops focus onto document.body, where the next Tab restarts at the top of the
  // page and a screen reader announces nothing. The panel moves focus out BEFORE
  // hiding; this checks every path that closes it, and checks the negative
  // explicitly, because "not body" is the failure everyone ships by accident.
  await open(page);

  // Path 1: Escape with focus in the SEARCH box.
  await page.locator("#stations-search").focus();
  await page.keyboard.press("Escape");
  expect(await activeDescriptor(page)).toBe("#stations-toggle");
  expect(await activeDescriptor(page)).not.toBe("BODY");

  // Path 2: Escape with focus on a RESULT ROW, which is deeper in the subtree.
  await page.locator("#stations-toggle").press("Enter");
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().focus();
  await page.keyboard.press("Escape");
  expect(await activeDescriptor(page)).toBe("#stations-toggle");

  // Path 3: the TOGGLE itself, which closes from outside the panel.
  await page.locator("#stations-toggle").press("Enter");
  await expect(page.locator("#stations-panel")).toBeVisible();
  await page.locator("#stations-toggle").press("Enter");
  await expect(page.locator("#stations-panel")).toBeHidden();
  expect(await activeDescriptor(page)).toBe("#stations-toggle");

  // Path 4: ESCAPE DURING MAP SYNC, the path the review singled out. A station is
  // selected (which pans the map and opens a Leaflet popup, the one interaction
  // with two focus authorities) and Escape follows immediately.
  await page.locator("#stations-toggle").press("Enter");
  await page.locator("#stations-search").fill("times");
  const row = page.locator("#stations-results button.station-row").first();
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#stations-detail h3")).toContainText("Times Sq-42 St");
  await page.keyboard.press("Escape");
  await expect(page.locator("#stations-panel")).toBeHidden();
  expect(await activeDescriptor(page)).toBe("#stations-toggle");
});

test("A1c. map sync pans and opens the popup, and never steals focus", async ({ page }) => {
  // REQUIRED ASSERTION (a). Leaflet moves focus into a popup when it opens one,
  // which would drag a rider out of the panel mid-search and leave Tab resuming
  // from the map. Selection captures and restores the focused element around the
  // transition; this proves the rider is still where they were.
  await open(page);
  await page.locator("#stations-search").fill("times");
  const row = page.locator("#stations-results button.station-row").first();
  await row.focus();
  expect(await activeDescriptor(page)).toBe("BUTTON");

  await page.keyboard.press("Enter");

  // BOTH SURFACES show the same station: the panel heading and the Leaflet popup.
  await expect(page.locator("#stations-detail h3")).toContainText("Times Sq-42 St");
  await expect(page.locator(".leaflet-popup-content")).toContainText("Times Sq-42 St");

  // And focus is still on the row the rider activated, not in the popup and not on
  // the body. Polled rather than asserted once, because Leaflet's focus move
  // happens during the popup open and a single immediate check could pass before
  // it ever occurred.
  await expect
    .poll(async () => activeDescriptor(page), { timeout: 3000 })
    .toBe("BUTTON");
  expect(await activeDescriptor(page)).not.toBe("BODY");
});

test("A1d. the docked default: open at desktop width, closed below the breakpoint", async ({
  page,
}) => {
  // Pins the placement decision in both directions, so the docked-open desktop
  // page the legacy specs now run against is a tested default rather than an
  // incidental one.
  await open(page);
  await expect(page.locator("#stations-panel")).toBeVisible();
  await expect(page.locator("#stations-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("body")).toHaveClass(/stations-docked/);
  // Docking must not have taken focus: the page loads with focus nowhere, and a
  // panel that grabbed it on load would be disorienting and would defeat the skip
  // link.
  expect(await activeDescriptor(page)).toBe("BODY");

  // Below the breakpoint it loads CLOSED: the map greets, and the panel is one tap
  // or one skip link away.
  await page.setViewportSize({ width: DOCK_MIN_WIDTH - 200, height: 720 });
  await installMocks(page);
  await page.goto("/");
  await awaitRegistry(page);
  await expect(page.locator("#stations-panel")).toBeHidden();
  await expect(page.locator("#stations-toggle")).toHaveAttribute("aria-expanded", "false");
});

test("A1e. empty query prompts, no match says so, and results are capped honestly", async ({
  page,
}) => {
  await open(page);
  const status = page.locator("#stations-status");
  const rows = page.locator("#stations-results button.station-row");

  // An empty query is a question nobody asked; 900 rows is a hostile answer to it.
  await expect(status).toContainText("Type a station name");
  await expect(rows).toHaveCount(0);

  // No match is an honest zero, not an empty list with no explanation.
  await page.locator("#stations-search").fill("zzzznotastation");
  await expect(status).toContainText("No stations match");
  await expect(rows).toHaveCount(0);

  // A match states the count. The fixtures are small, so the cap is exercised in
  // the node tests (searchStations); what matters here is that the count the panel
  // shows comes from the same helper and is rendered for everyone to read.
  await page.locator("#stations-search").fill("st");
  await expect(status).toContainText(/of \d+ stations?/);
  expect(await rows.count()).toBeGreaterThan(0);
});

test("A1f. stale and warming arrivals render the honest text the popups earned", async ({
  page,
}) => {
  const ctx = await installMocks(page);
  // WARMING: the backend answers 503 with a detail line while its cache fills. The
  // panel shows that line rather than inventing a message.
  ctx.overrides.subwayArrivals = (route) =>
    json(route, { detail: "Arrivals cache is warming up; try again in a few seconds." }, 503);
  await open(page, { install: false });
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator("#stations-detail")).toContainText("warming up");

  // STALE: a payload whose fetched_at is well past the staleness threshold gets the
  // same "as of Xm ago" line the popups show, from the same helper and threshold.
  // IN FIXTURE TIME, not wall-clock time: the page's clock is frozen at FROZEN, so
  // a real Date.now() here would be weeks in the future and the age would come out
  // negative rather than stale.
  ctx.overrides.subwayArrivals = (route, fixtures) => {
    const body = fixtures.subwayArrivals();
    return json(route, { ...body, fetched_at: fx.FROZEN_S - 600 });
  };
  await page.locator("#stations-search").fill("canal");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator(".station-detail-stale")).toContainText(/as of \d+m ago/);
});

test("A1g. a ferry dock announces its accessibility in words, not as a glyph alone", async ({
  page,
}) => {
  // Ferry is the only system whose stops carry wheelchair_boarding, so it is the
  // only place the indicator appears. The glyph is aria-hidden and the words live
  // in a visually-hidden span, so the row reads as "Wall St/Pier 11, Ferry,
  // wheelchair accessible" rather than announcing a symbol name or nothing.
  await open(page);
  await page.locator("#stations-search").fill("wall");
  const row = page.locator("#stations-results button.station-row").first();
  await expect(row).toContainText("Wall St/Pier 11");
  await expect(row).toContainText("wheelchair accessible");
  await expect(row.locator(".station-row-access")).toHaveAttribute("aria-hidden", "true");

  // The dock that is NOT accessible says nothing at all, rather than implying it.
  await page.locator("#stations-search").fill("williamsburg");
  await expect(page.locator("#stations-results button.station-row").first()).not.toContainText(
    "wheelchair",
  );
});

test("A1h. AirTrain renders scheduled headways, labeled as scheduled", async ({ page }) => {
  // AirTrain publishes no realtime feed. The panel takes the feedless branch and
  // says so, rather than counting down to a time nobody promised. Per the review
  // ruling this is system-shape honesty, and it is the branch any future feedless
  // system takes.
  await open(page);
  // Federal Circle is the AirTrain fixture's station B, and the only station BOTH
  // branches serve, so this also pins that the detail lists every serving branch
  // rather than the first one it finds. "Howard Beach" is a route name in the
  // fixture, not a station name, which is why searching for it finds nothing.
  await page.locator("#stations-search").fill("federal");
  const rows = page.locator("#stations-results button.station-row");
  await expect(rows).toHaveCount(1);
  await rows.first().press("Enter");
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("Scheduled service");
  await expect(detail).toContainText("no live tracking");
  // The frozen clock is 12:00Z, which is 08:00 America/New_York in July, so the
  // 06:00-11:00 band applies: seven minutes, with the word "scheduled" on the row
  // itself rather than carried only by the heading above it.
  const branches = detail.locator("li");
  await expect(branches).toHaveText([
    "Jamaica: a train about every 7 minutes, scheduled",
    "Howard Beach: a train about every 7 minutes, scheduled",
  ]);
  // A schedule cannot go stale, and there is no feed here for staleness to measure.
  await expect(detail.locator(".station-detail-stale")).toHaveCount(0);

  // AND IT IS SPOKEN, NOT ONLY DRAWN. The review found this branch rendering its
  // text in total silence: focus stays on the result row, the detail is elsewhere in
  // the DOM, so a rider using a screen reader pressed Enter and heard nothing at all.
  // Live stations spoke and feedless ones did not, which is backwards.
  await expect(page.locator("#stations-announce")).toContainText("Scheduled service");
  await expect(page.locator("#stations-announce")).toContainText("every 7 minutes");
});

test("A1m. reopening the panel never presents the old arrivals as current", async ({ page }) => {
  // THE DEFECT THE REVIEW FOUND. Closing stops the tick but leaves the rendered
  // arrivals in the DOM, so before the fix, closing the panel, waiting ten minutes,
  // and reopening it showed byte-identical text: "1 train in 2 minutes, 8:01 AM
  // arrival" for a train that had left eight minutes earlier, with no staleness line.
  // The countdown and the clock time agreed with each other, so nothing in the text
  // gave it away.
  await open(page);
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("in 2 minutes");
  const before = await detail.innerText();

  await page.keyboard.press("Escape");
  await expect(page.locator("#stations-panel")).toBeHidden();
  // Ten minutes pass with the panel closed. The arrivals fixture is fixed, so on
  // reopen the SAME payload is now ten minutes old: that is the point.
  await page.clock.fastForward(600_000);
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  // The reopened panel is honest about age, and the departed train is no longer
  // counting down to a time that has passed.
  await expect(detail.locator(".station-detail-stale")).toContainText(/as of \d+m ago/);
  const after = await detail.innerText();
  expect(after, "the detail must not be the pre-close text verbatim").not.toBe(before);
  expect(after).not.toContain("in 2 minutes");
});

test("A1n. a first-load arrivals failure is spoken, not only drawn", async ({ page }) => {
  // The other half of the same defect: the error branch returned before reaching the
  // live region, so the one moment a rider most needs to be told something was the
  // one moment the panel said nothing.
  const ctx = await installMocks(page);
  ctx.overrides.subwayArrivals = (route) =>
    json(route, { detail: "Arrivals cache is warming up; try again in a few seconds." }, 503);
  await open(page, { install: false });
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator("#stations-detail")).toContainText("warming up");
  await expect(page.locator("#stations-announce")).toContainText("warming up");
  await expect(page.locator("#stations-announce")).toContainText("Times Sq");
});

test("A1p. switching to a feedless station stops the tick, so no tick can speak", async ({ page }) => {
  // ROUND 2 OF THE REVIEW FOUND THIS, and it broke the one hard rule of the phase.
  // Only fetchPanelArrivals used to stop the countdown interval, and a feedless
  // station never calls it, so selecting AirTrain after a subway station left the
  // subway's one-second timer running against the AirTrain detail. Each tick
  // re-entered the scheduled branch, which wrote the live region directly. A text
  // dedup hid it until the scheduled text CHANGED, which it does on a headway-band
  // boundary, and then a countdown tick spoke with no input and no network event.
  //
  // The clock starts at 10:59:30 New York and runs across 11:00, where the committed
  // AirTrain bands step from seven minutes to four.
  const ctx = await installMocks(page);
  ctx.overrides.airtrain = (route, fixtures) => json(route, fixtures.airtrain());
  const BAND_EDGE = Date.UTC(2026, 6, 2, 14, 59, 30); // 10:59:30 America/New_York
  await page.clock.install({ time: new Date(BAND_EDGE) });
  await page.clock.pauseAt(new Date(BAND_EDGE));
  await page.goto("/");
  await awaitRegistry(page);

  // A live station first, which is what arms the tick.
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator("#stations-detail")).toContainText("Northbound");
  expect(await page.evaluate(() => panelTimer !== null), "a live station arms the tick").toBe(true);

  // Then a feedless one. The tick must stop, because there is nothing to count.
  await page.locator("#stations-search").fill("federal");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator("#stations-detail")).toContainText("every 7 minutes");
  expect(await page.evaluate(() => panelTimer === null), "a feedless station stops the tick").toBe(true);

  // Nothing is touching the page now. Advance across the band edge and the live
  // region must not say a word, even though the scheduled text would change.
  await page.evaluate(() => {
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  });
  const spokenBefore = await page.locator("#stations-announce").innerText();
  await page.clock.runFor(45_000);
  expect(await page.locator("#stations-announce").innerText(), "no tick may write the live region").toBe(
    spokenBefore,
  );
  // And the detail is not being repainted by a leaked interval either.
  await expect(page.locator("#stations-detail")).toContainText("every 7 minutes");

  // BELT AND BRACES, TESTED SEPARATELY. The fix has two independent guards: the tick
  // is stopped on a station switch (above), and the scheduled branch refuses to speak
  // on a tick render at all. With the first guard working, nothing can deliver a tick
  // here, so the second is invoked directly. Otherwise it would sit untested until the
  // day someone reintroduces a timer and discovers the guard never worked.
  await page.evaluate(() => renderStationDetail({ tick: true }));
  expect(
    await page.locator("#stations-announce").innerText(),
    "a tick render of the scheduled branch must stay silent",
  ).toBe(spokenBefore);
});

test("A1q. reopening after a failed load keeps the error, and never fakes Loading", async ({ page }) => {
  // ROUND 2's second finding. The reopen re-render took its error argument as null,
  // fell through to the "no body yet" branch, and painted "Loading arrivals..." over a
  // truthful error, for a fetch that had already come back. The reopen refresh then
  // keeps quiet on failure by design, so the fake loading line stayed while the
  // backend was down, contradicting the error still sitting in the live region.
  const ctx = await installMocks(page);
  ctx.overrides.subwayArrivals = (route) =>
    json(route, { detail: "Arrivals cache is warming up; try again in a few seconds." }, 503);
  await open(page, { install: false });
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("warming up");

  await page.keyboard.press("Escape");
  await expect(page.locator("#stations-panel")).toBeHidden();
  await page.locator("#stations-toggle").click();
  await expect(page.locator("#stations-panel")).toBeVisible();

  // Still the truth, and specifically NOT a loading line for a fetch that finished.
  await expect(detail).toContainText("warming up");
  await expect(detail).not.toContainText("Loading arrivals");
  // No tick either: there are no countdowns to move.
  expect(await page.evaluate(() => panelTimer === null), "an errored station arms no tick").toBe(true);
});

test("A1r. two refresh cycles of unchanged data produce not one extra announcement", async ({ page }) => {
  // THIS SPEC EXISTS BECAUSE A DEDUP WAS REMOVED. announcePanelState used to compare
  // the live region's own textContent against the new text and skip the write if they
  // matched, which meant nobody could tell what that check was really preventing.
  // It is gone; this pins what has to be true without it, and it counts WRITES rather
  // than comparing final text, because assigning an identical string to a live region
  // still mutates it and a screen reader still speaks.
  await open(page);
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator("#stations-detail")).toContainText("Northbound");
  await expect(page.locator("#stations-announce")).toContainText("Times Sq");

  // Count every mutation of the region from here on. The first announcement has
  // already happened; everything after this point is repaint and refresh.
  await page.evaluate(() => {
    window.__announceWrites = 0;
    new MutationObserver(() => {
      window.__announceWrites++;
    }).observe(document.getElementById("stations-announce"), {
      childList: true,
      characterData: true,
      subtree: true,
    });
  });

  // 31 seconds covers two full 15s background refresh cycles AND 31 countdown ticks,
  // so both repeat paths are on trial at once: the tick that must never speak, and the
  // refresh whose payload is byte-identical every time (the mock serves one fixture).
  await page.clock.runFor(31_000);

  // The countdowns MUST have moved, or this spec proves nothing: a page where nothing
  // repainted would trivially report zero announcements.
  await expect(page.locator("#stations-detail")).toContainText("in 1 minute");
  expect(await page.evaluate(() => window.__announceWrites), "silent across both cycles").toBe(0);
});

test("A1o. a stale payload's age is spoken, not left on screen alone", async ({ page }) => {
  // The announcement reads the countdowns aloud whether or not the feed behind them
  // is current, so the caveat has to travel with them rather than living only in the
  // visible text a listening rider cannot see.
  const ctx = await installMocks(page);
  ctx.overrides.subwayArrivals = (route, fixtures) =>
    json(route, { ...fixtures.subwayArrivals(), fetched_at: fx.FROZEN_S - 600 });
  await open(page, { install: false });
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect(page.locator(".station-detail-stale")).toContainText(/as of \d+m ago/);
  await expect(page.locator("#stations-announce")).toContainText(/as of \d+m ago/);
});

test("A1s. an NJ Transit station reaches the panel and reads its flat departure board", async ({
  page,
}) => {
  // THE PANEL IS THE TEXT EQUIVALENT OF THE MAP, so a system that draws markers and
  // is unreachable here is half-shipped. NJT is also the first system whose arrivals
  // endpoint is FLAT: no direction buckets at all, one chronological list, with the
  // destination on the row. That shape had to be given a bucket name the panel could
  // print rather than being dropped for having no directions dict.
  await open(page);
  await page.locator("#stations-search").fill("newark penn");
  const rows = page.locator("#stations-results button.station-row");
  // ONE ROW, not two: the PATH fixture also has a "Newark", and the id spaces of the
  // two systems overlap freely, so a registry key that was not system-qualified would
  // have merged them. Searching the fuller name pins which station this is.
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("NJ Transit");
  await rows.first().press("Enter");

  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("Newark Penn Station");
  await expect(detail).toContainText("NJ Transit");

  // New York Penn Station is the one with a board in the fixture. Selected by name so
  // the spec does not depend on registry order.
  await page.locator("#stations-search").fill("new york penn");
  await expect(rows).toHaveCount(1);
  await rows.first().press("Enter");
  await expect(detail).toContainText("Departures");
  // The destination reads in the sentence, which is what a departure board is for,
  // and the train number rides along as the aside every other system already gets.
  const lines = detail.locator("li");
  await expect(lines).toHaveText([
    "Northeast Corridor to Trenton train in 2 minutes, 8:01 AM arrival, train 3800",
    "Montclair-Boonton Line to Dover train in 5 minutes, 8:05 AM arrival, train 6634",
    // The route-less row, which models.NjtArrival really serves. It keeps its
    // destination and its countdown and simply has no route to name, which is the
    // honest rendering: dropping the row would hide a departure the rider can catch.
    "to Bay Head train in 8 minutes, 8:08 AM arrival",
  ]);
  // A fresh payload says nothing about its age, exactly as the popups do.
  await expect(detail.locator(".station-detail-stale")).toHaveCount(0);

  // AND IT IS SPOKEN. The panel's live region is the surface a rider who cannot see
  // the detail actually gets.
  await expect(page.locator("#stations-announce")).toContainText("Northeast Corridor to Trenton");
});

test("A1t. an NJ Transit station whose route has no line still gets a chip, not a blank", async ({
  page,
}) => {
  // AMENDMENT A, on the panel. Hoboken lists routes 2 and 17; route 17 never reaches
  // /api/njt-routes, so its chip has no served colour and no name. The chip must
  // still render, in the neutral fallback, with the route id as its text: a station
  // that silently dropped a route it serves would be worse than an ugly chip.
  await open(page);
  await page.locator("#stations-search").fill("hoboken");
  const rows = page.locator("#stations-results button.station-row");
  const njtRow = rows.filter({ hasText: "NJ Transit" });
  await expect(njtRow).toHaveCount(1);
  const chips = njtRow.locator(".station-chip");
  await expect(chips).toHaveText(["2", "17"]);
  const styles = await chips.evaluateAll((els) => els.map((el) => el.style.background));
  // Route 2's own colour from the feed, and the neutral fallback for the route that
  // has none. Written as the rendered rgb() rather than the hex the code carries,
  // because that is what the browser reports back.
  expect(styles).toEqual(["rgb(230, 104, 89)", "rgb(74, 78, 105)"]);
});

/* ---- F12: a superseded selection's error body may not touch the panel ---------
   Audit 5's F12, fixed on claude/release1-small-fixes. The panel's sequence guard
   used to run after the response HEADERS and after the SUCCESS body, and nowhere
   after the ERROR body, so station A's 503 detail line could land after the rider
   had already selected station B and overwrite B's panel with A's failure.

   THIS NEEDS THE HEADERS AND THE BODY TO ARRIVE SEPARATELY, which no route.fulfill
   can express: Playwright delivers a stubbed response whole, and a whole late
   response is caught by the guard that already existed, so a spec built that way
   would pass with the fix reverted and prove nothing. A 503's detail line really is
   a second await over a body the browser reads after the status is known, so the
   split below is the network fact the defect lives in, not a contrivance: window
   .fetch is wrapped before any app script runs, and the test decides when each half
   of a held response resolves. Everything not held is served normally.

   Hermetic counterpart: docs/reviews/audit-2026-09-05/f12_stale_error_body_overwrites.mjs,
   which drives the same interleaving over the real committed LIRR capture in a vm.
------------------------------------------------------------------------------- */

// The 503 a warming backend really sends, copied from backend/cache.py _serve_cached.
const WARMING_DETAIL = "Feed cache is warming up; try again in a few seconds.";

// Wrap window.fetch so ONE arrivals response can be delivered in two halves. Installed
// as an init script so it is in place before stations.js runs; every request that is
// not explicitly held falls through to the real fetch, and so still meets mock.js.
async function installArrivalsDeck(page) {
  await page.addInitScript(() => {
    const real = window.fetch.bind(window);
    const deck = { pattern: "/api/subway-arrivals/", holdNext: false, calls: [] };
    window.__deck = deck;
    window.fetch = (input, init) => {
      const url = String(input && input.url ? input.url : input);
      if (!deck.holdNext || !url.includes(deck.pattern)) return real(input, init);
      // Claimed by the test. One call only: selectStation calls fetchPanelArrivals
      // BEFORE it syncs the map, so the first arrivals request after a selection is
      // always the panel's and the popup's goes to the real fetch behind it.
      deck.holdNext = false;
      let sendHeaders;
      let sendBody;
      const headers = new Promise((resolve) => {
        sendHeaders = resolve;
      });
      const body = new Promise((resolve) => {
        sendBody = resolve;
      });
      deck.calls.push({
        url,
        headers: (ok, status) => sendHeaders({ ok, status, json: () => body }),
        body: (payload) => sendBody(payload),
      });
      return headers;
    };
  });
}

const deckCallCount = (page) => page.evaluate(() => window.__deck.calls.length);
const holdNextArrivals = (page) => page.evaluate(() => {
  window.__deck.holdNext = true;
});
const deckHeaders = (page, i, ok, status) =>
  page.evaluate(([n, o, s]) => window.__deck.calls[n].headers(o, s), [i, ok, status]);
const deckBody = (page, i, payload) =>
  page.evaluate(([n, p]) => window.__deck.calls[n].body(p), [i, payload]);

test("A1u. a superseded station's error body never overwrites the station on screen (F12)", async ({
  page,
}) => {
  const ctx = await installMocks(page);
  await installArrivalsDeck(page);
  // Canal St's own arrivals, so the rows on screen are provably B's and not the
  // fixture every subway station would otherwise share.
  const canalArrivals = {
    fetched_at: fx.FROZEN_S,
    station_id: "A31",
    station_name: "Canal St",
    directions: { Uptown: [{ route_id: "A", trip_id: "canal-1", arrival: fx.FROZEN_S + 240 }] },
  };
  await open(page, { install: false });
  expect(ctx.leaks, "the deck must not have let anything reach the network").toEqual([]);

  // Station A: the rider selects Times Sq and its panel fetch is held mid-flight.
  await holdNextArrivals(page);
  await page.locator("#stations-search").fill("times");
  await page.locator("#stations-results button.station-row").first().click();
  await expect.poll(() => deckCallCount(page), { timeout: 10_000 }).toBe(1);
  expect(await page.evaluate(() => window.__deck.calls[0].url)).toContain("/api/subway-arrivals/127");

  // A answers 503 HEADERS. Its detail line, the second await, stays in the air.
  await deckHeaders(page, 0, false, 503);

  // Station B, selected while A's error body is still pending. This is the bump that
  // the error branch used to read and then ignore.
  await holdNextArrivals(page);
  await page.locator("#stations-search").fill("canal");
  await page.locator("#stations-results button.station-row").first().click();
  await expect.poll(() => deckCallCount(page), { timeout: 10_000 }).toBe(2);

  // B answers in full and the panel renders it.
  await deckHeaders(page, 1, true, 200);
  await deckBody(page, 1, canalArrivals);
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("Canal St");
  await expect(detail.locator("ul.station-arrivals li")).toHaveCount(1);
  const rowsBefore = await detail.locator("ul.station-arrivals li").first().innerText();
  const spokenBefore = await page.locator("#stations-announce").innerText();

  // AND NOW A's DELAYED 503 BODY LANDS, two selections stale.
  await deckBody(page, 0, { detail: WARMING_DETAIL });

  // SAMPLED, NOT POLLED ONCE. There is no timer between the body resolving and the
  // render that used to follow it, only a microtask chain, and a single assertion
  // would pass before the body had been read at all. Each round trip below gives the
  // page room to run it; under the reverted guard the warming text appears in the
  // first few. A7g records the same trap in the bus specs.
  for (let i = 0; i < 10; i++) {
    expect(
      await detail.locator("ul.station-arrivals li").count(),
      `B's arrivals must survive A's stale error body (sample ${i})`,
    ).toBe(1);
  }
  await expect(detail).toContainText("Canal St");
  await expect(detail).not.toContainText("warming up");
  expect(await detail.locator("ul.station-arrivals li").first().innerText()).toBe(rowsBefore);
  expect(await page.evaluate(() => panelError), "no error may be recorded for a station left behind").toBe(
    null,
  );
  expect(
    await page.locator("#stations-announce").innerText(),
    "and the live region must not speak A's failure under B's name",
  ).toBe(spokenBefore);

  // STAYS SHOWING IT. The defect was sticky rather than a one-frame flicker: the
  // production tick repainted the same overwritten state every second, so the repaint
  // is part of the claim.
  await page.evaluate(() => renderStationDetail({ tick: true }));
  await expect(detail).toContainText("Canal St");
  await expect(detail.locator("ul.station-arrivals li")).toHaveCount(1);
  await expect(detail).not.toContainText("warming up");
});

/* ==================================================================
   A1v-A1z, A1v2-A1z2: the panel says what the popup says (F11)

   THE FINDING. The map popup consulted the alert store and the panel did not, so a
   station suspension appeared on one surface and not the other. That is not a cosmetic
   gap: at 375 the open panel makes #map inert, so the popup cannot be opened or read,
   and the panel is the only text surface a rider has. The acceptance the auditor wrote
   is that the suspension appears in BOTH, including when no train of the affected
   route is currently predicted.

   These specs drive the two surfaces in one page, in the order a rider would reach
   them, and compare what each one says.
   ================================================================== */

// Boot with a given alerts body and wait for the store to actually hold it, rather
// than for a timer. The alerts poll is a fetch like any other, so a spec that asserts
// immediately after navigation races it.
async function openWithAlerts(page, body) {
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route) => json(route, body);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await awaitRegistry(page);
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byStop.size + alertsIndex.byRoute.size > 0,
  );
  return ctx;
}

// Select a station through the panel the way a rider does: search, then click the row.
async function selectStation(page, query) {
  if (await page.evaluate(() => document.getElementById("stations-panel").hidden)) {
    await page.locator("#stations-toggle").click();
  }
  await page.locator("#stations-search").fill(query);
  const row = page.locator("#stations-results button.station-row").first();
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.locator("#stations-detail h3")).toBeVisible();
}

const panelAlerts = (page) => page.locator("#stations-detail ul.station-alerts li");

test("A1v. a station suspension is in the panel AND the popup, with no train of that route (F11)", async ({
  page,
}) => {
  // THE AUDITOR'S ACCEPTANCE, both halves in one page.
  //
  // Times Sq (127) serves routes 1, 2 and 3; the committed arrivals fixture carries
  // only 1s and 2s. So "[3] suspended overnight" reaches this station ONLY through the
  // static routes-per-station index, which is the case the panel had no way to show at
  // all and the case a rider most needs, because a suspended route is precisely the
  // one with no train coming.
  await openWithAlerts(page, { ...fx.alerts(), alerts: fx.stationAlertList() });

  await selectStation(page, "times");
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("Northbound"); // the board rendered too
  await expect(panelAlerts(page)).toHaveCount(2);
  await expect(detail).toContainText("Times Sq-42 St is closed");
  await expect(detail).toContainText("[3] suspended overnight");
  // A route that does not serve this station stays out of the panel exactly as it
  // stays out of the popup: the panel is not simply printing the whole store.
  await expect(detail).not.toContainText("[Z] does not serve Times Sq");

  // THE SAME STATION'S POPUP, for the comparison the finding is about. Closing the
  // panel is what makes the map reachable at all, which is the 375 argument in
  // miniature.
  await page.evaluate(() => closeStationsPanel());
  await page.evaluate(() => stationLayer.getLayers()[0].openPopup());
  const popup = page.locator(".leaflet-popup-content");
  await expect(popup).toContainText("Times Sq-42 St is closed");
  await expect(popup).toContainText("[3] suspended overnight");
  await expect(popup).not.toContainText("[Z] does not serve Times Sq");
});

test("A1w. the panel shows the alert when the arrivals FAIL (F11)", async ({
  page,
}) => {
  // THE ORDERING CLAIM, which is the part that would be easy to get wrong by hanging
  // the alerts off the arrivals body. A rider whose arrivals fetch failed is exactly
  // the rider who most needs to know the station is closed, and the matcher's route
  // set falls back to the station's own routes list when there is no body to read.
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route) => json(route, { ...fx.alerts(), alerts: fx.stationAlertList() });
  ctx.overrides.subwayArrivals = (route) =>
    json(route, { detail: "Feed cache is warming up; try again in a few seconds." }, 503);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await awaitRegistry(page);
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byStop.size > 0,
  );

  await selectStation(page, "times");
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("warming up"); // the arrivals really did fail
  await expect(panelAlerts(page)).toHaveCount(2);
  await expect(detail).toContainText("Times Sq-42 St is closed");
  await expect(detail).toContainText("[3] suspended overnight");
});

test("A1z2. the panel shows the alert while the arrivals are still IN FLIGHT (F11)", async ({
  page,
}) => {
  // THE OTHER BRANCH, and a different claim from A1w. There the fetch resolved and
  // failed; here it has not resolved at all, so the panel is on its "Loading arrivals"
  // line with no body in hand. The alerts render anyway, which is only true because
  // the matcher's route set falls back to the station's own routes list.
  //
  // The arrivals response is HELD rather than delayed by a timer: the page clock is
  // paused, so a timer-based delay would never fire and the spec would hang instead of
  // measuring anything.
  let release;
  const held = new Promise((resolve) => {
    release = resolve;
  });
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route) => json(route, { ...fx.alerts(), alerts: fx.stationAlertList() });
  ctx.overrides.subwayArrivals = async (route) => {
    await held;
    return json(route, fx.subwayArrivals());
  };
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await awaitRegistry(page);
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byStop.size > 0,
  );

  await selectStation(page, "times");
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("Loading arrivals");
  await expect(panelAlerts(page)).toHaveCount(2);
  await expect(detail).toContainText("Times Sq-42 St is closed");

  // And the board arriving does not displace them: the alerts are ADDED to the panel,
  // not an alternative to it.
  release();
  await expect(detail).toContainText("Northbound");
  await expect(panelAlerts(page)).toHaveCount(2);
});

test("A1x. an NJ Transit station shows its alert in both surfaces, through a FLAT board (F11)", async ({
  page,
}) => {
  // THE SECOND HALF OF THE FINDING, from 15c's ledger. NJ Transit's arrivals board is
  // flat, with no directions on it at all, and the station alert join could not read
  // that shape, so systems/njt.js rendered no alerts block.
  //
  // HOBOKEN RATHER THAN PENN STATION, because Hoboken isolates the flat-shape arm.
  // Its static routes are 2 and 17; the board served here carries a route 9 train, and
  // "[9] Northeast Corridor suspended" is scoped to route 9 and no stop. So this alert
  // reaches this station only if the join reads route ids out of a flat arrivals list.
  const ctx = await installMocks(page);
  ctx.overrides.alerts = (route) => json(route, { ...fx.alerts(), alerts: fx.stationAlertList() });
  ctx.overrides.njtArrivals = (route) => json(route, fx.njtArrivalsHoboken());
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.clock.pauseAt(new Date(fx.FROZEN_MS));
  await page.goto("/");
  await awaitRegistry(page);
  await page.waitForFunction(
    () => typeof alertsIndex !== "undefined" && alertsIndex.byRoute.has("njt|9"),
  );

  await selectStation(page, "hoboken");
  const detail = page.locator("#stations-detail");
  await expect(detail).toContainText("NJ Transit");
  await expect(detail).toContainText("[9] Northeast Corridor suspended");
  // Penn Station's stop-scoped alert is for a different station and does not follow.
  await expect(detail).not.toContainText("New York Penn Station platforms closed");

  // And the popup, which had no alerts block at all before F11.
  await page.evaluate(() => closeStationsPanel());
  await page.evaluate(() => {
    const marker = njtStations.getLayers().find((m) => m.getLatLng().lat.toFixed(4) === "40.7350");
    marker.openPopup();
  });
  await expect(page.locator(".leaflet-popup-content")).toContainText(
    "[9] Northeast Corridor suspended",
  );
});

test("A1y. a RETAINED alert set is labeled as held, never presented as current (F11)", async ({
  page,
}) => {
  // A retained set is not late, it is HELD: the subway alerts feed is down and the
  // backend is serving the alerts it last decoded. The panel says so, with an age,
  // because an alert set a rider acts on must never imply it is current when it is a
  // carried-forward copy.
  const held = fx.FROZEN_S - 600;
  await openWithAlerts(
    page,
    fx.alertsWithSystems({ alerts: fx.stationAlertList(), frozen: "subway", retainedSince: held }),
  );

  await selectStation(page, "times");
  const detail = page.locator("#stations-detail");
  await expect(panelAlerts(page)).toHaveCount(2); // the held alerts are still shown
  await expect(detail.locator(".station-alerts-stale")).toHaveText("alerts held from 10m ago");
  // It is a different line from the ARRIVALS age line, which is about a different
  // feed; the arrivals here are fresh, so that one is absent.
  await expect(detail.locator(".station-detail-stale")).toHaveCount(0);
});

test("A1z. a station with no matched alert still gets the hedge when its source is stale (F11)", async ({
  page,
}) => {
  // AN EMPTY ALERT SET FROM A DEAD FEED LOOKS EXACTLY LIKE AN EMPTY ONE FROM A HEALTHY
  // FEED, and only this line tells them apart. Canal St matches nothing in the store,
  // so without the hedge the panel would quietly imply that nothing is wrong there.
  // Same reasoning as the R1 marker riding on an empty popup block.
  const ctx = await openWithAlerts(
    page,
    fx.alertsWithSystems({ alerts: fx.stationAlertList() }),
  );
  await selectStation(page, "canal");
  const detail = page.locator("#stations-detail");
  await expect(panelAlerts(page)).toHaveCount(0);
  await expect(detail.locator(".station-alerts-stale")).toHaveCount(0); // fresh: no hedge

  // The subway alerts feed stops decoding while everything else keeps polling, then
  // the clock crosses the threshold. The panel repaints on its own tick.
  ctx.overrides.alerts = (route, fixtures) =>
    json(route, fixtures.alertsWithSystems({
      alerts: fixtures.stationAlertList(),
      fetchedAt: fx.FROZEN_S + 310,
      servedAt: fx.FROZEN_S + 310,
      frozen: "subway",
      frozenAt: fx.FROZEN_S,
      retainedSince: null,
    }));
  await page.clock.fastForward(310_000);
  await page.evaluate(() => loadAlerts());
  await page.evaluate(() => renderStationDetail({ tick: true }));
  await expect(panelAlerts(page)).toHaveCount(0); // still nothing matched here
  await expect(detail.locator(".station-alerts-stale")).toHaveText("alerts may be out of date");
});

// Count WRITES to the panel's live region from this point on, not final text:
// assigning an identical string still mutates the region and a screen reader still
// speaks, so comparing the text at the end would miss exactly the chattiness these
// specs exist to prevent. Same lesson as A1r, and the same shape.
async function watchPanelAnnouncements(page) {
  await page.evaluate(() => {
    window.__panelSpeech = [];
    new MutationObserver(() => {
      window.__panelSpeech.push(document.getElementById("stations-announce").textContent);
    }).observe(document.getElementById("stations-announce"), {
      childList: true,
      characterData: true,
      subtree: true,
    });
  });
}

const panelSpeech = (page) => page.evaluate(() => window.__panelSpeech);

// The alert half of whatever was spoken. Case-insensitive because "Service alerts for
// this station have cleared." starts the sentence, and a case-sensitive match for
// "service alert" silently finds nothing there, which reads as a missing announcement
// rather than as a bad filter.
const alertSpeechLines = async (page) =>
  (await panelSpeech(page)).filter((line) => /service alert/i.test(line));

test("A1v2. an alert appearing for the selected station announces ONCE, and ticks stay silent (F11)", async ({
  page,
}) => {
  // BOTH HALVES OF THE RULE IN ONE SPEC, because either alone is easy to satisfy
  // wrongly. Announcing on every repaint satisfies "it announces"; announcing never
  // satisfies "ticks are silent".
  //
  // THE TICK IS THE HARD CASE, and it is why this announcement does not use the
  // panel's tick guard. The alerts poll lands between arrivals fetches, so the very
  // next repaint carrying a new alert is a COUNTDOWN TICK. A tick-suppressed
  // announcement would therefore never fire at all. What stands in front of this
  // write instead is a change guard on the alert identities, which is strictly
  // stronger: it cannot fire twice for one change however many ticks carry it.
  test.setTimeout(90_000);
  const ctx = await installMocks(page);
  let suspended = false;
  ctx.overrides.alerts = (route, fixtures) =>
    json(route, { ...fixtures.alerts(), alerts: suspended ? fixtures.stationAlertList() : [] });
  await open(page, { install: false });

  await selectStation(page, "times");
  await expect(page.locator("#stations-detail")).toContainText("Northbound");
  await expect(page.locator("#stations-announce")).toContainText("Times Sq");
  await expect(panelAlerts(page)).toHaveCount(0);

  await watchPanelAnnouncements(page);
  // Thirty-one seconds of ticks and two background refreshes with NO alert: the
  // silence half, on trial first.
  await page.clock.runFor(31_000);
  expect(await panelSpeech(page), "quiet while nothing changed").toEqual([]);

  // The suspension appears on the next alerts poll (60s cadence).
  suspended = true;
  await page.clock.runFor(60_000 + 2_000);

  await expect(panelAlerts(page)).toHaveCount(2);
  // TWO alerts land in the same poll (a stop suspension and a route suspension), and
  // the summary counts them rather than speaking twice.
  const alertLines = await alertSpeechLines(page);
  expect(alertLines, "exactly one alert announcement").toEqual([
    "2 new service alerts for this station.",
  ]);
  // A SUMMARY, NEVER THE BODY: the block on screen carries the wording, and a live
  // region reading a full service alert aloud would be unusable during the incident
  // it exists for.
  expect(alertLines[0]).not.toContain("suspended");
  expect(alertLines[0]).not.toContain("closed");

  // And it does not repeat: another half minute of ticks over the SAME alert set.
  const before = (await alertSpeechLines(page)).length;
  await page.clock.runFor(31_000);
  expect(
    (await alertSpeechLines(page)).length - before,
    "an unchanged alert set never speaks again",
  ).toBe(0);
});

test("A1w2. an alert CLEARING for the selected station is announced too (F11)", async ({ page }) => {
  // The panel's deliberate divergence from the banner, in a browser. The banner stays
  // silent on a clear because a rider sees the strip disappear; this block lives
  // inside a subtree that is replaced every second, so there is no disappearance to
  // perceive, and at 375 the inert map makes this the only surface saying anything.
  // A rider who changed their plan on "this station is closed" is owed the retraction.
  test.setTimeout(90_000);
  const ctx = await installMocks(page);
  let suspended = true;
  ctx.overrides.alerts = (route, fixtures) =>
    json(route, { ...fixtures.alerts(), alerts: suspended ? fixtures.stationAlertList() : [] });
  await open(page, { install: false });

  await selectStation(page, "times");
  await expect(panelAlerts(page)).toHaveCount(2);
  await watchPanelAnnouncements(page);

  suspended = false;
  await page.clock.runFor(60_000 + 2_000);

  await expect(panelAlerts(page)).toHaveCount(0);
  expect(await alertSpeechLines(page)).toEqual([
    "Service alerts for this station have cleared.",
  ]);
});

test("A1x2. selecting a station that ALREADY has alerts does not interrupt (F11)", async ({
  page,
}) => {
  // The first observation seeds silently. Rendering an alert that was already there
  // when the rider arrived is not a change, and the arrivals announcement already
  // names the station on selection; a second interruption riding on it would be the
  // chattiness these specs exist to prevent.
  await openWithAlerts(page, { ...fx.alerts(), alerts: fx.stationAlertList() });
  await watchPanelAnnouncements(page);

  await selectStation(page, "times");
  await expect(panelAlerts(page)).toHaveCount(2);
  expect(
    await alertSpeechLines(page),
    "selection speaks the board, not an alert change",
  ).toEqual([]);
  // The station itself WAS announced, so this is silence about the alert set rather
  // than a live region that is not working.
  await expect(page.locator("#stations-announce")).toContainText("Times Sq");
});

test("A1y2. at 320 the alerts block stays inside the panel and scrolls nothing sideways (F11)", async ({
  page,
}) => {
  // AXE CANNOT SEE THIS ONE. A block that overflows its container is not a contrast
  // defect, an ARIA defect or a heading defect; it is text a rider cannot read, and
  // the page-wide scan at 320 passes right over it. 320 is where it would happen:
  // the alert headers are the longest strings the panel renders and the list carries
  // its own indent inside a coloured box.
  await page.setViewportSize({ width: 320, height: 640 });
  await openWithAlerts(page, { ...fx.alerts(), alerts: fx.stationAlertList() });
  await selectStation(page, "times");
  await expect(panelAlerts(page)).toHaveCount(2);

  const geometry = await page.evaluate(() => {
    const de = document.documentElement;
    const panel = document.getElementById("stations-panel").getBoundingClientRect();
    const rows = [...document.querySelectorAll("#stations-detail ul.station-alerts li")];
    const box = document.querySelector("#stations-detail ul.station-alerts").getBoundingClientRect();
    return {
      documentOverflow: de.scrollWidth - de.clientWidth,
      boxEscapesPanel: box.left < panel.left - 0.5 || box.right > panel.right + 0.5,
      rowsEscapeTheBox: rows.some(
        (li) => li.getBoundingClientRect().right > box.right + 0.5,
      ),
      // A row that wrapped to nothing would satisfy every bound above while showing
      // the rider no text at all.
      shortestRow: Math.min(...rows.map((li) => li.getBoundingClientRect().height)),
    };
  });
  expect(geometry.documentOverflow, "no sideways scroll at 320").toBe(0);
  expect(geometry.boxEscapesPanel, "the alerts box stays inside the panel").toBe(false);
  expect(geometry.rowsEscapeTheBox, "the bullets stay inside their box").toBe(false);
  expect(geometry.shortestRow, "every alert row is actually rendered").toBeGreaterThan(10);
});
