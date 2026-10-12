#!/usr/bin/env node
/*
 * A04 (Medium): "Clock correction can leave countdowns and freshness permanently wrong"
 * Reproduction for the 2026-10-12 audit of rosenfieldben/nyc-transit-live, reviewed at main
 * 1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2.
 *
 * THE AUDIT'S CLAIM, quoted from the report (finding A04, Medium):
 *
 *   Failure. "Browser/server skew is the minimum offset observed across the entire session. A
 *   later forward change to the device clock cannot increase that stored minimum. Corrected
 *   application time then runs ahead until reload, affecting departure countdowns, freshness
 *   labels and estimated motion."
 *
 *   Evidence. "Calibrating a device five minutes slow and then supplying a fresh response after
 *   the device clock is corrected makes a zero-age source appear 300 seconds old. This was
 *   reproduced through production helpers."
 *
 *   Repair. "Anchor a recent server timestamp to monotonic elapsed time, or detect wall-clock
 *   discontinuities and reset a bounded calibration window. Test forward and backward clock
 *   changes after initial skew; assert arrival countdowns, source age and train placement
 *   together."
 *
 *   Code evidence (the report's two links, as text):
 *     frontend/helpers.js:1775
 *       https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/frontend/helpers.js#L1775
 *     frontend/systems/shared.js:2063
 *       https://github.com/rosenfieldben/nyc-transit-live/blob/1e3e73d11cedd1a12cbdb70c69b05901d66ba4b2/frontend/systems/shared.js#L2063
 *   Those are where to look (the calibration, and the corrected clock every vehicle surface
 *   reads). Nothing below asserts on a line, a symbol or a comment.
 *
 * WHAT THIS SCRIPT MEASURES
 *   One browser session, run by its own timers as a browser runs it (WHAT IS INJECTED, 2), on a
 *   device whose wall clock is injected. Four arms over the same true-time timeline:
 *
 *     control   the device clock is right throughout. The reference every surface is read
 *               against, and also what a reload gives (arm "reload" checks that separately).
 *     slow      the audit's case: the device runs SKEW_S slow for the first eight polls, the
 *               operating system then corrects it (a forward jump of SKEW_S), and a zero-age
 *               response arrives. Then LATER_POLLS more polls (two hours) to test "until
 *               reload", which is as far as "until reload" is measured here.
 *     fast      the mirror the spec asked for: SKEW_S fast, then corrected (a backward jump).
 *               PREDICTED before measuring: the stored value is a MINIMUM, so a backward jump
 *               produces a smaller sample, which the minimum accepts on the very next poll.
 *               It should therefore be wrong only in the window before that poll and then
 *               self-correct. Measured below; the asymmetry is the content of the row.
 *     reload    a fresh session started after the correction, on the corrected device.
 *
 *   At each instant every surface the audit names is read from what production RENDERED:
 *     freshness label   the status line map.js writes (#status) after its poll, and the
 *                       qualifier the board row carries beside its countdown;
 *     source age        the same age as a number: the production ingestEnvelope and
 *                       systemAges over the /api/subways body this harness served last,
 *                       evaluated in the page at that instant (the status line rounds it to
 *                       minutes);
 *     countdown         the station popup's row and the station panel's spoken sentence for a
 *                       prediction LEAD_S seconds after the served instant;
 *     estimated motion  where the page draws a subway train interpolated between two
 *                       stations, and the opacity it dims that marker to. Read at T from a
 *                       train FIRST served by the poll at T (a04-new), because the glide never
 *                       moves a marker backward within a segment, so a train already on the map
 *                       keeps whatever position the window before the poll pushed it to; the
 *                       fixture's sub-1 is read in that window to show the push itself.
 *   Plus one surface the audit did not name, measured because the same corrected clock reaches
 *   it: the "alerts may be out of date" marker every station popup carries.
 *
 * WHAT IS PRODUCTION CODE HERE (nothing under test is re-implemented)
 *   All eleven first-party frontend files, unmodified, executed as their own source text in one
 *   node:vm context in index.html's order (the composition the 09-05 F14 harness established):
 *   helpers.js, systems/shared.js, systems/buses.js, systems/subway.js, systems/railroad.js,
 *   systems/airtrain.js, systems/path.js, systems/ferry.js, systems/njt.js, stations.js, map.js.
 *   map.js's own load-time kickoff runs, and so do the intervals it and every other file set:
 *   the 15 s vehicle poll and the 60 s alerts poll fire from the page's own setInterval calls
 *   at their own instants, as do the one-second repaints and the animation frame. This script
 *   never calls a poll loop, and never calls noteClockOffset, itself. So whatever a repair
 *   adds runs here if it would run in a browser tab left open: on the poll path, in the
 *   animation frame, or on a timer of its own.
 *   Driven by name, which is how they are called and not something asserted: openStationsPanel
 *   and selectStation (a rider picking Times Sq-42 St in the panel, which also opens its map
 *   popup through syncMapToStation), and the exported ingestEnvelope and systemAges, applied to
 *   a body this harness holds (to print the source age as an exact number).
 *   NO MODULE STATE IS READ to measure anything. A train's marker is found through the
 *   Leaflet stub, which holds every marker production creates: a sub-1 marker is the one
 *   created at page load and drawn on sub-1's segment (no station dot lies on it), and the
 *   a04-new marker is the one created at T on that segment. The stored offset is printed as a
 *   diagnostic only, never asserted, so a repair that removes or renames it cannot turn this
 *   script red for that reason.
 *
 * WHAT IS INJECTED (everything that is not production code)
 *   1. THE DEVICE CLOCK. Inside the vm, Date.now() and `new Date()` read a harness wall clock
 *      equal to TRUE TIME plus the device's skew, and performance.now() reads a MONOTONIC clock
 *      (true time since the page loaded) that a wall-clock correction does not move, which is
 *      what a browser's performance.now() does. performance.timeOrigin is the DEVICE wall clock
 *      at page load, as a browser stamps it, so on the slow device it is SKEW_S behind true
 *      time. No production file reads either at 1e3e73d; they are stubbed so that the audit's
 *      first remedy (anchor to monotonic time) would run here against the same device a browser
 *      is. True time starts at the committed fixture module's FROZEN_S
 *      (tests/e2e/fixtures/api.js) minus 120 s and advances only when this script advances it,
 *      so nothing reads today's date.
 *   2. THE EVENT LOOP. setTimeout, setInterval, requestAnimationFrame and requestIdleCallback
 *      queue their callbacks on the monotonic clock, and advancing true time fires every
 *      callback that falls due on the way, at its own instant (ties in creation order),
 *      letting the promises each one starts settle before the next. Animation frames and idle
 *      callbacks run once a second (a browser runs frames up to sixty times a second and
 *      animateTrains does its work at most ten), plus one more frame at every instant this
 *      script reads the map. `window` is the global, as in a browser, so window.setTimeout is
 *      the same queue. Window and document events (focus, visibilitychange, pageshow) never
 *      fire: the tab stays open and visible, which is the audit's scenario.
 *      AbortSignal.timeout is the one host timer that never fires: an endpoint this harness
 *      does not answer stays pending, never erroring, so the status line carries no clause
 *      about a feed that is not under test.
 *   3. SERVER RESPONSES, each stamped with the TRUE instant of the request:
 *        /api/subways   FIX.subwaysWithSystems at that instant, restamped to a ZERO-AGE source:
 *                       envelope and every block's fetched_at = feed_timestamp = served_at, and
 *                       every row's observed_at the same instant. The trains are the fixture's
 *                       own (trip sub-1 runs from 40.74 to 40.75 between FROZEN_S - 60 and
 *                       FROZEN_S + 60), plus, from the poll at FROZEN_S on, a04-new: sub-1's
 *                       row under a new trip id.
 *        /api/alerts    zero-age, no alerts (FIX.alerts' shape).
 *        /api/subway-stops            FIX.subwayStops().
 *        /api/subway-arrivals/127     FIX.subwayArrivals' shape, zero-age, with ONE Northbound
 *                       route 1 prediction LEAD_S after the served instant.
 *      Every other fetch stays pending forever. Those sources never ingest a payload, so the
 *      status line says nothing about them.
 *   4. HOST STUBS: a DOM that synthesizes elements by id (the 09-05 F14 stub), a Leaflet stub
 *      whose markers keep their position, opacity, popup content and creation instant,
 *      matchMedia (desktop, no reduced-motion preference).
 *
 * HERMETIC: no network (the vm holds no require, no process, and only the stub fetch), no
 * credential, no NJ Transit code path is reached: the stub fetch refuses any URL naming
 * njtransit or raildata and records it, and a check asserts that list is empty; NJ Transit's
 * own /api/ fetches stay pending and are never answered. No wall clock, nothing written
 * anywhere. Deterministic: the only clock is the injected one. A few seconds, nearly all of
 * it the two-hour horizon's one-second repaints.
 *
 * RUN IT (from the repository root):
 *     node docs/reviews/audit-2026-10-12/a04_clock_offset_minimum.mjs
 * Exits 0 while the finding behaves as recorded, 1 the moment it does not, 2 if the harness
 * itself cannot run. The last line is the disposition.
 *
 * RECORDED DISPOSITION: VERIFIED. The slow-then-corrected device reads a zero-age source as
 * exactly 300 s old on every surface, and keeps doing so on every poll of the two hours
 * measured after the correction (a reload heals it); the fast-then-corrected mirror heals on
 * its first poll after the correction.
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
const FIX = require(join(ROOT, "tests/e2e/fixtures/api.js"));

/* ------------------------------------------------------------------ the scenario */

