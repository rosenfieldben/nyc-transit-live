// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// MR3, the commuter rail grammar. Everything here is pure, which is the point: the brief's
// 3.1 state table is the deliverable of this stage, and a table is a function. Asking it
// one row at a time in node is a stronger test than finding a shape in a screenshot, and it
// is the only way the two rows that no fixture world reaches can be tested at all.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  RAIL_BRANCH_CODES,
  RAIL_NEUTRAL_COLOR,
  railBranchCode,
  railBranchColor,
  railBranchInk,
  railBranchPaint,
  railTagGeometry,
  railTagState,
  railTagSvg,
  railTagChevronPath,
  railStationSvg,
  segmentBearing,
  railDirectionReverses,
  railTrainBearing,
  positionQualifier,
  markerOpacity,
  staleAge,
  readableTextOn,
  contrastRatio,
  FEED_STALE_AFTER_S,
  UNDATED_SYSTEMS,
  RAIL_STATION_BOX,
} = require("./helpers.js");

/* ---------------- the board a row's words are read against ---------------- */

// positionBoard's shape, written out rather than built from an envelope: these tests are
// about the TABLE, and a fake envelope would only add a way for them to be wrong.
const NOW = 1_800_000_000;
const board = (system, { gated = !UNDATED_SYSTEMS.has(system), pollAge = 0 } = {}) => ({
  now: NOW,
  servedAt: NOW,
  system,
  gated,
  pollAge,
});

// A served row plus the kind positionQualifier gives it, so every case below starts from
// what the BACKEND would serve rather than from a kind typed by hand. That is what makes
// this an oracle for the table and not a restatement of railTagState.
function draw(row, { system = "LIRR", before = null, pollAge = 0 } = {}) {
  const b = board(system, { pollAge });
  const position = positionQualifier(row, b);
  const age = row.observed_at == null ? (row.provenance === "retained" ? b.pollAge : null) : NOW - row.observed_at;
  return { kind: position.kind, words: position.words, state: railTagState(row, before, position.kind, age), age };
}

/* ---------------- THE STATE TABLE, ONE CASE PER ROW ---------------- */

/* The seven rows of section 3.1 of docs/design/map-redesign/map-redesign-v3-brief.md.
   Each case names the row, the served row that reaches it, and all four columns. The
   `dim` column is asserted against markerOpacity as well as against the literal, because
   dimming is the freshness contract's and this stage may not grow a second dimming rule:
   the tie below is what kills a tag that dims on its own terms. */

test("MR3 3.1 row 1: reported and unqualified draws a solid body, a filled head, and is not dimmed", () => {
  const { kind, state } = draw({ provenance: "reported", observed_at: NOW - 5 });
  assert.equal(kind, ""); // the app's word for "nothing to say", which is row 1
  assert.equal(state.body, "solid");
  assert.equal(state.head, "filled");
  assert.equal(state.headingTrusted, true);
  assert.equal(state.dim, false);
  assert.equal(state.row, "reported-unqualified");
});

test("MR3 3.1 row 2: reported and qualified keeps the solid body and the filled head, and IS dimmed", () => {
  const { kind, state, age } = draw({ provenance: "reported", observed_at: NOW - 300 });
  assert.equal(kind, "aged");
  assert.equal(state.body, "solid");
  assert.equal(state.head, "filled");
  assert.equal(state.dim, true);
  // The tie: the table's opacity column and the contract's dimming are one rule.
  assert.equal(state.dim, markerOpacity(age) < 1);
});

test("MR3 3.1 row 3: estimated draws an OUTLINED body and a FILLED head, which is the row the table exists for", () => {
  const { kind, state } = draw({ provenance: "estimated", observed_at: NOW - 5 });
  assert.equal(kind, "estimated");
  // "we know where it is going but not exactly where it is" (brief 3.1).
  assert.equal(state.body, "outlined");
  assert.equal(state.head, "filled");
  assert.equal(state.headingTrusted, true);
  assert.equal(state.dim, false);
});

