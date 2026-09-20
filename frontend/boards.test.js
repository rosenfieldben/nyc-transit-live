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
const H = require("./helpers.js");

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
    // MR4: systems/shared.js's canvas-theme registry, which systems/subway.js joins at
    // load so a theme swap can repaint the ribbons and the station circles. It belongs
    // beside staleTreatments for the same reason: a load-time hook this harness does not
    // exercise, stubbed to the real signature (the real one returns the painter).
    registerCanvasFamily: (_name, paint) => paint,
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
    popup: withoutMarks(popup),
    panel: panelLines(h.byId("stations-detail")),
    spoken: h.byId("stations-announce").textContent,
  };
}

/* MR5: A MARK IS ONE TOKEN IN THESE PINS, and the reason is length rather than laziness. Section 5
   gives a subway station's kicker the route marks of every line calling there, drawn by the MAP's
   own builder, so Times Sq's board would arrive here with four hundred characters of SVG per route
   and the pin would become unreadable. What these pins are for is the WORDS and the ORDER of a
   board; the token carries the mark's label, its drawn size and its declared fills, which is what a
   board decides about a mark, and its geometry stays where marks live.

   IMPORTED RATHER THAN COPIED, and the first version of this file copied it. tests/e2e/popup.js
   requires nothing itself, so a node test can require it; the copy's own comment claimed otherwise
   and was wrong, which made it two implementations of one reader in the commit that named that
   shape. One now, and the browser tier's markup pins and these read a mark the same way. */
const { withoutMarks } = require("../tests/e2e/popup.js");

// The subway badge colors, spelled once so the popup literals stay readable.
const RED = 'style="background:#c0392b;color:#ffffff"';
const BROWN = 'style="background:#5d4037;color:#ffffff"';

/* ONE OF Times Sq's KICKER PLATES, as withoutMarks prints it: the route the plate carries, the size
   the popup drew it at, and the fills the plate declares, in the order the SVG lists them (the
   rounded square, then the numeral). 17 is POPUP_MARK_ROW, the design's small mark, and it is a
   literal here on purpose: the constant's own value is pinned in frontend/popupvocab.test.js, so if
   a later stage draws a kicker's plates larger these pins say so rather than following along.

   THE FILLS ARE THE PAIR RED SPELLS ABOVE, which is the reason they are in the token at all: the
   kicker's plates and the arrival badges below both ask lineColor for the 1 train and both ink
   against it, so a plate whose paint stopped agreeing with its badge is a defect the reader of a
   popup can see, and a token that said only [mark 1] could not fail on it. */
const plate = (route) => `[mark ${route} 17x17 #c0392b,#ffffff]`;
const TIMES_SQ_PLATES = [1, 2, 3].map(plate).join("");

/* MR5: SECTION 5's GRAMMAR, AS FOUR TEMPLATES, so six board pins stay readable after the popup
   became a kicker, a title and a three-cell grid. These are literal templates written HERE, in the
   same spirit as RED and BROWN above: a production builder that stopped emitting `class="n"`, or
   that put its cells in another order, still fails every pin below, because the expected string is
   assembled from these literals and compared whole.

   AND THE FIRST PIN USES NONE OF THEM. The subway board is spelled out character by character so
   the grammar itself is pinned in one place with no shared template in the way: if these four ever
   drifted alongside the builders they describe, that pin is what would still say so. */
const kicker = (left, right = "") => `<div class="pk"><span>${left}</span>\n<span>${right}</span></div>\n`;
const title = (text) => `<div class="pt"><span>${text}</span></div>\n`;
const dir = (text) => `<div class="dir">${text}</div>\n`;
const arr = (...rows) => `<div class="arr">${rows.join("\n")}</div>\n`;
// One row: the badge, the middle cell (a route name, a train number, a qualifier, or nothing at
// all) and the countdown in its own nowrap cell.
const row = (mark, middle, n) => `<span>${mark}</span>\n<span>${middle}</span>\n<span class="n">${n}</span>`;

