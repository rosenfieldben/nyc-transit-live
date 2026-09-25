// MR2: the pure half of the subway restyle.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// Stage MR2 draws the subway twice over: a casing and a line per shape, a haloed bullet
// per train, a dot or a ring per station, a name per station, and route focus over all of
// it. Four of those five decisions are arithmetic over data the page already has, so they
// live in helpers.js and are asked here directly rather than through a browser. What is
// NOT here is anything that needs a map: the layer identity route focus must preserve, the
// dimming it composes with, and the marker HTML itself are browser claims and they are in
// tests/e2e/subway.spec.js.
//
// THE PALETTE IS THE APP'S OWN, by ruling R1 of docs/reviews/map-redesign-rounds.md: the
// handoff's official-MTA hexes and its circular lettered bullet are overruled by the
// repository's own README note about route symbols. So every colour asserted here is
// lineColor()'s answer and never a hex copied out of the design.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  lineColor,
  YELLOW_TRUNK_ROUTES,
  isYellowTrunk,
  trunkDrawOrder,
  RIBBON_CASING_WEIGHT,
  RIBBON_CASING_OPACITY,
  RIBBON_LINE_WEIGHT,
  RIBBON_LINE_OPACITY,
  FOCUS_DIM_LINE,
  FOCUS_DIM_CASING,
  FOCUS_DIM_TRAIN,
  focusOpacity,
  routeFocusAnnouncement,
  routeFocusLabel,
  STATION_LOCAL_RADIUS,
  STATION_TRANSFER_RADIUS,
  STATION_TRANSFER_WEIGHT,
  isTransferStation,
  stationTrunks,
  stationMarkStyle,
  stationLabelClass,
  HUB_TRUNKS_AT_ONE_STOP,
  HUB_TRUNKS_ACROSS_STOPS,
  subwayComplexIndex,
  stationKickerRoutes,
  LABEL_HUB_ZOOM,
  LABEL_ALL_ZOOM,
  labelZoomBand,
  LABEL_NO_HUB_ZOOM,
  namesToggleAnnouncement,
  namesToggleTitle,
  stationLabelShown,
  markerOpacity,
  STALE_MARKER_OPACITY,
  SUBWAY_KEY_ALIASES,
  compareRouteIds,
  subwayRouteUniverse,
  bulletRouteIds,
  drawnRouteIds,
  ribbonRouteSet,
  focusRoutesForBullet,
  bulletTrackSet,
  bulletDrawsSomething,
  bulletTitle,
  subwayKeyModel,
} = require("./helpers.js");

const INK = "#201e1d";
const PAPER = "#f3f2f2";

/* ---------------- the draw order ---------------- */

test("MR2: the yellow trunk is drawn last, and everything else keeps the order it arrived in", () => {
  // THE MUTATION THIS KILLS is "yellow not drawn last". Yellow is the one trunk that
  // vanishes under its neighbours, and on Broadway it shares track with three of them.
  assert.deepEqual(trunkDrawOrder(["1", "N", "A", "Q", "7"]), ["1", "A", "7", "N", "Q"]);
  // Stable inside each group: the non-yellow routes come out in the order they went in,
  // so a static payload that lists its routes differently changes nothing but this.
  assert.deepEqual(trunkDrawOrder(["7", "A", "1"]), ["7", "A", "1"]);
  assert.deepEqual(trunkDrawOrder(["W", "R", "Q", "N"]), ["W", "R", "Q", "N"]);
  // Already last, and still last.
  assert.deepEqual(trunkDrawOrder(["1", "2", "N"]), ["1", "2", "N"]);
  // Nothing yellow at all, and nothing moves.
  assert.deepEqual(trunkDrawOrder(["1", "2", "3"]), ["1", "2", "3"]);
  assert.deepEqual(trunkDrawOrder([]), []);
});

test("MR2: a route is yellow by lineColor's own rule, an exact id then its first character", () => {
  for (const route of YELLOW_TRUNK_ROUTES) assert.equal(isYellowTrunk(route), true, route);
  // The four yellow routes are the four that share one hex, which is what makes them a
  // trunk. Asserted through lineColor rather than against a literal, so the day the
  // palette moves this test moves with it instead of contradicting it.
  const yellow = lineColor("N");
  for (const route of YELLOW_TRUNK_ROUTES) assert.equal(lineColor(route), yellow, route);
  // A lettered variant lands on its parent trunk, the same fallback lineColor takes.
  assert.equal(isYellowTrunk("NX"), true);
  assert.equal(isYellowTrunk("1"), false);
  assert.equal(isYellowTrunk("A"), false);
  assert.equal(isYellowTrunk(""), false);
  assert.equal(isYellowTrunk(null), false);
  assert.equal(isYellowTrunk(undefined), false);
});

test("MR2: the casing is wider than the line, which is the whole of what a casing is", () => {
  assert.ok(
    RIBBON_CASING_WEIGHT > RIBBON_LINE_WEIGHT,
    `a casing at ${RIBBON_CASING_WEIGHT} under a line at ${RIBBON_LINE_WEIGHT} would not show`,
  );
  assert.equal(RIBBON_CASING_WEIGHT, 6.5);
  assert.equal(RIBBON_LINE_WEIGHT, 4);
  assert.equal(RIBBON_CASING_OPACITY, 0.9);
  assert.equal(RIBBON_LINE_OPACITY, 1);
});

/* ---------------- route focus ---------------- */

test("MR2: with nothing focused every part of every route draws at full", () => {
  for (const part of ["line", "casing", "train"]) {
    assert.equal(focusOpacity(null, "1", part), part === "casing" ? RIBBON_CASING_OPACITY : part === "line" ? RIBBON_LINE_OPACITY : 1);
    assert.equal(focusOpacity("", "1", part), focusOpacity(null, "1", part));
  }
});

