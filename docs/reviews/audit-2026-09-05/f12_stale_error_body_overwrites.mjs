#!/usr/bin/env node
/*
 * F12 (P2): "An old station's delayed error can overwrite the current station."
 * Verification harness for the 2026-09-05 audit record.
 *
 * RUN IT (from the repository root):
 *     node docs/reviews/audit-2026-09-05/f12_stale_error_body_overwrites.mjs
 * Exits 0 while the record matches the code, non-zero when it does not.
 * No network is used, no wall clock is read, and nothing outside this directory is written.
 *
 * F12 IS FIXED ON claude/release1-small-fixes, SO ARM 1 NOW CHECKS THE FIX. The
 * interleaving it drives is unchanged, byte for byte: station A's 503 headers, a switch
 * to B, B's good rows, then A's delayed error body. What changed is what the panel is
 * required to do with that last event, and the checks below carry both halves so the
 * before is not lost. BEFORE: B's 12 arrival rows fell to 0 behind A's warming message,
 * panelError took A's detail, and the live region spoke it under B's name. AFTER:
 * fetchPanelArrivals checks its sequence ONCE, after every await it makes and before it
 * writes anything, so A's body is read and discarded. This arm exits non-zero the moment
 * that guard is removed, which is what makes it a regression check rather than a record.
 *
 * ARMS 2, 3 AND 4 ARE UNCHANGED AND STILL RECORD THE FINDINGS AS FOUND. Arm 4 is N3, a
 * separate item; when it is fixed its checks move the same way arm 1's did here.
 *
 * THE AUDIT'S CLAIM, quoted from docs/reviews/audit-2026-09-05.md section 1:
 *
 *     "### F12 - An old station's delayed error can overwrite the current station
 *
 *      Evidence: panel error-response path (frontend/stations.js#L464).
 *
 *      The controller checks its sequence after response headers arrive, then awaits
 *      error JSON without another sequence check. In the reproduced sequence, station A's
 *      503 headers arrived; the user selected B; B's successful data rendered; then A's
 *      delayed error body completed and set the shared panel error. B remained selected
 *      but its good arrivals were hidden behind A's warming message.
 *
 *      Remedy: recheck request identity immediately after each awaited body read,
 *      including error branches. Audit analogous popup and bus-route error branches. [...]
 *
 *      Acceptance: split headers/body delivery in a regression; neither an old successful
 *      body nor an old error body may change the selected station's state."
 *
 *   (The audit's own em dashes are written as plain hyphens above, per this repository's
 *    no-em-dash rule. Nothing else in the quotation is altered.)
 *
 * WHAT THIS SCRIPT MEASURES
 *   Arm 1  the finding, and since the fix the regression check on it. The real panel
 *          controller is driven through the exact interleaving above, with headers and
 *          body delivered as two separately controlled promises, and the panel's rendered
 *          DOM is printed after each step. Measured: which station is selected, how many
 *          arrival rows the panel holds, and what the shared live region last said.
 *   Arm 2  the success-branch control the acceptance criterion also names. Same
 *          interleaving, except station A answers 200 with a delayed GOOD body. This is
 *          what separates "the error branch is missing one guard" from "the controller is
 *          unguarded". Only one of the two halves of the acceptance criterion can fail.
 *   Arm 3  the analogous popup branch the remedy names
 *          (frontend/systems/shared.js openStationArrivals). Two orderings are driven:
 *          3a two different station markers, 3b one marker closed and reopened.
 *   Arm 4  the analogous bus-route branch the remedy names
 *          (frontend/systems/buses.js showBusRoute). Two orderings again:
 *          4a the stale error lands while a newer route fetch is still in flight,
 *          4b the stale error lands after a newer route fetch has already drawn.
 *
 * WHAT IS PRODUCTION CODE (nothing under test is re-implemented here)
 *   frontend/helpers.js          loaded whole. shapeStationArrivals, arrivalSentence,
 *                                staleAge, announcementWorthy, FETCH_DEADLINE_MS,
 *                                railroadArrivalsHtml, esc, routeColor and the rest.
 *   frontend/systems/shared.js   loaded whole. registerStation, the real stationRegistry,
 *                                bindStationPopup and openStationArrivals (arm 3).
 *   frontend/stations.js         loaded whole. selectStation, fetchPanelArrivals (the
 *                                function at line 464 the audit links), renderStationDetail,
 *                                announceState, panelStation / panelSeq / panelError.
 *   frontend/systems/buses.js    loaded whole. showBusRoute, clearBusRoute, setBusRouteNote,
 *                                busRouteOwnedBy, busPopup (arm 4).
 *   All four files are executed as their own source text inside one node:vm context, in the
 *   same order index.html loads them, so every function called below is the shipped one.
 *   The panel state is read back with probe expressions compiled into that same context,
 *   because `let panelError` lives in the context's global lexical scope rather than on the
 *   global object. No production logic is copied into this file, with one narrow exception
 *   noted under INJECTED below.
 *
 * WHAT IS INJECTED (and why)
 *   * A DOM stub: getElementById / createElement / appendChild / replaceChildren /
 *     textContent / classList, enough for the panel renderer and the popup writers. The
 *     rendered tree is a plain object graph this script can count nodes in.
 *   * A Leaflet stub: every L.* call returns a chainable recorder. shared.js and buses.js
 *     build layers and markers at load; none of that is under test. L.polyline calls are
 *     counted in arm 4 because "did the route line get drawn" is a measured fact there.
 *   * fetch: replaced by a deck of controllable calls. Each call hands back a promise for
 *     the RESPONSE (the headers) and a second, separate promise for .json() (the body), so
 *     the two can be delivered in either order at any point. This is the whole point of the
 *     harness: production awaits those two things at two different places, and the audit's
 *     claim is about what happens between them. Nothing reaches a network; there is no
 *     NJ Transit code path here at all.
 *   * The clock is frozen at the arrivals fixture's own capture header
 *     (backend/tests/fixtures/railroad_lirr_arrivals_expected.json, field "now" =
 *     1782006915.0) by replacing Date inside the context. Every age the panel computes is
 *     therefore measured against that capture, never against today's date, and the run is
 *     identical on any day.
 *   * setInterval is a no-op returning 0, so the one-second repaint never fires on its own.
 *     Arm 1 calls the production renderer with {tick: true} explicitly instead, which is
 *     what that timer does, so the tick is measured rather than merely stubbed away.
 *   * The two arrivals payloads are built from the committed capture named above:
 *     {fetched_at: <that capture's own now>, system, stop_id, stop_name, directions: <its
 *     own per-station rows>}, which is the shape backend/models.py RailroadStationArrivals
 *     serves. The rows themselves are the fixture's, unedited.
 *   * The 503 detail string is the production one, copied from backend/cache.py
 *     _serve_cached: "Feed cache is warming up; try again in a few seconds." That is the
 *     "warming message" the audit refers to.
 *   * THE ONE NARROW COPY: arm 3 needs the popup descriptor that systems/railroad.js builds
 *     at its lines 85 to 100 and hands to bindStationPopup. railroad.js cannot be loaded
 *     here (it fetches its stop list at load), so that descriptor literal is reproduced
 *     verbatim below and marked. It is four fields and a render call; every function it
 *     calls (stationAlertsBlock, railroadArrivalsHtml) is production, and the code under
 *     test in arm 3 (openStationArrivals) is production and untouched.
 */