test("MR3 3.1 row 4: placed draws an outlined body AND an outlined head", () => {
  const { kind, state } = draw({ provenance: "placed", observed_at: NOW - 5 });
  assert.equal(kind, "placed");
  assert.equal(state.body, "outlined");
  assert.equal(state.head, "outlined");
  assert.equal(state.headingTrusted, true);
  assert.equal(state.dim, false);
});

test("MR3 3.1 row 5: retained wears whatever the last state was, dimmed", () => {
  const row = { provenance: "retained", observed_at: null };
  // Retained after a GPS fix: solid and headed, as it was drawn.
  const wasReported = draw(row, { before: "reported", pollAge: 300 });
  assert.equal(wasReported.kind, "retained");
  assert.equal(wasReported.state.body, "solid");
  assert.equal(wasReported.state.head, "filled");
  assert.equal(wasReported.state.dim, true);

  // Retained after a placement: outlined and outlined, as it was drawn.
  const wasPlaced = draw(row, { before: "placed", pollAge: 300 });
  assert.equal(wasPlaced.state.body, "outlined");
  assert.equal(wasPlaced.state.head, "outlined");
  assert.equal(wasPlaced.state.dim, true);

  // Retained after an estimate keeps the estimate's disagreeing pair.
  const wasEstimated = draw(row, { before: "estimated", pollAge: 300 });
  assert.equal(wasEstimated.state.body, "outlined");
  assert.equal(wasEstimated.state.head, "filled");

  // A retained row the page never saw is read PESSIMISTICALLY, the same way
  // railroadHollow reads it: the solid body is the one mark that claims GPS, and a dot
  // rather than a chevron because a heading it never had is not one to draw.
  const neverSeen = draw(row, { before: null, pollAge: 300 });
  assert.equal(neverSeen.state.body, "outlined");
  assert.equal(neverSeen.state.head, "outlined");
  assert.equal(neverSeen.state.headingTrusted, false);
});

test("MR3 3.1 row 6: an age-gated row with no clock draws an outlined body and refuses a heading", () => {
  // LIRR dates every fix (the freshness contract's 3.3 table), so a reported LIRR row with
  // no observed_at is an ANOMALY in the contract's own words, and clause (c) says it out
  // loud. The tag says it too.
  const { kind, words, state } = draw({ provenance: "reported", observed_at: null }, { system: "LIRR" });
  assert.equal(kind, "unknown");
  assert.equal(words, "live GPS, age unknown");
  assert.equal(state.body, "outlined");
  assert.equal(state.head, "outlined");
  // THE HEADING IS REFUSED, not merely unavailable: even handed a bearing, this row draws a
  // dot. That is the one thing headingTrusted exists to say.
  assert.equal(state.headingTrusted, false);
  assert.match(railTagSvg({ system: "LIRR", code: "BAB", color: "00985F", state, bearing: 42 }), /<circle /);

  /* THE ONE DEVIATION FROM THE TABLE, and it is in the opacity column only. The table says
     dimmed; this is not, because dimming is markerOpacity's and markerOpacity reads an age,
     and the whole content of this row is that there is no age. staleAge(null) is false, so
     dimming here would tell a rider "this is old" about a train whose age the same tag has
     just said is unknown. The pessimism the row is for is carried by the body and the head
     instead, which is the stronger statement anyway: outlined and a dot. Asserted rather
     than left implicit, so that a later stage that decides to dim it has to come here. */
  assert.equal(state.dim, false);
  assert.equal(markerOpacity(null), 1);
});

test("MR3 3.1 row 7: an undated Metro-North fix draws solid and live, by policy", () => {
  // Metro-North's positions are not age-gated at all (the contract's 3.3 table: gating on a
  // stamp that is a copy of a lagging header "would mark a live fleet stale"), so the SAME
  // row that is row 6 on the LIRR is row 1 on Metro-North. Asked as its own case because it
  // is a policy, and a change that left rows 1 through 6 alone could still break it.
  assert.equal(UNDATED_SYSTEMS.has("MNR"), true);
  const { kind, words, state } = draw({ provenance: "reported", observed_at: null }, { system: "MNR" });
  assert.equal(kind, "");
  assert.equal(words, "live GPS");
  assert.equal(state.body, "solid");
  assert.equal(state.head, "filled");
  assert.equal(state.headingTrusted, true);
  assert.equal(state.dim, false);
});

