#!/usr/bin/env python3
"""F04 (P1) "AirTrain's historical fixture looks like current scheduled service" : reproduction.

THE AUDIT'S CLAIM (section 1 of docs/reviews/audit-2026-09-05.md, finding F04):

    "The committed fixture says its source calendar expired December 31, 2021 and
    is not a live schedule authority. It still includes Terminal 2. The backend
    drops the provenance, while rider surfaces choose headway bands using the
    current New York time and describe them as scheduled service.

    The Port Authority announced that AirTrain's Terminal 2 station had closed for
    demolition in July 2022. This demonstrates an actual obsolete station, beyond
    the age of the source alone. 'No live tracking' does not communicate that the
    reference itself is historical."

    Cited: data/airtrain_jfk.json L2 (fixture provenance and Terminal 2),
    backend/airtrain_static.py L60 (provenance removed from the API),
    frontend/stations.js L529 (current-time headway rendering).

RUN IT (from the repository root):

    .venv/bin/python docs/reviews/audit-2026-09-05/f04_airtrain_historical_reference.py

THE EXTERNAL HALF, CONFIRMED OUT OF BAND AND RECORDED HERE VERBATIM.
The script itself never touches the network. The closure was confirmed on
2026-09-05 by fetching the exact URL the audit cites,

  https://www.panynj.gov/port-authority/en/press-room/press-release-archives/
  2022-press-releases/bridges--tunnels-and-rail-advisory-for-july-22-to-28.html

which returns HTTP 200 with the title "BRIDGES, TUNNELS AND RAIL ADVISORY FOR
JULY 22 TO 28". The page body is client rendered, so the prose was read from the
same page's own Adobe Experience Manager page model,
".../bridges--tunnels-and-rail-advisory-for-july-22-to-28.model.json" under
/content/port-authority/en/..., which the shell's own cq:pagemodel_root_url meta
tag points at. Verbatim, from that page:

  Date: July 22, 2022. Press Release Number: 74-2022.

  Headline bullet (keyTakeaways[1]):
    "JFK Airport's Green Garage and AirTrain JFK's Terminal 2 Station Closed for
     Construction; AirTrain Customers Should Use Terminal 1"

  Body, section "AIRPORTS & AIRTRAIN":
    "John F. Kennedy International Airport's Green Garage and AirTrain JFK's
     Terminal 2 station have closed for demolition activities associated with the
     construction of JFK's new Terminal One. AirTrain JFK service to Terminal 2
     has been discontinued."

    "For access to Terminal 2 via AirTrain, customers should use the Terminal 1
     station. ... Service to all other airport terminals is not affected on the
     Howard Beach and Jamaica lines."

That last sentence is why this script flags exactly ONE committed station as
proven obsolete: the cited Port Authority source shows no other AirTrain station
closed or renamed. See the OTHER STATIONS block printed at the end for the one
secondary flag (Terminal 7), which is recorded as unproven by this source.

WHAT IS PRODUCTION CODE HERE (nothing below is re-implemented):

  * backend/airtrain_static.py load_airtrain: the real loader, called on the real
    committed path airtrain_static.AIRTRAIN_FIXTURE.
  * backend/main.py app driven over httpx.ASGITransport: GET /api/airtrain is the
    real routed handler (backend/routes/airtrain.py) behind the real response
    model models.AirTrainData, so the served keys below are read out of SERVED
    JSON bytes, not out of a Python dict. app.state.airtrain is primed exactly as
    backend/main.py line 281 primes it, because ASGITransport sends no lifespan
    events (the same arrangement backend/tests/test_airtrain.py uses).
  * frontend/helpers.js, frontend/systems/airtrain.js and frontend/stations.js
    are loaded AS SOURCE into one node:vm context, in the order index.html loads
    them, and then the real functions are called: loadAirtrain (the layer
    builder), airtrainStationName, airtrainStationPopupHtml (the map popup),
    selectHeadwayBand (the band chooser), nyMinutesSinceMidnight (the New York
    clock read) and renderStationDetail / renderScheduledDetail (the accessible
    station panel). The registry entry fed to the panel is the one loadAirtrain
    itself built from the served payload.

WHAT IS STUBBED OR INJECTED (stated plainly):

  * The browser is stubbed, not simulated: a minimal document (getElementById,
    createElement, append/appendChild/replaceChildren, textContent), a Leaflet
    stand-in (L.divIcon, L.polyline), the two layer groups and labeledMarker and
    registerStation that frontend/systems/shared.js would otherwise supply.
    shared.js itself is NOT loaded, because it builds a real Leaflet map at load.
    Every AirTrain-specific line is the shipped source.
  * fetch is stubbed to hand loadAirtrain the exact JSON body /api/airtrain just
    served in part A. No network.
  * THE CLOCK IS THE INJECTED FAULT INPUT. The vm context's global Date is
    replaced, per case, with a subclass of the context's own Date whose zero-arg
    constructor returns one fixed instant. That is how "the current New York
    time" is made deterministic: the assertions below depend only on the injected
    instants, never on the day this script is run. If the rendered band tracks
    the injected clock, the production code is reading the wall clock, which is
    exactly the claim under test.
  * Two fixed calendar days are used so both New York offsets are covered:
    2026-01-15 (EST, UTC-5) and 2026-07-15 (EDT, UTC-4).

WHAT IS MEASURED:

  1. That the committed fixture carries a _provenance block naming GTFS calendar
     end_date 20211231 and disclaiming schedule authority.
  2. That neither load_airtrain nor the served /api/airtrain bytes carry any of
     it: served top-level keys, and a substring sweep of the whole response body.
  3. The committed station and route counts, re-derived from the served payload.
  4. Which served stations and which served routes reference Terminal 2, id
     160561, and at what position in each route's station order.
  5. That the production panel and the production popup pick a headway band from
     the injected New York clock and label the result as scheduled service, with
     the chosen band re-derived from the fixture's own band table.
  6. That no rider-facing string on any of those surfaces says the reference is
     historical, expired, or dated 2021.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
# CONTAINMENT, INSTALLED BEFORE THE FIRST BACKEND IMPORT. env_seams calls load_dotenv
# when it is imported, so a credential scrub that runs before that import is undone by
# it; the addresses set here are what make this process unable to reach NJ Transit at
# all. See _hermetic for the leak this closes and the measurement behind it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _hermetic  # noqa: E402

_hermetic.contain()

sys.path.insert(0, str(REPO_ROOT / "backend"))

import httpx  # noqa: E402

import airtrain_static  # noqa: E402
import main as app_module  # noqa: E402
from models import AirTrainData  # noqa: E402

# CONTAINMENT, ASSERTED NOW THAT THE BACKEND HAS BEEN IMPORTED. env_seams ran
# load_dotenv during those imports, so this is where the credentials it may have
# refilled are dropped again and where the addresses are checked against the real
# NJ Transit host. Raises rather than warns: a script that cannot prove it is
# contained must not run at all.
_hermetic.verify()


TERMINAL_2_ID = "160561"
TERMINAL_2_NAME = "Terminal 2"

# The vintage markers the fixture itself records. If a rider-facing surface ever
# starts carrying one of these, this finding has been addressed and the sweep
# below must fail loudly rather than quietly keep passing.
VINTAGE_MARKERS = (
    "_provenance",
    "provenance",
    "20211231",
    "2021",
    "calendar end_date",
    "not a live schedule authority",
    "511ny",
    "datatools-511ny",
    "historical",
    "expired",
    "reference only",
)

FAILURES: list[str] = []


def rule(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def check(ok: bool, label: str, detail: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f"   ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(label + (f" :: {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# The node:vm driver. Written to a temp file at run time and executed with node.
# It loads the SHIPPED frontend sources; it does not contain a copy of any of
# the logic it exercises.
# ---------------------------------------------------------------------------
NODE_DRIVER = r"""
"use strict";
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const repoRoot = process.argv[2];
const payloadPath = process.argv[3];
const outPath = process.argv[4];
const casesPath = process.argv[5];