test("MR2: focusing a route leaves it alone and drops everything else, part by part", () => {
  assert.equal(focusOpacity("4", "4", "line"), RIBBON_LINE_OPACITY);
  assert.equal(focusOpacity("4", "4", "casing"), RIBBON_CASING_OPACITY);
  assert.equal(focusOpacity("4", "4", "train"), 1);

  assert.equal(focusOpacity("4", "1", "line"), FOCUS_DIM_LINE);
  assert.equal(focusOpacity("4", "1", "casing"), FOCUS_DIM_CASING);
  assert.equal(focusOpacity("4", "1", "train"), FOCUS_DIM_TRAIN);

  // THE UNFOCUSED CASING IS ZERO, not a fraction. A casing is paper, and paper at 18%
  // over a basemap is a grey smear the design asks not to draw; the line alone survives.
  assert.equal(FOCUS_DIM_CASING, 0);
  assert.equal(FOCUS_DIM_LINE, 0.18);
  assert.equal(FOCUS_DIM_TRAIN, 0.15);
});

test("MR2: route focus composes with the freshness contract rather than replacing it", () => {
  // markerOpacity already takes a base, so a train's opacity is its own observation's
  // answer TIMES focus. This is the one arithmetic claim MR2 makes about dimming, and it
  // is made by passing a base to the existing function rather than by changing it.
  const staleAge = 10_000;
  const freshAge = 1;

  // On the focused route, a stale train stays exactly as dim as the contract made it.
  assert.equal(
    markerOpacity(staleAge, focusOpacity("4", "4", "train")),
    STALE_MARKER_OPACITY,
    "focusing a route must not restore a stale train to full",
  );
  // Off the focused route, a stale train is dimmed twice.
  assert.equal(markerOpacity(staleAge, focusOpacity("4", "1", "train")), STALE_MARKER_OPACITY * FOCUS_DIM_TRAIN);
  // And a fresh train off the focused route carries the focus dimming alone.
  assert.equal(markerOpacity(freshAge, focusOpacity("4", "1", "train")), FOCUS_DIM_TRAIN);
  assert.equal(markerOpacity(freshAge, focusOpacity("4", "4", "train")), 1);
});

test("MR2: the focus state's three transitions, and the words each one says", () => {
  // Pressing a bullet with nothing focused focuses it; pressing the same one again clears;
  // pressing a different one moves the focus rather than clearing it. The transition is
  // the caller's, so what is asserted here is the sentence each state says.
  assert.equal(routeFocusAnnouncement("4"), "Focused on the 4; press again to clear.");
  assert.equal(routeFocusAnnouncement("N"), "Focused on the N; press again to clear.");
  assert.equal(routeFocusAnnouncement(null), "Route focus cleared.");
  assert.equal(routeFocusAnnouncement(""), "Route focus cleared.");
  // The bullet's own label does NOT move with the state, which is what lets it carry
  // aria-pressed (round 2 of MR1 removed an aria-pressed whose label contradicted it).
  assert.equal(routeFocusLabel("4"), "Focus route 4");
  assert.equal(routeFocusLabel("N"), "Focus route N");
});

/* ---------------- stations ---------------- */

test("hub definition: a complex is a ring at three trunks, or at two across two stops", () => {
  /* THE RULE SINCE claude/subway-hub-definition. F9 asked this of a stop's own routes, and "two
     trunks at one stop" turned out to mean shared track far more often than an interchange: the
     B beside the C on Central Park West, the F beside the G at Carroll St. So a lone stop needs
     THREE trunks, and two trunks make a hub only when they meet across two or more stops of one
     complex, which is the walk a rider actually makes. */
  assert.equal(HUB_TRUNKS_AT_ONE_STOP, 3);
  assert.equal(HUB_TRUNKS_ACROSS_STOPS, 2);
  // A bare routes list is a stop alone.
  assert.equal(isTransferStation(["1"]), false);
  assert.equal(isTransferStation(["1", "A"]), false, "two trunks at one stop is shared track");
  assert.equal(isTransferStation(["1", "A", "L"]), true, "three at one stop is the ruling's exception");
  assert.equal(isTransferStation(["1", "A", "L", "G", "J", "7"]), true);
  // A complex: its routes unioned across its stops, and how many stops that is.
  assert.equal(isTransferStation({ routes: ["1", "A"], stops: 2 }), true, "168 St: the 1 and the A/C");
  assert.equal(isTransferStation({ routes: ["1", "2", "3"], stops: 2 }), false, "one trunk is one line");
  assert.equal(isTransferStation({ routes: ["1"], stops: 5 }), false);
  assert.equal(isTransferStation({ routes: ["1", "A"], stops: 1 }), false, "a complex of one is a stop alone");
  // A stop count that is not a count reads as one stop, never as many.
  for (const stops of [0, -2, NaN, null, undefined, "2x"]) {
    assert.equal(isTransferStation({ routes: ["1", "A"], stops }), false, String(stops));
  }

  const local = stationMarkStyle(["1", "A"], INK, PAPER);
  assert.equal(local.radius, STATION_LOCAL_RADIUS);
  assert.equal(local.fillColor, INK);
  assert.equal(local.stroke, false);
  assert.equal(local.weight, 0);

  const transfer = stationMarkStyle(["1", "A", "L"], INK, PAPER);
  assert.equal(transfer.radius, STATION_TRANSFER_RADIUS);
  assert.equal(transfer.fillColor, PAPER);
  assert.equal(transfer.color, INK);
  assert.equal(transfer.weight, STATION_TRANSFER_WEIGHT);
  assert.equal(transfer.stroke, true);

  // A ring is bigger than a dot, because it has to hold a hole.
  assert.ok(STATION_TRANSFER_RADIUS > STATION_LOCAL_RADIUS);
});

