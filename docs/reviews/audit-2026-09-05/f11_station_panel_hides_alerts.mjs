#!/usr/bin/env node
/*
 * F11 (P2): "The station panel hides relevant service alerts."
 * Verification of the 2026-09-05 audit finding recorded in docs/reviews/audit-2026-09-05.md.
 *
 * RUN FROM THE REPO ROOT:
 *   node docs/reviews/audit-2026-09-05/f11_station_panel_hides_alerts.mjs
 *
 * WHAT THE AUDIT CLAIMS (quoted from section 1 of the audit record):
 *   "The panel renders arrivals without consulting the alert store. Popups use
 *    station/route matching, but station-specific and route-specific alerts are excluded
 *    from the agency-wide banner. Executing the production renderer and helpers with a
 *    station-specific service suspension produced an alert in the popup helper, no banner
 *    entry, and ordinary arrival text in the panel."
 *   "On a phone the open panel makes the map inert, so the hidden popup is not an
 *    equivalent active source of information."
 *   Cited evidence: frontend/stations.js#L606 (station-detail renderer),
 *   frontend/systems/shared.js#L1319 (map popup alert matching),
 *   frontend/stations.js#L131 (mobile inert behavior).
 *
 * WHAT THIS SCRIPT MEASURES, AND HOW.
 *   It loads FOUR PRODUCTION SOURCE FILES, unmodified, into one node:vm context in the
 *   same order index.html loads them (frontend/helpers.js, frontend/systems/shared.js,
 *   frontend/systems/subway.js, frontend/stations.js), then drives the real entry points:
 *
 *     loadAlerts()          production, systems/shared.js: fetches /api/alerts, builds the
 *                           alert index (indexAlerts), computes the banner set
 *                           (bannerAlerts) and renders it (renderAlertBanner).
 *     loadStations()        production, systems/subway.js: builds the station marker, binds
 *                           the popup (bindStationPopup) whose render function is
 *                           stationAlertsBlock("subway", s, b) + subwayArrivalsHtml(s, b),
 *                           and registers the station for the panel (registerStation).
 *     openStationsPanel()   production, stations.js: opens the panel and applies the mobile
 *                           inert sweep (applyOverlayInertness -> setBackgroundInert).
 *     selectStation(key)    production, stations.js: renders the panel detail
 *                           (renderStationDetail) AND syncs the map, which opens the
 *                           station popup (syncMapToStation -> marker.openPopup() ->
 *                           bindStationPopup's popupopen -> openStationArrivals ->
 *                           renderStation -> the render function above).
 *
 *   One rider action therefore produces all three surfaces from production code: the popup
 *   HTML, the banner DOM, and the panel DOM. Nothing here re-implements a matcher, a
 *   renderer or a banner rule.
 *
 * WHAT IS INJECTED (everything not production code is listed here):
 *   1. Host globals the browser would supply: a small DOM built by PARSING the production
 *      frontend/index.html (so the element tree the inert sweep walks is the real one), a
 *      Leaflet stub (map, layer groups, circleMarker, popup lifecycle), matchMedia driven
 *      by an explicit viewport size, a routing fetch stub, and non-firing timer stubs
 *      (setInterval/setTimeout record and never run, so the countdown tick cannot repaint
 *      during measurement).
 *   2. A frozen clock. Date.now() is pinned to the committed fixture's own FROZEN_MS
 *      (2026-07-02T12:00:00Z), so ages are measured against the fixture's own timestamps
 *      and never against today's wall clock.
 *   3. API responses from the COMMITTED fixture module tests/e2e/fixtures/api.js:
 *      subwayStops(), subwayArrivals(), alertsWithSystems({...}).
 *   4. THE FAULT: two subway service alerts injected into the /api/alerts response, because
 *      no committed fixture carries a subway station suspension. One is STOP-scoped to
 *      station 127 (Times Sq-42 St), one is ROUTE-scoped to route 3 (which serves 127 in the
 *      static routes list and has no imminent train in the arrivals fixture). Both are
 *      stated verbatim in the output below.
 *   No network is contacted, and no NJ Transit code path or host is touched.
 *
 * KNOWN STUB LIMITATION, stated rather than papered over: the DOM stub does not parse
 * assigned innerHTML into elements, so renderAlertBanner's rebuild branch is read back as
 * the exact HTML string production assigned. The empty-banner branch used by the finding
 * calls replaceChildren() instead, which the stub models exactly (0 child elements).
 *
 * EXIT: 0 when the finding still behaves as recorded, non-zero with the failing checks
 * named. The last line is the disposition.
 */

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, "..", "..", "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