const AUDIT_AGE_S = 300; // the report's number: "a zero-age source appear 300 seconds old"
const SKEW_S = 300; // the report's "five minutes slow"
const LEAD_S = 600; // the board row's prediction: ten minutes after the served instant
// map.js polls every 15 s on its own interval. This constant only names the instants the
// script READS at, each one an instant that interval lands on; it drives nothing.
const CADENCE_S = 15;
const STATION_KEY = "subway|127"; // Times Sq-42 St in the committed FIX.subwayStops()
const TRAIN_TRIP = "sub-1"; // the committed FIX.subways() train, on the map since page load
// The same train's anchors under a trip id first served by the zero-age poll at T. Placement at T
// is read from THIS one, because a train already on the map carries its glide's monotonic clamp
// (trainLatLng never moves a marker backward within a segment), so where the frames in the
// window before that poll pushed sub-1 is where it stays for the rest of its segment whatever
// the clock does next. A train first drawn at T shows what the clock AT T does to placement.
const NEW_TRIP = "a04-new";

const T = FIX.FROZEN_S; // the instant the first zero-age response after the correction is served
const BOOT = T - 120; // page load; map.js polls at load and every 15 s, so eight polls before T
const CORRECT_AT = T - 10; // the operating system fixes the device clock (after the T - 15 poll)
const WINDOW = T - 5; // after the correction, before the next poll lands
// "Until reload", measured over two hours of polling after T. A repair that heals the
// correction only after a bounded window (a minimum over the last N minutes of samples, a
// periodic re-baseline) heals inside this horizon unless its window is longer than two hours,
// and the permanence check below then goes red; anything slower is, for this record, the
// defect. The disposition says how far it was measured rather than claiming forever.
const LATER_POLLS = 480;
const HORIZON_S = LATER_POLLS * CADENCE_S;
const RELOAD_AT = T + HORIZON_S + CADENCE_S;
// The animation frame's cadence on the injected event loop (see WHAT IS INJECTED, 2).
const FRAME_MS = 1000;

const TRAIN = FIX.subways().data.find((row) => row.trip_id === TRAIN_TRIP);

// A marker drawn on TRAIN's segment: its longitude is the segment's (prev_lon equals
// longitude in the fixture) and its latitude lies between the two anchors. No station dot
// lies on it (FIX.subwayStops() puts Times Sq-42 St at 40.7554, -73.9874), so this picks out
// a marker carrying TRAIN's row by where production drew it, not by any name.
function onTrainSegment(lat, lon) {
  const lo = Math.min(TRAIN.prev_lat, TRAIN.latitude);
  const hi = Math.max(TRAIN.prev_lat, TRAIN.latitude);
  return Math.abs(lon - TRAIN.longitude) < 1e-9 && lat >= lo - 1e-9 && lat <= hi + 1e-9;
}