test("MR2 F9: a skip-stop pair and a local/express pair are ONE trunk, so they are locals", () => {
  /* THE MUTATION THIS KILLS is counting route ids instead of trunks, which is what this did
     until round 3 found it. The J and the Z are one line taking turns at the same platform,
     and the archive has a Jamaica Avenue station for every pair of them; every ["A","C"] and
     ["4","5"] stop is the same shape. Counting ids drew all of them as interchanges and, worse,
     gave them the hub class the zoom-12 band exists to keep sparse. lineColor() already knows
     which ids are one line, because sharing a colour is what that means. */
  for (const pair of [["J", "Z"], ["A", "C"], ["A", "C", "E"], ["4", "5"], ["4", "5", "6"], ["N", "Q", "R", "W"]]) {
    assert.equal(isTransferStation(pair), false, pair.join("/"));
    assert.equal(stationLabelClass(pair), "stn-label subway", pair.join("/"));
    assert.equal(stationMarkStyle(pair, INK, PAPER).stroke, false, pair.join("/"));
    assert.equal(stationTrunks(pair).size, 1, pair.join("/"));
  }
  // A real interchange still is one: two trunks across two stops, whatever the id count. The
  // same pairs at ONE stop are shared track since claude/subway-hub-definition, and a pair of
  // ids on one trunk is not a hub across any number of stops.
  for (const real of [["J", "L"], ["A", "1"], ["4", "6", "N"], ["GS", "7"]]) {
    assert.equal(isTransferStation({ routes: real, stops: 2 }), true, real.join("/"));
    assert.equal(stationLabelClass({ routes: real, stops: 2 }), "stn-label subway hub", real.join("/"));
    assert.equal(isTransferStation(real), false, `${real.join("/")} at one stop`);
  }
  for (const pair of [["J", "Z"], ["A", "C", "E"], ["4", "5", "6"]]) {
    assert.equal(isTransferStation({ routes: pair, stops: 3 }), false, pair.join("/"));
  }
  // Two ids lineColor cannot place collapse into one trunk rather than inventing a transfer:
  // an unknown id must not make a claim about the network.
  assert.equal(isTransferStation({ routes: ["ZZ1", "ZZ2"], stops: 2 }), false);
  // Ids off the wire may be numbers.
  assert.equal(isTransferStation({ routes: [4, 5], stops: 2 }), false);
  assert.equal(isTransferStation({ routes: [4, "A"], stops: 2 }), true);
});

test("MR2: a station with no routes is a local dot, which is the direction that matters", () => {
  // THE MUTATION THIS KILLS is "the transfer ring drawn for single-route stations", and
  // this is its quieter twin. The routes field is optional on the stops endpoint. Against
  // the real static archive 171 stations have one route and 325 have two or more and none
  // has zero, so a backend that stopped serving the index would turn all 496 into transfer
  // rings: a claim about the network made out of a missing value.
  for (const missing of [[], null, undefined, [""], [null]]) {
    assert.equal(isTransferStation(missing), false, JSON.stringify(missing));
    assert.equal(stationMarkStyle(missing, INK, PAPER).radius, STATION_LOCAL_RADIUS, JSON.stringify(missing));
    assert.equal(stationMarkStyle(missing, INK, PAPER).stroke, false, JSON.stringify(missing));
    assert.equal(stationLabelClass(missing), "stn-label subway", JSON.stringify(missing));
  }
});

/* THE DIAGNOSIS'S OWN TABLE, over the real payload (claude/subway-hub-definition). On 2026-09-25 the
   deployed build named 96 St, 86 St, 72 St, Carroll St, 25 St and 36 St at zoom 13, a band that
   shows hubs only, and every one of them was a hub under F9's per-stop count. This asks the same
   stations again through the complex rule, reading tests/e2e/fixtures/subway_stops_real.json,
   which is production's /api/subway-stops payload from that day plus the complex_id the branch
   serves (the backend test_the_e2e_census_fixture_agrees_with_this_archive holds it to the
   committed transfers.txt).

   THE MUTATION THIS KILLS FIRST is the predicate counting per stop again: 96 St on Central Park
   West is A, B and C at one stop, two trunks, and F9's rule rings it. */
const REAL_STOPS = JSON.parse(
  require("node:fs").readFileSync(
    require("node:path").join(__dirname, "..", "tests", "e2e", "fixtures", "subway_stops_real.json"),
    "utf8",
  ),
);
const REAL_COMPLEXES = subwayComplexIndex(REAL_STOPS);
const realHub = (id) => isTransferStation(REAL_COMPLEXES.get(id));

test("hub definition: the diagnosis's stations, asked of their complexes", () => {
  assert.equal(REAL_STOPS.length, 496);
  // The shared-track locals the deployed build named at zoom 13.
  for (const [id, name] of [
    ["A19", "96 St"],
    ["A20", "86 St"],
    ["A22", "72 St"],
    ["F21", "Carroll St"],
    ["R35", "25 St"],
    ["R36", "36 St"],
  ]) {
    const complex = REAL_COMPLEXES.get(id);
    assert.equal(REAL_STOPS.find((s) => s.id === id).name, name, id);
    assert.equal(complex.stops, 1, `${name} (${id}) is a stop alone`);
    assert.equal(stationTrunks(complex.routes).size, 2, `${name} (${id}) serves two trunks at one stop`);
    assert.equal(realHub(id), false, `${name} (${id}) is shared track, not a hub`);
    // And under F9's rule it WAS one, which is the finding, kept as a premise so this test
    // cannot pass over stations that were never in question.
    assert.equal(stationTrunks(REAL_STOPS.find((s) => s.id === id).routes).size >= 2, true, id);
  }
  // 36 St on Queens Boulevard: E, F/M and R at one stop, three trunks, the ruling's exception.
  assert.equal(REAL_COMPLEXES.get("G20").stops, 1);
  assert.equal(realHub("G20"), true, "36 St G20");
  // Times Square: five stops, each ONE trunk, which is why F9 gave none of them a ring.
  const timesSquare = ["127", "725", "902", "A27", "R16"];
  for (const id of timesSquare) {
    assert.equal(stationTrunks(REAL_STOPS.find((s) => s.id === id).routes).size, 1, `${id} alone is one trunk`);
    assert.equal(REAL_COMPLEXES.get(id), REAL_COMPLEXES.get("127"), `${id} is in Times Square's complex`);
    assert.equal(realHub(id), true, `Times Square ${id}`);
  }
  assert.equal(REAL_COMPLEXES.get("127").stops, 5);
  assert.equal(stationTrunks(REAL_COMPLEXES.get("127").routes).size, 5);
  // Court Square: the 7, the G and the E/M, three stops.
  for (const id of ["719", "F09", "G22"]) assert.equal(realHub(id), true, `Court Sq ${id}`);
  assert.equal(REAL_COMPLEXES.get("719").stops, 3);
});