test("PIN subway: Times Sq, one contributing group, every row dated by its header", () => {
  const out = render("subway");
  /* SPELLED OUT IN FULL, which is this file's one unaided pin of MR5's grammar: the kicker with the
     station's own route marks on the right (three plates, each normalised by withoutMarks to its
     route, its drawn size and its fills, because a plate is four hundred characters of SVG), the
     title, a heading per direction and a
     three-cell row per arrival. Every other pin in this file assembles the same shapes from the
     four templates above; this one is what would catch those templates drifting. */
  assert.equal(
    out.popup,
    `<div class="pk"><span>Subway</span>\n<span>${TIMES_SQ_PLATES}</span></div>\n` +
      '<div class="pt"><span>Times Sq-42 St</span></div>\n' +
      '<div class="dir">Northbound</div>\n' +
      '<div class="arr">' +
      `<span><span class="arr-badge" ${RED}>1</span></span>\n<span></span>\n<span class="n">2 min</span>\n` +
      `<span><span class="arr-badge" ${RED}>2</span></span>\n<span></span>\n<span class="n">5 min</span>` +
      "</div>\n" +
      '<div class="dir">Southbound</div>\n' +
      '<div class="arr">' +
      `<span><span class="arr-badge" ${RED}>1</span></span>\n<span></span>\n<span class="n">3 min</span>` +
      "</div>\n",
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
    kicker("LIRR") +
      title("Jamaica") +
      dir("Inbound") +
      arr(row(`<span class="arr-badge" ${BROWN}>1</span>`, 'Babylon Branch <span class="popup-sub">#8412</span>', "4 min")) +
      dir("Outbound") +
      arr(row(`<span class="arr-badge" ${BROWN}>1</span>`, 'Babylon Branch <span class="popup-sub">#8413</span>', "7 min")),
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
    // MR5: THE KICKER IS THE SERVED CODE, "MNR", where this station's own panel row two assertions
    // down says "Metro-North". The popup's head has printed the code since phase 9; the divergence
    // is recorded as an MR5 finding and is not reworded here.
    kicker("MNR") +
      title("Grand Central") +
      dir("Inbound") +
      arr(row(`<span class="arr-badge" ${BROWN}>1</span>`, 'Hudson <span class="popup-sub">#795</span>', "4 min")) +
      dir("Outbound") +
      arr(row(`<span class="arr-badge" ${BROWN}>1</span>`, 'Hudson <span class="popup-sub">#812</span>', "6 min")),
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
    kicker("PATH") +
      title("World Trade Center") +
      dir("To New York") +
      arr(row('<span class="arr-badge" style="background:#4d92fb;color:#1a1a1a">859</span>', "Hoboken - 33rd", "2 min")) +
      dir("To New Jersey") +
      arr(row('<span class="arr-badge" style="background:#d93a30;color:#ffffff">862</span>', "Newark - World Trade Center", "5 min")),
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
    // A FLAT BOARD IS ONE .arr WITH THREE ROWS, where every other board opens one per bucket.
    kicker("NJ Transit") +
      title("New York Penn Station") +
      arr(
        row('<span class="arr-badge" style="background:#DD3439;color:#ffffff">9</span>', 'Trenton <span class="popup-sub">3800</span>', "2 min"),
        row('<span class="arr-badge" style="background:#E66859;color:#1a1a1a">2</span>', 'Dover <span class="popup-sub">6634</span>', "5 min"),
        row('<span class="arr-badge" style="background:#4a4e69;color:#ffffff">?</span>', "Bay Head", "8 min"),
      ),
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
    /* MR5: the dock's accessibility glyph is the kicker's right-hand slot, which is where section 5
       puts it, and it keeps the title attribute that is the only place its words exist.
       The bucket headings' ink is walked against the popup's OWN surface now that section 5 makes
       it --surface (before this stage: #007c94 and #8c7300, both walked against #ffffff). The
       words, the order and the countdowns are unchanged; only the two ink values moved, and each
       still clears 4.5 on the surface it is printed on.
       A FERRY ROW HAS NO BADGE AND NOTHING TO NAME: its bucket is the route, so the first two
       cells are empty and the countdown carries its own "departs". */
    kicker("NYC Ferry", '<span class="popup-access" title="Wheelchair accessible">&#9855;</span>') +
      title("Wall St/Pier 11") +
      '<div class="dir" style="color:#006f85">East River</div>\n' +
      arr(row("", "", "2 min")) +
      '<div class="dir" style="color:#735e00">South Brooklyn</div>\n' +
      arr(row("", "", "departs 2 min")),
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

/* ---------------- 6.2: the boards when their content is old ---------------- */

// A copy of one fixture board whose every DATED row was observed `ageS` before the board
// was served, the poll itself still fresh: exactly the F03 world, a provider serving old
// content to polls that keep succeeding. Undated rows (Metro-North's) stay undated.
//
// THE CONTENT CLOCKS AGE WITH THE ROWS, the way the backend serves them. A subway row is
// dated by its group's header and NJ Transit's and the ferry's by their TripUpdates
// header, so the block's and the envelope's feed_timestamp are that same old stamp, and
// PATH's envelope clock is its oldest row's. LIRR alone dates each trip itself, so its
// header stays current over old trips. Old rows under a current header everywhere is a
// world the backend never serves, and a panel line raised from the envelope's content
// clock would pass every test run in it.
function agedBody(body, ageS) {
  const copy = JSON.parse(JSON.stringify(body));
  const stamp = copy.served_at - ageS;
  for (const row of H.stationArrivalsRows(copy)) {
    if (row.observed_at != null) row.observed_at = stamp;
  }
  if (copy.system !== "LIRR") {
    if (copy.feed_timestamp != null) copy.feed_timestamp = stamp;
    for (const block of Object.values(copy.systems || {})) {
      if (block.feed_timestamp != null) block.feed_timestamp = stamp;
    }
  }
  return copy;
}

// The qualifier words each surface put on each row, in board order: the popup's spans and
// the panel's sentences (arrivalSentence appends ", <qualifier>" after the clock label).
const popupQualifiers = (html) => [...html.matchAll(/<span class="arr-qualifier">([^<]*)<\/span>/g)].map((m) => m[1]);
const panelQualifiers = (lines) =>
  lines
    .filter((line) => line.startsWith("  li "))
    .map((line) => (line.match(/ (?:AM|PM) (?:arrival|departure), ((?:as of|showing last known|age unknown)[^,]*(?:, as of [^,]*)?)/) || [])[1] || "");

function renderBody(name, body) {
  const h = loadFrontend();
  const board = boards(h.sandbox)[name];
  const popup = board.popup(body);
  h.sandbox.__entry = board.entry;
  h.sandbox.__body = body;
  h.run(
    "panelStation = __entry; panelBody = __body; panelError = null;" +
      " panelAnnounced = null; panelAlertsAnnounced = null; renderStationDetail();",
  );
  return {
    popup: withoutMarks(popup),
    panel: panelLines(h.byId("stations-detail")),
    spoken: h.byId("stations-announce").textContent,
  };
}

test("6.2 the subway board, ten minutes behind: every row says so, in the popup and in the panel", () => {
  const S = loadFrontend().sandbox;
  const out = renderBody("subway", agedBody(boards(S).subway.body, 600));
  const q = ' <span class="arr-qualifier">as of 10m ago</span>';
  assert.equal(
    out.popup,
    kicker("Subway", TIMES_SQ_PLATES) +
      title("Times Sq-42 St") +
      dir("Northbound") +
      arr(
        row(`<span class="arr-badge" ${RED}>1</span>`, q, "2 min"),
        row(`<span class="arr-badge" ${RED}>2</span>`, q, "5 min"),
      ) +
      dir("Southbound") +
      arr(row(`<span class="arr-badge" ${RED}>1</span>`, q, "3 min")),
  );
  assert.deepEqual(out.panel, [
    "h3 Times Sq-42 St (Subway)",
    "h4 Northbound",
    "ul.station-arrivals",
    "  li 1 train in 2 minutes, 8:01 AM arrival, as of 10m ago",
    "  li 2 train in 5 minutes, 8:05 AM arrival, as of 10m ago",
    "h4 Southbound",
    "ul.station-arrivals",
    "  li 1 train in 3 minutes, 8:03 AM arrival, as of 10m ago",
  ]);
  // THE COUNTDOWN STILL COUNTS TO THE PREDICTION; the words sit beside it. And the
  // caveat is spoken with the times it qualifies, not left on screen alone.
  assert.equal(
    out.spoken,
    "Times Sq-42 St, Subway. Northbound: 1 train in 2 minutes, 8:01 AM arrival, as of 10m ago. " +
      "2 train in 5 minutes, 8:05 AM arrival, as of 10m ago. " +
      "Southbound: 1 train in 3 minutes, 8:03 AM arrival, as of 10m ago",
  );
});

test("6.2 ONE HELPER, TWO SURFACES: every dated board words its aged rows identically in both", () => {
  const S = loadFrontend().sandbox;
  for (const name of ["subway", "lirr", "path", "njt", "ferry"]) {
    const body = agedBody(boards(S)[name].body, 600);
    const out = renderBody(name, body);
    const rows = H.stationArrivalsRows(body).length;
    assert.deepEqual(popupQualifiers(out.popup), Array(rows).fill("as of 10m ago"), `${name} popup`);
    assert.deepEqual(panelQualifiers(out.panel), Array(rows).fill("as of 10m ago"), `${name} panel`);
    // A dated row speaks for itself, so the board's own line has nothing to add.
    assert.doesNotMatch(out.popup, /popup-stale/, `${name} popup line`);
    assert.ok(!out.panel.some((l) => l.includes("station-detail-stale")), `${name} panel line`);
  }
  // THE PANEL DERIVES NO AGE OF ITS OWN, which is what "one helper, not two" means in
  // source: stations.js renders what helpers.js hands it and names none of the clocks.
  // Comments are stripped first, because the history of the old rule is written there.
  const code = readFileSync(join(__dirname, "stations.js"), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
  // feed_timestamp among them: it is the served CONTENT clock, and a panel line raised
  // from it is the per-envelope judgement that per-row qualification replaces. And the
  // names ingestEnvelope hands the clocks over under, so going through the door is no way
  // around this either.
  const clocks = ["fetched_at", "served_at", "observed_at", "feed_timestamp", "fetchedAt", "servedAt", "feedTimestamp", "observedAt"];
  for (const name of [...clocks, "ingestEnvelope", "humanizeAge", "staleAge", "servedAge", "ageSeconds"]) {
    assert.ok(!new RegExp(`\\b${name}\\b`).test(code), `stations.js must not name ${name}`);
  }
  assert.ok(code.includes("shapeStationArrivals("), "the panel shapes through the shared helper");
});

test("6.2 per ROW, on both surfaces: a board with a lagging contributor and a current one", () => {
  // The auditor's second clause. Times Sq with a second contributor: the 2 train's group
  // is ten minutes behind, the 1 train's is current. A per-envelope rule could only mark
  // all three rows or none of them.
  const S = loadFrontend().sandbox;
  const body = boards(S).subway.body;
  body.directions.Northbound[1].observed_at = fx.FROZEN_S - 600;
  body.feed_timestamp = fx.FROZEN_S - 600;
  body.systems = {
    "1-7+S": fx.systemBlock(fx.FROZEN_S, { routes: ["1"] }),
    LAGGING: fx.systemBlock(fx.FROZEN_S, { routes: ["2"], feedTimestamp: fx.FROZEN_S - 600 }),
  };
  const out = renderBody("subway", body);
  assert.deepEqual(popupQualifiers(out.popup), ["as of 10m ago"]);
  /* MR5: THE QUALIFIER RIDES THE ROW'S MIDDLE CELL AND THE COUNTDOWN ITS OWN, so "5 min" and
     "as of 10m ago" are no longer adjacent in the markup. The claim is that they are in the SAME
     ROW, which the grid says by the three cells being consecutive: the lagging row is the one
     whose qualifier sits between its badge and its 5 min. */
  assert.match(
    out.popup,
    /<span> <span class="arr-qualifier">as of 10m ago<\/span><\/span>\n<span class="n">5 min<\/span>/,
  );
  assert.deepEqual(panelQualifiers(out.panel), ["", "as of 10m ago", ""]);
  // And no board-wide line on either surface, though this envelope's content clock IS ten
  // minutes old: the rows carry the age, so a line would say it twice, and say it of the
  // current rows too.
  assert.doesNotMatch(out.popup, /popup-stale/);
  assert.ok(!out.panel.some((l) => l.includes("station-detail-stale")), out.panel.join("\n"));
  assert.ok(!out.spoken.includes("Subway. as of"), out.spoken);
});

test("6.2 a row's age is anchored at served_at, on both surfaces, whichever side of it the client clock is", () => {
  // Every other render here runs at now == served_at, where the anchored age and the
  // client clock's own subtraction agree. They part when the two clocks do.
  const S = loadFrontend().sandbox;
  const servedAt = (at, ageS) => {
    const body = boards(S).subway.body;
    body.fetched_at = body.served_at = at;
    for (const block of Object.values(body.systems)) block.fetched_at = at;
    return agedBody(body, ageS);
  };
  // Served 30 s AHEAD of the corrected client clock (the global offset over-corrects, or
  // this response's server leads the vehicle feeds'), its rows 100 s old when served. A
  // clock behind served_at adds nothing: 100 s, qualified. Read off the client clock alone
  // they would be 70 s and silent, a stale countdown presented as current.
  const ahead = renderBody("subway", servedAt(fx.FROZEN_S + 30, 100));
  assert.deepEqual(popupQualifiers(ahead.popup), Array(3).fill("as of 100s ago"));
  assert.deepEqual(panelQualifiers(ahead.panel), Array(3).fill("as of 100s ago"));
  // Served 30 s BEHIND it, its rows 60 s old when served: the 30 s since count, so 90 s,
  // qualified. Measured from served_at alone they would be 60 s and silent.
  const behind = renderBody("subway", servedAt(fx.FROZEN_S - 30, 60));
  assert.deepEqual(popupQualifiers(behind.popup), Array(3).fill("as of 90s ago"));
  assert.deepEqual(panelQualifiers(behind.panel), Array(3).fill("as of 90s ago"));
});

test("6.2 Metro-North's stale poll: the line speaks once, the clause rides it, the rows stay silent", () => {
  const S = loadFrontend().sandbox;
  const body = boards(S).mnr.body;
  body.fetched_at = fx.FROZEN_S - 400;
  body.systems.MNR.fetched_at = fx.FROZEN_S - 400;
  const out = renderBody("mnr", body);
  // The rider's word for the system, never the feed code: the panel SPEAKS this line,
  // and "MNR" would be read letter by letter.
  const line = "as of 7m ago; Metro-North prediction age unavailable";
  // MR5: the board line sits under the title, where the head used to be followed by it directly.
  assert.ok(out.popup.startsWith(kicker("MNR") + title("Grand Central") + `<div class="popup-stale">${line}</div>\n`), out.popup);
  assert.deepEqual(popupQualifiers(out.popup), []);
  assert.deepEqual(out.panel.slice(0, 2), ["h3 Grand Central (Metro-North)", `p.station-detail-stale ${line}`]);
  assert.deepEqual(panelQualifiers(out.panel), ["", ""]);
  assert.ok(out.spoken.startsWith(`Grand Central, Metro-North. ${line}. Inbound:`), out.spoken);
});

test("6.2 UNDATED_SYSTEMS is exactly the railroad systems the backend never dates", () => {
  // The frontend's one statement of the policy's non-gated rows, held against the
  // backend's one statement of it rather than restating Metro-North a second time.
  const src = readFileSync(join(__dirname, "..", "backend", "feeds", "railroad.py"), "utf8");
  const feeds = src.match(/^RAILROAD_FEED_URLS = \{([\s\S]*?)^\}/m);
  const admitted = src.match(/^RAILROAD_FRESHNESS_SYSTEMS = frozenset\(\{([^}]*)\}\)/m);
  assert.ok(feeds && admitted, "both statements are where this test reads them");
  const systems = [...feeds[1].matchAll(/^\s*"([A-Za-z]+)":/gm)].map((m) => m[1]).sort();
  const dated = [...admitted[1].matchAll(/"([A-Za-z]+)"/g)].map((m) => m[1]);
  assert.deepEqual(systems, ["LIRR", "MNR"]);
  assert.deepEqual([...H.UNDATED_SYSTEMS].sort(), systems.filter((x) => !dated.includes(x)));
});
