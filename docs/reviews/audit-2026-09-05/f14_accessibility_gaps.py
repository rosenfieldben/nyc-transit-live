#!/usr/bin/env python3
"""F14 (P2) "Important accessibility functions remain unavailable": verification.

WHAT THE AUDIT CLAIMED (docs/reviews/audit-2026-09-05.md, section 1, F14):

    "The existing statement candidly acknowledges that updates cannot be paused,
     arbitrary pointer panning relies on dragging, buses have no station-panel
     equivalent, and no manual assistive-technology testing has been performed.
     These are known limitations, not newly discovered regressions. Removing
     individual markers from Tab order is sensible at fleet scale, but bus popup
     information, route alerts and route drawing then have no keyboard equivalent."

    Evidence cited: ACCESSIBILITY.md, frontend/systems/buses.js around line 270,
    and GitHub issue #88.

So F14 is a claim about DOCUMENTED limitations. This script checks each of the four
claims separately against the code that is supposed to have them, and checks that
ACCESSIBILITY.md still says so.

WHAT THIS SCRIPT MEASURES, AND HOW

  Production code, driven for real. The whole frontend (helpers.js, systems/shared.js,
  the seven systems/*.js files, stations.js, map.js) is loaded into one node:vm context
  in the exact order index.html loads it, with stub browser globals (a fake Leaflet, a
  fake DOM, recording timers, a fake fetch). Nothing in the frontend is reimplemented
  here: the real refreshAll, the real applyBuses, the real labeledMarker, the real
  registerStation, the real renderStationResults, the real selectStation and the real
  Escape handler all run. The script then reports what that production code did:
  which timers the page started, what options each marker was built with, what the
  station registry holds, what the panel search answers, and whether any control the
  page owns can stop any of it.

  Injected, and named as such:
    - the transport. fetch is a stub. Exactly three endpoints answer, from the
      COMMITTED fixture module tests/e2e/fixtures/api.js: /api/subway-stops,
      /api/buses and /api/subway-arrivals/127. Every other endpoint returns a promise
      that never settles, so no poll completes and nothing reaches a network.
    - the clock. Date.now is pinned to that fixture module's own FROZEN_MS
      (2026-07-02T12:00:00Z), so no measurement here depends on today's date.
    - Leaflet and the DOM are stubs. This proves what the application code DOES
      (the options it passes, the attributes it writes, the timers it starts), not
      what a browser paints. The browser half is the repository's Playwright suite.

  Static checks over the real sources, for the parts that are properties of the
  source rather than of one execution: the control inventory in frontend/index.html,
  the map-movement call sites, the registerStation call sites, the openPopup call
  sites, and the wording of ACCESSIBILITY.md.

  Claim (d) is about a PROCESS, not about code. It is therefore verified as a
  documentation check: that ACCESSIBILITY.md still states no assistive technology has
  been used, and that nothing anywhere else in the repository records such a session.

  GitHub issue #88 was read live with the GitHub MCP tools at verification time
  (2026-09-05): state "open", opened 2026-08-03, titled "Accessibility: auto-updating
  content cannot be paused (WCAG 2.2.2)", and it was the repository's ONLY open issue
  (total open count 1), which matches the audit. That observation is recorded here
  rather than re-queried, because this script must stay hermetic and deterministic.

RUN IT (from the repository root):

    python3 docs/reviews/audit-2026-09-05/f14_accessibility_gaps.py

Exits 0 while the finding still behaves as recorded, non-zero when reality has moved.
Requires node (v22 here) on PATH. No credentials, no network, no NJ Transit anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))

# Recorded when this verification ran; see the module docstring.
ISSUE_88 = {
    "number": 88,
    "state": "open",
    "created_at": "2026-08-03",
    "title": "Accessibility: auto-updating content cannot be paused (WCAG 2.2.2)",
    "open_issues_in_repo": 1,
    "read_at": "2026-09-05 (GitHub MCP, issue_read + list_issues)",
}

# (claim letter, message) for every assertion that did not hold.
failures: list[tuple[str, str]] = []


def check(ok: bool, message: str, claim: str = "setup") -> bool:
    """Record an assertion against one claim. All are reported, then one exit."""
    if not ok:
        failures.append((claim, message))
    return ok


def read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8") as handle:
        return handle.read()


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


# ---------------------------------------------------------------------------
# The node:vm harness. Written to a temp file at run time, deleted afterwards.
# ---------------------------------------------------------------------------

HARNESS = r"""
// Loads the REAL frontend into one vm context, in index.html's order, with stub
// browser globals. Prints one JSON object describing what production code did.
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = process.argv[2];
const FIX = require(path.join(ROOT, "tests/e2e/fixtures/api.js"));

const report = { loaded: [], loadErrors: [], fetchesAttempted: [], mapMoves: [] };

