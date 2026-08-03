// A4: THE ESCAPE LADDER'S CONSOLIDATION, asserted at the SOURCE.
//
// WHY SOURCE AND NOT BEHAVIOUR. The ladder is a document-level listener in the CAPTURE
// phase, so it decides and then stops the event before anything bound on a surface could
// see it. That is the right design, and it has one consequence for testing: a second
// Escape handler bound on the panel would be completely inert, and therefore completely
// invisible to any spec that drives the keyboard. Behaviour cannot tell "one door" from
// "one door plus a dead handler nobody can reach".
//
// It matters anyway, because the dead handler is a trap for the next reader: it looks like
// live ordering logic, and the day someone changes the ladder's phase it becomes live
// again with a different answer. So the invariant is asserted where it is visible.
//
// Same shape as markers.test.js, which pins "no system builds a marker outside the
// factory" by reading the sources rather than by driving a browser.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const read = (rel) => fs.readFileSync(path.join(__dirname, rel), "utf8");

// Every file that could plausibly bind a key handler: the page wiring, the panel, and
// every system file. Listed rather than globbed so a new file has to be added here
// deliberately, which is the moment to ask whether it should be binding keys at all.
const FRONTEND_SOURCES = [
  "map.js",
  "stations.js",
  "helpers.js",
  "systems/shared.js",
  "systems/buses.js",
  "systems/subway.js",
  "systems/railroad.js",
  "systems/airtrain.js",
  "systems/path.js",
  "systems/ferry.js",
];

test("A4: exactly one keydown listener in the whole frontend, and it is the ladder", () => {
  const found = [];
  for (const rel of FRONTEND_SOURCES) {
    const src = read(rel);
    for (const match of src.matchAll(/addEventListener\(\s*\n?\s*"keydown"/g)) {
      found.push(rel);
    }
  }
  assert.deepEqual(
    found,
    ["map.js"],
    `keydown must be bound exactly once, in the ladder. Found in: ${found.join(", ") || "nowhere"}`,
  );
});

test("A4: the ladder is bound on the document, in the capture phase", () => {
  const src = read("map.js");
  // The capture flag makes the ladder's decision unconditional rather than contingent on
  // whether another handler acted first. It is NOT what prevents a double close: moving the
  // ladder to the bubble phase was measured and the outcome was unchanged, because
  // Leaflet's own Escape handler declined to act in the state that would have collided.
  // Pinned anyway, because "decides first, always" is the property that was chosen, and it
  // is a one-word edit away from being lost silently.
  assert.match(
    src,
    /document\.addEventListener\(\s*\n?\s*"keydown",[\s\S]{0,4000}?\n\s*true,\n\s*\);/,
    "the ladder must be document.addEventListener(\"keydown\", handler, true)",
  );
});

test("A4: stations.js keeps the focus contract and gives up the key", () => {
  const src = read("stations.js");
  // The panel still owns closing and where focus goes; what it no longer owns is deciding
  // which surface a key closes. Both halves asserted, because deleting the handler while
  // also losing closeStationsPanel would pass a test that only looked for the absence.
  assert.ok(!/addEventListener\(\s*\n?\s*"keydown"/.test(src), "stations.js must not bind keydown");
  assert.ok(/function closeStationsPanel\(/.test(src), "stations.js still owns closing and focus return");
});
