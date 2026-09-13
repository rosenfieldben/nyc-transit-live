// PIN: the arrival boards, before section 6.2 touches either surface.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// Section 6.2 of docs/design/freshness-contract.md teaches the station popup and the
// station panel to qualify a prediction by its own age. Its promise to every board that
// is serving FRESH content is that nothing changes: silence means current, and a fresh
// row gains no words. A promise like that is worth what its measurement is worth, so
// this is the measurement, committed before either surface is edited (the F11 pattern,
// frontend/stationalerts.test.js).
//
// THE EXPECTATIONS ARE LITERAL STRINGS, taken from the production renderers over the
// hermetic fixtures (tests/e2e/fixtures/api.js) at the fixtures' frozen instant. Nothing
// here recomputes an answer from the code it is checking, so a board that starts saying
// one more word, or orders two rows the other way, moves a literal and fails.
//
// WHAT RUNS IS THE PRODUCTION CODE, not a copy. helpers.js, systems/subway.js and
// stations.js are loaded whole into one vm context in index.html order, the way the
// f04 audit driver does it, over a minimal DOM that records what the panel builds. The
// popup renderers are the functions the station popups call; the panel is the real
// renderStationDetail writing the real #stations-detail and #stations-announce. The
// alert store is empty and fresh, so these pins cover the boards and nothing else
// (the F11 pins own the alert join).
//
// SIX BOARDS, which is every system that has one: subway, LIRR, Metro-North, PATH, NJ
// Transit and ferry. AirTrain has no arrivals board, only scheduled headways. The
// fixtures date every row the way 6.1 serves it: the subway and NJ Transit by their
// feed header, LIRR and PATH by each trip's own clock, ferry docks by the TripUpdates
// header, and Metro-North by nothing at all, because it dates no prediction. That last
// one is the board where "silence means current" is most easily broken, since an
// undated row is exactly where a careless rule would print "age unknown".

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const fx = require("../tests/e2e/fixtures/api.js");

// ---- the browser stand-in -------------------------------------------------------

// Just enough of an element for stations.js: it builds with createElement, sets
// textContent and className, and appends. Nothing here parses markup, so the panel's
// structure is read back exactly as the code assembled it.
function makeEl(tag) {
  return {
    tagName: tag,
    className: "",
    textContent: "",
    hidden: false,
    children: [],
    style: {},
    classList: { contains: () => false, toggle: () => {}, add: () => {}, remove: () => {} },
    append(...nodes) {
      this.children.push(...nodes);
    },
    appendChild(node) {
      this.children.push(node);
      return node;
    },
    replaceChildren(...nodes) {
      this.children = nodes.slice();
    },
    contains: () => false,
    addEventListener() {},
    removeEventListener() {},
    setAttribute() {},
    removeAttribute() {},
    focus() {},
  };
}