test("MR3 3.1: the table's dim column is the freshness contract's, at the threshold and on both sides of it", () => {
  // Not a row of the table but the rule under its opacity column, asked where it turns over.
  // A second dimming rule inside the tag would pass every row above and fail here.
  for (const age of [0, 1, FEED_STALE_AFTER_S - 1, FEED_STALE_AFTER_S, FEED_STALE_AFTER_S + 1, 10_000]) {
    const state = railTagState({ provenance: "reported" }, null, staleAge(age) ? "aged" : "", age);
    assert.equal(state.dim, staleAge(age), `age ${age}`);
    assert.equal(state.dim, markerOpacity(age) < 1, `age ${age} disagrees with markerOpacity`);
  }
});

/* ---------------- the code table ---------------- */

test("MR3 codes: the LIRR and Metro-North tables are keyed by NAME and cover every route the feeds serve", () => {
  // Twelve LIRR branches and six Metro-North lines (brief 6.1 and 6.2), which is what the
  // live feeds carry. The count is asserted so a row cannot be dropped silently.
  assert.equal(Object.keys(RAIL_BRANCH_CODES.LIRR).length, 12);
  assert.equal(Object.keys(RAIL_BRANCH_CODES.MNR).length, 6);
  assert.equal(railBranchCode("LIRR", "1", "Babylon Branch"), "BAB");
  assert.equal(railBranchCode("LIRR", "12", "City Terminal Zone"), "CTZ");
  assert.equal(railBranchCode("LIRR", "13", "Greenport Service"), "GRN");
  assert.equal(railBranchCode("MNR", "3", "New Haven"), "NH");
  assert.equal(railBranchCode("MNR", "6", "Waterbury"), "WAT");
  // KEYED BY NAME, NOT BY ID, which is the brief's finding: the v2 table's ids were sample
  // data. So the SAME name gives the same code whatever id it arrives under, and an id that
  // moved cannot silently relabel a branch.
  assert.equal(railBranchCode("LIRR", "77", "Babylon Branch"), "BAB");
  // And an id alone is not a code: this is the mutation "the code table keyed by id again".
  assert.equal(railBranchCode("LIRR", "1", null), "1");
});

test("MR3 codes: NJ Transit takes the feed's own short name, and an unknown route falls back to its id", () => {
  // The feed publishes route_short_name for all twelve, so hand-tabling it would be a second
  // answer to a question the feed answers.
  assert.equal(railBranchCode("NJT", "9", "Northeast Corridor", "NEC"), "NEC");
  assert.equal(railBranchCode("NJT", "10", "North Jersey Coast Line", "NJCL"), "NJCL");
  // A short name wins over a table entry, so a feed that starts publishing one for the
  // railroads is followed rather than overridden.
  assert.equal(railBranchCode("LIRR", "1", "Babylon Branch", "BAB1"), "BAB1");
  // THE FALLBACK: route 17, the event-only Meadowlands Rail Line, is a real id that never
  // appears on /api/njt-routes, so a route nothing names is the NORMAL case on that layer.
  assert.equal(railBranchCode("NJT", "17", null, null), "17");
  assert.equal(railBranchColor(null), RAIL_NEUTRAL_COLOR);
  assert.equal(railBranchColor(""), RAIL_NEUTRAL_COLOR);
  // The neutral has to be readable, which is the one thing a fallback colour can get wrong:
  // readableTextOn's answer on it clears 4.5, which v2's #607d8b did not for either ink.
  assert.ok(contrastRatio(readableTextOn(RAIL_NEUTRAL_COLOR), RAIL_NEUTRAL_COLOR) >= 4.5);
});

test("MR3 colours: a feed hex arrives with no '#' and is not normalised", () => {
  assert.equal(railBranchColor("00985F"), "#00985F"); // routes.txt publishes no "#"
  assert.equal(railBranchColor("#00985F"), "#00985F"); // and one that does is left alone
  assert.equal(railBranchColor("  00985F "), "#00985F");
});