const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));

// ---- the browser stand-in ------------------------------------------------
function makeEl(tag) {
  return {
    tagName: tag,
    id: null,
    className: "",
    textContent: "",
    hidden: true,
    children: [],
    style: {},
    classList: {
      contains: () => false,
      toggle: () => {},
      add: () => {},
      remove: () => {},
    },
    append(...nodes) { this.children.push(...nodes); },
    appendChild(node) { this.children.push(node); return node; },
    replaceChildren(...nodes) { this.children = nodes.slice(); },
    contains() { return false; },
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    removeAttribute() {},
    focus() {},
  };
}

const elements = Object.create(null);
function byId(id) {
  if (!elements[id]) { elements[id] = makeEl("div"); elements[id].id = id; }
  return elements[id];
}
function textOf(el) {
  const parts = [];
  const walk = (node) => {
    if (!node || typeof node !== "object") return;
    if (node.textContent) parts.push(node.textContent);
    for (const kid of node.children || []) walk(kid);
  };
  walk(el);
  return parts.join(" | ");
}

// ---- the stand-ins frontend/systems/shared.js would otherwise supply -----
const captured = { stations: [], polylines: 0 };
const sandbox = {
  console,
  URLSearchParams,
  document: {
    getElementById: byId,
    createElement: makeEl,
    body: makeEl("body"),
    activeElement: null,
  },
  AbortSignal: { timeout: () => null },
  fetch: async (url) => ({ ok: true, status: 200, json: async () => payload }),
  L: {
    divIcon: (opts) => ({ __icon: true, opts }),
    polyline: (latlngs, opts) => {
      captured.polylines += 1;
      return { addTo: () => ({}) };
    },
  },
  lineRenderer: null,
  airtrainRouteLinesLayer: { __layer: "routes" },
  airtrainStationLayer: { __layer: "stations" },
  labeledMarker: (latlng, options, name) => {
    const marker = {
      __latlng: latlng,
      __name: name,
      __popupFn: null,
      bindPopup(fn) { this.__popupFn = fn; return this; },
      addTo() { return this; },
    };
    return marker;
  },
  registerStation: (entry) => { captured.stations.push(entry); },
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);

