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
/* `S` IS THE SANDBOX AND `H` IS THE MODULE, AND THE DIFFERENCE IS NOT COSMETIC. A `const` declared at
   the top level of a script run in a vm context does NOT become a property of that context, so
   `H.FERRY_FALLBACK_COLOR` is `undefined` while `S.pathColor` (a function declaration) is the real
   thing. Measured, after ruling R3's kicker put the ferry's fallback on the page for the first time:
   the SV route has no row in the routes fixture, so its hull was drawn `fill="undefined"`. Every
   CONSTANT here comes from `H`, the same file required as a module, and every FUNCTION from `S`, the
   loaded page. */
function boards(S) {
  const railroadNames = new Map(fx.railroadRoutes().map((r) => [`${r.system}|${r.route}`, r.name]));
  const railroadName = (system) => (r) => railroadNames.get(`${system}|${r}`) || null;
  /* R1: THE BADGE'S PAINT, KEYED THE WAY THE LOADER KEYS IT. systems/railroad.js closes
     railroadBranchPaint over the (system|route_id) colour table; this is the fixture's own copy of
     that closure, built from the same served rows, because this file's contract is that each board
     is called with the arguments its loader passes. Defaulting it instead would render the rail
     neutral and the two pins below would fail for the wrong reason. */
  const railroadPaints = new Map(fx.railroadRoutes().map((r) => [`${r.system}|${r.route}`, r]));
  const railroadPaint = (system) => (r) => {
    const row = railroadPaints.get(`${system}|${r}`);
    return S.railBranchPaint(row?.color ?? null, row?.text_color ?? null);
  };
  /* R3: THE KICKER'S ROUTE MARKS, one resolver per family, each the fixture's copy of what its
     loader closes over. The boards below pass them, because this file's contract is that a board is
     called with the arguments its loader passes: defaulting them would leave four kickers empty and
     the four new pins would pin nothing, which is the trap the fixture's own `routes` comment
     names. */
  const railroadMark = (system) => (r) => {
    const row = railroadPaints.get(`${system}|${r}`);
    return {
      svg: S.railRouteTagSvg({
        system,
        code: S.railBranchCode(system, r, row?.name ?? null),
        color: row?.color ?? null,
        textColor: row?.text_color ?? null,
      }),
      name: row?.name ?? r,
    };
  };
  const njtMark = (r) => {
    const paint = njt.paints.get(r);
    return {
      svg: S.railRouteTagSvg({
        system: "NJT",
        code: S.railBranchCode("NJT", r, njt.names.get(r) ?? null, njt.shortNames.get(r) ?? null),
        color: paint?.color ?? null,
        textColor: paint?.textColor ?? null,
      }),
      name: njt.names.get(r) || r,
    };
  };
  const pathMark = (r) => ({
    svg: S.pathDiamondSvg(pathColors.get(r) ?? H.PATH_FALLBACK_COLOR),
    name: pathNames.get(r) || r,
  });
  const ferryMark = (r) => ({
    svg: S.ferryHullSvg(ferryColors.get(r) ?? H.FERRY_FALLBACK_COLOR),
    name: ferryNames.get(r) || r,
  });
  const pathColors = new Map(fx.pathRoutes().map((r) => [r.id, S.pathColor(r.color)]));
  const pathNames = new Map(fx.pathRoutes().map((r) => [r.id, r.name]));
  const njt = S.njtRouteTables(fx.njtRoutes());
  const ferryColors = new Map(
    fx.ferryRoutes().map((r) => [r.id, S.pathColor(r.color, H.FERRY_FALLBACK_COLOR)]),
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
      popup: (b) =>
        S.railroadArrivalsHtml(lirr, b, now, railroadPaint(lirr.system), railroadName(lirr.system), "", railroadMark(lirr.system)),
      entry: {
        key: `${lirr.system}|${lirr.id}`, kind: "railroad", systemLabel: "LIRR", noun: "train",
        id: lirr.id, system: lirr.system, name: lirr.name, routes: lirr.routes ?? [], wheelchair: false,
        arrivalsUrl: `/api/railroad-arrivals/${lirr.system}/${lirr.id}`,
        nameFor: railroadName(lirr.system),
        // R1: the panel's chip resolver, which registerStation carries now. R3 gave the stops
        // fixture the `routes` the endpoint has always served, so this draws a chip in the panel
        // rather than nothing: the comment here used to say it rendered nothing in any world.
        colorFor: (r) => railroadPaint(lirr.system)(r).fill,
      },
    },
    mnr: {
      body: fx.railroadArrivals(),
      popup: (b) =>
        S.railroadArrivalsHtml(mnr, b, now, railroadPaint(mnr.system), railroadName(mnr.system), "", railroadMark(mnr.system)),
      entry: {
        key: `${mnr.system}|${mnr.id}`, kind: "railroad", systemLabel: "Metro-North", noun: "train",
        id: mnr.id, system: mnr.system, name: mnr.name, routes: mnr.routes ?? [], wheelchair: false,
        arrivalsUrl: `/api/railroad-arrivals/${mnr.system}/${mnr.id}`,
        nameFor: railroadName(mnr.system),
        // R1: the panel's chip resolver, which registerStation carries now. R3 gave the stops
        // fixture the `routes` the endpoint has always served, so this draws a chip in the panel
        // rather than nothing: the comment here used to say it rendered nothing in any world.
        colorFor: (r) => railroadPaint(mnr.system)(r).fill,
      },
    },
    path: {
      body: fx.pathArrivals(),
      popup: (b) =>
        S.pathArrivalsHtml(
          path,
          b,
          now,
          (r) => pathColors.get(r) ?? H.PATH_FALLBACK_COLOR,
          (r) => pathNames.get(r) || null,
          "",
          pathMark,
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
          // R1: the published colour the tag draws with, mirroring systems/njt.js's board resolver.
          (r) => S.railBranchColor(njt.paints.get(r)?.color ?? null),
          (r) => S.njtRouteName(r, njt.names),
          "",
          njtMark,
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
        S.ferryArrivalsHtml(
          dock,
          b,
          now,
          (r) => ferryColors.get(r) ?? H.FERRY_FALLBACK_COLOR,
          undefined,
          "",
          ferryMark,
        ),
      entry: {
        key: `ferry|${dock.id}`, kind: "ferry", systemLabel: "Ferry", noun: "boat",
        id: dock.id, name: dock.name, routes: dock.routes ?? [], wheelchair: dock.wheelchair === true,
        arrivalsUrl: `/api/ferry-arrivals/${dock.id}`, nameFor: (r) => ferryNames.get(r) || null,
      },
    },
  };
}

