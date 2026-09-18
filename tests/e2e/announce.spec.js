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
  /* The chrome rewrites itself continuously, and none of that is news. Anything comparing
     rendered text would announce forever; this is the spec that fails if someone reaches for
     a string compare.

     MR1 MOVED THE VACUITY GUARD, and kept it honest. It used to read #status, which repainted
     every poll because the status line CONTAINED the clock. The clock is its own element in
     the header now and the note is only the problems, so on a healthy page #status is empty
     and stays empty: reading it would have made this spec vacuous in the quietest possible
     way, passing because nothing anywhere had changed. The clock is what repaints now, so
     the clock is what proves the page was alive; and #status staying empty through it is a
     second claim worth making, because a note that invented something to say on a healthy
     poll is exactly what mutation M4 produces. */
  await installMocks(page);
  await open(page);
  await watchAnnouncements(page);

  const clock = () => page.locator("#clock-time").textContent();
  const note = () => page.locator("#status").textContent();
  const beforeClock = await clock();
  expect(await note(), "a healthy page's note starts empty").toBe("");
  await page.clock.runFor(POLL_MS * 2 + 2000);

  // The visible chrome really did repaint, or this spec proves nothing.
  expect(await clock(), "the clock must actually have repainted").not.toBe(beforeClock);
  // And the note still has nothing to say, because nothing is wrong.
  expect(await note(), "a healthy poll must not give the note something to say").toBe("");
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

// Every MutationObserver BATCH the page region receives: how many records it carried,
// and what the region said after it. A2f to A2i count callbacks, which cannot see the
// shape N6 names (two writes landing as one batch) or its 6.3 variant (two writes in two
// batches that no task boundary, and so no rendering opportunity, separates); a render
// whose writes are composed arrives as exactly one batch of exactly one record.
async function watchBatches(page) {
  await page.evaluate(() => {
    window.__pageBatches = [];
    new MutationObserver((records) => {
      window.__pageBatches.push({
        records: records.length,
        text: document.getElementById("page-announce").textContent,
      });
    }).observe(document.getElementById("page-announce"), {
      childList: true,
      characterData: true,
      subtree: true,
    });
  });
}

const batches = (page) => page.evaluate(() => window.__pageBatches);

// THE WORLD A2j AND A2l SHARE, and the one the age gate makes: an LIRR fix drawn
// qualified, its own observation 590 s old, while Metro-North's poll is 200 s old. On the
// next poll, served 15 s later, the fix is 605 s old, past OBS_MAX_S, and the gate
// withholds it (gone from the payload, counted in LIRR's block), while Metro-North
// recovers. A rider holding that train's popup is rescued, in the status line's words
// (the feed still carries the train, so "left the feed" would be false of it), and the
// same poll announces the recovery.
const WITHHELD_KEY = "LIRR|lirr-gps-1";
const WITHHELD_FIX = {
  system: "LIRR", trip_id: "lirr-gps-1", route_id: "1", latitude: 40.76, longitude: -73.6,
  bearing: 90, train_num: "2751", stop_id: null, stop_name: null, direction: "Eastbound",
  prev_lat: null, prev_lon: null, prev_time: null, next_time: null,
  observed_at: null, provenance: "reported",
};
const WITHHELD_SPOKEN =
  "The LIRR Babylon Branch you were following is no longer shown, last seen over 10m ago. " +
  "Focus moved to the map. Live data current again for Metro-North.";

function withheldWorld(fixtures, withheld) {
  const at = withheld ? fx.FROZEN_S + 15 : fx.FROZEN_S;
  const body = fixtures.railroadsWithSystems({
    data: [...fixtures.railroads().data, ...(withheld ? [] : [WITHHELD_FIX])],
    fetchedAt: at,
    mnrAt: withheld ? at : fx.FROZEN_S - 200,
    lirrPositions: fixtures.positionSteps(withheld ? { suppressed: 1 } : { qualified: 1 }),
  });
  // The fix's own clock, set after the build, where stampObserved cannot move it to the
  // poll's: this is an OLD observation in a healthy LIRR, which is F01's whole subject.
  for (const row of body.data) if (row.trip_id === WITHHELD_FIX.trip_id) row.observed_at = fx.FROZEN_S - 590;
  return body;
}

// Load the page on that world's first poll, with the rider holding the fix's popup.
async function holdingTheWithheldFix(page, ctx, world) {
  ctx.overrides.railroads = (route, fixtures) => json(route, withheldWorld(fixtures, world.withheld));
  await open(page);
  await expect.poll(() => page.evaluate((key) => railroads.has(key), WITHHELD_KEY)).toBe(true);
  // The page loaded with Metro-North degraded, which a load does not say aloud.
  await expect(page.locator("#status")).toContainText("railroad: MNR as of 3m ago");
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), WITHHELD_KEY);
  await page.locator(".leaflet-popup-close-button").focus();
}

