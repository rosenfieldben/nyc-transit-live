// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// MR3, the commuter rail grammar. Everything here is pure, which is the point: the brief's
// 3.1 state table is the deliverable of this stage, and a table is a function. Asking it
// one row at a time in node is a stronger test than finding a shape in a screenshot, and it
// is the only way the two rows that no fixture world reaches can be tested at all.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const {
  RAIL_BRANCH_CODES,
  RAIL_NEUTRAL_COLOR,
  NJT_FALLBACK_COLOR,
  railBranchCode,
  railBranchColor,
  railBranchInk,
  railBranchPaint,
  railTagGeometry,
  railTagState,
  railroadHollow,
  railTagSvg,
  railTagChevronPath,
  railStationSvg,
  segmentBearing,
  railTrainBearing,
  polylineCumLengths,
  staticPayloadHasField,
  positionQualifier,
  markerOpacity,
  STALE_MARKER_OPACITY,
  staleAge,
  observationDimAge,
  observationGated,
  OBSERVATION_GATED,
  AGE_UNKNOWN,
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
  const own = row.observed_at == null ? (row.provenance === "retained" ? b.pollAge : null) : NOW - row.observed_at;
  // THROUGH THE PRODUCTION RULE, not through a copy of it: observationDimAge is what
  // vehicleMarkerAge applies for every system, and a test that computed the age itself would
  // pass while the page dimmed differently. This is what makes the dim column below an oracle
  // for what a rider sees rather than for what this file believes.
  const age = observationDimAge(row, own, b.gated);
  /* `dimmed` IS markerOpacity's ANSWER, NOT A FIELD ON THE STATE (round 4). railTagState used to
     return a `dim` field and nothing in the app read it: every rail marker's opacity comes from
     markerOpacity(vehicleMarkerAge(...)) applied to the marker itself. Asserting the table's
     opacity column against the function that actually dims the marker is the oracle; asserting
     it against a second copy of staleAge inside the table was a tautology. */
  return {
    kind: position.kind,
    words: position.words,
    state: railTagState(row, before, position.kind),
    age,
    dimmed: markerOpacity(age) < 1,
  };
}

/* ---------------- THE STATE TABLE, ONE CASE PER ROW ---------------- */

/* The seven rows of section 3.1 of docs/design/map-redesign/map-redesign-v3-brief.md.
   Each case names the row, the served row that reaches it, and all four columns. The opacity
   column is asked of markerOpacity through the `dimmed` field draw() computes, because dimming
   is the freshness contract's and this stage may not grow a second dimming rule: what the rows
   below assert is what a rider's marker is actually drawn at. */

test("MR3 3.1 row 1: reported and unqualified draws a solid body, a filled head, and is not dimmed", () => {
  const { kind, state, dimmed } = draw({ provenance: "reported", observed_at: NOW - 5 });
  assert.equal(kind, ""); // the app's word for "nothing to say", which is row 1
  assert.equal(state.body, "solid");
  assert.equal(state.head, "filled");
  assert.equal(state.headingTrusted, true);
  assert.equal(dimmed, false);
  assert.equal(state.row, "reported-unqualified");
});

test("MR3 3.1 row 2: reported and qualified keeps the solid body and the filled head, and IS dimmed", () => {
  const { kind, state, age, dimmed } = draw({ provenance: "reported", observed_at: NOW - 300 });
  assert.equal(kind, "aged");
  assert.equal(state.body, "solid");
  assert.equal(state.head, "filled");
  assert.equal(dimmed, true);
  // The tie: the table's opacity column IS the contract's dimming, at the value a rider sees.
  assert.equal(markerOpacity(age), STALE_MARKER_OPACITY);
});

test("MR3 3.1 row 3: estimated draws an OUTLINED body and a FILLED head, which is the row the table exists for", () => {
  const { kind, state, dimmed } = draw({ provenance: "estimated", observed_at: NOW - 5 });
  assert.equal(kind, "estimated");
  // "we know where it is going but not exactly where it is" (brief 3.1).
  assert.equal(state.body, "outlined");
  assert.equal(state.head, "filled");
  assert.equal(state.headingTrusted, true);
  assert.equal(dimmed, false);
});

