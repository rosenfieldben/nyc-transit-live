/* MR3 capture harness. Temporary; deleted after the pair is taken.
   The Rail preset at 1280 and at 375, on the stock fixture world at the frozen clock, so a
   before and an after differ only by the tree they were run in. */
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const fx = require("./fixtures/api");
const path = require("node:path");

const OUT = process.env.MR3_OUT || path.join(__dirname, "..", "..", "docs", "reviews", "map-redesign", "mr3");
const TAG = process.env.MR3_TAG || "after";

for (const [name, viewport] of [
  ["desktop", { width: 1280, height: 720 }],
  ["375", { width: 375, height: 720 }],
]) {
  test(`capture ${TAG} ${name} on the Rail preset`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await installMocks(page);
    await page.clock.install({ time: new Date(fx.FROZEN_MS) });
    await page.goto("/");
    // Every rail family loaded, or the capture shows a half-built map.
    await page.waitForFunction(
      () => railroads.size === 2 && njtTrainRecords.size === 4 && stationRegistry.length === 14,
      null,
      { timeout: 20_000 },
    );
    await page.locator("#view-rail").click();
    await expect(page.locator("#view-rail")).toHaveAttribute("aria-pressed", "true");
    // A canvas renderer repaints on requestAnimationFrame, which a paused clock does not run
    // (MR2's H13 found this while capturing its own screenshots), so the clock is left running
    // here and given real time to settle instead of being paused.
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, `${TAG}-${name}.png`) });
  });
}
