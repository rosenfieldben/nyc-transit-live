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
const PAGE_LEVEL_RECEIVERS = new Set([
  "window",
  "document",
  "document.body",
  "document.documentElement",
  "self",
  "globalThis",
]);

const ACTIVATION_KEYDOWNS = {
  "systems/shared.js button": "the Leaflet popup close button, whose href A4 removed (Enter and Space activation)",
};

/* ROUND 3 WENT THROUGH THE SCAN A THIRD TIME, and the lesson has stopped being about
   regexes. Round 1 keyed the allowlist by file; round 2 keyed it by receiver and read one
   quote style; round 3 wrote the router with a TEMPLATE LITERAL, as document.onkeydown, and
   behind an alias, and also pointed out that reading the file as raw text made a COMMENT
   mentioning addEventListener into a phantom binding.
   Three rules now, and they are what a scanner owes rather than what one regex can do.
   ONE: read code, not prose. Comments and string literals are removed first, so neither a
   phantom nor a hiding place exists.
   TWO: every spelling of "bind a keydown" is recognised, including the assignment form,
   because onkeydown is a router with different punctuation.
   THREE: anything matching addEventListener that this scanner cannot CLASSIFY is a loud
   failure. A form it has never seen is exactly the case where silence is worst.
   The corpus below is the standing record of every spelling an adversarial round invented,
   so the next extension starts from a failing example. */

/* COMMENTS OUT, STRINGS KEPT BUT MAPPED. The first attempt at this blanked the inside of
   every string literal, which removed the phantom AND the event name the scan needs to read
   out of the very same call: addEventListener("keydown") became addEventListener("       ").
   The two concerns are separate. Comments are deleted outright, because nothing in one is
   ever a real binding. String literals STAY, so an event name is still readable, and a match
   is rejected instead when it lies INSIDE one, which is what a phantom actually is. */
function stripComments(src) {
  return src.replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, (m) => " ".repeat(m.length));
}

function stringRanges(code) {
  const ranges = [];
  for (const m of code.matchAll(/"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`/g)) {
    ranges.push([m.index, m.index + m[0].length]);
  }
  return ranges;
}

const insideAny = (ranges, index) => ranges.some(([from, to]) => index > from && index < to);

// Aliases of a page-level object, so `const d = document; d.addEventListener(...)` is not a
// hiding place. Only the direct form is recognised, which is the honest limit: an alias
// built by a function call is not something this scanner claims to see, and the
// unclassified rule below is what covers the rest.
function pageLevelAliases(code) {
  const aliases = new Set();
  for (const m of code.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$.]*)\s*;/g)) {
    if (PAGE_LEVEL_RECEIVERS.has(m[2])) aliases.add(m[1]);
  }
  return aliases;
}