test("MR3 colours: the feed's ink is used where it is legible and recomputed where it is not", () => {
  /* THE AGENCIES PUBLISH AN INK THAT DOES NOT ALWAYS WORK, and that is measured rather than
     assumed. Of the 19 (route_color, route_text_color) pairs the LIRR and Metro-North feeds
     publish, ELEVEN clear 4.5 as published and EIGHT do not:

       four are a readable fill under an unreadable ink, so the ink is recomputed and the
         fill kept: Babylon 3.71, Oyster Bay 2.92, Long Beach 2.98, Hudson 3.65, all of them
         white on a mid-tone;
       four are the New Haven family's shared red, which no ink clears, so the fill moves.

     This qualifies a sentence claude/railroad-route-colors put in two docstrings, that "a
     renderer can trust a railroad text_color". It can be PREFERRED, which is what this does.
     It cannot be trusted, and the four ratios above are why. */
  assert.equal(railBranchInk("A626AA", "FFFFFF"), "#FFFFFF"); // published and legible: kept
  assert.equal(railBranchInk("CE8E00", "121212"), "#121212"); // the agency's dark ink, kept
  assert.equal(railBranchInk("00985F", "FFFFFF"), "#1a1a1a"); // published at 3.71: recomputed
  assert.equal(railBranchInk("00AF3F", "FFFFFF"), "#1a1a1a"); // 2.92, the worst of the four
  assert.equal(railBranchInk("009B3A", "FFFFFF"), "#1a1a1a"); // 3.65
  // Computed where the feed said nothing at all, which is every NJ Transit route. The Bergen
  // and Main lines share its yellow, which needs DARK ink: the case a hardcoded white ruins.
  assert.equal(railBranchInk("FFD411"), "#1a1a1a");
  assert.equal(railBranchInk("FFD411", ""), "#1a1a1a");
  assert.equal(railBranchInk("075AAA"), "#ffffff");
});

