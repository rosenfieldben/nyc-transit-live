/* MR5 capture harness. Temporary in MR2's, MR3's and MR4's sense: it lives here rather than in
   tests/e2e/ so it is not part of the suite, and it is kept because it is what makes the pair
   reproducible rather than a pair of pictures. MEASURING.md beside it says how to run it.

   THIS STAGE'S SUBJECT IS THE POPUP, so the frames are popups rather than maps. A full-page frame
   photographs a popup at about a fifth of its height and MR4's six frames are already the map: what
   MR5 changes is what a rider reads after they click, and the honest evidence for that is the popup
   itself, one per system, at the size it actually renders.

   FOURTEEN SURFACES, TWO THEMES, CLIPPED TO THE POPUP. The clip is `.leaflet-popup` rather than the
   page, so each frame is the wrapper, its tip and its ink edge at 1:1. Both themes because the
   surface, the ink, the hairlines and the footer's square all come from tokens, and a popup whose
   contrast was wrong in one theme would look right in the other.

   AND TWO FULL FRAMES AT 375, which is the one thing a clip cannot show: the popup against the
   chrome it has to clear, with ruling S3's clamped autopan having run. */
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const fx = require("./fixtures/api");
const path = require("node:path");

const OUT =
  process.env.MR5_OUT || path.join(__dirname, "..", "..", "docs", "reviews", "map-redesign", "mr5");
const TAG = process.env.MR5_TAG || "after";

/* The same fourteen surfaces pins.spec.js pins, by the same table, so a frame and a golden are of
   one thing. Copied rather than imported because this file is run from tests/e2e/ as a temporary
   spec and MEASURING.md's second form runs it inside a worktree of the BEFORE tree, where
   tests/e2e/popup.js does not have this table yet. */
const MARKERS = `{
  "subway train": () => trains.get("sub-1").marker,
  "subway station": () => stationRegistry.find((e) => e.key === "subway|127").marker,
  "bus": () => buses.get("MTA NYCT_101").marker,
  "lirr train": () => railroads.get("LIRR|lirr-placed-1").marker,
  "lirr station": () => stationRegistry.find((e) => e.key === "LIRR|12").marker,
  "mnr train": () => railroads.get("MNR|mnr-gps-1").marker,
  "mnr station": () => stationRegistry.find((e) => e.key === "MNR|1").marker,
  "njt train": () => njtTrainRecords.get("NJ_3800").marker,
  "njt station": () => stationRegistry.find((e) => e.key === "NJT|109").marker,
  "path train": () => pathTrainRecords.get("p-1").marker,
  "path station": () => stationRegistry.find((e) => e.key === "PATH|26734").marker,
  "ferry boat": () => ferryBoatRecords.get("H1").marker,
  "ferry dock": () => stationRegistry.find((e) => e.key === "ferry|18").marker,
  "airtrain station": () => stationRegistry.find((e) => e.key === "airtrain|A").marker,
}`;

const SURFACES = [
  "subway train", "subway station", "bus",
  "lirr train", "lirr station", "mnr train", "mnr station",
  "njt train", "njt station", "path train", "path station",
  "ferry boat", "ferry dock", "airtrain station",
];

const slug = (name) => name.replace(/[^a-z0-9]+/gi, "-");

async function bootMap(page, theme) {
  await installMocks(page);
  await page.clock.install({ time: new Date(fx.FROZEN_MS) });
  await page.goto("/");
  /* EVERY FAMILY LOADED, ASSERTED RATHER THAN SLEPT ON, because a half-built map photographs as a
     design decision and a popup opened before its station registry exists photographs as an empty
     one. These are the same counts MR4's harness waits for, plus the registry the station popups
     are found through. */
  await page.waitForFunction(
    () =>
      trains.size > 0 &&
      buses.size === 2 &&
      pathTrainRecords.size === 2 &&
      ferryBoatRecords.size === 3 &&
      railroads.size === 2 &&
      njtTrainRecords.size === 4 &&
      stationRegistry.length === 14,
    null,
    { timeout: 20_000 },
  );
  if (theme === "dark") {
    const button = page.locator("#theme-toggle");
    if (await button.isVisible()) await button.click();
    else await page.evaluate(() => applyTheme("dark"));
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  }
}

/* THE SETTLE LOOP IS THE PINS' OWN, for the reason pins.spec.js states: a station popup fetches its
   arrivals, the boot pauses the clock so an in-page timer never fires, and a frame taken before the
   fetch resolves is a picture of "Loading arrivals". Each turn advances the paused clock so the
   app's own timers get theirs. */
async function openSettled(page, which) {
  await page.evaluate(new Function("which", `const MARKERS = ${MARKERS}; MARKERS[which]().openPopup();`), which);
  let previous = null;
  for (let i = 0; i < 80; i++) {
    const now = await page.locator(".leaflet-popup-content").first().innerHTML();
    if (now === previous && !/Loading arrivals/.test(now)) return;
    previous = now;
    await page.clock.runFor(100);
  }
  throw new Error(`${which}: the popup never settled`);
}

for (const theme of ["light", "dark"]) {
  test(`capture ${TAG} the fourteen popups (${theme})`, async ({ page }) => {
    // Desktop, because a popup at 375 is capped by the viewport (A6e) and this frame is about the
    // popup's own metrics: the 220px floor, the 320 cap, the 14x16 margin, the ink edge.
    await page.setViewportSize({ width: 1280, height: 900 });
    await bootMap(page, theme);
    for (const which of SURFACES) {
      await openSettled(page, which);
      const popup = page.locator(".leaflet-popup").first();
      await expect(popup).toBeVisible();
      /* THE FRAME WAITS FOR THE MAP TO STOP MOVING, and this is the one thing the paused clock does
         not give for free: Leaflet's fade and its pan are CSS transitions on the compositor's own
         clock, so a popup that has just opened (and that ruling S3's padding has just panned clear
         of the chrome) is still in flight when the shot is taken. Playwright said so in as many
         words, "element is not stable". Real milliseconds here, plus the disabled-animations flag,
         which freezes any transition still running at the moment of capture. */
      await page.waitForTimeout(500);
      const suffix = theme === "light" ? "" : "-dark";
      await popup.screenshot({
        animations: "disabled",
        path: path.join(OUT, `${TAG}-popup-${slug(which)}${suffix}.png`),
      });
      await page.evaluate(new Function("which", `const MARKERS = ${MARKERS}; MARKERS[which]().closePopup();`), which);
    }
  });

  test(`capture ${TAG} a popup against the chrome at 375 (${theme})`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 720 });
    await bootMap(page, theme);
    // A STATION BOARD, because it is the tall popup: the one ruling S3's clamped padding has to
    // move clear of the header and the one a rider meets most.
    await openSettled(page, "subway station");
    await page.waitForTimeout(1200);
    await page.screenshot({
      path: path.join(OUT, `${TAG}-chrome-375${theme === "light" ? "" : "-dark"}.png`),
    });
  });
}