import fs from "node:fs";
import path from "node:path";
import url from "node:url";
import vm from "node:vm";

const HERE = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..", "..");
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8");
const readJson = (rel) => JSON.parse(read(rel));

/* ------------------------------------------------------------------ fixtures */

const ARRIVALS_FIXTURE = "backend/tests/fixtures/railroad_lirr_arrivals_expected.json";
const STOPS_FIXTURE = "backend/tests/fixtures/railroad_lirr_stops.json";
const capture = readJson(ARRIVALS_FIXTURE);
const stops = readJson(STOPS_FIXTURE);

// The capture's own header. Every age below is measured against this and nothing else.
const CAPTURE_NOW = capture.now;
const FROZEN_MS = CAPTURE_NOW * 1000;

// Station A is the one whose request fails; station B is the one the rider switches to.
const A_ID = "113"; // Long Beach
const B_ID = "102"; // Jamaica
const SYSTEM = capture.system; // "LIRR"

// Production 503 details, copied from the backend that emits them:
// backend/cache.py _serve_cached, and backend/routes/buses.py get_bus_route.
const WARMING_DETAIL = "Feed cache is warming up; try again in a few seconds.";
const BUS_ROUTE_DETAIL = "Bus route shapes are still indexing; try again in a minute.";

function arrivalsBody(stopId) {
  return {
    fetched_at: CAPTURE_NOW,
    system: SYSTEM,
    stop_id: stopId,
    stop_name: stops[stopId].name,
    directions: capture.arrivals[stopId],
  };
}

function fixtureRowCount(stopId) {
  return Object.values(capture.arrivals[stopId]).reduce((n, rows) => n + rows.length, 0);
}

/* ------------------------------------------------------------------ DOM stub */

function makeEl(id) {
  const el = {
    id,
    tagName: null,
    hidden: false,
    textContent: "",
    className: "",
    innerHTML: "",
    value: "",
    style: {},
    children: [],
    parentElement: null,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      toggle(c, on) { if (on) this._s.add(c); else this._s.delete(c); },
      contains(c) { return this._s.has(c); },
    },
    setAttribute() {},
    removeAttribute() {},
    getAttribute() { return null; },
    addEventListener() {},
    removeEventListener() {},
    focus() {},
    click() {},
    closest() { return null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    contains() { return false; },
    remove() {},
    appendChild(child) { this.children.push(child); child.parentElement = this; return child; },
    append(...cs) { for (const c of cs) this.appendChild(c); },
    replaceChildren(...cs) { this.children = []; for (const c of cs) this.appendChild(c); },
    insertBefore(child) { return this.appendChild(child); },
  };
  return el;
}

function makeDocument() {
  const byId = new Map();
  return {
    _byId: byId,
    body: makeEl("body"),
    documentElement: makeEl("html"),
    activeElement: null,
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, makeEl(id));
      return byId.get(id);
    },
    createElement(tag) {
      const el = makeEl(null);
      el.tagName = String(tag).toUpperCase();
      return el;
    },
    createTextNode(t) { const el = makeEl(null); el.tagName = "#text"; el.textContent = t; return el; },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

/* -------------------------------------------------------------- Leaflet stub */

// Every L.* member is a recorder that returns the same chainable node, which is all
// shared.js and buses.js need at load. Calls are recorded so arm 4 can count polylines.
function leafletStub() {
  const calls = [];
  const node = new Proxy(
    { __leaflet: true, calls },
    {
      get(target, prop) {
        if (prop in target) return target[prop];
        if (typeof prop === "symbol") return undefined;
        return (...args) => {
          calls.push({ method: String(prop), args });
          return node;
        };
      },
      set(target, prop, value) { target[prop] = value; return true; },
    },
  );
  return node;
}

