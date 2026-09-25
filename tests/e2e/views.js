/* FOLLOW-UP 1: ONE PLACE THAT KNOWS HOW TO PUT THE MAP AT A PRESET.
   ==========================================================================

   The bus layer's zoom rule is the first thing on this map whose answer depends on which of
   the three presets a rider is at, so two files need to go there: pins.spec.js P6, which says
   what the rule may not change at each preset, and buszoom.spec.js D7, which says what it does.
   One helper rather than two, because the fifth defect shape is two implementations of one
   reader, and "is the map at the Rail preset yet" is a reader.

   TWO WAYS TO ARRIVE, AND THEY ARE NOT INTERCHANGEABLE.

   pressView is the rider's path: the preset's own button, then the fly. The fly is 0.8s of
   animation frames, and a paused clock (every boot in this suite that pins ages) runs none of
   them until it is told to, so the caller says which clock the page has. Arrival is asserted
   two ways that do not share a code path: the button's aria-pressed (mapIsAt, in shared.js) and
   the root's data-zoom (paintZoomBand), because the first says the map is at the preset and the
   second says the zoomend that repaints every band has run.

   placeView is the preset's DESTINATION without the journey, read out of the app's own
   VIEW_PRESETS table so it cannot name a zoom the buttons do not. It exists for one reading: a
   popup carries the age of its feed in seconds, so a pin that runs the clock forward between
   presets pins four different ages and not one popup. setView with animate:false moves the map
   and fires zoomend synchronously without advancing the clock at all. */
const { expect } = require("@playwright/test");

// The three presets, by the ids their buttons carry, in the order the view stack draws them.
const PRESET_IDS = ["view-city", "view-rail", "view-region"];

// The zoom a preset names, asked of the page rather than written down here.
const presetZoom = (page, id) =>
  page.evaluate((key) => VIEW_PRESETS.find((preset) => preset.id === key).zoom, id);

async function atZoom(page, zoom) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.getAttribute("data-zoom")), {
      message: `the root must say data-zoom="${zoom}" once the map has arrived`,
    })
    .toBe(String(zoom));
}

// Press a preset's button and wait for the map to be there. `clock` is "paused" when the page's
// clock was installed and paused (the frozen-clock boot), and "running" otherwise.
async function pressView(page, id, { clock = "paused" } = {}) {
  const zoom = await presetZoom(page, id);
  await page.locator(`#${id}`).click();
  if (clock === "paused") await page.clock.runFor(1200);
  await expect(page.locator(`#${id}`)).toHaveAttribute("aria-pressed", "true");
  await atZoom(page, zoom);
  return zoom;
}

// A preset's destination without the fly and without moving the clock (see the header).
async function placeView(page, id) {
  const zoom = await page.evaluate((key) => {
    const preset = VIEW_PRESETS.find((p) => p.id === key);
    map.setView(preset.center, preset.zoom, { animate: false });
    return preset.zoom;
  }, id);
  await atZoom(page, zoom);
  return zoom;
}

module.exports = { PRESET_IDS, presetZoom, pressView, placeView };
