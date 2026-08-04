// A4 ROUND 3: A SPEC THAT CLAIMS A STATE MUST PROVE IT IS IN ONE.
//
// THE FAILURE THIS RETIRES, WHICH THREE ADVERSARIAL ROUNDS FOUND THREE TIMES:
//
//   A9k  titled "closing a popup the rider was NOT in does not move them", and never closed
//        a popup: the rider was in the panel, so Escape took the PANEL rung and the code
//        under test was never entered. Its own comment claimed it caught a mutant that in
//        fact only A9l catches.
//   A9m  titled "the close button closes the popup that owns it, not whichever is current",
//        and never held two popups: openOn() auto-closes the previous one, so at click time
//        only one was live and the fix passed for the wrong reason.
//   A1y  added ".station-row" to the controls it requires to be keyboard reachable, in a
//        walk that never opens the panel, so zero station rows exist when the check runs.
//   the gate's "popup open with cross-link" state opened a popup with no cross-link, so the
//        page-wide scan had never examined one.
//
// Every one of them PASSED. None of them was measuring what its name said. The common shape
// is that reaching a state and asserting about a state are different acts, and only the
// second one was ever written down.
//
// So a state gets a WITNESS: the smallest observable fact that is true when the page is in
// that state and false otherwise. A spec asserts the witness BEFORE its own assertions, and
// the failure message names what is absent rather than reporting a confusing downstream
// mismatch twenty lines later.
//
// THE CONVENTION FOR NEW SPECS. If your test's title says "with X open", "from inside Y" or
// "while Z is showing", call expectState first with the witness for that state. If the
// witness you need is not here, add it here rather than inline: a witness written inline is
// a witness the next spec cannot reuse and the next reviewer cannot audit. Same reasoning as
// popup.js one directory over, which turned a trap that had cost three phases into a
// function nobody can get wrong.

const { expect } = require("@playwright/test");

/* THE WITNESSES. Each is a name, a question asked of the live page, and the sentence a spec
   author needs to read when it is false. They are deliberately small: a witness that asserts
   a lot is a second test, and a state a spec cannot reach should fail on the state, not on
   an assertion that happens to notice. */
const WITNESSES = {
  "panel open": {
    ask: () => !document.getElementById("stations-panel").hidden,
    absent: "the station panel is closed, so nothing inside it exists to assert about",
  },
  "panel closed": {
    ask: () => document.getElementById("stations-panel").hidden,
    absent:
      "the station panel is open, so a state named for the page WITHOUT it is scanning a " +
      "different page. Measured: the closed-panel scan reports 516 nodes and the skip link " +
      "as an incomplete; the open-panel scan reports 548 and no skip-link incomplete",
  },
  "panel results listed": {
    ask: () => document.querySelectorAll("#stations-results button.station-row").length > 0,
    absent: "the panel is showing no station rows, so any claim about a row is vacuous",
  },
  "panel detail open": {
    ask: () => !!document.querySelector("#stations-detail h3"),
    absent: "no station is selected, so the arrivals detail does not exist",
  },
  "one popup open": {
    ask: () => openPopupsOnMap().length === 1,
    absent: "exactly one popup must be open; found a different number, so which popup a " +
      "click or a keypress acts on is not the question this spec thinks it is",
  },
  "two popups open": {
    ask: () => openPopupsOnMap().length === 2,
    absent:
      "two popups must be live at once for this spec to mean anything. Leaflet auto-closes " +
      "the previous popup unless the NEW one is opened with autoClose:false AND the old one " +
      "was not opened through openOn(), which is how A9m spent a round asserting nothing",
  },
  "popup finished opening": {
    ask: () => {
      const popup = openPopupsOnMap()[0];
      const el = popup && popup.getElement ? popup.getElement() : null;
      return !!el && getComputedStyle(el).opacity === "1";
    },
    absent:
      "the popup is still fading in, so it is SEMI-TRANSPARENT and whatever is behind it " +
      "shows through. A contrast scan taken now measures the popup's text against a map tile " +
      "rather than against the popup, and only the muted colours fail, which reads like a " +
      "real contrast defect and is not one",
  },
  "popup has a cross-link": {
    ask: () => !!document.querySelector(".leaflet-popup-content .popup-crosslink"),
    absent:
      "the open popup carries no cross-link button, so this is not the popup the spec names. " +
      "Only a PLACED railroad train gets one (see isPlacedRailroad)",
  },
  "popup under the legend": {
    ask: () => {
      const popup = openPopupsOnMap()[0];
      const el = popup && popup.getElement ? popup.getElement() : null;
      const legend = document.getElementById("panel");
      if (!el || !legend) return false;
      const a = el.getBoundingClientRect();
      const b = legend.getBoundingClientRect();
      return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    },
    absent:
      "the open popup does not overlap the legend, so the app has no reason to move it and " +
      "a spec about whether it moves it is asserting nothing",
  },
  "banner showing": {
    ask: () => document.querySelectorAll(".alert-banner-row").length > 0,
    absent: "the alert banner has no rows, so it is not showing and cannot be covering anything",
  },
  "background inert": {
    ask: () => !!document.getElementById("map").closest("[inert]") || document.getElementById("map").inert,
    absent: "the map is not inert, so the mobile overlay is not in the state that makes it an overlay",
  },
  "focus inside the popup": {
    ask: () => {
      const popup = openPopupsOnMap()[0];
      const el = popup && popup.getElement ? popup.getElement() : null;
      return !!el && !!document.activeElement && el.contains(document.activeElement);
    },
    absent:
      "focus is not inside the popup, so any rung, restore or rescue that keys on the rider " +
      "being inside it will not run and the spec will pass without entering the code it names",
  },
  "focus inside the panel": {
    ask: () => {
      const panel = document.getElementById("stations-panel");
      return !!panel && !panel.hidden && !!document.activeElement && panel.contains(document.activeElement);
    },
    absent: "focus is not inside the station panel, so the panel rung will not be the one that runs",
  },
};

/* ASSERT THE STATE, THEN ASSERT ABOUT IT.
   Takes one witness name or several. Polls rather than reads once, because most of these
   states are reached by a click whose effect lands on a later task, and a witness that
   flaked would be worse than no witness at all. */
async function expectState(page, names, note = "") {
  for (const name of [].concat(names)) {
    const witness = WITNESSES[name];
    if (!witness) {
      throw new Error(
        `expectState: no witness named "${name}". Add it to tests/e2e/state.js rather than ` +
          `asserting inline, so the next spec can reuse it and the next reviewer can audit it.`,
      );
    }
    await expect
      .poll(async () => page.evaluate(witness.ask), { timeout: 5_000 })
      .toBe(true)
      .catch(() => {
        throw new Error(`state "${name}" was never reached${note ? ` (${note})` : ""}: ${witness.absent}`);
      });
  }
}

// The witness names, for the spec that asserts this file is wired into the suites rather
// than sitting unused next to them.
const witnessNames = () => Object.keys(WITNESSES);

module.exports = { expectState, witnessNames, WITNESSES };