test("A2j. a vanish rescue and a status transition in one poll are one write, not two (6.3)", async ({ page }) => {
  // THE COLLISION THE AGE GATE MADE REACHABLE (memo D11). The gate takes a train off the
  // map in a feed that is decoding normally: an LIRR fix aging past OBS_MAX_S with no
  // fresh prediction leaves the payload and is counted instead. If the rider's focus is in
  // that train's popup, the vanishing-focus door speaks from inside applyRailroads; if the
  // same poll changes a system's degraded membership, the status transition speaks from
  // refreshAll's tail. On the code before this, those were two writes in two batches,
  // and when the railroad response settled last nothing but microtasks lay between them,
  // so an atomic polite region read after that task said only the second sentence.
  const ctx = await installMocks(page);
  const world = { withheld: false };
  await holdingTheWithheldFix(page, ctx, world);
  await watchBatches(page);

  world.withheld = true;
  await page.evaluate(() => refreshAll());
  await expect.poll(() => batches(page)).not.toEqual([]);
  expect(await batches(page), "one render, one batch, one record, both sentences").toEqual([
    { records: 1, text: WITHHELD_SPOKEN },
  ]);
  // The rescue itself still happened at once, and the count the gate added is on the
  // status line, where it belongs, and in no announcement: the rescue speaks the line's
  // words ("no longer shown, last seen over 10m ago"), and never its number.
  expect(await page.evaluate(() => document.activeElement.id)).toBe("map");
  // The rescue speaks the count's WORDS ("no longer shown, last seen over 10m ago") and
  // never its number, which is the point. The number is not on the status line here
  // either, and that is the other half of the same rule: this is the poll on which
  // Metro-North recovered, so the railroad is now wholly healthy and the withheld clause
  // rides a line that is no longer raised. A train can therefore leave the map with the
  // line saying nothing at all, which is the trade design 3.2 takes over a status line a
  // rider stops reading; frontend/positions.test.js pins the riding, clause by clause.
  const status = page.locator("#status");
  await expect(status).not.toContainText("railroad:");
  await expect(status).not.toContainText("not shown");
});

test("A2l. a status change the animation tick finds mid-poll joins that poll's one write, after the rescue (6.3)", async ({ page }) => {
  // THE CASE A2j CANNOT SEE. Its page clock is paused, and Playwright's fake clock runs
  // requestAnimationFrame too, so no animation tick lands inside its poll. In a browser one
  // can: refreshSource rebuilds the freshness index as each response lands, so a tick after
  // the railroad's response and before the poll's last sees Metro-North recover, and the
  // tick announces status changes itself (A2f). Here the subway's response is held back
  // while the clock runs, so a tick lands exactly there. Measured on the first cut, which
  // held only what the poll itself said: the tick spoke the recovery at once, and the
  // rescue came out alone at the poll's end, second. Both orders of the two sentences are
  // plausible readings of one poll, so the spec pins the one a rider experienced: the
  // train they were holding went first.
  const ctx = await installMocks(page);
  let subwayGate = null;
  ctx.overrides.subways = async (route, fixtures) => {
    if (subwayGate) await subwayGate;
    return json(route, fixtures.subways());
  };
  const world = { withheld: false };
  await holdingTheWithheldFix(page, ctx, world);
  // The clock was paused at load, so no tick has run yet: run a few, so the animation loop
  // has recorded Metro-North in its stale set and can notice it leave. Saying nothing,
  // since the load already knew it.
  await page.clock.runFor(200);
  await watchBatches(page);

  let releaseSubway = null;
  subwayGate = new Promise((resolve) => {
    releaseSubway = resolve;
  });
  world.withheld = true;
  await page.evaluate(() => {
    window.__poll = refreshAll();
  });
  // The railroad's response has landed and its apply has taken the fix off the map...
  await expect.poll(() => page.evaluate((key) => railroads.has(key), WITHHELD_KEY)).toBe(false);
  expect(await page.evaluate(() => document.activeElement.id), "the focus move is not held").toBe("map");
  // ...and the animation tick runs while the subway's is still out.
  await page.clock.runFor(300);
  expect(await batches(page), "nothing is spoken while the poll is rendering").toEqual([]);
  releaseSubway();
  subwayGate = null;
  await page.evaluate(() => window.__poll);
  await expect.poll(() => batches(page)).not.toEqual([]);
  expect(await batches(page), "the poll's one write: the rescue, then the recovery the tick found").toEqual([
    { records: 1, text: WITHHELD_SPOKEN },
  ]);
});

test("A2k. the count of trains not shown changing, appearing or clearing says nothing (6.3)", async ({ page }) => {
  // "A suppression count moving from 23 to 24 is not a membership change and must say
  // nothing" (memo D11). The status line changes with the count; the live region does
  // not, because what it speaks is a system's degraded membership and the count is none.
  const ctx = await installMocks(page);
  let suppressed = 23;
  // METRO-NORTH'S POLL IS OLD SO THE LINE IS RAISED, because the withheld clause rides a
  // line and never raises one: on a wholly healthy railroad it says nothing at all, which
  // is what smoke.spec.js's C2j pins. The count still has to be able to change in view of
  // a rider without the live region saying a word, and that is what this test is about.
  ctx.overrides.railroads = (route, fixtures) =>
    json(
      route,
      fixtures.railroadsWithSystems({
        mnrAt: fixtures.FROZEN_S - 180,
        lirrPositions: fixtures.positionSteps({ suppressed }),
      }),
    );
  await open(page);
  const status = page.locator("#status");
  await expect(status).toContainText(
    "railroad: MNR as of 3m ago; LIRR 23 trains not shown, last seen over 10m ago; MNR position age unavailable",
  );
  await watchBatches(page);
  for (const [next, line] of [
    [24, "LIRR 24 trains not shown"],
    [0, null],
    [1, "LIRR 1 train not shown"],
  ]) {
    suppressed = next;
    await page.evaluate(() => refreshAll());
    if (line) await expect(status).toContainText(line);
    else await expect(status).not.toContainText("not shown");
    // The line it rides is still there either way, so a vanishing count is the clause
    // going quiet rather than the whole line going away.
    await expect(status).toContainText("railroad: MNR as of 3m ago");
  }
  expect(await batches(page), "a count is not news").toEqual([]);
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