/* ------------------------------------------------------------- fetch control */

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function fetchDeck() {
  const calls = [];
  const fetchImpl = (requestUrl) => {
    const call = { url: String(requestUrl), headers: deferred(), body: deferred(), state: "open" };
    calls.push(call);
    return call.headers.promise;
  };
  return {
    calls,
    fetchImpl,
    // Deliver only the HEADERS. The body stays pending until deliverBody is called.
    deliverHeaders(call, { ok, status }) {
      call.state = ok ? "headers-200" : `headers-${status}`;
      call.headers.resolve({
        ok,
        status,
        json: () => call.body.promise,
      });
    },
    deliverBody(call, payload) {
      call.state = "body-delivered";
      call.body.resolve(payload);
    },
  };
}

// Let every pending microtask and promise job in the vm context run to completion.
// The vm shares this isolate's microtask queue, so awaiting a macrotask here drains it.
const settle = async (turns = 8) => {
  for (let i = 0; i < turns; i += 1) await new Promise((r) => setImmediate(r));
};

/* ------------------------------------------------------- the loaded frontend */

// index.html loads helpers, shared, the system files, then stations.js. The bus arm
// needs systems/buses.js and systems/subway.js (which owns the shared `lineRenderer`
// the bus route line draws into), so the order below is index.html's own, minus the
// system files that fetch their static data at load and are irrelevant here.
const BASE_LOAD_ORDER = ["frontend/helpers.js", "frontend/systems/shared.js"];
const BUS_LOAD_ORDER = ["frontend/systems/buses.js", "frontend/systems/subway.js"];
const PANEL_LOAD_ORDER = ["frontend/stations.js"];
const FRONTEND_LOAD_ORDER = [...BASE_LOAD_ORDER, ...PANEL_LOAD_ORDER];

function bootFrontend({ withBuses = false } = {}) {
  const document = makeDocument();
  const L = leafletStub();
  const deck = fetchDeck();

  class FrozenDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(FROZEN_MS);
      else super(...args);
    }
    static now() { return FROZEN_MS; }
  }

  const sandbox = {
    document,
    L,
    console,
    Date: FrozenDate,
    fetch: (u, init) => deck.fetchImpl(u, init),
    AbortSignal: { timeout: () => ({ __abortSignalStub: true }) },
    setInterval: () => 0,
    clearInterval: () => {},
    setTimeout,
    clearTimeout,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: () => {},
    matchMedia: (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }),
    addEventListener: () => {},
    removeEventListener: () => {},
    URLSearchParams,
    URL,
    Math, JSON, Set, Map, WeakMap, Promise, Array, Object, Number, String, Boolean,
    Error, TypeError, RangeError, RegExp, Intl, Symbol, performance,
    isNaN, isFinite, parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.self = sandbox;

  const ctx = vm.createContext(sandbox);
  const files = withBuses
    ? [...BASE_LOAD_ORDER, ...BUS_LOAD_ORDER, ...PANEL_LOAD_ORDER]
    : FRONTEND_LOAD_ORDER;
  for (const rel of files) vm.runInContext(read(rel), ctx, { filename: rel });

  // `railroadRouteNames` is declared in systems/railroad.js, which is not loaded here.
  // Empty is a real production state: the route-names endpoint has not resolved yet, and
  // the panel then prints route ids. Nothing in this finding depends on route names.
  vm.runInContext("var railroadRouteNames = new Map();", ctx, { filename: "harness:route-names" });

  const probe = (expr) => vm.runInContext(`(${expr})`, ctx, { filename: "harness:probe" });
  const exec = (src) => vm.runInContext(src, ctx, { filename: "harness:exec" });

  return { ctx, document, L, deck, probe, exec, sandbox };
}

/* ------------------------------------------------- reading the rendered panel */

function walkText(el, out = []) {
  if (el.textContent) out.push({ tag: el.tagName, cls: el.className, text: el.textContent });
  for (const child of el.children) walkText(child, out);
  return out;
}

function countTag(el, tag, n = 0) {
  for (const child of el.children) {
    if (child.tagName === tag) n += 1;
    n = countTag(child, tag, n);
  }
  return n;
}

function panelSnapshot(env, label) {
  const detail = env.document.getElementById("stations-detail");
  const announce = env.document.getElementById("stations-announce");
  const nodes = walkText(detail);
  return {
    label,
    selected: env.probe("panelStation && panelStation.key"),
    seq: env.probe("panelSeq"),
    error: env.probe("panelError"),
    hasBody: env.probe("panelBody != null"),
    bodyStop: env.probe("panelBody && panelBody.stop_id"),
    arrivalRows: countTag(detail, "LI"),
    headings: nodes.filter((n) => n.tag === "H3").map((n) => n.text),
    notes: nodes.filter((n) => n.cls === "station-detail-note").map((n) => n.text),
    firstRows: nodes.filter((n) => n.tag === "LI").slice(0, 2).map((n) => n.text),
    announced: announce.textContent,
  };
}

function printPanel(step, snap) {
  console.log(`  ${step}`);
  console.log(`      selected station .... ${snap.selected ?? "(none)"}   panelSeq=${snap.seq}`);
  console.log(`      panel heading ....... ${snap.headings.join(" | ") || "(none)"}`);
  console.log(`      arrival rows drawn .. ${snap.arrivalRows}`);
  console.log(`      panelBody stop_id ... ${snap.bodyStop ?? "(null)"}`);
  console.log(`      panelError .......... ${snap.error === null ? "(null)" : JSON.stringify(snap.error)}`);
  if (snap.notes.length) console.log(`      note paragraph ...... ${JSON.stringify(snap.notes[0])}`);
  if (snap.firstRows.length) console.log(`      first rows .......... ${snap.firstRows.map((r) => JSON.stringify(r)).join(", ")}`);
  console.log(`      live region says .... ${JSON.stringify(snap.announced)}`);
}