test("MR3 colours: every colour the three feeds publish carries legible type, and the ONE that cannot moves its fill", () => {
  /* ALL 26 PUBLISHED COLOURS, measured 2026-09-19 off the live archives. The assertion is on
     the ratio of the ink the tag actually prints against the fill it actually paints, which
     is the only pair a rider sees. Asserting merely that a choice was made is what the note
     at railroadColor warns about. */
  const PUBLISHED = [
    // LIRR's 13, each with the route_text_color the feed publishes beside it.
    ["00985F", "FFFFFF"], ["CE8E00", "121212"], ["00AF3F", "FFFFFF"], ["A626AA", "FFFFFF"],
    ["00B2A9", "121212"], ["FF6319", "FFFFFF"], ["6E3219", "FFFFFF"], ["00A1DE", "121212"],
    ["C60C30", "FFFFFF"], ["006EC7", "FFFFFF"], ["60269E", "FFFFFF"], ["4D5357", "FFFFFF"],
    ["A626AA", "FFFFFF"],
    // Metro-North's 6. Four of them share EE0034, which is why the move below is four rows.
    ["EE0034", "FFFFFF"], ["EE0034", "FFFFFF"], ["EE0034", "FFFFFF"], ["EE0034", "FFFFFF"],
    ["009B3A", "FFFFFF"], ["0039A6", "FFFFFF"],
    // NJ Transit's 12, every one of which publishes route_color and NO route_text_color.
    ["075AAA", ""], ["E66859", ""], ["FFD411", ""], ["08A652", ""], ["A4C9AA", ""],
    ["DD3439", ""], ["03A3DF", ""], ["94219A", ""], ["E87725", ""], ["F2A537", ""],
    ["C1AA72", ""], ["E87725", ""],
  ];
  let moved = 0;
  let recomputed = 0;
  for (const [hex, tc] of PUBLISHED) {
    const paint = railBranchPaint(hex, tc);
    const ratio = contrastRatio(paint.ink, paint.fill);
    assert.ok(ratio >= 4.5, `${hex} prints ink at only ${ratio?.toFixed(3)}`);
    if (paint.moved) moved += 1;
    else if (tc && paint.ink.toLowerCase() !== `#${tc}`.toLowerCase()) recomputed += 1;
    // THE LINE IS NEVER MOVED, whatever the block did: a polyline carries no type.
    assert.equal(railBranchColor(hex), `#${hex}`);
  }
  /* THE THREE COUNTS, asserted so a change that started moving every fill, or stopped
     rescuing these, is visible rather than merely still green. Measured 2026-09-19 over the
     19 pairs the two MTA railroad feeds publish plus NJ Transit's 12 inkless routes. */
  assert.equal(moved, 4, "only the New Haven family's shared red should move its fill");
  assert.equal(recomputed, 4, "Babylon, Oyster Bay, Long Beach and Hudson publish an illegible ink");

  /* THE ONE THAT MOVES, named and measured. EE0034 takes white at 4.48 and dark at 3.88, so
     nothing clears on the colour as published; a 1% step toward black takes white to 4.55. */
  assert.equal(contrastRatio("#ffffff", "#EE0034").toFixed(2), "4.48");
  assert.equal(contrastRatio("#1a1a1a", "#EE0034").toFixed(2), "3.88");
  const newHaven = railBranchPaint("EE0034", "FFFFFF");
  assert.equal(newHaven.fill, "#ec0033");
  assert.equal(newHaven.ink, "#ffffff");
  assert.equal(newHaven.moved, true);
  assert.ok(contrastRatio(newHaven.ink, newHaven.fill) >= 4.5);

  // AND AN UNREADABLE INK OVER A READABLE FILL IS A WRONG INK, NOT A WRONG COLOUR: the fill
  // stays and the ink is recomputed. A feed publishing white on its yellow is this case.
  const yellowWithWhite = railBranchPaint("FFD411", "FFFFFF");
  assert.equal(yellowWithWhite.fill, "#FFD411");
  assert.equal(yellowWithWhite.ink, "#1a1a1a");
  assert.equal(yellowWithWhite.moved, false);
});

/* ---------------- the tag's arithmetic ---------------- */

test("MR3 tag width: the agency block is the agency's and the branch block is the code's length", () => {
  // README: agency 11px for "L" and "M", 16px for "NJ"; branch code.length * 5.6 + 7.
  assert.deepEqual(railTagGeometry("LIRR", "BAB"), {
    glyph: "L", agencyWidth: 11, codeWidth: 24, width: 35, height: 13, centre: 17.5,
  });
  assert.equal(railTagGeometry("MNR", "NH").glyph, "M");
  assert.equal(railTagGeometry("NJT", "NEC").glyph, "NJ");
  // 3 chars: 3 * 5.6 + 7 = 23.8, rounded once at the end.
  assert.equal(railTagGeometry("LIRR", "BAB").codeWidth, 24);
  assert.equal(railTagGeometry("MNR", "NH").codeWidth, Math.round(2 * 5.6 + 7)); // 18
  assert.equal(railTagGeometry("NJT", "NJCL").codeWidth, Math.round(4 * 5.6 + 7)); // 29
  // Monotonic in the code's length, which is the property a tag that truncated would break.
  const widths = ["A", "AB", "ABC", "ABCD", "ABCDE"].map((c) => railTagGeometry("LIRR", c).width);
  for (let i = 1; i < widths.length; i++) assert.ok(widths[i] > widths[i - 1]);
  // The centre is HALF THE WIDTH UNROUNDED, so the head sits on the line for an odd tag.
  assert.equal(railTagGeometry("MNR", "NH").centre, (11 + 18) / 2);
  // An unknown system takes NJ Transit's wider block rather than throwing, because the
  // fallback for a code is the route id and ids run longer than codes do.
  assert.equal(railTagGeometry("AMTK", "NE").agencyWidth, 16);
});

