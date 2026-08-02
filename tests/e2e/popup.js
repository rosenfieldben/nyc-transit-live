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

module.exports = { expectPopupState, popupOpen, REGISTRIES };