test("MR3 3.1 row 4: placed draws an outlined body AND an outlined head", () => {
  const { kind, state, dimmed } = draw({ provenance: "placed", observed_at: NOW - 5 });
  assert.equal(kind, "placed");
  assert.equal(state.body, "outlined");
  assert.equal(state.head, "outlined");
  assert.equal(state.headingTrusted, true);
  assert.equal(dimmed, false);
});

test("MR3 3.1 row 5: retained wears whatever the last state was, dimmed", () => {
  const row = { provenance: "retained", observed_at: null };
  // Retained after a GPS fix: solid and headed, as it was drawn.
  const wasReported = draw(row, { before: "reported", pollAge: 300 });
  assert.equal(wasReported.kind, "retained");
  assert.equal(wasReported.state.body, "solid");
  assert.equal(wasReported.state.head, "filled");
  assert.equal(wasReported.dimmed, true);

  // Retained after a placement: outlined and outlined, as it was drawn.
  const wasPlaced = draw(row, { before: "placed", pollAge: 300 });
  assert.equal(wasPlaced.state.body, "outlined");
  assert.equal(wasPlaced.state.head, "outlined");
  assert.equal(wasPlaced.dimmed, true);

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
  const { kind, words, state, age, dimmed } = draw({ provenance: "reported", observed_at: null }, { system: "LIRR" });
  assert.equal(kind, "unknown");
  assert.equal(words, "live GPS, age unknown");
  assert.equal(state.body, "outlined");
  assert.equal(state.head, "outlined");
  // THE HEADING IS REFUSED, not merely unavailable: even handed a bearing, this row draws a
  // dot. That is the one thing headingTrusted exists to say.
  assert.equal(state.headingTrusted, false);
  assert.match(railTagSvg({ system: "LIRR", code: "BAB", color: "00985F", state, bearing: 42 }), /<circle /);

  /* AND IT DIMS, which is the operator's ruling on finding N3 and the 6.3 erratum dated
     2026-09-19 in docs/design/freshness-contract.md. The table said dimmed; the code had fallen
     into drawing it bright, because staleAge read an age and there was none, so a row the
     contract itself calls an anomaly rendered exactly like a fix five seconds old.

     DIMMING CARRIES NOT-FRESH, NOT AN AGE. AGE_UNKNOWN is the absence of a clock rather than a
     large number, and the three assertions below are the whole rule: the row takes that value,
     staleAge names it stale, and the opacity a rider sees is the contract's 0.45. THE MUTATION
     IS THE NULL CASE REVERTED, and it kills this. */
  assert.equal(age, AGE_UNKNOWN);
  assert.equal(staleAge(age), true);
  assert.equal(dimmed, true);
  assert.equal(markerOpacity(age), STALE_MARKER_OPACITY);
  assert.equal(markerOpacity(age), 0.45);
  // AND ONLY FOR A ROW THAT WAS OWED A CLOCK. A row with no stamp on a system that dates
  // nothing keeps its null, which is row 7 and is asserted there too.
  assert.equal(observationDimAge({ observed_at: null }, null, false), null);
  // A row that HAS a clock the page could not measure against keeps its null as well: that is a
  // gap in the page, not an anomaly in the row, and dimming the whole map for it would be the
  // opposite of honest.
  assert.equal(observationDimAge({ observed_at: 123 }, null, true), null);
});

test("MR3 3.1 row 7: an undated Metro-North fix draws solid and live, by policy", () => {
  // Metro-North's positions are not age-gated at all (the contract's 3.3 table: gating on a
  // stamp that is a copy of a lagging header "would mark a live fleet stale"), so the SAME
  // row that is row 6 on the LIRR is row 1 on Metro-North. Asked as its own case because it
  // is a policy, and a change that left rows 1 through 6 alone could still break it.
  assert.equal(UNDATED_SYSTEMS.has("MNR"), true);
  const { kind, words, state, dimmed } = draw({ provenance: "reported", observed_at: null }, { system: "MNR" });
  assert.equal(kind, "");
  assert.equal(words, "live GPS");
  assert.equal(state.body, "solid");
  assert.equal(state.head, "filled");
  assert.equal(state.headingTrusted, true);
  assert.equal(dimmed, false);
});

