// SCRATCH - skeptic probe of the focus() scroll claim. Deleted before exit.
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const fx = require("./fixtures/api");

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

const snap = (page, tag) =>
  page.evaluate((t) => {
    const m = document.getElementById("map").getBoundingClientRect();
    const p = document.getElementById("stations-panel").getBoundingClientRect();
    const popupRoot = map._popup && map._popup.getElement();
    const pr = popupRoot ? popupRoot.getBoundingClientRect() : null;
    const el = document.activeElement;
    return `${t} scrollX=${window.scrollX} map=[${Math.round(m.left)},${Math.round(m.right)}] panel=[${Math.round(
      p.left,
    )},${Math.round(p.right)}] mapScrollLeft=${document.getElementById("map").scrollLeft} popup=${
      pr ? `[${Math.round(pr.left)},${Math.round(pr.right)}]` : "none"
    } active=${el ? el.tagName + "." + el.className : "NONE"}`;
  }, tag);

async function stepwise(page) {
  await installMocks(page);
  await open(page);
  const out = [await snap(page, "loaded")];
  const placed = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id != null) return key;
    return null;
  });
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), placed);
  out.push(await snap(page, "afterOpenPopup"));
  await page.locator(".popup-crosslink").focus();
  out.push(await snap(page, "afterCrosslinkFocus"));
  return out;
}

test("probe D: stepwise, where does the scroll first appear", async ({ page }) => {
  const out = await stepwise(page);
  console.log(out.join("\n"));
});

test("probe E: fair differential - scroll reset to 0, popup pushed off viewport, helper", async ({ page }) => {
  const out = await stepwise(page);
  // Push popup into the off-viewport strip, then put the page back where the rider
  // would have it (panel visible) before the poll fires.
  const push = await page.evaluate(() => {
    const popup = map._popup;
    const pt = map.latLngToContainerPoint(popup.getLatLng());
    map.panBy([-(1120 - pt.x), 0], { animate: false });
    window.scrollTo(0, 0);
    return map.latLngToContainerPoint(popup.getLatLng()).x;
  });
  out.push(`pushed containerX=${push}`);
  out.push(await snap(page, "beforePoll"));
  await page.clock.runFor(16_000);
  await page.waitForTimeout(300);
  out.push(await snap(page, "afterPollHELPER"));
  console.log(out.join("\n"));
});

test("probe F: fair differential - same, raw popup.update() stub", async ({ page }) => {
  const out = await stepwise(page);
  await page.evaluate(() => {
    window.updatePopupKeepingFocus = (marker) => {
      const p = marker.getPopup && marker.getPopup();
      if (p) p.update();
    };
  });
  const push = await page.evaluate(() => {
    const popup = map._popup;
    const pt = map.latLngToContainerPoint(popup.getLatLng());
    map.panBy([-(1120 - pt.x), 0], { animate: false });
    window.scrollTo(0, 0);
    return map.latLngToContainerPoint(popup.getLatLng()).x;
  });
  out.push(`pushed containerX=${push}`);
  out.push(await snap(page, "beforePoll"));
  await page.clock.runFor(16_000);
  await page.waitForTimeout(300);
  out.push(await snap(page, "afterPollRAW"));
  console.log(out.join("\n"));
});

test("probe G: real keyboard route - Tab into popup from the page, no programmatic focus", async ({ page }) => {
  await installMocks(page);
  await open(page);
  const out = [await snap(page, "loaded")];
  const placed = await page.evaluate(() => {
    for (const [key, record] of railroads) if (record.latest.stop_id != null) return key;
    return null;
  });
  // A rider opens the popup by clicking the marker (mouse), which is the only way a
  // vehicle popup opens: markers are aria-hidden / not keyboard reachable per index.html.
  await page.evaluate((key) => railroads.get(key).marker.openPopup(), placed);
  out.push(await snap(page, "afterOpenPopup"));
  // Then Tab. Where does the browser send it, and does anything scroll?
  for (let i = 0; i < 4; i++) {
    await page.keyboard.press("Tab");
    out.push(await snap(page, `tab${i + 1}`));
  }
  console.log(out.join("\n"));
});