test("hub definition: the census over the real payload, before and after", () => {
  /* 124 rings under F9's per-stop count; under the complex rule, 100 stops in 48 complexes. The
     e2e census (subway.spec.js D2i1) counts the same thing on the drawn page. */
  const before = REAL_STOPS.filter((s) => stationTrunks(s.routes).size >= 2);
  const after = REAL_STOPS.filter((s) => realHub(s.id));
  assert.equal(before.length, 124);
  assert.equal(after.length, 100);
  assert.equal(new Set(after.map((s) => REAL_COMPLEXES.get(s.id))).size, 48);
  // Every stop of a hub complex is a hub: the ring is the station's, not the platform's.
  for (const s of after) {
    for (const t of REAL_STOPS.filter((u) => REAL_COMPLEXES.get(u.id) === REAL_COMPLEXES.get(s.id))) {
      assert.equal(realHub(t.id), true, `${t.name} (${t.id})`);
    }
  }
});

test("hub definition: subwayComplexIndex groups by complex_id and treats a missing one as alone", () => {
  const index = subwayComplexIndex([
    { id: "127", routes: ["1", "2", "3"], complex_id: "127" },
    { id: "725", routes: ["7"], complex_id: "127" },
    { id: "A19", routes: ["A", "B", "C"], complex_id: "A19" },
    // A payload from before the field: no complex_id at all, so a stop alone.
    { id: "X1", routes: ["1", "A"] },
    { id: "X2", routes: ["1", "A"], complex_id: null },
  ]);
  assert.equal(index.get("127"), index.get("725"), "one object for every stop of a complex");
  assert.deepEqual(index.get("725"), { id: "127", routes: ["1", "2", "3", "7"], stops: 2 });
  assert.deepEqual(index.get("A19"), { id: "A19", routes: ["A", "B", "C"], stops: 1 });
  assert.deepEqual(index.get("X1"), { id: "X1", routes: ["1", "A"], stops: 1 });
  assert.deepEqual(index.get("X2"), { id: "X2", routes: ["1", "A"], stops: 1 });
  assert.equal(isTransferStation(index.get("127")), true);
  assert.equal(isTransferStation(index.get("X1")), false);
  // Nothing in, nothing out, and junk rows are skipped rather than keyed as "undefined".
  assert.equal(subwayComplexIndex([]).size, 0);
  assert.equal(subwayComplexIndex(null).size, 0);
  assert.equal(subwayComplexIndex([null, {}, { id: null }]).size, 0);
});

test("hub definition: the kicker lists the complex's routes, the stop's own first", () => {
  const complex = { routes: ["1", "2", "3", "7", "7X", "GS", "A", "C", "E", "N", "Q", "R", "W"], stops: 5 };
  assert.deepEqual(stationKickerRoutes(["7", "7X"], complex).slice(0, 2), ["7", "7X"]);
  assert.deepEqual([...stationKickerRoutes(["7", "7X"], complex)].sort(), [...complex.routes].sort());
  assert.equal(stationKickerRoutes(["7", "7X"], complex).length, 13, "no route twice");
  // A stop alone, or no complex at all, lists exactly its own routes, in its own order.
  assert.deepEqual(stationKickerRoutes(["A", "B", "C"], ["A", "B", "C"]), ["A", "B", "C"]);
  assert.deepEqual(stationKickerRoutes(["A", "B", "C"], null), ["A", "B", "C"]);
  assert.deepEqual(stationKickerRoutes(undefined, null), []);
});

test("MR2: the two theme colours are the caller's, so a theme swap is a setStyle", () => {
  // The style function never reaches for a token itself, which is what lets MR4 restyle a
  // canvas layer in place instead of rebuilding 496 of them.
  const dark = stationMarkStyle(["1", "A", "L"], "#f3f2f2", "#201e1d");
  assert.equal(dark.color, "#f3f2f2");
  assert.equal(dark.fillColor, "#201e1d");
});

/* ---------------- labels ---------------- */

test("MR2: a hub label is the same station a transfer ring is", () => {
  /* MR4 ADDED `subway`, which is a positive class and is the point. `.stn-label` began as the
     subway's alone, so counting it meant "subway station names"; MR3 put the rail families in
     it and MR4 the ferry's docks, and every sentinel that had been written as
     `.stn-label:not(.rail)` was silently wrong the moment a family arrived that was not rail.
     A class a family carries cannot be widened by a family that does not, so these four
     assertions are what stop the qualifier being dropped again. */
  assert.equal(stationLabelClass(["1"]), "stn-label subway");
  assert.equal(stationLabelClass(["1", "A", "L"]), "stn-label subway hub");
  assert.equal(stationLabelClass({ routes: ["1", "A"], stops: 2 }), "stn-label subway hub");
  assert.equal(stationLabelClass(["1", "A"]), "stn-label subway");
  assert.equal(stationLabelClass([]), "stn-label subway");
  assert.equal(stationLabelClass(undefined), "stn-label subway");
  // One predicate behind both, so a station cannot draw a ring and label itself local.
  for (const routes of [
    [],
    ["1"],
    ["J", "Z"],
    ["1", "A"],
    ["1", "A", "L"],
    ["4", "5", "6", "N", "Q"],
    { routes: ["1", "A"], stops: 2 },
    { routes: ["1", "2"], stops: 3 },
  ]) {
    assert.equal(
      stationLabelClass(routes).includes("hub"),
      isTransferStation(routes),
      JSON.stringify(routes),
    );
    assert.equal(
      stationMarkStyle(routes, INK, PAPER).stroke,
      isTransferStation(routes),
      JSON.stringify(routes),
    );
  }
});