function scanKeydowns(src) {
  const code = stripComments(src);
  const strings = stringRanges(code);
  const aliases = pageLevelAliases(code);
  const bindings = [];
  const unclassified = [];

  // The assignment form. onkeydown is a router with different punctuation, and nothing in
  // the addEventListener pattern would ever have seen it.
  for (const m of code.matchAll(/([A-Za-z_$][\w$]*(?:\.[\w$]+)*)\.onkeydown\s*=/g)) {
    if (insideAny(strings, m.index)) continue;
    bindings.push({ receiver: m[1], form: "onkeydown" });
  }

  // Every addEventListener call, then classified. Splitting recognition from classification
  // is what makes an unknown spelling loud instead of absent.
  for (const m of code.matchAll(/(?:([^\s;{}()]+)\s*\.\s*)?\baddEventListener\s*\(([^,)]*)[,)]/g)) {
    if (insideAny(strings, m.index)) continue; // a call written inside a string is prose
    const event = m[2].trim();
    const quoted = /^(["'`])([A-Za-z]+)\1$/.exec(event);
    if (!quoted) {
      // A computed or variable event name. It may or may not be keydown, and this scanner
      // cannot tell, which is exactly the case it must not pass over in silence.
      unclassified.push(`addEventListener(${event.slice(0, 40)})`);
      continue;
    }
    if (quoted[2] !== "keydown") continue;
    const raw = (m[1] || "window").replace(/\?/g, "");
    if (!/^[A-Za-z_$][\w$]*(?:\.[\w$]+)*$/.test(raw)) {
      unclassified.push(`addEventListener on receiver ${raw.slice(0, 40)}`);
      continue;
    }
    bindings.push({ receiver: aliases.has(raw) ? "document" : raw, form: "addEventListener" });
  }
  return { bindings, unclassified };
}

function keydownBindings() {
  const found = [];
  const unclassified = [];
  for (const rel of FRONTEND_SOURCES) {
    const scan = scanKeydowns(read(rel));
    for (const b of scan.bindings) found.push({ file: rel, ...b });
    for (const u of scan.unclassified) unclassified.push(`${rel}: ${u}`);
  }
  return { found, unclassified };
}

test("A4: exactly one keydown ROUTER in the frontend, and every other keydown is named", () => {
  const { found: bindings, unclassified } = keydownBindings();

  // ANTI-VACUITY. A receiver pattern that stopped matching would report no routers and no
  // unnamed listeners, which reads exactly like a clean page.
  assert.ok(bindings.length >= 2, `the scan must find the frontend's keydown bindings, found ${bindings.length}`);

  // AND ANYTHING IT CANNOT CLASSIFY IS LOUD. A spelling this scanner has never seen is the
  // case where staying quiet is worst, because that is precisely when nobody is looking.
  assert.deepEqual(
    unclassified,
    [],
    "an addEventListener call this scanner cannot classify. Teach scanKeydowns the form, with " +
      "a corpus entry, rather than leaving a binding it cannot see",
  );

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
  assert.deepEqual(
    Object.keys(ACTIVATION_KEYDOWNS).filter((key) => !scoped.includes(key)),
    [],
    "an entry in ACTIVATION_KEYDOWNS no longer matches a binding; delete it rather than leave it",
  );
});

/* THE SPELLING CORPUS: every form an adversarial round invented, with what the scan must
   make of it. Extending the scanner starts here, with a failing example. */
const KEYDOWN_CORPUS = [
  ['document.addEventListener("keydown", f, true);', ["document"], [], "round 1's form, the ladder itself"],
  ["document.addEventListener('keydown', f);", ["document"], [], "round 2: single quotes"],
  ["document.addEventListener(`keydown`, f);", ["document"], [], "round 3: a template literal"],
  ["document?.addEventListener('keydown', f);", ["document"], [], "round 2: optional chaining"],
  ['addEventListener("keydown", f);', ["window"], [], "round 2: bare, implicit window in a classic script"],
  ["document.onkeydown = f;", ["document"], [], "round 3: the assignment form"],
  ["window.onkeydown = f;", ["window"], [], "round 3: the assignment form on window"],
  ["globalThis.addEventListener('keydown', f);", ["globalThis"], [], "round 3: globalThis"],
  ["const d = document; d.addEventListener('keydown', f);", ["document"], [], "round 3: an alias"],
  ['button.addEventListener("keydown", f);', ["button"], [], "a control's own activation, not a router"],
  ['// document.addEventListener("keydown", f) in a comment', [], [], "round 3: a phantom in prose"],
  ["const s = 'document.addEventListener(keydown';", [], [], "round 3: a phantom in a string"],
  ["el.addEventListener(EVENT_NAME, f);", [], ["unclassified"], "a computed event name cannot be judged"],
];

test("A4: the keydown scanner reads every spelling the rounds invented", () => {
  const wrong = [];
  for (const [source, receivers, flags, why] of KEYDOWN_CORPUS) {
    const scan = scanKeydowns(source);
    const got = scan.bindings.map((b) => b.receiver);
    if (JSON.stringify(got) !== JSON.stringify(receivers)) {
      wrong.push(`${why}: expected receivers ${JSON.stringify(receivers)}, got ${JSON.stringify(got)}`);
    }
    const wantsUnclassified = flags.includes("unclassified");
    if (wantsUnclassified !== scan.unclassified.length > 0) {
      wrong.push(`${why}: expected unclassified=${wantsUnclassified}, got ${JSON.stringify(scan.unclassified)}`);
    }
  }
  assert.deepEqual(wrong, [], "the keydown scanner has drifted from the corpus of spellings it must read");
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