/* -------------------------------------------------------- panel arm scaffolding */

function registerTwoStations(env) {
  // The real registerStation from systems/shared.js, given entries shaped exactly as
  // systems/railroad.js shapes them (key/kind/systemLabel/noun/id/system/name/lat/lon/
  // routes/wheelchair/arrivalsUrl/nameFor). marker and layer are left out on purpose:
  // syncMapToStation returns immediately without a marker, which keeps the map out of
  // this measurement without touching the code under test.
  env.exec(`
    for (const stopId of ${JSON.stringify([A_ID, B_ID])}) {
      const meta = ${JSON.stringify({ [A_ID]: stops[A_ID], [B_ID]: stops[B_ID] })}[stopId];
      registerStation({
        key: "${SYSTEM}|" + stopId,
        kind: "railroad",
        systemLabel: "${SYSTEM}",
        noun: "train",
        id: stopId,
        system: "${SYSTEM}",
        name: meta.name,
        lat: meta.lat,
        lon: meta.lon,
        routes: [],
        wheelchair: false,
        arrivalsUrl: "/api/railroad-arrivals/${SYSTEM}/" + encodeURIComponent(stopId),
        nameFor: () => null,
      });
    }
  `);
  // The panel is on screen: this finding is about what a rider reading the panel sees.
  env.document.getElementById("stations-panel").hidden = false;
}

/* ========================================================================== */
/* ARM 1: the finding                                                          */
/* ========================================================================== */

async function armOne() {
  console.log("");
  rule("ARM 1  the finding: station A's delayed 503 body versus station B's good data");
  const env = bootFrontend();
  registerTwoStations(env);

  const keyA = `${SYSTEM}|${A_ID}`;
  const keyB = `${SYSTEM}|${B_ID}`;
  const nameA = stops[A_ID].name;
  const nameB = stops[B_ID].name;
  console.log(`  station A = ${keyA} (${nameA}), station B = ${keyB} (${nameB})`);
  console.log(`  arrivals capture: ${ARRIVALS_FIXTURE}, header now=${CAPTURE_NOW} (the frozen clock)`);
  console.log(`  fixture rows: A=${fixtureRowCount(A_ID)}, B=${fixtureRowCount(B_ID)}`);
  console.log("");

  // step 1: the rider selects station A. Production selectStation starts the fetch.
  env.exec(`selectStation(${JSON.stringify(keyA)});`);
  await settle();
  const s1 = panelSnapshot(env, "after selecting A");
  printPanel("step 1  rider selects A; fetchPanelArrivals is in flight", s1);

  const callA = env.deck.calls[0];
  if (!callA) throw new Error("harness: station A's fetch never happened");

  // step 2: A's response HEADERS arrive with a warming 503. The body stays pending.
  env.deck.deliverHeaders(callA, { ok: false, status: 503 });
  await settle();
  const s2 = panelSnapshot(env, "A headers 503, body pending");
  printPanel("step 2  A answers 503 HEADERS; its error body has not arrived", s2);

  // step 3: the rider selects station B while A's error body is still in flight.
  env.exec(`selectStation(${JSON.stringify(keyB)});`);
  await settle();
  const s3 = panelSnapshot(env, "B selected");
  printPanel("step 3  rider selects B; panelSeq is bumped past A's request", s3);

  const callB = env.deck.calls[1];
  if (!callB) throw new Error("harness: station B's fetch never happened");

  // step 4: B answers 200 and its good body lands. The panel renders B's arrivals.
  env.deck.deliverHeaders(callB, { ok: true, status: 200 });
  env.deck.deliverBody(callB, arrivalsBody(B_ID));
  await settle();
  const s4 = panelSnapshot(env, "B rendered");
  printPanel("step 4  B answers 200 and renders its arrivals", s4);

  // step 5: A's delayed error BODY finally completes.
  env.deck.deliverBody(callA, { detail: WARMING_DETAIL });
  await settle();
  const s5 = panelSnapshot(env, "A's stale error body landed");
  printPanel("step 5  A's delayed 503 BODY completes, long after A stopped being selected", s5);

  // step 6: the one-second repaint the production tick performs. Is this a one-frame
  // flicker, or state the panel keeps repainting?
  env.exec("renderStationDetail({ tick: true });");
  const s6 = panelSnapshot(env, "after a production tick repaint");
  printPanel("step 6  one production tick repaint later (renderStationDetail({tick:true}))", s6);

  console.log("");
  console.log("  ARITHMETIC");
  console.log(`      B's arrival rows before A's stale body:  ${s4.arrivalRows}`);
  console.log(`      B's arrival rows after A's stale body:   ${s5.arrivalRows}`);
  console.log(`      rows hidden by a request the rider abandoned two steps earlier: ${s4.arrivalRows - s5.arrivalRows}`);
  console.log(`      selected station never changed:          ${s4.selected} -> ${s5.selected}`);
  console.log(`      panelBody still holds B's payload:       stop_id=${s5.bodyStop}`);
  console.log(`      panelSeq at A's request = 1, at B's = ${s5.seq}; A's body was read with no re-check of that`);

  return { s1, s2, s3, s4, s5, s6, env };
}

/* ========================================================================== */
/* ARM 2: the success-branch control                                           */
/* ========================================================================== */