test("MR2: the zoom band is none below 12, hubs at 12 and 13, and all from 14", () => {
  // THE MUTATION THIS KILLS is "labels not gated by zoom". CSS cannot compare integers, so
  // the root carries data-zoom="<n>" and the stylesheet enumerates; this is the same
  // decision in the one place a test can ask it.
  assert.equal(LABEL_HUB_ZOOM, 12);
  assert.equal(LABEL_ALL_ZOOM, 14);
  for (const z of [0, 5, 10, 11, 11.9]) assert.equal(labelZoomBand(z), "none", String(z));
  for (const z of [12, 13, 13.9]) assert.equal(labelZoomBand(z), "hubs", String(z));
  for (const z of [14, 15, 19]) assert.equal(labelZoomBand(z), "all", String(z));
  // A zoom that is not a number gates everything off rather than on: a label drawn because
  // a value was missing is a name in the wrong place.
  for (const z of [null, undefined, NaN, "14"]) assert.equal(labelZoomBand(z), "none", String(z));
});

test("MR2: one station's name is on screen only when the band, its kind and the toggle agree", () => {
  const LOCAL = ["1"];
  const HUB = ["1", "A", "L"];
  // Below the band nothing shows.
  assert.equal(stationLabelShown(11, HUB, true), false);
  assert.equal(stationLabelShown(11, LOCAL, true), false);
  // Hubs band: the ring's name, not the dot's.
  assert.equal(stationLabelShown(12, HUB, true), true);
  assert.equal(stationLabelShown(12, LOCAL, true), false);
  assert.equal(stationLabelShown(13, HUB, true), true);
  // All band: both.
  assert.equal(stationLabelShown(14, HUB, true), true);
  assert.equal(stationLabelShown(14, LOCAL, true), true);
  // The Names toggle overrides the band in one direction only, which is off.
  for (const zoom of [11, 12, 14, 19]) {
    for (const routes of [LOCAL, HUB]) {
      assert.equal(stationLabelShown(zoom, routes, false), false, `${zoom}/${routes}`);
    }
  }
});

test("MR2 F1: with no hub anywhere the band skips a step and shows every name from 13", () => {
  /* THE DEGRADED BACKEND STATE, and the reason it is worth a band of its own: stop_times.txt
     is not a required member of the subway static archive, load_subway_station_routes returns
     {} on any failure, and the endpoint then serves routes: [] for all 496 stations while the
     status stays "ready". Every station is a local, no label carries the hub class, and
     "hubs from 12" correctly reveals nothing at the opening zoom and at the City preset while
     the Names button reads pressed. The arithmetic was right and the map was blank.

     13 rather than 12, because with no hub to thin the field the collision measurements make
     12 the worst zoom (89% of painted labels overlapping another); and 13 rather than 14,
     because a rider should not have to reach 14 to see any name at all. */
  assert.equal(LABEL_NO_HUB_ZOOM, 13);
  assert.equal(labelZoomBand(11, false), "none");
  assert.equal(labelZoomBand(12, false), "none");
  assert.equal(labelZoomBand(13, false), "all");
  assert.equal(labelZoomBand(19, false), "all");
  // The band with hubs is untouched, which is the half that must not have moved.
  for (const z of [0, 11]) assert.equal(labelZoomBand(z, true), "none", String(z));
  for (const z of [12, 13]) assert.equal(labelZoomBand(z, true), "hubs", String(z));
  for (const z of [14, 19]) assert.equal(labelZoomBand(z, true), "all", String(z));
  // Default is the hub world, so no existing call site changed meaning.
  for (const z of [11, 12, 13, 14]) assert.equal(labelZoomBand(z), labelZoomBand(z, true), String(z));
  // A local station's own name shows at 13 in that world and not at 12.
  assert.equal(stationLabelShown(13, ["1"], true, false), true);
  assert.equal(stationLabelShown(12, ["1"], true, false), false);
  // And the toggle still overrides it.
  assert.equal(stationLabelShown(13, ["1"], false, false), false);
  // A non-number is still off, whichever world it is.
  for (const z of [null, undefined, NaN, "13"]) assert.equal(labelZoomBand(z, false), "none", String(z));
});