/* ==================================================================
   Injected host: a minimal DOM
   ================================================================== */

const VOID_TAGS = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "param", "source", "track", "wbr",
]);
const BOOL_ATTRS = new Set(["hidden", "checked", "disabled", "selected", "inert"]);

class ClassList {
  constructor(el) {
    this.el = el;
    this.set = new Set();
  }
  add(name) {
    this.set.add(name);
  }
  remove(name) {
    this.set.delete(name);
  }
  contains(name) {
    return this.set.has(name);
  }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : !!force;
    if (on) this.set.add(name);
    else this.set.delete(name);
    return on;
  }
  toString() {
    return [...this.set].join(" ");
  }
}

class StubStyle {
  setProperty(name, value) {
    this[name] = value;
  }
  getPropertyValue(name) {
    return this[name] ?? "";
  }
}

let DOC = null; // set once the document exists (focus needs it)

class El {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.childNodes = []; // El instances and {text} nodes
    this.parentElement = null;
    this.attrs = new Map();
    this.style = new StubStyle();
    this.dataset = {};
    this.classList = new ClassList(this);
    this.listeners = new Map();
    this.hidden = false;
    this.inert = false;
    this.id = "";
    this.value = "";
    this.checked = false;
    this.type = "";
    this._innerHTML = null;
  }
  get children() {
    return this.childNodes.filter((n) => n instanceof El);
  }
  get childElementCount() {
    return this.children.length;
  }
  get className() {
    return this.classList.toString();
  }
  set className(value) {
    this.classList.set = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get textContent() {
    let out = "";
    for (const node of this.childNodes) out += node instanceof El ? node.textContent : node.text;
    return out;
  }
  set textContent(value) {
    this.childNodes = [{ text: String(value) }];
    this._innerHTML = null;
  }
  get innerHTML() {
    return this._innerHTML ?? "";
  }
  set innerHTML(html) {
    // Stub: the assigned markup is stored verbatim, not parsed (see the header note).
    this.childNodes = [];
    this._innerHTML = String(html);
  }
  appendChild(node) {
    if (node instanceof El) node.parentElement = this;
    this.childNodes.push(node);
    this._innerHTML = null;
    return node;
  }
  append(...nodes) {
    for (const node of nodes) this.appendChild(node);
  }
  replaceChildren(...nodes) {
    for (const node of this.children) node.parentElement = null;
    this.childNodes = [];
    this._innerHTML = null;
    for (const node of nodes) this.appendChild(node);
  }
  removeChild(node) {
    this.childNodes = this.childNodes.filter((n) => n !== node);
    if (node instanceof El) node.parentElement = null;
    return node;
  }
  setAttribute(name, value) {
    this.attrs.set(name, String(value));
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
    if (BOOL_ATTRS.has(name)) this[name] = true;
  }
  getAttribute(name) {
    return this.attrs.has(name) ? this.attrs.get(name) : null;
  }
  removeAttribute(name) {
    this.attrs.delete(name);
    if (BOOL_ATTRS.has(name)) this[name] = false;
  }
  hasAttribute(name) {
    return this.attrs.has(name);
  }
  addEventListener(type, fn) {
    const list = this.listeners.get(type) ?? [];
    list.push(fn);
    this.listeners.set(type, list);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter((f) => f !== fn));
  }
  dispatch(type, event = {}) {
    for (const fn of this.listeners.get(type) ?? []) fn.call(this, { target: this, ...event });
  }
  click() {
    this.dispatch("click", { preventDefault() {}, stopImmediatePropagation() {} });
  }
  contains(node) {
    for (let cur = node; cur; cur = cur.parentElement) if (cur === this) return true;
    return false;
  }
  closest() {
    return null;
  }
  querySelector() {
    return null; // see the stub-limitation note in the header
  }
  getBoundingClientRect() {
    return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 };
  }
  focus() {
    // The platform makes .focus() a no-op inside an inert subtree; the stub models that,
    // which is what lets the mobile-inert claim be measured rather than asserted.
    for (let cur = this; cur; cur = cur.parentElement) if (cur.inert) return;
    if (DOC) DOC.activeElement = this;
  }
  blur() {
    if (DOC && DOC.activeElement === this) DOC.activeElement = DOC.body;
  }
  describe() {
    const id = this.id ? `#${this.id}` : "";
    const cls = this.className ? `.${this.className.split(" ").join(".")}` : "";
    return `${this.tagName.toLowerCase()}${id}${cls}`;
  }
}