// Where a correct clock draws TRAIN at true time t, re-derived from the fixture's own anchors by
// the straight-line interpolation the frontend uses when no route geometry has loaded (none is
// answered here). Printed beside the control arm, which is the measured reference.
function expectedLat(t) {
  const f = Math.min(1, Math.max(0, (t - TRAIN.prev_time) / (TRAIN.next_time - TRAIN.prev_time)));
  return TRAIN.prev_lat + (TRAIN.latitude - TRAIN.prev_lat) * f;
}

/* ------------------------------------------------------------------ payloads */

function zeroAge(body, t) {
  body.fetched_at = t;
  body.feed_timestamp = t;
  body.served_at = t;
  for (const block of Object.values(body.systems ?? {})) {
    block.fetched_at = t;
    block.feed_timestamp = t;
  }
  for (const row of body.data ?? []) row.observed_at = t;
  return body;
}

const RESPONDERS = {
  "/api/subways": (t) => {
    const data = FIX.subways().data;
    if (t >= T) data.push({ ...TRAIN, trip_id: NEW_TRIP });
    return zeroAge(FIX.subwaysWithSystems({ data, fetchedAt: t }), t);
  },
  "/api/alerts": (t) => ({ fetched_at: t, served_at: t, alerts: [] }),
  "/api/subway-stops": () => FIX.subwayStops(),
  "/api/subway-arrivals/127": (t) => ({
    fetched_at: t,
    feed_timestamp: t,
    station_id: "127",
    station_name: "Times Sq-42 St",
    directions: {
      Northbound: [
        { route_id: "1", trip_id: "a04-row", arrival: t + LEAD_S, observed_at: t, provenance: "reported" },
      ],
      Southbound: [],
    },
    served_at: t,
    systems: { "1-7+S": FIX.systemBlock(t, { routes: ["1", "2"], feedTimestamp: t }) },
  }),
};

/* ------------------------------------------------------------------ host stubs */

// The 09-05 F14 DOM stub: elements are synthesized on first lookup by id, and a querySelector
// answers a cached synthesized child rather than null.
function makeDom() {
  const byId = new Map();
  let doc;
  function makeEl(tag, id) {
    const el = {
      tagName: String(tag || "div").toUpperCase(), id: id || "", className: "",
      textContent: "", innerHTML: "", hidden: false, checked: true, value: "",
      dataset: {}, children: [], attrs: {}, listeners: {}, parentElement: null,
      style: { setProperty() {}, removeProperty() {}, getPropertyValue() { return ""; } },
    };
    const set = new Set();
    el.classList = {
      add: (...c) => c.forEach((x) => set.add(x)),
      remove: (...c) => c.forEach((x) => set.delete(x)),
      contains: (c) => set.has(c),
      toggle: (c, force) => {
        const on = force === undefined ? !set.has(c) : !!force;
        if (on) set.add(c);
        else set.delete(c);
        return on;
      },
    };
    el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
    el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
    el.removeAttribute = (k) => { delete el.attrs[k]; };
    el.hasAttribute = (k) => k in el.attrs;
    el.addEventListener = (t, fn) => { (el.listeners[t] = el.listeners[t] || []).push(fn); };
    el.removeEventListener = (t, fn) => {
      el.listeners[t] = (el.listeners[t] || []).filter((f) => f !== fn);
    };
    el.appendChild = (c) => { el.children.push(c); if (c) c.parentElement = el; return c; };
    el.append = (...cs) => cs.forEach(el.appendChild);
    el.replaceChildren = (...cs) => { el.children = []; cs.forEach(el.appendChild); };
    el.insertBefore = (c) => el.appendChild(c);
    el.removeChild = (c) => { el.children = el.children.filter((x) => x !== c); };
    el.remove = () => {};
    el._queried = new Map();
    el.querySelector = (selector) => {
      const key = String(selector);
      if (!el._queried.has(key)) {
        const child = makeEl("div", "");
        child.className = key.startsWith(".") ? key.slice(1).split(".").join(" ") : "";
        child.parentElement = el;
        el._queried.set(key, child);
      }
      return el._queried.get(key);
    };
    el.querySelectorAll = (selector) => [el.querySelector(selector)];
    el.closest = () => null;
    el.contains = (n) => n === el || el.children.some((c) => c && c.contains && c.contains(n));
    el.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 });
    el.focus = () => { doc.activeElement = el; };
    el.blur = () => { doc.activeElement = doc.body; };
    el.scrollIntoView = () => {};
    Object.defineProperty(el, "firstElementChild", { get: () => el.children[0] || null });
    return el;
  }
  doc = {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, makeEl("div", id));
      return byId.get(id);
    },
    createElement: (tag) => makeEl(tag, ""),
    createTextNode: () => makeEl("#text", ""),
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    querySelectorAll: () => [],
    documentElement: makeEl("html", ""),
    body: makeEl("body", ""),
    hidden: false,
  };
  doc.activeElement = doc.body;
  // index.html declares these two hidden; the panel starts closed exactly as the markup does.
  for (const id of ["stations-panel", "route-banner"]) {
    const el = makeEl("div", id);
    el.hidden = true;
    byId.set(id, el);
  }
  return { doc, makeEl };
}

