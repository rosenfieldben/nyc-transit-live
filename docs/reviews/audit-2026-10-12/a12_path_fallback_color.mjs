#!/usr/bin/env node
/*
 * A12 (Low): "PATH keeps fallback colors after delayed metadata."
 * Reproduction of the 2026-10-12 audit finding, recorded in docs/reviews/audit-2026-10-12.md.
 *
 * THE AUDIT'S CLAIM, quoted from the report's "Additional confirmed narrow defects" table:
 *
 *   Evidence cell: "frontend/systems/path.js:289 refreshes icons when route ID changes, not
 *   when the resolved color changes. Reproduced gray remaining after metadata supplied the
 *   route's red. Already acknowledged in code/tests."
 *
 *   Correction cell: "Include resolved color in the marker visual key, preserving marker
 *   identity. Test delayed route loading and the actual icon color."
 *
 *   Cited code link (as text, not an assertion):
 *   https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/frontend/systems/path.js#L289
 *
 * WHAT THIS SCRIPT MEASURES. The cold start the A2c e2e spec drives in a browser, on a virtual
 * clock: the page boots, the PATH route table's first request answers 503 and its loader backs
 * off, the first PATH poll lands meanwhile, production's retry later lands the table, and the
 * page keeps polling. The drive is by EVENTS, not by fixed times: the clock runs until the
 * table has landed, then until each further poll has landed (bounded at ten virtual minutes
 * per step), so neither the retry backoff nor the poll interval is pinned. After every step
 * the COMPUTED FILL of each train's diamond is read off the element Leaflet rendered. Three
 * trains:
 *   p-1, p-2  created BEFORE the route table landed (the finding),
 *   p-3       created AFTER it landed, on the same route (the control),
 * and a fourth post-table poll in which p-2's route_id changes, which is the one thing the
 * re-icon gate looks at (the mechanism).
 *
 * WHAT IS PRODUCTION CODE HERE. Every script frontend/index.html loads, read from index.html
 * itself and executed unmodified, in its order, in one node:vm context (map.js last, whose own
 * kickoff runs retryUntil(loadPathRoutes, ...), refreshAll(), the poll interval and the
 * animation frames). This script calls NO production function and reads no production
 * variable: it only moves the clock and reads the page. So every production path that could
 * re-skin a diamond is live: the static loader and its retry, refreshSource -> applyPath, the
 * poll tail and its staleTreatments sweeps, and the animation tick. The icon markup is turned
 * into the marker's element by the REAL L.DivIcon.prototype.createIcon from the vendored
 * Leaflet (1.9.4), loaded in a second vm context. The stylesheets index.html links are read and
 * evaluated as a cascade (below).
 *
 * WHAT IS INJECTED (everything not production code):
 *   1. A DOM. getElementById creates on demand (the F12 stub's approach). The markup Leaflet
 *      renders into a marker element (and any markup later written into that subtree, and
 *      any element created with createElementNS) is PARSED into real nodes, with attributes,
 *      a CSSStyleDeclaration model (property assignment, setProperty with priority,
 *      removeProperty, cssText, and the style attribute all write the same declarations),
 *      classList over className, dataset over data-* attributes, outerHTML, replaceWith,
 *      insertAdjacentHTML, closest and the usual tree accessors. Other elements keep markup
 *      as a string (the 09-05 behaviour) and answer a bare-tag query with a cached stand-in.
 *      The selector engine implements compound selectors (tag, #id, .class, [attr] with every
 *      operator, :not/:is/:where/:root) with descendant and child combinators, and THROWS on
 *      anything else, naming it (the 09-05 F11/F12 rule).
 *   2. The real page's ancestry for a marker: element -> marker pane -> map pane -> #map ->
 *      <main> -> <body> -> <html>, as Leaflet and index.html build it, with the marker element
 *      a child of its pane (so a document-wide query finds the diamonds, as in a browser).
 *      <html> and #map carry the attributes index.html writes on them. Leaflet's own classes
 *      (leaflet-marker-icon from the real createIcon, leaflet-zoom-animated and
 *      leaflet-interactive as Marker._initIcon adds them) are on the marker element.
 *   3. A Leaflet stub for everything EXCEPT the icon rendering: the map, panes, layer groups,
 *      polylines (their options are recorded, which is how the route colour the static loader
 *      drew is measured), and L.marker. The marker models the Leaflet 1.9.4 behaviour this
 *      finding turns on: on add, and on setIcon while on the map, it calls
 *      options.icon.createIcon(previousElement), as Marker._initIcon does, so the element is
 *      re-rendered only when production calls setIcon. L.divIcon IS the real Leaflet's.
 *   4. A VIRTUAL CLOCK, the node analogue of the Playwright page.clock the A2c spec uses. It
 *      starts at the committed e2e fixture's FROZEN_MS (2026-07-02T12:00:00Z); Date and
 *      performance.now read it; setTimeout/setInterval schedule on it; requestAnimationFrame
 *      callbacks run once per virtual second. Every due timer fires in time order with the
 *      promise queue drained between firings, so "the poll lands during the backoff" is an
 *      ordering, not a race.
 *   5. A CLOSED fetch router. /api/path-routes: the first request a 503 {"detail": "warming
 *      up"} (the A2c spec's own override), every later one the committed fixture
 *      tests/e2e/fixtures/api.js pathRoutes(). /api/path: the committed fixture path() with
 *      its clocks re-stamped to the virtual now. Any other /api/ path answers 503 (a backend
 *      still cold for every other system). Any other same-origin URL is recorded as refused
 *      and fails the coverage check. An absolute URL, or one naming an NJ Transit host, ends
 *      the run on the spot with exit 1 (inside the fetch stub, so no production catch can
 *      swallow it). No network is reached.
 *   6. The poll payloads: the committed fixture's trains (p-1 and p-2, both route 862), plus
 *      p-3, a third route-862 train at Journal Square served in every poll after the table's
 *      200 was served, and from the fourth such poll p-2 served with route_id 859. p-3 and
 *      the route change are synthetic and are the only edits to fixture data.
 *   7. matchMedia (no match), getComputedStyle (empty tokens), AbortSignal.timeout, and
 *      sentinels for the CSSOM (CSSStyleSheet, CSS.registerProperty, document.styleSheets,
 *      adoptedStyleSheets, a <style>'s .sheet). `location` is left undefined, as in F11, so
 *      helpers.js applies the PRODUCTION freshness thresholds.
 *   8. An in-process inspector session (node:inspector, no port opened) that pauses on every
 *      exception, caught or not, and records each TypeError/ReferenceError/RangeError raised
 *      once the scripts have loaded (their load-time code raises eleven caught errors of its
 *      own, explained where the watch starts). Production's catch blocks (retryUntil,
 *      refreshSource) would otherwise hide a stub gap as "the fix did nothing".
 *
 * HOW A MARKER IS FOUND. Through the Leaflet stub's own log of the L.marker calls production
 * made (only PATH trains are served, so only path.js makes any), among those on the map, told
 * apart by WHERE production placed them: a PATH train with no glide anchors is drawn at its
 * own coordinates, and the three trains sit at three different stations kilometres apart.
 *
 * HOW THE COLOUR IS READ: THE COMPUTED FILL, FAIL CLOSED. For every SVG shape in the marker's
 * rendered subtree that is painted (not display:none, opacity 0, fill-opacity 0 or hidden),
 * `fill` is computed the way CSS computes it, walking the real ancestry in (2): inline
 * !important, then stylesheet !important, inline, stylesheet, the SVG presentation attribute,
 * then inheritance; var() resolved from custom properties inherited along that same chain
 * (inline on any ancestor, or declared by a stylesheet rule); currentcolor through `color`;
 * inherit/initial/unset. The stylesheets are the ones index.html links (leaflet.css,
 * style.css) plus every <style> the page creates, in order, with specificity. Rule matching is
 * three-valued: a rule that might apply but cannot be decided (a dynamic pseudo-class, a
 * sibling combinator, an @media condition, a leaflet-* class the real Leaflet may have added to
 * a pane or the container) makes the value UNRESOLVED rather than guessed. Equally
 * unresolved: a paint server (url()), an element in the icon this reader does not paint, a
 * custom property written on an element whose place in the page the harness does not model,
 * any CSSOM use, markup the parser cannot read, a DOM member production read that this stub
 * does not have (on any element or the document: the F11/F12 selector rule applied to
 * members, so a guarded `if (el.someApi)` is not a silent no-op), an unimplemented selector,
 * or an engine error raised by production. An unresolved reading equals no colour: it fails
 * THE FINDING and the coverage check, so a repaint through any route this reader cannot
 * follow turns the script red with the reason, never green. Colours are normalised to
 * #rrggbb (hex, rgb() and a few names), so how a colour is spelled is invisible.
 *
 * WHAT THE VERIFICATION ROUND CHANGED. The first version read fill TEXT from the markup and
 * resolved var() against the root and body only. Four repaints a browser shows (a custom
 * property on the map container, one on the marker element, a per-route class with an
 * injected !important rule, and a style.cssText write) left it green, and a currentColor
 * refactor turned it red. The reader above computes what Chromium computes for each of those
 * (the verifier's Playwright probe: rgb(84,110,122) -> rgb(217,58,48) under the four repaints,
 * rgb(84,110,122) throughout under the refactor). The same probe agrees on the further routes
 * the tightening tried (an svg innerHTML rewrite, outerHTML, the fill presentation attribute,
 * a page stylesheet's custom property, a constructed CSSStyleSheet, Typed OM) and on two
 * writes a browser ignores (a fill attribute under an inline fill, an svg fill under the
 * path's own), which this script reads as the browser does. The router is closed, the retry
 * and poll timings are no longer pinned, and the fallback is the colour poll 1 measured
 * rather than a production function called by name.
 *
 * THE ACKNOWLEDGMENT the audit refers to is in the code and in the e2e suite. It is quoted
 * here as text and NOTHING BELOW ASSERTS ON IT:
 *   frontend/systems/path.js:289-292 (in applyPath): "THE LABEL TRACKS THE DATA, AND IS NOT
 *     GATED ON route_id CHANGING. The re-icon below is gated that way, and the step-1
 *     inventory proved what that costs: when the route table loads late, a diamond keeps the
 *     fallback colour permanently because route_id never changed."
 *   tests/e2e/markers.spec.js:128-131 (in spec "A2c. a PATH train gets its real route name
 *     once the route table loads late", at :127): "THE TRAP THE INVENTORY FOUND. applyPath
 *     re-icons ONLY when route_id changes, and the static route table loads asynchronously
 *     with retry backoff. A diamond created before the table lands keeps the fallback colour
 *     forever, because route_id never changes afterwards." A2c then asserts only the NAME,
 *     not the colour, so no committed test pins the diamond's colour either way.
 *   frontend/systems/njt.js:500-505 names the same trap for path.js AND railroad.js ("Both
 *     of those gated the re-icon on route_id changing ..."); railroad.js has since moved to a
 *     whole-mark skin key, so path.js is the one layer that still gates on route_id alone.
 *
 * HERMETIC. No network (fetch is the closed router above; nothing else in the context can open
 * a socket; the inspector session is in-process), no credential, no wall clock (Date,
 * performance and every timer run on the virtual clock), no file written. Deterministic: no
 * randomness, and timers fire only when this script advances the clock. Well under a second.
 *
 * RUN IT (from the repository root):
 *   node docs/reviews/audit-2026-10-12/a12_path_fallback_color.mjs
 * Exits 0 while the finding behaves as recorded, 1 the moment it does not.
 *
 * RECORDED DISPOSITION: VERIFIED. Both pre-table diamonds are drawn in the fallback #546e7a
 * and stay #546e7a after the table supplies route 862's #d93a30 and through every later poll,
 * while a route-862 diamond created after the table is #d93a30 in the same poll.
 */

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join, posix } from "node:path";
import { fileURLToPath } from "node:url";
import inspector from "node:inspector";
import vm from "node:vm";

