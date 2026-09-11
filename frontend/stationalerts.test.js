// THE PIN, TAKEN BEFORE THE STATION ALERT JOIN WAS TOUCHED (F11).
//
// F11's fix teaches one helper to read every arrivals body shape so the station panel
// and NJ Transit can both reach the join, and the promise attached to that change is
// that the systems ALREADY using it see no difference at all. A promise like that is
// worth exactly as much as the measurement behind it, so this file was written and
// committed first, against unmodified production code, and its expectations are
// LITERAL STRINGS rather than anything recomputed from the implementation. A helper
// that quietly starts matching one more alert, or ordering two rows the other way,
// changes a literal here and fails.
//
// WHAT IT EXECUTES IS THE PRODUCTION FUNCTION, not a copy of it. systems/shared.js is
// a browser script with no module exports (it reaches for L, document and window at
// load), so the whole file cannot simply be required. Instead the named function's
// SOURCE TEXT is sliced out of the file by brace balance and evaluated in a vm with
// its dependencies injected. That keeps the pin honest in the one way that matters:
// editing stationAlertsBlock in systems/shared.js changes what this test runs.
//
// THE ALERT STORE IS INJECTED, AND SAYS SO. No committed fixture carries a subway
// station suspension, which is the same reason docs/reviews/audit-2026-09-05/
// f11_station_panel_hides_alerts.mjs injects its own. Everything else here is the
// committed hermetic fixture: the stations, their routes-per-station lists, and the
// arrivals bodies the real endpoints serve.

const test = require("node:test");
const assert = require("node:assert");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const H = require("./helpers.js");
const fx = require("../tests/e2e/fixtures/api.js");

const SHARED = join(__dirname, "systems", "shared.js");