// A deliberately small HTML parser: enough for frontend/index.html, which has no inline
// script or style content and no ">" inside an attribute value (both checked).
function parseHtml(html) {
  const src = html.replace(/<!--[\s\S]*?-->/g, "").replace(/<!doctype[^>]*>/gi, "");
  const rootHolder = new El("#root");
  const stack = [rootHolder];
  const tagRe = /<(\/?)([a-zA-Z][\w:-]*)([^>]*?)(\/?)>/g;
  let last = 0;
  let m;
  const pushText = (text) => {
    if (text.trim()) stack[stack.length - 1].appendChild({ text: text.trim() });
  };
  while ((m = tagRe.exec(src)) !== null) {
    pushText(src.slice(last, m.index));
    last = tagRe.lastIndex;
    const [, closing, name, rawAttrs, selfClose] = m;
    const tag = name.toLowerCase();
    if (closing) {
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tagName === tag.toUpperCase()) {
          stack.length = i;
          break;
        }
      }
      continue;
    }
    const el = new El(tag);
    const attrRe = /([\w:.-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
    let a;
    while ((a = attrRe.exec(rawAttrs)) !== null) {
      const key = a[1];
      const value = a[2] ?? a[3] ?? a[4] ?? "";
      el.setAttribute(key, value);
      if (key === "type") el.type = value;
      if (key === "value") el.value = value;
    }
    stack[stack.length - 1].appendChild(el);
    if (!VOID_TAGS.has(tag) && !selfClose) stack.push(el);
  }
  pushText(src.slice(last));
  return rootHolder;
}