test("MR3 tag markup: the body, the head and the box say what the state said", () => {
  const solidFilled = railTagSvg({
    system: "LIRR", code: "BAB", color: "00985F", textColor: "FFFFFF",
    state: railTagState({ provenance: "reported" }, null, "", 5), bearing: 90,
  });
  // The box is 30 tall and the head is centred on y 21, which is what puts it on the track.
  assert.match(solidFilled, /viewBox="0 0 35 30"/);
  assert.match(solidFilled, /rotate\(90 17\.5 21\)/);
  assert.match(solidFilled, /<path /); // a chevron, because a bearing was given
  assert.doesNotMatch(solidFilled, /<circle /);
  assert.match(solidFilled, /fill="#00985F"/); // the branch block in the feed's own colour
  // AND DARK INK ON IT, not the FFFFFF the LIRR publishes for this route: white on Babylon's
  // green is 3.71, so the ink is recomputed while the colour is kept (railBranchPaint).
  assert.match(solidFilled, /fill="#1a1a1a"/);
  assert.match(solidFilled, /class="rail-tag rail-tag-solid rail-head-filled"/);
  // PAPER AND INK AS INLINE STYLE, not as an SVG attribute: `fill="var(--paper)"` is not a
  // paint value in SVG 1.1 and draws black.
  assert.match(solidFilled, /style="fill: var\(--paper\)"/);
  assert.doesNotMatch(solidFilled, /fill="var\(--/);

  const outlinedFilled = railTagSvg({
    system: "NJT", code: "NEC", color: "DD3439",
    state: railTagState({ provenance: "estimated" }, null, "estimated", 5), bearing: 180,
  });
  assert.match(outlinedFilled, /class="rail-tag rail-tag-outlined rail-head-filled"/);
  assert.match(outlinedFilled, /stroke-width="1\.2"/); // the outlined box's stroke
  assert.match(outlinedFilled, /<line x1="16"/); // the divider at the agency block's edge
  assert.match(outlinedFilled, /height="2\.5"/); // the branch colour reduced to a stripe

  // No bearing at all is a dot whatever the state trusts, because a chevron at an arbitrary
  // angle would be a heading we do not have.
  const noHeading = railTagSvg({
    system: "MNR", code: "HUD", color: "009B3A",
    state: railTagState({ provenance: "reported" }, null, "", 5), bearing: null,
  });
  assert.match(noHeading, /<circle /);
  assert.doesNotMatch(noHeading, /<path /);
});

test("MR3 tag markup: the chevron is written from the tag's centre so one path serves any width", () => {
  assert.equal(railTagChevronPath(17.5), "M 17.5 15.5 L 22.5 24.5 L 17.5 22 L 12.5 24.5 Z");
  assert.equal(railTagChevronPath(22.5), "M 22.5 15.5 L 27.5 24.5 L 22.5 22 L 17.5 24.5 Z");
});

test("MR3 station square: one 10x10 shape in a 20x20 hit box, for all three families", () => {
  const svg = railStationSvg();
  assert.equal(RAIL_STATION_BOX, 20); // the hit target, WCAG 2.2's floor is met by the box
  assert.match(svg, /viewBox="0 0 20 20"/);
  // The drawn square stays 10 while the click box is 20: an 8x8 rect in a 1.6 stroke,
  // centred, which is a 10px mark and not a 20px blob at city zoom.
  assert.match(svg, /<rect x="6" y="6" width="8" height="8"/);
  assert.match(svg, /stroke-width="1\.6"/);
  assert.match(svg, /style="fill: var\(--paper\); stroke: var\(--ink\)"/);
  // A SQUARE, and nothing round: this is the half of "a square always means regional rail"
  // that can be asserted without a page.
  assert.doesNotMatch(svg, /<circle|rx=/);
});

/* ---------------- bearing ---------------- */

test("MR3 bearing: a segment's azimuth is degrees clockwise from north", () => {
  assert.equal(Math.round(segmentBearing([40.7, -74.0], [40.8, -74.0])), 0); // due north
  assert.equal(Math.round(segmentBearing([40.7, -74.0], [40.6, -74.0])), 180); // due south
  assert.equal(Math.round(segmentBearing([40.7, -74.0], [40.7, -73.9])), 90); // due east
  assert.equal(Math.round(segmentBearing([40.7, -74.0], [40.7, -74.1])), 270); // due west
  assert.equal(segmentBearing([40.7, -74.0], [40.7, -74.0]), null); // a point points nowhere
  assert.equal(segmentBearing(null, [40.7, -74.0]), null);
  // The longitude is scaled by the latitude's cosine, so a square degree box is not read as
  // a 45 degree run: at this latitude a degree of longitude is about 0.76 of a degree of
  // latitude, so equal deltas read east of northeast.
  const diagonal = segmentBearing([40.7, -74.0], [40.8, -73.9]);
  assert.ok(diagonal > 30 && diagonal < 45, `equal deltas read ${diagonal}`);
});

test("MR3 bearing: the SERVED direction decides the sign, and only 'Inbound' reverses", () => {
  assert.equal(railDirectionReverses("Inbound"), true);
  assert.equal(railDirectionReverses("inbound"), true);
  assert.equal(railDirectionReverses(" Inbound "), true);
  assert.equal(railDirectionReverses("Outbound"), false);
  assert.equal(railDirectionReverses(null), false);
  assert.equal(railDirectionReverses(undefined), false);
  // NOT A HEADSIGN. v3.1 overrules v2's `headsign === "New York"` in as many words, and NJ
  // Transit serves no direction field at all, so a headsign must never reach this.
  assert.equal(railDirectionReverses("New York"), false);
  assert.equal(railDirectionReverses("New York Penn Station"), false);
});

test("MR3 bearing: a slice is already prev-to-next, and Inbound turns it around", () => {
  const northbound = { points: [[40.70, -74.0], [40.80, -74.0]] };
  // Outbound: the slice's own direction, untouched.
  assert.equal(
    Math.round(railTrainBearing({ _route: northbound, direction: "Outbound", latitude: 40.75, longitude: -74.0 })),
    0,
  );
  // Inbound: the same slice, reversed. THIS IS THE SIGN TEST, and the mutation it kills is
  // dropping the reversal.
  assert.equal(
    Math.round(railTrainBearing({ _route: northbound, direction: "Inbound", latitude: 40.75, longitude: -74.0 })),
    180,
  );
  // The two differ by exactly half a turn, which is the property, not the two numbers.
  const out = railTrainBearing({ _route: northbound, direction: "Outbound" });
  const inb = railTrainBearing({ _route: northbound, direction: "Inbound" });
  assert.equal((inb - out + 360) % 360, 180);
});

test("MR3 bearing: the served bearing wins, the anchors are next, and nothing left is a dot", () => {
  // 1. A GPS train that sent a bearing: used as served, and NEVER reversed, because a served
  // bearing is already the way the train points and reversing it turns the train around.
  const served = { bearing: 45, direction: "Inbound", _route: { points: [[40.7, -74.0], [40.8, -74.0]] } };
  assert.equal(railTrainBearing(served), 45);
  assert.equal(railTrainBearing({ bearing: 370 }), 10); // normalised into [0, 360)
  assert.equal(railTrainBearing({ bearing: -90 }), 270);
  // 2. No slice, but served anchors: prev to the drawn position. This is NJ Transit's case,
  // which serves no direction word at all, so the anchors ARE the served direction.
  const anchored = { prev_lat: 40.70, prev_lon: -74.0, latitude: 40.80, longitude: -74.0 };
  assert.equal(Math.round(railTrainBearing(anchored)), 0);
  assert.equal(Math.round(railTrainBearing({ ...anchored, direction: "Inbound" })), 180);
  // 3. Nothing to say: a reported fix with no bearing, no slice and no anchors.
  assert.equal(railTrainBearing({ provenance: "reported", latitude: 40.7, longitude: -74.0 }), null);
  assert.equal(railTrainBearing({}), null);
  assert.equal(railTrainBearing(null), null);
  // A one-point slice is not a direction, and falls through to the anchors rather than
  // reading points[0] twice.
  assert.equal(railTrainBearing({ _route: { points: [[40.7, -74.0]] }, ...anchored }), 0);
});