// Render one board on both surfaces, in a fresh context: the popup's markup, the
// panel's detail area, and what the panel's live region said on selection. `body`
// replaces the fixture's board where given, and `error` stands in the panel's
// failed-fetch path (what a non-200's `detail` became), with no body and no popup.
// `station` puts a subway board at another station (its id, name and routes) on both
// surfaces, the way the loader would register it: the F8 probe needs a station two groups
// serve, and the harness's Times Sq is served by one.
function render(name, { body, error = null, station = null } = {}) {
  const h = loadFrontend();
  const board = boards(h.sandbox)[name];
  const served = error === null ? (body ?? board.body) : null;
  const popup =
    served === null ? "" : station ? h.sandbox.subwayArrivalsHtml(station, served) : board.popup(served);
  h.sandbox.__entry = station
    ? {
        ...board.entry,
        key: `subway|${station.id}`,
        id: station.id,
        name: station.name,
        routes: station.routes,
        arrivalsUrl: `/api/subway-arrivals/${station.id}`,
      }
    : board.entry;
  h.sandbox.__body = served;
  h.sandbox.__error = error;
  h.run(
    "panelStation = __entry; panelBody = __body; panelError = __error;" +
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

   IMPORTED RATHER THAN COPIED, and the first version of this file copied it. One now, and the
   browser tier's markup pins and these read a mark the same way.

   FROM marktoken.js, NOT popup.js, which is where round 2 pointed it and where CI found the hole.
   popup.js was picked on the claim that it "requires nothing itself"; it requires @playwright/test,
   and the frontend-tests job runs `node --test` with no `npm ci`, so this line threw at load and
   took all thirteen tests in this file with it while a local run stayed green. The reader now sits
   in a file that requires nothing, and tests/nodetier.test.js holds that of the whole closure so
   the next import cannot make the same hole quietly. */
const { withoutMarks } = require("../tests/e2e/marktoken.js");

// The subway badge colors, spelled once so the popup literals stay readable.
const RED = 'style="background:#c0392b;color:#ffffff"';

/* AND THE TWO RAILROADS' OWN, WHICH IS ONE CONSTANT SPLIT INTO TWO AND IS RULING R1 STATED AS A
   DIFF. Both boards' badges used to read `background:#5d4037;color:#ffffff`, one brown from one
   shared constant, because the resolver behind them hashed the ROUTE ID and never saw the system:
   LIRR route 1 is the Babylon Branch and MNR route 1 is the Hudson Line, two agencies publishing
   two different greens, and the app drew them the same. The two literals below cannot be folded
   back into one without re-creating that defect, and their inks are the published ones (both
   agencies serve route_text_color, and railBranchPaint prefers it where it is legible). */
const BABYLON = 'style="background:#00985F;color:#1a1a1a"';
const HUDSON = 'style="background:#009B3A;color:#1a1a1a"';

/* ONE OF Times Sq's KICKER PLATES, as withoutMarks prints it: the route the plate carries, the size
   the popup drew it at, and the fills the plate declares, in the order the SVG lists them (the paper
   backing, the rounded square, then the numeral). 17 is POPUP_MARK_ROW, the design's small mark, and
   it is a literal here on purpose: the constant's own value is pinned in frontend/popupvocab.test.js,
   so if a later stage draws a kicker's plates larger these pins say so rather than following along.

   THE LAST TWO FILLS ARE THE PAIR RED SPELLS ABOVE, which is the reason they are in the token at all:
   the kicker's plates and the arrival badges below both ask lineColor for the 1 train and both ink
   against it, so a plate whose paint stopped agreeing with its badge is a defect the reader of a
   popup can see, and a token that said only [mark 1] could not fail on it. The first is the backing
   the plate draws under itself, which entered the token when ruling R3 taught withoutMarks to read a
   fill declared in a `style` attribute: without that the PATH diamond and the ferry hull, which
   declare theirs only that way, carried no colour in their tokens at all. */
const plate = (route) => `[mark ${route} 17x17 var(--paper),#c0392b,#ffffff]`;
/* AND THE WORDS UNDER THE MARKS (ruling R3), which every kicker with routes in it now carries: the
   marks are aria-hidden, so without this span a rider who cannot see them learns nothing about which
   routes call at the station. It is the station panel's own pattern for its route chips and its access
   glyph, and A1's `.visually-hidden` is the class. The list is every route, not the shown ones: the
   count beside the marks is what an eye reads for the rest. */
const spokenRoutes = (...names) => `<span class="visually-hidden">${names.join(", ")}</span>`;
const TIMES_SQ_PLATES = [1, 2, 3].map(plate).join("") + spokenRoutes(1, 2, 3);

/* THE OTHER FOUR FAMILIES' KICKER MARKS, as withoutMarks prints them, one literal per served route
   in the fixtures. Each is that family's own map mark at the same 17: a BODY-ONLY rail tag (its
   agency glyph and its branch code, the two <text> nodes joined with a middle dot, and the four
   paints its two blocks and two letters declare), a PATH diamond, a ferry hull.

   THE WIDTHS ARE THE TAG'S OWN ARITHMETIC and they differ per code length, which is the measurement
   that set the shared cap at three: 45.77 for a three-letter railroad code, 52.31 and 66.69 for NJ
   Transit's NEC and MNBTN. A hull is 26.71 and a diamond, like a plate, is 17. */
const railTag = (glyph, code, width, fill, ink) =>
  `[mark ${glyph}·${code} ${width}x17 var(--ink),${fill},var(--paper),${ink}]`;
const BABYLON_TAG = railTag("L", "BAB", 45.77, "#00985F", "#1a1a1a");
const HUDSON_TAG = railTag("M", "HUD", 45.77, "#009B3A", "#1a1a1a");
const NEC_TAG = railTag("NJ", "NEC", 52.31, "#DD3439", "#ffffff");
const MNBTN_TAG = railTag("NJ", "MNBTN", 66.69, "#E66859", "#1a1a1a");
const diamond = (fill) => `[mark 17x17 ${fill}]`;
// The hull's second declared paint is its `none` stroke, which is part of what the mark declares.
const hull = (fill) => `[mark 26.71x17 ${fill},none]`;

/* MR5: SECTION 5's GRAMMAR, AS FOUR TEMPLATES, so six board pins stay readable after the popup
   became a kicker, a title and a three-cell grid. These are literal templates written HERE, in the
   same spirit as RED and the two rail greens above: a production builder that stopped emitting `class="n"`, or
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
    kicker("LIRR", BABYLON_TAG + spokenRoutes("Babylon Branch")) +
      title("Jamaica") +
      dir("Inbound") +
      arr(row(`<span class="arr-badge" ${BABYLON}>1</span>`, 'Babylon Branch <span class="popup-sub">#8412</span>', "4 min")) +
      dir("Outbound") +
      arr(row(`<span class="arr-badge" ${BABYLON}>1</span>`, 'Babylon Branch <span class="popup-sub">#8413</span>', "7 min")),
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
    kicker("MNR", HUDSON_TAG + spokenRoutes("Hudson")) +
      title("Grand Central") +
      dir("Inbound") +
      arr(row(`<span class="arr-badge" ${HUDSON}>1</span>`, 'Hudson <span class="popup-sub">#795</span>', "4 min")) +
      dir("Outbound") +
      arr(row(`<span class="arr-badge" ${HUDSON}>1</span>`, 'Hudson <span class="popup-sub">#812</span>', "6 min")),
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
    kicker(
      "PATH",
      diamond("#d93a30") + diamond("#4d92fb") + spokenRoutes("Newark - World Trade Center", "Hoboken - 33rd"),
    ) +
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
    kicker("NJ Transit", NEC_TAG + MNBTN_TAG + spokenRoutes("Northeast Corridor", "Montclair-Boonton Line")) +
      title("New York Penn Station") +
      arr(
        row('<span class="arr-badge" style="background:#DD3439;color:#ffffff">9</span>', 'Trenton <span class="popup-sub">3800</span>', "2 min"),
        row('<span class="arr-badge" style="background:#E66859;color:#1a1a1a">2</span>', 'Dover <span class="popup-sub">6634</span>', "5 min"),
        // THE ROUTE-LESS ROW, WHICH IS WHERE R1's SECOND NEUTRAL LEAVES BY VALUE. This arrival
        // carries no route_id, so its badge draws the fallback: #4a4e69 (njtColor's, phase 15c's)
        // until ruling R1, and the rail families' own #6d6e71 now, which is what the tag beside it
        // on the map has always drawn for a route the routes endpoint does not carry.
        row('<span class="arr-badge" style="background:#6d6e71;color:#ffffff">?</span>', "Bay Head", "8 min"),
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
    /* THE ONE KICKER WHOSE RIGHT SLOT WAS ALREADY OCCUPIED: the routes' hulls, then the words, then
       the access glyph, which stays rightmost where it has always been. SV is the third route this
       dock serves and the routes fixture does not carry it, so its hull is the ferry's published
       fallback and its spoken name is its id: the one board here that renders a family's fallback. */
    kicker(
      "NYC Ferry",
      hull("#00839c") + hull("#ffd100") + hull("#78909c") +
        spokenRoutes("East River", "South Brooklyn", "SV") +
        '<span class="popup-access" title="Wheelchair accessible">&#9855;</span>',
    ) +
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
  assert.ok(
    out.popup.startsWith(
      kicker("MNR", HUDSON_TAG + spokenRoutes("Hudson")) +
        title("Grand Central") +
        `<div class="popup-stale">${line}</div>\n`,
    ),
    out.popup,
  );
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

/* Q10 OF THE FRESHNESS CONTRACT'S AMENDMENT 1, AND H6's RIDER HALF, RENDERED. A single-system
   board, and a railroad board with both railroads failing, used to answer a failed FIRST poll with
   HTTP 502 and `{"detail": ...}`: the upstream exception's own text with only its URLs scrubbed,
   which the panel printed and spoke (and the popup printed), so a reason phrase or a response body
   the UPSTREAM chose reached a rider. The bodies here are what the backend serves now, from
   backend/tests/test_q10_first_poll_boards.py, which drives each family's real first poll against
   a stub carrying one marker in its reason phrase and one in its body and holds the served boards
   and the operator's copy of each failure to tests/e2e/fixtures/q10_first_poll_boards.json.

   THE CONTROL FIRST: the operator's detail, through the panel's failed-fetch path, IS read out, so
   this harness sees a marker when one reaches a rider. Then the board: neither marker is anywhere
   in it, and every surface says the system is not reporting (Amendment 1's Q3 and Q11, Step 3),
   in the slot "No trains" held while no renderer read the never-decoded block (Step 2's interim,
   its adversarial round's F1, which the two steps landing together closed). A subway board names
   the routes its dark group serves at the station, through the route-to-group table. */
test("Q10 a failed first poll reaches the rider as a board, never as the upstream's own text", () => {
  const served = JSON.parse(
    readFileSync(join(__dirname, "..", "tests", "e2e", "fixtures", "q10_first_poll_boards.json"), "utf8"),
  );
  const markers = ["Q10-REASON-PHRASE-MARKER", "Q10-RESPONSE-BODY-MARKER"];
  const expected = {
    subway: ["Times Sq-42 St (Subway)", "1, 2 and 3 trains not reporting"],
    path: ["World Trade Center (PATH)", "PATH not reporting"],
    ferry: ["Wall St/Pier 11 (Ferry)", "NYC Ferry not reporting"],
    njt: ["New York Penn Station (NJ Transit)", "NJ Transit not reporting"],
    lirr: ["Jamaica (LIRR)", "LIRR not reporting"],
  };
  assert.deepEqual(Object.keys(served).sort(), ["ferry", "lirr", "njt", "path", "subway"]);
  for (const [family, [heading, words]] of Object.entries(expected)) {
    const { board, detail } = served[family];
    const station = heading.replace(/ \(.*\)$/, "");
    const label = heading.match(/\((.*)\)$/)[1];

    const before = render(family, { error: detail });
    assert.deepEqual(before.panel.at(-1), `p.station-detail-note ${detail}`, family);
    assert.equal(before.spoken, `${station}, ${label}. ${detail}`, family);
    assert.ok(markers.some((m) => before.spoken.includes(m)), `${family}: the control sees a marker`);

    const after = render(family, { body: board });
    assert.equal(after.spoken, `${station}, ${label}. ${words}.`, family);
    assert.equal(after.panel[0], `h3 ${heading}`, family);
    assert.equal(after.panel.at(-1), `p ${words}.`, family);
    // The subway's popup has a slot per direction; every other board, one.
    const slot = `<div class="arr-none">${words}</div>\n`;
    assert.ok(
      family === "subway"
        ? after.popup.endsWith(`${dir("Northbound")}${slot}${dir("Southbound")}${slot}`)
        : after.popup.endsWith(`<div class="pt"><span>${station}</span></div>\n${slot}`),
      after.popup,
    );
    for (const surface of [after.popup, after.panel.join("\n"), after.spoken]) {
      for (const marker of markers) assert.ok(!surface.includes(marker), `${family}: ${marker}`);
    }
  }
});

/* A03's FOUR BOARDS, the auditor's acceptance for the frontend half (Amendment 1, A1.6 Step 3):
   "Regression cases must distinguish genuinely fresh empty service from stale, failed and unknown
   empty service in both surfaces." Times Sq, the harness's subway station, whose routes 1, 2 and 3
   expect the 1-7+S group, served empty at the frozen instant T in four states of that one block,
   section (d) of the A03 witness in this file's terms: FRESH, A (content 620 s old behind a poll
   0 s old), B (never decoded) and C (failed, last decoded 200 s before). */
const T = fx.FROZEN_S;
const timesSq = (block) => ({
  fetched_at: block.fetched_at,
  feed_timestamp: block.feed_timestamp,
  station_id: "127",
  station_name: "Times Sq-42 St",
  directions: { Northbound: [], Southbound: [] },
  served_at: T,
  systems: { "1-7+S": { retained_since: null, routes: [], ...block } },
});
const FRESH_BLOCK = { fetched_at: T, feed_timestamp: T - 5, ok: true };
const STALE_BLOCK = { fetched_at: T, feed_timestamp: T - 620, ok: true };
const NEVER_BLOCK = { fetched_at: null, feed_timestamp: null, ok: false, retained_since: T - 20 };
const failedBlock = (ago) => ({ fetched_at: T - ago, feed_timestamp: T - ago - 5, ok: false, retained_since: T - ago + 20 });
const emptyTimesSq = (slot, line = "") =>
  kicker("Subway", TIMES_SQ_PLATES) +
  title("Times Sq-42 St") +
  (line ? `<div class="popup-stale">${line}</div>\n` : "") +
  `${dir("Northbound")}<div class="arr-none">${slot}</div>\n${dir("Southbound")}<div class="arr-none">${slot}</div>\n`;

test("A03 the fresh empty board reads exactly as it did before Amendment 1", () => {
  const out = render("subway", { body: timesSq(FRESH_BLOCK) });
  assert.equal(out.popup, emptyTimesSq("No trains"));
  assert.deepEqual(out.panel, ["h3 Times Sq-42 St (Subway)", "p No trains."]);
  assert.equal(out.spoken, "Times Sq-42 St, Subway. No trains.");
  // And a board with no systems map at all (an older backend) reads the same: its substitute
  // block is ok with a current clock, never "not reporting".
  const old = render("subway", { body: { ...timesSq(FRESH_BLOCK), systems: null } });
  assert.deepEqual([old.popup, old.panel, old.spoken], [out.popup, out.panel, out.spoken]);
});

test("A03 boards A, B and C each read differently from the fresh one, on every surface", () => {
  const fresh = render("subway", { body: timesSq(FRESH_BLOCK) });
  const a = render("subway", { body: timesSq(STALE_BLOCK) });
  const b = render("subway", { body: timesSq(NEVER_BLOCK) });
  const c = render("subway", { body: timesSq(failedBlock(200)) });
  // A, stale: the line, by the worse of poll and content age (Q4), above "No trains".
  assert.equal(a.popup, emptyTimesSq("No trains", "as of 10m ago"));
  assert.deepEqual(a.panel, ["h3 Times Sq-42 St (Subway)", "p.station-detail-stale as of 10m ago", "p No trains."]);
  assert.equal(a.spoken, "Times Sq-42 St, Subway. as of 10m ago. No trains.");
  // B and C, never decoded and failed: the slot names the routes the dark group serves here (Q3),
  // in one word for both (Q11), with no line.
  for (const out of [b, c]) {
    assert.equal(out.popup, emptyTimesSq("1, 2 and 3 trains not reporting"));
    assert.deepEqual(out.panel, ["h3 Times Sq-42 St (Subway)", "p 1, 2 and 3 trains not reporting."]);
    assert.equal(out.spoken, "Times Sq-42 St, Subway. 1, 2 and 3 trains not reporting.");
  }
  for (const out of [a, b, c]) {
    assert.notEqual(out.popup, fresh.popup);
    assert.notDeepEqual(out.panel, fresh.panel);
    assert.notEqual(out.spoken, fresh.spoken);
  }
  // B and C share the rider's word and differ in the served field, as the board's own reading of
  // each block tells them apart: never decoded, ageless, against failed, its last decode 200 s old.
  const { sandbox } = loadFrontend();
  const state = (block) => {
    const [c] = sandbox.boardFreshness("subway", timesSq(block), T, ["1", "2", "3"]).contributors;
    return [c.state, c.age];
  };
  assert.deepEqual([state(NEVER_BLOCK), state(failedBlock(200))], [["never", null], ["failed", 200]]);
});

test("A03 a failed contributor is news from 90 s after its last decode, and not before", () => {
  // THE GRACE (Q11), one failed poll is not news on a board any more than on the status line. Its
  // content is dated at its last decode here, so nothing but the failure could raise a line.
  const failed = (ago) => ({ ...failedBlock(ago), feed_timestamp: T - ago });
  assert.equal(render("subway", { body: timesSq(failed(89)) }).spoken, "Times Sq-42 St, Subway. No trains.");
  assert.equal(
    render("subway", { body: timesSq(failed(90)) }).spoken,
    "Times Sq-42 St, Subway. 1, 2 and 3 trains not reporting.",
  );
});

test("A03 a contributor failing inside the grace keeps its content's age on an empty board", () => {
  // Board A (content 620 s old behind a current poll) reads "as of 10m ago"; its NEXT poll fails,
  // 20 s later. Inside the grace it is not yet dark, and its content is now 640 s old: the board
  // keeps dating it ("as of 11m ago", 640 s), rather than going back to reading as fresh empty
  // service until 90 s after the failure (the adversarial round of Step 3, its F1).
  const next = { fetched_at: T - 20, feed_timestamp: T - 640, ok: false, retained_since: T };
  const out = render("subway", { body: timesSq(next) });
  assert.equal(out.popup, emptyTimesSq("No trains", "as of 11m ago"));
  assert.equal(out.spoken, "Times Sq-42 St, Subway. as of 11m ago. No trains.");
  // Content 89 s old and its poll failed 50 s ago: under the band on both clocks, so nothing.
  const quiet = { fetched_at: T - 50, feed_timestamp: T - 89, ok: false, retained_since: T - 30 };
  assert.equal(render("subway", { body: timesSq(quiet) }).spoken, "Times Sq-42 St, Subway. No trains.");
});

test("A03 a dark group with no row is named on a board that has other rows, by the routes it serves here", () => {
  // Forest Hills-71 Av's real routes (E, F, FX, M, R): ACE has a row, BDFM is current with none,
  // NQRW has never decoded. The line names R, the one route of the dark group at this station.
  const h = loadFrontend();
  const station = { id: "G08", name: "Forest Hills-71 Av", routes: ["E", "F", "FX", "M", "R"] };
  const block = (b) => ({ retained_since: null, routes: [], ...b });
  const body = {
    fetched_at: null,
    feed_timestamp: null,
    station_id: "G08",
    station_name: "Forest Hills-71 Av",
    directions: {
      Northbound: [{ route_id: "E", trip_id: "e1", arrival: T + 120, observed_at: T - 5, provenance: "reported" }],
      Southbound: [],
    },
    served_at: T,
    systems: {
      ACE: block({ fetched_at: T, feed_timestamp: T - 5, ok: true, routes: ["E"] }),
      BDFM: block({ fetched_at: T, feed_timestamp: T - 5, ok: true }),
      NQRW: block({ fetched_at: null, feed_timestamp: null, ok: false }),
    },
  };
  const popup = withoutMarks(h.sandbox.subwayArrivalsHtml(station, body));
  assert.ok(popup.includes('<div class="popup-stale">R trains not reporting</div>\n'), popup);
  // The empty direction keeps "No trains": not every contributor is dark.
  assert.ok(popup.includes(`${dir("Southbound")}<div class="arr-none">No trains</div>\n`), popup);

  // A DARK GROUP WITH A ROW HERE SAYS NOTHING: its row speaks for it. ACE failed 200 s ago and its
  // block lists no route (the cap emptied its drawn trains), yet the E row is ACE's by the
  // route-to-group table, so no clause is added; with NQRW healthy too the board has no line.
  const held = {
    ...body,
    systems: {
      ACE: block({ fetched_at: T - 200, feed_timestamp: T - 205, ok: false }),
      BDFM: block({ fetched_at: T, feed_timestamp: T - 5, ok: true }),
      NQRW: block({ fetched_at: T, feed_timestamp: T - 5, ok: true }),
    },
  };
  const rows = [...held.directions.Northbound];
  const board = h.sandbox.boardFreshness("subway", held, T, station.routes);
  assert.deepEqual({ ...h.sandbox.boardLine(board, rows) }, { words: null, kind: "", slot: null });
  assert.ok(!withoutMarks(h.sandbox.subwayArrivalsHtml(station, held)).includes("not reporting"));
});

test("A03 an empty board with one dark group and one healthy keeps No trains and names the dark one", () => {
  // W 4 St-Wash Sq (A, C, E, B, D, F, M): ACE never decoded, BDFM current and empty. Not every
  // contributor is dark, so the slot stays "No trains" (Q3 takes it only when every one is) and
  // the line names the dark group's routes, never the healthy B, D, F and M.
  const h = loadFrontend();
  const station = { id: "A32", name: "W 4 St-Wash Sq", routes: ["A", "C", "E", "B", "D", "F", "M"] };
  const body = {
    fetched_at: null,
    feed_timestamp: null,
    station_id: "A32",
    station_name: "W 4 St-Wash Sq",
    directions: { Northbound: [], Southbound: [] },
    served_at: T,
    systems: {
      ACE: { fetched_at: null, feed_timestamp: null, ok: false, retained_since: T - 20, routes: [] },
      BDFM: { fetched_at: T, feed_timestamp: T - 5, ok: true, retained_since: null, routes: [] },
    },
  };
  const board = h.sandbox.boardFreshness("subway", body, T, station.routes);
  assert.deepEqual(
    { ...h.sandbox.boardLine(board, []) },
    { words: "A, C and E trains not reporting", kind: "dark:A, C and E trains", slot: null },
  );
  const popup = withoutMarks(h.sandbox.subwayArrivalsHtml(station, body));
  assert.ok(popup.includes('<div class="popup-stale">A, C and E trains not reporting</div>\n'), popup);
  assert.ok(popup.includes(`${dir("Southbound")}<div class="arr-none">No trains</div>\n`), popup);
});

test("A03 a change in WHO is dark is announced, an age counting up is not", () => {
  // Forest Hills-71 Av: NQRW dark ("R trains"), then BDFM dark as well ("F, FX, M and R trains"),
  // then NQRW back with BDFM still dark ("F, FX and M trains"). Each is news to a rider on a screen
  // reader; the kind carries who is dark, so announcementWorthy hears it (the adversarial round of
  // Step 3, its F2). The aged line counting from 10m to 11m changes no kind and is not news.
  const h = loadFrontend();
  const routes = ["E", "F", "FX", "M", "R"];
  const row = { route_id: "E", trip_id: "e1", arrival: T + 120, observed_at: T - 5, provenance: "reported" };
  const ok = { fetched_at: T, feed_timestamp: T - 5, ok: true, retained_since: null, routes: [] };
  const down = { fetched_at: T - 200, feed_timestamp: T - 205, ok: false, retained_since: null, routes: [] };
  const line = (bdfm, nqrw) => {
    const body = { served_at: T, systems: { ACE: { ...ok, routes: ["E"] }, BDFM: bdfm, NQRW: nqrw } };
    return h.sandbox.boardLine(h.sandbox.boardFreshness("subway", body, T, routes), [row]);
  };
  const lines = [line(ok, down), line(down, down), line(down, ok)];
  assert.deepEqual(
    lines.map((l) => [l.words, l.kind]),
    [
      ["R trains not reporting", "dark:R trains"],
      ["F, FX, M and R trains not reporting", "dark:F, FX, M and R trains"],
      ["F, FX and M trains not reporting", "dark:F, FX and M trains"],
    ],
  );
  const shaped = (l) => ({ systemLine: l.words, systemLineKind: l.kind, slot: "No trains", buckets: [] });
  assert.equal(h.sandbox.announcementWorthy(shaped(lines[0]), shaped(lines[1])), true);
  assert.equal(h.sandbox.announcementWorthy(shaped(lines[1]), shaped(lines[2])), true);
  const aged = (content) =>
    h.sandbox.boardLine(
      h.sandbox.boardFreshness("subway", { served_at: T, systems: { ACE: { ...ok, feed_timestamp: T - content } } }, T, routes),
      [],
    );
  const [ten, eleven] = [aged(600), aged(660)];
  assert.deepEqual([ten.words, eleven.words, ten.kind, eleven.kind], ["as of 10m ago", "as of 11m ago", "aged", "aged"]);
  assert.equal(h.sandbox.announcementWorthy(shaped(ten), shaped(eleven)), false);
});

test("A03 a fresh empty ferry dock reads No boats on the panel and the spoken board", () => {
  // The panel's slot comes from shapeStationArrivals' `slot`, which takes the dock's noun from its
  // registry entry: a ferry dock reads "No boats", never "No trains", on every surface.
  const body = {
    ...fx.ferryArrivals(),
    routes: {},
    served_at: T,
    systems: { ferry: { fetched_at: T, feed_timestamp: T - 5, ok: true, retained_since: null, routes: null } },
  };
  const out = render("ferry", { body });
  assert.equal(out.panel.at(-1), "p No boats.");
  assert.equal(out.spoken, "Wall St/Pier 11, Ferry. No boats.");
  assert.ok(out.popup.endsWith('<div class="arr-none">No boats</div>\n'), out.popup);
});

test("A03 every other family's failed board names its system after the grace", () => {
  // PATH failed 200 s before, and NJ Transit's content 620 s old behind a current poll.
  const path = fx.pathArrivals();
  const pathBody = {
    ...path,
    directions: {},
    feed_timestamp: null,
    served_at: T,
    systems: { PATH: { fetched_at: T - 200, feed_timestamp: null, ok: false, retained_since: null, routes: null } },
  };
  assert.equal(render("path", { body: pathBody }).spoken, "World Trade Center, PATH. PATH not reporting.");
  // The same failed block holding a kept row: the row speaks for itself, and no clause is added
  // (one system's board: any row is its).
  const kept = { ...pathBody, directions: path.directions };
  const keptBoard = loadFrontend().sandbox.boardFreshness("path", kept, T);
  assert.deepEqual(
    { ...loadFrontend().sandbox.boardLine(keptBoard, Object.values(path.directions).flat()) },
    { words: null, kind: "", slot: null },
  );
  assert.ok(!render("path", { body: kept }).spoken.includes("not reporting"));
  const njt = fx.njtArrivals();
  const njtBody = {
    ...njt,
    arrivals: [],
    served_at: T,
    systems: { njt: { fetched_at: T, feed_timestamp: T - 620, ok: true, retained_since: null, routes: null } },
  };
  assert.equal(
    render("njt", { body: njtBody }).spoken,
    "New York Penn Station, NJ Transit. as of 10m ago. No trains.",
  );
});

/* F8, THE RULING OF 2026-11-08 (A1.3 amended): ON A BOARD WITH ROWS, A GROUP THAT IS STALE AND HAS NO
   ROW HERE IS NAMED, "{routes} trains as of {age} ago". A1.3 dated a stale contributor on an EMPTY
   board only, and named a dark one on any board, so at a station two groups serve, a group answering
   every poll with content 45 minutes old whose predictions here had all departed was not mentioned
   while the other group's row was on the board: its rows were gone, so no row's qualifier dated it,
   and the 2 train read as the whole board (the adversarial round of Step 3, its F8, recorded on the
   2026-10-12 ledger's live list). THE LEDGER'S PROBE, COMMITTED: routes 2, 3, B and Q; the 1-7+S
   group fresh with one row, the 2 train; BDFM ok, its poll current and its content 2700 s old, with
   no row. A world rather than a stop: no station is served by exactly these four routes.

   THE LEDGER'S NOTE SAID THE SAME BDFM BLOCK FAILING WOULD READ "B and Q trains not reporting", and
   it reads "B trains not reporting": Q is NQRW's (SUBWAY_ROUTE_GROUPS), so BDFM serves only the B
   here. The ruling's example, "B and Q trains as of 45m ago", is what the board says when NQRW is
   stale with no row as well, which the second test below pins. */
const PROBE = { id: "F8", name: "Probe St", routes: ["2", "3", "B", "Q"] };
const PROBE_KICKER = kicker(
  "Subway",
  "[mark 2 17x17 var(--paper),#c0392b,#ffffff][mark 3 17x17 var(--paper),#c0392b,#ffffff]" +
    "[mark B 17x17 var(--paper),#d68910,#1a1a1a]" +
    '<span class="pmore" aria-hidden="true">+1</span>' +
    spokenRoutes(2, 3, "B", "Q"),
);
const PROBE_ROW = { route_id: "2", trip_id: "p2", arrival: T + 120, observed_at: T - 5, provenance: "reported" };
const probeBlock = (b) => ({ retained_since: null, routes: [], ...b });
const PROBE_GROUP = probeBlock({ fetched_at: T, feed_timestamp: T - 5, ok: true, routes: ["2"] });
const probeBody = (systems, rows = [PROBE_ROW]) => ({
  fetched_at: T,
  feed_timestamp: T - 5,
  station_id: "F8",
  station_name: "Probe St",
  directions: { Northbound: rows, Southbound: [] },
  served_at: T,
  systems: { "1-7+S": PROBE_GROUP, ...systems },
});
const probeLine = (systems, rows = [PROBE_ROW]) => {
  const { sandbox } = loadFrontend();
  const board = sandbox.boardFreshness("subway", probeBody(systems, rows), T, PROBE.routes);
  return { ...sandbox.boardLine(board, rows) };
};
const PROBE_ROWS_POPUP =
  `${dir("Northbound")}<div class="arr"><span><span class="arr-badge" ${RED}>2</span></span>\n<span></span>\n` +
  `<span class="n">2 min</span></div>\n${dir("Southbound")}<div class="arr-none">No trains</div>\n`;
const PROBE_ROW_SPOKEN = "Northbound: 2 train in 2 minutes, 8:02 AM arrival";
const AGED_BDFM = probeBlock({ fetched_at: T, feed_timestamp: T - 2700, ok: true });

test("F8 the ledger's probe: a stale group with no row on a populated board is named, on every surface", () => {
  const out = render("subway", { body: probeBody({ BDFM: AGED_BDFM }), station: PROBE });
  assert.equal(
    out.popup,
    `${PROBE_KICKER}${title("Probe St")}<div class="popup-stale">B trains as of 45m ago</div>\n${PROBE_ROWS_POPUP}`,
  );
  assert.deepEqual(out.panel, [
    "h3 Probe St (Subway)",
    "p.station-detail-stale B trains as of 45m ago",
    "h4 Northbound",
    "ul.station-arrivals",
    "  li 2 train in 2 minutes, 8:02 AM arrival",
  ]);
  assert.equal(out.spoken, `Probe St, Subway. B trains as of 45m ago. ${PROBE_ROW_SPOKEN}`);
  assert.deepEqual(probeLine({ BDFM: AGED_BDFM }), { words: "B trains as of 45m ago", kind: "aged:B trains", slot: null });

  // THE FRESH BOARD IS BYTE-IDENTICAL: the same group current, still with no row here, says nothing,
  // and the board reads exactly as the board with no BDFM block at all.
  const fresh = render("subway", { body: probeBody({ BDFM: probeBlock({ fetched_at: T, feed_timestamp: T - 5, ok: true }) }), station: PROBE });
  assert.equal(fresh.popup, `${PROBE_KICKER}${title("Probe St")}${PROBE_ROWS_POPUP}`);
  assert.deepEqual(fresh.panel, ["h3 Probe St (Subway)", "h4 Northbound", "ul.station-arrivals", "  li 2 train in 2 minutes, 8:02 AM arrival"]);
  assert.equal(fresh.spoken, `Probe St, Subway. ${PROBE_ROW_SPOKEN}`);
  const without = render("subway", { body: probeBody({}), station: PROBE });
  assert.deepEqual([without.popup, without.panel, without.spoken], [fresh.popup, fresh.panel, fresh.spoken]);
});

test("F8 two stale groups with no row are one clause, by the routes they serve here and the worse age", () => {
  // BDFM's content 2700 s old and NQRW's 2400 s: one population, one clause, the worst age answering
  // (45m, not 40m), the routes in the station's order. The ruling's own example.
  const NQRW = probeBlock({ fetched_at: T, feed_timestamp: T - 2400, ok: true });
  assert.deepEqual(probeLine({ BDFM: AGED_BDFM, NQRW }), {
    words: "B and Q trains as of 45m ago",
    kind: "aged:B and Q trains",
    slot: null,
  });
  const out = render("subway", { body: probeBody({ BDFM: AGED_BDFM, NQRW }), station: PROBE });
  assert.equal(out.spoken, `Probe St, Subway. B and Q trains as of 45m ago. ${PROBE_ROW_SPOKEN}`);
  assert.equal(out.panel[1], "p.station-detail-stale B and Q trains as of 45m ago");
});

test("F8 the band's two sides, on either clock: 89 s says nothing, 90 s names the group", () => {
  // The band test the empty board's clause reads (contributorAged): the WORSE of the poll's age and
  // the content's, against FEED_STALE_AFTER_S, 90 s, the boundary inclusive.
  const content = (age) => probeBlock({ fetched_at: T, feed_timestamp: T - age, ok: true });
  const poll = (age) => probeBlock({ fetched_at: T - age, feed_timestamp: T - 5, ok: true });
  assert.deepEqual(
    [content(89), content(90), poll(89), poll(90)].map((BDFM) => probeLine({ BDFM }).words),
    [null, "B trains as of 90s ago", null, "B trains as of 90s ago"],
  );
});

test("F8 a stale group beside a dark one: one clause each, the stale clause first", () => {
  // BDFM stale with no row, NQRW never decoded with no row, 1-7+S's row on the board.
  const NQRW = probeBlock({ fetched_at: null, feed_timestamp: null, ok: false });
  assert.deepEqual(probeLine({ BDFM: AGED_BDFM, NQRW }), {
    words: "B trains as of 45m ago; Q trains not reporting",
    kind: "aged:B trains+dark:Q trains",
    slot: null,
  });
  const out = render("subway", { body: probeBody({ BDFM: AGED_BDFM, NQRW }), station: PROBE });
  assert.equal(out.spoken, `Probe St, Subway. B trains as of 45m ago; Q trains not reporting. ${PROBE_ROW_SPOKEN}`);
});

test("F8 a stale group WITH a row here is unchanged: its row carries the age, the line says nothing", () => {
  const rowB = { route_id: "B", trip_id: "pb", arrival: T + 240, observed_at: T - 2700, provenance: "reported" };
  const rows = [PROBE_ROW, rowB];
  assert.deepEqual(probeLine({ BDFM: AGED_BDFM }, rows), { words: null, kind: "", slot: null });
  const out = render("subway", { body: probeBody({ BDFM: AGED_BDFM }, rows), station: PROBE });
  assert.deepEqual(out.panel, [
    "h3 Probe St (Subway)",
    "h4 Northbound",
    "ul.station-arrivals",
    "  li 2 train in 2 minutes, 8:02 AM arrival",
    "  li B train in 4 minutes, 8:04 AM arrival, as of 45m ago",
  ]);
});

test("F8 a group failing inside the grace keeps its age on a populated board, then reads not reporting", () => {
  // The same band test as the empty board's (contributorAged), so a stale group whose next poll
  // fails is not dropped from the line for the 90 s before it goes dark: 20 s into the failure it
  // is still dated by its content (2720 s, 45m), and from 90 s it reads "not reporting".
  const failing = (ago) => probeBlock({ fetched_at: T - ago, feed_timestamp: T - 2700 - ago, ok: false });
  assert.deepEqual(
    [failing(20), failing(89), failing(90)].map((BDFM) => [probeLine({ BDFM }).words, probeLine({ BDFM }).kind]),
    [
      ["B trains as of 45m ago", "aged:B trains"],
      ["B trains as of 46m ago", "aged:B trains"],
      ["B trains not reporting", "dark:B trains"],
    ],
  );
});

test("F8 the clause appearing is announced once, its age counting up is not, a second group is", () => {
  const { sandbox } = loadFrontend();
  const shaped = (l) => ({ systemLine: l.words, systemLineKind: l.kind, slot: "No trains", buckets: [] });
  const fresh = probeLine({ BDFM: probeBlock({ fetched_at: T, feed_timestamp: T - 5, ok: true }) });
  const at45 = probeLine({ BDFM: AGED_BDFM });
  const at46 = probeLine({ BDFM: probeBlock({ fetched_at: T, feed_timestamp: T - 2760, ok: true }) });
  const both = probeLine({ BDFM: AGED_BDFM, NQRW: probeBlock({ fetched_at: T, feed_timestamp: T - 2700, ok: true }) });
  assert.deepEqual(
    [fresh.words, at45.words, at46.words, both.words],
    [null, "B trains as of 45m ago", "B trains as of 46m ago", "B and Q trains as of 45m ago"],
  );
  assert.equal(sandbox.announcementWorthy(shaped(fresh), shaped(at45)), true);
  assert.equal(sandbox.announcementWorthy(shaped(at45), shaped(at46)), false);
  assert.equal(sandbox.announcementWorthy(shaped(at45), shaped(both)), true);
});

test("F8 only the stale groups with no row are named, and only their age counts", () => {
  // A mixed population (the adversarial round of claude/rider-text-residue, its F5): BDFM stale
  // 2700 s with no row, NQRW stale 3600 s WITH its Q row on the board. The line names B alone, at
  // BDFM's 45m: NQRW's row carries its own age, so neither its routes nor its 60m join the clause.
  const rowQ = { route_id: "Q", trip_id: "pq", arrival: T + 300, observed_at: T - 3600, provenance: "reported" };
  const NQRW = probeBlock({ fetched_at: T, feed_timestamp: T - 3600, ok: true });
  assert.deepEqual(probeLine({ BDFM: AGED_BDFM, NQRW }, [PROBE_ROW, rowQ]), {
    words: "B trains as of 45m ago",
    kind: "aged:B trains",
    slot: null,
  });
});

test("F8 a line names routes in the station's order, whatever order the groups arrive in", () => {
  // The envelope lists its groups in its own order (the backend sorts them, ACE before BDFM); the
  // rider hears the station's (the adversarial round of claude/rider-text-residue, its F4). NQRW
  // arriving before BDFM still reads "B and Q", as the station lists them.
  const NQRW = probeBlock({ fetched_at: T, feed_timestamp: T - 2700, ok: true });
  const { sandbox } = loadFrontend();
  const body = { ...probeBody({}), systems: { "1-7+S": PROBE_GROUP, NQRW, BDFM: AGED_BDFM } };
  const board = sandbox.boardFreshness("subway", body, T, PROBE.routes);
  assert.equal(sandbox.boardLine(board, [PROBE_ROW]).words, "B and Q trains as of 45m ago");
  // And the dark clause, which reads the same helper: at a station whose routes are B, D and E, with
  // ACE and BDFM never decoded and ACE listed first, "B, D and E", not "E, B and D".
  const never = probeBlock({ fetched_at: null, feed_timestamp: null, ok: false });
  const bde = sandbox.boardFreshness(
    "subway",
    { served_at: T, systems: { "1-7+S": PROBE_GROUP, ACE: never, BDFM: never } },
    T,
    ["B", "D", "E", "2"],
  );
  assert.equal(sandbox.boardLine(bde, [PROBE_ROW]).words, "B, D and E trains not reporting");
});

test("F8 a board with no systems map names nothing above rows that are its own", () => {
  // An older backend's subway board carries no systems map, so ingestSystems stands one system in
  // for it, named "subway", which no route joins to through the table. It is the board's one system
  // and every row is its, so its old content is the rows' to date, never a clause over them (the
  // adversarial round of claude/rider-text-residue, its F3). The same body empty still dates itself.
  const { sandbox } = loadFrontend();
  const old = { fetched_at: T, feed_timestamp: T - 600, served_at: T, directions: { Northbound: [PROBE_ROW] } };
  const board = sandbox.boardFreshness("subway", old, T, PROBE.routes);
  assert.deepEqual({ ...sandbox.boardLine(board, [PROBE_ROW]) }, { words: null, kind: "", slot: null });
  assert.deepEqual({ ...sandbox.boardLine(board, []) }, { words: "as of 10m ago", kind: "aged", slot: null });
});