test("MR2 F7: the Names toggle says what happened, and says why when there is nothing to show", () => {
  // It announced NOTHING before round 3, while route focus has announced since this stage was
  // written. The labels are aria-hidden by design, so this sentence is the only evidence a
  // screen reader gets that the press did anything at all.
  assert.equal(namesToggleAnnouncement(false, "all"), "Station names off.");
  assert.equal(namesToggleAnnouncement(false, "none"), "Station names off.");
  assert.equal(namesToggleAnnouncement(true, "all"), "Station names on.");
  assert.equal(namesToggleAnnouncement(true, "hubs"), "Station names on.");
  // The one state a rider cannot work out from the screen: on, and nothing can show.
  assert.match(namesToggleAnnouncement(true, "none"), /^Station names on; none at this zoom/);

  /* EVERY BAND IT IS GIVEN, NOT THE FIRST (MR3 round 4). The toggle hides commuter-rail names as
     well (style.css's `:root[data-labels="off"] .stn-label.rail`), and the two bands disagree by
     design: rail names show from zoom 11 and the subway's first band opens at 12. Reading the
     subway's alone, the press at zoom 11 said "none at this zoom, zoom in to see them" while it
     had just switched off every rail name on screen, which is the one thing this sentence exists
     to prevent. THE MUTATION IS READING ONLY THE FIRST BAND, and these four kill it. */
  assert.equal(namesToggleAnnouncement(true, "none", "all"), "Station names on.");
  assert.equal(namesToggleAnnouncement(true, "all", "none"), "Station names on.");
  assert.equal(namesToggleAnnouncement(true, "hubs", "none"), "Station names on.");
  assert.match(namesToggleAnnouncement(true, "none", "none"), /^Station names on; none at this zoom/);
  // Off is off whatever the bands say, because nothing is shown either way.
  assert.equal(namesToggleAnnouncement(false, "none", "all"), "Station names off.");

  /* AND NO BANDS AT ALL IS "ON", NOT "NONE". `[].every(...)` is vacuously true, so the obvious
     spelling of "every band is none" would make a caller that names no band claim the zoom shows
     no names. A caller that names none has told us nothing about the zoom, and the unqualified
     sentence is the honest answer. Asserted because it is a trap a later stage will walk into
     while adding its own families to the call. */
  assert.equal(namesToggleAnnouncement(true), "Station names on.");
  // A band the root has not been given yet reads null, which is not "none" and must not become it.
  assert.equal(namesToggleAnnouncement(true, "none", null), "Station names on.");

  /* The tooltip carries the other one, which is the degraded backend rather than the zoom, and
     it is keyed on HOW MANY STATIONS LIST ROUTES rather than on how many are hubs. A network
     with no interchange and a full route index is a legitimate network, and over that map this
     sentence would be false: the hermetic fixture is exactly that case. */
  assert.equal(namesToggleTitle(496, 496), "");
  assert.equal(namesToggleTitle(2, 2), "");
  assert.equal(namesToggleTitle(1, 496), "", "one station listing routes is enough to make it true");
  assert.match(namesToggleTitle(0, 496), /No station lists the routes that call there/);
  assert.match(namesToggleTitle(0, 496), /from zoom 13/);
  // No stations at all is not that state, it is a map that has not loaded yet.
  assert.equal(namesToggleTitle(0, 0), "");
});

/* ---------------- the derived key (round 2, G3) ----------------
   The key was a hand-written table of five trunks until round 2. The table was wrong in
   both directions at once: it offered bullets for routes the feed never carries (Z, W and a
   bare S are not feed ids), and it had no bullet for SI at all. So the key is now a
   function of the loaded route list and the trains on the map, and focus is MEMBERSHIP in a
   ribbon's route set rather than equality with one route id.

   THE FIXTURE IS THE REAL WORLD'S SHAPE. Measured from the MTA static archive
   (gtfs_subway.zip, fetched during this stage): 24 routes and 35 shapes, and the drawn set
   is 1 2 3 4 5 6 7 A B C D E F FS G GS H J L M N Q R SI. There is no Z shape, no W shape
   and no S shape, which is the whole reason this section exists. REAL_SHAPED keeps one id
   per trunk from that list so the assertions can be read, and every claim below holds of
   the full 24 for the same reason it holds of these nine. */

const REAL_SHAPED = ["1", "J", "N", "Q", "R", "GS", "FS", "H", "SI"];
const shapedWorld = (ids = REAL_SHAPED) =>
  ids.map((route) => ({ route, polylines: [[[40.7, -73.9], [40.8, -73.8]]] }));

test("MR2 G3: the bullet list is the loaded routes plus the trains, alias-collapsed", () => {
  // GS, FS and H are three feed ids for one bullet, so they collapse into S; Z arrives as a
  // train with no shape of its own and still earns a bullet, because the app can draw it.
  assert.deepEqual(subwayRouteUniverse(shapedWorld(), ["1", "Z"]), [
    "1", "J", "N", "Q", "R", "S", "SI", "Z",
  ]);
  // SI is in the archive and was missing from the hand-written table. It is here by
  // construction now rather than by anybody remembering it.
  assert.ok(subwayRouteUniverse(shapedWorld(), []).includes("SI"));
  // The alias collapses only when one of its targets is really present: a world with no
  // shuttle at all has no S bullet to press.
  assert.deepEqual(subwayRouteUniverse(shapedWorld(["1"]), []), ["1"]);
  assert.deepEqual(subwayRouteUniverse(shapedWorld(["GS"]), []), ["S"]);
  assert.deepEqual(subwayRouteUniverse(shapedWorld(["H"]), []), ["S"]);
  // Digits before letters, then lexicographic, so S sorts before SI and 7 before A.
  assert.deepEqual(subwayRouteUniverse(shapedWorld(["A", "7", "SI", "GS", "1"]), []), [
    "1", "7", "A", "S", "SI",
  ]);
  assert.equal(compareRouteIds("S", "SI") < 0, true);
  assert.equal(compareRouteIds("7", "A") < 0, true);
  assert.equal(compareRouteIds("A", "7") > 0, true);
  assert.equal(compareRouteIds("Q", "Q"), 0);
  // Nothing loaded is not an error, it is an empty key.
  assert.deepEqual(subwayRouteUniverse([], []), []);
  assert.deepEqual(subwayRouteUniverse(null, []), []);
});

test("MR2 G3: a bullet stands for feed ids, and S stands for three of them", () => {
  assert.deepEqual(bulletRouteIds("S"), ["GS", "FS", "H"]);
  assert.deepEqual(bulletRouteIds("1"), ["1"]);
  assert.deepEqual(bulletRouteIds("SI"), ["SI"]);
  assert.deepEqual(Object.keys(SUBWAY_KEY_ALIASES), ["S"]);
});

