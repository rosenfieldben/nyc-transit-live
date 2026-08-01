// SCRATCH: same boundary scenario at a NARROW viewport, where the page has no
// horizontal overflow at all, to show the scroll is not an artifact of the docked layout.
const { test, expect } = require("@playwright/test");
const { installMocks } = require("./mock");
const fx = require("./fixtures/api");

const state = (page) =>
  page.evaluate(() => {
    const c = map.getContainer();
    const cr = c.getBoundingClientRect();
    const p = map._popup && map._popup.getElement();
    const el = document.activeElement;
    return {
      docOverflowPx: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      windowScrollX: window.scrollX,
      containerScrollLeft: c.scrollLeft,
      container: [Math.round(cr.x), Math.round(cr.right)],
      popup: p ? [Math.round(p.getBoundingClientRect().x), Math.round(p.getBoundingClientRect().right)] : null,
      tilePaneX: Math.round(document.querySelector(".leaflet-tile-pane").getBoundingClientRect().x),
      active: el === document.body ? "BODY" : el.tagName + "." + String(el.className || "").trim(),
    };
  });

for (const mode of ["helper", "raw-baseline"]) {
  test(`NARROW-${mode}`, async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 720 });
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
    console.log(`${mode} before zoom: ` + JSON.stringify(await state(page)));

    const box = await page.locator("#map").boundingBox();
    await page.mouse.move(box.x + 25, box.y + box.height - 25);
    for (let i = 0; i < 3; i++) {
      await page.mouse.wheel(0, -400);
      await page.clock.runFor(250);
    }
    console.log(`${mode} after zoom:  ` + JSON.stringify(await state(page)));

    await page.clock.runFor(1200);
    console.log(`${mode} at poll:     ` + JSON.stringify(await state(page)));
    await page.clock.runFor(800);
    console.log(`${mode} settled:     ` + JSON.stringify(await state(page)));
  });
}
