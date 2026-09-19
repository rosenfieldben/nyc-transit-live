/* MR4 capture harness. Temporary, in MR3's and MR2's sense: it lives here rather than in
   tests/e2e/ so it is not part of the suite, and it is kept because it is what makes the pair
   reproducible rather than a pair of pictures.

   THE CITY PRESET RATHER THAN RAIL, because this stage's families are the city's: PATH across
   the Hudson, the ferry on the East River, the buses in Manhattan and Brooklyn. AirTrain is out
   at JFK and is the one family the frame has to reach for, so the region view is captured too
   at desktop.

   AND BOTH THEMES, which is the other half of what this stage ships: the same four frames in
   the dark theme, because a mark whose casing was wrong would look fine in the light one. */
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const fx = require("./fixtures/api");
const path = require("node:path");

const OUT =
  process.env.MR4_OUT || path.join(__dirname, "..", "..", "docs", "reviews", "map-redesign", "mr4");
const TAG = process.env.MR4_TAG || "after";

for (const [name, viewport, preset] of [
  ["desktop", { width: 1280, height: 720 }, "#view-city"],
  ["375", { width: 375, height: 720 }, "#view-city"],
  ["region", { width: 1280, height: 720 }, "#view-region"],
]) {
  for (const theme of ["light", "dark"]) {
    const label = theme === "light" ? name : `${name}-dark`;
    test(`capture ${TAG} ${label} on ${preset}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installMocks(page);
      await page.clock.install({ time: new Date(fx.FROZEN_MS) });
      await page.goto("/");
      /* EVERY FAMILY LOADED, ASSERTED RATHER THAN SLEPT ON, because a half-built map
         photographs as a design decision. All four of this stage's families plus the rail ones,
         since the pair is also the evidence that the theme swap reached the casings. */
      await page.waitForFunction(
        () =>
          buses.size === 2 &&
          pathTrainRecords.size === 2 &&
          ferryBoatRecords.size === 3 &&
          railroads.size === 2 &&
          njtTrainRecords.size === 4 &&
          stationRegistry.length === 14 &&
          airtrainRouteLinesLayer.getLayers().length > 0,
        null,
        { timeout: 20_000 },
      );
      /* THE THEME IS SET THROUGH THE CONTROL A RIDER NOW HAS, which is the one thing this pair
         is about that MR3's was not: the before tree's button is hidden, so the before pair's
         dark frames are taken through applyTheme and the after pair's through the button. Both
         end at the same attribute, and the capture says which path it took. */
      if (theme === "dark") {
        const button = page.locator("#theme-toggle");
        if (await button.isVisible()) await button.click();
        else await page.evaluate(() => applyTheme("dark"));
        await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      }
      await page.locator(preset).click();
      await expect(page.locator(preset)).toHaveAttribute("aria-pressed", "true");
      /* THE CLOCK IS LEFT RUNNING AND GIVEN TIME TO SETTLE, which is the one thing that is not
         obvious and which MR2 paid for (its H13): a canvas renderer repaints on
         requestAnimationFrame and a paused clock never fires one, so a capture taken on a
         paused clock can show an undimmed map whose every opacity has already changed. The
         frozen START time is what keeps the header's clock and every age in a popup constant
         between the two runs. */
      await page.waitForTimeout(1500);
      await page.screenshot({ path: path.join(OUT, `${TAG}-${label}.png`) });
    });
  }
}

/* MR4 ROUND 2 ADDS THE KEY PANEL'S OWN FRAMES, and the reason is that the six frames above
   photograph it CLOSED. The Key is a disclosure at every width, so every capture this stage took
   shows the button and not the panel: the round that rewrote seven of the panel's rows is
   invisible in its own evidence. Measured rather than assumed, which is how this was found: the
   six frames above regenerate identically from the tree before this round and the tree after it.

   SO THESE ARE THE ROUND'S BEFORE AND AFTER, and the pair is the deliverable. Desktop shows the
   panel as a two-column grid, 375 shows it as one column with its own scroll, and both themes
   because the glyphs are literals on a plate that does not move and a reader should be able to
   check that claim rather than take it.

   THE PRESET IS STILL THE CITY'S, so the map under the panel is the same map the four frames
   above show and the only difference between a pair is the panel. */
for (const [name, viewport] of [
  ["key-desktop", { width: 1280, height: 720 }],
  ["key-375", { width: 375, height: 720 }],
]) {
  for (const theme of ["light", "dark"]) {
    const label = theme === "light" ? name : `${name}-dark`;
    test(`capture ${TAG} ${label} with the Key open`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await installMocks(page);
      await page.clock.install({ time: new Date(fx.FROZEN_MS) });
      await page.goto("/");
      await page.waitForFunction(
        () =>
          buses.size === 2 &&
          pathTrainRecords.size === 2 &&
          ferryBoatRecords.size === 3 &&
          railroads.size === 2 &&
          njtTrainRecords.size === 4 &&
          stationRegistry.length === 14 &&
          airtrainRouteLinesLayer.getLayers().length > 0,
        null,
        { timeout: 20_000 },
      );
      if (theme === "dark") {
        const button = page.locator("#theme-toggle");
        if (await button.isVisible()) await button.click();
        else await page.evaluate(() => applyTheme("dark"));
        await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      }
      await page.locator("#view-city").click();
      await expect(page.locator("#view-city")).toHaveAttribute("aria-pressed", "true");
      /* THE PANEL IS OPENED THROUGH THE CONTROL, never by removing the attribute, for the reason
         the theme is set through the button above: a frame taken past the control is a picture of
         a state a rider may not be able to reach. */
      await page.locator("#legend-toggle").click();
      await expect(page.locator("#legend")).toBeVisible();
      await expect(page.locator("#legend-toggle")).toHaveAttribute("aria-expanded", "true");
      await page.waitForTimeout(1500);
      await page.screenshot({ path: path.join(OUT, `${TAG}-${label}.png`) });
    });
  }
}
