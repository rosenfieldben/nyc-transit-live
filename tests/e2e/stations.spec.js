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