test("MR3 3.1: the opacity column is the freshness contract's, at the threshold and on both sides", () => {
  /* Not a row of the table but the rule under its opacity column, asked where it turns over,
     and asked of the ONE function that dims a marker. The table holds no opacity of its own
     (round 4): railTagState returns no dim field, so there is no second rule to drift. The
     assertion that a tag cannot dim on its own terms is therefore structural rather than
     numeric, and it is the one below about the returned keys. */
  for (const age of [0, 1, FEED_STALE_AFTER_S - 1, FEED_STALE_AFTER_S, FEED_STALE_AFTER_S + 1, 10_000]) {
    assert.equal(markerOpacity(age) < 1, staleAge(age), `age ${age}`);
    assert.equal(markerOpacity(age), staleAge(age) ? STALE_MARKER_OPACITY : 1, `age ${age}`);
  }
  /* THE TABLE CARRIES NO OPACITY AND NO AGE, asserted on the keys of EVERY ROW rather than one.
     This is the mutation "put dim back", and the first version of this assertion asked only the
     fresh `reported` row, so a `dim` restored on the estimated branch alone SURVIVED it. The
     table has five return sites; a rule that may not exist has to be absent from all of them.

     WHY IT MAY NOT EXIST AT ALL: a field here is a second expression of the freshness contract's
     dimming, living in a table nothing draws from, and the three commits it lived for are how the
     row 6 argument in helpers.js came to contradict the operator's own ruling on N3. The opacity
     a rider sees is markerOpacity's, applied to the marker.

     AND THE EXTRA ARGUMENT IS REFUSED TOO. `railTagState.length` is 1, because parameters after
     the first default do not count, so arity alone cannot catch a fourth parameter; what catches
     it is that the answer must not CHANGE when one is passed. */
  const ROWS = [
    [{ provenance: "reported" }, null, ""],
    [{ provenance: "reported" }, null, "aged"],
    [{ provenance: "estimated" }, null, "estimated"],
    [{ provenance: "placed" }, null, "placed"],
    [{ provenance: "reported" }, null, "unknown"],
    [{ provenance: "retained" }, "reported", "retained"],
    [{ provenance: "retained" }, null, "retained"],
  ];
  for (const [row, before, kind] of ROWS) {
    const keys = Object.keys(railTagState(row, before, kind)).sort();
    assert.deepEqual(keys, ["body", "head", "headingTrusted", "row"], `kind ${kind || "(fresh)"}`);
    // An age handed to it changes nothing, which is what "this table has no opacity" means.
    assert.deepEqual(
      railTagState(row, before, kind, 10_000),
      railTagState(row, before, kind),
      `kind ${kind || "(fresh)"} answers differently when handed an age`,
    );
  }
});

test("MR3 3.1: the body is railroadHollow's answer, and row 6 is the only place it is not", () => {
  /* ONE EXPRESSION OF THE BODY RULE, held by a test rather than by a comment. railTagState
     CALLS railroadHollow, so this asserts the tie across every provenance and every `before`
     the app can produce: an outlined body must mean hollow, and a solid body must mean not,
     with the single documented exception. A copy of the rule inside railTagState would pass
     every row of the table and fail here the first time either changed. */
  const PROVENANCES = ["reported", "estimated", "placed", "retained", "unknown", undefined];
  const BEFORES = [null, "reported", "estimated", "placed"];
  let exceptions = 0;
  for (const provenance of PROVENANCES) {
    for (const before of BEFORES) {
      for (const kind of ["", "aged", "estimated", "placed", "retained", "unknown"]) {
        const row = { provenance };
        const state = railTagState(row, before, kind);
        const hollow = railroadHollow(row, before);
        if (kind === "unknown" && !hollow) {
          // ROW 6: railroadHollow says solid (the provenance is `reported`) and the table says
          // outlined. Counted rather than skipped, so the exception cannot quietly widen.
          assert.equal(state.body, "outlined");
          exceptions += 1;
          continue;
        }
        assert.equal(state.body, hollow ? "outlined" : "solid", `${provenance}/${before}/${kind}`);
      }
    }
  }
  // Exactly the reported-and-retained-from-reported combinations, and nothing else, reach the
  // exception: 4 befores for `reported` plus the one retained-from-reported case.
  assert.equal(exceptions, 5);
});