const say = (...a) => console.log(...a);
const NOT_AS_RECORDED = "DISPOSITION: NOT AS RECORDED, see the failed checks above";

// A throw anywhere (the harness, or production code running inside it) is a record that no
// longer holds, and it says so in the same words a failed check does.
const harnessFailure = (err) => {
  say(`HARNESS ERROR: ${err && err.stack ? err.stack : err}`);
  say(NOT_AS_RECORDED);
  process.exit(1);
};
process.on("uncaughtException", harnessFailure);
process.on("unhandledRejection", harnessFailure);

const require = createRequire(import.meta.url);
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

/* ==================================================================
   Coverage: what the harness cannot model is recorded, never guessed past
   ================================================================== */

const gaps = new Set(); // CSSOM use, unreadable markup, page stylesheets written as markup
const selectorGaps = new Set(); // selector forms the engine does not implement
const engineErrors = []; // TypeError/ReferenceError/... raised while production ran
let watching = false; // off while the vendored Leaflet runs its own load-time feature probes

/* ==================================================================
   CSS: declarations, stylesheets, selectors
   ================================================================== */

const stripComments = (text) => String(text).replace(/\/\*[\s\S]*?\*\//g, "");

// Split on `sep` where it is not inside (), [] or quotes.
function splitTop(text, sep) {
  const out = [];
  let depth = 0;
  let quote = null;
  let cur = "";
  for (const ch of String(text)) {
    if (quote) {
      if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "(" || ch === "[") depth += 1;
    else if (ch === ")" || ch === "]") depth -= 1;
    else if (ch === sep && depth === 0) {
      out.push(cur);
      cur = "";
      continue;
    }
    cur += ch;
  }
  out.push(cur);
  return out;
}

const IMPORTANT = /\s*!\s*important\s*$/i;
function parseDecls(text) {
  const out = [];
  for (const raw of splitTop(stripComments(text), ";")) {
    const i = raw.indexOf(":");
    if (i <= 0) continue;
    const prop = raw.slice(0, i).trim();
    let value = raw.slice(i + 1).trim();
    const important = IMPORTANT.test(value);
    if (important) value = value.replace(IMPORTANT, "").trim();
    if (prop && value !== "") out.push({ name: prop.startsWith("--") ? prop : prop.toLowerCase(), value, important });
  }
  return out;
}

// A stylesheet's style rules in source order. Rules inside @media/@supports/@layer/@container
// are kept and marked conditional (the condition is never evaluated, so such a rule can only
// MAYBE apply); @font-face, @keyframes, @page and the like style no element and are skipped.
function parseSheet(css, sheet) {
  const s = stripComments(css);
  const rules = [];
  let i = 0;
  const skipBlock = () => {
    for (let depth = 1; i < s.length && depth; i += 1) {
      if (s[i] === "{") depth += 1;
      else if (s[i] === "}") depth -= 1;
    }
  };
  const list = (conditional) => {
    while (i < s.length) {
      while (i < s.length && /\s/.test(s[i])) i += 1;
      if (i >= s.length) return;
      if (s[i] === "}") {
        i += 1;
        return;
      }
      let j = i;
      while (j < s.length && s[j] !== "{" && s[j] !== ";" && s[j] !== "}") j += 1;
      const prelude = s.slice(i, j).trim();
      i = j + 1;
      if (s[j] === "}") return;
      if (s[j] !== "{") continue; // a statement at-rule (@import, @charset) or stray text
      if (/^@(media|supports|layer|container|document)\b/i.test(prelude)) list(true);
      else if (prelude.startsWith("@")) skipBlock();
      else {
        const start = i;
        skipBlock();
        rules.push({ sheet, prelude, decls: parseDecls(s.slice(start, i - 1)), conditional });
      }
    }
  };
  list(false);
  return rules;
}

// One compound selector: tag or *, then #id, .class, [attr op value i], :pseudo(args),
// ::pseudo-element. Null if it is not one.
function parseCompound(text) {
  const c = { tag: null, ids: [], classes: [], attrs: [], pseudos: [], pseudoElement: false };
  let i = 0;
  const head = /^(\*|[a-zA-Z][\w-]*)/.exec(text);
  if (head) {
    c.tag = head[1].toLowerCase();
    i = head[1].length;
  }
  while (i < text.length) {
    const rest = text.slice(i);
    let m;
    if ((m = /^#([\w-]+)/.exec(rest))) c.ids.push(m[1]);
    else if ((m = /^\.([\w-]+)/.exec(rest))) c.classes.push(m[1]);
    else if ((m = /^\[\s*([\w:-]+)\s*(?:([~|^$*]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]+))\s*([iIsS])?\s*)?\]/.exec(rest))) {
      c.attrs.push({ name: m[1].toLowerCase(), op: m[2] ?? null, value: m[3] ?? m[4] ?? m[5] ?? null, ci: (m[6] ?? "").toLowerCase() === "i" });
    } else if ((m = /^(::?)([\w-]+)/.exec(rest))) {
      let len = m[0].length;
      let args = null;
      if (rest[len] === "(") {
        let depth = 0;
        let k = len;
        for (; k < rest.length; k += 1) {
          if (rest[k] === "(") depth += 1;
          else if (rest[k] === ")" && --depth === 0) break;
        }
        if (k >= rest.length) return null;
        args = rest.slice(len + 1, k);
        len = k + 1;
      }
      const name = m[2].toLowerCase();
      if (m[1] === "::" || ["before", "after", "first-line", "first-letter"].includes(name)) c.pseudoElement = true;
      else c.pseudos.push({ name, args });
      i += len;
      continue;
    } else return null;
    i += m[0].length;
  }
  return c;
}

// One complex selector -> [{ comb, compound }] left to right (comb is the combinator BEFORE
// the compound: " ", ">", "+", "~", or null for the first). Null if it is not one.
function parseComplex(sel) {
  const s = String(sel).trim();
  const parts = [];
  let i = 0;
  let comb = null;
  while (i < s.length) {
    let j = i;
    let depth = 0;
    let quote = null;
    for (; j < s.length; j += 1) {
      const ch = s[j];
      if (quote) {
        if (ch === quote) quote = null;
      } else if (ch === '"' || ch === "'") quote = ch;
      else if (ch === "(" || ch === "[") depth += 1;
      else if (ch === ")" || ch === "]") depth -= 1;
      else if (depth === 0 && /[\s>+~]/.test(ch)) break;
    }
    const compound = j > i ? parseCompound(s.slice(i, j)) : null;
    if (!compound) return null;
    parts.push({ comb, compound });
    let k = j;
    let next = null;
    while (k < s.length && /\s/.test(s[k])) {
      next = " ";
      k += 1;
    }
    if (k < s.length && /[>+~]/.test(s[k])) {
      next = s[k];
      k += 1;
      while (k < s.length && /\s/.test(s[k])) k += 1;
    }
    if (k < s.length && !next) return null;
    if (k >= s.length && next && next !== " ") return null;
    comb = next;
    i = k;
  }
  return parts.length ? parts : null;
}
function parseSelectorList(text) {
  const list = splitTop(text, ",").map((x) => parseComplex(x));
  return list.length && list.every(Boolean) ? list : null;
}

const cmpSpec = (x, y) => x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
function specificity(parts) {
  const sum = [0, 0, 0];
  for (const { compound: k } of parts) {
    sum[0] += k.ids.length;
    sum[1] += k.classes.length + k.attrs.length;
    if (k.tag && k.tag !== "*") sum[2] += 1;
    if (k.pseudoElement) sum[2] += 1;
    for (const p of k.pseudos) {
      if (p.name === "where") continue;
      const inner = ["not", "is", "matches", "has"].includes(p.name) && p.args ? parseSelectorList(p.args) : null;
      if (!inner) {
        sum[1] += 1;
        continue;
      }
      const best = inner.map(specificity).sort(cmpSpec).pop();
      for (let n = 0; n < 3; n += 1) sum[n] += best[n];
    }
  }
  return sum;
}

/* ---- matching: exact for production's queries, three-valued for the cascade ---- */

const NO = 0;
const MAYBE = 1;
const YES = 2;

class UnimplementedSelector extends Error {}
function unimplemented(sel, why) {
  selectorGaps.add(`${JSON.stringify(sel)} (${why})`);
  return new UnimplementedSelector(
    `audit DOM stub: unimplemented selector ${JSON.stringify(sel)} (${why}). It supports compound ` +
      `selectors (tag, #id, .class, [attr], :not/:is/:where/:root) with descendant and child ` +
      `combinators. Teach it the new form rather than letting it answer null.`,
  );
}

const classesOf = (el) => new Set(String(el._className).split(/\s+/).filter(Boolean));
function attrMatches(el, { name, op, value, ci }) {
  let v = el.getAttribute(name);
  if (v === null || v === undefined) return false;
  if (op === null) return true;
  let want = value;
  if (ci) {
    v = v.toLowerCase();
    want = want.toLowerCase();
  }
  if (op === "=") return v === want;
  if (op === "~=") return v.split(/\s+/).includes(want);
  if (op === "|=") return v === want || v.startsWith(`${want}-`);
  if (op === "^=") return want !== "" && v.startsWith(want);
  if (op === "$=") return want !== "" && v.endsWith(want);
  if (op === "*=") return want !== "" && v.includes(want);
  return false;
}

// Every node on a marker's chain is known exactly (parsed icon nodes, <main>, <body>, <html>:
// index.html's static attributes plus every production write, all through this stub) EXCEPT
// for what the real Leaflet adds to its panes and container and this stub does not: leaflet-*
// classes, and a tabindex and inline style on the container. A test only that could satisfy
// is MAYBE there; any other failed test is NO. Leaflet's panes have no id.
let MAIN = null;
const panes = new Set(); // the Leaflet panes the stub created
let container = null; // the map container, #map
const leafletMayAdd = (el, kind, name) =>
  (panes.has(el) || el === container) && (kind === "class" ? name.startsWith("leaflet-") : ["tabindex", "style", "class"].includes(name));

function matchCompound(k, el, mode, sel) {
  if (!(el instanceof El) || el.tagName === "#TEXT") return NO;
  if (k.pseudoElement) {
    if (mode === "query") throw unimplemented(sel, "a pseudo-element");
    return NO; // it styles a pseudo-element, not this node
  }
  const cascade = mode === "cascade";
  let r = YES;
  const test = (ok, kind, name) => {
    if (ok) return true;
    if (!cascade || !leafletMayAdd(el, kind, name)) return false;
    r = MAYBE;
    return true;
  };
  if (k.tag && k.tag !== "*" && k.tag !== el.tagName.toLowerCase()) return NO;
  const id = cascade && panes.has(el) ? "" : el.id;
  for (const want of k.ids) if (id !== want) return NO;
  for (const cls of k.classes) if (!test(classesOf(el).has(cls), "class", cls)) return NO;
  for (const at of k.attrs) if (!test(attrMatches(el, at), "attr", at.name)) return NO;
  for (const p of k.pseudos) {
    let v;
    if (p.name === "root") v = el === DOC.documentElement ? YES : NO;
    else if (["not", "is", "where", "matches"].includes(p.name)) {
      const list = p.args === null ? null : parseSelectorList(p.args);
      if (!list) {
        if (mode === "query") throw unimplemented(sel, `:${p.name}(${p.args})`);
        v = MAYBE;
      } else {
        const best = Math.max(...list.map((parts) => matchComplex(parts, el, mode, sel)));
        v = p.name !== "not" ? best : best === YES ? NO : best === NO ? YES : MAYBE;
      }
    } else {
      if (mode === "query") throw unimplemented(sel, `:${p.name}`);
      v = MAYBE; // a state or structural pseudo-class: may or may not hold
    }
    if (v === NO) return NO;
    r = Math.min(r, v);
  }
  return r;
}

function matchComplex(parts, el, mode, sel, k = parts.length - 1) {
  const here = matchCompound(parts[k].compound, el, mode, sel);
  if (here === NO || k === 0) return here;
  const comb = parts[k].comb;
  if (comb === ">") return Math.min(here, el.parentElement ? matchComplex(parts, el.parentElement, mode, sel, k - 1) : NO);
  if (comb === " ") {
    let best = NO;
    for (let a = el.parentElement; a && best < YES; a = a.parentElement) best = Math.max(best, matchComplex(parts, a, mode, sel, k - 1));
    return Math.min(here, best);
  }
  if (mode === "query") throw unimplemented(sel, `the ${JSON.stringify(comb)} combinator`);
  return Math.min(here, MAYBE); // sibling combinators are not evaluated
}

function matches(el, selector) {
  const sel = String(selector).trim();
  const list = parseSelectorList(sel);
  if (!list) throw unimplemented(sel, "does not parse");
  return list.some((parts) => matchComplex(parts, el, "query", sel) === YES);
}

/* ==================================================================
   Injected host: the DOM
   ================================================================== */

// A CSSStyleDeclaration: property assignment, setProperty(name, value, priority),
// removeProperty, getPropertyValue/Priority and cssText all read and write one set of
// declarations. Every custom property write is recorded with the element it was written on.
const customWrites = new Map(); // --name -> Set of elements
const kebab = (p) => (p.startsWith("--") ? p : p.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`));
function makeStyle(owner) {
  const decls = new Map();
  const api = {
    setProperty(name, value, priority = "") {
      const key = kebab(String(name).trim());
      if (value === null || value === undefined || String(value).trim() === "") {
        decls.delete(key);
        return;
      }
      decls.set(key, { value: String(value).trim(), important: String(priority).toLowerCase() === "important" });
      if (key.startsWith("--") && owner) {
        if (!customWrites.has(key)) customWrites.set(key, new Set());
        customWrites.get(key).add(owner);
      }
    },
    removeProperty(name) {
      const key = kebab(String(name).trim());
      const old = decls.get(key)?.value ?? "";
      decls.delete(key);
      return old;
    },
    getPropertyValue: (name) => decls.get(kebab(String(name).trim()))?.value ?? "",
    getPropertyPriority: (name) => (decls.get(kebab(String(name).trim()))?.important ? "important" : ""),
    get cssText() {
      return [...decls].map(([k, d]) => `${k}: ${d.value}${d.important ? " !important" : ""};`).join(" ");
    },
    set cssText(text) {
      decls.clear();
      for (const d of parseDecls(text)) api.setProperty(d.name, d.value, d.important ? "important" : "");
    },
    get length() { return decls.size; },
    item: (i) => [...decls.keys()][i] ?? "",
    _decl: (name) => decls.get(name) ?? null,
  };
  return new Proxy(api, {
    get(t, p, r) {
      if (typeof p !== "string" || p in t) return Reflect.get(t, p, r);
      if (p.startsWith("--")) return undefined; // a browser's style["--x"] is not the property
      return decls.get(kebab(p))?.value ?? "";
    },
    set(t, p, v, r) {
      if (typeof p !== "string" || p in t) return Reflect.set(t, p, v, r);
      if (!p.startsWith("--")) t.setProperty(kebab(p), v); // style["--x"] = v does nothing in a browser
      return true;
    },
  });
}

// Unknown members: reading a property an element does not have is recorded on that element
// (only once production runs), so a call through an API this DOM does not implement, which
// production's catch blocks would swallow, is still seen.
const allEls = [];
const EL_TRAP = {
  get(t, p, r) {
    if (watching && typeof p === "string" && !(p in t) && p !== "then") t._unknown.add(p);
    return Reflect.get(t, p, r);
  },
  set(t, p, v, r) {
    if (p === "style" && typeof v === "string") {
      t.style.cssText = v;
      return true;
    }
    return Reflect.set(t, p, v, r);
  },
};

const styleElements = []; // every <style> the page creates
const linkElements = []; // every <link> the page creates
const textNode = (text) => Object.assign(new El("#text"), { textContent: String(text) });
const asNode = (c) => (c instanceof El ? c : textNode(c));
const dataKey = (p) => `data-${String(p).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
const describe = (el) => (el instanceof El ? `<${el._rawTag.toLowerCase()}${el.id ? `#${el.id}` : ""}>` : String(el));

class El {
  constructor(tag, id = "", parse = false) {
    this.tagName = String(tag).toUpperCase();
    this._rawTag = String(tag);
    this.id = id;
    this._parse = parse; // markup written into this element is parsed into nodes
    this._unknown = new Set();
    this.children = [];
    this.parentElement = null;
    this.attrs = new Map();
    this.hidden = false;
    this.inert = false;
    this.value = "";
    this.checked = false;
    this.textContent = "";
    this._innerHTML = "";
    this._queried = new Map(); // bare tag -> stand-in, for string markup only
    this._className = "";
    this.listeners = new Map();
    const self = this;
    const tokens = () => String(self._className).split(/\s+/).filter(Boolean);
    const write = (list) => { self._className = list.join(" "); };
    this.classList = {
      add: (...names) => write([...new Set([...tokens(), ...names.map(String)])]),
      remove: (...names) => write(tokens().filter((n) => !names.includes(n))),
      contains: (n) => tokens().includes(n),
      toggle: (n, force) => {
        const on = force === undefined ? !tokens().includes(n) : !!force;
        if (on) self.classList.add(n);
        else self.classList.remove(n);
        return on;
      },
      replace: (a, b) => {
        if (!tokens().includes(a)) return false;
        write(tokens().map((n) => (n === a ? b : n)));
        return true;
      },
      get value() { return self._className; },
      get length() { return tokens().length; },
      item: (i) => tokens()[i] ?? null,
    };
    this.dataset = new Proxy({}, {
      get: (_t, p) => (typeof p === "string" && self.attrs.has(dataKey(p)) ? self.attrs.get(dataKey(p)) : undefined),
      set: (_t, p, v) => { self.attrs.set(dataKey(p), String(v)); return true; },
      deleteProperty: (_t, p) => { self.attrs.delete(dataKey(p)); return true; },
      has: (_t, p) => self.attrs.has(dataKey(p)),
      ownKeys: () => [...self.attrs.keys()].filter((k) => k.startsWith("data-")).map((k) => k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())),
      getOwnPropertyDescriptor: (_t, p) => (self.attrs.has(dataKey(p)) ? { value: self.attrs.get(dataKey(p)), enumerable: true, configurable: true, writable: true } : undefined),
    });
    const proxy = new Proxy(this, EL_TRAP);
    this.style = makeStyle(proxy);
    allEls.push(proxy);
    if (this.tagName === "STYLE") styleElements.push(proxy);
    if (this.tagName === "LINK") linkElements.push(proxy);
    return proxy;
  }
  get className() { return this._className; }
  set className(v) { this._className = String(v); }
  get innerHTML() { return this._parse ? this.children.map(serialize).join("") : this._innerHTML; }
  set innerHTML(html) {
    for (const c of this.children) c.parentElement = null;
    this.children = [];
    this._queried = new Map(); // new markup: nothing inside it has been touched yet
    if (this._parse) {
      this._innerHTML = "";
      for (const n of parseMarkup(html, this)) this.appendChild(n);
    } else {
      this._innerHTML = String(html);
      if (/<(style|link)\b/i.test(this._innerHTML)) gaps.add(`a <style> or <link> written as markup into ${describe(this)}`);
    }
  }
  get outerHTML() { return serialize(this); }
  set outerHTML(html) {
    if (!this._parse || !this.parentElement) {
      gaps.add(`outerHTML written on ${describe(this)}, which this DOM keeps as text`);
      return;
    }
    this.replaceWith(...parseMarkup(html, this.parentElement));
  }
  insertAdjacentHTML(where, html) {
    if (!this._parse) {
      gaps.add(`insertAdjacentHTML on ${describe(this)}, which this DOM keeps as text`);
      return;
    }
    const nodes = parseMarkup(html, this);
    const pos = String(where).toLowerCase();
    if (pos === "beforebegin") this.before(...nodes);
    else if (pos === "afterend") this.after(...nodes);
    else if (pos === "afterbegin") this.prepend(...nodes);
    else this.append(...nodes);
  }
  get parentNode() { return this.parentElement; }
  get childNodes() { return this.children; }
  get firstChild() { return this.children[0] ?? null; }
  get lastChild() { return this.children[this.children.length - 1] ?? null; }
  get firstElementChild() { return (this._parse ? this.children.find((c) => c.tagName !== "#TEXT") : this.children[0]) ?? null; }
  get lastElementChild() { return [...this.children].reverse().find((c) => c.tagName !== "#TEXT") ?? null; }
  get childElementCount() { return this.children.filter((c) => c.tagName !== "#TEXT").length; }
  get nextElementSibling() { return this._sibling(1); }
  get previousElementSibling() { return this._sibling(-1); }
  _sibling(step) {
    const kids = this.parentElement ? this.parentElement.children.filter((c) => c.tagName !== "#TEXT") : [];
    const i = kids.indexOf(this);
    return i === -1 ? null : (kids[i + step] ?? null);
  }
  get isConnected() { for (let n = this; n; n = n.parentElement) if (n === DOC.documentElement) return true; return false; }
  get ownerDocument() { return DOC; }
  get nodeType() { return this.tagName === "#TEXT" ? 3 : 1; }
  get nodeName() { return this.tagName; }
  get localName() { return this._rawTag.toLowerCase(); }
  // A page stylesheet's CSSOM object: not modelled, so using it is a coverage gap.
  get sheet() {
    if (this.tagName !== "STYLE" && this.tagName !== "LINK") return undefined;
    gaps.add(`the CSSOM sheet of a page ${describe(this)} was used`);
    return { cssRules: [], insertRule() { return 0; }, deleteRule() {}, replaceSync() {}, replace: async () => {} };
  }
  setAttribute(k, v) {
    const name = String(k);
    const value = String(v);
    if (name === "style") {
      this.attrs.set("style", "");
      this.style.cssText = value;
      return;
    }
    if (name === "class") {
      this.attrs.set("class", "");
      this._className = value;
      return;
    }
    this.attrs.set(name, value);
    if (name === "id") this.id = value;
  }
  getAttribute(k) {
    if (k === "style") return this.attrs.has("style") || this.style.length ? this.style.cssText : null;
    if (k === "class") return this.attrs.has("class") || this._className ? this._className : null;
    return this.attrs.has(k) ? this.attrs.get(k) : null;
  }
  removeAttribute(k) {
    if (k === "style") this.style.cssText = "";
    if (k === "class") this._className = "";
    this.attrs.delete(k);
  }
  hasAttribute(k) { return this.getAttribute(k) !== null; }
  toggleAttribute(k, force) {
    const on = force === undefined ? !this.hasAttribute(k) : !!force;
    if (on) this.setAttribute(k, this.getAttribute(k) ?? "");
    else this.removeAttribute(k);
    return on;
  }
  setAttributeNS(_ns, k, v) { this.setAttribute(String(k).replace(/^\w+:/, ""), v); }
  getAttributeNS(_ns, k) { return this.getAttribute(k); }
  removeAttributeNS(_ns, k) { this.removeAttribute(k); }
  hasAttributeNS(_ns, k) { return this.hasAttribute(k); }
  addEventListener(t, fn) { this.listeners.set(t, [...(this.listeners.get(t) ?? []), fn]); }
  removeEventListener(t, fn) { this.listeners.set(t, (this.listeners.get(t) ?? []).filter((f) => f !== fn)); }
  appendChild(c) {
    if (!(c instanceof El)) throw new TypeError("appendChild: argument is not a Node");
    if (c.parentElement) c.parentElement.removeChild(c);
    this.children.push(c);
    c.parentElement = this;
    return c;
  }
  append(...cs) { cs.forEach((c) => this.appendChild(asNode(c))); }
  prepend(...cs) { [...cs].reverse().forEach((c) => this.insertBefore(asNode(c), this.children[0] ?? null)); }
  insertBefore(c, ref) {
    if (!(c instanceof El)) throw new TypeError("insertBefore: argument is not a Node");
    if (c.parentElement) c.parentElement.removeChild(c);
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i === -1) this.children.push(c);
    else this.children.splice(i, 0, c);
    c.parentElement = this;
    return c;
  }
  replaceChildren(...cs) {
    for (const c of this.children) c.parentElement = null;
    this.children = [];
    cs.forEach((c) => this.appendChild(asNode(c)));
  }
  replaceChild(c, old) {
    if (!(c instanceof El) || !(old instanceof El)) throw new TypeError("replaceChild: argument is not a Node");
    this.insertBefore(c, old);
    return this.removeChild(old);
  }
  removeChild(c) {
    this.children = this.children.filter((x) => x !== c);
    if (c instanceof El && c.parentElement === this) c.parentElement = null;
    return c;
  }
  remove() { if (this.parentElement) this.parentElement.removeChild(this); }
  before(...cs) { const p = this.parentElement; if (p) cs.forEach((c) => p.insertBefore(asNode(c), this)); }
  after(...cs) {
    const p = this.parentElement;
    if (!p) return;
    const ref = p.children[p.children.indexOf(this) + 1] ?? null;
    cs.forEach((c) => p.insertBefore(asNode(c), ref));
  }
  replaceWith(...cs) {
    const p = this.parentElement;
    if (!p) return;
    cs.forEach((c) => p.insertBefore(asNode(c), this));
    p.removeChild(this);
  }
  cloneNode(deep = false) {
    const copy = new El(this._rawTag, "", this._parse);
    for (const [k, v] of this.attrs) copy.setAttribute(k, k === "style" ? this.style.cssText : k === "class" ? this._className : v);
    copy._className = this._className;
    copy.style.cssText = this.style.cssText;
    copy.textContent = this.textContent;
    if (deep) {
      if (this._parse) this.children.forEach((c) => copy.appendChild(c.cloneNode(true)));
      else copy.innerHTML = this._innerHTML;
    }
    return copy;
  }
  contains(n) { for (let cur = n; cur; cur = cur.parentElement) if (cur === this) return true; return false; }
  closest(sel) {
    for (let n = this; n; n = n.parentElement) if (n.tagName !== "#TEXT" && matches(n, sel)) return n;
    return null;
  }
  focus() { DOC.activeElement = this; }
  blur() { if (DOC.activeElement === this) DOC.activeElement = DOC.body; }
  scrollIntoView() {}
  getBoundingClientRect() { return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  // Web Animations can repaint a fill without touching a declaration: not modelled.
  animate() {
    gaps.add(`element.animate() on ${describe(this)}`);
    return { cancel() {}, finish() {}, finished: Promise.resolve(), addEventListener() {} };
  }
  getAnimations() { return []; }
  matches(sel) { return matches(this, sel); }
  getElementsByTagName(tag) { return this.querySelectorAll(tag === "*" ? "*" : String(tag)); }
  getElementsByClassName(names) { return this.querySelectorAll(String(names).trim().split(/\s+/).map((n) => `.${n}`).join("")); }
  querySelectorAll(sel) {
    const out = [];
    const walk = (el) => {
      for (const c of el.children) {
        if (!(c instanceof El) || c.tagName === "#TEXT") continue;
        if (matches(c, sel)) out.push(c);
        walk(c);
      }
    };
    walk(this);
    // An element whose markup is kept as a string answers a bare-tag query by whether the tag
    // occurs in it, with one cached stand-in per tag (so a write onto it is kept until the
    // markup is replaced). Only bare tags: this is not a parser.
    const tag = String(sel).trim().toLowerCase();
    if (!out.length && !this._parse && this._innerHTML && /^[a-z][\w-]*$/.test(tag)) {
      if (new RegExp(`<${tag}[\\s>/]`, "i").test(this._innerHTML)) {
        if (!this._queried.has(tag)) {
          const inner = new El(tag);
          inner.parentElement = this;
          this._queried.set(tag, inner);
        }
        out.push(this._queried.get(tag));
      }
    }
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }
}

/* ---- markup: parse into nodes, serialize back ---- */

const VOID = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"]);
const ENTITY = { quot: '"', amp: "&", lt: "<", gt: ">", apos: "'", "#39": "'", "#x27": "'" };
const decode = (s) => String(s).replace(/&(quot|amp|lt|gt|apos|#39|#x27);/g, (_m, e) => ENTITY[e]);
const TOKEN = /<!--[\s\S]*?-->|<\/\s*([a-zA-Z][\w:-]*)\s*>|<([a-zA-Z][\w:-]*)((?:\s+[^\s"'=<>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'=<>`]+))?)*)\s*(\/?)>|([^<]+)/y;
const ATTR = /([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
function parseMarkup(html, where) {
  const text = String(html);
  const roots = [];
  const stack = [];
  const add = (n) => (stack.length ? stack[stack.length - 1].appendChild(n) : roots.push(n));
  TOKEN.lastIndex = 0;
  while (TOKEN.lastIndex < text.length) {
    const at = TOKEN.lastIndex;
    const m = TOKEN.exec(text);
    if (!m || TOKEN.lastIndex === at) {
      gaps.add(`markup written into ${describe(where)} that this parser cannot read: ${JSON.stringify(text.slice(at, at + 40))}`);
      break;
    }
    if (m[1]) {
      const close = m[1].toLowerCase();
      const k = stack.map((e) => e._rawTag.toLowerCase()).lastIndexOf(close);
      if (k === -1) gaps.add(`a stray </${close}> in markup written into ${describe(where)}`);
      else stack.length = k;
    } else if (m[2]) {
      const el = new El(m[2], "", true);
      for (const a of (m[3] ?? "").matchAll(ATTR)) el.setAttribute(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ""));
      add(el);
      if (!m[4] && !VOID.has(m[2].toLowerCase())) stack.push(el);
    } else if (m[5] !== undefined && m[5].trim()) add(textNode(decode(m[5])));
  }
  return roots;
}
const escapeAttr = (v) => String(v).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
function serialize(n) {
  if (n.tagName === "#TEXT") return String(n.textContent).replace(/&/g, "&amp;").replace(/</g, "&lt;");
  const attrs = [...n.attrs.keys()];
  if (!attrs.includes("style") && n.style.length) attrs.push("style");
  if (!attrs.includes("class") && n._className) attrs.push("class");
  const html = attrs.map((k) => ` ${k}="${escapeAttr(n.getAttribute(k) ?? "")}"`).join("");
  const inner = n._parse ? n.children.map(serialize).join("") : n._innerHTML;
  return `<${n._rawTag}${html}>${inner}</${n._rawTag}>`;
}

/* ---- the document ---- */

const byId = new Map();
const DOC_TARGET = {
  body: new El("body"),
  documentElement: new El("html"),
  head: new El("head"),
  activeElement: null,
  hidden: false,
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, new El("div", id));
    return byId.get(id);
  },
  createElement: (tag) => new El(tag),
  // SVG content production creates is parsed like an icon's own markup.
  createElementNS: (_ns, tag) => new El(tag, "", true),
  createTextNode: (text) => textNode(text),
  addEventListener() {},
  removeEventListener() {},
  querySelector(sel) { return DOC.querySelectorAll(sel)[0] ?? null; },
  // Every element getElementById made, every element appended under <head> or <body>, and
  // (through the panes) every marker Leaflet rendered, each once.
  querySelectorAll(sel) {
    const out = new Set();
    for (const el of [...byId.values(), DOC_TARGET.head, DOC_TARGET.body]) {
      if (matches(el, sel)) out.add(el);
      for (const hit of el.querySelectorAll(sel)) out.add(hit);
    }
    return [...out];
  },
  getElementsByTagName: (tag) => DOC.querySelectorAll(String(tag)),
  getElementsByClassName: (names) => DOC.querySelectorAll(String(names).trim().split(/\s+/).map((n) => `.${n}`).join("")),
  // The CSSOM is not modelled: touching it is a coverage gap, never a silent no-op.
  get styleSheets() {
    gaps.add("document.styleSheets (CSSOM)");
    return [];
  },
  get adoptedStyleSheets() {
    gaps.add("document.adoptedStyleSheets (CSSOM)");
    return [];
  },
  set adoptedStyleSheets(_v) { gaps.add("document.adoptedStyleSheets (CSSOM)"); },
};
const documentUnknown = new Set();
const DOC = new Proxy(DOC_TARGET, {
  get(t, p, r) {
    if (watching && typeof p === "string" && !(p in t) && p !== "then") documentUnknown.add(p);
    return Reflect.get(t, p, r);
  },
});
DOC.activeElement = DOC.body;

/* ==================================================================
   index.html: the scripts it runs, the stylesheets it links, the root's attributes
   ================================================================== */

const INDEX = read("frontend/index.html").replace(/<!--[\s\S]*?-->/g, "");
const fromFrontend = (src) => posix.join("frontend", src);
const scriptSrcs = [...INDEX.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*>/g)].map((m) => fromFrontend(m[1]));
const LEAFLET_SRC = scriptSrcs.find((s) => s.includes("vendor/leaflet/"));
const SOURCES = scriptSrcs.filter((s) => s !== LEAFLET_SRC);
const sheetHrefs = [...INDEX.matchAll(/<link\b[^>]*\brel="stylesheet"[^>]*>/g)]
  .map((m) => /\bhref="([^"]+)"/.exec(m[0]))
  .filter(Boolean)
  .map((m) => fromFrontend(m[1]));
if (!LEAFLET_SRC || !SOURCES.length || !sheetHrefs.length) throw new Error("harness: index.html names no Leaflet, no scripts or no stylesheets");
const inlineSheets = [...INDEX.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)].map((m, k) => ({ name: `index.html <style> #${k + 1}`, text: m[1] }));
const STATIC_SHEETS = [...sheetHrefs.map((href) => ({ name: href, text: read(href) })), ...inlineSheets];
for (const tagName of ["html", "body"]) {
  const open = new RegExp(`<${tagName}\\b([^>]*)>`, "i").exec(INDEX);
  const el = tagName === "html" ? DOC.documentElement : DOC.body;
  for (const a of (open ? open[1] : "").matchAll(ATTR)) el.setAttribute(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ""));
}

/* ==================================================================
   The REAL Leaflet, for the one thing this finding is about: rendering an icon
   ================================================================== */

// Elements Leaflet creates (the marker's icon element among them) parse the markup written
// into them, so the rendered diamond is a real node tree.
const leafletDocument = new Proxy(DOC, {
  get(t, p, r) {
    if (p === "createElement") return (tag) => new El(tag, "", true);
    return Reflect.get(t, p, r);
  },
});
const leafletSandbox = {
  window: null,
  document: leafletDocument,
  navigator: { userAgent: "node", platform: "node", maxTouchPoints: 0 },
  screen: { width: 800, height: 600 },
  Element: El,
  setTimeout() { return 0; },
  clearTimeout() {},
  console,
  module: { exports: {} },
};
leafletSandbox.window = Object.assign({}, leafletSandbox, {
  devicePixelRatio: 1,
  addEventListener() {},
  removeEventListener() {},
});
leafletSandbox.exports = leafletSandbox.module.exports;
leafletSandbox.self = leafletSandbox;
leafletSandbox.globalThis = leafletSandbox;
vm.createContext(leafletSandbox);
vm.runInContext(read(LEAFLET_SRC), leafletSandbox, { filename: LEAFLET_SRC });
const RealL = leafletSandbox.module.exports;

/* ==================================================================
   Injected host: the Leaflet stub production runs against
   ================================================================== */

const markerLog = []; // every L.marker production constructed, in order
const polylineLog = []; // every L.polyline production constructed: { points, options }

function makeLayerGroup() {
  const members = new Set();
  const group = {
    _onMap: false,
    addTo(m) { m.addLayer(group); return group; },
    addLayer(layer) {
      members.add(layer);
      if (group._onMap && layer && typeof layer._added === "function") layer._added();
      return group;
    },
    removeLayer(layer) {
      members.delete(layer);
      if (layer && typeof layer._removed === "function") layer._removed();
      return group;
    },
    clearLayers() { [...members].forEach((l) => group.removeLayer(l)); return group; },
    hasLayer: (l) => members.has(l),
    getLayers: () => [...members],
    eachLayer(fn) { members.forEach(fn); return group; },
  };
  return group;
}

function makeMarker(latlng, options) {
  const handlers = new Map();
  const marker = {
    options: { ...options },
    _latlng: latlng,
    _icon: null,
    _onMap: false,
    _popupOpen: false,
    _popup: null,
    renders: 0, // how many times the element was (re)rendered from an icon
    on(event, fn) { handlers.set(event, [...(handlers.get(event) ?? []), fn]); return marker; },
    off() { return marker; },
    fire(event, payload = {}) { for (const fn of handlers.get(event) ?? []) fn.call(marker, payload); return marker; },
    // Leaflet 1.9.4 Marker._initIcon: `options.icon.createIcon(this._icon)` (a DivIcon reuses
    // the old DIV and rewrites its innerHTML from options.html), then the zoom-animation and
    // interactive classes, and a new element is appended to the marker's pane.
    _initIcon() {
      const before = marker._icon;
      const el = marker.options.icon.createIcon(before);
      el.classList.add("leaflet-zoom-animated", "leaflet-interactive");
      if (before && el !== before) before.remove();
      if (!el.parentElement) mapStub.getPane(marker.options.pane || "markerPane").appendChild(el);
      marker._icon = el;
      marker.renders += 1;
    },
    _added() { marker._onMap = true; marker._initIcon(); marker.fire("add"); },
    // Leaflet's onRemove takes the element out of its pane.
    _removed() {
      marker.fire("remove");
      marker._onMap = false;
      if (marker._icon) marker._icon.remove();
    },
    addTo(layer) { layer.addLayer(marker); return marker; },
    // Leaflet 1.9.4 Marker.setIcon: store the icon, and re-render only if on the map.
    setIcon(icon) { marker.options.icon = icon; if (marker._onMap) marker._initIcon(); return marker; },
    getIcon: () => marker.options.icon,
    getElement: () => marker._icon,
    setLatLng(ll) { marker._latlng = ll; return marker; },
    getLatLng: () => marker._latlng,
    setOpacity(o) { marker.options.opacity = o; return marker; },
    bindPopup(content, opts) { marker._popup = { content, opts, getElement: () => null, setContent() {} }; return marker; },
    getPopup: () => marker._popup,
    isPopupOpen: () => marker._popupOpen,
    openPopup() { marker._popupOpen = true; marker.fire("popupopen"); return marker; },
    closePopup() { marker._popupOpen = false; marker.fire("popupclose"); return marker; },
    setPopupContent() { return marker; },
    bindTooltip() { return marker; },
    unbindTooltip() { return marker; },
    getTooltip: () => null,
    openTooltip() { return marker; },
    closeTooltip() { return marker; },
    setStyle() { return marker; },
    remove() { return marker; },
  };
  return marker;
}

// The container and its panes, linked upward as Leaflet and index.html nest them:
// marker pane -> map pane -> #map -> <main> -> <body> -> <html>, with #map carrying the
// attributes index.html gives it. Above the marker pane only the parent links are set (no
// child lists), so the panes and #map are reached by document queries only as before; the
// marker elements themselves are children of their pane, as Leaflet appends them.
container = DOC.getElementById("map");
{
  const open = /<[a-z]+\b[^>]*\bid="map"[^>]*>/i.exec(INDEX);
  for (const a of (open ? open[0].replace(/^<[a-z]+/i, "").replace(/>$/, "") : "").matchAll(ATTR)) {
    if (a[1] !== "id") container.setAttribute(a[1], decode(a[2] ?? a[3] ?? a[4] ?? ""));
  }
}
MAIN = new El("main");
container.parentElement = MAIN;
MAIN.parentElement = DOC.body;
DOC.body.parentElement = DOC.documentElement;
DOC.head.parentElement = DOC.documentElement;
const mapHandlers = new Map();
const mapStub = {
  on(event, fn) { mapHandlers.set(event, [...(mapHandlers.get(event) ?? []), fn]); return mapStub; },
  off() { return mapStub; },
  once() { return mapStub; },
  fire(event, payload = {}) { for (const fn of mapHandlers.get(event) ?? []) fn(payload); return mapStub; },
  setView() { return mapStub; },
  flyTo() { return mapStub; },
  panTo() { return mapStub; },
  panBy() { return mapStub; },
  setZoom() { return mapStub; },
  fitBounds() { return mapStub; },
  invalidateSize() { return mapStub; },
  closePopup() { return mapStub; },
  createPane(name) { return mapStub.getPane(name); },
  getPane(name) {
    const pane = DOC.getElementById(`pane-${name}`);
    if (!panes.has(pane)) {
      panes.add(pane);
      pane.parentElement = name === "mapPane" ? container : mapStub.getPane("mapPane");
    }
    return pane;
  },
  // A marker added straight to the map renders like one added through a group; a group is
  // only marked as on the map (every group production creates is added at load, empty).
  addLayer(layer) {
    if (layer && typeof layer._added === "function") layer._added();
    else if (layer) layer._onMap = true;
    return mapStub;
  },
  removeLayer(layer) {
    if (layer && typeof layer._removed === "function") layer._removed();
    else if (layer) layer._onMap = false;
    return mapStub;
  },
  hasLayer: (layer) => !!(layer && layer._onMap),
  getContainer: () => container,
  getZoom: () => 12,
  getCenter: () => ({ lat: 40.7128, lng: -74.006 }),
  getBounds: () => ({ contains: () => true, pad: () => mapStub.getBounds() }),
  getSize: () => ({ x: 1280, y: 800 }),
  latLngToContainerPoint: () => ({ x: 0, y: 0 }),
};

let stampSeq = 0;
const L = {
  map: () => mapStub,
  tileLayer: () => ({ addTo() { return this; }, on() { return this; } }),
  layerGroup: makeLayerGroup,
  featureGroup: makeLayerGroup,
  canvas: () => ({}),
  svg: () => ({}),
  marker(latlng, options) {
    const m = makeMarker(latlng, options);
    markerLog.push(m);
    return m;
  },
  circleMarker: (latlng, options) => makeMarker(latlng, options),
  polyline(points, options) {
    const line = { points, options, addTo() { return line; }, setStyle() { return line; }, getLatLngs: () => points };
    polylineLog.push(line);
    return line;
  },
  // THE REAL ONE. Every icon production builds is a genuine Leaflet DivIcon, and its
  // createIcon is what turns options.html into the element a rider sees.
  divIcon: (options) => RealL.divIcon(options),
  icon: (options) => RealL.icon(options),
  latLng: (a, b) => (Array.isArray(a) ? { lat: a[0], lng: a[1] } : { lat: a, lng: b }),
  control: {
    zoom: () => ({ addTo() { return this; } }),
    attribution: () => ({ addTo() { return this; } }),
    scale: () => ({ addTo() { return this; } }),
  },
  Util: { stamp: (obj) => (obj.__stamp ??= ++stampSeq) },
  DomEvent: { disableClickPropagation() {}, disableScrollPropagation() {}, on() {}, off() {} },
};

/* ==================================================================
   Fixtures (committed), the virtual clock, and the closed router
   ================================================================== */

const fixtures = require(join(ROOT, "tests/e2e/fixtures/api.js"));
const FROZEN_MS = fixtures.FROZEN_MS;
const FROZEN_S = fixtures.FROZEN_S;

/* ---- the virtual clock ---- */

const clock = { ms: FROZEN_MS, seq: 0, timers: new Map(), frames: [], fired: 0, framesRun: 0 };
const schedule = (fn, delay, every, args) => {
  const id = ++clock.seq;
  const wait = Math.max(0, Number(delay) || 0);
  clock.timers.set(id, { fn, at: clock.ms + wait, every: every ? Math.max(1, wait) : null, args });
  return id;
};
const settle = async (turns = 8) => {
  for (let i = 0; i < turns; i += 1) await new Promise((resolve) => setImmediate(resolve));
};
// Let `ms` of virtual time pass, one virtual second at a time: every timer due in the slice
// fires in time order (promise queue drained after each), then one animation frame runs.
async function advance(ms) {
  const end = clock.ms + ms;
  while (clock.ms < end) {
    const sliceEnd = Math.min(end, clock.ms + 1000);
    for (;;) {
      let next = null;
      for (const [id, t] of clock.timers) {
        if (t.at <= sliceEnd && (!next || t.at < next.t.at || (t.at === next.t.at && id < next.id))) next = { id, t };
      }
      if (!next) break;
      clock.ms = next.t.at;
      if (next.t.every) next.t.at += next.t.every;
      else clock.timers.delete(next.id);
      clock.fired += 1;
      next.t.fn(...(next.t.args ?? []));
      await settle();
    }
    clock.ms = sliceEnd;
    const frames = clock.frames.splice(0);
    clock.framesRun += frames.length;
    for (const fn of frames) fn(clock.ms - FROZEN_MS);
    await settle();
  }
}
// Run the clock until `done()` holds, a virtual second at a time, for at most ten virtual
// minutes: an event that never happens is a harness error naming it, not a hang.
const MAX_WAIT_MS = 10 * 60 * 1000;
async function advanceUntil(done, what) {
  const start = clock.ms;
  while (!done()) {
    if (clock.ms - start >= MAX_WAIT_MS) throw new Error(`harness: ${what} did not happen within ${MAX_WAIT_MS / 60000} virtual minutes`);
    await advance(1000);
  }
}

class ClockDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(clock.ms);
    else super(...args);
  }
  static now() { return clock.ms; }
}

/* ---- the router ---- */

// The poll payloads. p-1 and p-2 are the committed fixture's; p-3 (served in every poll after
// the table's 200 was served) and p-2's route change (from the fourth such poll) are the
// injected edits (see the header).
const P3 = {
  id: "p-3", route_id: "862",
  latitude: 40.73301, longitude: -74.06289, stop_id: "26731",
  stop_name: "Journal Square", direction: "To New York",
  prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 300,
  observed_at: FROZEN_S - 5, provenance: "placed",
};
const requests = { routes: [], polls: [], cold: new Map(), refused: [], pollsBeforeTable: null };
const trainsForPoll = (n) => {
  const trains = fixtures.path().trains;
  const after = requests.pollsBeforeTable === null ? 0 : n - requests.pollsBeforeTable;
  if (after >= 1) trains.push({ ...P3 });
  if (after >= 4) for (const t of trains) if (t.id === "p-2") t.route_id = "859";
  return trains;
};
const TRAIN_AT = {};
for (const t of [...fixtures.path().trains, P3]) TRAIN_AT[t.id] = [t.latitude, t.longitude];

// The fixture's envelope, every clock in it shifted to the virtual now, so the feed is fresh.
const CLOCK_FIELDS = ["fetched_at", "feed_timestamp", "served_at", "observed_at", "prev_time", "next_time"];
function restamped(obj, shift) {
  const out = { ...obj };
  for (const key of CLOCK_FIELDS) if (typeof out[key] === "number") out[key] += shift;
  return out;
}
function pathPollBody(n) {
  const shift = clock.ms / 1000 - FROZEN_S;
  const body = restamped(fixtures.path(), shift);
  body.trains = trainsForPoll(n).map((t) => restamped(t, shift));
  return body;
}

const respond = (status, body) => ({ ok: status === 200, status, json: async () => JSON.parse(JSON.stringify(body)) });
// Ends the run inside the fetch stub itself, so no production catch can turn it into a
// quiet "poll failed".
function refuseHard(url, why) {
  say(`HARNESS ERROR: refused ${url} (${why}); this harness serves nothing off the page's own origin`);
  say(NOT_AS_RECORDED);
  process.exit(1);
}
function makeFetch() {
  return async (input) => {
    const url = String(input && typeof input === "object" && "url" in input ? input.url : input);
    if (/njtransit|raildata/i.test(url)) refuseHard(url, "an NJ Transit host");
    if (/^[a-z][\w+.-]*:|^\/\//i.test(url)) refuseHard(url, "an absolute URL");
    const path = url.split(/[?#]/)[0];
    if (path === "/api/path-routes") {
      // The A2c spec's own override: the first attempt is a warming 503, later ones the table.
      const status = requests.routes.length === 0 ? 503 : 200;
      requests.routes.push({ atMs: clock.ms - FROZEN_MS, status });
      if (status === 200 && requests.pollsBeforeTable === null) requests.pollsBeforeTable = requests.polls.length;
      return status === 200 ? respond(200, fixtures.pathRoutes()) : respond(503, { detail: "warming up" });
    }
    if (path === "/api/path") {
      requests.polls.push({ atMs: clock.ms - FROZEN_MS });
      return respond(200, pathPollBody(requests.polls.length));
    }
    if (path.startsWith("/api/")) {
      requests.cold.set(path, (requests.cold.get(path) ?? 0) + 1);
      return respond(503, { detail: "warming up" });
    }
    requests.refused.push(url);
    return respond(404, { detail: "not served by this harness" });
  };
}

/* ---- the CSSOM sentinels ---- */

class CSSStyleSheetSentinel {
  constructor() { gaps.add("a constructed CSSStyleSheet (CSSOM)"); this.cssRules = []; }
  insertRule() { return 0; }
  deleteRule() {}
  replaceSync() {}
  async replace() { return this; }
}
const CSS_SENTINEL = {
  escape: (s) => String(s).replace(/[^\w-]/g, (c) => `\\${c}`),
  supports: () => true,
  registerProperty: () => { gaps.add("CSS.registerProperty (CSSOM)"); },
};

const sandbox = {
  console,
  Date: ClockDate,
  document: DOC,
  L,
  fetch: makeFetch(),
  AbortSignal: { timeout: (ms) => ({ stubDeadlineMs: ms }) },
  URLSearchParams,
  URL,
  CSS: CSS_SENTINEL,
  CSSStyleSheet: CSSStyleSheetSentinel,
  matchMedia: (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} }),
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  setTimeout: (fn, delay, ...args) => schedule(fn, delay, false, args),
  setInterval: (fn, delay, ...args) => schedule(fn, delay, true, args),
  clearTimeout: (id) => clock.timers.delete(id),
  clearInterval: (id) => clock.timers.delete(id),
  requestAnimationFrame: (fn) => clock.frames.push(fn),
  cancelAnimationFrame: () => {},
  queueMicrotask: (fn) => Promise.resolve().then(fn),
  addEventListener: () => {},
  removeEventListener: () => {},
  // `location` is left undefined, as in the 09-05 F11 harness: helpers.js then takes its own
  // guarded branch and the PRODUCTION freshness thresholds apply, not query-string overrides.
  innerWidth: 1280,
  innerHeight: 800,
  performance: { now: () => clock.ms - FROZEN_MS },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
const ctx = vm.createContext(sandbox);

/* ---- engine errors production raises, caught or not ---- */

const ENGINE_ERRORS = new Set(["TypeError", "ReferenceError", "RangeError", "SyntaxError", "EvalError", "URIError"]);
const session = new inspector.Session();
session.connect();
session.on("Debugger.paused", (message) => {
  try {
    const data = message.params.data || {};
    if (watching && ENGINE_ERRORS.has(data.className)) {
      engineErrors.push(String(data.description || data.className).split("\n").slice(0, 2).map((s) => s.trim()).join(" "));
    }
  } finally {
    session.post("Debugger.resume");
  }
});
session.post("Debugger.enable");
session.post("Debugger.setPauseOnExceptions", { state: "all" });

// index.html's order, map.js last: its kickoff is what drives everything below. The watch
// starts once the scripts have loaded. Their load-time code raises eleven caught
// ReferenceErrors of its own: ten temporal-dead-zone reads (top-level calls in shared.js
// that reach bindings declared further down it, which a browser raises identically) and one
// localStorage lookup this sandbox does not provide (the theme preference, which falls back
// as it does with storage blocked). Everything a fix for this finding would change runs
// later: in the loader's response, the poll apply, the sweeps and the animation tick.
for (const rel of SOURCES) vm.runInContext(read(rel), ctx, { filename: rel });
watching = true;

/* ==================================================================
   Reading the rendered diamonds: the computed fill
   ================================================================== */

// The cascade's rules: every stylesheet index.html links, then every <style> the page
// created, in order. Each declaration is one entry.
function rulesFrom(sheets) {
  const out = [];
  for (const { name, text } of sheets) {
    for (const rule of parseSheet(text, name)) {
      const selectors = splitTop(rule.prelude, ",").map((s) => s.trim()).filter(Boolean).map((text) => {
        const parts = parseComplex(text);
        return { text, parts, spec: parts ? specificity(parts) : [0, 0, 0] };
      });
      for (const decl of rule.decls) out.push({ sheet: name, selectors, decl, conditional: rule.conditional });
    }
  }
  return out;
}
const STATIC_RULES = rulesFrom(STATIC_SHEETS);
const styleText = (el) => [el.textContent, el._innerHTML, ...el.children.map((c) => c.textContent)].filter(Boolean).join("\n");
function currentRules() {
  const page = styleElements.map((el, k) => ({ name: `page <style> #${k + 1}`, text: styleText(el) }));
  const all = [...STATIC_RULES, ...rulesFrom(page)];
  all.forEach((r, order) => { r.order = order; });
  return all;
}

const isGap = (v) => v !== null && typeof v === "object";
const SVG_TAGS = new Set(["SVG", "PATH", "G", "RECT", "CIRCLE", "ELLIPSE", "POLYGON", "POLYLINE", "LINE", "TEXT", "USE"]);
const PRESENTATION = new Set(["fill", "color", "fill-opacity", "opacity", "visibility", "display"]);
const INITIAL = { fill: "#000000", color: { gap: "color reaches the root with no value (the browser's own text colour)" } };

// The value the cascade gives `prop` on `el` before var() and currentcolor: null when nothing
// declares it there (it then inherits), a gap when a rule that might apply cannot be decided.
function cascaded(el, prop, rules) {
  if (!(el instanceof El)) return null;
  const inline = el.style._decl(prop);
  if (inline && inline.important) return inline.value;
  const hits = [];
  for (const r of rules) {
    if (r.decl.name !== prop && !(r.decl.name === "all" && !prop.startsWith("--"))) continue;
    let best = NO;
    let spec = [0, 0, 0];
    for (const s of r.selectors) {
      const m = s.parts ? matchComplex(s.parts, el, "cascade", s.text) : MAYBE;
      if (m > best) best = m;
      if (m > NO && cmpSpec(s.spec, spec) > 0) spec = s.spec;
    }
    if (r.conditional && best === YES) best = MAYBE;
    if (best > NO) hits.push({ r, sure: best === YES, spec });
  }
  const decide = (list) => {
    const unsure = list.find((h) => !h.sure);
    if (unsure) return { gap: `${unsure.r.sheet} rule "${unsure.r.selectors.map((s) => s.text).join(", ")}" might set ${prop} on ${describe(el)}` };
    list.sort((x, y) => cmpSpec(x.spec, y.spec) || x.r.order - y.r.order);
    return list[list.length - 1].r.decl.value;
  };
  const important = hits.filter((h) => h.r.decl.important);
  if (important.length) return decide(important);
  if (inline) return inline.value;
  if (hits.length) return decide(hits);
  if (PRESENTATION.has(prop) && SVG_TAGS.has(el.tagName)) {
    const attr = el.getAttribute(prop);
    if (attr !== null) return attr;
  }
  return null;
}

// A custom property's value on `el`, inherited along the real ancestry. A write on an element
// whose place in the page this harness does not model (anything outside a parsed icon that
// is not on this chain) might be an ancestor it cannot see, so it is a gap, not a miss.
function customValue(el, name, rules, depth) {
  for (const writer of customWrites.get(name) ?? []) {
    if (writer._parse) continue; // a parsed node's ancestry is exact: off this chain is not above it
    let above = false;
    for (let a = el; a; a = a.parentElement) if (a === writer) above = true;
    if (!above) return { gap: `${name} was written on ${describe(writer)}, whose place in the page this harness does not model` };
  }
  for (let a = el; a; a = a.parentElement) {
    const v = cascaded(a, name, rules);
    if (isGap(v)) return v;
    if (v === null || /^\s*(inherit|unset)\s*$/i.test(v)) continue;
    if (/^\s*initial\s*$/i.test(v)) return null;
    return substitute(a, v, rules, depth + 1);
  }
  return null;
}
function substitute(el, value, rules, depth) {
  if (depth > 64) return { gap: `var() nested too deep in ${value}` };
  let out = "";
  let s = String(value);
  for (let at = s.search(/var\(/i); at !== -1; at = s.search(/var\(/i)) {
    out += s.slice(0, at);
    let depthParen = 0;
    let k = at + 3;
    for (; k < s.length; k += 1) {
      if (s[k] === "(") depthParen += 1;
      else if (s[k] === ")" && --depthParen === 0) break;
    }
    if (k >= s.length) return { gap: `an unbalanced var() in ${value}` };
    const inner = s.slice(at + 4, k);
    const comma = inner.indexOf(",");
    const name = (comma === -1 ? inner : inner.slice(0, comma)).trim();
    let got = customValue(el, name, rules, depth);
    if (isGap(got)) return got;
    if (got === null) {
      if (comma === -1) return { gap: `var(${name}) has no value and no fallback` };
      got = substitute(el, inner.slice(comma + 1).trim(), rules, depth + 1);
      if (isGap(got)) return got;
    }
    out += got;
    s = s.slice(k + 1);
  }
  return out + s;
}

const NAMED = { black: "#000000", white: "#ffffff", red: "#ff0000", gray: "#808080", grey: "#808080" };
function normalizeColour(v) {
  const s = String(v).trim().toLowerCase();
  let m;
  if ((m = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/.exec(s))) return `#${m[1]}${m[1]}${m[2]}${m[2]}${m[3]}${m[3]}`;
  if (/^#[0-9a-f]{6}$/.test(s)) return s;
  if ((m = /^#([0-9a-f]{6})ff$/.exec(s))) return `#${m[1]}`;
  if ((m = /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)\s*(?:[,/]\s*(?:1(?:\.0+)?|100%)\s*)?\)$/.exec(s))) {
    return `#${[m[1], m[2], m[3]].map((x) => Number(x).toString(16).padStart(2, "0")).join("")}`;
  }
  return NAMED[s] ?? s;
}

function computedValue(el, prop, rules, depth = 0) {
  if (depth > 64) return { gap: `${prop} nested too deep` };
  if (!el) return INITIAL[prop] ?? null;
  const v = cascaded(el, prop, rules);
  if (isGap(v)) return v;
  const kw = v === null ? "inherit" : v.trim().toLowerCase();
  if (kw === "inherit" || kw === "unset") return computedValue(el.parentElement, prop, rules, depth + 1);
  if (kw === "initial") return INITIAL[prop] ?? null;
  if (kw === "revert" || kw === "revert-layer") return { gap: `${prop}: ${kw} on ${describe(el)}` };
  const resolved = substitute(el, v, rules, depth + 1);
  if (isGap(resolved)) return resolved;
  const t = resolved.trim().toLowerCase();
  if (t === "currentcolor") return computedValue(prop === "color" ? el.parentElement : el, "color", rules, depth + 1);
  if (t.startsWith("url(")) return { gap: `${prop} on ${describe(el)} is a paint server, ${t}` };
  return normalizeColour(t);
}

// Is this shape painted at all? Inline declarations and presentation attributes on the shape
// and its ancestors up to the marker element: what a page script changes at run time.
const ownValue = (el, prop) => el.style.getPropertyValue(prop) || (SVG_TAGS.has(el.tagName) ? el.getAttribute(prop) ?? "" : "");
function painted(shape, top) {
  let visibility = "";
  let fillOpacity = "";
  for (let a = shape; a; a = a.parentElement) {
    if (/^none$/i.test(ownValue(a, "display").trim())) return false;
    if (Number.parseFloat(ownValue(a, "opacity")) === 0) return false;
    visibility ||= ownValue(a, "visibility");
    fillOpacity ||= ownValue(a, "fill-opacity");
    if (a === top) break;
  }
  return !/^(hidden|collapse)$/i.test(visibility.trim()) && Number.parseFloat(fillOpacity) !== 0;
}

// Global coverage gaps: any one of them makes every reading unresolved.
function harnessGaps() {
  const out = [...gaps, ...[...selectorGaps].map((s) => `unimplemented selector ${s}`)];
  out.push(...engineErrors.map((e) => `production raised ${e}`));
  for (const link of linkElements) if (/stylesheet/i.test(link.getAttribute("rel") ?? "")) out.push(`a page <link rel="stylesheet">`);
  // The F11/F12 rule for selectors, applied to members: a property production read that this
  // DOM does not have is a gap wherever it was read (a guarded `if (el.someApi)` on a staging
  // element is as silent as a swallowed throw). Production reads none once loaded.
  for (const el of allEls) if (el._unknown.size) out.push(`${describe(el)}.${[...el._unknown][0]} read but not modelled by this DOM`);
  for (const name of documentUnknown) out.push(`document.${name} read but not modelled by this DOM`);
  return out;
}

const GEOMETRY = new Set(["PATH", "RECT", "CIRCLE", "ELLIPSE", "POLYGON", "POLYLINE", "LINE"]);
const NON_PAINTING = new Set(["SVG", "G", "DIV", "SPAN", "TITLE", "DESC", "STYLE", "#TEXT"]);
const descendants = (el) => el.children.flatMap((c) => (c instanceof El ? [c, ...descendants(c)] : []));
function fillsOf(marker) {
  const el = marker && marker.getElement();
  if (!el) return ["no element"];
  const global = harnessGaps();
  if (global.length) return [`unresolved (${global[0]})`];
  const nodes = descendants(el);
  const odd = nodes.find((n) => !GEOMETRY.has(n.tagName) && !NON_PAINTING.has(n.tagName));
  if (odd) return [`unresolved (the icon holds a ${describe(odd)} this reader does not paint)`];
  const rules = currentRules();
  const out = [];
  for (const shape of nodes.filter((n) => GEOMETRY.has(n.tagName))) {
    if (!painted(shape, el)) continue;
    const v = computedValue(shape, "fill", rules);
    if (isGap(v)) return [`unresolved (${v.gap})`];
    if (v !== "none" && v !== "transparent") out.push(v);
  }
  return out;
}
const nameOf = (marker) => (marker && marker.getElement() ? marker.getElement().getAttribute("aria-label") : null);
const onMap = () => markerLog.filter((m) => m._onMap);

// A train's marker: the on-map marker production placed at that train's coordinates. A PATH
// train without glide anchors is drawn at its own latitude/longitude (trainLatLng), so the
// diamond's position tells the three apart; a 0.002 degree tolerance absorbs any small
// display offset, and the three stations are kilometres apart.
function markerFor(id) {
  const [lat, lon] = TRAIN_AT[id];
  const hits = onMap().filter((m) => {
    const ll = m.getLatLng();
    const [mlat, mlon] = Array.isArray(ll) ? ll : [ll.lat, ll.lng];
    return Math.abs(mlat - lat) < 0.002 && Math.abs(mlon - lon) < 0.002;
  });
  if (hits.length !== 1) throw new Error(`harness: ${hits.length} on-map markers at ${id}'s position`);
  return hits[0];
}

/* ==================================================================
   Drive it: only the clock moves; map.js's own kickoff does everything else
   ================================================================== */

// What the static loader drew for one route: the polylines production constructed on that
// route's shapes, and the colours it gave them.
function linesFor(routeId) {
  const shapes = fixtures.pathRoutes().find((r) => r.id === routeId).shape.map((s) => JSON.stringify(s));
  return polylineLog.filter((line) => shapes.includes(JSON.stringify(line.points)));
}
const tableState = () => ({
  routeRequests: requests.routes.map((r) => ({ ...r })),
  linesDrawn: polylineLog.length,
  route862Colours: [...new Set(linesFor("862").map((l) => normalizeColour(l.options.color)))],
  polls: requests.polls.length,
});
const virtualS = () => (clock.ms - FROZEN_MS) / 1000;
const timeline = [];
function look(label, ids) {
  const row = { label: `t=${virtualS()}s, ${label}`, polls: requests.polls.length, trains: {} };
  for (const id of ids) {
    const m = markerFor(id);
    row.trains[id] = { marker: m, colours: fillsOf(m), renders: m.renders, name: nameOf(m) };
  }
  row.markersConstructed = markerLog.length;
  row.markersOnMap = onMap().length;
  timeline.push(row);
  return row;
}

// Boot. map.js ran at load: retryUntil(loadPathRoutes) met the 503 and armed its backoff, and
// refreshAll() fetched the first PATH poll. Drain the promise queue and look.
await settle();
await advanceUntil(() => requests.polls.length >= 1 && markerLog.length >= 2, "the first PATH poll");
const atBoot = tableState();
look("boot: poll 1 landed while the route table backs off after its first answer", ["p-1", "p-2"]);

// Production's retry lands the table (whenever its backoff says).
await advanceUntil(() => linesFor("862").length > 0, "the route table landing");
const afterRetry = tableState();
const pollsAtTable = requests.polls.length;
look("the retry landed the route table", ["p-1", "p-2"]);

// map.js's own interval polls on; p-3 appears in the first poll after the table.
for (let k = 1; k <= 4; k += 1) {
  await advanceUntil(() => requests.polls.length >= pollsAtTable + k, `poll ${pollsAtTable + k}`);
  look(k < 4 ? `poll ${pollsAtTable + k}` : `poll ${pollsAtTable + k}, p-2 now served on route 859`, ["p-1", "p-2", "p-3"]);
}
const atEnd = tableState();
watching = false;
session.disconnect();

/* ==================================================================
   Report
   ================================================================== */

const rule = (t) => { say("=".repeat(78)); say(t); say("=".repeat(78)); };
const [poll1, landed, post1, post2, post3, post4] = timeline;
const afterTable = [landed, post1, post2, post3];
const same = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
const preTable = ["p-1", "p-2"];

const fixtureRoute = (id) => fixtures.pathRoutes().find((r) => r.id === id);
const ROUTE_862 = normalizeColour(`#${fixtureRoute("862").color}`);
const ROUTE_859 = normalizeColour(`#${fixtureRoute("859").color}`);
// THE FALLBACK is what poll 1 measured, before any route colour existed: the one colour both
// pre-table diamonds were drawn in.
const poll1Fills = preTable.map((id) => poll1.trains[id].colours);
const FALLBACK = poll1Fills[0].length === 1 && same(poll1Fills[0], poll1Fills[1]) ? poll1Fills[0][0] : `(${JSON.stringify(poll1Fills)})`;
// A reading is a colour only if it resolved to one: an "unresolved (...)" reading equals
// nothing, not even another unresolved reading.
const isColour = (v) => /^#[0-9a-f]{6}$/.test(v);

function hsl(hex) {
  if (!/^#[0-9a-f]{6}$/.test(hex)) return { h: NaN, s: NaN, l: NaN };
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  let h = 0;
  if (d) {
    if (max === r) h = 60 * (((g - b) / d) % 6);
    else if (max === g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
  }
  return { h: Math.round((h + 360) % 360), s: Math.round(s * 100) / 100, l: Math.round(l * 100) / 100 };
}
const describeColour = (hex) => {
  const { h, s, l } = hsl(hex);
  return Number.isNaN(h) ? hex : `${hex} (hue ${h} deg, saturation ${s}, lightness ${l})`;
};
const coverage = harnessGaps();
const unknownMembers = [
  ...allEls.filter((el) => el._unknown.size).map((el) => `${describe(el)}.${[...el._unknown].join("|")}`),
  ...[...documentUnknown].map((name) => `document.${name}`),
];

rule("A12 (Low): PATH keeps fallback colours after delayed metadata");
say("Production sources executed in one node:vm context, in index.html order (map.js's kickoff drives the run):");
for (const rel of SOURCES) say(`  ${rel}`);
say(`Icon rendering: the real L.DivIcon from ${LEAFLET_SRC} (Leaflet ${RealL.version}).`);
say(`Cascade: ${STATIC_SHEETS.map((s) => s.name).join(", ")} (${STATIC_RULES.length} declarations), plus ${styleElements.length} page-created <style>.`);
say(`Virtual clock: started at ${new Date(FROZEN_MS).toISOString()} (committed fixture FROZEN_MS), advanced to t=${virtualS()}s;`);
say(`  ${clock.fired} production timers fired, ${clock.framesRun} animation frames run, ${clock.timers.size} timers still pending.`);
say(`Cold /api/ endpoints answered 503 throughout: ${[...requests.cold.entries()].map(([u, n]) => `${u} x${n}`).join(", ")}`);
say("");
say("COLOURS");
say(`  fallback, measured on poll 1 (no route table yet) ... ${describeColour(FALLBACK)}`);
say(`  route 862 (Newark - WTC), committed fixture ......... ${describeColour(ROUTE_862)}`);
say(`  route 859 (Hoboken - 33rd), committed fixture ....... ${describeColour(ROUTE_859)}`);
say("");
say("THE ROUTE TABLE (/api/path-routes requests, virtual ms and status)");
say(`  at boot:     ${JSON.stringify(atBoot.routeRequests)}, lines drawn=${atBoot.linesDrawn}, PATH polls=${atBoot.polls}`);
say(`  after retry: ${JSON.stringify(afterRetry.routeRequests)}, lines drawn=${afterRetry.linesDrawn}, route 862 lines painted ${JSON.stringify(afterRetry.route862Colours)}, PATH polls=${afterRetry.polls}`);
say(`  by the end:  ${atEnd.routeRequests.length} requests; PATH polls served at ${JSON.stringify(requests.polls.map((p) => p.atMs))} ms`);
say("");
say("THE DIAMONDS: the computed fill of each painted shape in the element Leaflet rendered");
for (const row of timeline) {
  say(`  ${row.label}  [PATH polls so far: ${row.polls}]`);
  say(`      markers constructed=${row.markersConstructed} on map=${row.markersOnMap}`);
  for (const [id, t] of Object.entries(row.trains)) {
    say(`      ${id}: fill ${JSON.stringify(t.colours)}  element renders=${t.renders}`);
    say(`            name ${JSON.stringify(t.name)}`);
  }
}
say("");
say("HARNESS COVERAGE");
say(`  engine errors raised by production: ${engineErrors.length}; unimplemented selectors: ${selectorGaps.size}; refused URLs: ${requests.refused.length}`);
say(`  page-created <style>: ${styleElements.length}, <link>: ${linkElements.length}; CSSOM/markup gaps: ${gaps.size}`);
say(`  unmodelled DOM members production read: ${unknownMembers.join(", ") || "none"}`);
say("");

// Marker identity: the marker found for p-1 at every step is the same object, and nothing
// was ever constructed for it twice.
const p1Markers = new Set(timeline.map((row) => row.trains["p-1"].marker));
const p2Markers = new Set(timeline.map((row) => row.trains["p-2"].marker));
const statuses = (state) => state.routeRequests.map((r) => r.status);

say("ARITHMETIC");
say(`  p-1 after the table and three more polls paints ${JSON.stringify(post3.trains["p-1"].colours)}; the table says ${ROUTE_862}`);
say(`  p-3, created on the same route in the first poll after the table, paints ${JSON.stringify(post1.trains["p-3"].colours)}`);
say(`  re-renders of p-1's element after the table landed: ${post3.trains["p-1"].renders - poll1.trains["p-1"].renders}`);
say(`  p-2's element re-rendered on the route_id change: ${post4.trains["p-2"].renders - post3.trains["p-2"].renders} time(s), now paints ${JSON.stringify(post4.trains["p-2"].colours)}`);
say("");

const checks = [
  [
    "cold start: the route table's first request was refused, nothing was drawn, and poll 1 had already drawn both diamonds",
    same(statuses(atBoot), [503]) && atBoot.linesDrawn === 0 && atBoot.polls >= 1 && poll1.markersOnMap === 2,
    `route requests ${JSON.stringify(atBoot.routeRequests)}, lines=${atBoot.linesDrawn}, PATH polls=${atBoot.polls}, diamonds on map=${poll1.markersOnMap}`,
  ],
  [
    "the fallback both pre-table diamonds were drawn in is a gray, distinct from route 862's red (audit: \"gray\" vs \"the route's red\")",
    isColour(FALLBACK) && FALLBACK !== ROUTE_862 && hsl(FALLBACK).s < 0.25 && (hsl(ROUTE_862).h < 20 || hsl(ROUTE_862).h > 340) && hsl(ROUTE_862).s > 0.5,
    `fallback ${describeColour(FALLBACK)}; route ${describeColour(ROUTE_862)}`,
  ],
  [
    "production's own retry landed the table after poll 1, drew route 862's lines in its red, and asked no more",
    same(statuses(atEnd), [503, 200]) && requests.pollsBeforeTable >= 1 && same(afterRetry.route862Colours, [ROUTE_862]),
    `route requests ${JSON.stringify(atEnd.routeRequests)}, PATH polls before the 200=${requests.pollsBeforeTable}, 862 lines=${JSON.stringify(afterRetry.route862Colours)}`,
  ],
  [
    "the metadata reached the trains: p-1's accessible name took the route's real name",
    !(poll1.trains["p-1"].name ?? "").includes(fixtureRoute("862").name) &&
      (post1.trains["p-1"].name ?? "").includes(fixtureRoute("862").name),
    `before ${JSON.stringify(poll1.trains["p-1"].name)} / after ${JSON.stringify(post1.trains["p-1"].name)}`,
  ],
  [
    "THE FINDING: both pre-table diamonds still paint the fallback after the table and three later polls (audit: gray remained)",
    isColour(FALLBACK) &&
      afterTable.every((row) => preTable.every((id) => same(row.trains[id].colours, [FALLBACK]))) &&
      same(post4.trains["p-1"].colours, [FALLBACK]),
    [...afterTable, post4]
      .map((row) => preTable.map((id) => `${id}=${row.trains[id].colours.join(",")}`).join(" "))
      .join(" | ") + " (the last pair is the poll where p-2 changed route)",
  ],
  [
    "and their elements were never re-rendered after the table landed (no setIcon reached them)",
    preTable.every((id) => post3.trains[id].renders === poll1.trains[id].renders) &&
      post4.trains["p-1"].renders === poll1.trains["p-1"].renders,
    `p-1: ${poll1.trains["p-1"].renders} -> ${post4.trains["p-1"].renders} through the route-change poll, ` +
      `p-2: ${poll1.trains["p-2"].renders} -> ${post3.trains["p-2"].renders} before it`,
  ],
  [
    "CONTROL: a route-862 diamond created after the table paints the route's red, in the same polls",
    [post1, post2, post3].every((row) => same(row.trains["p-3"].colours, [ROUTE_862])),
    [post1, post2, post3].map((row) => `p-3=${row.trains["p-3"].colours.join(",")}`).join(" "),
  ],
  [
    "MECHANISM: a route_id change does re-render the icon, in the new route's colour (audit: refreshes when route ID changes)",
    same(post4.trains["p-2"].colours, [ROUTE_859]) && post4.trains["p-2"].renders > post3.trains["p-2"].renders,
    `p-2 ${post3.trains["p-2"].colours.join(",")} -> ${post4.trains["p-2"].colours.join(",")}, element renders ${post3.trains["p-2"].renders} -> ${post4.trains["p-2"].renders}`,
  ],
  [
    "marker identity is preserved throughout: one marker per train, none rebuilt",
    p1Markers.size === 1 && p2Markers.size === 1 && post4.markersConstructed === 3 && post4.markersOnMap === 3,
    `p-1 markers=${p1Markers.size} p-2 markers=${p2Markers.size} constructed=${post4.markersConstructed} on map=${post4.markersOnMap}`,
  ],
  [
    "HARNESS COVERAGE: everything production did to the page is something this harness models, and it asked for no URL the harness does not serve",
    coverage.length === 0 && requests.refused.length === 0,
    coverage.length || requests.refused.length
      ? [...coverage.slice(0, 4), ...requests.refused.map((u) => `refused ${u}`)].join("; ")
      : "no engine error, no unimplemented selector, no unmodelled DOM member read, no CSSOM use, no refused URL",
  ],
];

let failed = 0;
rule("CHECKS");
for (const [name, ok, detail] of checks) {
  if (!ok) failed += 1;
  say(`  ${ok ? "PASS" : "FAIL"}  ${name}`);
  say(`        measured: ${detail}`);
}
say("");

if (failed) {
  say(`${failed} of ${checks.length} checks failed.`);
  say(NOT_AS_RECORDED);
  process.exit(1);
}
say(`All ${checks.length} checks passed.`);
say(
  `DISPOSITION: VERIFIED  Two PATH diamonds drawn before the route table landed paint the fallback ${FALLBACK} ` +
    `and still paint ${FALLBACK} after the table supplied route 862's ${ROUTE_862} and ${post3.polls - landed.polls} ` +
    `more polls, with ${post3.trains["p-1"].renders - poll1.trains["p-1"].renders} re-renders, while a route-862 ` +
    `diamond created after the table paints ${ROUTE_862} in the same poll; only a route_id change re-renders the icon.`,
);
process.exit(0);