/* ---- recording timers ---- */
let nextTimer = 1;
const live = new Map();
function describe(fn) {
  if (fn && fn.name) return fn.name;
  const src = String(fn || "").replace(/\s+/g, " ").trim();
  return src.length > 54 ? src.slice(0, 54) + "..." : src;
}
function recordTimer(kind, fn, delay) {
  const id = nextTimer++;
  live.set(id, { kind, delay: delay || 0, label: describe(fn) });
  return id;
}
const clearStub = (id) => { live.delete(id); };
let rafCount = 0;

/* ---- stub DOM ---- */
function makeEl(tag, id) {
  const el = {
    tagName: String(tag || "div").toUpperCase(), id: id || "", className: "",
    textContent: "", innerHTML: "", hidden: false, checked: true, value: "",
    dataset: {}, children: [], attrs: {}, listeners: {}, parentElement: null,
    style: { setProperty() {}, removeProperty() {} },
  };
  const set = new Set();
  el.classList = {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    contains: (c) => set.has(c),
    toggle: (c, force) => {
      const on = force === undefined ? !set.has(c) : !!force;
      if (on) set.add(c); else set.delete(c);
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
  el.querySelector = () => null;
  el.querySelectorAll = () => [];
  el.closest = () => null;
  el.contains = (n) => n === el || el.children.some((c) => c && c.contains && c.contains(n));
  el.getBoundingClientRect = () => ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 });
  el.focus = () => { doc.activeElement = el; };
  el.blur = () => { doc.activeElement = doc.body; };
  el.scrollIntoView = () => {};
  Object.defineProperty(el, "firstElementChild", { get: () => el.children[0] || null });
  return el;
}
const byId = new Map();
const docListeners = {};
const doc = {
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, makeEl("div", id));
    return byId.get(id);
  },
  createElement: (tag) => makeEl(tag, ""),
  createTextNode: () => makeEl("#text", ""),
  addEventListener(t, fn) { (docListeners[t] = docListeners[t] || []).push(fn); },
  removeEventListener() {},
  querySelector: () => null,
  querySelectorAll: () => [],
  documentElement: makeEl("html", ""),
  body: makeEl("body", ""),
  hidden: false,
};
doc.activeElement = doc.body;

/* ---- stub Leaflet ---- */
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
const markers = [];
function makeMarker(latlng, options, factory) {
  const el = makeEl("div", "");
  const rec = {
    _factory: factory, _latlng: latlng, options: { ...options }, _events: {},
    _popupFn: null, _popupOpen: false, _element: el, _opacity: 1,
  };
  markers.push(rec);
  rec.on = (t, fn) => { (rec._events[t] = rec._events[t] || []).push(fn); return rec; };
  rec.off = () => rec;
  rec.fire = (t, p) => { (rec._events[t] || []).forEach((fn) => fn.call(rec, p || {})); return rec; };
  rec.addTo = (layer) => { layer.addLayer(rec); rec.fire("add"); return rec; };
  rec.bindPopup = (content) => { rec._popupFn = content; return rec; };
  rec.getPopup = () => (rec._popupFn ? { getElement: () => null, setContent() {} } : null);
  rec.isPopupOpen = () => rec._popupOpen;
  rec.openPopup = () => { rec._popupOpen = true; rec.fire("popupopen"); return rec; };
  rec.closePopup = () => { rec._popupOpen = false; rec.fire("popupclose"); return rec; };
  rec.setPopupContent = () => rec;
  rec.setLatLng = (ll) => { rec._latlng = ll; return rec; };
  rec.getLatLng = () => rec._latlng;
  rec.setIcon = (icon) => { rec.options.icon = icon; return rec; };
  rec.setOpacity = (o) => { rec._opacity = o; return rec; };
  rec.getElement = () => rec._element;
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
  setView(...a) { report.mapMoves.push({ how: "setView", args: JSON.stringify(a) }); return mapObj; },
  panTo(...a) { report.mapMoves.push({ how: "panTo", args: JSON.stringify(a[0]) }); return mapObj; },
  panBy(...a) { report.mapMoves.push({ how: "panBy", args: JSON.stringify(a[0]) }); return mapObj; },
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
};
const L = {
  map: () => mapObj,
  layerGroup: makeLayerGroup,
  canvas: () => ({}),
  divIcon: (o) => ({ options: { ...o } }),
  marker: (ll, o) => makeMarker(ll, o, "L.marker"),
  circleMarker: (ll, o) => makeMarker(ll, o, "L.circleMarker"),
  polyline: () => ({ addTo() { return this; }, setStyle() { return this; }, getLatLngs: () => [] }),
  tileLayer: () => ({ addTo() { return this; } }),
};

/* ---- stub network: three committed-fixture endpoints, everything else hangs ---- */
const responders = new Map();
function fetchStub(url) {
  report.fetchesAttempted.push(String(url));
  if (responders.has(String(url))) {
    const body = responders.get(String(url));
    return Promise.resolve({ ok: true, status: 200, json: async () => body });
  }
  return new Promise(() => {});
}