test("MR2 G3: a drawn ribbon carries every trunk-mate that has no geometry of its own", () => {
  const routes = shapedWorld();
  const known = new Set([...REAL_SHAPED, "Z"]);
  // The J/Z ribbon. Z has no shape, shares the brown trunk, and rides J's polylines, so the
  // polyline's tag is the union and not J alone.
  assert.deepEqual(ribbonRouteSet("J", routes, known), ["J", "Z"]);
  // A route whose trunk has no orphan carries itself.
  assert.deepEqual(ribbonRouteSet("1", routes, known), ["1"]);
  assert.deepEqual(ribbonRouteSet("SI", routes, known), ["SI"]);
  // W is not in this world at all, so no yellow ribbon claims it.
  assert.deepEqual(ribbonRouteSet("N", routes, known), ["N"]);
  // Let a W train arrive and all three yellow ribbons pick it up, because all three are
  // track it could be on and the app has no shape to tell them apart.
  const withW = new Set([...known, "W"]);
  assert.deepEqual(ribbonRouteSet("N", routes, withW), ["N", "W"]);
  assert.deepEqual(ribbonRouteSet("Q", routes, withW), ["Q", "W"]);
  assert.deepEqual(ribbonRouteSet("R", routes, withW), ["R", "W"]);
  // A route that IS drawn is never somebody else's orphan, which is what keeps the 1 out of
  // the 2's tag in the full archive.
  assert.deepEqual(ribbonRouteSet("1", shapedWorld(["1", "2", "3"]), new Set(["1", "2", "3"])), ["1"]);
});

test("MR2 G3: only routes with geometry count as drawn", () => {
  assert.deepEqual([...drawnRouteIds(shapedWorld(["1", "J"]))], ["1", "J"]);
  // A route the backend lists with no shapes is loaded but not drawn, and that distinction
  // is the one a disabled bullet is made of.
  assert.deepEqual([...drawnRouteIds([{ route: "L", polylines: [] }])], []);
  assert.deepEqual([...drawnRouteIds([{ route: "L" }])], []);
  assert.deepEqual([...drawnRouteIds(null)], []);
});

test("MR2 G3: focus is membership, so Z lights the J/Z ribbon and W lights all three yellows", () => {
  const routes = shapedWorld();
  /* THE FOCUS SET IS THE BULLET'S OWN IDS AND NEVER GROWS. The first version of this took
     the transitive closure of the ribbons a bullet touches, and D2o caught what that does
     to the Broadway trunk: N's ribbon carries W, W's closure is N, Q and R, so focusing N
     reached Q and R through W and lit three routes when a rider asked for one. A ribbon
     lights because ITS set contains one of the bullet's ids; the bullet's ids stay put. */
  assert.deepEqual(focusRoutesForBullet("Z"), ["Z"]);
  assert.deepEqual(focusRoutesForBullet("N"), ["N"]);
  assert.deepEqual(focusRoutesForBullet("S"), ["GS", "FS", "H"]);
  assert.deepEqual(focusRoutesForBullet("SI"), ["SI"]);

  // Z: the user's first case. Z's id is on J's polylines, so pressing Z lights that ribbon.
  assert.ok(ribbonRouteSet("J", routes, new Set([...REAL_SHAPED, "Z"])).includes("Z"));
  // and no other ribbon carries it, so nothing else lights.
  for (const drawn of ["1", "N", "SI", "GS"]) {
    assert.equal(ribbonRouteSet(drawn, routes, new Set([...REAL_SHAPED, "Z"])).includes("Z"), false, drawn);
  }
  // W: the user's second case. N, Q and R are each drawn, W is not, so all three carry it
  // and pressing W lights the whole Broadway trunk, which is the honest answer.
  const withW = new Set([...REAL_SHAPED, "W"]);
  for (const drawn of ["N", "Q", "R"]) assert.ok(ribbonRouteSet(drawn, routes, withW).includes("W"), drawn);
  // and pressing N lights N ALONE, which is the asymmetry the closure got wrong.
  const n = focusRoutesForBullet("N");
  assert.equal(ribbonRouteSet("N", routes, withW).some((id) => n.includes(id)), true);
  assert.equal(ribbonRouteSet("Q", routes, withW).some((id) => n.includes(id)), false);
  assert.equal(ribbonRouteSet("R", routes, withW).some((id) => n.includes(id)), false);

  // The TRACK set is still the closure, and it is what the title is written from: pressing
  // Z really does light the ribbon J is drawn as, and the tooltip says so.
  assert.deepEqual(bulletTrackSet("Z", routes, ["Z"]), ["J", "Z"]);
  assert.deepEqual(bulletTrackSet("J", routes, ["Z"]), ["J", "Z"]);
  assert.deepEqual(bulletTrackSet("W", routes, ["W"]), ["N", "Q", "R", "W"]);
  assert.deepEqual(bulletTrackSet("S", routes, []), ["FS", "GS", "H"]);
  assert.deepEqual(bulletTrackSet("SI", routes, []), ["SI"]);
  // A route with a shape of its own and no orphan trunk-mate shares track with nobody, which
  // is what stage 1's behaviour was and what must not have changed for the common case.
  assert.deepEqual(bulletTrackSet("1", routes, ["1"]), ["1"]);
});

test("MR2 G3: a bullet whose set draws nothing is disabled and says why", () => {
  // THE MUTATION THIS KILLS is "a bullet with an empty set dims the map": a route the
  // backend lists with no shapes and no train running. Pressing it would drop every ribbon
  // and every train to the focus floor and light nothing, which is a blank map with no way
  // back except pressing the same dead bullet again.
  const routes = [...shapedWorld(), { route: "L", polylines: [] }];
  assert.deepEqual(bulletTrackSet("L", routes, []), ["L"]);
  assert.equal(bulletDrawsSomething("L", routes, []), false);
  assert.equal(bulletTitle("L", ["L"], false), "Nothing on the map right now for L.");
  // It is still in the key: a rider looking for the L learns it is not running, which is
  // information, and an absent bullet is not.
  assert.ok(subwayRouteUniverse(routes, []).includes("L"));
  // One L train on the map is enough to enable it, with no shape anywhere.
  assert.equal(bulletDrawsSomething("L", routes, ["L"]), true);
  // and a drawn route is enabled with no train at all, because its ribbon is the thing
  // being focused.
  assert.equal(bulletDrawsSomething("1", routes, []), true);
  // Z IS ENABLED BY J'S RIBBON with neither a shape nor a train of its own, which is why
  // this asks the ribbons rather than the id: a set-membership test over drawn ids alone
  // would grey out the one bullet round 2 exists to make reachable.
  assert.equal(bulletDrawsSomething("Z", routes, []), true);
  // and the S bullet is enabled by any one of its three, not by all three.
  assert.equal(bulletDrawsSomething("S", [{ route: "GS", polylines: [[[1, 2], [3, 4]]] }], []), true);
});