/* ---------------- R-d: the deploy boundary on a cached static payload ---------------- */

test("MR3 R-d: a routes payload is re-read once when the field this stage needs is absent from all of it", () => {
  /* THE PREDICATE THAT DECIDES THE RE-READ. systems/shared.js's fetchRoutesPayload does the
     fetching and cannot be asked in node; this is the judgement it makes, and it is the part that
     can be wrong. /api/njt-routes is static-derived under an hour-long cache, so on the deploy
     that adds route_short_name a browser can hold a well formed payload without it and every NJ
     Transit tag prints "9" instead of "NEC" for up to an hour. */
  const withField = [{ route: "9", short_name: "NEC" }, { route: "17" }];
  const withoutField = [{ route: "9" }, { route: "17" }];
  assert.equal(staticPayloadHasField(withField, "short_name"), true);
  assert.equal(staticPayloadHasField(withoutField, "short_name"), false);

  /* SOME, NOT EVERY, and this is the assertion that keeps the re-read from being permanent.
     Route 17 is the event-only Meadowlands line: it has no trips in an ordinary publication, so
     it never reaches this endpoint with a short name, and a predicate keyed on EVERY entry would
     re-read past the cache on every single load forever. `withField` above is exactly that shape
     and must read true. THIS IS THE MUTATION: some -> every. */
  assert.equal(staticPayloadHasField([{ short_name: "NEC" }, {}, {}], "short_name"), true);

  // A NULL VALUE IS ABSENT. A backend that knows a nullable column and has nothing to put in it
  // serves null, which is indistinguishable here from one that never heard of the field, and the
  // pessimistic reading costs one request where the optimistic one would skip the re-read on the
  // exact shape a half-rolled deploy of a nullable column takes.
  assert.equal(staticPayloadHasField([{ color: null }, { color: null }], "color"), false);
  assert.equal(staticPayloadHasField([{ color: null }, { color: "00985F" }], "color"), true);

  // AND NOTHING IS NOT A PAYLOAD. An empty array is the warming state the loaders already retry
  // on and a null is a failed read: neither is a payload missing a field, and re-reading either
  // past the cache would be a request spent on a question that is not being asked.
  assert.equal(staticPayloadHasField([], "color"), false);
  assert.equal(staticPayloadHasField(null, "color"), false);
  assert.equal(staticPayloadHasField(undefined, "color"), false);
  assert.equal(staticPayloadHasField([null, undefined], "color"), false);
  // No field named means nothing to check, which is how a caller that needs no new field opts out.
  assert.equal(staticPayloadHasField([{ a: 1 }], ""), false);
});

/* ---------------- the code table ---------------- */