// Slice a top-level `function NAME(...) { ... }` out of a source file by balancing
// braces from the body's opening brace. Brace-balanced rather than "up to the next
// line that is just }", because that stops at the first nested block a future edit
// indents differently and would silently pin half a function.
function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} not found in the production source`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i++) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  assert.fail(`${name} has unbalanced braces`);
}

// The injected alert store. Selectors are in each system's own id space, matching the
// committed station fixtures: subway 127 (Times Sq, static routes 1/2/3), LIRR 12
// (Jamaica), MNR 1 (Grand Central), ferry dock 18 (Wall St/Pier 11, static routes
// ER/SB/SV), NJT 109 (New York Penn, static routes 9/2).
//
// ROUTE "3" AND ROUTE "SV" ARE THE ONES WORTH NAMING. Both serve their station in the
// static routes-per-station index and have NO imminent train in the arrivals fixture,
// so they can reach the station only through the static side of the union. They are
// the H5 case, and F11's acceptance is that the panel shows them too.
const INJECTED_ALERTS = [
  alert("sub-stop", "subway", "Times Sq-42 St is closed", { stops: ["127"] }),
  alert("sub-route-3", "subway", "[3] suspended overnight", { routes: ["3"] }),
  alert("sub-route-Z", "subway", "[Z] does not serve Times Sq", { routes: ["Z"] }),
  alert("lirr-stop", "LIRR", "Jamaica platforms closed", { stops: ["12"] }),
  alert("mnr-stop", "MNR", "Grand Central closed", { stops: ["1"] }),
  alert("ferry-stop", "ferry", "Wall St/Pier 11 landing closed", { stops: ["18"] }),
  alert("ferry-route-er", "ferry", "East River route reroute", { routes: ["ER"] }),
  alert("ferry-route-sv", "ferry", "Soundview route suspended", { routes: ["SV"] }),
  alert("njt-stop", "njt", "New York Penn Station platforms closed", { stops: ["109"] }),
  alert("njt-route-2", "njt", "[2] Morris and Essex suspended", { routes: ["2"] }),
];

function alert(id, system, header, { routes = [], stops = [] } = {}) {
  return {
    id,
    system,
    header,
    description: null,
    effect: "NO_SERVICE",
    cause: "MAINTENANCE",
    routes,
    stops,
    starts_at: fx.FROZEN_S - 600,
    ends_at: null,
  };
}

// Evaluate the production stationAlertsBlock over a given alert store, with its
// dependencies injected. The freshness marker is stubbed to "" so these pins are
// about the alert JOIN alone; staleAlertsMarker reads browser-scope globals and has
// its own coverage. Both the helper the fix introduces and the two pieces it
// composes are injected, so the pin keeps running whichever of them the function
// reaches for.
function productionStationAlertsBlock(alerts) {
  const body = extractFunction(readFileSync(SHARED, "utf8"), "stationAlertsBlock");
  const sandbox = {
    Object,
    Set,
    Array,
    // THE WHOLE HELPER NAMESPACE, not a hand-listed few. Listing them means editing
    // this harness the moment the production function reaches for one more, and a
    // pin whose harness changes alongside the code it pins is worth less. Spreading
    // the module keeps the EXPECTATIONS below the only thing that can move.
    ...H,
    alertsIndex: H.indexAlerts(alerts),
    staleAlertsMarker: () => "",
  };
  vm.createContext(sandbox);
  vm.runInContext(`${body}\nthis.__block = stationAlertsBlock;`, sandbox);
  return sandbox.__block;
}

const block = productionStationAlertsBlock(INJECTED_ALERTS);

const row = (text) => `<div class="alert-row">${text}</div>`;
const wrap = (...rows) => `<div class="alert-block">${rows.join("")}</div>`;

// The stations, straight off the committed fixtures. The railroad stops fixture
// publishes no routes-per-station list (the endpoint does not serve one for the
// railroads), which is why these two match by stop selector alone.
const TIMES_SQ = fx.subwayStops()[0];
const JAMAICA = fx.railroadStops()[0];
const GRAND_CENTRAL = fx.railroadStops()[1];
const PIER_11 = fx.ferryStops()[0];
const NY_PENN = fx.njtStops()[0];

test("PIN subway: a stop suspension and a served route with no train, in that order", () => {
  // compareAlerts puts open-ended alerts first, then by starts_at, then by id: both
  // are open-ended and share a start, so "sub-route-3" sorts before "sub-stop".
  assert.equal(
    block("subway", TIMES_SQ, fx.subwayArrivals()),
    wrap(row("[3] suspended overnight"), row("Times Sq-42 St is closed")),
  );
});

test("PIN subway: a route that does not serve the station stays out", () => {
  assert.ok(!block("subway", TIMES_SQ, fx.subwayArrivals()).includes("[Z]"));
});

test("PIN LIRR: Jamaica matches by stop selector", () => {
  assert.equal(block("LIRR", JAMAICA, fx.railroadArrivals()), wrap(row("Jamaica platforms closed")));
});

test("PIN MNR: Grand Central matches by stop selector", () => {
  assert.equal(
    block("MNR", GRAND_CENTRAL, fx.railroadArrivals()),
    wrap(row("Grand Central closed")),
  );
});

test("PIN the railroads do not leak into each other, on colliding bare ids", () => {
  // MNR Grand Central is id "1" and LIRR has its own id space; a join scoped only by
  // id would hand Jamaica's alert to a Metro-North platform.
  assert.ok(!block("MNR", GRAND_CENTRAL, fx.railroadArrivals()).includes("Jamaica"));
  assert.ok(!block("LIRR", JAMAICA, fx.railroadArrivals()).includes("Grand Central"));
});

test("PIN ferry: a dock joins its stop alert and BOTH served-route alerts", () => {
  // This is the output ferry.js produces today through its own inline copy of the
  // join. Pinned here as a literal so collapsing that copy onto the shared helper is
  // provably output-preserving rather than merely believed to be.
  assert.equal(
    block("ferry", PIER_11, fx.ferryArrivals()),
    wrap(
      row("East River route reroute"),
      row("Soundview route suspended"),
      row("Wall St/Pier 11 landing closed"),
    ),
  );
});

test("PIN ferry: the inline copy in systems/ferry.js agrees with the helper", () => {
  // The expression ferry.js evaluates today, written out. If these two ever disagree,
  // one of the two surfaces is showing a rider a different alert set than the other,
  // which is the class of defect F11 is about.
  const inline = H.alertsBlockHtml(
    H.matchStationAlerts(H.indexAlerts(INJECTED_ALERTS), "ferry", PIER_11.id, PIER_11.routes ?? []),
  );
  assert.equal(inline, block("ferry", PIER_11, fx.ferryArrivals()));
});

test("PIN NJ Transit: the helper already answered it; the gap was the call site", () => {
  // THE FINDING'S SECOND HALF, MEASURED RATHER THAN INFERRED. 15c's ledger deferred
  // the NJT alert join with the reason that stationAlertsBlock "unions the route ids
  // out of body.directions, and a flat NJT arrivals body has no directions". Run
  // against the committed NJT fixtures, the helper returns the right answer today:
  // it seeds the route set from station.routes FIRST and only ADDS to it from the
  // body, so a body with no directions costs nothing a station's own routes list
  // already covers. Nothing in systems/njt.js ever called it, which is the whole gap.
  assert.equal(
    block("njt", NY_PENN, fx.njtArrivals()),
    wrap(row("[2] Morris and Essex suspended"), row("New York Penn Station platforms closed")),
  );
});

test("PIN PATH and AirTrain have no alerts feed, so the join has nothing to give", () => {
  // feeds/alerts.py ALERT_FEED_URLS carries subway, bus, LIRR, MNR, ferry and njt.
  // systems/path.js renders no alerts block for exactly this reason, and this pins
  // that F11's fix must not invent one: an alerts area with no feed behind it would
  // tell a rider that silence means no alerts when it means no data.
  assert.equal(block("path", fx.pathStops()[0], fx.pathArrivals()), "");
  assert.equal(block("airtrain", { id: "jfk-1", routes: [] }, null), "");
});

test("PIN an empty store renders no container at all, on every system", () => {
  const empty = productionStationAlertsBlock([]);
  assert.equal(empty("subway", TIMES_SQ, fx.subwayArrivals()), "");
  assert.equal(empty("LIRR", JAMAICA, fx.railroadArrivals()), "");
  assert.equal(empty("ferry", PIER_11, fx.ferryArrivals()), "");
  assert.equal(empty("njt", NY_PENN, fx.njtArrivals()), "");
});