function makeDocument(html) {
  const rootHolder = parseHtml(html);
  const htmlEl = rootHolder.children.find((el) => el.tagName === "HTML") ?? rootHolder;
  const bodyEl = htmlEl.children.find((el) => el.tagName === "BODY") ?? htmlEl;
  const walk = (el, out = []) => {
    out.push(el);
    for (const child of el.children) walk(child, out);
    return out;
  };
  const all = walk(htmlEl);
  const byId = new Map();
  for (const el of all) if (el.id && !byId.has(el.id)) byId.set(el.id, el);
  const doc = {
    documentElement: htmlEl,
    body: bodyEl,
    activeElement: bodyEl,
    elementCount: all.length,
    getElementById: (id) => byId.get(id) ?? null,
    createElement: (tag) => new El(tag),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  DOC = doc;
  return doc;
}

/* ==================================================================
   Injected host: matchMedia, Leaflet, timers, fetch
   ================================================================== */

const viewport = { width: 1280, height: 800 };

function makeMatchMedia() {
  return (query) => {
    const max = /\(max-width:\s*(\d+)px\)/.exec(query);
    const min = /\(min-width:\s*(\d+)px\)/.exec(query);
    let matches = false;
    if (max) matches = viewport.width <= Number(max[1]);
    else if (min) matches = viewport.width >= Number(min[1]);
    else if (/prefers-reduced-motion/.test(query)) matches = false;
    return {
      media: query,
      get matches() {
        const nmax = /\(max-width:\s*(\d+)px\)/.exec(query);
        const nmin = /\(min-width:\s*(\d+)px\)/.exec(query);
        if (nmax) return viewport.width <= Number(nmax[1]);
        if (nmin) return viewport.width >= Number(nmin[1]);
        if (/prefers-reduced-motion/.test(query)) return false;
        return matches;
      },
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
    };
  };
}

// Leaflet stub. Only what the loaded production files call.
function makeLeaflet(mapLayers, firedMapEvents) {
  const handlers = new Map();
  const on = (target, event, fn) => {
    const list = handlers.get(event) ?? [];
    list.push({ target, fn });
    handlers.set(event, list);
    return target;
  };
  const fire = (event, payload) => {
    for (const entry of handlers.get(event) ?? []) entry.fn.call(entry.target, payload);
    firedMapEvents.push(event);
  };
  const paneStyles = new Map();
  const mapStub = {
    __isMap: true,
    on: (event, fn) => on(mapStub, event, fn),
    off: () => mapStub,
    setView: () => mapStub,
    createPane: (name) => paneStyles.set(name, { style: {} }),
    getPane: (name) => paneStyles.get(name) ?? { style: {} },
    panBy: () => mapStub,
    panTo: () => mapStub,
    invalidateSize: () => mapStub,
    addLayer: (layer) => {
      mapLayers.add(layer);
      return mapStub;
    },
    removeLayer: (layer) => {
      mapLayers.delete(layer);
      return mapStub;
    },
    hasLayer: (layer) => mapLayers.has(layer),
    closePopup: () => mapStub,
    getContainer: () => DOC.getElementById("map"),
    fire,
  };
  const layerGroup = () => {
    const group = {
      __isLayerGroup: true,
      addTo: (m) => {
        m.addLayer(group);
        return group;
      },
      addLayer: () => group,
      removeLayer: () => group,
      clearLayers: () => group,
    };
    return group;
  };
  const circleMarker = (latlng, options) => {
    const marker = {
      __isMarker: true,
      latlng,
      options,
      popupContent: "",
      popupOpen: false,
      handlers: new Map(),
      popup: { getElement: () => null },
      bindPopup(content) {
        marker.popupContent = content;
        return marker;
      },
      on(event, fn) {
        const list = marker.handlers.get(event) ?? [];
        list.push(fn);
        marker.handlers.set(event, list);
        return marker;
      },
      addTo: () => marker,
      remove: () => marker,
      isPopupOpen: () => marker.popupOpen,
      setPopupContent(html) {
        marker.popupContent = html;
        return marker;
      },
      getLatLng: () => latlng,
      setLatLng: () => marker,
      openPopup() {
        marker.popupOpen = true;
        for (const fn of marker.handlers.get("popupopen") ?? []) fn.call(marker);
        mapStub.fire("popupopen", { popup: marker.popup });
        return marker;
      },
      closePopup() {
        marker.popupOpen = false;
        for (const fn of marker.handlers.get("popupclose") ?? []) fn.call(marker);
        mapStub.fire("popupclose", { popup: marker.popup });
        return marker;
      },
      setStyle: () => marker,
    };
    return marker;
  };
  return {
    map: () => mapStub,
    tileLayer: () => ({ addTo: () => ({}) }),
    layerGroup,
    canvas: () => ({}),
    circleMarker,
    marker: (latlng, options) => circleMarker(latlng, options),
    polyline: () => ({ addTo: () => ({}), setStyle: () => {} }),
    divIcon: () => ({}),
    icon: () => ({}),
    latLng: (a, b) => ({ lat: a, lng: b }),
  };
}

/* ==================================================================
   Fixtures (committed) and the injected fault
   ================================================================== */

const fixtures = require(join(ROOT, "tests/e2e/fixtures/api.js"));
const FROZEN_MS = fixtures.FROZEN_MS;
const FROZEN_S = fixtures.FROZEN_S;

const STATION_ID = "127"; // Times Sq-42 St, from the committed subwayStops() fixture
const STATION_KEY = `subway|${STATION_ID}`;

// THE INJECTED FAULT. No committed fixture carries a subway station suspension, so the
// audit's scenario is injected explicitly, in the shape backend/models.py Alert serves.
const STOP_ALERT = {
  id: "f11-stop",
  system: "subway",
  header: "Times Sq-42 St: no subway service at this station. Trains are bypassing the station.",
  description: null,
  effect: "NO_SERVICE",
  cause: "MAINTENANCE",
  routes: [],
  stops: [STATION_ID],
  starts_at: FROZEN_S - 600,
  ends_at: null,
};
const ROUTE_ALERT = {
  id: "f11-route",
  system: "subway",
  header: "3 trains are suspended in both directions.",
  description: null,
  effect: "NO_SERVICE",
  cause: "MAINTENANCE",
  routes: ["3"],
  stops: [],
  starts_at: FROZEN_S - 600,
  ends_at: null,
};
// The control: a selector-less alert, which IS the banner's population.
const AGENCY_ALERT = {
  id: "f11-agency",
  system: "subway",
  header: "Reduced subway service systemwide this weekend.",
  description: null,
  effect: "REDUCED_SERVICE",
  cause: "MAINTENANCE",
  routes: [],
  stops: [],
  starts_at: FROZEN_S - 600,
  ends_at: null,
};

let alertsPayload = fixtures.alertsWithSystems({ alerts: [STOP_ALERT, ROUTE_ALERT] });

const fetchLog = [];
function makeFetch() {
  const routes = new Map([
    ["/api/alerts", () => alertsPayload],
    ["/api/subway-stops", () => fixtures.subwayStops()],
    [`/api/subway-arrivals/${STATION_ID}`, () => fixtures.subwayArrivals()],
  ]);
  return async (url) => {
    const path = String(url);
    fetchLog.push(path);
    const handler = routes.get(path);
    if (!handler) throw new Error(`hermetic harness: unrouted fetch ${path}`);
    const body = JSON.parse(JSON.stringify(handler()));
    return { ok: true, status: 200, json: async () => body };
  };
}

/* ==================================================================
   The vm context, loaded with the production sources
   ================================================================== */

class FrozenDate extends Date {
  constructor(...args) {
    if (args.length === 0) super(FROZEN_MS);
    else super(...args);
  }
  static now() {
    return FROZEN_MS;
  }
}

const timerLog = { intervals: 0, timeouts: 0 };
const mapLayers = new Set();
const firedMapEvents = [];
const document = makeDocument(read("frontend/index.html"));
const windowStub = {
  addEventListener: () => {},
  removeEventListener: () => {},
  location: { search: "" },
};

const sandbox = {
  console,
  Date: FrozenDate,
  document,
  window: windowStub,
  matchMedia: makeMatchMedia(),
  L: makeLeaflet(mapLayers, firedMapEvents),
  fetch: makeFetch(),
  AbortSignal: { timeout: (ms) => ({ stubDeadlineMs: ms }) },
  // helpers.js reads the query string through URLSearchParams; `location` is left
  // undefined so it takes its own guarded branch and the PRODUCTION thresholds apply.
  URLSearchParams,
  setInterval: () => {
    timerLog.intervals += 1;
    return timerLog.intervals;
  },
  clearInterval: () => {},
  setTimeout: () => {
    timerLog.timeouts += 1;
    return timerLog.timeouts;
  },
  clearTimeout: () => {},
  requestAnimationFrame: () => 0,
  cancelAnimationFrame: () => {},
};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
const ctx = vm.createContext(sandbox);

const SOURCES = [
  "frontend/helpers.js",
  "frontend/systems/shared.js",
  "frontend/systems/subway.js",
  "frontend/stations.js",
];
for (const rel of SOURCES) vm.runInContext(read(rel), ctx, { filename: rel });

// Top-level `const`/`let` live in the context's global lexical scope, not on the global
// object, so probes are evaluated inside the context rather than read off `sandbox`.
const runIn = (expr) => vm.runInContext(expr, ctx, { filename: "<probe>" });
const flush = async (turns = 8) => {
  for (let i = 0; i < turns; i++) await new Promise((resolve) => globalThis.setTimeout(resolve, 0));
};
const escIn = runIn("esc");

/* ==================================================================
   Drive the production entry points
   ================================================================== */

viewport.width = 375; // a phone, which is the case claim (e) is about
viewport.height = 667;

runIn("loadAlerts()");
runIn("loadStations()");
await flush();

const bannerEl = document.getElementById("alert-banner");
const bannerAfterStationAlerts = { html: bannerEl.innerHTML, text: bannerEl.textContent, kids: bannerEl.childElementCount };
const bannerSetSize = runIn("lastBannerAlerts.length");
const bannerAlertsDirect = runIn("bannerAlerts")([STOP_ALERT, ROUTE_ALERT]).length;

runIn("openStationsPanel()");
const inertPhone = {
  narrow: runIn("narrowViewport()"),
  map: document.getElementById("map").inert,
  legend: document.getElementById("panel").inert,
  banner: document.getElementById("alert-banner").inert,
  panel: document.getElementById("stations-panel").inert,
  announce: document.getElementById("page-announce").inert,
  title: document.getElementById("app-title").inert,
  toggle: document.getElementById("stations-toggle").inert,
};

runIn(`selectStation(${JSON.stringify(STATION_KEY)})`);
await flush();

const entry = runIn(`stationRegistry.find((row) => row.key === ${JSON.stringify(STATION_KEY)})`);
const popupHtml = entry.marker.popupContent;
const detailEl = document.getElementById("stations-detail");
const announceEl = document.getElementById("stations-announce");
const panelText = detailEl.textContent;
const panelRows = detailEl.children.map((el) => `${el.describe()}  ${el.textContent}`);
const panelListRows = [];
for (const child of detailEl.children) {
  for (const li of child.children) panelListRows.push(`${li.describe()}  ${li.textContent}`);
}

// A focus probe for claim (e): can a rider reach the map container, or the toggle behind
// the overlay, while the panel is open? `inert` inherits, so the probe walks ancestors.
document.getElementById("map").focus();
const focusReachedMapWhilePanelOpen = document.activeElement === document.getElementById("map");
document.getElementById("stations-toggle").focus();
const focusReachedToggleWhilePanelOpen =
  document.activeElement === document.getElementById("stations-toggle");

// Re-derived from the committed arrivals fixture rather than asserted: route 3 serves this
// station in the static routes list and has NO imminent train, which is the audit's
// acceptance case ("including when no trains of the affected route are currently predicted").
const arrivalsFixture = fixtures.subwayArrivals();
const predictedRoutes = [
  ...new Set(Object.values(arrivalsFixture.directions).flat().map((row) => row.route_id)),
].sort();
const routeAlertHasNoTrain = !predictedRoutes.includes(ROUTE_ALERT.routes[0]);

// The control: an agency-wide alert, through the same production banner path.
alertsPayload = fixtures.alertsWithSystems({ alerts: [AGENCY_ALERT] });
runIn("loadAlerts()");
await flush();
const bannerAfterAgencyAlert = { html: bannerEl.innerHTML, text: bannerEl.textContent, kids: bannerEl.childElementCount };

// And the desktop comparison for the inert sweep.
viewport.width = 1280;
viewport.height = 800;
runIn("applyOverlayInertness()");
const inertDesktop = { map: document.getElementById("map").inert };

// Re-derived rather than asserted: the container id production hands to Leaflet, which is
// the element every popup is rendered inside.
const sharedSrcLines = read("frontend/systems/shared.js").split("\n");
const mapCtorIndex = sharedSrcLines.findIndex((text) => /=\s*L\.map\(/.test(text));
const mapCtorLine = { number: mapCtorIndex + 1, text: sharedSrcLines[mapCtorIndex].trim() };

/* ==================================================================
   Static corroboration of claim (a): the panel never names the alert store
   ================================================================== */

const stationsSrc = read("frontend/stations.js");
const stripComments = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").split("\n").map((line) => line.replace(/\/\/.*$/, "")).join("\n");
const ALERT_SYMBOLS = [
  "alertsIndex",
  "matchStationAlerts",
  "matchRouteAlerts",
  "stationAlertsBlock",
  "alertsBlockHtml",
  "bannerAlerts",
  "staleAlertsMarker",
];
const stationsCode = stripComments(stationsSrc);
const alertSymbolHits = ALERT_SYMBOLS.filter((name) => stationsCode.includes(name));

/* ==================================================================
   Report
   ================================================================== */

const line = (n = 78) => "-".repeat(n);
const escaped = (alert) => escIn(alert.header);
const say = (...args) => console.log(...args);

say("=".repeat(78));
say("F11 (P2): the station panel hides relevant service alerts");
say("=".repeat(78));
say("");
say("Production sources executed in one node:vm context, in index.html order:");
for (const rel of SOURCES) say(`  ${rel}`);
say(`DOM parsed from frontend/index.html: ${document.elementCount} elements.`);
say(`Frozen clock: Date.now() = ${FROZEN_MS} (${new Date(FROZEN_MS).toISOString()}), from the committed fixture.`);
say(`Fetches served by the hermetic router: ${fetchLog.join(", ")}`);
say(`Timer stubs armed but never fired: ${timerLog.intervals} intervals, ${timerLog.timeouts} timeouts.`);
say("");
say("INJECTED ALERT STORE (/api/alerts body, built with the committed alertsWithSystems fixture):");
for (const alert of [STOP_ALERT, ROUTE_ALERT]) {
  say(`  id=${alert.id}  system=${alert.system}  stops=${JSON.stringify(alert.stops)}  routes=${JSON.stringify(alert.routes)}`);
  say(`     header: ${alert.header}`);
}
say(`  agency-wide (selector-less) alerts in this store: 0`);
say("");
say("STATION UNDER TEST (committed subwayStops fixture):");
say(`  id=${STATION_ID}  name=${entry.name}  static routes=${JSON.stringify(entry.routes)}  kind=${entry.kind}`);
say(`  routes with an imminent train in the committed arrivals fixture: ${JSON.stringify(predictedRoutes)}`);
say(`  so the route-scoped alert (route ${ROUTE_ALERT.routes[0]}) has no predicted train here: ${routeAlertHasNoTrain}`);
say("");

say(line());
say("[1] POPUP  production path: selectStation -> syncMapToStation -> marker.openPopup()");
say("           -> bindStationPopup popupopen -> openStationArrivals -> renderStation");
say("           -> subway.js render = stationAlertsBlock(\"subway\", s, b) + subwayArrivalsHtml(s, b)");
say(line());
say(popupHtml);
say(line());
const popupHasStop = popupHtml.includes(escaped(STOP_ALERT));
const popupHasRoute = popupHtml.includes(escaped(ROUTE_ALERT));
const popupAlertRows = (popupHtml.match(/class="alert-row"/g) || []).length;
say(`alert rows rendered in popup: ${popupAlertRows}`);
say(`  stop-scoped alert f11-stop present:   ${popupHasStop}`);
say(`  route-scoped alert f11-route present: ${popupHasRoute}`);
say("");

say(line());
say("[2] BANNER  production path: loadAlerts -> bannerAlerts -> renderAlertBanner (#alert-banner)");
say(line());
say(`#alert-banner innerHTML: ${JSON.stringify(bannerAfterStationAlerts.html)}`);
say(`#alert-banner textContent: ${JSON.stringify(bannerAfterStationAlerts.text)}`);
say(`#alert-banner child elements: ${bannerAfterStationAlerts.kids}`);
say(line());
say(`bannerAlerts([stop-scoped, route-scoped]) returned ${bannerAlertsDirect} of 2 alerts`);
say(`lastBannerAlerts after loadAlerts(): ${bannerSetSize}`);
say("");

say(line());
say("[3] PANEL  production path: openStationsPanel -> selectStation -> fetchPanelArrivals");
say("           -> renderStationDetail (stations.js L567, arrivals block at L606)");
say(line());
for (const row of panelRows) say(`  ${row}`);
for (const row of panelListRows) say(`    ${row}`);
say(line());
say(`#stations-detail textContent: ${JSON.stringify(panelText)}`);
say(`#stations-announce textContent: ${JSON.stringify(announceEl.textContent)}`);
const panelHasStop = panelText.includes(STOP_ALERT.header) || announceEl.textContent.includes(STOP_ALERT.header);
const panelHasRoute = panelText.includes(ROUTE_ALERT.header) || announceEl.textContent.includes(ROUTE_ALERT.header);
say(`  stop-scoped alert text anywhere in the panel:  ${panelHasStop}`);
say(`  route-scoped alert text anywhere in the panel: ${panelHasRoute}`);
say(`  alert-store symbols named anywhere in stations.js code (comments stripped): ${alertSymbolHits.length ? alertSymbolHits.join(", ") : "none"}`);
say("");

say(line());
say("[4] CONTROL  the same banner path with an agency-wide (selector-less) alert");
say(line());
say(`#alert-banner innerHTML: ${JSON.stringify(bannerAfterAgencyAlert.html)}`);
say(`#alert-banner child elements: ${bannerAfterAgencyAlert.kids} (the stub stores assigned innerHTML without parsing it, so the markup above is this branch's measurement)`);
const bannerShowsAgency = bannerAfterAgencyAlert.html.includes(escaped(AGENCY_ALERT));
say(`  agency-wide alert reaches the banner: ${bannerShowsAgency}`);
say("");

say(line());
say("[5] MOBILE INERT  production path: openStationsPanel -> applyOverlayInertness (stations.js L131)");
say("                  -> setBackgroundInert (L113), walking the parsed index.html tree");
say(line());
say(`viewport 375x667, narrowViewport() = ${inertPhone.narrow}, panel open`);
say(`  #map            inert=${inertPhone.map}`);
say(`  #panel (legend) inert=${inertPhone.legend}`);
say(`  #alert-banner   inert=${inertPhone.banner}`);
say(`  #stations-toggle inert=${inertPhone.toggle} (its own attribute; it sits inside the inert #panel subtree, and inert inherits)`);
say(`  exempt: #stations-panel inert=${inertPhone.panel}, #page-announce inert=${inertPhone.announce}, #app-title inert=${inertPhone.title}`);
say(`  focus() on #map while the panel is open moved focus there: ${focusReachedMapWhilePanelOpen}`);
say(`  focus() on #stations-toggle while the panel is open moved focus there: ${focusReachedToggleWhilePanelOpen}`);
say(`viewport 1280x800: #map inert=${inertDesktop.map}`);
say("");
say("Why that reaches the popup: Leaflet renders every popup inside the container element it");
say("was constructed with, and production constructs the map on #map itself:");
say(`  frontend/systems/shared.js:${mapCtorLine.number}: ${mapCtorLine.text}`);
say("So the inert attribute measured on #map covers the popup subtree. That nesting is a");
say("Leaflet structural fact, not something this harness renders: the stub popup has no");
say("element of its own.");
say("");

/* ==================================================================
   Assertions against the recorded disposition
   ================================================================== */

const checks = [
  ["(b,d) popup renders the stop-scoped alert", popupHasStop],
  ["(b,d) popup renders the route-scoped alert", popupHasRoute],
  ["(b,d) popup alert rows == 2", popupAlertRows === 2],
  ["(c,d) banner renders no entry for the station/route alerts", bannerAfterStationAlerts.kids === 0 && bannerAfterStationAlerts.html === "" && bannerAfterStationAlerts.text === ""],
  ["(c) bannerAlerts() excludes both selector-scoped alerts", bannerAlertsDirect === 0 && bannerSetSize === 0],
  ["(c) control: an agency-wide alert DOES reach the banner", bannerShowsAgency && bannerAfterAgencyAlert.html !== ""],
  ["(a,d) panel detail carries no alert text", !panelHasStop && !panelHasRoute],
  ["(a,d) panel renders ordinary arrival text instead", /train in \d+ minutes?, \d+:\d\d [AP]M arrival/.test(panelText)],
  ["(a) stations.js code never names the alert store", alertSymbolHits.length === 0],
  ["(e) an open panel on a phone makes #map inert", inertPhone.narrow === true && inertPhone.map === true],
  ["(e) the inert sweep spares the panel and the live region", inertPhone.panel === false && inertPhone.announce === false],
  ["(e) focus cannot land on the inert map while the panel is open", focusReachedMapWhilePanelOpen === false],
  ["(e) nor on the toggle inside the inert legend subtree", focusReachedToggleWhilePanelOpen === false],
  ["(b) the route-scoped alert reaches the popup with no predicted train of that route", routeAlertHasNoTrain === true && popupHasRoute],
  ["(e) the same sweep leaves #map active at 1280", inertDesktop.map === false],
  ["(e) popups are rendered inside the very element the sweep inerts", /L\.map\(\"map\"/.test(mapCtorLine.text)],
];

let failed = 0;
say(line());
say("CHECKS");
say(line());
for (const [name, ok] of checks) {
  if (!ok) failed += 1;
  say(`  ${ok ? "PASS" : "FAIL"}  ${name}`);
}
say("");

if (failed) {
  say(`${failed} of ${checks.length} checks failed: reality no longer matches the recorded disposition.`);
  say("DISPOSITION: CHANGED. Re-verify F11 by hand; the audit record is now out of date.");
  process.exit(1);
}

say(`All ${checks.length} checks passed.`);
say(
  "DISPOSITION: VERIFIED " +
    "one station suspension plus one route suspension render 2 alert rows in the production popup, " +
    "0 rows in the banner, and 0 characters in the panel, whose map is inert at 375px.",
);
