// A3: ONE PLACE THAT KNOWS HOW TO ASK WHETHER A POPUP IS OPEN.
//
// THE TRAP THIS RETIRES. A closing Leaflet popup is not removed when it closes: it stays
// in the DOM for the length of its fade. Under this suite's paused clock the fade never
// runs, so the corpse never leaves at all, and three separate things a spec might
// reasonably ask all lie about it:
//
//   document.querySelectorAll(".leaflet-popup")   still finds the closed one
//   map._popup                                    still references the closed one
//   locator(".leaflet-popup-content")             matches TWO nodes and dies on strict mode
//
// Only the MARKER tells the truth, through Leaflet's own isPopupOpen(). A2 measured all
// four behaviours and wrote them into systems/shared.js, and the documentation did not
// stop it happening: this trap has now cost a debugging round in A1, A2 and A3, the last
// time in a spec written by someone who had just finished reading the comment about it.
//
// So it stops being knowledge and becomes a function. A spec that calls this cannot get
// it wrong, and a spec that hand-rolls the query is now visibly doing something the
// harness already does, which is the point: documentation asks people to remember, a
// helper asks them to type less.

const { expect } = require("@playwright/test");

// The frontend is a buildless ordered-script page, so its registries are top-level
// `const` bindings in global SCOPE, which is not the same as properties on globalThis.
// They therefore cannot be looked up by string; the object below is built inside the
// page where the lexical bindings are in scope, and the caller names one.
//
// stationRegistry is an ARRAY keyed by `entry.key`, unlike the vehicle Maps. That
// difference is handled here once rather than at every call site.
const REGISTRIES = ["buses", "trains", "railroads", "pathTrainRecords", "ferryBoatRecords", "stationRegistry"];

function popupOpen(page, registry, key) {
  return page.evaluate(
    ([reg, k]) => {
      const sources = {
        buses,
        trains,
        railroads,
        pathTrainRecords,
        ferryBoatRecords,
        stationRegistry,
      };
      const source = sources[reg];
      if (!source) return `unknown registry: ${reg}`;
      const record = source instanceof Map ? source.get(k) : source.find((row) => row.key === k);
      if (!record || !record.marker) return `no marker for ${reg}/${k}`;
      return record.marker.isPopupOpen();
    },
    [registry, key],
  );
}

/**
 * Assert a marker's popup is open (or closed), asking Leaflet rather than the document.
 *
 * Polls, because opening a popup can be asynchronous from the caller's point of view
 * (a bound content function, an autopan, a poll-driven re-render), and a bare read
 * would be a race the DOM query at least failed loudly on.
 *
 * A missing marker or an unknown registry surfaces as a STRING rather than false, so a
 * typo'd key fails with "no marker for buses/nope" instead of quietly asserting that a
 * popup which cannot exist is closed. That distinction is the whole reason this returns
 * the diagnostic instead of a boolean.
 */
async function expectPopupState(page, { registry, key }, open, options = {}) {
  if (!REGISTRIES.includes(registry)) {
    throw new Error(`expectPopupState: unknown registry ${registry}; expected one of ${REGISTRIES.join(", ")}`);
  }
  await expect
    .poll(async () => popupOpen(page, registry, key), { timeout: 5_000, ...options })
    .toBe(open);
}

/* MR5: AND ONE PLACE THAT KNOWS WHICH MARK IS WHICH, for the same reason.
   ==========================================================================

   The three things below were written in pins.spec.js and are now needed by two files:
   pins.spec.js holds what a popup must NOT stop saying, popups.spec.js holds what the popup
   CHROME must do, and both have to open the same fourteen surfaces in the same world. A second
   copy of the marker table would be a second answer to "which mark is the LIRR train", and the
   two would drift the first time a fixture id moved.

   They are MOVED rather than duplicated, and the moved text is unchanged: the comments below are
   pins.spec.js's own, including the measured reasons the settle loop runs on the driver side and
   the map's own closePopup() is not enough. */

// The marker table, sent into the page by name. page.evaluate runs a function's SOURCE in
// the page's global scope, so the app's top-level consts are in scope the way they are in
// the console; nothing here closes over this file.
const MARKER_TABLE = `{
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

const inPage = (body) => new Function("which", `const MARKERS = ${MARKER_TABLE}; ${body}`);

/* ONE POPUP AT A TIME, AND READ THROUGH THE MARKER, not through the document. This app
   can hold a vehicle popup and a station popup open together (state.js carries a "two
   popups open" witness for exactly that), so `.leaflet-popup-content` is not a unique
   selector and the previous pin's popup would still be on screen. Asking the marker for
   its own popup element cannot pick up a neighbour's, and map.closePopup() alone cannot
   close the second one because only one of them is the map's "current" popup.

   THE SETTLE LOOP RUNS FROM HERE, NOT IN THE PAGE, and that is not a style choice: the
   boot pauses the clock (page.clock.pauseAt), so a setTimeout inside the page never fires
   and an in-page wait deadlocks until the test times out. Measured, that is exactly what
   happened: every station pin sat for the full timeout. Each round trip below is real
   time on the driver's side, which is what lets a station's arrivals fetch resolve, and
   the clock is advanced between reads so the app's own timers get their turn too. */
const closeAllPopups = (page) =>
  page.evaluate(() => {
    map.closePopup();
    for (const record of [...trains.values(), ...railroads.values(), ...buses.values(),
      ...pathTrainRecords.values(), ...ferryBoatRecords.values(), ...njtTrainRecords.values()]) {
      record.marker.closePopup();
    }
    for (const entry of stationRegistry) if (entry.marker) entry.marker.closePopup();
  });

const STOCK_SURFACES = [
  "subway train", "subway station", "bus",
  "lirr train", "lirr station", "mnr train", "mnr station",
  "njt train", "njt station", "path train", "path station",
  "ferry boat", "ferry dock", "airtrain station",
];

module.exports = {
  expectPopupState,
  popupOpen,
  REGISTRIES,
  MARKER_TABLE,
  inPage,
  closeAllPopups,
  STOCK_SURFACES,
};