async function armTwo() {
  console.log("");
  rule("ARM 2  control: the same interleaving with a delayed SUCCESS body from A");
  const env = bootFrontend();
  registerTwoStations(env);
  const keyA = `${SYSTEM}|${A_ID}`;
  const keyB = `${SYSTEM}|${B_ID}`;

  env.exec(`selectStation(${JSON.stringify(keyA)});`);
  await settle();
  const callA = env.deck.calls[0];

  // A answers 200 this time. Headers only; the good body stays pending.
  env.deck.deliverHeaders(callA, { ok: true, status: 200 });
  await settle();
  printPanel("step 2  A answers 200 HEADERS; its body has not arrived", panelSnapshot(env, "A headers 200"));

  env.exec(`selectStation(${JSON.stringify(keyB)});`);
  await settle();
  const callB = env.deck.calls[1];
  env.deck.deliverHeaders(callB, { ok: true, status: 200 });
  env.deck.deliverBody(callB, arrivalsBody(B_ID));
  await settle();
  const before = panelSnapshot(env, "B rendered");
  printPanel("step 4  B answers 200 and renders its arrivals", before);

  // A's stale SUCCESS body completes.
  env.deck.deliverBody(callA, arrivalsBody(A_ID));
  await settle();
  const after = panelSnapshot(env, "A's stale success body landed");
  printPanel("step 5  A's delayed SUCCESS body completes", after);

  console.log("");
  console.log("  ARITHMETIC");
  console.log(`      rows before A's stale success body: ${before.arrivalRows}   panelBody stop_id=${before.bodyStop}`);
  console.log(`      rows after  A's stale success body: ${after.arrivalRows}   panelBody stop_id=${after.bodyStop}`);
  console.log(`      A's own fixture row count is ${fixtureRowCount(A_ID)}; the panel never showed it`);
  console.log("      the guard that stops this is stations.js line 493, the seq re-check the error branch lacks");

  return { before, after };
}

/* ========================================================================== */
/* ARM 3: the analogous popup branch                                           */
/* ========================================================================== */

// VERBATIM COPY, and the only one in this file: systems/railroad.js lines 85 to 100 build
// this descriptor and hand it to the production bindStationPopup. railroad.js itself cannot
// be loaded (it fetches its stop list at load), so the literal is reproduced here. Every
// function it names is production code loaded from shared.js and helpers.js.
const RAILROAD_DESCRIPTOR_SRC = `(station, arrivalsUrl) => (m) => ({
  station,
  marker: m,
  body: null,
  url: arrivalsUrl,
  render: (s, b) =>
    stationAlertsBlock(s.system, s, b) +
    railroadArrivalsHtml(
      s,
      b,
      Date.now() / 1000 - (minClockOffset ?? 0),
      (routeId) => railroadRouteNames.get(\`\${s.system}|\${routeId}\`) || null,
    ),
})`;

function makeMarker(name) {
  const marker = {
    name,
    popupOpen: false,
    popupContent: null,
    writes: [],
    handlers: new Map(),
    bindPopup() { return marker; },
    on(event, fn) {
      if (!marker.handlers.has(event)) marker.handlers.set(event, []);
      marker.handlers.get(event).push(fn);
      return marker;
    },
    addTo() { return marker; },
    setPopupContent(html) { marker.popupContent = html; marker.writes.push(html); return marker; },
    isPopupOpen() { return marker.popupOpen; },
    getPopup() { return null; },
    fire(event) { for (const fn of marker.handlers.get(event) ?? []) fn.call(marker); },
  };
  return marker;
}

function popupHarness(env) {
  const build = env.probe(RAILROAD_DESCRIPTOR_SRC);
  const bind = (marker, station, arrivalsUrl) => {
    env.probe("bindStationPopup")(marker, build(station, arrivalsUrl));
  };
  return { bind };
}

function popupSummary(marker) {
  const html = marker.popupContent ?? "";
  return {
    open: marker.popupOpen,
    writes: marker.writes.length,
    hasWarming: html.includes(WARMING_DETAIL),
    // One rendered arrival row per route badge; see railroadArrivalsHtml in helpers.js.
    trainRows: (html.match(/arr-badge/g) ?? []).length,
    head: html.slice(0, 96).replace(/\s+/g, " "),
  };
}

function printPopup(label, marker) {
  const s = popupSummary(marker);
  console.log(`      ${label}: open=${s.open} writes=${s.writes} warmingText=${s.hasWarming} rows=${s.trainRows}`);
  console.log(`          content head: ${JSON.stringify(s.head)}`);
}