// index.html declares these two hidden. The stub DOM starts them that way, so the
// panel begins closed exactly as the markup does and a rider has to open it.
for (const id of ["stations-panel", "route-banner"]) {
  const el = makeEl("div", id);
  el.hidden = true;
  byId.set(id, el);
}

const sandbox = {
  console, document: doc, L, fetch: fetchStub,
  window: { addEventListener() {}, removeEventListener() {}, innerWidth: 1280, innerHeight: 720 },
  setInterval: (fn, d) => recordTimer("interval", fn, d),
  setTimeout: (fn, d) => recordTimer("timeout", fn, d),
  clearInterval: clearStub,
  clearTimeout: clearStub,
  requestAnimationFrame: () => { rafCount += 1; return rafCount; },
  cancelAnimationFrame: () => {},
  queueMicrotask: (fn) => Promise.resolve().then(fn),
  AbortSignal: { timeout: () => ({}) },
  URLSearchParams, URL, TextEncoder, TextDecoder, performance,
};
sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);

// The fixture module's own capture instant. Nothing here reads the wall clock.
vm.runInContext("Date.now = () => " + FIX.FROZEN_MS + ";", ctx);
report.frozenClockMs = FIX.FROZEN_MS;

responders.set("/api/subway-stops", FIX.subwayStops());
responders.set("/api/buses", FIX.buses());
responders.set("/api/subway-arrivals/127", FIX.subwayArrivals());
report.busFixture = FIX.buses().data;

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
for (const rel of FILES) {
  try {
    vm.runInContext(fs.readFileSync(path.join(ROOT, rel), "utf8"), ctx, { filename: rel });
    report.loaded.push(rel);
  } catch (err) {
    report.loadErrors.push(rel + ": " + (err && err.message));
    break;
  }
}

const flush = () => new Promise((resolve) => setImmediate(resolve));
const snapshot = () => [...live.entries()].map(([id, t]) => ({ id, ...t }));
const runInCtx = (code) => vm.runInContext(code, ctx);

const CONTROL_IDS = [
  "stations-skip", "stations-toggle", "stations-close", "stations-search",
  "legend-toggle", "route-clear", "toggle-buses", "toggle-subways",
  "toggle-stations", "toggle-railroads", "toggle-airtrain", "toggle-path",
  "toggle-ferries", "toggle-njt",
];

const evt = (el, type, extra) => Object.assign({
  type, target: el, currentTarget: el,
  preventDefault() {}, stopPropagation() {}, stopImmediatePropagation() {},
}, extra || {});
const press = (id, type, extra) => {
  const el = byId.get(id);
  (el.listeners[type] || []).forEach((fn) => fn(evt(el, type, extra)));
};