test("MR3 codes: the LIRR and Metro-North tables are keyed by NAME and cover every route the feeds serve", () => {
  /* THIRTEEN LIRR branches and six Metro-North lines. The brief's 6.1 lists twelve and says
     "There is no route 11"; the live feed serves route 11, Belmont Park, colour 60269E, which
     the operator's ruling R-c settles as BEL. docs/design/map-redesign/map-redesign-v3-brief.md
     carries a dated note saying so rather than having the claim edited away. The count is
     asserted so a row cannot be dropped silently, and it is what caught this. */
  assert.equal(Object.keys(RAIL_BRANCH_CODES.LIRR).length, 13);
  assert.equal(Object.keys(RAIL_BRANCH_CODES.MNR).length, 6);
  assert.equal(railBranchCode("LIRR", "1", "Babylon Branch"), "BAB");
  assert.equal(railBranchCode("LIRR", "11", "Belmont Park"), "BEL");
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
    state: railTagState({ provenance: "reported" }, null, ""), bearing: 90,
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
  // PAPER AND INK AS INLINE STYLE, not as an SVG attribute, and MR4 corrected the reason
  // written here: the attribute form DOES resolve in Chromium (measured, identical computed
  // rgb), so this is a cascade rule rather than a resolution one. A presentation attribute is
  // the lowest-priority author declaration there is, so any stylesheet rule beats it silently.
  // helpers.js says it in full above railTagSvg.
  assert.match(solidFilled, /style="fill: var\(--paper\)"/);
  assert.doesNotMatch(solidFilled, /fill="var\(--/);

  const outlinedFilled = railTagSvg({
    system: "NJT", code: "NEC", color: "DD3439",
    state: railTagState({ provenance: "estimated" }, null, "estimated"), bearing: 180,
  });
  assert.match(outlinedFilled, /class="rail-tag rail-tag-outlined rail-head-filled"/);
  assert.match(outlinedFilled, /stroke-width="1\.2"/); // the outlined box's stroke
  assert.match(outlinedFilled, /<line x1="16"/); // the divider at the agency block's edge
  assert.match(outlinedFilled, /height="2\.5"/); // the branch colour reduced to a stripe

  // No bearing at all is a dot whatever the state trusts, because a chevron at an arbitrary
  // angle would be a heading we do not have.
  const noHeading = railTagSvg({
    system: "MNR", code: "HUD", color: "009B3A",
    state: railTagState({ provenance: "reported" }, null, ""), bearing: null,
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

/* A REAL SLICE, built the way computeRouteSlice returns one: the WHOLE branch polyline, its
   cumulative lengths, and the interval [s0, s1] the train is travelling along. `points` is NOT
   the two-point chord the train sits on, which is the shape the first draft of these tests used
   and the reason a defect survived them (see the ledger's round 4 entry). */
function slice(points, from = 0, to = null) {
  const cum = polylineCumLengths(points);
  return { points, cum, s0: from, s1: to == null ? cum[cum.length - 1] : to };
}

test("MR3 bearing: s0 to s1 IS the direction of travel, and the direction WORD does not turn it", () => {
  /* THE OPERATOR'S RULING R-a, and the defect it repairs. computeRouteSlice hands back the
     whole branch polyline plus the interval the train occupies, and trainLatLng interpolates
     s0 + (s1 - s0) * f: s0 -> s1 is therefore the way the train is MOVING by construction,
     whichever way the agency happened to wind that branch's shape. Reversing it on the served
     direction word turned every inbound train around, because the word had already been spent
     when the interval was built. */
  const north = slice([[40.70, -74.0], [40.80, -74.0]]);
  const south = slice([[40.80, -74.0], [40.70, -74.0]]);
  for (const direction of ["Outbound", "Inbound", "inbound", " Inbound ", null, undefined]) {
    assert.equal(
      Math.round(railTrainBearing({ _route: north, direction, latitude: 40.75, longitude: -74.0 })),
      0,
      `north slice, direction ${String(direction)}`,
    );
    assert.equal(
      Math.round(railTrainBearing({ _route: south, direction, latitude: 40.75, longitude: -74.0 })),
      180,
      `south slice, direction ${String(direction)}`,
    );
  }
  // NOT A HEADSIGN EITHER. v3.1 overrules v2's `headsign === "New York"` in as many words, and
  // NJ Transit serves no direction field at all, so no word of any kind may reach the geometry.
  assert.equal(Math.round(railTrainBearing({ _route: north, direction: "New York" })), 0);

  /* THE INTERVAL IS READ, NOT THE WHOLE LINE. A branch that turns has a different azimuth on
     each leg, so a train on the second leg must take the second leg's. This is the assertion
     that fails if railTrainBearing goes back to points[0] -> points[points.length - 1]. */
  const bend = [[40.70, -74.0], [40.80, -74.0], [40.80, -73.9]];
  const cum = polylineCumLengths(bend);
  const onFirstLeg = railTrainBearing({ _route: slice(bend, 0, cum[1]) });
  const onSecondLeg = railTrainBearing({ _route: slice(bend, cum[1], cum[2]) });
  assert.equal(Math.round(onFirstLeg), 0);
  assert.equal(Math.round(onSecondLeg), 90);
  const endToEnd = railTrainBearing({ _route: slice(bend) });
  assert.ok(Math.round(endToEnd) > 0 && Math.round(endToEnd) < 90, `end to end reads ${endToEnd}`);
});

test("MR3 bearing: the served bearing wins, the anchors are next, and nothing left is a dot", () => {
  // 1. A GPS train that sent a bearing: used as served, and NEVER reversed, because a served
  // bearing is already the way the train points and reversing it turns the train around.
  const served = { bearing: 45, direction: "Inbound", _route: slice([[40.7, -74.0], [40.8, -74.0]]) };
  assert.equal(railTrainBearing(served), 45);
  assert.equal(railTrainBearing({ bearing: 370 }), 10); // normalised into [0, 360)
  assert.equal(railTrainBearing({ bearing: -90 }), 270);
  // 2. No slice, but served anchors: prev to the drawn position. This is NJ Transit's case,
  // which serves no direction word at all, so the anchors ARE the served direction.
  const anchored = { prev_lat: 40.70, prev_lon: -74.0, latitude: 40.80, longitude: -74.0 };
  assert.equal(Math.round(railTrainBearing(anchored)), 0);
  /* AND "Inbound" DOES NOT TURN IT EITHER, which is the correction R-a orders and the line
     this file had asserting 180 over a pair whose true azimuth is 0. prev_lat/prev_lon is
     where the train WAS and latitude/longitude is where it IS, so the pair is travel-directed
     the same way the slice's interval is, and reversing it pointed every inbound NJ Transit
     train backwards. A test that asserts the defect is worse than no test: it makes the bug
     load-bearing. Recorded as such in docs/reviews/map-redesign-rounds.md. */
  assert.equal(Math.round(railTrainBearing({ ...anchored, direction: "Inbound" })), 0);
  // 3. Nothing to say: a reported fix with no bearing, no slice and no anchors.
  assert.equal(railTrainBearing({ provenance: "reported", latitude: 40.7, longitude: -74.0 }), null);
  assert.equal(railTrainBearing({}), null);
  assert.equal(railTrainBearing(null), null);
  // A one-point slice is not a direction, and falls through to the anchors rather than
  // reading points[0] twice.
  assert.equal(railTrainBearing({ _route: slice([[40.7, -74.0]]), ...anchored }), 0);
  // A slice with no measured interval is not a direction either: s0 and s1 are what make it
  // travel-directed, so without them there is nothing to read and the anchors answer.
  assert.equal(railTrainBearing({ _route: { points: [[40.7, -74.0], [40.6, -74.0]] }, ...anchored }), 0);
});

/* MR5, finding N6: ONE NEUTRAL REACHES THE NJ TRANSIT POPUP HEAD, and it is the rail
   family's.

   MR3 left two on screen for one unknown route and said so: the tag reaches
   `railBranchColor` and draws the design's `#6d6e71`; the popup head reached
   `njtRouteColor` and drew phase 15c's older `#4a4e69`. Route 17, the event-only
   Meadowlands line, never appears on `/api/njt-routes` at all, so it is the live example
   and it wore both at once. MR3 named it and left it because `P1k` pinned that popup byte
   for byte; MR5 owns the popup and converges the two here.

   ASSERTED AS A SOURCE FACT, and that is the point rather than laziness: no fixture world
   has an unknown NJ Transit route, so the drawn page cannot tell the two neutrals apart and
   every browser gate stays green either way. What is checkable is that the head reads the
   SAME RESOLVER the tag does. Two constants that happen to be equal would be a coincidence
   waiting for someone to change one of them; one function is a fact. */
test("MR5 N6: the NJ Transit popup head resolves its colour the way the tag does", () => {
  const njt = readFileSync(join(__dirname, "systems", "njt.js"), "utf8");
  const body = njt.slice(njt.indexOf("function njtTrainPopup("));
  const call = body.slice(0, body.indexOf("\n}"));

  assert.match(
    call,
    /railBranchColor\(njtBranch\(/,
    "the NJ Transit popup head must resolve through railBranchColor, the way njtIcon's tag does",
  );
  assert.ok(
    !/njtRouteColor\(/.test(call),
    "the NJ Transit popup head still reaches njtRouteColor, which falls back to the second neutral",
  );

  // And the two ends of the claim, so the test says what the colours ARE and not only which
  // function was called: an unknown route resolves to the rail neutral, and the second
  // neutral is a different colour, which is what made this a finding.
  assert.equal(railBranchColor(null), RAIL_NEUTRAL_COLOR);
  assert.equal(RAIL_NEUTRAL_COLOR, "#6d6e71");
  assert.notEqual(NJT_FALLBACK_COLOR, RAIL_NEUTRAL_COLOR);
});