// A Leaflet stub. Markers keep what the production code wrote to them (position, opacity,
// popup content) so it can be read back, and the true instant they were created (`born`);
// `markers` is every marker production created, and `opened` is the marker whose popup was
// opened last, which is how the popup a rider is looking at is found without naming any
// production state.
function makeLeaflet(makeEl, doc, nowS) {
  const seen = { opened: null, markers: [] };
  let stampSeq = 0;
  function makeLayerGroup() {
    const members = new Set();
    const g = {
      addTo(m) { m.addLayer(g); return g; },
      addLayer(l) { members.add(l); return g; },
      removeLayer(l) { members.delete(l); return g; },
      clearLayers() { members.clear(); return g; },
      hasLayer: (l) => members.has(l),
      getLayers: () => [...members],
      eachLayer(fn) { members.forEach(fn); return g; },
    };
    return g;
  }
  function makeMarker(latlng, options) {
    const el = makeEl("div", "");
    const rec = { _latlng: latlng, options: { ...options }, _events: {}, _popupOpen: false, popupContent: null, born: nowS() };
    seen.markers.push(rec);
    rec.on = (t, fn) => { (rec._events[t] = rec._events[t] || []).push(fn); return rec; };
    rec.off = () => rec;
    rec.fire = (t, p) => { (rec._events[t] || []).forEach((fn) => fn.call(rec, p || {})); return rec; };
    rec.addTo = (layer) => { layer.addLayer(rec); rec.fire("add"); return rec; };
    rec.bindPopup = () => rec;
    rec.getPopup = () => ({ getElement: () => null, setContent() {} });
    rec.isPopupOpen = () => rec._popupOpen;
    rec.openPopup = () => { rec._popupOpen = true; seen.opened = rec; rec.fire("popupopen"); return rec; };
    rec.closePopup = () => { rec._popupOpen = false; rec.fire("popupclose"); return rec; };
    rec.setPopupContent = (html) => { rec.popupContent = html; return rec; };
    rec.bindTooltip = (content, opts = {}) => {
      const tipEl = makeEl("div", "");
      tipEl.className = opts.className || "leaflet-tooltip";
      tipEl.textContent = String(content);
      rec._tooltip = { options: opts, getElement: () => tipEl, setContent(html) { tipEl.textContent = String(html); } };
      rec.fire("tooltipopen", { tooltip: rec._tooltip });
      return rec;
    };
    rec.unbindTooltip = () => rec;
    rec.getTooltip = () => rec._tooltip || null;
    rec.openTooltip = () => rec;
    rec.closeTooltip = () => rec;
    rec.setLatLng = (ll) => { rec._latlng = ll; return rec; };
    rec.getLatLng = () => rec._latlng;
    rec.setIcon = (icon) => { rec.options.icon = icon; return rec; };
    rec.setOpacity = (o) => { rec.options.opacity = o; return rec; };
    rec.getElement = () => el;
    rec.remove = () => rec;
    return rec;
  }
  const mapEvents = {};
  const mapLayers = new Set();
  const mapPanes = {};
  const mapObj = {
    on(t, fn) { (mapEvents[t] = mapEvents[t] || []).push(fn); return mapObj; },
    off: () => mapObj,
    fire(t, p) { (mapEvents[t] || []).forEach((fn) => fn(p || {})); return mapObj; },
    setView: () => mapObj,
    panTo: () => mapObj,
    panBy: () => mapObj,
    flyTo: () => mapObj,
    setZoom: () => mapObj,
    createPane(name) { mapPanes[name] = makeEl("div", name); return mapPanes[name]; },
    getPane: (name) => mapPanes[name] || makeEl("div", name),
    addLayer(l) { mapLayers.add(l); return mapObj; },
    removeLayer(l) { mapLayers.delete(l); return mapObj; },
    hasLayer: (l) => mapLayers.has(l),
    closePopup: () => mapObj,
    invalidateSize: () => mapObj,
    getContainer: () => doc.getElementById("map"),
    getZoom: () => 12,
    getCenter: () => ({ lat: 40.7128, lng: -74.006 }),
    latLngToContainerPoint: () => ({ x: 0, y: 0 }),
  };
  const L = {
    map: () => mapObj,
    layerGroup: makeLayerGroup,
    canvas: () => ({}),
    divIcon: (o) => ({ options: { ...o } }),
    marker: (ll, o) => makeMarker(ll, o),
    circleMarker: (ll, o) => makeMarker(ll, o),
    polyline: () => ({ addTo() { return this; }, setStyle() { return this; }, getLatLngs: () => [] }),
    tileLayer: () => ({ addTo() { return this; } }),
    control: {
      zoom: () => ({ addTo() { return this; } }),
      attribution: () => ({ addTo() { return this; } }),
      scale: () => ({ addTo() { return this; } }),
    },
    latLng: (a, b) => ({ lat: a, lng: b }),
    Util: { stamp: (obj) => (obj.__stamp ??= ++stampSeq) },
  };
  return { L, seen };
}

/* ------------------------------------------------------------------ one browser session */

const settle = async (turns = 16) => {
  for (let i = 0; i < turns; i += 1) await new Promise((r) => setImmediate(r));
};