async function main() {
  if (report.loadErrors.length) return;
  for (let i = 0; i < 12; i += 1) await flush(); // let the three answered endpoints land

  report.constants = {
    POLL_INTERVAL_MS: runInCtx("POLL_INTERVAL_MS"),
    ALERT_POLL_INTERVAL_MS: runInCtx("ALERT_POLL_INTERVAL_MS"),
    TRAIN_TICK_MS: runInCtx("TRAIN_TICK_MS"),
    liveSources: JSON.parse(runInCtx("JSON.stringify(Object.keys(sources))")),
  };
  report.timersAtLoad = snapshot();
  report.rafAtLoad = rafCount;
  report.movesAtLoad = report.mapMoves.length;

  report.controlsWithListeners = {};
  for (const id of CONTROL_IDS) {
    const el = byId.get(id);
    report.controlsWithListeners[id] = el ? Object.keys(el.listeners) : [];
  }
  report.documentListeners = Object.keys(docListeners);

  // What the bus layer built, straight off the markers the real applyBuses made.
  report.markerFactoryCounts = markers.reduce((acc, m) => {
    acc[m._factory] = (acc[m._factory] || 0) + 1;
    return acc;
  }, {});
  report.busMarkers = markers
    .filter((m) => m._factory === "L.marker"
      && m.options.icon && String(m.options.icon.options.className || "").includes("bus"))
    .map((m) => ({
      keyboardOption: "keyboard" in m.options ? m.options.keyboard : "ABSENT",
      role: m._element.getAttribute("role"),
      ariaLabel: m._element.getAttribute("aria-label"),
      appWroteTabindex: m._element.hasAttribute("tabindex"),
      popupHtml: typeof m._popupFn === "function" ? m._popupFn(m) : null,
      popupEvents: Object.keys(m._events),
    }));

  report.registry = JSON.parse(runInCtx(
    "JSON.stringify(stationRegistry.map((e) => ({ key: e.key, kind: e.kind, name: e.name })))"));

  // A rider opens the station panel with the page's own control, not by fiat.
  report.panelOpenAtLoad = runInCtx("stationsPanelOpen()");
  press("stations-toggle", "click");
  report.panelOpenAfterToggle = runInCtx("stationsPanelOpen()");

  report.panelSearch = {};
  for (const q of ["Times", "M15", "B46", "MTA NYCT_101", "bus"]) {
    runInCtx("stationsSearch.value = " + JSON.stringify(q) + "; renderStationResults();");
    report.panelSearch[q] = {
      status: byId.get("stations-status").textContent,
      rows: byId.get("stations-results").children.length,
    };
  }

  // The real panel path for a station a rider picked: it starts the countdown tick.
  const movesBeforeSelect = report.mapMoves.length;
  runInCtx("selectStation('subway|127');");
  for (let i = 0; i < 12; i += 1) await flush();
  report.timersAfterSelect = snapshot();
  report.movesFromStationSelect = report.mapMoves.slice(movesBeforeSelect);

  // The pause probe: drive every listener the page bound to a control it owns, plus
  // document-level keydowns for the keys a pause shortcut would plausibly use, and
  // watch which of the running timers (if any) stop.
  const before = snapshot();
  const probes = [];
  function fire(label, fn) {
    const liveBefore = new Set(live.keys());
    let error = null;
    try { fn(); } catch (err) { error = String((err && err.message) || err); }
    probes.push({
      label, error,
      cleared: [...liveBefore].filter((id) => !live.has(id))
        .map((id) => before.find((t) => t.id === id) || { id }),
    });
  }
  for (const id of CONTROL_IDS) {
    const el = byId.get(id);
    if (!el) continue;
    for (const type of Object.keys(el.listeners)) {
      if (type === "change") {
        fire(id + ":change(uncheck)", () => {
          el.checked = false;
          el.listeners[type].forEach((fn) => fn(evt(el, type)));
        });
        fire(id + ":change(recheck)", () => {
          el.checked = true;
          el.listeners[type].forEach((fn) => fn(evt(el, type)));
        });
      } else {
        fire(id + ":" + type, () => el.listeners[type].forEach((fn) => fn(evt(el, type))));
      }
    }
  }
  for (const key of [" ", "p", "k", "Escape", "Pause", "MediaPlayPause"]) {
    fire("document:keydown(" + (key === " " ? "Space" : key) + ")",
      () => (docListeners.keydown || []).forEach((fn) => fn(evt(doc.body, "keydown", { key }))));
  }
  report.pauseProbe = { before, probes, after: snapshot() };
}

main()
  .catch((err) => { report.fatal = String((err && err.stack) || err); })
  .then(() => { process.stdout.write(JSON.stringify(report)); });
"""


def run_harness() -> dict:
    node = shutil.which("node")
    if not node:
        print("FATAL: node is not on PATH; this check drives the real frontend in node:vm.")
        raise SystemExit(2)
    workdir = tempfile.mkdtemp(prefix="f14-harness-")
    try:
        script = os.path.join(workdir, "harness.js")
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(HARNESS)
        proc = subprocess.run(
            [node, script, REPO], capture_output=True, text=True, timeout=180, check=False
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            print("FATAL: the vm harness did not produce a report.")
            print(proc.stdout[-2000:])
            print(proc.stderr[-2000:])
            raise SystemExit(2)
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Static inventories over the real sources
# ---------------------------------------------------------------------------

CONTROL_TAG = re.compile(r"<(button|input|select|textarea|a)\b([^>]*)>", re.I)
ATTR = re.compile(r"([a-zA-Z-]+)\s*=\s*\"([^\"]*)\"")
PAUSE_WORDS = re.compile(
    r"\b(pause|paused|unpause|resume|stop|stopped|play|freeze|frozen|hold|suspend|"
    r"slow\s*down|update\s*rate|refresh\s*rate)\b", re.I)
PAN_WORDS = re.compile(
    r"\b(pan|scroll\s*map|move\s*map|recenter|re-centre|nudge|north|south|east|west|"
    r"left|right|up|down)\b", re.I)


def html_controls(html: str) -> list[dict]:
    """Every interactive element the app's own markup declares, in document order."""
    body = html[html.index("<body"):]
    out = []
    for match in CONTROL_TAG.finditer(body):
        tag = match.group(1).lower()
        attrs = dict((k.lower(), v) for k, v in ATTR.findall(match.group(2)))
        if tag == "a" and "href" not in attrs:
            continue
        tail = body[match.end():]
        text = re.sub(r"<[^>]*>", " ", tail.split("</" + tag + ">")[0] if "</" + tag + ">" in tail else "")
        out.append({
            "tag": tag,
            "id": attrs.get("id", ""),
            "type": attrs.get("type", ""),
            "aria": attrs.get("aria-label", ""),
            "text": " ".join(text.split())[:44],
        })
    return out