test("MR2 G3: the title explains an alias and a shared track, and the name never moves", () => {
  const routes = shapedWorld();
  // The S tooltip the user asked for: the bullet says which three routes it is.
  assert.equal(bulletTitle("S", bulletTrackSet("S", routes, []), true), "S is GS, FS, H. Focus S.");
  // A disabled alias still explains itself before saying it is dark.
  assert.equal(bulletTitle("S", ["GS", "FS", "H"], false), "S is GS, FS, H. Nothing on the map right now for S.");
  // Shared track is named, so pressing Z and watching J light is explained before it
  // happens rather than after.
  assert.equal(bulletTitle("Z", ["J", "Z"], true), "Focus Z. Shares track with J.");
  assert.equal(bulletTitle("W", ["N", "Q", "R", "W"], true), "Focus W. Shares track with N, Q, R.");
  // The plain case says nothing it does not need to.
  assert.equal(bulletTitle("1", ["1"], true), "Focus 1.");
  // THE ACCESSIBLE NAME IS NOT THE TITLE and does not move with the state, which is the
  // aria-pressed rule MR1 round 2 settled: a button whose name changes when pressed is
  // announced twice and read as two controls.
  assert.equal(routeFocusLabel("1"), "Focus route 1");
  assert.equal(routeFocusLabel("S"), "Focus route S");
});

test("MR2 G3: the whole key is grouped by trunk, in an order the data decides", () => {
  const model = subwayKeyModel(shapedWorld(), ["1", "Z"]);
  assert.deepEqual(
    model.map((group) => [group.color, group.bullets.map((b) => b.id)]),
    [
      ["#c0392b", ["1"]],
      ["#7d5a3c", ["J", "Z"]],
      ["#e6b800", ["N", "Q", "R"]],
      ["#566573", ["S"]],
      ["#34495e", ["SI"]],
    ],
  );
  // A group's colour is lineColor's answer for the bullet's first feed id, never a hex out
  // of the handoff: ruling R1 again, and the reason S is the shuttle grey rather than the
  // design's own.
  for (const group of model) {
    for (const bullet of group.bullets) {
      assert.equal(group.color, lineColor(bulletRouteIds(bullet.id)[0]), bullet.id);
    }
  }
  // Every bullet carries its own focus set, enabled flag and title, so the DOM builder has
  // no decisions left to make and there is one place to test them.
  const all = model.flatMap((group) => group.bullets);
  assert.deepEqual(all.map((b) => b.id), ["1", "J", "Z", "N", "Q", "R", "S", "SI"]);
  assert.ok(all.every((b) => b.enabled === true));
  assert.deepEqual(all.find((b) => b.id === "Z").focus, ["Z"]);
  assert.deepEqual(all.find((b) => b.id === "S").focus, ["GS", "FS", "H"]);
  // and the title carries the closure the focus set deliberately does not.
  assert.equal(all.find((b) => b.id === "Z").title, "Focus Z. Shares track with J.");
  assert.equal(all.find((b) => b.id === "S").title, "S is GS, FS, H. Focus S.");
  // An empty world is an empty key rather than five empty groups.
  assert.deepEqual(subwayKeyModel([], []), []);
});

test("MR2 G3: focusOpacity reads a ribbon's whole set, not one route id", () => {
  // The ribbon on Jamaica Ave is tagged ["J","Z"]. Focusing either one leaves it full.
  const jz = ["J", "Z"];
  assert.equal(focusOpacity(["J", "Z"], jz, "line"), RIBBON_LINE_OPACITY);
  assert.equal(focusOpacity(["J", "Z"], jz, "casing"), RIBBON_CASING_OPACITY);
  // and a route outside the set drops, part by part, exactly as equality used to.
  assert.equal(focusOpacity(["J", "Z"], ["1"], "line"), FOCUS_DIM_LINE);
  assert.equal(focusOpacity(["J", "Z"], ["1"], "casing"), FOCUS_DIM_CASING);
  assert.equal(focusOpacity(["J", "Z"], ["1"], "train"), FOCUS_DIM_TRAIN);
  // A train is tagged with one id and still matches a multi-route focus: this is the Z
  // train under the Z bullet.
  assert.equal(focusOpacity(["J", "Z"], "Z", "train"), 1);
  assert.equal(focusOpacity(["J", "Z"], "J", "train"), 1);
  // A bare string focus still works, because stage 1's single-route call sites and the
  // Escape ladder both pass one.
  assert.equal(focusOpacity("4", ["4"], "line"), RIBBON_LINE_OPACITY);
  assert.equal(focusOpacity("4", ["5"], "line"), FOCUS_DIM_LINE);
  // Nothing focused is full for everything, whichever shape the arguments take.
  for (const empty of [null, "", []]) {
    assert.equal(focusOpacity(empty, jz, "line"), RIBBON_LINE_OPACITY, JSON.stringify(empty));
    assert.equal(focusOpacity(empty, jz, "train"), 1, JSON.stringify(empty));
  }
  // A part nobody named is left alone rather than guessed at.
  assert.equal(focusOpacity(["J"], ["1"], "halo"), 1);
  // Ids are compared as strings, because a route id off the wire may be a number.
  assert.equal(focusOpacity(["7"], [7], "line"), RIBBON_LINE_OPACITY);
});