async function armThree() {
  console.log("");
  rule("ARM 3  the analogous popup branch (systems/shared.js openStationArrivals)");

  /* 3a: two different markers. */
  const envA = bootFrontend();
  const harnessA = popupHarness(envA);
  const stationA = { id: A_ID, system: SYSTEM, name: stops[A_ID].name };
  const stationB = { id: B_ID, system: SYSTEM, name: stops[B_ID].name };
  const markerA = makeMarker("markerA");
  const markerB = makeMarker("markerB");
  harnessA.bind(markerA, stationA, `/api/railroad-arrivals/${SYSTEM}/${A_ID}`);
  harnessA.bind(markerB, stationB, `/api/railroad-arrivals/${SYSTEM}/${B_ID}`);

  console.log("  3a  rider opens A's popup, then clicks B's marker (two different markers)");
  markerA.popupOpen = true;
  markerA.fire("popupopen");
  await settle();
  const callA3 = envA.deck.calls[0];
  envA.deck.deliverHeaders(callA3, { ok: false, status: 503 });
  await settle();

  // Leaflet closes the old popup before opening the new one.
  markerA.popupOpen = false;
  markerA.fire("popupclose");
  markerB.popupOpen = true;
  markerB.fire("popupopen");
  await settle();
  const callB3 = envA.deck.calls[1];
  envA.deck.deliverHeaders(callB3, { ok: true, status: 200 });
  envA.deck.deliverBody(callB3, arrivalsBody(B_ID));
  await settle();
  printPopup("before A's stale body, marker B", markerB);
  const bWritesBefore = markerB.writes.length;
  const bContentBefore = markerB.popupContent;

  envA.deck.deliverBody(callA3, { detail: WARMING_DETAIL });
  await settle();
  printPopup("after  A's stale body, marker B", markerB);
  printPopup("after  A's stale body, marker A (closed)", markerA);
  const bUntouched = markerB.writes.length === bWritesBefore && markerB.popupContent === bContentBefore;
  const aGotStale = (markerA.popupContent ?? "").includes(WARMING_DETAIL);
  console.log(`      marker B untouched by A's stale error: ${bUntouched}`);
  console.log(`      marker A received the stale error instead: ${aGotStale}`);
  console.log("      reading: the popup write is addressed to the marker captured with the request,");
  console.log("      so a stale error cannot cross to a DIFFERENT station's popup the way the panel does.");

  /* 3b: one marker, closed and reopened. */
  console.log("");
  console.log("  3b  rider opens A's popup, closes it, reopens the SAME marker (production seq bump on close)");
  const envB = bootFrontend();
  const harnessB = popupHarness(envB);
  const marker = makeMarker("markerA");
  harnessB.bind(marker, stationA, `/api/railroad-arrivals/${SYSTEM}/${A_ID}`);

  marker.popupOpen = true;
  marker.fire("popupopen");
  await settle();
  const first = envB.deck.calls[0];
  envB.deck.deliverHeaders(first, { ok: false, status: 503 });
  await settle();

  marker.popupOpen = false;
  marker.fire("popupclose"); // production bumps stationSeq here
  marker.popupOpen = true;
  marker.fire("popupopen"); // production starts a second request
  await settle();
  const second = envB.deck.calls[1];
  envB.deck.deliverHeaders(second, { ok: true, status: 200 });
  envB.deck.deliverBody(second, arrivalsBody(A_ID));
  await settle();
  printPopup("before the first request's stale body", marker);
  const reopenedRows = popupSummary(marker).trainRows;

  envB.deck.deliverBody(first, { detail: WARMING_DETAIL });
  await settle();
  printPopup("after  the first request's stale body", marker);
  const overwritten = popupSummary(marker).hasWarming;
  console.log(`      stationSeq now = ${envB.probe("stationSeq")}; the stale body was read without re-checking it`);
  console.log(`      rows visible before the stale body: ${reopenedRows}; after: ${popupSummary(marker).trainRows}`);
  console.log(`      reopened popup overwritten by the abandoned request: ${overwritten}`);

  return { bUntouched, aGotStale, overwritten, reopenedRows };
}

/* ========================================================================== */
/* ARM 4: the analogous bus-route branch                                       */
/* ========================================================================== */

// The record shape systems/buses.js keeps in its `buses` map: marker, routeId, bearing
// and the latest decoded vehicle. Seeded directly because the bus feed loop is not what
// this arm measures; showBusRoute, clearBusRoute and busPopup are the production code.
function seedBus(env, bus) {
  const marker = makeMarker(`bus-${bus.id}`);
  env.sandbox.__seedMarker = marker;
  env.sandbox.__seedRecord = {
    marker,
    routeId: bus.route_id,
    bearing: null,
    latest: { id: bus.id, route_id: bus.route_id, latitude: 40.7, longitude: -73.9, bearing: null },
  };
  env.exec("buses.set(__seedRecord.latest.id, __seedRecord);");
  return marker;
}