// A page load at true time `trueS` on a device whose wall clock reads `skewS` off true time.
async function openSession(trueS, skewS) {
  const clock = { trueMs: trueS * 1000, skewMs: skewS * 1000, loadedMs: trueS * 1000 };
  const wallMs = () => clock.trueMs + clock.skewMs;
  const monoMs = () => clock.trueMs - clock.loadedMs;
  class DeviceDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(wallMs());
      else super(...args);
    }
    static now() {
      return wallMs();
    }
  }
  // timeOrigin is the DEVICE wall clock at load, read once, as a browser stamps it.
  const performanceStub = { now: monoMs, timeOrigin: wallMs() };

  // THE EVENT LOOP: every timer and frame callback, due on the monotonic clock.
  const queue = new Map(); // id -> { id, fn, args, every, due, repeat, frame }
  let timerSeq = 0;
  const schedule = (fn, ms, { repeat = false, args = [], frame = false } = {}) => {
    timerSeq += 1;
    const every = Math.max(1, Number(ms) || 0);
    queue.set(timerSeq, { id: timerSeq, fn, args, every, due: monoMs() + every, repeat, frame });
    return timerSeq;
  };
  const cancel = (id) => { queue.delete(id); };
  const fire = (entry) => {
    if (typeof entry.fn !== "function") return;
    if (entry.frame) entry.fn(performanceStub.now());
    else entry.fn(...entry.args);
  };

  const { doc, makeEl } = makeDom();
  const { L, seen } = makeLeaflet(makeEl, doc, () => clock.trueMs / 1000);
  const fetchLog = [];
  const refused = [];
  const lastServed = {};
  const fetchStub = (url) => {
    const path = String(url);
    const trueNow = clock.trueMs / 1000;
    fetchLog.push(path);
    if (/njtransit|raildata/i.test(path)) {
      refused.push(path);
      throw new Error(`hermetic harness: refused ${path}`);
    }
    const respond = RESPONDERS[path];
    if (!respond) return new Promise(() => {}); // pending forever: never answered
    const text = JSON.stringify(respond(trueNow));
    lastServed[path] = text;
    const body = JSON.parse(text);
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  };

  const sandbox = {
    console,
    document: doc,
    L,
    fetch: fetchStub,
    Date: DeviceDate,
    performance: performanceStub,
    // Window events (focus, visibilitychange, pageshow) are accepted and never fire: the tab
    // stays open and visible throughout, which is the audit's scenario.
    addEventListener() {},
    removeEventListener() {},
    innerWidth: 1280,
    innerHeight: 800,
    setInterval: (fn, ms, ...args) => schedule(fn, ms, { repeat: true, args }),
    setTimeout: (fn, ms, ...args) => schedule(fn, ms, { args }),
    clearInterval: cancel,
    clearTimeout: cancel,
    requestAnimationFrame: (fn) => schedule(fn, FRAME_MS, { frame: true }),
    cancelAnimationFrame: cancel,
    requestIdleCallback: (fn) => schedule(() => fn({ didTimeout: false, timeRemaining: () => 50 }), FRAME_MS),
    cancelIdleCallback: cancel,
    queueMicrotask: (fn) => Promise.resolve().then(fn),
    matchMedia: (q) => ({
      matches: false, media: q,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
    }),
    AbortSignal: { timeout: () => ({}) },
    URLSearchParams, URL, TextEncoder, TextDecoder,
  };
  // window IS the global, as in a browser, so a timer set as window.setTimeout is this queue.
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  const ctx = vm.createContext(sandbox);
  const FILES = [
    "frontend/helpers.js",
    "frontend/systems/shared.js",
    "frontend/systems/buses.js",
    "frontend/systems/subway.js",
    "frontend/systems/railroad.js",
    "frontend/systems/airtrain.js",
    "frontend/systems/path.js",
    "frontend/systems/ferry.js",
    "frontend/systems/njt.js",
    "frontend/stations.js",
    "frontend/map.js",
  ];
  for (const rel of FILES) vm.runInContext(read(rel), ctx, { filename: rel });
  const run = (src) => vm.runInContext(src, ctx, { filename: "a04-harness" });
  await settle(); // map.js's load-time loadAlerts() and refreshAll() land: the first poll
  run("openStationsPanel({ focusSearch: false })");
  await settle();

  // A marker's drawn position, whether production handed Leaflet [lat, lon] or {lat, lng}.
  const position = (m) => {
    const ll = m.getLatLng();
    return Array.isArray(ll) ? { lat: ll[0], lon: ll[1] } : { lat: ll.lat, lon: ll.lng ?? ll.lon };
  };
  // The markers production created on TRAIN's segment at true instant `bornS`.
  const segmentMarkers = (bornS) =>
    seen.markers.filter((m) => {
      const { lat, lon } = position(m);
      return m.born === bornS && onTrainSegment(lat, lon);
    });
  const drawn = (bornS, what) => {
    const hits = segmentMarkers(bornS);
    if (hits.length > 1) throw new Error(`harness: ${hits.length} markers match ${what}`);
    if (!hits.length) return null;
    return { ...position(hits[0]), opacity: hits[0].options.opacity ?? 1 };
  };

  const session = {
    files: FILES,
    fetchLog,
    refused,
    timersFired: 0,
    // Advance true time, firing every timer and frame that falls due on the way at its own
    // instant (earliest first, ties in creation order), and settling after each.
    async advanceTo(targetS) {
      const target = targetS * 1000 - clock.loadedMs;
      if (target < monoMs()) throw new Error("harness: time cannot run backward");
      for (;;) {
        let next = null;
        for (const entry of queue.values()) {
          if (entry.due > target) continue;
          if (!next || entry.due < next.due || (entry.due === next.due && entry.id < next.id)) next = entry;
        }
        if (!next) break;
        clock.trueMs = clock.loadedMs + next.due;
        if (next.repeat) next.due += next.every;
        else queue.delete(next.id);
        fire(next);
        session.timersFired += 1;
        await settle(2);
      }
      clock.trueMs = targetS * 1000;
    },
    correctDevice() {
      clock.skewMs = 0;
    },
    trueNow: () => clock.trueMs / 1000,
    deviceNow: () => wallMs() / 1000,
    // One more animation frame at this very instant (the one queued is run early).
    frame() {
      const queued = [...queue.values()].filter((entry) => entry.frame);
      if (!queued.length) throw new Error("harness: no animation frame was queued");
      for (const entry of queued) {
        queue.delete(entry.id);
        fire(entry);
      }
    },
    // A rider picks the station in the panel: production renders the panel AND opens the
    // station's map popup, each with its own fetch of the arrivals board at this instant.
    async pickStation() {
      if (seen.opened) seen.opened.closePopup();
      seen.opened = null;
      run(`selectStation(${JSON.stringify(STATION_KEY)})`);
      await settle();
      const popup = seen.opened ? seen.opened.popupContent ?? "" : "";
      const sentences = [];
      const walk = (el) => {
        for (const child of el.children ?? []) {
          if (child && child.tagName === "LI") sentences.push(child.textContent);
          if (child) walk(child);
        }
      };
      walk(doc.getElementById("stations-detail"));
      return { popup, sentences };
    },
    statusLine: () => doc.getElementById("status").textContent,
    // The age of the last /api/subways body served, as the page ages it at this instant:
    // the exported ingestEnvelope (the door refreshSource ingests through) and systemAges.
    sourceAge: () => {
      const body = lastServed["/api/subways"];
      if (body === undefined) return null;
      const ages = Object.values(run(`systemAges(ingestEnvelope(${body}, "subways"))`));
      return ages.length ? Math.max(...ages) : null;
    },
    // sub-1 as drawn by the marker created at page load on its segment, and a04-new as drawn
    // by the marker created at T on the same segment; null when there is none.
    train: () => drawn(trueS, `${TRAIN_TRIP} (created at load on its segment)`),
    newTrain: () => drawn(T, `${NEW_TRIP} (created at T on ${TRAIN_TRIP}'s segment)`),
    storedOffset: () => run("typeof minClockOffset === 'undefined' ? '(not present)' : minClockOffset"),
  };
  return session;
}

