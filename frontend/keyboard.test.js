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

/* THE INVARIANT IS ABOUT ROUTERS, NOT ABOUT THE WORD "keydown", and it took two goes to say
   that precisely.

   The first version asserted exactly one keydown listener anywhere in the frontend. That was
   true when written and false one commit later, because the popup close button's Enter and
   Space handler is a keydown listener too. It went red and stayed red until the node tier was
   run again, which is its own lesson.

   The two are not the same kind of thing. The ladder ROUTES a key: it looks at the page,
   decides which surface a press belongs to, and stops the event. A second router would
   silently compete with it, and because the ladder captures, the loser would be invisible to
   any spec that drives the keyboard, which is exactly why this is asserted at the source. The
   close button's handler ROUTES nothing: it is bound to one control and restores the
   activation a browser synthesises for links but not for role=button anchors.

   The second version keyed the allowlist by FILE, and the adversarial round walked through
   the hole: it appended a second bubble-phase Escape router bound on WINDOW inside
   systems/shared.js, a file already excused, and 154 node tests and 8 escape specs stayed
   green. A file-keyed exemption excuses a file, not a listener, and this file's whole point
   is that WHAT THE LISTENER IS BOUND TO decides what kind of thing it is.

   So the key is now file AND receiver. Any page-level receiver (window, document, the body,
   the documentElement) is a router by construction and may appear exactly once, in the
   ladder. Anything else is a control's own activation and must be named below with its
   reason. The same round also found that `document?.addEventListener` slipped the old regex
   entirely, so the receiver pattern reads optional chaining too. */
const PAGE_LEVEL_RECEIVERS = new Set(["window", "document", "document.body", "document.documentElement", "self"]);

const ACTIVATION_KEYDOWNS = {
  "systems/shared.js button":
    "the Leaflet popup close button, whose href A4 removed (Enter and Space activation)",
};

function keydownBindings() {
  const found = [];
  for (const rel of FRONTEND_SOURCES) {
    const src = read(rel);
    // The receiver may be a dotted path and may use optional chaining; both are normalised
    // to a plain path so `document?.addEventListener` cannot hide behind punctuation.
    for (const m of src.matchAll(/([A-Za-z_$][\w$]*(?:\??\.[\w$]+)*)\??\.addEventListener\(\s*\n?\s*"keydown"/g)) {
      found.push({ file: rel, receiver: m[1].replace(/\?/g, "") });
    }
  }
  return found;
}

test("A4: exactly one keydown ROUTER in the frontend, and every other keydown is named", () => {
  const bindings = keydownBindings();

  // ANTI-VACUITY. A receiver pattern that stopped matching would report no routers and no
  // unnamed listeners, which reads exactly like a clean page.
  assert.ok(bindings.length >= 2, `the scan must find the frontend's keydown bindings, found ${bindings.length}`);

  const routers = bindings.filter((b) => PAGE_LEVEL_RECEIVERS.has(b.receiver));
  assert.deepEqual(
    routers.map((b) => `${b.file} ${b.receiver}`),
    ["map.js document"],
    "a keydown on a page-level receiver is a key ROUTER and there may be exactly one, the ladder",
  );

  const scoped = bindings.filter((b) => !PAGE_LEVEL_RECEIVERS.has(b.receiver)).map((b) => `${b.file} ${b.receiver}`);
  const unnamed = scoped.filter((key) => !(key in ACTIVATION_KEYDOWNS));
  assert.deepEqual(
    unnamed,
    [],
    "every keydown outside the ladder must be a control's own activation, named above with its reason",
  );
  // The allowlist is not allowed to outlive what it describes either: an entry whose binding
  // is gone is a stale exemption waiting to cover the next one.
  assert.deepEqual(
    Object.keys(ACTIVATION_KEYDOWNS).filter((key) => !scoped.includes(key)),
    [],
    "an entry in ACTIVATION_KEYDOWNS no longer matches a binding; delete it rather than leave it",
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