async function armFour() {
  console.log("");
  rule("ARM 4  the analogous bus-route branch (systems/buses.js showBusRoute)");

  const ROUTE = "M15";
  const busA = { id: "MTA NYCT_0001", route_id: ROUTE };
  const busB = { id: "MTA NYCT_0002", route_id: ROUTE };

  /* 4a: the stale error lands while the newer route fetch is still in flight. */
  console.log("  4a  bus A's 503 body lands while bus B's route fetch is still in flight");
  const env = bootFrontend({ withBuses: true });
  seedBus(env, busA);
  seedBus(env, busB);

  env.exec(`showBusRoute(${JSON.stringify(busA)});`);
  await settle();
  const callA = env.deck.calls[0];
  env.deck.deliverHeaders(callA, { ok: false, status: 503 });
  await settle();

  env.exec(`showBusRoute(${JSON.stringify(busB)});`);
  await settle();
  const callB = env.deck.calls[1];

  const pendingBefore = env.probe("pendingBusId");
  const ownedBefore = env.probe(`busRouteOwnedBy(${JSON.stringify(busB.id)})`);
  console.log(`      before A's stale body: pendingBusId=${JSON.stringify(pendingBefore)} busRouteOwnedBy(B)=${ownedBefore} seq=${env.probe("busRouteSeq")}`);

  env.deck.deliverBody(callA, { detail: BUS_ROUTE_DETAIL });
  await settle();
  const pendingAfter = env.probe("pendingBusId");
  const ownedAfter = env.probe(`busRouteOwnedBy(${JSON.stringify(busB.id)})`);
  console.log(`      after  A's stale body: pendingBusId=${JSON.stringify(pendingAfter)} busRouteOwnedBy(B)=${ownedAfter} seq=${env.probe("busRouteSeq")}`);
  console.log("      busRouteOwnedBy is what buses.js line 242 asks before clearing a line whose bus was");
  console.log("      reassigned mid-flight; a stale error answers it 'no' for a request still in flight.");

  // let B finish so the arm leaves no half-open request
  env.deck.deliverHeaders(callB, { ok: true, status: 200 });
  env.deck.deliverBody(callB, { directions: [[[40.7, -73.9], [40.71, -73.91]]] });
  await settle();

  /* 4b: the stale error lands after the newer route already drew. */
  console.log("");
  console.log("  4b  bus A's 503 body lands AFTER bus B's route drew (both on the same route)");
  const env2 = bootFrontend({ withBuses: true });
  seedBus(env2, busA);
  const markerB2 = seedBus(env2, busB);
  markerB2.popupOpen = true;

  env2.exec(`showBusRoute(${JSON.stringify(busA)});`);
  await settle();
  const a2 = env2.deck.calls[0];
  env2.deck.deliverHeaders(a2, { ok: false, status: 503 });
  await settle();

  env2.exec(`showBusRoute(${JSON.stringify(busB)});`);
  await settle();
  const b2 = env2.deck.calls[1];
  env2.deck.deliverHeaders(b2, { ok: true, status: 200 });
  env2.deck.deliverBody(b2, { directions: [[[40.7, -73.9], [40.71, -73.91]]] });
  await settle();

  const drawnBefore = env2.L.calls.filter((c) => c.method === "polyline").length;
  const bannerBefore = env2.document.getElementById("route-banner").hidden;
  const shownBefore = env2.probe("shownBusRoute && shownBusRoute.routeId");
  const noteBefore = env2.probe(`busRouteNotes.has(${JSON.stringify(ROUTE)})`);
  const popupBefore = env2.probe("busPopup")(env2.sandbox.__seedRecord);
  console.log(`      before A's stale body: polylines=${drawnBefore} bannerHidden=${bannerBefore} shownBusRoute.routeId=${shownBefore} note=${noteBefore}`);
  console.log(`          bus B popup carries the failure note: ${popupBefore.includes(BUS_ROUTE_DETAIL)}`);

  env2.deck.deliverBody(a2, { detail: BUS_ROUTE_DETAIL });
  await settle();
  const drawnAfter = env2.L.calls.filter((c) => c.method === "polyline").length;
  const bannerAfter = env2.document.getElementById("route-banner").hidden;
  const shownAfter = env2.probe("shownBusRoute && shownBusRoute.routeId");
  const noteAfter = env2.probe(`busRouteNotes.has(${JSON.stringify(ROUTE)})`);
  const popupAfter = env2.probe("busPopup")(env2.sandbox.__seedRecord);
  const noteText = env2.probe(`(busRouteNotes.get(${JSON.stringify(ROUTE)}) || {}).message || null`);
  console.log(`      after  A's stale body: polylines=${drawnAfter} bannerHidden=${bannerAfter} shownBusRoute.routeId=${shownAfter} note=${noteAfter}`);
  console.log(`          note text: ${JSON.stringify(noteText)}`);
  console.log(`          bus B popup carries the failure note: ${popupAfter.includes(BUS_ROUTE_DETAIL)}`);
  console.log("      reading: the route line stays drawn and the banner still names it, while the popup");
  console.log("      of a bus on that very route now says the route shapes are still indexing.");

  return {
    pendingBefore, pendingAfter, ownedBefore, ownedAfter,
    drawnBefore, drawnAfter, bannerAfter, shownAfter,
    noteBefore, noteAfter,
    popupPoisonedBefore: popupBefore.includes(BUS_ROUTE_DETAIL),
    popupPoisonedAfter: popupAfter.includes(BUS_ROUTE_DETAIL),
  };
}

/* ========================================================================== */
/* the production source, printed so the reader can see what is being claimed  */
/* ========================================================================== */

function showBranch(rel, anchor, lines) {
  const src = read(rel).split("\n");
  const idx = src.findIndex((l) => l.includes(anchor));
  if (idx < 0) throw new Error(`harness: anchor not found in ${rel}: ${anchor}`);
  console.log(`  ${rel}, from line ${idx + 1}:`);
  for (let i = idx; i < Math.min(idx + lines, src.length); i += 1) {
    console.log(`    ${String(i + 1).padStart(5)} | ${src[i]}`);
  }
}

/* ========================================================================== */

function rule(title) {
  console.log("=".repeat(78));
  console.log(title);
  console.log("=".repeat(78));
}

