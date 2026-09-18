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
  stationMarkStyle,
  stationLabelClass,
  LABEL_HUB_ZOOM,
  LABEL_ALL_ZOOM,
  labelZoomBand,
  stationLabelShown,
  markerOpacity,
  STALE_MARKER_OPACITY,
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

test("MR2: one route is a local dot and two or more is a transfer ring", () => {
  assert.equal(isTransferStation(1), false);
  assert.equal(isTransferStation(2), true);
  assert.equal(isTransferStation(6), true);

  const local = stationMarkStyle(1, INK, PAPER);
  assert.equal(local.radius, STATION_LOCAL_RADIUS);
  assert.equal(local.fillColor, INK);
  assert.equal(local.stroke, false);
  assert.equal(local.weight, 0);

  const transfer = stationMarkStyle(3, INK, PAPER);
  assert.equal(transfer.radius, STATION_TRANSFER_RADIUS);
  assert.equal(transfer.fillColor, PAPER);
  assert.equal(transfer.color, INK);
  assert.equal(transfer.weight, STATION_TRANSFER_WEIGHT);
  assert.equal(transfer.stroke, true);

  // A ring is bigger than a dot, because it has to hold a hole.
  assert.ok(STATION_TRANSFER_RADIUS > STATION_LOCAL_RADIUS);
});

test("MR2: a station with no routes is a local dot, which is the direction that matters", () => {
  // THE MUTATION THIS KILLS is "the transfer ring drawn for single-route stations", and
  // this is its quieter twin. The routes field is optional on the stops endpoint. Against
  // the real static archive 171 stations have one route and 325 have two or more and none
  // has zero, so a backend that stopped serving the index would turn all 496 into transfer
  // rings: a claim about the network made out of a missing value.
  for (const missing of [0, null, undefined]) {
    assert.equal(isTransferStation(missing), false, String(missing));
    assert.equal(stationMarkStyle(missing, INK, PAPER).radius, STATION_LOCAL_RADIUS, String(missing));
    assert.equal(stationMarkStyle(missing, INK, PAPER).stroke, false, String(missing));
  }
});

test("MR2: the two theme colours are the caller's, so a theme swap is a setStyle", () => {
  // The style function never reaches for a token itself, which is what lets MR4 restyle a
  // canvas layer in place instead of rebuilding 496 of them.
  const dark = stationMarkStyle(3, "#f3f2f2", "#201e1d");
  assert.equal(dark.color, "#f3f2f2");
  assert.equal(dark.fillColor, "#201e1d");
});

/* ---------------- labels ---------------- */

test("MR2: a hub label is the same station a transfer ring is", () => {
  assert.equal(stationLabelClass(1), "stn-label");
  assert.equal(stationLabelClass(2), "stn-label hub");
  assert.equal(stationLabelClass(0), "stn-label");
  assert.equal(stationLabelClass(undefined), "stn-label");
  // One predicate behind both, so a station cannot draw a ring and label itself local.
  for (const n of [0, 1, 2, 3, 9]) {
    assert.equal(stationLabelClass(n).includes("hub"), isTransferStation(n), String(n));
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
  const LOCAL = 1;
  const HUB = 3;
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