/* ------------------------------------------------------------------ reading a board */

function popupRow(html) {
  // One Northbound row: <span class="n">COUNTDOWN</span>, and optionally a qualifier span.
  const countdown = /<span class="n(?: now)?">([^<]*)<\/span>/.exec(html);
  const qualifier = /<span class="arr-qualifier">([^<]*)<\/span>/.exec(html);
  return {
    countdown: countdown ? countdown[1] : null,
    qualifier: qualifier ? qualifier[1] : "",
    alertsMarker: html.includes("alerts may be out of date"),
  };
}

// Every surface at one instant. `polled` says whether a poll has just landed (the status line is
// written only by the poll's tail, so it is read only then). `trains` is false for the reload
// session, where sub-1 and a04-new are both created at its load on the same segment.
async function surfaces(session, { polled, trains = true }) {
  const board = await session.pickStation();
  session.frame();
  const train = trains ? session.train() : null;
  if (trains && !train) throw new Error(`harness: train ${TRAIN_TRIP} was never drawn`);
  const fresh = trains ? session.newTrain() : null;
  return {
    trueS: session.trueNow(),
    deviceS: session.deviceNow(),
    storedOffset: session.storedOffset(),
    sourceAge: polled ? session.sourceAge() : null,
    status: polled ? session.statusLine() : null,
    ...popupRow(board.popup),
    sentence: board.sentences[0] ?? null,
    train,
    fresh,
  };
}

/* ------------------------------------------------------------------ the arms */

// The shared timeline, on the page's own timers: load at BOOT, its polls every 15 s to T - 15,
// the device clock is corrected at CORRECT_AT (if it was off), the WINDOW reading, the poll
// at T, a reading ten seconds later with no poll, then LATER_POLLS more polls, each read for
// its source age as it lands.
async function runArm(name, skewS) {
  const session = await openSession(BOOT, skewS);
  await session.advanceTo(T - CADENCE_S);
  const beforeCorrection = await surfaces(session, { polled: true });
  await session.advanceTo(CORRECT_AT);
  session.correctDevice();
  await session.advanceTo(WINDOW);
  const window = await surfaces(session, { polled: false });
  await session.advanceTo(T);
  const atT = await surfaces(session, { polled: true });
  await session.advanceTo(T + 10);
  session.frame();
  const tenLater = session.newTrain();
  const laterAges = [];
  for (let k = 1; k <= LATER_POLLS; k += 1) {
    await session.advanceTo(T + k * CADENCE_S);
    laterAges.push(session.sourceAge());
  }
  const atEnd = await surfaces(session, { polled: true });
  return {
    name, skewS, beforeCorrection, window, atT, tenLater, laterAges, atEnd,
    fetchLog: session.fetchLog, refused: session.refused, timersFired: session.timersFired, files: session.files,
  };
}

async function runReload() {
  const session = await openSession(RELOAD_AT, 0);
  return { ...(await surfaces(session, { polled: true, trains: false })), fetchLog: session.fetchLog, refused: session.refused };
}

/* ------------------------------------------------------------------ report */

const say = (...args) => console.log(...args);
const rule = (title) => {
  say("=".repeat(78));
  say(title);
  say("=".repeat(78));
};
const fmt = (n) => (typeof n === "number" ? (Number.isInteger(n) ? String(n) : n.toFixed(4)) : String(n));
const at = (trueS) => {
  const d = trueS - T;
  return d === 0 ? "T" : d > 0 ? `T+${d}` : `T${d}`;
};
// A long run of readings, compressed: "300 x480", or "300 x39, 0 x441".
const runs = (values) => {
  const out = [];
  for (const v of values) {
    if (out.length && out[out.length - 1].v === v) out[out.length - 1].n += 1;
    else out.push({ v, n: 1 });
  }
  return out.map(({ v, n }) => `${fmt(v)} x${n}`).join(", ");
};

function printInstant(label, s) {
  say(`  ${label}`);
  say(`      true time ${at(s.trueS)}   device clock reads ${fmt(s.deviceS - s.trueS)} s off true   stored offset (diagnostic) ${fmt(s.storedOffset)}`);
  if (s.sourceAge != null) say(`      zero-age source, age by systemAges ......... ${fmt(s.sourceAge)} s`);
  if (s.status != null) say(`      status line (#status) ....................... ${JSON.stringify(s.status)}`);
  say(`      popup row: countdown ${JSON.stringify(s.countdown)}, qualifier ${JSON.stringify(s.qualifier)}, alerts marker ${s.alertsMarker}`);
  say(`      panel sentence .............................. ${JSON.stringify(s.sentence)}`);
  if (s.train) {
    say(`      train ${TRAIN_TRIP}: lat ${fmt(s.train.lat)} (correct clock: ${fmt(expectedLat(s.trueS))}), opacity ${fmt(s.train.opacity)}`);
  }
  if (s.fresh) {
    say(`      train ${NEW_TRIP}: lat ${fmt(s.fresh.lat)} (correct clock: ${fmt(expectedLat(s.trueS))}), opacity ${fmt(s.fresh.opacity)}`);
  }
}

function printArm(arm) {
  rule(`ARM ${arm.name}: device clock ${arm.skewS === 0 ? "right throughout" : `${Math.abs(arm.skewS)} s ${arm.skewS < 0 ? "SLOW" : "FAST"}, corrected at T-${T - CORRECT_AT}`}`);
  printInstant(`last poll before the correction (T-${CADENCE_S})`, arm.beforeCorrection);
  printInstant(`after the correction, before the next poll (T-${T - WINDOW})`, arm.window);
  printInstant("the first zero-age poll after the correction (T)", arm.atT);
  say(`  ten seconds later, no poll: train ${NEW_TRIP} lat ${fmt(arm.tenLater?.lat)} (correct clock: ${fmt(expectedLat(T + 10))})`);
  say(`  source age after each of the next ${LATER_POLLS} polls (${HORIZON_S / 3600} h): ${runs(arm.laterAges)}`);
  printInstant(`after the last of them (T+${HORIZON_S})`, arm.atEnd);
  say(`  timers and frames the page's event loop fired in this session: ${arm.timersFired}`);
  say("");
}

