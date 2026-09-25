/* Follow-up 1 capture harness: the Rail preset before and after the bus zoom rule.

   TEMPORARY IN MR2's TO MR4's SENSE: it lives here rather than in tests/e2e/ so it is not part
   of the suite, and it is kept because it is what makes the pair reproducible rather than a
   pair of pictures. MEASURING.md beside it says how to run it.

   A REAL BUS POPULATION, NOT THE FIXTURE'S TWO. The finding is about hundreds of arrows, and
   two buses photograph as nothing either side of the rule. So the buses are the 2136 of the
   committed OneBusAway capture (backend/tests/fixtures/bus_vehicle_positions.pb), decoded by
   the backend's own decoder into exactly the rows /api/buses serves (decode_capture.py), and
   every other feed is the stock fixture world.

   THE CAPTURE'S CLOCKS ARE SHIFTED, NOT REWRITTEN: every timestamp moves by one constant so the
   capture's poll lands on the suite's frozen clock, which keeps each bus's own age relative to
   that poll exactly what it was. A bus that was stale in the capture is drawn stale here.

   RAIL IS THE PAIR, AND TWO MORE RIDE ALONG. The Rail frames are the before and after the finding
   is about. The City frames should be the same picture twice, because City is where the rule
   draws every bus, and taking them is how that is shown rather than said. The OPENING frames are
   the view every rider meets first, zoom 12 with no preset pressed, where the rule now draws no
   bus at all; the review asked for them, because without them no picture showed the change a
   rider sees soonest. */
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");
const { installMocks, json } = require("./mock");
const fx = require("./fixtures/api");

const OUT =
  process.env.F1_OUT || path.join(__dirname, "..", "..", "docs", "reviews", "map-redesign", "followup-1");
const TAG = process.env.F1_TAG || "after";
const CAPTURE = process.env.F1_BUSES;

function busEnvelope() {
  const { feed_timestamp: feedTs, rows } = JSON.parse(fs.readFileSync(CAPTURE, "utf8"));
  const shift = fx.FROZEN_S - 5 - feedTs;
  return {
    fetched_at: fx.FROZEN_S,
    feed_timestamp: fx.FROZEN_S - 5,
    served_at: fx.FROZEN_S,
    data: rows.map((row) => ({ ...row, observed_at: row.observed_at == null ? null : row.observed_at + shift })),
  };
}

for (const preset of ["open", "view-rail", "view-city"]) {
  test(`capture ${TAG} ${preset}`, async ({ page }) => {
    expect(CAPTURE, "F1_BUSES must name the decoded capture (see MEASURING.md)").toBeTruthy();
    const body = busEnvelope();
    await page.setViewportSize({ width: 1280, height: 720 });
    const ctx = await installMocks(page);
    ctx.overrides.buses = (route) => json(route, body);
    // A RUNNING clock from the frozen start, for MR4's reason: a canvas repaints on animation
    // frames and a paused clock never fires one, and the fly to a preset needs them too.
    await page.clock.install({ time: new Date(fx.FROZEN_MS) });
    await page.goto("/");
    // EVERY BUS AND EVERY FAMILY LOADED, asserted rather than slept on: a half-built map
    // photographs as a design decision.
    await page.waitForFunction(
      (want) =>
        buses.size === want &&
        trains.size === 2 &&
        pathTrainRecords.size === 2 &&
        ferryBoatRecords.size === 3 &&
        njtTrainRecords.size === 4 &&
        stationRegistry.length === 14,
      body.data.length,
      { timeout: 30_000 },
    );
    if (preset === "open") {
      expect(await page.evaluate(() => map.getZoom()), "the map opens at 12").toBe(12);
    } else {
      await page.locator(`#${preset}`).click();
      await expect(page.locator(`#${preset}`)).toHaveAttribute("aria-pressed", "true");
    }
    await page.waitForTimeout(1500);
    const drawn = await page.locator(".bus-marker").filter({ visible: true }).count();
    // Recorded beside the frame, so MEASURING.md can say what each picture holds.
    fs.writeFileSync(
      path.join(OUT, `${TAG}-${preset.replace("view-", "")}.json`),
      `${JSON.stringify({ tag: TAG, preset, buses: body.data.length, drawnAnywhere: drawn }, null, 1)}\n`,
    );
    await page.screenshot({ path: path.join(OUT, `${TAG}-${preset.replace("view-", "")}.png`) });
  });
}
