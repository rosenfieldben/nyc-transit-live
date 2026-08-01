// SCRATCH: re-confirm against current HEAD (round 3's helper).
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const fx = require("./fixtures/api");

const state = (page) =>
  page.evaluate(() => {
    const c = map.getContainer();
    const cr = c.getBoundingClientRect();
    const p = map._popup && map._popup.getElement();
    const el = document.activeElement;
    const panel = document.getElementById("stations-panel");
    return {
      windowScrollX: window.scrollX,
      container: [Math.round(cr.x), Math.round(cr.right)],
      panel: panel ? [Math.round(panel.getBoundingClientRect().x), Math.round(panel.getBoundingClientRect().right)] : null,
      popup: p ? [Math.round(p.getBoundingClientRect().x), Math.round(p.getBoundingClientRect().right)] : null,
      active: el === document.body ? "BODY" : el.tagName + "." + String(el.className || "").trim(),
    };
  });

for (const mode of ["helper", "raw-baseline"]) {
  test(`FINAL-${mode}`, async ({ page }) => {
    await installMocks(page);
    await page.clock.install({ time: new Date(fx.FROZEN_MS) });
    await page.clock.pauseAt(new Date(fx.FROZEN_MS));
    await page.goto("/");
    await expect
      .poll(async () => page.evaluate(() => document.querySelectorAll(".leaflet-marker-icon").length), {
        timeout: 15_000,
      })
      .toBeGreaterThan(5);

    if (mode === "raw-baseline") {
      await page.evaluate(() => {
        window.updatePopupKeepingFocus = (marker) => {
          const popup = marker.getPopup && marker.getPopup();
          if (popup) popup.update();
        };
      });
    }

    const key = await page.evaluate(() => {
      for (const [k, r] of railroads) if (r.latest.stop_id != null) return k;
      return null;
    });
    await page.evaluate((k) => {
      const m = railroads.get(k).marker;
      map.setView(m.getLatLng(), map.getZoom(), { animate: false });
      m.openPopup();
    }, key);
    await expect(page.locator(".popup-crosslink")).toHaveCount(1);
    await page.locator(".popup-crosslink").focus();
    await expect(page.locator(".popup-crosslink")).toBeFocused();

    await page.clock.runFor(13_800);
    console.log(`${mode} parked:  ` + JSON.stringify(await state(page)));

    const box = await page.locator("#map").boundingBox();
    await page.mouse.move(box.x + 30, box.y + box.height - 30);
    for (let i = 0; i < 3; i++) {
      await page.mouse.wheel(0, -400);
      await page.clock.runFor(250);
    }
    console.log(`${mode} zoomed:  ` + JSON.stringify(await state(page)));

    await page.clock.runFor(1200);
    await page.clock.runFor(800);
    console.log(`${mode} settled: ` + JSON.stringify(await state(page)));
  });
}