async function main() {
  rule("F12 verification: a stale error body overwriting the current station");
  console.log("Production files driven, in the order index.html loads them:");
  for (const f of [...BASE_LOAD_ORDER, ...BUS_LOAD_ORDER, ...PANEL_LOAD_ORDER]) console.log(`  ${f}`);
  console.log(`Committed capture: ${ARRIVALS_FIXTURE} (header now=${CAPTURE_NOW})`);
  console.log(`Committed stops:   ${STOPS_FIXTURE}`);
  console.log("Clock frozen to the capture's own header; no network, no NJ Transit path.");
  console.log("");
  rule("The three error branches under discussion, as they stand in the source");
  showBranch("frontend/stations.js", "async function fetchPanelArrivals", 32);
  console.log("");
  showBranch("frontend/systems/shared.js", "async function openStationArrivals", 40);
  console.log("");
  showBranch("frontend/systems/buses.js", "async function showBusRoute", 40);

  const one = await armOne();
  const two = await armTwo();
  const three = await armThree();
  const four = await armFour();

  console.log("");
  rule("Regression checks against the recorded disposition");

  const checks = [
    [
      "arm 1: B's good data rendered before A's stale body arrived",
      one.s4.selected === `${SYSTEM}|${B_ID}` && one.s4.arrivalRows === fixtureRowCount(B_ID) && one.s4.error === null,
      `selected=${one.s4.selected} rows=${one.s4.arrivalRows} (fixture ${fixtureRowCount(B_ID)}) error=${JSON.stringify(one.s4.error)}`,
    ],
    // ---- arm 1, AFTER THE FIX. Each row names the value the finding recorded, so the
    // before and the after are readable side by side rather than only in the prose.
    [
      "arm 1 FIXED: A's delayed error body left the shared panel error alone (was: WARMING_DETAIL)",
      one.s5.error === null,
      `panelError=${JSON.stringify(one.s5.error)}`,
    ],
    [
      "arm 1: B remained the selected station throughout",
      one.s5.selected === `${SYSTEM}|${B_ID}` && one.s5.headings.some((h) => h.includes(stops[B_ID].name)),
      `selected=${one.s5.selected} heading=${JSON.stringify(one.s5.headings)}`,
    ],
    [
      `arm 1 FIXED: B's arrivals survived A's stale error body (was: ${fixtureRowCount(B_ID)} rows -> 0 behind the warming message)`,
      one.s4.arrivalRows === fixtureRowCount(B_ID) &&
        one.s5.arrivalRows === one.s4.arrivalRows &&
        !one.s5.notes.includes(WARMING_DETAIL),
      `rows ${one.s4.arrivalRows} -> ${one.s5.arrivalRows}, notes=${JSON.stringify(one.s5.notes)}`,
    ],
    [
      "arm 1: B's own payload is still in panelBody, and now nothing hides it",
      one.s5.hasBody && one.s5.bodyStop === B_ID,
      `panelBody.stop_id=${one.s5.bodyStop}`,
    ],
    [
      "arm 1 FIXED: the live region did not speak A's error under B's name",
      typeof one.s5.announced !== "string" || !one.s5.announced.includes(WARMING_DETAIL),
      `announced=${JSON.stringify(one.s5.announced)}`,
    ],
    [
      "arm 1 FIXED: and it stays fixed under the production tick, which is what used to repaint the overwrite",
      one.s6.arrivalRows === one.s4.arrivalRows && one.s6.error === null,
      `after tick: rows=${one.s6.arrivalRows} error=${JSON.stringify(one.s6.error)}`,
    ],
    [
      "arm 2 control: the SUCCESS branch does re-check the sequence, so a stale good body is discarded",
      two.after.arrivalRows === two.before.arrivalRows && two.after.bodyStop === B_ID && two.after.error === null,
      `rows ${two.before.arrivalRows} -> ${two.after.arrivalRows}, panelBody.stop_id=${two.after.bodyStop}, error=${JSON.stringify(two.after.error)}`,
    ],
    [
      "arm 3a: a stale popup error does NOT cross to a different station's marker",
      three.bUntouched === true && three.aGotStale === true,
      `marker B untouched=${three.bUntouched}, marker A took the stale error=${three.aGotStale}`,
    ],
    [
      "arm 3b: a stale popup error DOES overwrite the same marker reopened",
      three.overwritten === true && three.reopenedRows > 0,
      `reopened popup rows=${three.reopenedRows}, then overwritten by the warming text=${three.overwritten}`,
    ],
    [
      "arm 4a: a stale bus-route error clears pendingBusId for a request still in flight",
      four.pendingBefore !== null && four.pendingAfter === null && four.ownedBefore === true && four.ownedAfter === false,
      `pendingBusId ${JSON.stringify(four.pendingBefore)} -> ${JSON.stringify(four.pendingAfter)}, busRouteOwnedBy(B) ${four.ownedBefore} -> ${four.ownedAfter}`,
    ],
    [
      "arm 4b: a stale bus-route error re-adds a failure note to a route that is drawn",
      four.noteBefore === false && four.noteAfter === true && four.drawnAfter === four.drawnBefore &&
        four.drawnAfter > 0 && four.bannerAfter === false &&
        four.popupPoisonedBefore === false && four.popupPoisonedAfter === true,
      `note ${four.noteBefore} -> ${four.noteAfter}, polylines=${four.drawnAfter}, bannerHidden=${four.bannerAfter}, popup note ${four.popupPoisonedBefore} -> ${four.popupPoisonedAfter}`,
    ],
  ];

  const failures = [];
  for (const [label, ok, detail] of checks) {
    console.log(`  [${ok ? "PASS" : "FAIL"}] ${label}`);
    console.log(`         ${detail}`);
    if (!ok) failures.push(label);
  }

  console.log("");
  if (failures.length) {
    console.log("The audit record no longer matches reality. Failing checks:");
    for (const label of failures) console.log(`  - ${label}`);
    console.log("DISPOSITION: CHANGED. F12 no longer behaves as recorded; see the failing checks above.");
    return 1;
  }

  console.log(
    `DISPOSITION: FIXED (claude/release1-small-fixes)  Station A's 503 headers, then a switch to B, then B's ` +
      `${fixtureRowCount(B_ID)} good arrival rows, then A's delayed error body. BEFORE: B's rows fell to 0 ` +
      `behind A's warming message, panelError took A's detail, the live region spoke it under B's name, and a ` +
      `production tick repainted all of it. AFTER: all ${fixtureRowCount(B_ID)} rows survive, panelError stays ` +
      `null, the live region is untouched and the tick repaints B, because fetchPanelArrivals now checks its ` +
      `sequence once, after every await and before any write. The SUCCESS branch always re-checked, which is ` +
      `why arm 2 is unchanged. STILL AS FOUND: the popup branch overwrites only the same marker reopened, and ` +
      `the bus-route branch (N3) clears an in-flight request's pendingBusId and re-notes a route that is drawn.`,
  );
  return 0;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error("HARNESS ERROR:", err && err.stack ? err.stack : err);
    process.exit(2);
  },
);