async function main() {
  const control = await runArm("control", 0);
  const slow = await runArm("slow", -SKEW_S);
  const fast = await runArm("fast", SKEW_S);
  const reload = await runReload();

  rule("A04: a clock correction and the session-wide minimum offset");
  say("Production files executed in one node:vm context, in index.html's order:");
  for (const rel of control.files) say(`  ${rel}`);
  say(`True time T = FIX.FROZEN_S = ${T} (${new Date(T * 1000).toISOString()}). Page load at T-${T - BOOT}; from there`);
  say(`the page's own timers run on the monotonic clock (its vehicle poll lands every ${CADENCE_S} s).`);
  say(`Every response is zero-age at the true instant it is served. Board row: one prediction ${LEAD_S} s after it.`);
  say(`Train ${TRAIN_TRIP}: ${TRAIN.prev_lat} at T${TRAIN.prev_time - T} to ${TRAIN.latitude} at T+${TRAIN.next_time - T}, served from page load;`);
  say(`train ${NEW_TRIP}: the same anchors, first served by the poll at T.`);
  say(`Fetches answered: ${Object.keys(RESPONDERS).join(", ")}; every other fetch is left pending.`);
  say("");
  printArm(control);
  printArm(slow);
  printArm(fast);
  rule(`ARM reload: a new session at T+${RELOAD_AT - T} on the corrected device`);
  printInstant("its first poll", reload);
  say("");

  const near = (a, b) => Math.abs(a - b) < 1e-9;
  const allFetches = [control, slow, fast].flatMap((arm) => arm.fetchLog).concat(reload.fetchLog);
  const allRefused = [control, slow, fast].flatMap((arm) => arm.refused).concat(reload.refused);
  const firstHealed = (ages) => {
    const k = ages.findIndex((age) => age !== AUDIT_AGE_S);
    return k < 0 ? "never" : `later poll ${k + 1} (${at(T + (k + 1) * CADENCE_S)}) reads ${fmt(ages[k])} s`;
  };
  const c = control.atT;
  const s = slow.atT;
  const bothFresh = c.fresh !== null && s.fresh !== null;
  const checks = [
    // The control: the harness itself is not biased.
    ["control: a correct device reads the zero-age source as 0 s old, the status line is silent",
      c.sourceAge === 0 && c.status === "" && c.qualifier === "",
      `age ${c.sourceAge}, status ${JSON.stringify(c.status)}, qualifier ${JSON.stringify(c.qualifier)}`],
    ["control: the row 600 s out counts down \"10 min\" in the popup and \"in 10 minutes\" in the panel",
      c.countdown === "10 min" && / in 10 minutes, /.test(c.sentence ?? ""),
      `${JSON.stringify(c.countdown)} / ${JSON.stringify(c.sentence)}`],
    ["control: both trains are drawn where their anchors put them at true time, undimmed",
      c.fresh !== null && near(c.fresh.lat, expectedLat(T)) && c.fresh.opacity === 1 &&
        near(c.train.lat, expectedLat(T)) && c.train.opacity === 1,
      `${NEW_TRIP} lat ${fmt(c.fresh?.lat)}, ${TRAIN_TRIP} lat ${fmt(c.train.lat)} (expected ${fmt(expectedLat(T))}), opacity ${c.fresh?.opacity}`],
    // The slow device, before the correction: the calibration compensates, so this is a defect
    // of the CORRECTION and not of skew as such.
    ["slow: before the correction the minimum offset compensates (age 0, countdown \"10 min\")",
      slow.beforeCorrection.sourceAge === 0 && slow.beforeCorrection.countdown === "10 min",
      `age ${slow.beforeCorrection.sourceAge}, countdown ${JSON.stringify(slow.beforeCorrection.countdown)}`],
    // THE FINDING.
    [`slow: after the correction a zero-age source reads ${AUDIT_AGE_S} s old (audit: ${AUDIT_AGE_S} s)`,
      s.sourceAge === AUDIT_AGE_S,
      `measured ${s.sourceAge} s, audit ${AUDIT_AGE_S} s`],
    ["slow: freshness label, the status line map.js writes says the trains are 5 minutes old",
      s.status === "trains: as of 5m ago",
      `${JSON.stringify(s.status)} (control ${JSON.stringify(c.status)})`],
    ["slow: freshness label, the board row observed at the served instant says \"as of 5m ago\"",
      s.qualifier === "as of 5m ago",
      `${JSON.stringify(s.qualifier)} (control ${JSON.stringify(c.qualifier)})`],
    ["slow: countdown, the popup row 600 s out reads \"5 min\"",
      s.countdown === "5 min",
      `${JSON.stringify(s.countdown)} (control ${JSON.stringify(c.countdown)})`],
    ["slow: countdown, the panel says \"in 5 minutes\" beside the arrival clock time the control prints",
      / in 5 minutes, /.test(s.sentence ?? "") && clockOf(s.sentence) !== null && clockOf(s.sentence) === clockOf(c.sentence),
      `${JSON.stringify(s.sentence)} (control ${JSON.stringify(c.sentence)})`],
    ["slow: estimated motion, a train first served at T is drawn at its next station, not between the two",
      bothFresh && near(s.fresh.lat, TRAIN.latitude) && !near(s.fresh.lat, c.fresh.lat),
      `lat ${fmt(s.fresh?.lat)} (next station ${TRAIN.latitude}; correct clock ${fmt(c.fresh?.lat)})`],
    ["slow: estimated motion, and it does not move: ten seconds later it is still there",
      bothFresh && slow.tenLater !== null && control.tenLater !== null &&
        near(slow.tenLater.lat, s.fresh.lat) && !near(control.tenLater.lat, c.fresh.lat),
      `slow ${fmt(s.fresh?.lat)} -> ${fmt(slow.tenLater?.lat)}; control ${fmt(c.fresh?.lat)} -> ${fmt(control.tenLater?.lat)}`],
    ["slow: that train's marker is dimmed as stale",
      bothFresh && s.fresh.opacity < 1 && c.fresh.opacity === 1,
      `opacity ${fmt(s.fresh?.opacity)} (control ${fmt(c.fresh?.opacity)})`],
    ["slow: the window before that poll already reads the same, and pushes sub-1 to its next station",
      slow.window.countdown === "5 min" && slow.window.qualifier === "as of 5m ago" &&
        near(slow.window.train.lat, TRAIN.latitude) && !near(control.window.train.lat, TRAIN.latitude),
      `${JSON.stringify(slow.window.countdown)}, ${JSON.stringify(slow.window.qualifier)}, ${TRAIN_TRIP} lat ${fmt(slow.window.train.lat)} (control ${fmt(control.window.train.lat)})`],
    // "until reload": no fresh poll in the two hours measured heals it.
    [`slow: ${LATER_POLLS} more zero-age polls (${HORIZON_S / 3600} h) leave every one ${AUDIT_AGE_S} s old, and the board and status line with them`,
      slow.laterAges.length === LATER_POLLS && slow.laterAges.every((age) => age === AUDIT_AGE_S) &&
        slow.atEnd.countdown === "5 min" && slow.atEnd.status === "trains: as of 5m ago",
      `ages ${runs(slow.laterAges)}, first healed: ${firstHealed(slow.laterAges)}; at T+${HORIZON_S}: ${JSON.stringify(slow.atEnd.countdown)}, ${JSON.stringify(slow.atEnd.status)}`],
    ["reload: a new session on the corrected device reads the same source as 0 s old",
      reload.sourceAge === 0 && reload.countdown === "10 min" && reload.status === "",
      `age ${reload.sourceAge}, countdown ${JSON.stringify(reload.countdown)}, status ${JSON.stringify(reload.status)}`],
    // Beyond the audit's list: the alerts marker reads the same corrected clock.
    ["slow (not in the audit): a zero-age alerts feed is marked \"alerts may be out of date\" (its 300 s meets the 300 s alerts threshold)",
      s.alertsMarker === true && c.alertsMarker === false,
      `slow ${s.alertsMarker}, control ${c.alertsMarker}`],
    // The mirror.
    ["fast: before the correction the minimum offset compensates (age 0, countdown \"10 min\")",
      fast.beforeCorrection.sourceAge === 0 && fast.beforeCorrection.countdown === "10 min",
      `age ${fast.beforeCorrection.sourceAge}, countdown ${JSON.stringify(fast.beforeCorrection.countdown)}`],
    ["fast: between the correction and the next poll the clock runs 300 s behind (\"15 min\", no age shown)",
      fast.window.countdown === "15 min" && fast.window.qualifier === "",
      `${JSON.stringify(fast.window.countdown)}, qualifier ${JSON.stringify(fast.window.qualifier)}`],
    ["fast: the first poll after the correction heals every surface",
      fast.atT.sourceAge === 0 && fast.atT.status === "" && fast.atT.countdown === "10 min" &&
        fast.atT.qualifier === "" && fast.atT.fresh !== null && c.fresh !== null && near(fast.atT.fresh.lat, c.fresh.lat) &&
        fast.atT.fresh.opacity === 1 && fast.atT.alertsMarker === false,
      `age ${fast.atT.sourceAge}, ${JSON.stringify(fast.atT.countdown)}, ${NEW_TRIP} lat ${fmt(fast.atT.fresh?.lat)}, opacity ${fmt(fast.atT.fresh?.opacity)}`],
    ["fast: and stays healed",
      fast.laterAges.every((age) => age === 0) && fast.atEnd.countdown === "10 min",
      `ages ${runs(fast.laterAges)}, ${JSON.stringify(fast.atEnd.countdown)}`],
    // The harness: nothing left the page's own origin, nothing named an NJ Transit host, and
    // nothing this harness answers is an NJ Transit endpoint.
    ["hermetic: every fetch was to the page's own /api/, none was refused for naming njtransit or raildata, none to NJ Transit answered",
      allFetches.length > 0 && allFetches.every((p) => p.startsWith("/api/")) && allRefused.length === 0 &&
        !Object.keys(RESPONDERS).some((p) => /njt|njtransit|raildata/i.test(p)),
      `${allFetches.length} fetches, ${new Set(allFetches).size} distinct paths, ${Object.keys(RESPONDERS).length} answered, ${allRefused.length} refused`],
  ];

  rule("CHECKS (measured value beside the audit's or the control's)");
  let failed = 0;
  for (const [label, ok, detail] of checks) {
    if (!ok) failed += 1;
    say(`  ${ok ? "PASS" : "FAIL"}  ${label}`);
    say(`        ${detail}`);
  }
  say("");
  if (failed) {
    say(`${failed} of ${checks.length} checks failed.`);
    say("DISPOSITION: NOT AS RECORDED, see the failed checks above");
    return 1;
  }
  say(`All ${checks.length} checks passed.`);
  say(
    `DISPOSITION: VERIFIED  A device ${SKEW_S} s slow, then corrected, reads a zero-age source as ` +
      `${s.sourceAge} s old (audit ${AUDIT_AGE_S} s) on all ${LATER_POLLS + 1} polls of the ${HORIZON_S / 3600} h measured ` +
      `after the correction: status "${s.status}", row "${s.qualifier}", a ${LEAD_S} s countdown reads ` +
      `"${s.countdown}", and a glided train is drawn at its next station, not moving, dimmed; a reload ` +
      `heals it. The ${SKEW_S} s fast mirror reads "${fast.window.countdown}" until the next poll, which ` +
      `heals it, because a minimum accepts a smaller sample and never a larger one.`,
  );
  return 0;
}

function clockOf(sentence) {
  const m = /, (\d{1,2}:\d\d [AP]M) arrival/.exec(sentence ?? "");
  return m ? m[1] : null;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    console.error("HARNESS ERROR:", err && err.stack ? err.stack : err);
    process.exit(2);
  },
);