// One context per test, so no board's live-region memo can leak into the next.
function loadFrontend() {
  const elements = Object.create(null);
  const byId = (id) => (elements[id] ??= Object.assign(makeEl("div"), { id }));
  const sandbox = {
    console,
    URLSearchParams,
    document: { getElementById: byId, createElement: makeEl, body: makeEl("body"), activeElement: null },
    // systems/subway.js makes a canvas renderer and registers a dimming sweep at load.
    L: { canvas: () => ({}) },
    staleTreatments: [],
    // What systems/shared.js would otherwise supply to the panel's alert block: an empty
    // store whose source is current, so the block renders nothing and says nothing.
    alertsSystems: {},
    alertsFetchedAt: fx.FROZEN_S,
    alertsFirstAttemptAt: fx.FROZEN_S,
    alertsClockNow: () => fx.FROZEN_S,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  // THE FIXTURES' FROZEN INSTANT, installed before any source loads. The countdowns and
  // the clock labels are computed from Date.now(), and the clock labels name New York
  // time explicitly, so the strings below do not depend on the runner's zone.
  vm.runInContext(
    `(() => {
       const Real = Date;
       class Frozen extends Real {
         constructor(...a) { if (a.length === 0) super(${fx.FROZEN_MS}); else super(...a); }
         static now() { return ${fx.FROZEN_MS}; }
       }
       globalThis.Date = Frozen;
     })();`,
    sandbox,
  );
  for (const rel of ["helpers.js", "systems/subway.js", "stations.js"]) {
    vm.runInContext(readFileSync(join(__dirname, rel), "utf8"), sandbox, { filename: rel });
  }
  vm.runInContext("var alertsIndex = indexAlerts([]);", sandbox);
  return { sandbox, byId, run: (src) => vm.runInContext(src, sandbox) };
}

// The panel's detail area as indented lines: tag, class, text. Enough to see every
// word a rider can read and which element carries it.
function panelLines(node, depth = 0, out = []) {
  for (const kid of node.children) {
    const cls = kid.className ? `.${kid.className}` : "";
    out.push(`${"  ".repeat(depth)}${kid.tagName}${cls}${kid.textContent ? ` ${kid.textContent}` : ""}`);
    panelLines(kid, depth + 1, out);
  }
  return out;
}

// ---- the six boards, wired the way the loaders wire them -------------------------

// Each board's popup is called with the arguments its loader's popup lambda passes, and
// each panel entry carries the fields its loader's registerStation call gives it. The
// route lookups are built from the fixtures' route tables with the loaders' own keys.
function boards(S) {
  const railroadNames = new Map(fx.railroadRoutes().map((r) => [`${r.system}|${r.route}`, r.name]));
  const railroadName = (system) => (r) => railroadNames.get(`${system}|${r}`) || null;
  const pathColors = new Map(fx.pathRoutes().map((r) => [r.id, S.pathColor(r.color)]));
  const pathNames = new Map(fx.pathRoutes().map((r) => [r.id, r.name]));
  const njt = S.njtRouteTables(fx.njtRoutes());
  const ferryColors = new Map(
    fx.ferryRoutes().map((r) => [r.id, S.pathColor(r.color, S.FERRY_FALLBACK_COLOR)]),
  );
  const ferryNames = new Map(fx.ferryRoutes().map((r) => [r.id, r.name]));
  const [lirr, mnr] = fx.railroadStops();
  const subway = fx.subwayStops()[0];
  const path = fx.pathStops()[0];
  const penn = fx.njtStops()[0];
  const dock = fx.ferryStops()[0];
  const now = fx.FROZEN_S;
  return {
    subway: {
      body: fx.subwayArrivals(),
      popup: (b) => S.subwayArrivalsHtml(subway, b),
      entry: {
        key: `subway|${subway.id}`, kind: "subway", systemLabel: "Subway", noun: "train",
        id: subway.id, name: subway.name, routes: subway.routes, wheelchair: false,
        arrivalsUrl: `/api/subway-arrivals/${subway.id}`,
      },
    },
    lirr: {
      body: fx.railroadArrivalsLirr(),
      popup: (b) => S.railroadArrivalsHtml(lirr, b, now, railroadName(lirr.system)),
      entry: {
        key: `${lirr.system}|${lirr.id}`, kind: "railroad", systemLabel: "LIRR", noun: "train",
        id: lirr.id, system: lirr.system, name: lirr.name, routes: [], wheelchair: false,
        arrivalsUrl: `/api/railroad-arrivals/${lirr.system}/${lirr.id}`,
        nameFor: railroadName(lirr.system),
      },
    },
    mnr: {
      body: fx.railroadArrivals(),
      popup: (b) => S.railroadArrivalsHtml(mnr, b, now, railroadName(mnr.system)),
      entry: {
        key: `${mnr.system}|${mnr.id}`, kind: "railroad", systemLabel: "Metro-North", noun: "train",
        id: mnr.id, system: mnr.system, name: mnr.name, routes: [], wheelchair: false,
        arrivalsUrl: `/api/railroad-arrivals/${mnr.system}/${mnr.id}`,
        nameFor: railroadName(mnr.system),
      },
    },
    path: {
      body: fx.pathArrivals(),
      popup: (b) =>
        S.pathArrivalsHtml(
          path,
          b,
          now,
          (r) => pathColors.get(r) ?? S.PATH_FALLBACK_COLOR,
          (r) => pathNames.get(r) || null,
        ),
      entry: {
        key: `PATH|${path.id}`, kind: "path", systemLabel: "PATH", noun: "train",
        id: path.id, name: path.name, routes: path.routes ?? [], wheelchair: false,
        arrivalsUrl: `/api/path-arrivals/${path.id}`, nameFor: (r) => pathNames.get(r) || null,
      },
    },
    njt: {
      body: fx.njtArrivals(),
      popup: (b) =>
        S.njtArrivalsHtml(
          penn,
          b,
          now,
          (r) => S.njtRouteColor(r, njt.colors),
          (r) => S.njtRouteName(r, njt.names),
        ),
      entry: {
        key: `NJT|${penn.id}`, kind: "njt", systemLabel: "NJ Transit", noun: "train",
        id: penn.id, name: penn.name, routes: penn.routes ?? [], wheelchair: false,
        arrivalsUrl: `/api/njt-arrivals/${penn.id}`, nameFor: (r) => S.njtRouteName(r, njt.names),
      },
    },
    ferry: {
      body: fx.ferryArrivals(),
      popup: (b) =>
        S.ferryArrivalsHtml(dock, b, now, (r) => ferryColors.get(r) ?? S.FERRY_FALLBACK_COLOR),
      entry: {
        key: `ferry|${dock.id}`, kind: "ferry", systemLabel: "Ferry", noun: "boat",
        id: dock.id, name: dock.name, routes: dock.routes ?? [], wheelchair: dock.wheelchair === true,
        arrivalsUrl: `/api/ferry-arrivals/${dock.id}`, nameFor: (r) => ferryNames.get(r) || null,
      },
    },
  };
}

// Render one board on both surfaces, in a fresh context: the popup's markup, the
// panel's detail area, and what the panel's live region said on selection.
function render(name) {
  const h = loadFrontend();
  const board = boards(h.sandbox)[name];
  const popup = board.popup(board.body);
  h.sandbox.__entry = board.entry;
  h.sandbox.__body = board.body;
  h.run(
    "panelStation = __entry; panelBody = __body; panelError = null;" +
      " panelAnnounced = null; panelAlertsAnnounced = null; renderStationDetail();",
  );
  return {
    popup,
    panel: panelLines(h.byId("stations-detail")),
    spoken: h.byId("stations-announce").textContent,
  };
}

// The subway badge colors, spelled once so the popup literals stay readable.
const RED = 'style="background:#c0392b;color:#ffffff"';
const BROWN = 'style="background:#5d4037;color:#ffffff"';

test("PIN subway: Times Sq, one contributing group, every row dated by its header", () => {
  const out = render("subway");
  assert.equal(
    out.popup,
    "<b>Times Sq-42 St</b>" +
      '<div class="arr-dir">Northbound</div>' +
      `<span class="arr-badge" ${RED}>1</span> 2 min<br>` +
      `<span class="arr-badge" ${RED}>2</span> 5 min` +
      '<div class="arr-dir">Southbound</div>' +
      `<span class="arr-badge" ${RED}>1</span> 3 min`,
  );
  assert.deepEqual(out.panel, [
    "h3 Times Sq-42 St (Subway)",
    "h4 Northbound",
    "ul.station-arrivals",
    "  li 1 train in 2 minutes, 8:01 AM arrival",
    "  li 2 train in 5 minutes, 8:05 AM arrival",
    "h4 Southbound",
    "ul.station-arrivals",
    "  li 1 train in 3 minutes, 8:03 AM arrival",
  ]);
  assert.equal(
    out.spoken,
    "Times Sq-42 St, Subway. Northbound: 1 train in 2 minutes, 8:01 AM arrival. " +
      "2 train in 5 minutes, 8:05 AM arrival. Southbound: 1 train in 3 minutes, 8:03 AM arrival",
  );
});

test("PIN LIRR: Jamaica, each prediction dated by its own trip", () => {
  const out = render("lirr");
  assert.equal(
    out.popup,
    '<b>Jamaica</b> <span class="popup-sub">LIRR</span>' +
      '<div class="arr-dir">Inbound</div>' +
      `<span class="arr-badge" ${BROWN}>1</span> Babylon Branch <span class="popup-sub">#8412</span> 4 min` +
      '<div class="arr-dir">Outbound</div>' +
      `<span class="arr-badge" ${BROWN}>1</span> Babylon Branch <span class="popup-sub">#8413</span> 7 min`,
  );
  assert.deepEqual(out.panel, [
    "h3 Jamaica (LIRR)",
    "h4 Inbound",
    "ul.station-arrivals",
    "  li Babylon Branch train in 4 minutes, 8:04 AM arrival, train 8412",
    "h4 Outbound",
    "ul.station-arrivals",
    "  li Babylon Branch train in 7 minutes, 8:07 AM arrival, train 8413",
  ]);
  assert.equal(
    out.spoken,
    "Jamaica, LIRR. Inbound: Babylon Branch train in 4 minutes, 8:04 AM arrival, train 8412. " +
      "Outbound: Babylon Branch train in 7 minutes, 8:07 AM arrival, train 8413",
  );
});

test("PIN Metro-North: Grand Central, whose predictions carry no clock at all", () => {
  // THE BOARD MOST LIKELY TO MOVE BY ACCIDENT. Every row's observed_at is null because
  // Metro-North dates nothing, and a rule that read null as an anomaly here would stamp
  // "age unknown" on every row of an entire railroad, on every healthy day. It must stay
  // exactly as silent as the dated boards.
  const out = render("mnr");
  assert.equal(
    out.popup,
    '<b>Grand Central</b> <span class="popup-sub">MNR</span>' +
      '<div class="arr-dir">Inbound</div>' +
      `<span class="arr-badge" ${BROWN}>1</span> Hudson <span class="popup-sub">#795</span> 4 min` +
      '<div class="arr-dir">Outbound</div>' +
      `<span class="arr-badge" ${BROWN}>1</span> Hudson <span class="popup-sub">#812</span> 6 min`,
  );
  assert.deepEqual(out.panel, [
    "h3 Grand Central (Metro-North)",
    "h4 Inbound",
    "ul.station-arrivals",
    "  li Hudson train in 4 minutes, 8:04 AM arrival, train 795",
    "h4 Outbound",
    "ul.station-arrivals",
    "  li Hudson train in 6 minutes, 8:06 AM arrival, train 812",
  ]);
  assert.equal(
    out.spoken,
    "Grand Central, Metro-North. Inbound: Hudson train in 4 minutes, 8:04 AM arrival, train 795. " +
      "Outbound: Hudson train in 6 minutes, 8:06 AM arrival, train 812",
  );
});

test("PIN PATH: World Trade Center, two trips with two different clocks", () => {
  const out = render("path");
  assert.equal(
    out.popup,
    '<b>World Trade Center</b> <span class="popup-sub">PATH</span>' +
      '<div class="arr-dir">To New York</div>' +
      '<span class="arr-badge" style="background:#4d92fb;color:#1a1a1a">859</span> Hoboken - 33rd 2 min' +
      '<div class="arr-dir">To New Jersey</div>' +
      '<span class="arr-badge" style="background:#d93a30;color:#ffffff">862</span> Newark - World Trade Center 5 min',
  );
  assert.deepEqual(out.panel, [
    "h3 World Trade Center (PATH)",
    "h4 To New York",
    "ul.station-arrivals",
    "  li Hoboken - 33rd train in 2 minutes, 8:01 AM arrival",
    "h4 To New Jersey",
    "ul.station-arrivals",
    "  li Newark - World Trade Center train in 5 minutes, 8:05 AM arrival",
  ]);
  assert.equal(
    out.spoken,
    "World Trade Center, PATH. To New York: Hoboken - 33rd train in 2 minutes, 8:01 AM arrival. " +
      "To New Jersey: Newark - World Trade Center train in 5 minutes, 8:05 AM arrival",
  );
});

test("PIN NJ Transit: New York Penn, a flat board dated by the TripUpdates header", () => {
  const out = render("njt");
  assert.equal(
    out.popup,
    '<b>New York Penn Station</b> <span class="popup-sub">NJ Transit</span>' +
      '<span class="arr-badge" style="background:#DD3439;color:#ffffff">9</span> Trenton <span class="popup-sub">3800</span> 2 min<br>' +
      '<span class="arr-badge" style="background:#E66859;color:#1a1a1a">2</span> Dover <span class="popup-sub">6634</span> 5 min<br>' +
      '<span class="arr-badge" style="background:#4a4e69;color:#ffffff">?</span> Bay Head 8 min',
  );
  assert.deepEqual(out.panel, [
    "h3 New York Penn Station (NJ Transit)",
    "h4 Departures",
    "ul.station-arrivals",
    "  li Northeast Corridor to Trenton train in 2 minutes, 8:01 AM arrival, train 3800",
    "  li Montclair-Boonton Line to Dover train in 5 minutes, 8:05 AM arrival, train 6634",
    "  li to Bay Head train in 8 minutes, 8:08 AM arrival",
  ]);
  assert.equal(
    out.spoken,
    "New York Penn Station, NJ Transit. Departures: " +
      "Northeast Corridor to Trenton train in 2 minutes, 8:01 AM arrival, train 3800. " +
      "Montclair-Boonton Line to Dover train in 5 minutes, 8:05 AM arrival, train 6634. " +
      "to Bay Head train in 8 minutes, 8:08 AM arrival",
  );
});

test("PIN ferry: Wall St/Pier 11, a dock dated by TripUpdates and a boat dwelling", () => {
  const out = render("ferry");
  assert.equal(
    out.popup,
    '<b>Wall St/Pier 11</b> <span class="popup-sub">NYC Ferry</span>' +
      ' <span class="popup-access" title="Wheelchair accessible">&#9855;</span>' +
      '<div class="arr-dir" style="color:#007c94">East River</div>2 min' +
      '<div class="arr-dir" style="color:#8c7300">South Brooklyn</div>departs 2 min',
  );
  assert.deepEqual(out.panel, [
    "h3 Wall St/Pier 11 (Ferry)",
    "  span.visually-hidden , wheelchair accessible",
    "h4 East River",
    "ul.station-arrivals",
    "  li East River boat in 2 minutes, 8:01 AM arrival",
    "h4 South Brooklyn",
    "ul.station-arrivals",
    "  li South Brooklyn boat departs in 2 minutes, 8:01 AM departure",
  ]);
  assert.equal(
    out.spoken,
    "Wall St/Pier 11, Ferry. East River: East River boat in 2 minutes, 8:01 AM arrival. " +
      "South Brooklyn: South Brooklyn boat departs in 2 minutes, 8:01 AM departure",
  );
});

test("PIN the fixtures ARE fresh, so the six pins above are pins of fresh boards", () => {
  // The claim the pins rest on, checked rather than assumed: every dated row is well
  // inside the 90 second threshold at the frozen instant, and the only undated rows are
  // Metro-North's. A fixture edit that aged a row would otherwise turn one of the pins
  // above into a pin of a stale board, which is not what it says it is.
  const S = loadFrontend().sandbox;
  const all = boards(S);
  for (const [name, board] of Object.entries(all)) {
    const rows = S.stationArrivalsRows(board.body);
    assert.ok(rows.length, `${name} has rows`);
    for (const row of rows) {
      assert.equal(row.provenance, "reported", `${name} row provenance`);
      if (name === "mnr") {
        assert.equal(row.observed_at, null, "Metro-North dates nothing");
      } else {
        assert.ok(fx.FROZEN_S - row.observed_at < 60, `${name} row is fresh`);
      }
    }
    assert.equal(board.body.served_at, fx.FROZEN_S, `${name} served at the frozen instant`);
  }
});
