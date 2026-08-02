// A2: the page's own live region, in a browser.
//
// The status line and the alert banner both update visually and, before this, said
// nothing at all: a rider who cannot see the map got no signal that a feed had gone
// dark or that an agency-wide alert had appeared. Both now speak through one door
// (announcePage in systems/shared.js) with worthiness judged on underlying state.
//
// WHAT THESE SPECS ARE REALLY DEFENDING is the silence, not the speech. A live region
// that announces too often is worse than one that never speaks, because a rider cannot
// turn it off and cannot skip past it. So every spec here pairs its announcement with
// the refreshes that must NOT produce one.
//
// Same hermetic harness as the rest of the suite.

const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const POLL_MS = 15_000;
const ALERT_POLL_MS = 60_000;

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

// Count WRITES, not final text. Assigning an identical string to a live region still
// mutates it and a screen reader still speaks, so comparing the text at the end would
// miss exactly the chattiness these specs exist to prevent. Same lesson as A1r.
async function watchAnnouncements(page) {
  await page.evaluate(() => {
    window.__pageAnnouncements = [];
    new MutationObserver(() => {
      window.__pageAnnouncements.push(document.getElementById("page-announce").textContent);
    }).observe(document.getElementById("page-announce"), {
      childList: true,
      characterData: true,
      subtree: true,
    });
  });
}

const announcements = (page) => page.evaluate(() => window.__pageAnnouncements);

test("A2f. a feed going stale announces once, and then stays quiet", async ({ page }) => {
  // The subway feed keeps answering, but its fetched_at stops advancing, which is how
  // a wedged upstream actually looks: the poll succeeds and the data rots. The C2
  // freshness index crosses FEED_STALE_AFTER_S and the markers dim; before A2 that was
  // the only signal, and it was purely visual.
  const ctx = await installMocks(page);
  let stale = false;
  ctx.overrides.subways = (route, fixtures) => {
    const body = fixtures.subways();
    if (stale) {
      // Already past FEED_STALE_AFTER_S (90) when it arrives, rather than waiting for
      // it to age there. Same transition, one poll instead of six: driving ninety
      // seconds of virtual time also drives ~900 animation frames, which is slow in
      // real time and was the first draft's undoing.
      const old = fx.FROZEN_S - 200;
      body.fetched_at = old;
      if (body.systems) for (const name of Object.keys(body.systems)) body.systems[name].fetched_at = old;
    }
    return json(route, body);
  };
  await open(page);
  await watchAnnouncements(page);

  // Nothing said yet: the page loaded healthy and a load announces nothing.
  expect(await announcements(page)).toEqual([]);

  stale = true;
  await page.clock.runFor(POLL_MS + 2000);

  await expect.poll(async () => (await announcements(page)).length).toBeGreaterThan(0);
  const spoken = await announcements(page);
  expect(spoken[0]).toContain("Live data delayed");
  expect(spoken[0]).toContain("Subway");

  // AND THEN SILENCE. The system is still degraded and getting older on every poll and
  // every animation tick; none of that is news.
  const afterFirst = spoken.length;
  await page.clock.runFor(POLL_MS * 2 + 2000);
  expect(await announcements(page), "a degraded system getting older is not news").toHaveLength(afterFirst);
  expect(afterFirst, "one transition, one announcement").toBe(1);
});

test("A2g. two unchanged refreshes of a healthy page say nothing at all", async ({ page }) => {
  // The status line rewrites itself every poll because it contains a clock. Anything
  // comparing rendered text would announce forever; this is the spec that fails if
  // someone reaches for a string compare.
  await installMocks(page);
  await open(page);
  await watchAnnouncements(page);

  const before = await page.locator("#status").textContent();
  await page.clock.runFor(POLL_MS * 2 + 2000);
  const after = await page.locator("#status").textContent();

  // The visible line really did change, or this spec proves nothing.
  expect(after, "the status line must actually have repainted").not.toBe(before);
  expect(await announcements(page), "a repaint is not an announcement").toEqual([]);
});

test("A2h. a new agency-wide alert announces once, as a summary", async ({ page }) => {
  // The alerts loop runs on its own 60s cadence, so this spec has to drive more
  // virtual time than the others, and every virtual second also drives animation
  // frames. The default 30s of real time is not enough headroom for that.
  test.setTimeout(90_000);
  const ctx = await installMocks(page);
  let withAlert = false;
  ctx.overrides.alerts = (route, fixtures) => {
    const body = fixtures.alerts();
    if (withAlert) {
      // Agency-wide: no routes and no stops, which is what puts it in the banner
      // rather than only in a station popup.
      body.alerts = [
        {
          id: "a-1",
          system: "subway",
          header: "Reduced service systemwide",
          description: null,
          effect: "REDUCED_SERVICE",
          cause: "OTHER_CAUSE",
          routes: [],
          stops: [],
          starts_at: fx.FROZEN_S - 600,
          ends_at: null,
        },
      ];
    }
    return json(route, body);
  };
  await open(page);
  await watchAnnouncements(page);
  expect(await announcements(page)).toEqual([]);

  withAlert = true;
  await page.clock.runFor(ALERT_POLL_MS + 2000);

  await expect.poll(async () => (await announcements(page)).length).toBe(1);
  // A SUMMARY, NOT THE BODY. The strip carries the wording.
  expect((await announcements(page))[0]).toBe("New service alert.");
  await expect(page.locator("#alert-banner")).toContainText("Reduced service systemwide");

  // The same alert on the following refreshes is not news. tickAlertBanner re-renders
  // the strip on every 15s map poll (that is how the freshness marker appears without
  // waiting for the alerts loop), so two map cycles exercise the repeat path without
  // paying for another full alerts poll.
  await page.clock.runFor(POLL_MS * 2 + 2000);
  // Counted BY KIND, not in total. Driving this much virtual time against fixtures
  // whose fetched_at is fixed also carries every feed past the staleness threshold, so
  // the page legitimately says "Live data delayed" as well. That announcement is
  // correct and is A2f's subject; the claim HERE is only that an unchanged alert set
  // adds no further alert announcement. Asserting a total would have coupled this spec
  // to an unrelated true statement.
  const alertLines = (await announcements(page)).filter((line) => line.includes("service alert"));
  expect(alertLines, "an unchanged alert set is silent").toHaveLength(1);
});

test("A2i. the page region is one door, and nothing else writes it", async ({ page }) => {
  // The structural claim, checked against the running page rather than the source: the
  // region exists, is polite, is out of the visual layout, and is not the panel's
  // region (they are separate elements on purpose so a panel repaint and a page
  // announcement cannot be coupled).
  await installMocks(page);
  await open(page);

  const region = page.locator("#page-announce");
  await expect(region).toHaveAttribute("aria-live", "polite");
  await expect(region).toHaveAttribute("aria-atomic", "true");
  await expect(region).toHaveClass(/visually-hidden/);
  expect(await page.evaluate(() => document.querySelectorAll("#page-announce").length)).toBe(1);
  // Two regions, two purposes, no overlap.
  expect(await page.evaluate(() => document.getElementById("stations-announce") !== null)).toBe(true);
  expect(
    await page.evaluate(() => document.getElementById("page-announce") === document.getElementById("stations-announce")),
  ).toBe(false);
});