def source_hits(pattern: str, files: list[str]) -> list[str]:
    """file:line: text for a regex over production sources, comments excluded."""
    rx = re.compile(pattern)
    hits = []
    for rel in files:
        for number, line in enumerate(read(rel).splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                continue
            if rx.search(line):
                hits.append(f"{rel}:{number}: {stripped[:96]}")
    return hits


FRONTEND = [
    "frontend/helpers.js",
    "frontend/map.js",
    "frontend/stations.js",
    "frontend/systems/shared.js",
    "frontend/systems/buses.js",
    "frontend/systems/subway.js",
    "frontend/systems/railroad.js",
    "frontend/systems/airtrain.js",
    "frontend/systems/path.js",
    "frontend/systems/ferry.js",
    "frontend/systems/njt.js",
]


def main() -> int:
    print("F14 (P2): important accessibility functions remain unavailable")
    print("Verification of four documented limitations, against the code and the statement.")
    print(f"Repository root: {REPO}")

    data = run_harness()
    if data.get("loadErrors") or data.get("fatal"):
        print("FATAL: could not load the production frontend into the vm.")
        print(json.dumps({"loadErrors": data.get("loadErrors"), "fatal": data.get("fatal")}, indent=2))
        return 2

    html = read("frontend/index.html")
    access_raw = read("ACCESSIBILITY.md")
    # Phrase checks run against a whitespace-flattened copy, so a sentence that the
    # document happens to wrap across two lines still matches the sentence.
    access = " ".join(access_raw.split())
    controls = html_controls(html)

    rule("SETUP: the production frontend, loaded and driven")
    print(f"  files loaded into one node:vm context, in index.html order : {len(data['loaded'])}")
    for rel in data["loaded"]:
        print(f"      {rel}")
    print(f"  clock pinned to the fixture's own capture instant           : "
          f"{data['frozenClockMs']} ms (2026-07-02T12:00:00Z)")
    print(f"  endpoints answered from committed fixtures                  : "
          f"/api/subway-stops, /api/buses, /api/subway-arrivals/127")
    print(f"  endpoints the page requested in total (rest never settle)   : "
          f"{len(set(data['fetchesAttempted']))} distinct")
    print(f"  live poll sources declared in map.js                        : "
          f"{len(data['constants']['liveSources'])} "
          f"({', '.join(data['constants']['liveSources'])})")
    check(len(data["loaded"]) == 11, "the whole frontend no longer loads into the vm harness")

    # -----------------------------------------------------------------
    rule("CLAIM (a): updates cannot be paused")
    timers_load = data["timersAtLoad"]
    timers_after = data["timersAfterSelect"]
    print("  timers the page starts on its own, measured (not read from source):")
    for t in timers_load:
        print(f"      {t['kind']:8} every {t['delay']:>6} ms   {t['label']}")
    print(f"      animation frame loop requested at load: {data['rafAtLoad']} "
          f"(animateTrains, throttled to {data['constants']['TRAIN_TICK_MS']} ms)")
    print(f"  station panel state: hidden as the markup declares at load "
          f"({not data['panelOpenAtLoad']}), opened by the page's own "
          f"#stations-toggle ({data['panelOpenAfterToggle']})")
    print("  after that rider then selects a station in the panel:")
    for t in timers_after:
        print(f"      {t['kind']:8} every {t['delay']:>6} ms   {t['label']}")

    poll = data["constants"]["POLL_INTERVAL_MS"]
    alert_poll = data["constants"]["ALERT_POLL_INTERVAL_MS"]
    tick = data["constants"]["TRAIN_TICK_MS"]
    one_second_timers = [t for t in timers_after if t["delay"] == 1000]
    print("  one minute of a rider standing still, from those measured cadences:")
    print(f"      map repolls          : {60000 // poll} "
          f"(every {poll} ms, {len(data['constants']['liveSources'])} endpoints each)")
    print(f"      alert repolls        : {60000 // alert_poll} (every {alert_poll} ms)")
    print(f"      countdown repaints   : {60 * len(one_second_timers)} "
          f"({len(one_second_timers)} one-second tickers running at once)")
    print(f"      animation frames     : up to {60000 // tick} (throttle {tick} ms)")

    print(f"  interactive controls declared in frontend/index.html: {len(controls)}")
    for c in controls:
        label = c["aria"] or c["text"]
        print(f"      {c['tag']:6} {('#' + c['id']) if c['id'] else '(no id)':<18} "
              f"{c['type']:<9} {label}")
    pause_controls = [c for c in controls
                      if PAUSE_WORDS.search(" ".join([c["id"], c["aria"], c["text"]]))]
    print(f"  of those, controls with pause/stop/resume semantics : {len(pause_controls)}")

    probes = data["pauseProbe"]["probes"]
    errored = [p for p in probes if p["error"]]
    cleared = [p for p in probes if p["cleared"]]
    print(f"  rider actions driven through the real handlers      : {len(probes)} "
          f"({len(errored)} raised an error)")
    print("  actions that stopped any running timer:")
    if not cleared:
        print("      none")
    for p in cleared:
        for t in p["cleared"]:
            print(f"      {p['label']:<34} stopped: {t['kind']} {t['delay']} ms  {t['label']}")
    survivors = {t["delay"] for t in data["pauseProbe"]["after"]}
    print(f"  timers still running after all {len(probes)} actions      : "
          f"{sorted(survivors)} ms")
    layer_probes = [p for p in probes if p["label"].startswith("toggle-")]
    print(f"  of those, layer checkbox actions                    : {len(layer_probes)}, "
          f"which stopped {sum(len(p['cleared']) for p in layer_probes)} timers "
          f"(they hide markers, not countdowns, exactly as the statement says)")
    print("  reading: the one stop is the station panel closing, which removes the")
    print("  surface the countdown was on. The 15 s poll and the 60 s alert poll")
    print("  survive every control the page owns, so nothing here is a pause.")
    print("  honest caveat: the second one-second ticker belongs to the open station")
    print("  POPUP, and it survives Escape here only because the Leaflet stub keeps no")
    print("  map-level popup registry. In a browser Escape closes that popup and its")
    print("  ticker with it, which is again closing a surface, not pausing updates.")

    doc_pause = "Auto-updating content cannot be paused" in access
    doc_222 = "2.2.2 Pause, Stop" in access
    doc_88 = "issue #88" in access and "issues/88" in access
    print(f"  ACCESSIBILITY.md states the gap                     : {doc_pause}")
    print(f"  ACCESSIBILITY.md names WCAG 2.2.2                   : {doc_222}")
    print(f"  ACCESSIBILITY.md files it as issue #88              : {doc_88}")
    print(f"  issue #88 at verification time (recorded, not re-queried): "
          f"state={ISSUE_88['state']}, opened {ISSUE_88['created_at']}, "
          f"open issues in repo={ISSUE_88['open_issues_in_repo']}")

    pause_api = source_hits(r"\b(function|const|let)\s+(pause|unpause|resume|togglePause|"
                            r"freezeUpdates|stopUpdates)\b", FRONTEND)
    print(f"  production functions offering a pause/resume API    : {len(pause_api)}")

    check(poll == 15000 and alert_poll == 60000,
          f"poll cadence changed: {poll} ms map, {alert_poll} ms alerts", "a")
    check(len(one_second_timers) == 2,
          f"expected 2 one-second countdown tickers with a station open, saw {len(one_second_timers)}", "a")
    check(not pause_controls, "a pause/stop/resume control now exists in index.html", "a")
    check(not pause_api, f"a pause/resume API now exists in the frontend: {pause_api}", "a")
    check(15000 in survivors and 60000 in survivors,
          "a control now stops the poll loop: F14 claim (a) may be fixed", "a")
    check(data["panelOpenAtLoad"] is False and data["panelOpenAfterToggle"] is True,
          "the station panel no longer starts hidden and open by its own toggle", "a")
    check(sum(len(p["cleared"]) for p in layer_probes) == 0,
          "a layer checkbox now stops a timer, which the statement says it does not", "a")
    check(doc_pause and doc_222 and doc_88,
          "ACCESSIBILITY.md no longer documents the pause gap the audit quoted", "a")

    # -----------------------------------------------------------------
    rule("CLAIM (b): arbitrary pointer panning relies on dragging")
    pan_controls = [c for c in controls
                    if PAN_WORDS.search(" ".join([c["id"], c["aria"], c["text"]]))]
    print(f"  controls in index.html with any pan/move semantics  : {len(pan_controls)}")
    moves = source_hits(r"map\.(panTo|panBy|setView|flyTo|fitBounds)\s*\(", FRONTEND)
    print(f"  map-movement call sites in production code          : {len(moves)}")
    for hit in moves:
        print(f"      {hit}")
    custom_controls = source_hits(r"L\.control\b|L\.Control\b", FRONTEND)
    print(f"  custom Leaflet controls the app adds                : {len(custom_controls)}")
    print(f"  map movements caused by picking a station in the panel: "
          f"{len(data['movesFromStationSelect'])} "
          f"({', '.join(m['how'] for m in data['movesFromStationSelect']) or 'none'})")
    print("  that is the documented PARTIAL single-pointer path: it reaches registered")
    print("  stations, not an arbitrary area, so 2.5.7 stays unmet.")
    print("  Leaflet's own default zoom control is present (the app never disables it),")
    print("  and it is pointer-operable, but it zooms rather than pans: no button moves")
    print("  the view sideways, so reaching an arbitrary area still needs a drag.")
    doc_257 = "2.5.7 Dragging Movements is not met" in access
    doc_drag = "Panning the map is drag-only" in access
    print(f"  ACCESSIBILITY.md states the gap and names 2.5.7     : {doc_drag and doc_257}")
    check(not pan_controls, "a pan control now exists in index.html", "b")
    check(not custom_controls, f"the app now adds custom Leaflet controls: {custom_controls}", "b")
    check(len(data["movesFromStationSelect"]) >= 1,
          "selecting a station no longer pans the map (the partial pointer path is gone)", "b")
    check(doc_drag and doc_257, "ACCESSIBILITY.md no longer documents the dragging gap", "b")

    # -----------------------------------------------------------------
    rule("CLAIM (c): buses have no station-panel equivalent")
    registrars = sorted({hit.split(":")[0] for hit in source_hits(r"registerStation\(\{", FRONTEND)})
    print(f"  system files that register stations                 : {len(registrars)}")
    for rel in registrars:
        print(f"      {rel}")
    print(f"  buses.js among them                                 : "
          f"{'frontend/systems/buses.js' in registrars}")
    print(f"  buses in the committed fixture applied through the real poll path: "
          f"{len(data['busFixture'])} "
          f"({', '.join(b['route_id'] for b in data['busFixture'])})")
    print(f"  markers built, by factory                           : {data['markerFactoryCounts']}")
    print(f"  station registry contents after that poll           : {len(data['registry'])} entries")
    for entry in data["registry"]:
        print(f"      {entry['key']:<16} {entry['kind']:<8} {entry['name']}")
    bus_entries = [e for e in data["registry"] if e["kind"] == "bus"]
    print(f"  registry entries contributed by buses               : {len(bus_entries)}")
    print("  the real panel search (renderStationResults), by query:")
    for q, res in data["panelSearch"].items():
        print(f"      {q!r:<16} rows={res['rows']}  status={res['status']!r}")
    print("  each bus marker, as the real labeledMarker built it:")
    for m in data["busMarkers"]:
        print(f"      L.marker option keyboard={m.get('keyboardOption')!r}  "
              f"role={m['role']!r}  aria-label={m['ariaLabel']!r}")
        print(f"      tabindex written by app code: {m.get('appWroteTabindex')} "
              f"(Leaflet gates the tab stop AND role=button on that one option, which is "
              f"why the factory writes role=img back by hand)")
        print(f"      popup   : {m['popupHtml']}")
        print(f"      actions : {', '.join(m['popupEvents'])} "
              f"(popupopen draws the route line, popupclose clears it)")
    interactive_in_popup = [m for m in data["busMarkers"]
                            if re.search(r"<button|<a\s|tabindex|popup-crosslink",
                                         m["popupHtml"] or "")]
    print(f"  bus popups containing any focusable element or cross-link: "
          f"{len(interactive_in_popup)} of {len(data['busMarkers'])}")
    openers = source_hits(r"\.openPopup\(\)", FRONTEND)
    print(f"  programmatic openPopup call sites in production code: {len(openers)}")
    for hit in openers:
        print(f"      {hit}  (subject is a station-registry entry)")
    doc_bus = "Buses are not in the station panel" in access
    doc_markers = "Vehicle markers are deliberately outside the tab order" in access
    print(f"  ACCESSIBILITY.md states both halves                 : {doc_bus and doc_markers}")
    print("  so: a bus popup, its route alerts and its route line are reachable only by")
    print("  pointing at a marker that is not in the tab order and not in the registry")
    print("  that the panel and the two openPopup call sites search. The removal from")
    print("  Tab order is the deliberate fleet-scale choice the audit credits, and the")
    print("  markers keep role=img plus an accessible name for touch screen readers.")

    check(len(registrars) == 6 and "frontend/systems/buses.js" not in registrars,
          f"the set of station-registering systems changed: {registrars}", "c")
    check(len(data["busMarkers"]) == len(data["busFixture"]),
          "the bus fixture no longer produces one marker per vehicle", "c")
    check(bool(data["busMarkers"]) and all(m.get("keyboardOption") is False
                                           for m in data["busMarkers"]),
          "bus markers are no longer built with keyboard:false, so they are back in the "
          "tab order: F14 claim (c) has changed", "c")
    check(all(m.get("appWroteTabindex") is False for m in data["busMarkers"]),
          "app code now writes a tabindex onto a bus marker", "c")
    check(all(m["role"] == "img" and m["ariaLabel"] for m in data["busMarkers"]),
          "bus markers lost the role/name the tab-order exception promises", "c")
    check(not bus_entries, "buses now register station-panel entries", "c")
    check(data["panelSearch"]["Times"]["rows"] == 1,
          "the panel search control case broke: a known station no longer matches", "c")
    check(all(data["panelSearch"][q]["rows"] == 0 for q in ("M15", "B46", "MTA NYCT_101", "bus")),
          "a bus route or vehicle is now findable in the station panel", "c")
    check(not interactive_in_popup, "a bus popup now carries a focusable element", "c")
    check(len(openers) == 2, f"the programmatic openPopup call sites changed: {openers}", "c")
    check(doc_bus and doc_markers, "ACCESSIBILITY.md no longer documents the bus gap", "c")

    # -----------------------------------------------------------------
    rule("CLAIM (d): no manual assistive-technology testing (documentation check)")
    at_sentence = "**No assistive technology has been used on this page.**" in access
    named = [name for name in ("NVDA", "JAWS", "VoiceOver", "Narrator", "Dragon",
                               "switch access", "screen magnifiers") if name in access]
    no_rider = "no disabled rider has tested it" in access
    one_engine = "One browser engine." in access
    print(f"  ACCESSIBILITY.md carries the statement of absence   : {at_sentence}")
    print(f"  assistive technologies it names as untried          : {len(named)} "
          f"({', '.join(named)})")
    print(f"  it also states no disabled rider has tested it      : {no_rider}")
    print(f"  it also states one browser engine only (Chromium)   : {one_engine}")

    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", os.path.join("docs", "reviews")}
    at_rx = re.compile(r"NVDA|JAWS|VoiceOver|Narrator|TalkBack|Dragon naturally|screen magnifier", re.I)
    mentions: dict[str, int] = {}
    for base, dirs, files in os.walk(REPO):
        rel_base = os.path.relpath(base, REPO)
        if any(part in skip_dirs for part in rel_base.split(os.sep)) or rel_base.startswith("docs/reviews"):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            if not name.endswith((".md", ".js", ".py", ".html", ".css", ".yml", ".yaml", ".json")):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
            except (OSError, UnicodeDecodeError):
                continue
            hits = len(at_rx.findall(text))
            if hits:
                mentions[os.path.relpath(path, REPO)] = hits
    print(f"  files anywhere in the repository naming a screen reader (audit records excluded): "
          f"{len(mentions)}")
    for rel, count in sorted(mentions.items()):
        print(f"      {rel}: {count} mention(s)")
    print("  every one of those mentions is the statement that it has NOT been done;")
    print("  no test, spec, workflow or changelog records a manual session.")
    check(at_sentence and no_rider and len(named) >= 6,
          "ACCESSIBILITY.md no longer states that no assistive technology was used", "d")
    check(set(mentions) == {"ACCESSIBILITY.md"},
          f"screen readers are now named outside the statement of absence: {sorted(mentions)}", "d")

    # -----------------------------------------------------------------
    rule("FAIRNESS: what the page DOES provide accessibly (the audit credits this)")
    first_control = controls[0] if controls else {}
    aria_live = re.findall(r"<[^>!]*aria-live=", html)
    role_status = re.findall(r"<[^>!]*role=\"status\"", html)
    print(f"  first interactive element in the markup             : "
          f"#{first_control.get('id')} ({first_control.get('text')})")
    print(f"  live regions declared in index.html                 : "
          f"{len(aria_live) + len(role_status)} "
          f"({len(aria_live)} aria-live plus {len(role_status)} role=status)")
    print(f"  systems whose stations are text in the panel        : {len(registrars)} of 7 "
          f"(buses the exception)")
    print(f"  station rows the panel found for a real query       : "
          f"{data['panelSearch']['Times']['rows']} for 'Times'")
    print(f"  vehicle markers carrying role and accessible name   : "
          f"{sum(1 for m in data['busMarkers'] if m['role'] and m['ariaLabel'])} of "
          f"{len(data['busMarkers'])} buses in this fixture")
    print(f"  document-level key handling bound by the app        : "
          f"{', '.join(data['documentListeners'])} (the Escape ladder)")
    shared = read("frontend/systems/shared.js")
    map_call = shared[shared.index("L.map("):shared.index(".setView(", shared.index("L.map("))]
    print(f"  map container keeps Leaflet's own keyboard panning  : "
          f"{'keyboard' not in map_call} (the app passes no keyboard option to L.map, "
          f"so Leaflet's arrow-key panning stays on)")
    print("  and ACCESSIBILITY.md states every claim with the test id that proves it.")

    # -----------------------------------------------------------------
    rule("RESULT")
    claims = [
        ("a", "updates cannot be paused"),
        ("b", "arbitrary pointer panning relies on dragging"),
        ("c", "buses have no station-panel equivalent"),
        ("d", "no manual assistive-technology testing (documented)"),
    ]
    for letter, label in claims:
        broken = [m for (c, m) in failures if c == letter]
        state = "reproduced" if not broken else f"CHANGED ({len(broken)} check(s) failed)"
        print(f"  claim {letter}  {label}: {state}")
    if failures:
        print()
        print(f"  {len(failures)} check(s) no longer hold:")
        for claim, message in failures:
            print(f"      FAIL [claim {claim}]: {message}")
        print()
        print("DISPOSITION: CHANGED  The recorded verdict was VERIFIED; the code or the "
              "statement has moved since, so re-read the failures above before trusting "
              "the audit record.")
        return 1

    print()
    print("DISPOSITION: VERIFIED  All four documented limitations still hold: no pause "
          "control exists (28 rider actions never stopped the 15 s or 60 s polls), no pan "
          "control exists, buses add 0 station-panel entries and their 2 markers are built "
          "keyboard:false, and no manual assistive-technology testing is recorded anywhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