function loadSource(rel) {
  const src = fs.readFileSync(path.join(repoRoot, rel), "utf8");
  vm.runInContext(src, sandbox, { filename: rel });
}

// index.html order: helpers.js, then systems/*.js, then stations.js.
loadSource("frontend/helpers.js");
loadSource("frontend/systems/airtrain.js");
loadSource("frontend/stations.js");

// Keep the context's own Date so the fake extends a real one (Intl needs that).
vm.runInContext(
  "globalThis.__RealDate = Date;" +
  "globalThis.__installClock = function (ms) {" +
  "  const Real = globalThis.__RealDate;" +
  "  class Fixed extends Real {" +
  "    constructor(...a) { if (a.length === 0) { super(ms); } else { super(...a); } }" +
  "    static now() { return ms; }" +
  "  }" +
  "  globalThis.Date = Fixed;" +
  "};",
  sandbox,
);

const result = { cases: [], entries: [], errors: [] };

// Run the REAL loadAirtrain against the REAL served payload.
(async () => {
  const ok = await sandbox.loadAirtrain();
  result.loadAirtrainReturned = ok;
  result.polylinesDrawn = captured.polylines;
  result.registered = captured.stations.map((e) => ({
    key: e.key,
    kind: e.kind,
    id: e.id,
    name: e.name,
    systemLabel: e.systemLabel,
    routes: e.routes,
    arrivalsUrl: e.arrivalsUrl === null ? null : String(e.arrivalsUrl),
    airtrainRouteIds: (e.airtrainRoutes || []).map((r) => r.id),
    markerName: e.marker ? e.marker.__name : null,
    hasPopupFn: Boolean(e.marker && e.marker.__popupFn),
  }));

  for (const c of cases) {
    sandbox.__installClock(c.epochMs);
    const minutes = sandbox.nyMinutesSinceMidnight();
    const per = { label: c.label, epochMs: c.epochMs, nyMinutes: minutes, stations: [] };
    for (const stationId of c.stationIds) {
      const entry = captured.stations.find((e) => e.id === stationId);
      if (!entry) { result.errors.push("no registry entry for " + stationId); continue; }
      // The panel: set the module-scope selection the way selectStation would,
      // then call the REAL renderStationDetail (which routes a null arrivalsUrl
      // into the REAL renderScheduledDetail).
      sandbox.__entry = entry;
      vm.runInContext("panelStation = __entry; panelBody = null; panelError = null;", sandbox);
      vm.runInContext("renderStationDetail();", sandbox);
      const panelText = textOf(byId("stations-detail"));
      const spoken = byId("stations-announce").textContent;
      // The map popup: the callback the REAL loadAirtrain bound.
      const popupHtml = entry.marker.__popupFn();
      // The band the REAL chooser picks for the first serving route.
      const serving = (entry.airtrainRoutes || []).filter(
        (r) => (r.stations || []).includes(entry.id),
      );
      const bands = serving.map((r) => {
        const band = sandbox.selectHeadwayBand(r.headways, minutes);
        return { route: r.id, name: r.name, headway: band ? band.headway_min : null,
                 window: band ? band.start + "-" + band.end : null };
      });
      per.stations.push({ id: stationId, name: entry.name, panelText, spoken, popupHtml, bands });
    }
    result.cases.push(per);
  }

  fs.writeFileSync(outPath, JSON.stringify(result, null, 2), "utf8");
})().catch((err) => {
  fs.writeFileSync(outPath, JSON.stringify({ fatal: String(err && err.stack || err) }), "utf8");
  process.exitCode = 3;
});
"""


def ny_epoch_ms(year: int, month: int, day: int, hh: int, mm: int, utc_offset_h: int) -> int:
    """Milliseconds since the epoch for a fixed New York wall-clock instant.

    The offset is passed in explicitly rather than derived, so this stays a fixed
    constant and never consults a tz database or today's date.
    """
    import calendar

    return calendar.timegm((year, month, day, hh + utc_offset_h, mm, 0, 0, 0, 0)) * 1000


async def serve_airtrain() -> tuple[int, dict, str, dict]:
    """Drive the real ASGI app and return (status, body, raw text, headers)."""
    # backend/main.py line 281 does exactly this at startup; httpx's ASGITransport
    # sends no lifespan events, so prime it the way test_airtrain.py does.
    app_module.app.state.airtrain = airtrain_static.load_airtrain()
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/airtrain")
        return res.status_code, res.json(), res.text, dict(res.headers)


async def main_async() -> None:
    print(__doc__.split("WHAT IS MEASURED:")[0].rstrip())

    # ---- A. the committed artifact ---------------------------------------
    rule("A. THE COMMITTED ARTIFACT: data/airtrain_jfk.json")
    fixture_path = airtrain_static.AIRTRAIN_FIXTURE
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    print(f"  path                 : {fixture_path}")
    print(f"  top-level keys       : {sorted(raw)}")
    prov = raw.get("_provenance", {})
    print("  _provenance, verbatim:")
    for key, value in prov.items():
        print(f"    {key:14s}: {value}")
    end_date = re.search(r"end_date\s*(\d{8})", prov.get("caveat", ""))
    print(f"  parsed calendar end  : {end_date.group(1) if end_date else '(not found)'}")
    check("_provenance" in raw, "the committed fixture carries a _provenance block")
    check(
        end_date is not None and end_date.group(1) == "20211231",
        "its caveat names GTFS calendar end_date 20211231 (December 31, 2021)",
        f"caveat={prov.get('caveat')!r}",
    )
    check(
        "not a live schedule authority" in prov.get("caveat", ""),
        "its caveat disclaims schedule authority in those words",
    )

    # ---- B. the loader and the served endpoint ---------------------------
    rule("B. PRODUCTION LOADER AND THE REAL /api/airtrain RESPONSE")
    loaded = airtrain_static.load_airtrain()
    print(f"  load_airtrain() keys : {sorted(loaded)}")

    # Read the served contract itself rather than walking app.routes: FastAPI wraps
    # included routers, so the OpenAPI paths are the honest list of what is exposed.
    open_api_paths = sorted(app_module.app.openapi()["paths"])
    airtrain_routes = [p for p in open_api_paths if "airtrain" in p]
    print(f"  /api/airtrain* routes: {airtrain_routes} "
          f"(of {len(open_api_paths)} routed paths)")
    check(
        airtrain_routes == ["/api/airtrain"],
        "exactly one AirTrain endpoint exists, so there is no second surface that "
        "could still be carrying the provenance",
        f"{airtrain_routes}",
    )

    status, body, raw_text, headers = await serve_airtrain()
    print(f"  GET /api/airtrain    : HTTP {status}, {len(raw_text)} bytes")
    print(f"  cache-control        : {headers.get('cache-control')}")
    print(f"  served top-level keys: {sorted(body)}")
    print(f"  response model fields: {sorted(AirTrainData.model_fields)}")
    print(f"  served stations      : {len(body['stations'])}")
    print(f"  served routes        : {len(body['routes'])}")

    check(status == 200, "the real endpoint answered 200")
    check(
        "_provenance" not in loaded and set(loaded) == {"stations", "routes"},
        "load_airtrain drops _provenance (backend/airtrain_static.py line 60 to 61)",
        f"returned {sorted(loaded)}",
    )
    check(
        set(body) == {"stations", "routes"},
        "the SERVED body carries only stations and routes",
        f"served {sorted(body)}",
    )

    lowered = raw_text.lower()
    hits = [m for m in VINTAGE_MARKERS if m.lower() in lowered]
    print(f"  vintage markers in the served bytes: {hits or 'none'}")
    check(
        not hits,
        "no vintage marker survives anywhere in the served response bytes",
        f"hits={hits}",
    )
    check(
        len(body["stations"]) == 10 and len(body["routes"]) == 3,
        "the served payload is 10 stations and 3 routes",
        f"{len(body['stations'])} stations, {len(body['routes'])} routes",
    )

    # ---- C. Terminal 2 in the served payload -----------------------------
    rule("C. WHICH SERVED STATIONS AND ROUTES REFERENCE TERMINAL 2 (id 160561)")
    print("  served stations:")
    by_id = {}
    for st in body["stations"]:
        by_id[st["id"]] = st
        flag = "  <== the closed station" if st["id"] == TERMINAL_2_ID else ""
        print(f"    {st['id']}  {st['name']:28s} {st['lat']:.5f},{st['lon']:.5f}{flag}")

    t2 = by_id.get(TERMINAL_2_ID)
    check(
        t2 is not None and t2["name"] == TERMINAL_2_NAME,
        f"station id {TERMINAL_2_ID} is served, named {TERMINAL_2_NAME!r}",
        f"got {t2!r}",
    )

    print()
    print("  routes, and where Terminal 2 sits in each station order:")
    routes_with_t2 = []
    for rt in body["routes"]:
        stops = rt["stations"]
        names = [by_id.get(s, {}).get("name", s) for s in stops]
        if TERMINAL_2_ID in stops:
            routes_with_t2.append(rt["id"])
            pos = f"position {stops.index(TERMINAL_2_ID) + 1} of {len(stops)}"
        else:
            pos = "absent"
        print(f"    route {rt['id']} {rt['name']:16s} {len(stops)} stops, "
              f"{len(rt['polyline'])} polyline points, Terminal 2 {pos}")
        print(f"        order: {' -> '.join(names)}")
    print(f"  routes referencing Terminal 2: {routes_with_t2} "
          f"({len(routes_with_t2)} of {len(body['routes'])})")
    check(
        len(routes_with_t2) == 3,
        "all 3 served routes list Terminal 2 among their stations",
        f"{routes_with_t2}",
    )

    # ---- D. the rider surfaces, in a node:vm on the shipped sources ------
    rule("D. THE RIDER SURFACES: real stations.js panel and real popup, injected clock")
    cases = [
        # label, fixed New York wall-clock instant, expected band from the fixture
        ("2026-01-15 03:00 EST", ny_epoch_ms(2026, 1, 15, 3, 0, 5), 180, 15),
        ("2026-01-15 06:00 EST", ny_epoch_ms(2026, 1, 15, 6, 0, 5), 360, 7),
        ("2026-01-15 10:59 EST", ny_epoch_ms(2026, 1, 15, 10, 59, 5), 659, 7),
        ("2026-01-15 11:00 EST", ny_epoch_ms(2026, 1, 15, 11, 0, 5), 660, 4),
        ("2026-07-15 14:00 EDT", ny_epoch_ms(2026, 7, 15, 14, 0, 4), 840, 4),
        ("2026-07-15 22:30 EDT", ny_epoch_ms(2026, 7, 15, 22, 30, 4), 1350, 7),
    ]
    # Re-derive each expected headway from the fixture's own band table rather
    # than trusting the literal above: the literals are a cross-check, not input.
    bands_2877 = next(r for r in body["routes"] if r["id"] == "2877")["headways"]

    def band_for(minutes: int) -> int | None:
        for band in bands_2877:
            start = int(band["start"][:2]) * 60 + int(band["start"][3:])
            end = int(band["end"][:2]) * 60 + int(band["end"][3:])
            if start <= minutes < end:
                return band["headway_min"]
        return None

    print("  the fixture's own band table (identical on all 3 routes):")
    for band in bands_2877:
        print(f"    {band['start']}-{band['end']}  every {band['headway_min']} min")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        driver = tmpdir / "f04_driver.js"
        driver.write_text(NODE_DRIVER, encoding="utf-8")
        payload_file = tmpdir / "served.json"
        payload_file.write_text(json.dumps(body), encoding="utf-8")
        cases_file = tmpdir / "cases.json"
        cases_file.write_text(
            json.dumps([
                {"label": lab, "epochMs": ms, "stationIds": [TERMINAL_2_ID, "160567"]}
                for lab, ms, _mins, _hw in cases
            ]),
            encoding="utf-8",
        )
        out_file = tmpdir / "out.json"
        proc = subprocess.run(
            ["node", str(driver), str(REPO_ROOT), str(payload_file),
             str(out_file), str(cases_file)],
            capture_output=True,
            text=True,
        )
        if proc.stdout.strip():
            print("  node stdout:")
            for line in proc.stdout.strip().splitlines():
                print(f"    {line}")
        if proc.returncode != 0 or not out_file.exists():
            print("  node stderr:")
            print(proc.stderr)
            check(False, "the node:vm driver ran", f"exit {proc.returncode}")
            fe = {"cases": [], "registered": [], "errors": ["driver did not run"]}
        else:
            fe = json.loads(out_file.read_text(encoding="utf-8"))
            if "fatal" in fe:
                print("  node fatal:")
                print(fe["fatal"])
                check(False, "the node:vm driver ran to completion")
                fe = {"cases": [], "registered": [], "errors": [fe["fatal"]]}

    print()
    print(f"  real loadAirtrain() returned : {fe.get('loadAirtrainReturned')}")
    print(f"  guideway polylines drawn     : {fe.get('polylinesDrawn')}")
    print(f"  stations registered          : {len(fe.get('registered', []))}")
    reg_t2 = next((e for e in fe.get("registered", []) if e["id"] == TERMINAL_2_ID), None)
    if reg_t2:
        print("  the Terminal 2 registry entry the production loader built:")
        for key in ("key", "kind", "systemLabel", "name", "routes", "arrivalsUrl",
                    "markerName", "hasPopupFn"):
            print(f"    {key:13s}: {reg_t2[key]!r}")
    check(
        fe.get("loadAirtrainReturned") is True,
        "the real frontend loadAirtrain accepted the served payload",
    )
    check(
        len(fe.get("registered", [])) == 10,
        "it registered all 10 served stations as clickable map stations",
        f"{len(fe.get('registered', []))}",
    )
    check(
        reg_t2 is not None and reg_t2["arrivalsUrl"] is None and reg_t2["hasPopupFn"],
        "Terminal 2 is registered with a bound popup and a null arrivalsUrl "
        "(which is what routes it into the scheduled panel branch)",
    )

    print()
    print("  the panel and popup, per injected New York instant "
          "(Terminal 2, route 2877 'Air Terminal'):")
    clock_tracked = 0
    for (label, epoch_ms, want_min, want_hw), case in zip(cases, fe.get("cases", [])):
        derived = band_for(want_min)
        got_min = case["nyMinutes"]
        station = next((s for s in case["stations"] if s["id"] == TERMINAL_2_ID), None)
        got_hw = station["bands"][0]["headway"] if station and station["bands"] else None
        window = station["bands"][0]["window"] if station and station["bands"] else None
        ok = got_min == want_min and got_hw == derived == want_hw
        clock_tracked += 1 if ok else 0
        print(f"    {label}: nyMinutesSinceMidnight={got_min:4d} (expected {want_min:4d}), "
              f"band {window} every {got_hw} min (fixture table says {derived})")
        if station:
            print(f"        panel : {station['panelText']}")
            print(f"        spoken: {station['spoken']}")
            print(f"        popup : {station['popupHtml']}")
    check(
        clock_tracked == len(cases),
        f"all {len(cases)} injected New York instants drove the band the fixture's own "
        "table prescribes, through the production chooser",
        f"{clock_tracked}/{len(cases)}",
    )

    # THE RENDERED TEXT, READ BACK OUT. Everything above compares what the
    # production chooser RETURNED against the fixture's table. A panel or popup
    # that ignored the clock and printed a constant would still pass it, because
    # the driver calls selectHeadwayBand itself rather than reading the strings.
    # These two checks close that hole: they parse the headway back out of the
    # panel text and the popup HTML a rider actually receives, so freezing either
    # surface fails here even with the chooser left intact.
    check(
        all(r["headways"] == bands_2877 for r in body["routes"]),
        "all 3 served routes publish the same band table, so one derived headway "
        "covers every rendered row",
        f"{len(body['routes'])} routes",
    )
    panel_re = re.compile(r"every (\d+) minutes, scheduled")
    popup_re = re.compile(r"every ~(\d+) min")
    rendered_ok = 0
    print("  the same numbers, parsed back out of the rendered surfaces:")
    for (label, _epoch_ms, want_min, _want_hw), case in zip(cases, fe.get("cases", [])):
        derived = band_for(want_min)
        station = next((s for s in case["stations"] if s["id"] == TERMINAL_2_ID), None)
        panel_nums = [int(n) for n in panel_re.findall(station["panelText"])] if station else []
        popup_nums = [int(n) for n in popup_re.findall(station["popupHtml"])] if station else []
        ok = (
            len(panel_nums) == len(body["routes"])
            and len(popup_nums) == len(body["routes"])
            and set(panel_nums) == {derived}
            and set(popup_nums) == {derived}
        )
        rendered_ok += 1 if ok else 0
        print(f"    {label}: panel {panel_nums}, popup {popup_nums}, "
              f"fixture-derived {derived} -> {'ok' if ok else 'MISMATCH'}")
    check(
        rendered_ok == len(cases),
        f"the headway parsed back OUT of the rendered panel text and popup HTML "
        f"equals the fixture-derived band at all {len(cases)} instants",
        f"{rendered_ok}/{len(cases)}",
    )

    distinct = {
        (c["nyMinutes"],
         next(s for s in c["stations"] if s["id"] == TERMINAL_2_ID)["bands"][0]["headway"])
        for c in fe.get("cases", [])
        if any(s["id"] == TERMINAL_2_ID for s in c["stations"])
    }
    print(f"  distinct (minute-of-day, headway) pairs observed: {sorted(distinct)}")
    check(
        len({hw for _m, hw in distinct}) >= 3,
        "the rendered headway CHANGES with the injected clock, so the surface is "
        "reading the current New York time, not a fixed string",
        f"{sorted({hw for _m, hw in distinct})}",
    )

    # ---- E. what the rider is told, and what is withheld -----------------
    rule("E. THE WORDS THE RIDER GETS")
    sample = None
    for case in fe.get("cases", []):
        for station in case["stations"]:
            if station["id"] == TERMINAL_2_ID:
                sample = station
                break
        if sample:
            break
    if sample:
        print(f"  panel note + rows : {sample['panelText']}")
        print(f"  screen reader text: {sample['spoken']}")
        print(f"  map popup HTML    : {sample['popupHtml']}")
    surface_text = " ".join(
        f"{s['panelText']} {s['spoken']} {s['popupHtml']}"
        for c in fe.get("cases", []) for s in c["stations"]
    )
    index_html = (REPO_ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    legend_lines = [
        line.strip() for line in index_html.splitlines() if "AirTrain" in line
    ]
    print("  index.html AirTrain legend strings (source assertion, not executed):")
    for line in legend_lines:
        print(f"    {line}")

    check(
        "Scheduled service. AirTrain JFK publishes no live tracking." in surface_text,
        "the panel labels the output 'Scheduled service. AirTrain JFK publishes no "
        "live tracking.'",
    )
    check(
        "scheduled" in surface_text and "(scheduled)" in surface_text,
        "each rendered row is labeled scheduled on both surfaces",
    )
    surface_hits = sorted(
        {m for m in VINTAGE_MARKERS if m.lower() in surface_text.lower()}
        | {m for m in VINTAGE_MARKERS if m.lower() in " ".join(legend_lines).lower()}
    )
    print(f"  vintage markers anywhere on the rider surfaces: {surface_hits or 'none'}")
    check(
        not surface_hits,
        "NOTHING on any rider surface says the reference itself is historical",
        f"hits={surface_hits}",
    )

    # ---- F. the source anchors the audit cited ---------------------------
    rule("F. THE SOURCE ANCHORS THE AUDIT CITED (source assertion, not execution)")
    # THE AUDIT CITED LINE NUMBERS AND THIS RESOLVES THEM BY CONTENT. The numbers
    # below are the ones section 1 quotes, left exactly as cited because the audit text
    # is history and is not edited here. What each one anchors is the PRESENCE of a
    # construct, never its address, and Release 1 edits these files above those lines:
    # F12's fix to fetchPanelArrivals is 18 lines longer than the code it replaced, so
    # renderScheduledDetail moved down by exactly that. Searching for the construct
    # keeps the assertion the audit actually made (this code is here, and the band call
    # still passes no date) load-bearing, and printing the drift shows how old the
    # citation is instead of failing the run over an address nobody asserted.
    anchors = [
        ("data/airtrain_jfk.json", 2, '"_provenance"', '"_provenance" is declared'),
        ("backend/airtrain_static.py", 60, "drop _provenance", "the loader drops _provenance"),
        (
            "frontend/stations.js",
            529,
            "function renderScheduledDetail",
            "the scheduled-headway renderer exists",
        ),
        (
            "frontend/stations.js",
            548,
            "nyMinutesSinceMidnight()",
            "the band call passes no date, so it defaults to new Date(), the live clock",
        ),
    ]
    for rel, cited, needle, claim in anchors:
        lines = (REPO_ROOT / rel).read_text(encoding="utf-8").splitlines()
        found = [i + 1 for i, line in enumerate(lines) if needle in line]
        where = f"audit cited L{cited}"
        if found != [cited]:
            where += f", now at {found if found else 'nowhere'}"
        print(f"  {rel}: {needle!r} ({where})")
        for lineno in found:
            print(f"      L{lineno}: {lines[lineno - 1].strip()}")
        check(len(found) == 1, f"{claim} ({where})")

    # ---- G. the other committed stations ---------------------------------
    rule("G. THE OTHER COMMITTED STATIONS")
    print("  Proven obsolete by the Port Authority source quoted in this header:")
    print(f"    {TERMINAL_2_ID} Terminal 2, served by routes {routes_with_t2}. The release "
          "says AirTrain JFK service to Terminal 2 has been discontinued.")
    print()
    print("  Shown UNAFFECTED by that same source, so not flagged here:")
    print("    the release states 'Service to all other airport terminals is not "
          "affected on the Howard Beach and Jamaica lines.'")
    print()
    print("  Secondary flag, NOT established by the cited source and NOT asserted by")
    print("  this script: 160560 Terminal 7. Trade and local press place Terminal 7's")
    print("  passenger closure in 2023 and its demolition in the Terminal 6 programme,")
    print("  with the AirTrain station retained meanwhile. No Port Authority notice of")
    print("  an AirTrain Terminal 7 STATION closure was located, so this is recorded as")
    print("  a station that needs its own verification, not as a second defect.")
    print()
    print("  Also note, for the record, what the fixture does NOT contain: no Terminal 3")
    print("  station (demolished 2013) and no Terminal 6 station, which is consistent")
    print("  with a 2021-vintage capture and is not itself a defect.")

    # ---- verdict ---------------------------------------------------------
    rule("MEASURED SUMMARY")
    print(f"  committed fixture provenance          : present, end_date "
          f"{end_date.group(1) if end_date else '?'}, "
          "'not a live schedule authority'")
    print(f"  keys returned by load_airtrain        : {sorted(loaded)}")
    print(f"  keys in the served /api/airtrain body : {sorted(body)}")
    print(f"  vintage markers in served bytes       : {len(hits)} of "
          f"{len(VINTAGE_MARKERS)} searched")
    print(f"  served stations / routes              : {len(body['stations'])} / "
          f"{len(body['routes'])}")
    print(f"  routes listing Terminal 2 (160561)    : {routes_with_t2}")
    print(f"  stations registered on the map        : {len(fe.get('registered', []))}")
    print(f"  injected NY instants tracked by the UI: {clock_tracked}/{len(cases)}")
    print(f"  headways rendered across those        : "
          f"{sorted({hw for _m, hw in distinct})} min")
    print(f"  rider-facing vintage disclosure       : "
          f"{surface_hits if surface_hits else 'none'}")

    print()
    if FAILURES:
        print("REALITY HAS CHANGED. Failed assertions:")
        for item in FAILURES:
            print(f"  - {item}")
        print("DISPOSITION: NOT AS RECORDED, see the failed assertions above")
        raise SystemExit(1)
    print(
        "DISPOSITION: VERIFIED, load_airtrain returns only stations and routes and the "
        "endpoint's AirTrainData response model independently admits only those two "
        "fields, so the committed fixture's 20211231 'not a live schedule authority' "
        f"provenance reaches no rider: /api/airtrain serves {sorted(body)}. Terminal 2 "
        "(160561, discontinued by the Port Authority in July 2022) is still served and "
        f"listed by all {len(routes_with_t2)} routes, and the headway read back out of "
        f"the rendered panel text and popup HTML was {sorted({hw for _m, hw in distinct})} "
        f"minutes, tracking the injected New York clock across {rendered_ok}/{len(cases)} "
        "instants and labeled only as scheduled service."
    )


if __name__ == "__main__":
    asyncio.run(main_async())
