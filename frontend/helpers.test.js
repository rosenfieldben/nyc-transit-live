// Run with: node --test "frontend/*.test.js"  (from the repo root)
// Tests the pure helpers shared with the browser via plain <script> loading.
// NOTE: minClockOffset is module state that only ratchets downward, so the
// staleness tests run in a deliberate order (node:test runs them serially).

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  subwayTrainName,
  railroadTrainName,
  pathTrainName,
  ferryBoatName,
  busName,
  compassPoint,
  airtrainStationName,
  degradedIdentities,
  statusAnnouncement,
  alertIdentities,
  bannerAnnouncement,
  motionAllowed,
  watchMotionPreference,
  esc,
  routeColor,
  lineColor,
  staleness,
  humanizeAge,
  servedAge,
  boardFreshness,
  arrivalQualifier,
  boardSystemLine,
  boardLineHtml,
  qualifierHtml,
  UNDATED_SYSTEMS,
  alertsStale,
  alertsFreshnessBasis,
  ALERTS_STALE_AFTER_S,
  hashString,
  bannerRenderKey,
  emptyFeedDecision,
  shouldRefresh,
  noteClockOffset,
  formatCountdown,
  spokenCountdown,
  countdownParts,
  clockTimeLabel,
  STATION_RESULT_CAP,
  foldStationName,
  stationQueryTokens,
  searchStations,
  stationOverflowLine,
  shapeStationArrivals,
  arrivalSentence,
  ANNOUNCE_LEAD_SHIFT_S,
  announcementWorthy,
  trainLatLng,
  polylineCumLengths,
  pointAtArcLength,
  projectOntoRoute,
  computeRouteSlice,
  positionQualifier,
  orderedRailroadBuckets,
  railroadArrivalsHtml,
  PATH_BUCKET_ORDER,
  PATH_FALLBACK_COLOR,
  PATH_ROUTE_MAX_SLICE,
  PATH_ROUTE_ACCEPT_DIST,
  computePathRouteSlice,
  orderedPathBuckets,
  pathColor,
  formatPathHead,
  pathTrainPopupHtml,
  pathArrivalsHtml,
  FERRY_FALLBACK_COLOR,
  orderedFerryBuckets,
  ferryArrivalDisplay,
  ferryBoatIconState,
  ferryStatusText,
  ferrySpeedKnots,
  ferryBoatPopupHtml,
  ferryArrivalsHtml,
  NJT_FALLBACK_COLOR,
  njtColor,
  njtRouteColor,
  njtRouteName,
  njtKey,
  formatNjtHead,
  njtRouteTables,
  njtTrainPopupHtml,
  njtDelayText,
  njtTrainName,
  njtStationName,
  njtArrivalsHtml,
  njtRowLabel,
  njtAtItsStation,
  njtGlideTrain,
  njtOrderedArrivals,
  isNotConfigured,
  describeIdentity,
  ROUTE_MAX_SLICE,
  RAILROAD_ROUTE_MAX_SLICE,
  FEED_STALE_AFTER_S,
  ingestSystems,
  ingestEnvelope,
  contentClock,
  systemLag,
  systemAges,
  systemStaleAts,
  staleAge,
  markerOpacity,
  glideClock,
  STALE_MARKER_OPACITY,
  FERRY_DOCKED_OPACITY,
} = require("./helpers.js");

test("trainLatLng interpolates along prev->next and clamps to [0,1]", () => {
  const train = { prev_lat: 0, prev_lon: 0, latitude: 10, longitude: 20, prev_time: 100, next_time: 200 };
  assert.deepEqual(trainLatLng(train, 150), [5, 10]); // midpoint
  assert.deepEqual(trainLatLng(train, 50), [0, 0]); // before prev_time -> clamp 0 -> prev
  assert.deepEqual(trainLatLng(train, 999), [10, 20]); // after next_time -> clamp 1 -> next
});

test("trainLatLng falls back to the static position when anchors are unusable", () => {
  const base = { latitude: 10, longitude: 20 };
  // no previous station
  assert.deepEqual(
    trainLatLng({ ...base, prev_lat: null, prev_lon: null, prev_time: null, next_time: 200 }, 150),
    [10, 20],
  );
  // missing next_time
  assert.deepEqual(
    trainLatLng({ ...base, prev_lat: 0, prev_lon: 0, prev_time: 100, next_time: null }, 150),
    [10, 20],
  );
  // missing prev_time (prev coords present but untimed)
  assert.deepEqual(
    trainLatLng({ ...base, prev_lat: 0, prev_lon: 0, prev_time: null, next_time: 200 }, 150),
    [10, 20],
  );
  // non-monotonic times (next_time <= prev_time)
  assert.deepEqual(
    trainLatLng({ ...base, prev_lat: 0, prev_lon: 0, prev_time: 200, next_time: 200 }, 150),
    [10, 20],
  );
});

test("formatCountdown buckets a seconds delta into now / minutes", () => {
  assert.equal(formatCountdown(null), "");
  assert.equal(formatCountdown(NaN), "");
  assert.equal(formatCountdown(0), "now");
  assert.equal(formatCountdown(29), "now");
  assert.equal(formatCountdown(-15), "now"); // already due / just passed
  assert.equal(formatCountdown(30), "1 min");
  assert.equal(formatCountdown(89), "1 min");
  assert.equal(formatCountdown(90), "2 min");
  assert.equal(formatCountdown(600), "10 min");
});

test("formatCountdown renders the hours tier at 100 minutes and up", () => {
  // Boundary on both sides: 99 minutes stays in the minutes tier, 100 minutes
  // (6000s) crosses to the hours tier. Below is unchanged from the minutes-only
  // version (subway countdowns effectively never reach 100 min).
  assert.equal(formatCountdown(5940), "99 min"); // 99 min, minutes tier
  assert.equal(formatCountdown(6000), "1 h 40 min"); // 100 min, hours tier
  assert.equal(formatCountdown(7200), "2 h 0 min"); // exact hour keeps "0 min"
  assert.equal(formatCountdown(3600), "60 min"); // still minutes (60 < 100)
});

test("orderedRailroadBuckets keeps a stable Inbound, Outbound, Trains order", () => {
  const arr = (n) => [{ route_id: "1", trip_id: `t${n}`, arrival: n, train_num: null }];
  // Full set: fixed display order regardless of input key order.
  assert.deepEqual(
    orderedRailroadBuckets({ Trains: arr(3), Outbound: arr(2), Inbound: arr(1) }).map((b) => b[0]),
    ["Inbound", "Outbound", "Trains"],
  );
  // Subsets: only the present buckets, in order.
  assert.deepEqual(
    orderedRailroadBuckets({ Outbound: arr(2), Inbound: arr(1) }).map((b) => b[0]),
    ["Inbound", "Outbound"],
  );
  assert.deepEqual(orderedRailroadBuckets({ Trains: arr(1) }).map((b) => b[0]), ["Trains"]);
  // Empty directions, and buckets that arrive empty, yield nothing to render.
  assert.deepEqual(orderedRailroadBuckets({}), []);
  assert.deepEqual(orderedRailroadBuckets({ Inbound: [] }), []);
});

test("railroadArrivalsHtml escapes a hostile station name and train_num", () => {
  const station = { id: "12", system: "LI<b>RR", name: "Jamaica<script>" };
  const body = {
    directions: {
      Inbound: [{ route_id: "5", trip_id: "t1", arrival: 100, train_num: "27<img>12" }],
    },
  };
  const html = railroadArrivalsHtml(station, body, 40);
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("Jamaica&lt;script&gt;"));
  assert.ok(html.includes("LI&lt;b&gt;RR")); // system tag escaped
  assert.ok(html.includes("#27&lt;img&gt;12")); // train number escaped, kept its # prefix
  assert.ok(html.includes("1 min")); // (100 - 40)s -> "1 min" countdown
});

/* RULING R1's SHARPEST CASE, ASKED OF THE BOARD ITSELF: the badge must resolve its pair through
   railBranchPaint, not through the published colour plus readableTextOn.

   WHY IT NEEDS ITS OWN TEST. EE0034 is Metro-North's New Haven red and it takes white at 4.48 and
   dark at 3.88, so NEITHER ink clears on it; railBranchPaint answers that by MOVING the fill two
   units to #ec0033, which is the remedy this repo already applies to the tag's branch block. No
   fixture serves that colour, so a board that took the published fill and computed an ink would ship
   an AA failure on four of Metro-North's six routes with every gate green. The A3 sweep measures
   railBranchPaint's own pairs; this measures that the BOARD asks it. */
test("MR5 R1: a board badge takes the moved fill where no ink clears the published one", () => {
  const station = { id: "1", system: "MNR", name: "Grand Central" };
  const body = { directions: { Inbound: [{ route_id: "6", trip_id: "t1", arrival: 100, train_num: null }] } };
  const html = railroadArrivalsHtml(station, body, 40, () => railBranchPaint("EE0034", "FFFFFF"));
  assert.match(html, /background:#ec0033;color:#ffffff/, "the fill moved so its ink clears");
  assert.ok(!html.includes("background:#EE0034"), "the published fill would be 4.48 with white");
  assert.ok(contrastRatio("#ffffff", "#ec0033") >= 4.5, "and the moved pair is what clears");
});

test("railroadArrivalsHtml renders a No trains state for empty directions", () => {
  const html = railroadArrivalsHtml({ id: "1", system: "MNR", name: "Grand Central" }, { directions: {} }, 0);
  assert.ok(html.includes("Grand Central"));
  assert.ok(html.includes("arr-none"));
  assert.ok(html.includes("No trains"));
});

test("railroadArrivalsHtml shows the route name from nameFor and escapes it", () => {
  const station = { id: "12", system: "LIRR", name: "Jamaica" };
  const body = {
    directions: { Inbound: [{ route_id: "1", trip_id: "t1", arrival: 100, train_num: null }] },
  };
  // Hostile route name via the resolver: it must appear escaped, never raw.
  // SLOT 5 SINCE RULING R1, because slot 4 is the paint resolver now. Passed as `undefined` rather
  // than omitted: a hostile name in the paint slot would render no label at all and this test would
  // keep passing while testing nothing, which is the shape the phase's fifth defect is named for.
  const html = railroadArrivalsHtml(station, body, 40, undefined, () => "Bab<script>Branch");
  assert.ok(html.includes("Bab&lt;script&gt;Branch"));
  assert.ok(!html.includes("<script>"));
  // Absent name (resolver returns null) just omits the label, no crash.
  const plain = railroadArrivalsHtml(station, body, 40, undefined, () => null);
  assert.ok(plain.includes("arr-badge") && plain.includes("1 min"));
});

test("orderedPathBuckets keeps a stable To New York, To New Jersey, Trains order", () => {
  const arr = (n) => [{ route_id: "862", arrival: n }];
  assert.deepEqual(PATH_BUCKET_ORDER, ["To New York", "To New Jersey", "Trains"]);
  // Full set: fixed display order regardless of input key order.
  assert.deepEqual(
    orderedPathBuckets({ Trains: arr(3), "To New Jersey": arr(2), "To New York": arr(1) }).map((b) => b[0]),
    ["To New York", "To New Jersey", "Trains"],
  );
  // Subsets: only the present buckets, in order.
  assert.deepEqual(
    orderedPathBuckets({ "To New Jersey": arr(2), "To New York": arr(1) }).map((b) => b[0]),
    ["To New York", "To New Jersey"],
  );
  assert.deepEqual(orderedPathBuckets({ Trains: arr(1) }).map((b) => b[0]), ["Trains"]);
  // An unexpected key is appended rather than dropped (never silently hide trains).
  assert.deepEqual(
    orderedPathBuckets({ Shuttle: arr(2), "To New York": arr(1) }).map((b) => b[0]),
    ["To New York", "Shuttle"],
  );
  // Empty directions, and buckets that arrive empty, yield nothing to render.
  assert.deepEqual(orderedPathBuckets({}), []);
  assert.deepEqual(orderedPathBuckets({ "To New York": [] }), []);
});

test("pathColor validates and prefixes the feed's bare hex, else falls back", () => {
  assert.equal(pathColor("4d92fb"), "#4d92fb");
  assert.equal(pathColor("D93A30"), "#D93A30"); // either case accepted
  assert.equal(pathColor(null), PATH_FALLBACK_COLOR);
  assert.equal(pathColor(undefined), PATH_FALLBACK_COLOR);
  assert.equal(pathColor("fff"), PATH_FALLBACK_COLOR); // short form not served; reject
  // A hostile value never reaches a style attribute; the fallback does instead.
  assert.equal(pathColor('red;"onmouseover="x'), PATH_FALLBACK_COLOR);
  assert.equal(pathColor("4d92fb", "#000000"), "#4d92fb"); // fallback unused when valid
  assert.equal(pathColor("nope", "#000000"), "#000000"); // caller-chosen fallback
});

test("formatPathHead prefers the route name, falls back to route id, then PATH", () => {
  assert.equal(formatPathHead("862", "Newark - World Trade Center"), "Newark - World Trade Center");
  assert.equal(formatPathHead("862", null), "PATH route 862");
  assert.equal(formatPathHead(null, null), "PATH");
});

test("pathTrainPopupHtml shows placement fields, never the unstable trip id", () => {
  const train = {
    trip_id: "329352234",
    route_id: "862",
    stop_name: "Journal Square",
    direction: "To New Jersey",
  };
  // 6.3: the position line is the served provenance's words (positionQualifier).
  const position = positionQualifier({ observed_at: 995, provenance: "placed" }, { now: 1000, servedAt: 1000, system: "path" });
  const html = pathTrainPopupHtml(train, "Newark - World Trade Center", "#d93a30", position);
  assert.ok(html.includes("Newark - World Trade Center"));
  /* MR5: THE FACTS ARE SECTION 5's LABEL/VALUE ROWS, so "Next stop: Journal Square" is a label
     cell and a value cell rather than one sentence. The words are the same words; what moved is
     the colon, which the grid draws as a column. */
  assert.ok(html.includes('<div class="k">Next stop</div>\n<div class="v">Journal Square</div>'));
  assert.ok(html.includes('<div class="k">Direction</div>\n<div class="v">To New Jersey</div>'));
  assert.ok(html.includes('<div class="k">Position</div>\n<div class="v">scheduled position (no GPS)</div>'));
  // And the kicker is the feed strip's own word for this feed, which is where "PATH" moved to.
  assert.ok(html.startsWith('<div class="pk"><span>PATH</span>'));
  /* MR5: THE HEAD'S INK IS THE ROUTE COLOUR WALKED AGAINST THE POPUP'S OWN SURFACE, not the
     published colour and not the colour walked against white. This used to assert the raw
     `#d93a30`, which passed because PATH's red happens to clear 4.5 on white and readableInk
     returned it untouched. MR5 makes the popup --surface, so it no longer does: measured,
     #d93a30 reads 4.57 on white and is walked to #c3342b for 4.51 here.
     ASSERTED AS THE HELPER'S OWN OUTPUT rather than as the new hex, so this is not a second
     copy of readableInk's arithmetic, and asserted AGAINST the white form too: those two
     differ, so a builder that went back to the default fails on the second line. */
  assert.ok(html.includes(readableInk("#d93a30", POPUP_SURFACE_FALLBACK)));
  assert.ok(!html.includes(`color:${readableInk("#d93a30")}"`), "the head must not be inked against white");
  // The API contract: bridge trip ids are unstable and display-poor, never shown.
  assert.ok(!html.includes("329352234"));
});

test("pathTrainPopupHtml escapes hostile fields and omits absent ones", () => {
  const train = { route_id: "8<b>62", stop_name: null, direction: null };
  const html = pathTrainPopupHtml(train, null, "#546e7a");
  assert.ok(html.includes("PATH route 8&lt;b&gt;62"));
  assert.ok(!html.includes("8<b>62"));
  assert.ok(!html.includes("Next stop"));
  const hostileName = pathTrainPopupHtml({ route_id: "862" }, "New<script>ark", "#546e7a");
  assert.ok(hostileName.includes("New&lt;script&gt;ark"));
  assert.ok(!hostileName.includes("<script>"));
});

test("pathArrivalsHtml renders buckets in order with badge colors and countdowns", () => {
  const station = { id: "26734", name: "World Trade Center" };
  const body = {
    directions: {
      "To New Jersey": [{ route_id: "862", arrival: 400 }],
      "To New York": [{ route_id: "859", arrival: 100 }],
    },
  };
  const colorFor = (id) => ({ 859: "#4d92fb", 862: "#d93a30" })[id];
  const nameFor = (id) => ({ 859: "Hoboken - 33rd", 862: "Newark - World Trade Center" })[id];
  const html = pathArrivalsHtml(station, body, 40, colorFor, nameFor);
  assert.ok(html.indexOf("To New York") < html.indexOf("To New Jersey")); // fixed order
  assert.ok(html.includes("World Trade Center"));
  assert.ok(html.includes("#4d92fb") && html.includes("#d93a30")); // per-route badge colors
  assert.ok(html.includes("Hoboken - 33rd"));
  assert.ok(html.includes("1 min")); // (100 - 40)s
  assert.ok(html.includes("6 min")); // (400 - 40)s
});

test("pathArrivalsHtml renders No trains for an empty directions dict and escapes hostile fields", () => {
  const empty = pathArrivalsHtml({ id: "26733", name: "Newark" }, { directions: {} }, 0);
  assert.ok(empty.includes("Newark"));
  assert.ok(empty.includes("arr-none") && empty.includes("No trains"));

  const hostile = pathArrivalsHtml(
    { id: "26733", name: "New<script>ark" },
    { directions: { "To New York": [{ route_id: "8<img>", arrival: 100 }] } },
    40,
    undefined,
    () => "Ho<script>boken",
  );
  assert.ok(hostile.includes("New&lt;script&gt;ark"));
  assert.ok(hostile.includes("8&lt;img&gt;"));
  assert.ok(hostile.includes("Ho&lt;script&gt;boken"));
  assert.ok(!hostile.includes("<script>") && !hostile.includes("<img>"));
  // The default colorFor keeps the badge on the neutral fallback.
  assert.ok(hostile.includes(PATH_FALLBACK_COLOR));
});

// formatRailroadHead's test WENT WITH IT, to popupvocab.test.js: MR5 replaced the joined head with
// railroadHeadParts (a kicker and a title), and that file asserts the parts AND joins them back
// into the three strings this test used to assert, so the words are still pinned.

test("esc escapes all HTML-significant characters", () => {
  assert.equal(esc(`<b a="1" b='2'>&`), "&lt;b a=&quot;1&quot; b=&#39;2&#39;&gt;&amp;");
  assert.equal(esc("M15 +SelectBus"), "M15 +SelectBus");
  assert.equal(esc(42), "42"); // non-strings are stringified
});

test("routeColor is deterministic, distinct, and handles null", () => {
  assert.equal(routeColor("M15"), routeColor("M15"));
  assert.notEqual(routeColor("M15"), routeColor("B46"));
  assert.match(routeColor("M15"), /^hsl\(\d+, 75%, 40%\)$/);
  assert.equal(routeColor(null), "#777777");
  assert.equal(routeColor(""), "#777777");
});

test("lineColor maps trunks, falls back by first char, defaults gray", () => {
  assert.equal(lineColor("A"), lineColor("C")); // same trunk
  assert.equal(lineColor("6X"), lineColor("6")); // express variant by first char
  assert.equal(lineColor(null), "#555555");
  assert.equal(lineColor("X9"), "#555555"); // unknown line
});

// isPlacedRailroad's test went with it in 6.3: the railroad glyph, glide, words and
// cross-link read the served provenance now (railroadHollow, drawnFromPrediction,
// positionQualifier, railroadAtItsStation), and frontend/positions.test.js pins them,
// the no-times Metro-North placement this test existed for included.

// `now` is passed explicitly for determinism; minClockOffset is null here (nothing
// calls noteClockOffset before these), so the client-elapsed term reduces to
// now - servedAt. R1 model: sources now carry servedAt, and the poll-age term is
// server cache age (servedAt - fetchedAt) + client elapsed (now - servedAt), both
// skew-clean. The wording moved from "X data Nm old" to "X: as of Nm ago".
test("staleness flags upstream lag (skew-free) at/over the threshold", () => {
  const now = 10_000;
  // Fresh: content 15s old at a poll 5s ago, served just now.
  assert.equal(
    staleness({ label: "buses", fetchedAt: now - 5, servedAt: now, feedTimestamp: now - 15 }, now),
    null,
  );
  // Upstream stale: content was 100s old at the (recent) last poll. The diff of the
  // two server timestamps drives this, so the browser clock can't skew it.
  assert.equal(
    staleness(
      { label: "buses", fetchedAt: now - 5, servedAt: now, feedTimestamp: now - 105 },
      now,
    ),
    "buses: as of 100s ago",
  );
})

test("staleness flags a stuck backend via the server cache-age term (R1)", () => {
  const now = 10_000;
  // The stuck-backend / audit shape: the last successful poll was 200s ago
  // (fetched_at = now - 200), but the backend is still SERVING now (served_at = now),
  // so served_at - fetched_at = 200s of server cache age. Upstream lag alone (5s)
  // would stay silent. This is the exact gap the old fetched_at-only model was blind
  // to on a first load.
  assert.equal(
    staleness(
      { label: "trains", fetchedAt: now - 200, servedAt: now, feedTimestamp: now - 205 },
      now,
    ),
    "trains: as of 3m ago",
  );
  // Works with a missing feed_timestamp too (upstream lag unknown -> 0).
  assert.equal(
    staleness(
      { label: "buses", fetchedAt: now - 200, servedAt: now, feedTimestamp: null },
      now,
    ),
    "buses: as of 3m ago",
  );
  // Fallback: a response predating served_at still flags via the old single term.
  assert.equal(
    staleness({ label: "buses", fetchedAt: now - 200, servedAt: null, feedTimestamp: null }, now),
    "buses: as of 3m ago",
  );
})

test("staleness is null when fresh or never fetched", () => {
  const now = 10_000;
  assert.equal(
    staleness({ label: "buses", fetchedAt: null, servedAt: now, feedTimestamp: now }, now),
    null,
  );
  assert.equal(
    staleness(
      { label: "buses", fetchedAt: now - 5, servedAt: now - 5, feedTimestamp: now - 5 },
      now,
    ),
    null,
  );
  assert.equal(
    staleness({ label: "buses", fetchedAt: now - 5, servedAt: now, feedTimestamp: null }, now),
    null,
  );
})

test("R1 regression: a first load against a 200s-stale backend cache reads stale", () => {
  // THE AUDIT SCENARIO, pinned. A first page load hits a backend whose cache is
  // already 200s old: fetched_at = now - 200 (last successful poll), served_at = now
  // (this response was just built), feed_timestamp = now - 205. The client clock ==
  // the server clock (no real skew).
  const now = 10_000;
  const src = { label: "buses", fetchedAt: now - 200, servedAt: now, feedTimestamp: now - 205 };
  // BEFORE R1, noteClockOffset(fetched_at) recorded offset 200, which cancelled the
  // poll-age term (stale looked FRESH) and shifted every countdown by 200s. R1
  // calibrates off served_at, so the offset from this same response is ~0 (the
  // countdown-unshifted half is pinned end-to-end by the Playwright stale-serve test).
  noteClockOffset(src.servedAt, now); // clean: served_at == client now -> offset ~0
  // The server cache-age term (served_at - fetched_at = 200s) flags stale on the very
  // first observation, skew-free and independent of any calibration state.
  assert.equal(staleness(src, now), "buses: as of 3m ago");
})

test("staleness uses the server cache-age term, not now - fetchedAt, when client elapsed clamps (R1)", () => {
  // When served_at == now (the usual case) the new two-part poll age reduces
  // algebraically to now - fetchedAt, so it is indistinguishable from the old single
  // term. This case forces them APART to prove the server cache-age term is live: a
  // served_at 10s AHEAD of the injected now (mild clock jitter) makes clientElapsed
  // (now - served_at = -10) clamp to 0, so pollAge is the pure server cache age
  // (served_at - fetched_at = 110), NOT now - fetchedAt (100). The old formula would
  // have said "100s ago"; the new one says 110.
  const now = 10_000;
  assert.equal(
    staleness(
      { label: "buses", fetchedAt: now - 100, servedAt: now + 10, feedTimestamp: now - 105 },
      now,
    ),
    "buses: as of 110s ago",
  );
  // The same clamp stops a just-served response from reading as negatively stale: a
  // fresh poll served an instant "after" the eval clock is still fresh (age 0).
  assert.equal(
    staleness({ label: "buses", fetchedAt: now - 1, servedAt: now + 1, feedTimestamp: now - 1 }, now),
    null,
  );
})

test("6.2 humanizeAge gains an hours tier and is unchanged below a hundred minutes", () => {
  // Below the tier, exactly the two-tier strings every surface already shipped.
  assert.equal(humanizeAge(100), "100s");
  assert.equal(humanizeAge(119), "119s");
  assert.equal(humanizeAge(120), "2m");
  assert.equal(humanizeAge(200), "3m");
  assert.equal(humanizeAge(99 * 60), "99m");
  // From a hundred minutes on, hours: section 3.2's fix for an LIRR prediction 52538s
  // old, which the two tiers printed as "876m", and the capture's oldest GPS fix, 53676s.
  assert.equal(humanizeAge(100 * 60), "1h 40m");
  assert.equal(humanizeAge(2 * 3600), "2h");
  assert.equal(humanizeAge(52538), "14h 36m");
  assert.equal(humanizeAge(53676), "14h 55m");
  // ONE ROUNDING with the countdowns: the minute an age reports is the minute
  // countdownParts would report for the same number of seconds.
  assert.equal(humanizeAge(100 * 60 + 29), "1h 40m");
  assert.equal(humanizeAge(100 * 60 + 31), "1h 41m");
});

test("6.2 servedAge ages a stamp on the server's clock, anchored at served_at", () => {
  // served_at minus the stamp is skew-free (both from the payload); the time since is
  // the corrected client clock's, and never negative.
  assert.equal(servedAge(1000, 1600, 1600), 600);
  assert.equal(servedAge(1000, 1600, 1630), 630);
  assert.equal(servedAge(1000, 1600, 1590), 600, "a clock behind served_at adds nothing");
  // No served_at (a response from before 6.1): the corrected clock alone.
  assert.equal(servedAge(1000, null, 1100), 100);
  // Nothing to age.
  assert.equal(servedAge(null, 1600, 1600), null);
  assert.equal(servedAge("1000", 1600, 1600), null);
  assert.equal(servedAge(Number.NaN, 1600, 1600), null);
});

test("alertsStale gates on the backend's last successful poll (fetched_at) and the threshold", () => {
  const now = 10_000;
  // No successful poll yet (null fetchedAt): never stale, so boot shows no false marker.
  assert.equal(alertsStale(null, now), false);
  // Just polled: fresh.
  assert.equal(alertsStale(now - 10, now), false);
  // Exactly at and past the threshold: stale.
  assert.equal(alertsStale(now - ALERTS_STALE_AFTER_S, now), true);
  assert.equal(alertsStale(now - (ALERTS_STALE_AFTER_S + 60), now), true);
  // Higher bar than the feed threshold (alerts change slowly): a gap the feeds would
  // already flag is still fresh for alerts.
  assert.equal(alertsStale(now - (FEED_STALE_AFTER_S + 1), now), false);
})

test("C1 audit scenario: 200s with a FROZEN fetched_at and an advancing served_at go stale", () => {
  // THE FINDING, reproduced as the sequence the client actually sees. The alert feeds
  // are down but the backend keeps answering 200 from its last-known index, so every
  // response carries a NEW served_at (stamped at response build) and the SAME
  // fetched_at (the last poll that decoded). Under R1 the marker keyed on served_at,
  // which advanced on every poll, so the gate reset forever and the honesty hedge
  // could not fire during the exact outage it exists for.
  //
  // REVIEW FIX: this used to hand alertsStale each field ITSELF and compare, which
  // proved only arithmetic. The test picked the field, so a revert of the production
  // choice would sail straight past it. It now runs the bodies through
  // alertsFreshnessBasis, the function the production path uses to pick the field, so
  // reverting that to served_at fails here.
  const polledAt = 1000; // the last poll that decoded; never advances again
  const responses = [0, 60, 120, 180, 240, 300, 360].map((elapsed) => ({
    body: { fetched_at: polledAt, served_at: polledAt + elapsed }, // served_at always fresh
    clientNow: polledAt + elapsed,
  }));
  const live = responses.map((r) => alertsStale(alertsFreshnessBasis(r.body), r.clientNow));
  // Trips exactly at ALERTS_STALE_AFTER_S (300) and stays tripped.
  assert.deepEqual(live, [false, false, false, false, false, true, true]);
  // The counterfactual, to show the sequence really is one served_at cannot catch.
  const byServedAt = responses.map((r) => alertsStale(r.body.served_at, r.clientNow));
  assert.deepEqual(byServedAt, [false, false, false, false, false, false, false]);
})

test("alertsFreshnessBasis reads fetched_at and nothing else", () => {
  // The production field CHOICE, pinned where it actually lives.
  assert.equal(alertsFreshnessBasis({ fetched_at: 1000, served_at: 9999 }), 1000);
  assert.equal(alertsFreshnessBasis({ served_at: 9999 }), null); // no silent fallback
  assert.equal(alertsFreshnessBasis({ fetched_at: null, served_at: 9999 }), null);
  assert.equal(alertsFreshnessBasis({}), null);
  assert.equal(alertsFreshnessBasis(null), null);
  assert.equal(alertsFreshnessBasis(undefined), null);
  assert.equal(alertsFreshnessBasis({ fetched_at: "1000" }), null); // wrong type, not NaN math
  assert.equal(alertsFreshnessBasis({ fetched_at: 0 }), 0); // epoch is a real timestamp
})

test("alertsStale ages a never-filled index against the client's first attempt", () => {
  // REVIEW FIX. A null fetchedAt used to return the healthy answer with NO upper
  // bound, so a backend whose index never filled (every feed down since boot, so
  // /api/alerts errors and loadAlerts swallows it) left riders a confident,
  // alert-free map with no hedge, indefinitely.
  const firstTry = 1000;
  assert.equal(alertsStale(null, firstTry + 10, firstTry), false); // boot grace holds
  assert.equal(alertsStale(null, firstTry + ALERTS_STALE_AFTER_S - 1, firstTry), false);
  assert.equal(alertsStale(null, firstTry + ALERTS_STALE_AFTER_S, firstTry), true); // discloses
  assert.equal(alertsStale(null, 1e9, firstTry), true); // and stays disclosed
  // Omitting the basis keeps the old unbounded grace, so a caller with no first
  // attempt to point at is unaffected.
  assert.equal(alertsStale(null, 1e9), false);
  assert.equal(alertsStale(null, 1e9, null), false);
  // A real fetched_at always wins over the fallback basis.
  assert.equal(alertsStale(firstTry + 1e6, firstTry + 1e6, firstTry), false);
})

test("bannerRenderKey re-renders on a wording change under the same id", () => {
  const alert = { system: "subway", id: "wide-1", header: "Systemwide: reduced service" };
  const revised = { ...alert, header: "Systemwide: reduced service on 4 lines" };
  // Same id, revised wording: the keys must DIFFER, or the banner keeps the old text.
  assert.notEqual(bannerRenderKey([alert], false), bannerRenderKey([revised], false));
  // Identical content: same key, so an unchanged banner is not needlessly rebuilt
  // (reassigning innerHTML would drop any text the rider has selected).
  assert.equal(bannerRenderKey([alert], false), bannerRenderKey([{ ...alert }], false));
  // The stale flag still participates, so the marker paints and clears on its own.
  assert.notEqual(bannerRenderKey([alert], true), bannerRenderKey([alert], false));
  // A null header is handled rather than throwing. REVIEW FIX: the comment here used
  // to claim null differs from empty-string text, which is FALSE (String(null ?? "")
  // and String("") are the same input), and the assertion only checked the type, so it
  // could not have caught the discrepancy either way. State what actually holds.
  assert.equal(
    bannerRenderKey([{ ...alert, header: null }], false),
    bannerRenderKey([{ ...alert, header: "" }], false),
  );
  assert.notEqual(bannerRenderKey([{ ...alert, header: null }], false), bannerRenderKey([alert], false));
  // Order and identity still matter: two alerts vs one, and a different id.
  assert.notEqual(bannerRenderKey([alert, revised], false), bannerRenderKey([alert], false));
  assert.notEqual(bannerRenderKey([{ ...alert, id: "wide-2" }], false), bannerRenderKey([alert], false));
  // Scoped by system like every other alert join: the same id under two feeds differs.
  assert.notEqual(bannerRenderKey([{ ...alert, system: "bus" }], false), bannerRenderKey([alert], false));
  assert.equal(bannerRenderKey([], false), "F|"); // empty, fresh
})

test("hashString is deterministic, unsigned, and separates similar text", () => {
  assert.equal(hashString("abc"), hashString("abc"));
  assert.notEqual(hashString("abc"), hashString("abd"));
  assert.notEqual(hashString(""), hashString("a"));
  // Hex of an UNSIGNED 32-bit value: JS bitwise ops are signed, so without the >>> 0
  // the hash would sometimes render with a leading "-".
  for (const text of ["Systemwide: reduced service", "[Q] delays", "éè", "x".repeat(500)]) {
    assert.match(hashString(text), /^[0-9a-f]{1,8}$/);
  }
})

test("emptyFeedDecision keeps last-known on the first empty poll and records the run start", () => {
  const d = emptyFeedDecision(null, 1000);
  assert.equal(d.applyEmpty, false);
  assert.equal(d.error, "feed empty, showing last known");
  assert.equal(d.emptyRunStart, 1000); // this poll's fetched_at starts the run
});

test("emptyFeedDecision keeps last-known for empties within the window", () => {
  const d = emptyFeedDecision(1000, 1000 + FEED_STALE_AFTER_S - 1); // just inside
  assert.equal(d.applyEmpty, false);
  assert.equal(d.error, "feed empty, showing last known");
  assert.equal(d.emptyRunStart, 1000); // run start carried forward, not reset
});

test("emptyFeedDecision applies the empty set at and after the threshold", () => {
  const at = emptyFeedDecision(1000, 1000 + FEED_STALE_AFTER_S); // exactly at the boundary
  assert.equal(at.applyEmpty, true);
  assert.equal(at.error, "feed empty"); // the "showing last known" clause is dropped
  assert.equal(at.emptyRunStart, 1000);
  const after = emptyFeedDecision(1000, 1000 + FEED_STALE_AFTER_S + 30);
  assert.equal(after.applyEmpty, true);
  assert.equal(after.error, "feed empty");
});

test("emptyFeedDecision starts a fresh window after a reset (non-empty poll)", () => {
  // map.js resets emptyRunStart to null on any non-empty poll; a later empty then
  // begins a brand-new window rather than counting from the old, long-past run.
  const fresh = emptyFeedDecision(null, 5000);
  assert.equal(fresh.applyEmpty, false);
  assert.equal(fresh.emptyRunStart, 5000);
  const soon = emptyFeedDecision(fresh.emptyRunStart, 5000 + 1); // 1s into the new run
  assert.equal(soon.applyEmpty, false);
  assert.equal(soon.error, "feed empty, showing last known");
});

test("emptyFeedDecision holds last-known without starting a run when fetched_at is null", () => {
  // A missing server fetched_at cannot be timed, so we cannot bound the run: hold
  // last-known and leave the run start untouched rather than clearing markers.
  const d = emptyFeedDecision(null, null);
  assert.equal(d.applyEmpty, false);
  assert.equal(d.error, "feed empty, showing last known");
  assert.equal(d.emptyRunStart, null);
});

test("shouldRefresh gates a source only on its own inFlight flag (R2)", () => {
  // The per-source gate that replaced the whole-cycle `refreshing` lock: a source
  // NOT in flight is eligible, one already in flight is skipped this tick. This is
  // exactly what stops a single wedged source from freezing the others.
  assert.equal(shouldRefresh({ inFlight: false }), true);
  assert.equal(shouldRefresh({ inFlight: true }), false);
  // A brand-new descriptor (inFlight undefined before the first tick) is eligible.
  assert.equal(shouldRefresh({}), true);
});

test("shouldRefresh is independent per source (one wedged source does not gate another)", () => {
  // The property the old global lock lacked: filtering the descriptors by
  // shouldRefresh leaves the healthy sources eligible even while one is stuck in
  // flight, so refreshAll keeps polling the others.
  const sources = {
    buses: { inFlight: true }, // wedged
    subways: { inFlight: false },
    railroads: { inFlight: false },
  };
  const eligible = Object.entries(sources)
    .filter(([, s]) => shouldRefresh(s))
    .map(([k]) => k);
  assert.deepEqual(eligible, ["subways", "railroads"]);
});

test("noteClockOffset accepts a timestamp without throwing", () => {
  // minClockOffset is internal (used by the countdown and the poll-age term);
  // just confirm the exported helper is callable and null-safe.
  assert.doesNotThrow(() => noteClockOffset(Date.now() / 1000));
  assert.doesNotThrow(() => noteClockOffset(null));
})

// ---------------- v2 route-polyline interpolation ----------------

test("polylineCumLengths sums segment lengths (lon deltas zero -> exact lat distances)", () => {
  assert.deepEqual(polylineCumLengths([[0, 0], [1, 0], [3, 0]]), [0, 1, 3]);
});

test("pointAtArcLength walks the polyline and clamps to [0, total]", () => {
  const points = [[0, 0], [1, 0], [3, 0]];
  const cum = polylineCumLengths(points);
  assert.deepEqual(pointAtArcLength(points, cum, 0), [0, 0]);
  assert.deepEqual(pointAtArcLength(points, cum, 3), [3, 0]);
  assert.deepEqual(pointAtArcLength(points, cum, 2), [2, 0]);
  assert.deepEqual(pointAtArcLength(points, cum, 0.5), [0.5, 0]);
  assert.deepEqual(pointAtArcLength(points, cum, -1), [0, 0]); // clamp low
  assert.deepEqual(pointAtArcLength(points, cum, 99), [3, 0]); // clamp high
});

function geomFrom(...polylines) {
  return polylines.map((points) => ({ points, cum: polylineCumLengths(points) }));
}

test("projectOntoRoute returns the nearest polyline within tolerance, null beyond it", () => {
  const geom = geomFrom([[0, 0], [2, 0], [2, 2]]);
  const on = projectOntoRoute(geom, 1, 0); // on the first segment, ~s=1
  assert.equal(on.poly, 0);
  assert.ok(on.dist < 1e-9);
  assert.ok(Math.abs(on.s - 1) < 1e-9);
  assert.equal(projectOntoRoute(geom, 3, 3), null); // far from every polyline
});

test("projectOntoRoute picks the closer of two polylines", () => {
  // Poly 0 runs along lat=0; poly 1 runs along lat=5. A point at lat~5 is poly 1.
  const geom = geomFrom([[0, 0], [0, 2]], [[5, 0], [5, 2]]);
  const r = projectOntoRoute(geom, 5, 1);
  assert.equal(r.poly, 1);
});

test("computeRouteSlice returns a slice when both stations hit the same polyline", () => {
  const geom = geomFrom([[0, 0], [0, 2], [2, 2]]); // L-shape
  const train = { prev_lat: 0, prev_lon: 0, latitude: 2, longitude: 2 };
  const slice = computeRouteSlice(train, geom, { maxSlice: 100 }); // length gate tested separately
  assert.equal(slice.points, geom[0].points);
  assert.ok(Math.abs(slice.s0 - 0) < 1e-9);
  assert.ok(Math.abs(slice.s1 - geom[0].cum[geom[0].cum.length - 1]) < 1e-9);
});

test("computeRouteSlice returns null when prev is missing or geom absent", () => {
  const geom = geomFrom([[0, 0], [0, 2]]);
  assert.equal(computeRouteSlice({ prev_lat: null, prev_lon: null, latitude: 0, longitude: 1 }, geom), null);
  assert.equal(computeRouteSlice({ prev_lat: 0, prev_lon: 0, latitude: 0, longitude: 1 }, null), null);
});

test("computeRouteSlice returns null when the stations are on different polylines", () => {
  const geom = geomFrom([[0, 0], [0, 2]], [[5, 0], [5, 2]]);
  assert.equal(computeRouteSlice({ prev_lat: 0, prev_lon: 0, latitude: 5, longitude: 2 }, geom), null);
});

test("computeRouteSlice rejects an over-long slice but a larger maxSlice admits it", () => {
  const geom = geomFrom([[0, 0], [2, 0]]); // arc length 2 (lat units), well over ROUTE_MAX_SLICE
  const train = { prev_lat: 0, prev_lon: 0, latitude: 2, longitude: 0 };
  assert.equal(computeRouteSlice(train, geom), null);
  const slice = computeRouteSlice(train, geom, { maxSlice: 5 });
  assert.ok(slice && Math.abs(slice.s1 - slice.s0) > 1.9);
});

// ---------------- railroad slice tolerance ----------------

test("the railroad slice cap is looser than the subway one", () => {
  // If the railroad cap were <= the subway cap, every long railroad segment
  // would fail the length gate and fall back to the straight chord.
  assert.ok(RAILROAD_ROUTE_MAX_SLICE > ROUTE_MAX_SLICE);
});

test("a railroad-scale segment is admitted by the railroad cap, rejected by the subway default", () => {
  // ~0.15 in the isotropic basis: the magnitude of the LIRR's longest real gap
  // (Amagansett to Montauk). The subway default rejects it; the railroad cap
  // admits it. Both stations sit on the polyline, so projection succeeds.
  const geom = geomFrom([[0, 0], [0.15, 0]]); // arc length 0.15 (lat units)
  const train = { prev_lat: 0, prev_lon: 0, latitude: 0.15, longitude: 0 };
  assert.equal(computeRouteSlice(train, geom), null); // subway default (0.05) rejects
  const slice = computeRouteSlice(train, geom, { maxSlice: RAILROAD_ROUTE_MAX_SLICE });
  assert.ok(slice && Math.abs(slice.s1 - slice.s0) > 0.14); // railroad cap admits
});

test("the PATH slice cap sits between the subway and railroad caps", () => {
  // PATH's longest real gap (Journal Square to Harrison, ~0.071) exceeds the
  // subway cap, but PATH never has railroad branch-scale gaps, so a cap as
  // loose as the railroad's would give up misprojection protection for
  // nothing. Both orderings matter.
  assert.ok(PATH_ROUTE_MAX_SLICE > ROUTE_MAX_SLICE);
  assert.ok(PATH_ROUTE_MAX_SLICE < RAILROAD_ROUTE_MAX_SLICE);
  assert.equal(PATH_ROUTE_ACCEPT_DIST, 0.0025); // same projection tolerance as the others
});

test("a PATH-scale segment is admitted by the PATH cap, rejected by the subway default", () => {
  // ~0.071 in the isotropic basis: the magnitude of Journal Square to
  // Harrison, PATH's longest real inter-station gap. The subway default
  // rejects it (falls back to the chord); the PATH cap admits it so the NJ
  // side glides along the track geometry.
  const geom = geomFrom([[0, 0], [0.071, 0]]); // arc length 0.071 (lat units)
  const train = { prev_lat: 0, prev_lon: 0, latitude: 0.071, longitude: 0 };
  assert.equal(computeRouteSlice(train, geom), null); // subway default (0.05) rejects
  const slice = computeRouteSlice(train, geom, { maxSlice: PATH_ROUTE_MAX_SLICE });
  assert.ok(slice && Math.abs(slice.s1 - slice.s0) > 0.07); // PATH cap admits
});

test("computePathRouteSlice cannot let twin direction polylines split a segment", () => {
  // The live-observed failure computeRouteSlice has on PATH geometry: the two
  // direction polylines are parallel tracks a few meters apart, and each
  // endpoint independently picks whichever twin is micro-closer. Here prev
  // sits nearer twin A (lon 0.0001) and next nearer twin B (lon 0.0009), so
  // the same-polyline rule kills the generic slice; the PATH picker scores
  // each twin with both endpoints together and glides anyway.
  const twinA = [[0, 0], [0.02, 0]];
  const twinB = [[0, 0.001], [0.02, 0.001]];
  const geom = geomFrom(twinA, twinB);
  const train = { prev_lat: 0, prev_lon: 0.0001, latitude: 0.02, longitude: 0.0009 };
  assert.equal(computeRouteSlice(train, geom, { maxSlice: PATH_ROUTE_MAX_SLICE }), null);
  const slice = computePathRouteSlice(train, geom);
  assert.ok(slice, "the PATH picker must slice a twin the generic rule split");
  assert.ok(Math.abs(slice.s1 - slice.s0) > 0.019); // the full segment, one twin
});

test("computePathRouteSlice keeps the acceptDist and maxSlice gates", () => {
  const geom = geomFrom([[0, 0], [0.2, 0]]);
  // Off-track endpoint: nothing within tolerance, chord fallback (null).
  assert.equal(
    computePathRouteSlice({ prev_lat: 0.01, prev_lon: 0, latitude: 0.2, longitude: 0 }, geom),
    null,
  );
  // Over-long arc: beyond the PATH cap, rejected like the generic rule.
  assert.equal(
    computePathRouteSlice({ prev_lat: 0, prev_lon: 0, latitude: 0.2, longitude: 0 }, geom),
    null,
  );
  // No anchors or no geometry: null, the placed fallback.
  assert.equal(computePathRouteSlice({ prev_lat: null, latitude: 0.1, longitude: 0 }, geom), null);
  assert.equal(
    computePathRouteSlice({ prev_lat: 0, prev_lon: 0, latitude: 0.1, longitude: 0 }, undefined),
    null,
  );
  // A PATH-scale segment (Journal Square to Harrison magnitude) is admitted.
  const ok = computePathRouteSlice(
    { prev_lat: 0, prev_lon: 0, latitude: 0.071, longitude: 0 },
    geomFrom([[0, 0], [0.071, 0]]),
  );
  assert.ok(ok && Math.abs(ok.s1 - ok.s0) > 0.07);
});

test("trainLatLng follows the route slice, not the chord, when _route is present", () => {
  const points = [[0, 0], [0, 2], [2, 2]]; // L-shaped: up then right
  const cum = polylineCumLengths(points);
  const total = cum[cum.length - 1];
  const train = {
    prev_lat: 0, prev_lon: 0, latitude: 2, longitude: 2,
    prev_time: 100, next_time: 200, stop_id: "X",
    _route: { points, cum, s0: 0, s1: total },
  };
  const got = trainLatLng(train, 150, {}); // f = 0.5
  assert.deepEqual(got, pointAtArcLength(points, cum, 0.5 * total));
  assert.notDeepEqual(got, [1, 1]); // NOT the straight-chord midpoint
});

test("trainLatLng falls back to the straight chord when _route is absent", () => {
  const train = {
    prev_lat: 0, prev_lon: 0, latitude: 2, longitude: 2,
    prev_time: 100, next_time: 200, stop_id: "X",
  };
  assert.deepEqual(trainLatLng(train, 150, {}), [1, 1]); // chord midpoint
});

test("trainLatLng monotonic-f clamp: dwell can't drag the marker backward; resets per segment", () => {
  const state = {};
  const train = { prev_lat: 0, prev_lon: 0, latitude: 10, longitude: 0, prev_time: 100, stop_id: "X" };
  assert.deepEqual(trainLatLng({ ...train, next_time: 200 }, 150, state), [5, 0]); // f=0.5
  // Dwell: next_time grows so rawF would drop to 0.2, but the clamp holds f at 0.5.
  assert.deepEqual(trainLatLng({ ...train, next_time: 400 }, 160, state), [5, 0]);
  // Time marches on within the same segment: f advances to 0.8.
  assert.deepEqual(trainLatLng({ ...train, next_time: 200 }, 180, state), [8, 0]);
  // New segment (stop_id changes): clamp resets, f = 0.1.
  assert.deepEqual(trainLatLng({ ...train, stop_id: "Y", next_time: 200 }, 110, state), [1, 0]);
});

// ---- AirTrain JFK static headway helpers ----

// Separate require (additive; leaves the top import block untouched).
const { selectHeadwayBand, airtrainStationPopupHtml } = require("./helpers.js");
const {
  parseColor, contrastRatio, readableTextOn, readableInk, INK_DARK, LINE_COLORS,
  statusLineText, MOBILE_MAX_WIDTH_PX, narrowViewport,
  // MR5: the background a popup head's ink is walked against now that a popup is not white.
  POPUP_SURFACE_FALLBACK,
  // R1: the rail families' neutral and the pair resolver that replaced the hash palette.
  RAIL_NEUTRAL_COLOR, railBranchPaint,
} = require("./helpers.js");

// The real reconciled bands from data/airtrain_jfk.json (all 3 routes share them):
// 15 min overnight, 7 min shoulders, 4 min midday, half-open [start, end).
const AIRTRAIN_BANDS = [
  { start: "00:00", end: "06:00", headway_min: 15 },
  { start: "06:00", end: "11:00", headway_min: 7 },
  { start: "11:00", end: "22:00", headway_min: 4 },
  { start: "22:00", end: "24:00", headway_min: 7 },
];

const AIRTRAIN_ROUTES = [
  { id: "2878", name: "Jamaica", stations: ["160565", "160564"], headways: AIRTRAIN_BANDS },
  { id: "2879", name: "Howard Beach", stations: ["160564"], headways: AIRTRAIN_BANDS },
];

test("selectHeadwayBand maps both sides of every real band edge (half-open)", () => {
  const hw = (m) => selectHeadwayBand(AIRTRAIN_BANDS, m)?.headway_min;
  assert.equal(hw(0), 15); // 00:00 start of day
  assert.equal(hw(359), 15); // 05:59 last minute of the overnight band
  assert.equal(hw(360), 7); // 06:00 belongs to the NEXT band, not the one ending here
  assert.equal(hw(659), 7); // 10:59
  assert.equal(hw(660), 4); // 11:00
  assert.equal(hw(1319), 4); // 21:59
  assert.equal(hw(1320), 7); // 22:00
  assert.equal(hw(1439), 7); // 23:59 last minute of the day
});

test("selectHeadwayBand returns null on a gapped table (true null path)", () => {
  // Deliberately gapped: nothing covers 07:00-09:00 (420..540).
  const gapped = [
    { start: "06:00", end: "07:00", headway_min: 5 },
    { start: "09:00", end: "10:00", headway_min: 5 },
  ];
  assert.equal(selectHeadwayBand(gapped, 420), null); // 07:00 exactly, in the gap
  assert.equal(selectHeadwayBand(gapped, 480), null); // 08:00, mid-gap
  assert.equal(selectHeadwayBand(gapped, 539), null); // 08:59, last gap minute
  assert.equal(selectHeadwayBand(gapped, 400)?.headway_min, 5); // 06:40 IS covered (sanity)
  // Missing / empty band lists degrade to null, never throw.
  assert.equal(selectHeadwayBand([], 600), null);
  assert.equal(selectHeadwayBand(undefined, 600), null);
});

test("selectHeadwayBand pins out-of-range inputs to null", () => {
  // -1 precedes every band; 1440 is the exclusive end of the last band. Both fall
  // outside every half-open interval, so the defined behavior is null.
  assert.equal(selectHeadwayBand(AIRTRAIN_BANDS, -1), null);
  assert.equal(selectHeadwayBand(AIRTRAIN_BANDS, 1440), null);
});

test("airtrainStationPopupHtml: scheduled label + subhead, single-branch station", () => {
  const station = { id: "160565", name: "Jamaica Station-Station D" };
  const html = airtrainStationPopupHtml(station, AIRTRAIN_ROUTES, 720); // 12:00 -> 4 min
  assert.match(html, /Jamaica Station-Station D/);
  assert.match(html, /scheduled service \(no live tracking\)/);
  // MR5: a branch is a label and its headway is the value, in section 5's grid. The colon the
  // sentence used to carry is the column between them; "(scheduled)" stays inside the value.
  assert.match(html, /<div class="k">Jamaica<\/div>\n<div class="v">every ~4 min \(scheduled\)<\/div>/);
  assert.doesNotMatch(html, /Howard Beach/); // 160565 is served only by the Jamaica branch
});

test("airtrainStationPopupHtml: multi-branch station lists every serving branch", () => {
  const station = { id: "160564", name: "Federal Circle-Station C" };
  const html = airtrainStationPopupHtml(station, AIRTRAIN_ROUTES, 720);
  assert.match(html, /<div class="k">Jamaica<\/div>\n<div class="v">every ~4 min \(scheduled\)<\/div>/);
  assert.match(html, /<div class="k">Howard Beach<\/div>\n<div class="v">every ~4 min \(scheduled\)<\/div>/);
});

test("airtrainStationPopupHtml: null band renders a fallback, never 'undefined'", () => {
  const station = { id: "160564", name: "Federal Circle-Station C" };
  const html = airtrainStationPopupHtml(station, AIRTRAIN_ROUTES, 1440); // out of range -> null band
  assert.match(html, /schedule unavailable/);
  assert.doesNotMatch(html, /undefined/);
  assert.doesNotMatch(html, /every ~/); // no headway number when the band is unknown
});

test("airtrainStationPopupHtml escapes station and route names", () => {
  const station = { id: "x", name: "<script>Evil</script>" };
  const routes = [{ id: "r", name: "A&B <Branch>", stations: ["x"], headways: AIRTRAIN_BANDS }];
  const html = airtrainStationPopupHtml(station, routes, 720);
  assert.match(html, /&lt;script&gt;Evil&lt;\/script&gt;/);
  assert.match(html, /A&amp;B &lt;Branch&gt;/);
  assert.doesNotMatch(html, /<script>Evil<\/script>/); // the raw tag never reaches the DOM
});

test("airtrainStationPopupHtml uses no live-countdown markup", () => {
  const station = { id: "160564", name: "Federal Circle" };
  const html = airtrainStationPopupHtml(station, AIRTRAIN_ROUTES, 720);
  /* None of the CSS classes the live-arrivals countdown popups use. MR5 renamed the bucket heading
     from .arr-dir to section 5's .dir, and this asks for the class ATTRIBUTE rather than the bare
     word: "dir" is three letters that occur inside ordinary prose, and a substring test on it would
     pass or fail for reasons that have nothing to do with a heading. */
  for (const cls of ["dir", "arr", "arr-badge", "arr-none"]) {
    assert.ok(!html.includes(`class="${cls}"`), `must not use live-arrivals class ${cls}`);
  }
});

test("airtrainStationPopupHtml: station served by no branch", () => {
  const station = { id: "999", name: "Nowhere" };
  const html = airtrainStationPopupHtml(station, AIRTRAIN_ROUTES, 720);
  assert.match(html, /No AirTrain branch serves this station/);
  assert.doesNotMatch(html, /undefined/);
});

// ---- Service alerts helpers (phase 12b) ----

const { indexAlerts, matchStationAlerts, alertsBlockHtml } = require("./helpers.js");

// s1/s2 are subway; l1 is LIRR with a COLLIDING numeric stop ("127") and route
// ("1") shared with subway ids, to prove system scoping keeps them apart.
const ALERTS = [
  { id: "s1", system: "subway", header: "[2] delays", routes: ["2"], stops: ["127"], starts_at: 100, ends_at: null },
  { id: "s2", system: "subway", header: "Signal work", routes: ["Q"], stops: ["R20"], starts_at: 200, ends_at: 999 },
  { id: "l1", system: "LIRR", header: "LIRR alert", routes: ["1"], stops: ["127"], starts_at: 50, ends_at: null },
];

test("matchStationAlerts matches by stop id", () => {
  const idx = indexAlerts(ALERTS);
  const got = matchStationAlerts(idx, "subway", "127", []); // no arrivals routes
  assert.deepEqual(got.map((a) => a.id), ["s1"]);
});

test("matchStationAlerts matches by a route serving the station", () => {
  const idx = indexAlerts(ALERTS);
  // Station id not in any stop selector, but route Q serves it (routeIds is the
  // caller's union of the static routes-per-station index and the arrivals, H5).
  const got = matchStationAlerts(idx, "subway", "somewhere-else", ["Q"]);
  assert.deepEqual(got.map((a) => a.id), ["s2"]);
});

test("matchStationAlerts is scoped by system (LIRR ids never leak into subway)", () => {
  const idx = indexAlerts(ALERTS);
  // Subway popup at station "127" with route "1" in arrivals: the LIRR alert l1
  // shares BOTH that stop id and route id, but must not appear under "subway".
  const subway = matchStationAlerts(idx, "subway", "127", ["1"]);
  assert.deepEqual(subway.map((a) => a.id), ["s1"]);
  // The same collision resolves the other way under the LIRR system.
  const lirr = matchStationAlerts(idx, "LIRR", "127", ["1"]);
  assert.deepEqual(lirr.map((a) => a.id), ["l1"]);
});

test("matchStationAlerts dedups an alert matching by both stop and route", () => {
  const idx = indexAlerts(ALERTS);
  // s1 has stop "127" AND route "2"; passing both must yield it exactly once.
  const got = matchStationAlerts(idx, "subway", "127", ["2"]);
  assert.deepEqual(got.map((a) => a.id), ["s1"]);
});

test("matchStationAlerts sorts open-ended first, then by starts_at, then id", () => {
  const sortAlerts = [
    { id: "b", system: "subway", header: "b", routes: [], stops: ["X"], starts_at: 300, ends_at: null },
    { id: "a", system: "subway", header: "a", routes: [], stops: ["X"], starts_at: 100, ends_at: null },
    { id: "d", system: "subway", header: "d", routes: [], stops: ["X"], starts_at: 50, ends_at: 999 },
    { id: "c", system: "subway", header: "c", routes: [], stops: ["X"], starts_at: 100, ends_at: null },
  ];
  const got = matchStationAlerts(indexAlerts(sortAlerts), "subway", "X", []);
  // open-ended (a,c,b) before dated (d); within open-ended by start then id: a,c,b.
  assert.deepEqual(got.map((a) => a.id), ["a", "c", "b", "d"]);
});

test("matchStationAlerts returns [] for an empty store and for no matches", () => {
  assert.deepEqual(matchStationAlerts(indexAlerts([]), "subway", "127", ["2"]), []);
  assert.deepEqual(matchStationAlerts(indexAlerts(ALERTS), "subway", "ZZZ", ["ZZ"]), []);
});

test("alertsBlockHtml renders escaped header rows, or nothing when empty", () => {
  assert.equal(alertsBlockHtml([]), "");
  const html = alertsBlockHtml([{ id: "x", header: "Delay <at> Times & 5 St" }]);
  assert.match(html, /class="alert-block"/);
  assert.match(html, /class="alert-row"/);
  assert.match(html, /Delay &lt;at&gt; Times &amp; 5 St/);
  assert.doesNotMatch(html, /<at>/); // raw markup never reaches the popup
});

test("alertsBlockHtml skips alerts with no header and renders nothing if all are empty", () => {
  assert.equal(alertsBlockHtml([{ id: "x", header: null }]), "");
});

// ---- The station alert join, shared by the popup and the panel (F11) ----

const {
  stationArrivalsRows, stationAlertRouteIds, stationAlerts, stationAlertSystem,
  alertSourceNote, stationAlertAnnouncement, shownAlerts,
  ALERTS_STALE_NOTE, ALERTS_RETAINED_NOTE,
} = require("./helpers.js");

// The three body shapes the real endpoints serve, at their smallest.
const DIRECTIONS_BODY = { directions: { Northbound: [{ route_id: "1" }], Southbound: [{ route_id: "2" }] } };
const FERRY_BODY = { routes: { "East River": [{ route_id: "ER" }], "South Brooklyn": [{ route_id: "SB" }] } };
const FLAT_BODY = { arrivals: [{ route_id: "9" }, { route_id: "2" }, { route_id: null }] };

test("stationArrivalsRows reads directions, ferry route buckets, and a flat list", () => {
  assert.deepEqual(stationArrivalsRows(DIRECTIONS_BODY).map((r) => r.route_id), ["1", "2"]);
  assert.deepEqual(stationArrivalsRows(FERRY_BODY).map((r) => r.route_id), ["ER", "SB"]);
  assert.deepEqual(stationArrivalsRows(FLAT_BODY).map((r) => r.route_id), ["9", "2", null]);
});

test("stationArrivalsRows yields nothing for a body it cannot read", () => {
  for (const body of [null, undefined, {}, { directions: null }, { directions: "nope" }, { arrivals: null }]) {
    assert.deepEqual(stationArrivalsRows(body), [], JSON.stringify(body));
  }
});

test("stationArrivalsRows does not mistake a station's routes ARRAY for ferry buckets", () => {
  // `routes` names two different things in this codebase: a station's is an array of
  // route ids, a ferry body's is an object of buckets. Iterating the array as buckets
  // would try to spread a string, which is the crash this guard exists for.
  assert.deepEqual(stationArrivalsRows({ routes: ["ER", "SB"] }), []);
});

test("stationAlertRouteIds unions the static routes with the routes in the arrivals", () => {
  const ids = stationAlertRouteIds({ id: "127", routes: ["1", "2", "3"] }, DIRECTIONS_BODY);
  assert.deepEqual([...ids].sort(), ["1", "2", "3"]);
});

test("stationAlertRouteIds adds a route that is running but not in the static list", () => {
  // THE HALF THE FLAT SHAPE USED TO LOSE. Before F11 only `directions` was read, so a
  // ferry dock or an NJ Transit station whose static routes list was empty or behind
  // contributed nothing from its board at all.
  const flat = stationAlertRouteIds({ id: "12", routes: ["2"] }, FLAT_BODY);
  assert.deepEqual([...flat].sort(), ["2", "9"]);
  const ferry = stationAlertRouteIds({ id: "2", routes: ["ER"] }, FERRY_BODY);
  assert.deepEqual([...ferry].sort(), ["ER", "SB"]);
});

test("stationAlertRouteIds falls back to the static list with no body at all", () => {
  // The panel renders its alerts before the first arrivals fetch resolves and keeps
  // rendering them when it fails, which is only correct because this holds.
  const ids = stationAlertRouteIds({ id: "127", routes: ["1", "2", "3"] }, null);
  assert.deepEqual([...ids].sort(), ["1", "2", "3"]);
});

test("stationAlerts is the join both surfaces call, and a flat board reaches it", () => {
  const store = [
    { id: "njt-route-9", system: "njt", header: "[9] suspended", routes: ["9"], stops: [], starts_at: 1, ends_at: null },
    { id: "njt-stop", system: "njt", header: "Hoboken closed", routes: [], stops: ["12"], starts_at: 2, ends_at: null },
  ];
  const idx = indexAlerts(store);
  // Hoboken's static routes are 2 and 17; route 9 reaches it only through the flat
  // board. Both alerts land, in compareAlerts order (both open-ended, by starts_at).
  const hoboken = { id: "12", routes: ["2", "17"] };
  assert.deepEqual(
    stationAlerts(idx, "njt", hoboken, { arrivals: [{ route_id: "9" }] }).map((a) => a.id),
    ["njt-route-9", "njt-stop"],
  );
  // Without the board, only the stop selector matches: this is the difference the
  // arrivals side of the union makes, isolated.
  assert.deepEqual(stationAlerts(idx, "njt", hoboken, null).map((a) => a.id), ["njt-stop"]);
});

test("stationAlerts with no system matches nothing, however loud the store is", () => {
  const idx = indexAlerts([
    { id: "x", system: "subway", header: "everything is suspended", routes: ["1"], stops: ["127"], starts_at: 1, ends_at: null },
  ]);
  assert.deepEqual(stationAlerts(idx, null, { id: "127", routes: ["1"] }, null), []);
  assert.deepEqual(stationAlerts(idx, "", { id: "127", routes: ["1"] }, null), []);
});

test("stationAlertSystem maps a registry entry to its alert FEED, not its mode", () => {
  assert.equal(stationAlertSystem({ kind: "subway" }), "subway");
  assert.equal(stationAlertSystem({ kind: "ferry" }), "ferry");
  assert.equal(stationAlertSystem({ kind: "njt" }), "njt");
  // The two railroads share one map layer and one kind, and publish separate alert
  // feeds: the entry's own system is what the index is keyed by.
  assert.equal(stationAlertSystem({ kind: "railroad", system: "LIRR" }), "LIRR");
  assert.equal(stationAlertSystem({ kind: "railroad", system: "MNR" }), "MNR");
});

test("stationAlertSystem and alertSourceNote read own keys only", () => {
  // Both keys come off a parsed payload, and a plain bracket read finds INHERITED
  // properties: a kind of "constructor" would answer with Object's constructor rather
  // than null, and the panel would then ask the alert index for a function's alerts.
  // Nothing in the app produces such a key; a total function does not rely on that.
  assert.equal(stationAlertSystem({ kind: "constructor" }), null);
  assert.equal(stationAlertSystem({ kind: "__proto__" }), null);
  assert.equal(stationAlertSystem({ kind: "toString" }), null);
  assert.equal(alertSourceNote({}, "constructor", 1000, 1000), "");
  assert.equal(alertSourceNote({}, "__proto__", 1000, 1000), "");
});

test("stationAlertSystem returns null for the modes with no alerts feed", () => {
  // ALERT_FEED_URLS has no PATH and no AirTrain entry. Null is what keeps the panel
  // from rendering an alerts area whose silence would mean no data, not no alerts.
  assert.equal(stationAlertSystem({ kind: "path" }), null);
  assert.equal(stationAlertSystem({ kind: "airtrain" }), null);
  assert.equal(stationAlertSystem({ kind: "railroad" }), null); // no system on the entry
  assert.equal(stationAlertSystem(null), null);
});

// ---- The alert SOURCE hedge, per system (F11) ----

const FRESH = { fetchedAt: 1000, ok: true, retainedSince: null };

test("alertSourceNote says nothing while this system's own feed is current", () => {
  assert.equal(alertSourceNote({ subway: FRESH }, "subway", 1000, 1000), "");
});

test("alertSourceNote hedges on THIS system's age, not the envelope minimum", () => {
  // THE DIFFERENCE FROM THE POPUP MARKER, isolated. staleAlertsMarker ages against
  // alertsFreshnessBasis, the minimum across every system, which is right for the
  // agency-wide banner and wrong for a board showing one system: a frozen ferry feed
  // would hedge a perfectly current NJ Transit departure list.
  const systems = { njt: FRESH, ferry: { fetchedAt: 0, ok: false, retainedSince: null } };
  // One second inside NJ Transit's own threshold and long past the ferry's. The
  // envelope basis handed in is the ferry's 0, which is what the popup marker would
  // age against for both.
  const now = 1000 + ALERTS_STALE_AFTER_S - 1;
  assert.equal(alertSourceNote(systems, "njt", 0, now), "");
  assert.equal(alertSourceNote(systems, "ferry", 0, now), ALERTS_STALE_NOTE);
  // And NJ Transit does hedge once its OWN feed crosses, so this is a different
  // basis rather than a hedge that never fires.
  assert.equal(alertSourceNote(systems, "njt", 0, 1000 + ALERTS_STALE_AFTER_S), ALERTS_STALE_NOTE);
});

test("alertSourceNote says HELD, with an age, for a retained set", () => {
  // retained_since means the backend is serving the alerts it last decoded because
  // this feed is down (feeds/alerts.py merge_alert_generations). The set is not late,
  // it is held, and the age is what a rider needs.
  const systems = { LIRR: { fetchedAt: 900, ok: false, retainedSince: 700 } };
  assert.equal(alertSourceNote(systems, "LIRR", 900, 1000), `${ALERTS_RETAINED_NOTE} 5m ago`);
});

test("alertSourceNote prefers HELD over stale when a retained feed is also old", () => {
  const systems = { LIRR: { fetchedAt: 0, ok: false, retainedSince: 700 } };
  const note = alertSourceNote(systems, "LIRR", 0, 1000);
  assert.equal(note, `${ALERTS_RETAINED_NOTE} 5m ago`);
  assert.notEqual(note, ALERTS_STALE_NOTE);
});

test("alertSourceNote hedges a system that has NEVER decoded, against the first attempt", () => {
  // alertsFreshnessBasis deliberately SKIPS a never-decoded system, because four other
  // timestamps are still describing the set it feeds. Here the never-decoded system IS
  // the set, so the same reasoning points the other way.
  const systems = { njt: { fetchedAt: null, ok: false, retainedSince: null } };
  const sinceAt = 1000;
  assert.equal(alertSourceNote(systems, "njt", 1000, sinceAt + 1, sinceAt), "");
  assert.equal(
    alertSourceNote(systems, "njt", 1000, sinceAt + ALERTS_STALE_AFTER_S, sinceAt),
    ALERTS_STALE_NOTE,
  );
});

test("alertSourceNote falls back to the envelope basis when this system has no block", () => {
  // An /api/alerts body with no per-system block at all, which is the shape that
  // predates C2 and the shape ingestSystems synthesizes under a source key.
  assert.equal(alertSourceNote({}, "subway", 1000, 1000 + ALERTS_STALE_AFTER_S), ALERTS_STALE_NOTE);
  assert.equal(alertSourceNote({ alerts: FRESH }, "subway", 1000, 1000), "");
});

test("alertSourceNote says nothing when there is no system to be honest about", () => {
  assert.equal(alertSourceNote({}, null, 0, 1e9, 0), "");
});

// ---- The panel's alert announcement (F11) ----

const idsOf = (...headers) =>
  alertIdentities(headers.map((h, i) => ({ system: "subway", id: `a${i}`, header: h })));

test("stationAlertAnnouncement seeds silently on the first observation", () => {
  assert.equal(stationAlertAnnouncement(null, idsOf("Times Sq closed")), null);
  assert.equal(stationAlertAnnouncement(idsOf("Times Sq closed"), null), null);
});

test("stationAlertAnnouncement speaks once when an alert appears", () => {
  assert.equal(stationAlertAnnouncement(idsOf(), idsOf("Times Sq closed")), "New service alert for this station.");
  assert.equal(
    stationAlertAnnouncement(idsOf(), idsOf("a", "b")),
    "2 new service alerts for this station.",
  );
});

test("stationAlertAnnouncement stays silent on an unchanged set", () => {
  const set = idsOf("Times Sq closed");
  assert.equal(stationAlertAnnouncement(set, set), null);
});

test("stationAlertAnnouncement speaks when an alert CLEARS, unlike the banner", () => {
  // The deliberate divergence from bannerAnnouncement, pinned in both directions so
  // neither can be "fixed" into the other by someone who finds one of them surprising.
  assert.equal(
    stationAlertAnnouncement(idsOf("Times Sq closed"), idsOf()),
    "Service alerts for this station have cleared.",
  );
  assert.equal(bannerAnnouncement(idsOf("Times Sq closed"), idsOf()), null);
});

test("stationAlertAnnouncement distinguishes one clearing from all clearing", () => {
  const before = alertIdentities([
    { system: "subway", id: "a", header: "closed" },
    { system: "subway", id: "b", header: "delays" },
  ]);
  const after = alertIdentities([{ system: "subway", id: "b", header: "delays" }]);
  assert.equal(
    stationAlertAnnouncement(before, after),
    "A service alert for this station has cleared.",
  );
});

test("stationAlertAnnouncement treats a REWORDED alert as new, like the banner", () => {
  // The identity carries a hash of the header, so an incident revised in place under
  // one id is news rather than an unchanged set. The MTA really does revise wording
  // under a stable id; C1 recorded it for the banner and the same reasoning holds here.
  assert.equal(
    stationAlertAnnouncement(idsOf("Delays on the 4 line"), idsOf("All service suspended")),
    "New service alert for this station.",
  );
});

test("shownAlerts drops a headerless alert, so both renderers draw the same rows", () => {
  // The GTFS-RT alert message makes header_text optional and the feeds really do omit
  // it. alertsBlockHtml has always dropped these; the panel's element renderer had to
  // be taught the same rule or the same alert would be a blank bullet on one surface
  // and absent from the other. Sharing the filter is what keeps that from drifting.
  const alerts = [
    { id: "a", system: "subway", header: "Times Sq closed" },
    { id: "b", system: "subway", header: null },
    { id: "c", system: "subway" },
  ];
  assert.deepEqual(shownAlerts(alerts).map((a) => a.id), ["a"]);
  assert.deepEqual(shownAlerts(null), []);
  // And the popup renderer agrees, because it is the same filter.
  assert.equal(alertsBlockHtml(alerts), alertsBlockHtml(shownAlerts(alerts)));
});

test("stationAlertAnnouncement never speaks an alert's body", () => {
  // A SUMMARY, NEVER THE BODY: a live region reading a full service alert aloud would
  // be unusable during exactly the incident it exists for.
  const header = "Uptown 1 2 3 trains are rerouted via the express track after a fire";
  const spoken = stationAlertAnnouncement(idsOf(), idsOf(header));
  assert.ok(spoken);
  assert.ok(!spoken.includes("rerouted"), spoken);
});

// ---- Service alerts: route surfaces + agency-wide banner (phase 12c) ----

const { matchRouteAlerts, bannerAlerts } = require("./helpers.js");

const ROUTE_ALERTS = [
  { id: "bus-1", system: "bus", header: "B46 detour", routes: ["B46"], stops: [], starts_at: 100, ends_at: null },
  { id: "sub-b46", system: "subway", header: "hypothetical subway B46", routes: ["B46"], stops: [], starts_at: 100, ends_at: null },
  { id: "wide-1", system: "subway", header: "systemwide A", routes: [], stops: [], starts_at: 300, ends_at: null },
  { id: "wide-2", system: "LIRR", header: "systemwide B", routes: [], stops: [], starts_at: 100, ends_at: 999 },
  { id: "route-only", system: "bus", header: "M15 note", routes: ["M15"], stops: [], starts_at: 50, ends_at: null },
  { id: "stop-only", system: "subway", header: "stop note", routes: [], stops: ["127"], starts_at: 50, ends_at: null },
  { id: "route-and-stop", system: "subway", header: "both", routes: ["2"], stops: ["127"], starts_at: 50, ends_at: null },
];

test("matchRouteAlerts matches a bus route and is scoped by system", () => {
  const idx = indexAlerts(ROUTE_ALERTS);
  // bus "B46" matches only the bus alert, never the same-id subway alert.
  assert.deepEqual(matchRouteAlerts(idx, "bus", "B46").map((a) => a.id), ["bus-1"]);
  assert.deepEqual(matchRouteAlerts(idx, "subway", "B46").map((a) => a.id), ["sub-b46"]);
});

test("matchRouteAlerts returns [] for a null/missing route_id and for no match", () => {
  const idx = indexAlerts(ROUTE_ALERTS);
  assert.deepEqual(matchRouteAlerts(idx, "bus", null), []);
  assert.deepEqual(matchRouteAlerts(idx, "bus", undefined), []);
  assert.deepEqual(matchRouteAlerts(idx, "bus", "Q99"), []);
  assert.deepEqual(matchRouteAlerts(indexAlerts([]), "bus", "B46"), []);
});

test("matchRouteAlerts dedups an alert that names the route more than once", () => {
  const dup = [{ id: "z", system: "bus", header: "z", routes: ["B46", "B46"], stops: [], starts_at: 1, ends_at: null }];
  assert.deepEqual(matchRouteAlerts(indexAlerts(dup), "bus", "B46").map((a) => a.id), ["z"]);
});

test("matchRouteAlerts sorts deterministically like the station matcher", () => {
  const alerts = [
    { id: "b", system: "bus", header: "b", routes: ["X"], stops: [], starts_at: 300, ends_at: null },
    { id: "a", system: "bus", header: "a", routes: ["X"], stops: [], starts_at: 100, ends_at: null },
    { id: "d", system: "bus", header: "d", routes: ["X"], stops: [], starts_at: 50, ends_at: 999 },
    { id: "c", system: "bus", header: "c", routes: ["X"], stops: [], starts_at: 100, ends_at: null },
  ];
  assert.deepEqual(matchRouteAlerts(indexAlerts(alerts), "bus", "X").map((a) => a.id), ["a", "c", "b", "d"]);
});

// Pins the ferry alert scoping after H5: a DOCK joins the UNION of stop-scoped
// alerts and route-scoped alerts for every route serving it (the ferry render passes
// the dock's routes-per-station list, s.routes, as the route ids); a BOAT joins by
// its own route. So a route-scoped ferry alert now reaches the dock, and also every
// boat of that route.
test("ferry alert scope: a dock joins stop AND its served routes; a boat joins by route", () => {
  const idx = indexAlerts([
    { id: "dock", system: "ferry", header: "Wall St/Pier 11 closed", routes: [], stops: ["18"], starts_at: 1, ends_at: null },
    { id: "route", system: "ferry", header: "Rockaway/Soundview reroute", routes: ["ER"], stops: [], starts_at: 1, ends_at: null },
  ]);
  // Dock at stop 18 served by route ER: BOTH the stop-scoped and the route-scoped
  // alert surface (union), deduped and sorted by id.
  assert.deepEqual(matchStationAlerts(idx, "ferry", "18", ["ER"]).map((a) => a.id), ["dock", "route"]);
  // Degraded case (the routes-per-station derive came up empty, e.g. the committed
  // trim has no stop_times): with no route ids the dock falls back to stop-only.
  assert.deepEqual(matchStationAlerts(idx, "ferry", "18", []).map((a) => a.id), ["dock"]);
  // The route-scoped alert also reaches riders on every ER boat.
  assert.deepEqual(matchRouteAlerts(idx, "ferry", "ER").map((a) => a.id), ["route"]);
  // A null-route boat matches nothing.
  assert.deepEqual(matchRouteAlerts(idx, "ferry", null), []);
});

test("bannerAlerts keeps only selector-less alerts, across systems, sorted", () => {
  // wide-1 (open-ended) before wide-2 (dated); everything with a route or stop is out.
  assert.deepEqual(bannerAlerts(ROUTE_ALERTS).map((a) => a.id), ["wide-1", "wide-2"]);
});

test("bannerAlerts excludes route-only, stop-only, and route+stop alerts", () => {
  const scoped = [
    { id: "r", system: "bus", header: "r", routes: ["M15"], stops: [], starts_at: 1, ends_at: null },
    { id: "s", system: "subway", header: "s", routes: [], stops: ["127"], starts_at: 1, ends_at: null },
    { id: "rs", system: "subway", header: "rs", routes: ["2"], stops: ["127"], starts_at: 1, ends_at: null },
  ];
  assert.deepEqual(bannerAlerts(scoped), []);
});

test("bannerAlerts handles an empty or missing list", () => {
  assert.deepEqual(bannerAlerts([]), []);
  assert.deepEqual(bannerAlerts(undefined), []);
});

// ---- NYC Ferry helpers (phase 14c) ----

test("orderedFerryBuckets sorts route-name buckets alphabetically, dropping empties", () => {
  const arr = (n) => Array.from({ length: n }, (_, i) => ({ route_id: "ER", arrival: i }));
  assert.deepEqual(
    orderedFerryBuckets({ "South Brooklyn": arr(1), Astoria: arr(2), "East River": arr(1) }).map(
      (b) => b[0],
    ),
    ["Astoria", "East River", "South Brooklyn"],
  );
  // A bucket with no rows is omitted, not rendered empty.
  assert.deepEqual(orderedFerryBuckets({ Astoria: [], "East River": arr(1) }).map((b) => b[0]), [
    "East River",
  ]);
  assert.deepEqual(orderedFerryBuckets({}), []);
  assert.deepEqual(orderedFerryBuckets(undefined), []);
});

test("ferryArrivalDisplay counts down to arrival, then to departure once dwelling", () => {
  // Before the boat reaches the dock: arrival countdown.
  assert.deepEqual(ferryArrivalDisplay({ arrival: 120, departure: 180 }, 40), {
    mode: "arriving",
    seconds: 80,
  });
  // Dwelling (arrival already passed, departure still ahead): departure countdown.
  assert.deepEqual(ferryArrivalDisplay({ arrival: 30, departure: 180 }, 40), {
    mode: "departing",
    seconds: 140,
  });
  // Origin dock (no arrival, only a departure): departure countdown.
  assert.deepEqual(ferryArrivalDisplay({ arrival: null, departure: 90 }, 40), {
    mode: "departing",
    seconds: 50,
  });
  // Terminal dock (only an arrival) that has just passed: keep the arrival
  // countdown rather than dropping the row (it renders "now").
  assert.deepEqual(ferryArrivalDisplay({ arrival: 20, departure: null }, 40), {
    mode: "arriving",
    seconds: -20,
  });
  // Exactly at the arrival instant is still "arriving" (not yet dwelling).
  assert.equal(ferryArrivalDisplay({ arrival: 40, departure: 90 }, 40).mode, "arriving");
});

test("ferryBoatIconState maps STOPPED_AT to docked and everything else to active", () => {
  assert.equal(ferryBoatIconState("STOPPED_AT"), "docked");
  assert.equal(ferryBoatIconState("IN_TRANSIT_TO"), "active");
  assert.equal(ferryBoatIconState("INCOMING_AT"), "active");
  assert.equal(ferryBoatIconState(null), "active"); // unknown/missing: not frozen-looking
  assert.equal(ferryBoatIconState("FUTURE_ENUM"), "active");
});

test("ferryStatusText maps known statuses to plain words, omits the unknown", () => {
  assert.equal(ferryStatusText("STOPPED_AT"), "At dock");
  assert.equal(ferryStatusText("INCOMING_AT"), "Arriving at dock");
  assert.equal(ferryStatusText("IN_TRANSIT_TO"), "Under way");
  assert.equal(ferryStatusText(null), null); // omitted rather than asserted
  assert.equal(ferryStatusText("FUTURE_ENUM"), null);
});

test("ferrySpeedKnots converts m/s to knots only for an under-way boat above the floor", () => {
  // 6.5 m/s * 1.94384 = 12.6 kn (one decimal), under way -> shown.
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", 6.5), "12.6 kn");
  // 4.0 m/s * 1.94384 = 7.8 kn.
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", 4.0), "7.8 kn");
  // At the floor (0.5 m/s = 0.97 kn) it still shows, rounded to 1.0 kn.
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", 0.5), "1.0 kn");
  // Below the floor is dock jitter, not motion -> omitted.
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", 0.2), null);
  // Only IN_TRANSIT_TO shows speed; docked/arriving boats do not.
  assert.equal(ferrySpeedKnots("STOPPED_AT", 6.5), null);
  assert.equal(ferrySpeedKnots("INCOMING_AT", 6.5), null);
  // Missing or non-numeric speed -> omitted, never "NaN kn".
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", null), null);
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", undefined), null);
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", "6.5"), null);
  // A numeric-but-non-finite reading (a raw protobuf float can be NaN/Infinity)
  // is caught by the Number.isFinite guard, not the typeof or floor checks:
  // typeof NaN === "number" and NaN < FLOOR is false, so this is the only clause
  // standing between a garbage feed value and a rendered "NaN kn"/"Infinity kn".
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", NaN), null);
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", Infinity), null);
  assert.equal(ferrySpeedKnots("IN_TRANSIT_TO", -Infinity), null);
});

test("ferryBoatPopupHtml shows label, route name, status, and under-way speed in knots; escapes", () => {
  const html = ferryBoatPopupHtml(
    { label: "H201", status: "IN_TRANSIT_TO", speed: 6.5 },
    "East River",
    "#00839c",
  );
  assert.ok(html.includes("East River"));
  /* A3: the heading carries the route's IDENTITY, darkened only as far as readability demands.
     This fixture colour is a real NYC Ferry route colour and it measures 4.44 on white, so it
     darkens; the old assertion pinned the literal #00839c and was therefore pinning an
     unreadable value. Asserting the obligation instead survives any future change to how far
     readableInk goes.
     MR5 MOVED THE BACKGROUND, NOT THE OBLIGATION. The popup is --surface rather than white, so
     the obligation is now against that: measured, #00839c is walked to #007c94 for 4.87 on white
     and to #006f85 for 4.80 here. The white form is excluded too, because the two differ and that
     is the only way this can tell which one shipped. */
  assert.ok(html.includes(readableInk("#00839c", POPUP_SURFACE_FALLBACK)));
  assert.ok(contrastRatio(readableInk("#00839c", POPUP_SURFACE_FALLBACK), POPUP_SURFACE_FALLBACK) >= 4.5);
  assert.ok(!html.includes(`color:${readableInk("#00839c")}"`), "the head must not be inked against white");
  // MR5: section 5's rows. "Boat H201" was one line with the noun in front of the label; it is a
  // label cell and a value cell now, and "NYC Ferry" is the kicker the popup opens with.
  assert.ok(html.includes('<div class="k">Boat</div>\n<div class="v">H201</div>'));
  assert.ok(html.includes('<div class="k">Status</div>\n<div class="v">Under way</div>'));
  assert.ok(html.startsWith('<div class="pk"><span>NYC Ferry</span>'));
  // Under way above the floor: speed shown in knots (H4). 6.5 m/s = 12.6 kn.
  assert.ok(html.includes("12.6 kn"));
  // The raw m/s value is never surfaced.
  assert.ok(!html.includes("6.5"));
});

test("ferryBoatPopupHtml omits speed for a docked boat", () => {
  const html = ferryBoatPopupHtml(
    { label: "H202", status: "STOPPED_AT", speed: 0.3 },
    "East River",
    "#00839c",
  );
  assert.ok(html.includes("At dock"));
  // Docked boat: no speed row at all (dock jitter is noise, not motion).
  assert.ok(!html.includes("kn"));
  assert.ok(!html.includes(">Speed<"));
});

test("ferryBoatPopupHtml labels a null-route boat Unassigned and omits an unknown status", () => {
  const html = ferryBoatPopupHtml({ label: "H099", status: null }, null, FERRY_FALLBACK_COLOR);
  assert.ok(html.includes("Unassigned"));
  // The fallback fill is #78909c, which is 2.72 on white and therefore darkens when used as
  // heading text. Its use as a chip FILL is unchanged and covered separately. MR5: against the
  // popup's own surface rather than white, so #60737d becomes #5a6c75.
  assert.ok(html.includes(readableInk(FERRY_FALLBACK_COLOR, POPUP_SURFACE_FALLBACK)));
  assert.ok(html.includes('<div class="k">Boat</div>\n<div class="v">H099</div>'));
  // Unknown status -> no status ROW at all (ferryStatusText returned null, and the grid drops a
  // row with no value). Asserted on the label as well as on the words, because a row printed with
  // an empty value would still say "Status" to a rider.
  assert.ok(!html.includes("At dock") && !html.includes("Under way"));
  assert.ok(!html.includes(">Status<"), "a boat with no status says nothing about its status");
});

test("ferryBoatPopupHtml escapes hostile route name and label", () => {
  const html = ferryBoatPopupHtml({ label: "H<b>1", status: null }, "East<script>River", "#000");
  assert.ok(html.includes("East&lt;script&gt;River") && !html.includes("<script>"));
  assert.ok(html.includes("H&lt;b&gt;1") && !html.includes("H<b>1"));
});

test("ferryArrivalsHtml buckets by route name with arriving/departing countdowns", () => {
  const station = { id: "18", name: "Wall St/Pier 11", wheelchair: true };
  const body = {
    routes: {
      "South Brooklyn": [{ route_id: "SB", arrival: 30, departure: 180 }], // dwelling -> departs
      "East River": [{ route_id: "ER", arrival: 120, departure: 200 }], // arriving
    },
  };
  const colorFor = (id) => ({ ER: "#00839c", SB: "#ffd100" })[id];
  const html = ferryArrivalsHtml(station, body, 40, colorFor);
  assert.ok(html.includes("Wall St/Pier 11"));
  assert.ok(html.includes("NYC Ferry"));
  assert.ok(html.includes("&#9855;")); // wheelchair accessibility marker
  assert.ok(html.indexOf("East River") < html.indexOf("South Brooklyn")); // alphabetical
  // Route-coloured headings, each darkened to clear AA on the popup. #ffd100 is the sharper
  // case: bright yellow measures 1.51 on white, which is not text. MR5: the popup is --surface
  // rather than white, so the background the walk targets is the token's own value.
  assert.ok(
    html.includes(readableInk("#00839c", POPUP_SURFACE_FALLBACK)) &&
      html.includes(readableInk("#ffd100", POPUP_SURFACE_FALLBACK)),
  );
  for (const raw of ["#00839c", "#ffd100"]) {
    assert.ok(
      contrastRatio(readableInk(raw, POPUP_SURFACE_FALLBACK), POPUP_SURFACE_FALLBACK) >= 4.5,
      `${raw} heading ink is below AA on the popup`,
    );
  }
  assert.ok(html.includes("1 min")); // East River arriving in (120-40)=80s -> "1 min"
  assert.ok(html.includes("departs 2 min")); // South Brooklyn dwelling, departs in (180-40)=140s
});

test("ferryArrivalsHtml omits the accessibility marker when not accessible and renders No boats", () => {
  const noAccess = ferryArrivalsHtml(
    { id: "2", name: "South Williamsburg", wheelchair: false },
    { routes: { "East River": [{ route_id: "ER", arrival: 90, departure: 150 }] } },
    30,
  );
  assert.ok(!noAccess.includes("&#9855;"));
  const empty = ferryArrivalsHtml({ id: "18", name: "Wall St/Pier 11", wheelchair: true }, { routes: {} }, 0);
  assert.ok(empty.includes("Wall St/Pier 11"));
  assert.ok(empty.includes("arr-none") && empty.includes("No boats"));
});

test("ferryArrivalsHtml escapes a hostile route-bucket name and station name", () => {
  const html = ferryArrivalsHtml(
    { id: "18", name: "Pier<script>11" },
    { routes: { "East<b>River": [{ route_id: "ER", arrival: 90, departure: null }] } },
    30,
  );
  assert.ok(html.includes("Pier&lt;script&gt;11") && !html.includes("Pier<script>11"));
  assert.ok(html.includes("East&lt;b&gt;River") && !html.includes("East<b>River"));
});

// ---- Static-loader retry helper (phase 12d) ----

const { retryUntil } = require("./helpers.js");

// Instant injected sleep that records every wait it was asked for, so the exact
// backoff sequence is assertable without real timers.
function instantSleep() {
  const waits = [];
  const sleep = (ms) => {
    waits.push(ms);
    return Promise.resolve();
  };
  return { waits, sleep };
}

test("retryUntil resolves after a first-try success without sleeping", async () => {
  const { waits, sleep } = instantSleep();
  let calls = 0;
  await retryUntil(async () => {
    calls += 1;
    return true;
  }, { baseMs: 1000, capMs: 30000, sleep });
  assert.equal(calls, 1);
  assert.deepEqual(waits, []); // success on attempt one never schedules a wait
});

test("retryUntil doubles the backoff from baseMs and caps at capMs", async () => {
  const { waits, sleep } = instantSleep();
  let calls = 0;
  await retryUntil(async () => {
    calls += 1;
    return calls === 8; // fail 7 times, succeed on the 8th
  }, { baseMs: 1000, capMs: 30000, sleep });
  assert.equal(calls, 8);
  // 7 failures = 7 waits: doubling from 1000, capped at 30000 (32000 never appears).
  assert.deepEqual(waits, [1000, 2000, 4000, 8000, 16000, 30000, 30000]);
});

test("retryUntil treats a thrown error as falsy and keeps retrying", async () => {
  const { waits, sleep } = instantSleep();
  let calls = 0;
  await retryUntil(async () => {
    calls += 1;
    if (calls < 3) throw new Error("network down");
    return true;
  }, { baseMs: 500, capMs: 30000, sleep });
  assert.equal(calls, 3);
  assert.deepEqual(waits, [500, 1000]);
});

test("retryUntil with a loader-shaped fn: false on empty payload, true on populated", async () => {
  // Mimics the static loaders: an empty array is the backend's failed-warmup []
  // (not success), a populated one ends the loop.
  const payloads = [[], [], [{ id: "127" }]];
  const populated = [];
  const { waits, sleep } = instantSleep();
  await retryUntil(async () => {
    const data = payloads.shift();
    if (!data.length) return false;
    populated.push(...data);
    return true;
  }, { baseMs: 1000, capMs: 30000, sleep });
  assert.deepEqual(populated, [{ id: "127" }]); // populated exactly once, no double-add
  assert.deepEqual(waits, [1000, 2000]);
});

/* ---------------- Per-system freshness (C2) ---------------- */

test("C2 ingestSystems reads an aggregate block and synthesizes one for a single feed", () => {
  // The aggregate shape: one entry per subsystem, carried through verbatim.
  const aggregate = ingestSystems(
    {
      fetched_at: 1000,
      systems: {
        LIRR: { fetched_at: 1000, ok: true, retained_since: null },
        MNR: { fetched_at: 640, ok: false, retained_since: 700 },
      },
    },
    "railroads",
  );
  assert.deepEqual(Object.keys(aggregate).sort(), ["LIRR", "MNR"]);
  assert.equal(aggregate.MNR.fetchedAt, 640);
  assert.equal(aggregate.MNR.ok, false);
  assert.equal(aggregate.MNR.retainedSince, 700);

  // The single-feed shape (buses, PATH, ferry): no block, so ONE system named after
  // the source stands in, carrying the envelope's own fetched_at. Naming every system
  // of a source is just naming the source, so the status line words a single-feed
  // source exactly as it did pre-C2 (pinned by the healthy/all-stale test below).
  const single = ingestSystems({ fetched_at: 1000 }, "path");
  assert.deepEqual(Object.keys(single), ["path"]);
  assert.equal(single.path.fetchedAt, 1000);
  assert.equal(single.path.ok, true);
});

test("C2 ingestSystems tolerates malformed blocks without dimming the whole map", () => {
  // A block entry with no numeric fetched_at: unknown age (null), NOT stale. The
  // system is still reported through `ok`.
  const missing = ingestSystems(
    { fetched_at: 1000, systems: { SIR: { ok: false, retained_since: null } } },
    "subways",
  );
  assert.equal(missing.SIR.fetchedAt, null);
  assert.equal(missing.SIR.ok, false);
  // A missing `ok` reads as healthy: a malformed field must not dim everything.
  const noOk = ingestSystems({ fetched_at: 1000, systems: { G: { fetched_at: 1000 } } }, "subways");
  assert.equal(noOk.G.ok, true);
  // A non-numeric fetched_at (a string from a bad serializer) is treated as absent.
  const junk = ingestSystems({ fetched_at: 1000, systems: { L: { fetched_at: "1000" } } }, "s");
  assert.equal(junk.L.fetchedAt, null);
  // An EMPTY systems object falls back to the synthesized single system rather than
  // leaving the source with no freshness at all.
  const empty = ingestSystems({ fetched_at: 1000, systems: {} }, "subways");
  assert.deepEqual(Object.keys(empty), ["subways"]);
  assert.equal(empty.subways.fetchedAt, 1000);
  // So do a null block and a missing body.
  assert.deepEqual(Object.keys(ingestSystems({ fetched_at: 1000, systems: null }, "x")), ["x"]);
  assert.equal(ingestSystems(null, "x").x.fetchedAt, null);
  // routes is null unless the payload actually carries an array (see the coverage
  // fail-safe in subwaySystemAge).
  assert.equal(empty.subways.routes, null);
  assert.deepEqual(
    ingestSystems({ systems: { ACE: { fetched_at: 1, routes: ["A", "C"] } } }, "subways").ACE.routes,
    ["A", "C"],
  );
});

test("C2 systemAges ages each system separately; a block with no clock of its own takes the envelope's lag", () => {
  const now = 20_000;
  const source = {
    label: "railroad",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems: ingestSystems(
      {
        fetched_at: now,
        systems: {
          LIRR: { fetched_at: now, ok: true, retained_since: null },
          MNR: { fetched_at: now - 400, ok: false, retained_since: now - 400 },
          // Never decoded since boot: no age to compute.
          FUTURE: { fetched_at: null, ok: false, retained_since: null },
        },
      },
      "railroads",
    ),
  };
  const ages = systemAges(source, now);
  // These blocks predate the per-system content clock (6.1), so each system falls back
  // to the envelope's lag, which is how every pre-6.2 payload read and still reads.
  assert.equal(ages.LIRR, 5);
  assert.equal(ages.MNR, 400); // its own poll age, which the envelope's hides
  assert.equal(ages.FUTURE, null);
});

/* ---------------- 6.2: the door, and the lag term per system ---------------- */

test("6.2 the door reads each block's content clock, and nothing it was not asked for", () => {
  // THE DOOR IN THE LITERAL SENSE: a field the contract adds to a block reaches no
  // surface until this function reads it, which is how 6.1's per-system content clock
  // reached none. Pinned as an exact key set, so a seventh name cannot slip in unread and
  // the sixth cannot slip out. 6.3 added the sixth, `positions`, the ladder's counts the
  // status line reads (null on every one of these blocks, which carry none).
  const systems = ingestSystems(
    {
      fetched_at: 1000,
      feed_timestamp: 400,
      systems: {
        ACE: { fetched_at: 1000, feed_timestamp: 400, ok: true, retained_since: null, routes: ["A"], detail: "x" },
        G: { fetched_at: 1000, feed_timestamp: 995, ok: true, retained_since: null, routes: ["G"] },
        MNR: { fetched_at: 1000, feed_timestamp: null, ok: true, retained_since: null },
        OLD: { fetched_at: 1000, ok: true },
        BAD: { fetched_at: 1000, feed_timestamp: "995" },
      },
    },
    "subways",
  );
  for (const [name, system] of Object.entries(systems)) {
    assert.deepEqual(
      Object.keys(system).sort(),
      ["feedTimestamp", "fetchedAt", "ok", "positions", "retainedSince", "routes"],
      name,
    );
  }
  // The three states: a number, NULL as the backend's real answer (no content clock),
  // and UNDEFINED for a block that predates the clock or carries garbage in it.
  assert.equal(systems.ACE.feedTimestamp, 400);
  assert.equal(systems.G.feedTimestamp, 995);
  assert.equal(systems.MNR.feedTimestamp, null);
  assert.equal(systems.OLD.feedTimestamp, undefined);
  assert.equal(systems.BAD.feedTimestamp, undefined);
  assert.equal(contentClock(Number.NaN), undefined);
  // A synthesized single system carries the ENVELOPE's content clock, in all three states.
  assert.equal(ingestSystems({ fetched_at: 1000, feed_timestamp: 700 }, "path").path.feedTimestamp, 700);
  assert.equal(ingestSystems({ fetched_at: 1000, feed_timestamp: null }, "path").path.feedTimestamp, null);
  assert.equal(ingestSystems({ fetched_at: 1000 }, "path").path.feedTimestamp, undefined);
});

test("6.2 ingestEnvelope reads the envelope's three clocks through the same door", () => {
  const feed = ingestEnvelope(
    {
      fetched_at: 1000,
      feed_timestamp: 990,
      served_at: 1003,
      systems: { njt: { fetched_at: 1000, feed_timestamp: 990, ok: true } },
      trains: [{ id: "x" }],
    },
    "njt",
  );
  assert.deepEqual(Object.keys(feed).sort(), ["feedTimestamp", "fetchedAt", "servedAt", "systems"]);
  assert.equal(feed.fetchedAt, 1000);
  assert.equal(feed.feedTimestamp, 990);
  assert.equal(feed.servedAt, 1003);
  assert.equal(feed.systems.njt.feedTimestamp, 990);
  // An ARRIVALS envelope enters the same way. PATH's board has no systems block, so its
  // one system is synthesized under the key it is ingested with, carrying the board's
  // content clock (the oldest served row's, per cache._oldest_row_observed_at).
  const board = ingestEnvelope(
    { fetched_at: 1000, feed_timestamp: 962, served_at: 1001, systems: null, directions: {} },
    "path",
  );
  assert.deepEqual(Object.keys(board.systems), ["path"]);
  assert.equal(board.systems.path.feedTimestamp, 962);
  assert.equal(board.servedAt, 1001);
  // A clock that is not a finite number is no clock at all, and a missing body is empty.
  const junk = ingestEnvelope({ fetched_at: "1000", feed_timestamp: Number.NaN, served_at: null }, "x");
  assert.equal(junk.fetchedAt, null);
  assert.equal(junk.feedTimestamp, null);
  assert.equal(junk.servedAt, null);
  assert.equal(ingestEnvelope(null, "x").servedAt, null);
});

test("6.2 one lagging group ages alone, because the lag term is each system's own", () => {
  // F03's second clause at the door. The envelope's feed_timestamp is the minimum
  // header over the groups that decoded, so the old SHARED lag term handed the lagging
  // group's 600 seconds to every group: all eight dimmed, all eight froze, and the
  // status line spoke for the whole source while seven feeds were current.
  const now = 20_000;
  const source = {
    label: "trains",
    systemNoun: "group",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 600,
    systems: ingestSystems(
      {
        fetched_at: now,
        feed_timestamp: now - 600,
        systems: {
          "1-7+S": { fetched_at: now, feed_timestamp: now - 600, ok: true },
          ACE: { fetched_at: now, feed_timestamp: now - 5, ok: true },
        },
      },
      "subways",
    ),
  };
  assert.equal(systemLag(source, source.systems["1-7+S"]), 600);
  assert.equal(systemLag(source, source.systems.ACE), 5);
  assert.deepEqual(systemAges(source, now), { "1-7+S": 600, ACE: 5 });
  // The glide deadline agrees about which one is old: frozen at the observation, while
  // the current group keeps gliding until its own poll ages.
  const at = systemStaleAts(source);
  assert.equal(at["1-7+S"], now);
  assert.equal(at.ACE, now + FEED_STALE_AFTER_S);
  assert.equal(staleness(source, now), "trains: 1-7+S group as of 10m ago");
});

test("6.2 a system with NO content clock borrows nobody's: Metro-North beside a lagging LIRR", () => {
  // The railroad envelope's feed_timestamp is LIRR's header alone (Metro-North's is a
  // lagging copy and never published), so under the shared term a lagging LIRR aged
  // Metro-North too. Metro-North's block says null, which means no content clock: its
  // age is its poll age, and it stays out of a sentence about LIRR's content.
  const now = 20_000;
  const source = {
    label: "railroad",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 300,
    systems: ingestSystems(
      {
        fetched_at: now,
        feed_timestamp: now - 300,
        systems: {
          LIRR: { fetched_at: now, feed_timestamp: now - 300, ok: true },
          MNR: { fetched_at: now, feed_timestamp: null, ok: true },
        },
      },
      "railroads",
    ),
  };
  assert.deepEqual(systemAges(source, now), { LIRR: 300, MNR: 0 });
  // Metro-North's clause (6.3) rides the line the stale LIRR raised; it never raises one.
  assert.equal(staleness(source, now), "railroad: LIRR as of 5m ago; MNR position age unavailable");
  // The same payload from a backend that predates the per-system clock reads exactly as
  // it did before 6.2: the envelope's lag, for both.
  const older = {
    ...source,
    systems: ingestSystems(
      { fetched_at: now, systems: { LIRR: { fetched_at: now, ok: true }, MNR: { fetched_at: now, ok: true } } },
      "railroads",
    ),
  };
  assert.deepEqual(systemAges(older, now), { LIRR: 300, MNR: 300 });
  assert.equal(staleness(older, now), "railroad: as of 5m ago; MNR position age unavailable");
});

test("6.2 staleness' THIRD population: content old while the poll is fresh, in a clause of its own", () => {
  const now = 20_000;
  const subways = (systems) => ({
    label: "trains",
    systemNoun: "group",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 600,
    systems: ingestSystems({ fetched_at: now, feed_timestamp: now - 600, systems }, "subways"),
  });
  const fresh = { fetched_at: now, feed_timestamp: now - 5, ok: true };
  // All three populations at once, three clauses, each with ITS OWN age: BDFM's poll
  // stopped five minutes ago; ACE's poll is current and its content ten minutes old;
  // SIR has never decoded. Merged, ACE's ten minutes would be announced as BDFM's, or
  // BDFM's five as ACE's, which is the defect that split stale from blind.
  assert.equal(
    staleness(
      subways({
        "1-7+S": fresh,
        ACE: { fetched_at: now, feed_timestamp: now - 600, ok: true },
        BDFM: { fetched_at: now - 300, feed_timestamp: now - 305, ok: false, retained_since: now - 300 },
        SIR: { fetched_at: null, ok: false },
      }),
      now,
    ),
    "trains: BDFM group as of 5m ago; ACE group as of 10m ago; SIR group not reporting",
  );
  // The third alone, over a subset of the source: named, with its content age.
  assert.equal(
    staleness(subways({ "1-7+S": fresh, ACE: { fetched_at: now, feed_timestamp: now - 600, ok: true } }), now),
    "trains: ACE group as of 10m ago",
  );
  // The third alone over the WHOLE source reads as a single-feed source always has.
  assert.equal(
    staleness({ label: "PATH", fetchedAt: now, servedAt: now, feedTimestamp: now - 300 }, now),
    "PATH: as of 5m ago",
  );
  // THE COMMON CASE DID NOT GET NOISIER: a healthy day is null, and a system with no
  // content clock (Metro-North) is never content-old, however old LIRR's is not.
  assert.equal(staleness(subways({ "1-7+S": fresh, ACE: fresh }), now), null);
  assert.equal(
    staleness(
      {
        label: "railroad",
        fetchedAt: now,
        servedAt: now,
        feedTimestamp: now - 5,
        systems: ingestSystems(
          {
            fetched_at: now,
            systems: {
              LIRR: { fetched_at: now, feed_timestamp: now - 5, ok: true },
              MNR: { fetched_at: now, feed_timestamp: null, ok: true },
            },
          },
          "railroads",
        ),
      },
      now,
    ),
    null,
  );
});

test("C2 the healthy aggregate case reads EXACTLY as the pre-C2 whole-source case", () => {
  // On a healthy poll every system's fetched_at equals the envelope's, so the worst
  // per-system age is the age R1 computed. This is the assertion that pins "the
  // common case does not get noisier".
  const now = 20_000;
  const healthy = (systems) => ({
    label: "trains",
    systemNoun: "group",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems,
  });
  const block = {
    fetched_at: now,
    systems: { ACE: { fetched_at: now, ok: true }, G: { fetched_at: now, ok: true } },
  };
  assert.equal(staleness(healthy(ingestSystems(block, "subways")), now), null);
  // And a source whose systems are ALL stale words it exactly as before: no names,
  // because naming every system is just naming the source.
  const stuck = { fetched_at: now - 200, systems: { ACE: { fetched_at: now - 200, ok: true }, G: { fetched_at: now - 200, ok: true } } };
  const source = { ...healthy(ingestSystems(stuck, "subways")), fetchedAt: now - 200, feedTimestamp: now - 205 };
  assert.equal(staleness(source, now), "trains: as of 3m ago");
});

test("C2 staleness names a DEGRADED subsystem while the healthy ones stay quiet", () => {
  const now = 20_000;
  const railroads = {
    label: "railroad",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems: ingestSystems(
      {
        fetched_at: now,
        systems: {
          LIRR: { fetched_at: now, ok: true, retained_since: null },
          MNR: { fetched_at: now - 360, ok: false, retained_since: now - 360 },
        },
      },
      "railroads",
    ),
  };
  // The spec's example: MNR named, LIRR silent, the age MNR's own.
  // 6.3: Metro-North's own clause rides the line its outage raised, and never raises one.
  assert.equal(staleness(railroads, now), "railroad: MNR as of 6m ago; MNR position age unavailable");
  // The subway's systems are feed GROUPS, so systemNoun makes the phrase read right.
  const subways = {
    label: "trains",
    systemNoun: "group",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems: ingestSystems(
      {
        fetched_at: now,
        systems: {
          ACE: { fetched_at: now - 240, ok: false, retained_since: now - 240 },
          G: { fetched_at: now, ok: true, retained_since: null },
        },
      },
      "subways",
    ),
  };
  assert.equal(staleness(subways, now), "trains: ACE group as of 4m ago");
});

test("C2 staleness stays silent for a system that merely failed its LAST poll", () => {
  // A single failed poll is routine (a feed hiccups, the next poll recovers). Naming
  // it immediately would make the status line chatter constantly, so a degraded
  // system is named only once its age crosses the threshold. 30s < 90s: silent.
  const now = 20_000;
  const source = {
    label: "railroad",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems: ingestSystems(
      {
        fetched_at: now,
        systems: {
          LIRR: { fetched_at: now, ok: true },
          MNR: { fetched_at: now - 30, ok: false, retained_since: now - 30 },
        },
      },
      "railroads",
    ),
  };
  assert.equal(staleness(source, now), null);
});

test("C2 staleness reports a system that has NEVER decoded, which has no age", () => {
  const now = 20_000;
  const source = {
    label: "trains",
    systemNoun: "group",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems: ingestSystems(
      {
        fetched_at: now,
        systems: {
          ACE: { fetched_at: now, ok: true },
          // Down since boot: nothing to age against, so it cannot be "as of Xm ago".
          SIR: { fetched_at: null, ok: false },
        },
      },
      "subways",
    ),
  };
  assert.equal(staleness(source, now), "trains: SIR group not reporting");
});

test("C2 the empty-success rule survives: a healthy system with no data is not stale", () => {
  // A subway group that decoded and had NO trains running (a real overnight state)
  // reports ok with a current fetched_at, so its age is ~0, nothing dims and nothing
  // freezes. Absence renders as absence, never as retained-stale (the ferry
  // precedent, inverted).
  //
  // SCOPE, stated because an earlier version of this test implied more: the rule that
  // an empty HEALTHY group must not be retained is enforced in the backend merge and
  // owned by test_c2_a_healthy_but_EMPTY_group_replaces_rather_than_retains. The
  // client half is that an empty coverage list is still coverage, which is a
  // noteSubwaySystems behavior and is pinned by the "C2c2" e2e spec; nothing here
  // reads `routes`, so a fixture field for it would be decoration.
  const now = 20_000;
  const source = {
    label: "trains",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now,
    systems: ingestSystems(
      { fetched_at: now, systems: { SIR: { fetched_at: now, ok: true } } },
      "subways",
    ),
  };
  assert.equal(staleness(source, now), null);
  assert.equal(staleAge(systemAges(source, now).SIR), false);
  assert.equal(glideClock(now, systemStaleAts(source).SIR), now); // still gliding
});

test("C2 staleAge and markerOpacity share the threshold boundary", () => {
  assert.equal(staleAge(null), false); // unknown age is not stale
  assert.equal(staleAge(FEED_STALE_AFTER_S - 0.001), false);
  assert.equal(staleAge(FEED_STALE_AFTER_S), true); // >= , matching staleness()
  assert.equal(markerOpacity(0), 1);
  assert.equal(markerOpacity(null), 1);
  assert.equal(markerOpacity(FEED_STALE_AFTER_S), STALE_MARKER_OPACITY);
  assert.ok(STALE_MARKER_OPACITY > 0 && STALE_MARKER_OPACITY < 1); // dim, not invisible
});

test("C2 markerOpacity COMPOUNDS staleness with a marker's own resting opacity", () => {
  // The ferry layer's docked dimming used to be a css class, which an inline opacity
  // written for staleness would have overridden: every docked boat would have been
  // silently un-dimmed the moment C2 started setting opacities. It is now a base that
  // multiplies, so a docked boat on a stale feed is dimmed for BOTH reasons.
  assert.equal(markerOpacity(null, FERRY_DOCKED_OPACITY), FERRY_DOCKED_OPACITY);
  assert.equal(markerOpacity(0, FERRY_DOCKED_OPACITY), FERRY_DOCKED_OPACITY);
  assert.equal(
    markerOpacity(FEED_STALE_AFTER_S, FERRY_DOCKED_OPACITY),
    FERRY_DOCKED_OPACITY * STALE_MARKER_OPACITY,
  );
  // A base of 1 (every other layer) leaves the rule exactly as it reads without one.
  assert.equal(markerOpacity(FEED_STALE_AFTER_S, 1), markerOpacity(FEED_STALE_AFTER_S));
});

test("C2 systemStaleAts gives each system the instant its glide must stop", () => {
  const now = 20_000;
  const source = (extra, systems) => ({
    label: "trains",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    ...extra,
    systems: ingestSystems({ fetched_at: now, systems }, "subways"),
  });
  // The ordinary case: this system may be interpolated until its own poll age
  // reaches the threshold.
  let at = systemStaleAts(
    source({}, { ACE: { fetched_at: now - 30, ok: true }, G: { fetched_at: now, ok: true } }),
  );
  assert.equal(at.ACE, now - 30 + FEED_STALE_AFTER_S);
  assert.equal(at.G, now + FEED_STALE_AFTER_S);

  // RETAINED data stops being predictable the moment retention starts, before the
  // age threshold: the anchors behind the interpolation are known dead from then.
  at = systemStaleAts(
    source({}, { ACE: { fetched_at: now - 30, ok: false, retained_since: now - 10 } }),
  );
  assert.equal(at.ACE, now - 10);

  // UPSTREAM CONTENT ALREADY PAST THE THRESHOLD when it was polled: nothing may
  // advance past the observation itself. This is the case the age-based freeze got
  // wrong, because the lag term does not grow between polls.
  at = systemStaleAts(
    source({ feedTimestamp: now - 300 }, { ACE: { fetched_at: now, ok: true } }),
  );
  assert.equal(at.ACE, now);

  // Never decoded: no anchor, so no deadline.
  at = systemStaleAts(source({}, { SIR: { fetched_at: null, ok: false } }));
  assert.equal(at.SIR, null);
});

test("C2 glideClock passes a fresh system through and PINS a stale one for good", () => {
  const now = 20_000;
  // No deadline, or one still ahead: the live clock, untouched, so normal gliding is
  // bit-for-bit unchanged.
  assert.equal(glideClock(now, null), now);
  assert.equal(glideClock(now, now + 10), now);
  // Past the deadline: pinned AT it.
  assert.equal(glideClock(now, now - 50), now - 50);
  // AND IT STAYS PINNED, however far the clock runs. REVIEW FIX: the old signature
  // took the AGE and subtracted (age - threshold), which only held still while the
  // age grew with the clock. It does not when the upstream-lag term dominates, and
  // markers dead-reckoned at full speed while dimmed. An absolute instant cannot
  // drift, which is why this test can advance `now` alone.
  const deadline = now - 50;
  assert.equal(glideClock(now + 10, deadline), deadline);
  assert.equal(glideClock(now + 600, deadline), deadline);
  assert.equal(glideClock(now + 86_400, deadline), deadline);
});

test("C2 a lag-stale source freezes too: the regression the age-based freeze had", () => {
  // The exact shape the review reproduced. The backend keeps polling successfully
  // (poll age ~0) but the upstream header is 300s behind, so the source is stale on
  // the lag term alone and `age` is a CONSTANT across the poll interval. The old
  // `now - (age - threshold)` therefore advanced 1:1 with the clock: a full-speed
  // glide, permanently backdated. The deadline is now the observation itself.
  const now = 20_000;
  const source = {
    label: "trains",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 300,
    systems: ingestSystems({ fetched_at: now, systems: { ACE: { fetched_at: now, ok: true } } }, "subways"),
  };
  assert.equal(systemAges(source, now).ACE, 300); // stale, and constant with `now`
  assert.equal(systemAges(source, now + 30).ACE, 300);
  const at = systemStaleAts(source).ACE;
  assert.equal(glideClock(now, at), now);
  assert.equal(glideClock(now + 30, at), now); // frozen, not creeping
  assert.equal(glideClock(now + 300, at), now);
});

test("C2 / MR5 Q2: a board's age line and a vehicle's footer are two surfaces, and the difference is pinned", () => {
  /* THIS TEST CHANGED SHAPE BECAUSE THE APP DID. It used to assert that stalePopupLine and
     boardLineHtml produced IDENTICAL markup, "so a stale board and a stale train cannot be worded or
     styled apart". stalePopupLine is gone: vehicleStaleLine was its only caller and ruling Q2's
     footer took that job. So the claim it made is no longer true, and pretending otherwise by
     deleting the test would hide a rider-visible divergence rather than record it.

     WHAT A RIDER SEES NOW. A station board's system line is 6.2's and is untouched: lowercase "as of
     4m ago" in a .popup-stale div. A vehicle popup's footer says the FEED STRIP's words for the same
     age, "As of 4m ago", because the ruling is that the footer says it the strip's way so that the
     state is said one way on BOTH of those surfaces. The two are different facts from different
     sources (a board's arrivals against a feed's poll), which is the argument for letting them read
     differently; that they differ only in a capital letter is the argument against. Both forms are
     pinned here so whichever way a later stage resolves it, it does so deliberately. */
  assert.equal(boardLineHtml("as of 4m ago"), '<div class="popup-stale">as of 4m ago</div>\n');
  assert.equal(boardLineHtml(null), "");
  assert.equal(feedStateWords({ state: "stale", age: 240 }), "As of 4m ago");
  // The divergence, stated as an assertion so it cannot close silently either:
  assert.notEqual(feedStateWords({ state: "stale", age: 240 }), "as of 4m ago");
});

test("C2 alertsFreshnessBasis is the WORST system's fetched_at (the F1 partial case)", () => {
  // The F1 finding: four systems advancing and one frozen is a SUCCESSFUL poll, so
  // the envelope's fetched_at kept advancing and the marker could never fire. The
  // oldest system is the honest basis.
  const body = {
    fetched_at: 5000,
    systems: {
      subway: { fetched_at: 5000, ok: true, retained_since: null },
      bus: { fetched_at: 5000, ok: true, retained_since: null },
      LIRR: { fetched_at: 5000, ok: true, retained_since: null },
      MNR: { fetched_at: 5000, ok: true, retained_since: null },
      ferry: { fetched_at: 4600, ok: false, retained_since: 4600 },
    },
  };
  assert.equal(alertsFreshnessBasis(body), 4600);
  // Which is what makes the marker fire: 5000 would have read fresh at now = 4900.
  assert.equal(alertsStale(alertsFreshnessBasis(body), 4600 + ALERTS_STALE_AFTER_S), true);
  assert.equal(alertsStale(body.fetched_at, 4600 + ALERTS_STALE_AFTER_S), false);
  // A system that has never decoded returns null, which ages against the client's
  // first-attempt time instead: its alerts are missing rather than merely old.
  assert.equal(
    alertsFreshnessBasis({ fetched_at: 5000, systems: { subway: { fetched_at: null } } }),
    null,
  );
  // No systems block at all: the envelope's fetched_at, exactly as C1 left it.
  assert.equal(alertsFreshnessBasis({ fetched_at: 5000 }), 5000);
  assert.equal(alertsFreshnessBasis({ fetched_at: 5000, systems: {} }), 5000);
  assert.equal(alertsFreshnessBasis({ served_at: 5000 }), null); // never served_at
});

/* ---------------- A1: the accessible station surface ---------------- */

test("countdownParts is the one rounding decision, and both wordings agree with it", () => {
  // The extraction's whole purpose: the visual and spoken labels must never
  // disagree about which minute it is. Same input, same tier, every time.
  for (const seconds of [null, 0, 29, 30, 59, 60, 90, 5940, 6000, 36000]) {
    const parts = countdownParts(seconds);
    const visual = formatCountdown(seconds);
    const spoken = spokenCountdown(seconds);
    assert.equal(visual === "", parts.kind === "blank");
    assert.equal(spoken === "", parts.kind === "blank");
    assert.equal(visual === "now", parts.kind === "now");
    assert.equal(spoken === "now", parts.kind === "now");
  }
  // And the wordings themselves, pinned so a refactor cannot quietly reword them.
  assert.equal(formatCountdown(240), "4 min");
  assert.equal(spokenCountdown(240), "in 4 minutes");
  assert.equal(spokenCountdown(60), "in 1 minute"); // singular, not "1 minutes"
  assert.equal(spokenCountdown(6000), "in 1 hour 40 minutes");
  // 60 minutes stays MINUTES, not "1 hour", because the hours tier begins at 100
  // minutes and that boundary is shared with the visual label on purpose: the two
  // wordings agreeing matters more than either one reading ideally on its own.
  assert.equal(formatCountdown(3600), "60 min");
  assert.equal(spokenCountdown(3600), "in 60 minutes");
  assert.equal(spokenCountdown(7200), "in 2 hours"); // no trailing "0 minutes"
  assert.equal(spokenCountdown(10), "now");
  assert.equal(spokenCountdown(null), "");
});

test("station search folds case and diacritics, and tokenizes", () => {
  assert.equal(foldStationName("Grand Céntral"), "grand central");
  assert.equal(foldStationName("HOBOKEN"), "hoboken");
  assert.equal(foldStationName(null), "");
  assert.deepEqual(stationQueryTokens("  grand   CEN "), ["grand", "cen"]);
  assert.deepEqual(stationQueryTokens("   "), []);

  const stops = [
    { id: "1", name: "Grand Central", systemLabel: "Subway" },
    { id: "2", name: "East Grand Street", systemLabel: "Subway" },
    { id: "3", name: "Grand Army Plaza", systemLabel: "Subway" },
    { id: "4", name: "Astoria", systemLabel: "Ferry" },
  ];
  // Tokenized, so a partial second word still finds it: the spec's own example.
  assert.deepEqual(
    searchStations(stops, "grand cen").rows.map((r) => r.name),
    ["Grand Central"],
  );
  // Diacritic-insensitive in BOTH directions: unaccented query, accented name.
  assert.deepEqual(
    searchStations([{ id: "5", name: "Céntral Park" }], "central").rows.map((r) => r.name),
    ["Céntral Park"],
  );
  // Prefix matches sort above interior ones, then alphabetically.
  assert.deepEqual(
    searchStations(stops, "grand").rows.map((r) => r.name),
    ["Grand Army Plaza", "Grand Central", "East Grand Street"],
  );
  // An empty query is a PROMPT, not 900 rows: the caller shows a hint instead.
  const empty = searchStations(stops, "   ");
  assert.equal(empty.prompt, true);
  assert.deepEqual(empty.rows, []);
  // No match is not a prompt: it is an honest zero.
  const none = searchStations(stops, "zzz");
  assert.equal(none.prompt, false);
  assert.equal(none.total, 0);
});

test("station search caps results and reports how many it withheld", () => {
  const many = Array.from({ length: 120 }, (_, i) => ({
    id: String(i),
    // Zero-padded so the alphabetical tiebreak is deterministic.
    name: `Grand ${String(i).padStart(3, "0")}`,
  }));
  const capped = searchStations(many, "grand");
  assert.equal(capped.rows.length, STATION_RESULT_CAP);
  assert.equal(capped.total, 120);
  assert.equal(capped.hidden, 120 - STATION_RESULT_CAP);
  assert.equal(stationOverflowLine(capped.hidden), "70 more stations match; keep typing to narrow");
  // Singular, and silent when nothing was withheld.
  assert.equal(stationOverflowLine(1), "1 more station match; keep typing to narrow");
  assert.equal(stationOverflowLine(0), "");
  // An explicit cap is honored, so a caller (or a test) can shrink it.
  assert.equal(searchStations(many, "grand", 3).rows.length, 3);
  assert.equal(searchStations(many, "grand", 3).hidden, 117);
});

test("shapeStationArrivals buckets each system the way its popup already does", () => {
  const now = 1_700_000_000;
  // Subway: compass order, and a bucket with no trains is not fabricated.
  const subway = shapeStationArrivals(
    "subway",
    { fetched_at: now - 3, directions: { Southbound: [{ route_id: "3", arrival: now + 60 }] } },
    now,
  );
  assert.deepEqual(subway.buckets.map((b) => b.name), ["Southbound"]);
  // No system line on a board with rows: rows speak for their own age (6.2).
  assert.equal(subway.systemLine, null);
  // Railroad: Inbound first, train_num carried, route name resolved by the caller.
  const rail = shapeStationArrivals(
    "railroad",
    {
      fetched_at: now,
      directions: {
        Outbound: [{ route_id: "6", arrival: now + 900 }],
        Inbound: [{ route_id: "5", train_num: "8412", arrival: now + 240 }],
      },
    },
    now,
    { nameFor: (r) => (r === "5" ? "Babylon" : null) },
  );
  assert.deepEqual(rail.buckets.map((b) => b.name), ["Inbound", "Outbound"]);
  assert.equal(rail.buckets[0].rows[0].routeName, "Babylon");
  assert.equal(rail.buckets[0].rows[0].trainNum, "8412");
  assert.equal(rail.buckets[0].rows[0].seconds, 240);
  assert.equal(rail.buckets[0].rows[0].at, now + 240);
  // Ferry: buckets are route names, and a dwelling boat counts to its DEPARTURE.
  const ferry = shapeStationArrivals(
    "ferry",
    { fetched_at: now, served_at: now, routes: { Astoria: [dated({ route_id: "AS", arrival: now - 30, departure: now + 360 }, now - 5)] } },
    now,
  );
  assert.deepEqual(ferry.buckets.map((b) => b.name), ["Astoria"]);
  assert.equal(ferry.buckets[0].rows[0].mode, "departing");
  assert.equal(ferry.buckets[0].rows[0].seconds, 360);
  // An empty board with no poll time has no age to state, so it states none rather
  // than inventing one; with a poll time past the threshold it says how old (6.2).
  assert.equal(shapeStationArrivals("subway", { directions: {} }, now).systemLine, null);
  assert.equal(
    shapeStationArrivals("subway", { fetched_at: now - 200, served_at: now - 200, directions: {} }, now).systemLine,
    "as of 3m ago",
  );
});

/* ---------------- 6.2: each board row qualified by its own age ---------------- */

// A board as the backend serves it since 6.1: served_at on the envelope, the contract
// pair on the row. `at` is the row's observed_at.
const dated = (row, at, provenance = "reported") => ({ ...row, observed_at: at, provenance });

test("6.2 arrivalQualifier speaks section 3.2's vocabulary, exactly, row by row", () => {
  const now = 50_000;
  const gated = { now, servedAt: now, pollAge: 5, gated: true, system: "subway" };
  const undated = { now, servedAt: now, pollAge: 400, gated: false, system: "MNR" };
  const q = (row, board = gated) => arrivalQualifier(row, board);
  // reported and fresh: nothing, which is what silence means.
  assert.deepEqual(q(dated({}, now - 5)), { kind: "", words: "" });
  assert.deepEqual(q(dated({}, now - 89.9)), { kind: "", words: "" });
  // reported and past OBS_FRESH_S (FEED_STALE_AFTER_S, >= as everywhere): its age.
  assert.deepEqual(q(dated({}, now - FEED_STALE_AFTER_S)), { kind: "aged", words: "as of 90s ago" });
  assert.deepEqual(q(dated({}, now - 600)), { kind: "aged", words: "as of 10m ago" });
  assert.deepEqual(q(dated({}, now - 52538)), { kind: "aged", words: "as of 14h 36m ago" });
  // retained: always said, fresh or not, because it is not in the current decode.
  assert.deepEqual(q(dated({}, now - 30, "retained")), { kind: "retained", words: "showing last known, as of 30s ago" });
  assert.deepEqual(q(dated({}, now - 240, "retained")), { kind: "retained", words: "showing last known, as of 4m ago" });
  // A retained row with no clock of its own is as old as its system's last poll.
  assert.deepEqual(q(dated({}, null, "retained"), undated), { kind: "retained", words: "showing last known, as of 7m ago" });
  // null on an AGE-GATED row: an anomaly, said at the row.
  assert.deepEqual(q(dated({}, null)), { kind: "unknown", words: "age unknown" });
  // null on a row whose provider dates nothing (Metro-North): silent at the row.
  assert.deepEqual(q(dated({}, null), undated), { kind: "", words: "" });
  // Everything a prediction cannot honestly carry reads as unknown: the enumeration's
  // own `unknown`, no provenance at all (a backend from before 6.1), and the two
  // position values, which would be false of a prediction.
  for (const provenance of ["unknown", undefined, "placed", "estimated", "live-gps"]) {
    assert.deepEqual(q({ observed_at: now - 5, provenance }), { kind: "unknown", words: "age unknown" }, String(provenance));
  }
  // The age counts on between refreshes: a row served fresh, 100s later.
  assert.deepEqual(q(dated({}, now - 5), { ...gated, now: now + 100 }), { kind: "aged", words: "as of 105s ago" });
});

test("6.2 per ROW, not per envelope: two contributors on one board, only the lagging one's rows speak", () => {
  // F03's second clause on a board. 1-7+S has served content ten minutes behind while
  // its poll succeeds; ACE is current. Both contribute to this station. A rule applied
  // per ENVELOPE would qualify every row (the envelope's content clock is the worst
  // contributor's) or none (its fetched_at is fresh); only a per-row rule tells them apart.
  const now = 50_000;
  const body = {
    fetched_at: now,
    feed_timestamp: now - 600,
    served_at: now,
    directions: {
      Northbound: [
        dated({ route_id: "A", arrival: now + 60 }, now - 5),
        dated({ route_id: "2", arrival: now + 120 }, now - 600),
        dated({ route_id: "C", arrival: now + 180 }, now - 5),
      ],
    },
    systems: {
      "1-7+S": { fetched_at: now, feed_timestamp: now - 600, ok: true },
      ACE: { fetched_at: now, feed_timestamp: now - 5, ok: true },
    },
  };
  const shaped = shapeStationArrivals("subway", body, now);
  assert.deepEqual(
    shaped.buckets[0].rows.map((r) => [r.routeId, r.qualifier]),
    [["A", ""], ["2", "as of 10m ago"], ["C", ""]],
  );
  assert.equal(shaped.systemLine, null, "rows carry it; the board line does not repeat it");
  // New York time at this epoch is 8:53 AM (EST), so the labels are 8:54 and 8:55.
  assert.equal(
    arrivalSentence(shaped.buckets[0].rows[1]),
    "2 train in 2 minutes, 8:55 AM arrival, as of 10m ago",
  );
  assert.equal(arrivalSentence(shaped.buckets[0].rows[0]), "A train in 1 minute, 8:54 AM arrival");
});

test("6.2 the board's system line speaks only for what rows cannot, and never raises itself", () => {
  const now = 50_000;
  const mnr = (fetchedAt) => ({
    fetched_at: fetchedAt,
    feed_timestamp: null,
    served_at: now,
    system: "MNR",
    directions: { Inbound: [dated({ route_id: "1", arrival: now + 240, train_num: "795" }, null)] },
    systems: { MNR: { fetched_at: fetchedAt, feed_timestamp: null, ok: true } },
  });
  // A HEALTHY Metro-North board: its rows are undated by policy, and the clause that
  // says so cannot raise the line on its own. Nothing at all.
  assert.equal(shapeStationArrivals("railroad", mnr(now), now).systemLine, null);
  // Its poll gone stale: the poll's age is the only age these rows have, and the
  // per-system clause rides that line.
  assert.equal(
    shapeStationArrivals("railroad", mnr(now - 400), now).systemLine,
    "as of 7m ago; Metro-North prediction age unavailable",
  );
  // Its rows stay silent at the row either way.
  assert.equal(shapeStationArrivals("railroad", mnr(now - 400), now).buckets[0].rows[0].qualifier, "");
  // An LIRR board with the same stale poll: its rows are dated and each carries its own
  // age, so the line has nothing to add and says nothing.
  const lirr = {
    ...mnr(now - 400),
    system: "LIRR",
    feed_timestamp: now - 405,
    directions: { Inbound: [dated({ route_id: "1", arrival: now + 240, train_num: "8412" }, now - 420)] },
    systems: { LIRR: { fetched_at: now - 400, feed_timestamp: now - 405, ok: true } },
  };
  const lirrShaped = shapeStationArrivals("railroad", lirr, now);
  assert.equal(lirrShaped.systemLine, null);
  assert.equal(lirrShaped.buckets[0].rows[0].qualifier, "as of 7m ago");
  // The railroad popup renders the same line and the same row words, from the same helpers.
  const html = railroadArrivalsHtml({ id: "1", name: "Grand Central", system: "MNR" }, mnr(now - 400), now);
  assert.match(html, /<div class="popup-stale">as of 7m ago; Metro-North prediction age unavailable<\/div>/);
  assert.doesNotMatch(html, /arr-qualifier/);
  assert.deepEqual([...UNDATED_SYSTEMS], ["MNR"]);
});

test("6.2 a qualifier appearing is news once; its age counting up is not", () => {
  const now = 50_000;
  const board = (observedAt, at = now) =>
    shapeStationArrivals(
      "subway",
      { fetched_at: at, served_at: at, directions: { Northbound: [dated({ route_id: "1", arrival: now + 900 }, observedAt)] } },
      at,
    );
  const fresh = board(now - 5);
  const aged = board(now - 600);
  assert.equal(announcementWorthy(fresh, aged), true, "a qualifier appeared");
  // The same aged row a refresh later: the WORDS moved ("10m" to "11m"), the kind did not.
  const later = board(now - 600, now + 60);
  assert.notEqual(later.buckets[0].rows[0].qualifier, aged.buckets[0].rows[0].qualifier);
  assert.equal(announcementWorthy(aged, later), false, "its age counting up is not news");
  assert.equal(announcementWorthy(aged, board(now - 5)), true, "and clearing is");
  // Unknown and retained are kinds of their own.
  const unknown = board(null);
  assert.equal(announcementWorthy(aged, unknown), true);
  // A board system line appearing is news too, once.
  const empty = (at) =>
    shapeStationArrivals("subway", { fetched_at: at, served_at: now, directions: {} }, now);
  assert.equal(announcementWorthy(empty(now), empty(now - 400)), true);
  assert.equal(announcementWorthy(empty(now - 400), empty(now - 460)), false);
});

test("6.2 a board row's age is anchored at served_at: a clock behind it adds nothing, one past it adds the time since", () => {
  // The anchor is what makes the age skew-free: served_at - observed_at comes from one
  // server, and only the time SINCE served_at is read off the client's clock. Every other
  // board test runs at now == served_at, where that and now - observed_at agree.
  const body = (servedAt, observedAt) => ({
    fetched_at: servedAt,
    feed_timestamp: observedAt,
    served_at: servedAt,
    directions: { Northbound: [dated({ route_id: "1", arrival: servedAt + 600 }, observedAt)] },
    systems: { "1-7+S": { fetched_at: servedAt, feed_timestamp: observedAt, ok: true } },
  });
  const words = (b, now) => shapeStationArrivals("subway", b, now).buckets[0].rows[0].qualifier;
  // 100 s old when served, read by a corrected clock 30 s BEHIND served_at: still 100 s.
  assert.equal(words(body(1000, 900), 970), "as of 100s ago");
  // 60 s old when served, read 30 s past served_at: 90 s, over the threshold.
  assert.equal(words(body(1000, 940), 1030), "as of 90s ago");
  // The same row read AT served_at is current, so it was the 30 s since that qualified it.
  assert.equal(words(body(1000, 940), 1000), "");
});

test("6.2 a bucket's qualifiers are a SET: one more row crossing is not news, the last current row crossing is", () => {
  // An LIRR bucket whose provider dates each trip (3.3), so its rows cross the threshold
  // one at a time all evening. Announcing each crossing is the chatter the guard exists to
  // prevent. What a rider needs to hear is the moment no countdown in the bucket is
  // current any more, which is when the set loses its fresh member.
  const board = (at) =>
    shapeStationArrivals(
      "railroad",
      {
        fetched_at: at,
        feed_timestamp: at - 5,
        served_at: at,
        system: "LIRR",
        directions: {
          Inbound: [4900, 4930, 4955].map((observedAt, i) =>
            dated({ route_id: "1", train_num: String(8412 + i), arrival: 5600 + 300 * i }, observedAt),
          ),
        },
        systems: { LIRR: { fetched_at: at, feed_timestamp: at - 5, ok: true } },
      },
      at,
    );
  const kinds = (shaped) => shaped.buckets[0].rows.map((r) => r.qualifierKind);
  const first = board(5000);
  const second = board(5030);
  const third = board(5060);
  assert.deepEqual(kinds(first), ["aged", "", ""]);
  assert.deepEqual(kinds(second), ["aged", "aged", ""]);
  assert.deepEqual(kinds(third), ["aged", "aged", "aged"]);
  assert.equal(announcementWorthy(first, second), false, "a second row crossing while one is current");
  assert.equal(announcementWorthy(second, third), true, "the last current row going old");
});

test("arrivalSentence reads as a sentence, and names the instant honestly", () => {
  const now = 1_700_000_000; // 2023-11-14T22:13:20Z, 5:13 PM in New York
  const rail = shapeStationArrivals(
    "railroad",
    {
      fetched_at: now,
      served_at: now,
      directions: { Inbound: [dated({ route_id: "5", train_num: "8412", arrival: now + 240 }, now - 5)] },
    },
    now,
    { nameFor: () => "Babylon" },
  );
  assert.equal(
    arrivalSentence(rail.buckets[0].rows[0]),
    "Babylon train in 4 minutes, 5:17 PM arrival, train 8412",
  );
  // Route id is the fallback when the name is unknown, and the noun is the caller's.
  const subway = shapeStationArrivals(
    "subway",
    { fetched_at: now, served_at: now, directions: { Northbound: [dated({ route_id: "1", arrival: now + 10 }, now - 5)] } },
    now,
  );
  assert.equal(arrivalSentence(subway.buckets[0].rows[0]), "1 train now, 5:13 PM arrival");
  // Ferry: "departs" and a DEPARTURE label, because that is the field being counted.
  const ferry = shapeStationArrivals(
    "ferry",
    { fetched_at: now, served_at: now, routes: { Astoria: [dated({ route_id: "AS", arrival: now - 30, departure: now + 360 }, now - 5)] } },
    now,
  );
  assert.equal(
    arrivalSentence(ferry.buckets[0].rows[0], "boat"),
    "AS boat departs in 6 minutes, 5:19 PM departure",
  );
  // The zone is New York, not the runner's: the same instant in UTC reads differently.
  assert.equal(clockTimeLabel(now, "UTC"), "10:13 PM");
  assert.equal(clockTimeLabel(now, "America/New_York"), "5:13 PM");
  assert.equal(clockTimeLabel(null), "");
});

test("announcementWorthy speaks on real change and stays silent on the tick", () => {
  const now = 1_700_000_000;
  const shape = (dirs, at = now) =>
    shapeStationArrivals("subway", { fetched_at: at, directions: dirs }, at);
  const base = shape({ Northbound: [{ route_id: "1", arrival: now + 240 }] });

  // THE CASE THE HELPER EXISTS FOR: one second later, nothing else changed. A
  // screen reader narrating "4 minutes... 3 minutes..." forever is why.
  assert.equal(announcementWorthy(base, shape({ Northbound: [{ route_id: "1", arrival: now + 240 }] }, now + 1)), false);
  // Still silent a full minute later: the tick is never the news, at any distance.
  assert.equal(announcementWorthy(base, shape({ Northbound: [{ route_id: "1", arrival: now + 240 }] }, now + 60)), false);

  // A train appears: announce.
  assert.equal(
    announcementWorthy(base, shape({ Northbound: [{ route_id: "1", arrival: now + 240 }, { route_id: "2", arrival: now + 90 }] })),
    true,
  );
  // A train vanishes: announce.
  assert.equal(announcementWorthy(base, shape({ Northbound: [] })), true);
  // The route changed even though the count did not: announce.
  assert.equal(announcementWorthy(base, shape({ Northbound: [{ route_id: "7", arrival: now + 240 }] })), true);
  // A whole direction stopped running: announce.
  assert.equal(announcementWorthy(base, shape({})), true);
  // First render: the arrivals appearing IS the news.
  assert.equal(announcementWorthy(null, base), true);

  // Prediction jitter under the threshold: silent. The feeds revise estimates by
  // a few seconds every poll and announcing that is how a live region gets muted.
  assert.equal(
    announcementWorthy(base, shape({ Northbound: [{ route_id: "1", arrival: now + 240 + ANNOUNCE_LEAD_SHIFT_S - 1 }] })),
    false,
  );
  // Past the threshold: the wait really did change, so say so.
  assert.equal(
    announcementWorthy(base, shape({ Northbound: [{ route_id: "1", arrival: now + 240 + ANNOUNCE_LEAD_SHIFT_S + 1 }] })),
    true,
  );

  // A REORDERED payload carrying the same trains is not a change. The backends do
  // not promise a row order, so announcing on a reshuffle would be pure noise.
  const two = shape({ Northbound: [{ route_id: "1", arrival: now + 240 }, { route_id: "2", arrival: now + 600 }] });
  const flipped = shape({ Northbound: [{ route_id: "2", arrival: now + 600 }, { route_id: "1", arrival: now + 240 }] });
  assert.equal(announcementWorthy(two, flipped), false);
  // But a reorder that ALSO moves the lead arrival is a change, and is announced.
  const sooner = shape({ Northbound: [{ route_id: "2", arrival: now + 600 }, { route_id: "1", arrival: now + 60 }] });
  assert.equal(announcementWorthy(two, sooner), true);
});

test("announcementWorthy on a REPLACED lead train: visible identity decides", () => {
  // The case the A1 review asked to settle: the lead train is a DIFFERENT train
  // arriving at nearly the same time. Route set unchanged, delta under the
  // threshold. Announce, or stay silent?
  //
  // The ruling implemented here: announce only when the rider could perceive it.
  // The signature keys on route plus train number, so the answer differs by
  // system, and it differs for a reason rather than by accident.
  const now = 1_700_000_000;

  // RAILROAD, which renders a train number. 8412 is pulled and 8414 runs 10
  // seconds later: the sentence a rider hears changes from "train 8412" to
  // "train 8414", so staying silent would leave the panel saying one thing and
  // the live region having claimed another. Announce.
  const railBefore = shapeStationArrivals(
    "railroad",
    { fetched_at: now, directions: { Inbound: [{ route_id: "5", train_num: "8412", arrival: now + 240 }] } },
    now,
  );
  const railAfter = shapeStationArrivals(
    "railroad",
    { fetched_at: now, directions: { Inbound: [{ route_id: "5", train_num: "8414", arrival: now + 250 }] } },
    now,
  );
  assert.equal(announcementWorthy(railBefore, railAfter), true);

  // SUBWAY, which renders no train number. One "1" train replaced by another "1"
  // train 10 seconds later is, to anyone reading or hearing the panel, the same
  // sentence: "1 train in 4 minutes". Nothing perceptible changed, and announcing
  // an identity the surface never showed is indistinguishable from noise. Silent.
  const subBefore = shapeStationArrivals(
    "subway",
    { fetched_at: now, directions: { Northbound: [{ route_id: "1", trip_id: "A", arrival: now + 240 }] } },
    now,
  );
  const subAfter = shapeStationArrivals(
    "subway",
    { fetched_at: now, directions: { Northbound: [{ route_id: "1", trip_id: "B", arrival: now + 250 }] } },
    now,
  );
  assert.equal(announcementWorthy(subBefore, subAfter), false);

  // And the boundary still holds under a swap: if the replacement is far enough
  // out to change the wait, clause 3 fires regardless of visible identity.
  const subLater = shapeStationArrivals(
    "subway",
    { fetched_at: now, directions: { Northbound: [{ route_id: "1", trip_id: "B", arrival: now + 240 + ANNOUNCE_LEAD_SHIFT_S + 1 }] } },
    now,
  );
  assert.equal(announcementWorthy(subBefore, subLater), true);
});

/* ---------------- A2: the names on the map ---------------- */

test("A2: every marker name is built from the fields its popup renders", () => {
  // Subway: the route bullet the icon already shows, then where it is going.
  assert.equal(
    subwayTrainName({ route_id: "1", stop_name: "Times Sq-42 St", direction: "Northbound" }),
    "1 train, next stop Times Sq-42 St, Northbound",
  );
  // stop_id is the fallback the popup uses when the name did not resolve.
  assert.equal(subwayTrainName({ route_id: "A", stop_id: "A31" }), "A train, next stop A31");
  // A train with no route is still a subway train, never the literal "?" the icon
  // draws when it cannot fit a bullet.
  assert.equal(subwayTrainName({ stop_name: "Canal St" }), "Subway train, next stop Canal St");
  assert.equal(subwayTrainName({}), "Subway train");
  assert.equal(subwayTrainName(null), "Subway train");

  // Railroad: the popup's own head builder, and the position clause, which is the part
  // that tells a rider how much to trust the position. Since 6.3 it is the answer
  // positionQualifier gives the popup's compact line, said aloud, handed to the builder
  // rather than guessed from stop_id.
  const at = { now: 1000, servedAt: 1000 };
  const gps = positionQualifier({ observed_at: 995, provenance: "reported" }, { ...at, system: "LIRR" });
  const sched = positionQualifier({ observed_at: null, provenance: "placed" }, { ...at, system: "MNR" });
  assert.equal(
    railroadTrainName({ system: "LIRR", route_id: "10", train_num: "2751", direction: "Eastbound" }, "Babylon Branch", gps),
    "LIRR Babylon Branch, train 2751, Eastbound, live GPS",
  );
  // A PLACED train says so, and a train that names a stop has a next stop to give.
  assert.equal(
    railroadTrainName(
      { system: "MNR", route_id: "1", train_num: "8801", stop_id: "1", stop_name: "Grand Central" },
      "Hudson",
      sched,
    ),
    "Metro-North Hudson, train 8801, next stop Grand Central, scheduled position, no GPS",
  );
  // NO MIDDOT. The popup head joins system and route with "·", which is a visual
  // separator; spoken, it is noise or the words "middle dot". Same fields, spoken
  // shape. And "MNR" becomes the word a rider uses, as the A1 panel already does.
  assert.ok(!railroadTrainName({ system: "MNR", route_id: "1" }, "Hudson", sched).includes("·"));
  assert.equal(railroadTrainName({ system: "LIRR", route_id: "10" }, null, gps), "LIRR route 10, live GPS");

  // PATH: always a scheduled position, which the popup states and the name repeats.
  const placedPath = positionQualifier({ observed_at: 995, provenance: "placed" }, { ...at, system: "path" });
  assert.equal(
    pathTrainName(
      { route_id: "862", stop_name: "Grove St", direction: "To Newark" },
      "Newark - World Trade Center",
      placedPath,
    ),
    "Newark - World Trade Center, PATH, next stop Grove St, To Newark, scheduled position, no GPS",
  );
  // No route name resolved yet: formatPathHead's fallback, not a blank.
  assert.equal(pathTrainName({ route_id: "862" }, null, placedPath), "PATH route 862, PATH, scheduled position, no GPS");

  // Ferry: the status in the popup's own words, lowercased into the sentence.
  assert.equal(
    ferryBoatName({ label: "H201", status: "STOPPED_AT" }, "East River"),
    "East River, NYC Ferry, boat H201, at dock",
  );
  assert.equal(
    ferryBoatName({ label: "H202", status: "IN_TRANSIT_TO" }, "Rockaway"),
    "Rockaway, NYC Ferry, boat H202, under way",
  );
  // An unassigned boat is what the popup calls it too. And a boat whose status the
  // feed did not give says NOTHING about its status, rather than guessing "under way":
  // ferryStatusText returns null there and the popup omits the line for the same
  // reason. Inventing the one fact the rider is asking about is worse than silence.
  assert.equal(ferryBoatName({ label: "H9" }), "Unassigned route, NYC Ferry, boat H9");
  assert.equal(ferryBoatName({ label: "H9", status: "NONSENSE" }), "Unassigned route, NYC Ferry, boat H9");

  // AirTrain stations are the one station kind with an element to name.
  assert.equal(airtrainStationName({ name: "Federal Circle" }), "Federal Circle, AirTrain JFK station");
});

test("A2: a bus says its heading as a compass point, never as degrees", () => {
  // THE POINT OF THIS HELPER. The marker's whole visual job is an arrow; a rider
  // listening instead of looking needs the direction as a word, because "142 degrees"
  // is arithmetic to do while standing at a stop.
  assert.equal(busName({ route_id: "M15", bearing: 90 }), "M15 bus, heading east");
  assert.equal(busName({ route_id: "B62", bearing: 0 }), "B62 bus, heading north");
  assert.equal(busName({ route_id: "Q10" }), "Q10 bus, heading unknown");
  assert.equal(busName({ bearing: 180 }), "Bus, heading south");

  // The eight points, and the rounding between them.
  assert.equal(compassPoint(0), "north");
  assert.equal(compassPoint(45), "northeast");
  assert.equal(compassPoint(135), "southeast");
  assert.equal(compassPoint(225), "southwest");
  assert.equal(compassPoint(315), "northwest");
  // Wrapping at both ends: 350 and -10 are the same bearing and must read alike.
  assert.equal(compassPoint(350), "north");
  assert.equal(compassPoint(-10), "north");
  assert.equal(compassPoint(360), "north");
  // Halfway between two points rounds up, consistently, rather than throwing.
  assert.equal(compassPoint(22.5), "northeast");
  assert.equal(compassPoint(NaN), "unknown");
  assert.equal(compassPoint(null), "unknown");
});

/* ---------------- A2: when the page itself speaks ---------------- */

/* ---------------- A2: the motion gate ---------------- */

test("A2: motionAllowed reads the preference, and defaults to animating", () => {
  assert.equal(motionAllowed({ matches: true }), false); // rider asked for reduced motion
  assert.equal(motionAllowed({ matches: false }), true);
  // No matchMedia at all (node, or a browser too old to have it): animate as before
  // rather than silently degrading everyone's map.
  assert.equal(motionAllowed(null), true);
});

/* ---------------- A2: when the page itself speaks ---------------- */

// The freshness index shape the frontend already builds: "<source>|<system>" -> {age}.
// FEED_STALE_AFTER_S is 90, so 200 is degraded and 10 is not.
const fresh = (age) => ({ age });
const OLD = 200;
const NEW = 10;

test("A2: the status line announces degraded-SET transitions, not counts or strings", () => {
  const healthy = degradedIdentities({ "buses|buses": fresh(NEW), "subways|ACE": fresh(NEW) });
  assert.deepEqual(healthy, []);

  // FIRST OBSERVATION IS SILENT. A page load must not read its own condition aloud
  // before the rider has asked for anything. Asserted with a NON-EMPTY set, because a
  // page that loads while the buses are already delayed is the only case that tells
  // seeding apart from announcing; with an empty set the two are indistinguishable and
  // a mutation that announced on first sight would pass unnoticed.
  assert.equal(statusAnnouncement(null, ["buses|buses"]), null);
  assert.equal(statusAnnouncement(null, healthy), null);

  // Entering the degraded set is news, and it names what went wrong.
  const busesOut = degradedIdentities({ "buses|buses": fresh(OLD), "subways|ACE": fresh(NEW) });
  assert.equal(statusAnnouncement(healthy, busesOut), "Live data delayed for Bus.");

  // AN AGE TICK IS NOT A TRANSITION. The same system, older, is still the same set.
  const busesOlder = degradedIdentities({ "buses|buses": fresh(OLD * 5), "subways|ACE": fresh(NEW) });
  assert.equal(statusAnnouncement(busesOut, busesOlder), null);
  // And a re-render with literally the same input says nothing either.
  assert.equal(statusAnnouncement(busesOut, busesOut), null);

  // THE TEST A COUNT-BASED IMPLEMENTATION FAILS. One system recovers as another goes
  // out: the count is unchanged at one, but two things a rider cares about changed.
  const swapped = degradedIdentities({ "buses|buses": fresh(NEW), "subways|ACE": fresh(OLD) });
  assert.equal(
    statusAnnouncement(busesOut, swapped),
    "Live data delayed for Subway ACE. Live data current again for Bus.",
  );

  // A SECOND system joining an already-degraded one is a set change, so it announces.
  // A count-based implementation would notice this one but not the swap above; a
  // string-compare implementation would announce on every poll because the status line
  // carries a clock. Both traps are covered by comparing membership.
  const bothOut = degradedIdentities({ "buses|buses": fresh(OLD), "subways|ACE": fresh(OLD) });
  assert.equal(statusAnnouncement(busesOut, bothOut), "Live data delayed for Subway ACE.");

  // Recovery is worth one sentence: a rider told the data was delayed is owed the news
  // that it is not.
  assert.equal(statusAnnouncement(bothOut, healthy), "Live data current again for Bus and Subway ACE.");

  // A SYSTEM THAT HAS NEVER DECODED AND REPORTS ITSELF DOWN IS DEGRADED, not healthy.
  // The review found the worst possible shape here: a backend restart while a feed is
  // still failing republishes that system with fetched_at null, so a check on age alone
  // dropped it OUT of the degraded set and the page announced "Live data current again"
  // at the moment its trains vanished, then never mentioned it again.
  assert.deepEqual(degradedIdentities({ "subways|ACE": { age: null, ok: false } }), ["subways|ACE"]);
  const dead = degradedIdentities({ "subways|ACE": { age: null, ok: false } });
  assert.equal(statusAnnouncement(["subways|ACE"], dead), null, "a system that stayed dead says nothing new");

  // But a null age with no failure reported is a system still WARMING, which is not a
  // degradation and must not be announced as one.
  assert.deepEqual(degradedIdentities({ "ferry|ferry": { age: null, ok: true } }), []);
  // And an entry with no ok field at all (an older shape) is treated as reporting fine,
  // so this can never invent a degradation out of a missing property.
  assert.deepEqual(degradedIdentities({ "ferry|ferry": { age: null } }), []);

  // The index arrives as a Map in the browser and as an object in tests; both work.
  assert.deepEqual(degradedIdentities(new Map([["path|path", fresh(OLD)]])), ["path|path"]);
});

test("A2: identities are described the way a rider would say them", () => {
  // A single-feed source synthesizes one system named after itself, so naming it twice
  // would be noise.
  assert.equal(statusAnnouncement([], ["ferry|ferry"]), "Live data delayed for Ferry.");
  // A subway feed GROUP is qualified, because "ACE" alone means nothing to a rider.
  assert.equal(statusAnnouncement([], ["subways|ACE"]), "Live data delayed for Subway ACE.");
  // A railroad system is already what a rider calls it: "Railroad LIRR" is a phrase
  // only a schema would produce.
  assert.equal(statusAnnouncement([], ["railroads|LIRR"]), "Live data delayed for LIRR.");
  // BUT "MNR" is the feed's word, not a rider's, and this region is read ALOUD: an
  // initialism is spoken letter by letter. Raised by the review and dropped by my own
  // review script, which escalated only the first finding from each lens. Every other
  // spoken surface already went through railroadSystemLabel; this one did not.
  assert.equal(statusAnnouncement([], ["railroads|MNR"]), "Live data delayed for Metro-North.");
  // BUT the railroads source ALSO synthesizes a system named after itself whenever its
  // payload carries no systems block, and that path produced "Live data delayed for
  // railroads" in the first draft: lowercase and plural, straight out of the payload
  // key. A rider gets a whole word.
  assert.equal(statusAnnouncement([], ["railroads|railroads"]), "Live data delayed for Railroad.");
  // An unknown source (a system added later without a word here) falls back to its key
  // rather than throwing, which is the right failure: odd wording, never a crash.
  assert.equal(statusAnnouncement([], ["amtrak|amtrak"]), "Live data delayed for amtrak.");
  // Three or more read as a list with an "and".
  assert.equal(
    statusAnnouncement([], ["buses|buses", "path|path", "subways|ACE"]),
    "Live data delayed for Bus, PATH, and Subway ACE.",
  );
});

test("A2: the banner announces new and reworded alerts, and nothing else", () => {
  const alert = (id, header, system = "subway") => ({ id, header, system });
  const none = alertIdentities([]);
  const one = alertIdentities([alert("a1", "Delays on the A line")]);

  // First observation seeds silently, even when an alert is already showing on load.
  assert.equal(bannerAnnouncement(null, one), null);
  // A new alert announces, as a SUMMARY. The body belongs on screen and in the panel;
  // a live region reading a full service alert aloud would be unusable during exactly
  // the incident it exists for.
  assert.equal(bannerAnnouncement(none, one), "New service alert.");

  // An identical refresh is silent.
  assert.equal(bannerAnnouncement(one, alertIdentities([alert("a1", "Delays on the A line")])), null);

  // SAME ID, REWORDED CONTENT ANNOUNCES ONCE. This is the C1 pattern: the MTA revises
  // an ongoing incident in place rather than issuing a new id, and an id-only
  // comparison would leave a rider hearing nothing while the situation changed.
  const reworded = alertIdentities([alert("a1", "All A service suspended")]);
  assert.equal(bannerAnnouncement(one, reworded), "New service alert.");
  assert.equal(bannerAnnouncement(reworded, reworded), null); // and only once

  // ORDERING IS NOT NEWS: the identities are compared as a sorted set.
  const two = alertIdentities([alert("a1", "One"), alert("a2", "Two")]);
  const twoReordered = alertIdentities([alert("a2", "Two"), alert("a1", "One")]);
  assert.equal(bannerAnnouncement(two, twoReordered), null);

  // Several at once are counted rather than read out.
  assert.equal(bannerAnnouncement(none, two), "2 new service alerts.");

  // CLEARING IS SILENT. A rider is not told about the absence of an emergency, and the
  // strip disappearing is the signal.
  assert.equal(bannerAnnouncement(two, none), null);
  assert.equal(bannerAnnouncement(two, alertIdentities([alert("a1", "One")])), null);

  // The staleness marker is not part of an identity at all, so it cannot announce: it
  // is honesty about the feed, not news about the transit system.
  assert.deepEqual(alertIdentities([alert("a1", "One")]), alertIdentities([alert("a1", "One")]));
});

test("A2: the motion preference is watched, not only read once", () => {
  // A rider who turns reduced motion on mid-session must be believed without
  // reloading, so the gate subscribes rather than sampling at load. The media query
  // list is injected, which is what lets node drive a change event at all.
  const listeners = [];
  const mql = {
    matches: false,
    addEventListener: (type, fn) => listeners.push([type, fn]),
    removeEventListener: (type, fn) => {
      const i = listeners.findIndex(([t, f]) => t === type && f === fn);
      if (i >= 0) listeners.splice(i, 1);
    },
  };

  const seen = [];
  const stop = watchMotionPreference((allowed) => seen.push(allowed), mql);
  assert.equal(listeners.length, 1);
  assert.equal(listeners[0][0], "change");

  // The rider turns reduced motion ON: the gate closes.
  mql.matches = true;
  listeners[0][1]();
  assert.deepEqual(seen, [false]);

  // And back off again: the gate reopens. Both directions, because a preference that
  // could only ever be turned on would strand a rider who changed their mind.
  mql.matches = false;
  listeners[0][1]();
  assert.deepEqual(seen, [false, true]);

  // Unsubscribing actually detaches.
  stop();
  assert.equal(listeners.length, 0);

  // A media query list too old to support addEventListener yields a no-op unsubscribe
  // rather than throwing on a browser nobody tests.
  const ancient = { matches: true };
  const noop = watchMotionPreference(() => assert.fail("must not be called"), ancient);
  assert.equal(typeof noop, "function");
  noop();
});

// ---------------------------------------------------------------------------
// A3: contrast, computed rather than curated.
// ---------------------------------------------------------------------------

test("A3: parseColor reads every colour form this app actually emits", () => {
  assert.deepEqual(parseColor("#abc"), [170, 187, 204]);
  assert.deepEqual(parseColor("#d68910"), [214, 137, 16]);
  assert.deepEqual(parseColor("rgb(1, 2, 3)"), [1, 2, 3]);
  assert.deepEqual(parseColor("rgba(1, 2, 3, 0.5)"), [1, 2, 3]);
  // routeColor emits hsl(); without this branch every bus route would be unjudgeable.
  const hsl = parseColor("hsl(0, 100%, 50%)").map(Math.round);
  assert.deepEqual(hsl, [255, 0, 0]);
  // NULL rather than a guess. A caller that gets a colour we cannot read must be able
  // to tell, because the alternative is silently printing unreadable text.
  assert.equal(parseColor("chartreuse"), null);
  assert.equal(parseColor(""), null);
  assert.equal(parseColor(null), null);
  assert.equal(contrastRatio("chartreuse", "#fff"), null);
});

test("A3: contrastRatio matches the WCAG anchors and is order-independent", () => {
  assert.equal(Math.round(contrastRatio("#ffffff", "#000000")), 21);
  assert.equal(Math.round(contrastRatio("#ffffff", "#ffffff")), 1);
  assert.equal(contrastRatio("#777777", "#ffffff"), contrastRatio("#ffffff", "#777777"));
  // The published ratio for the classic "web grey on white" pair, to three places.
  assert.ok(Math.abs(contrastRatio("#767676", "#ffffff") - 4.54) < 0.01);
});

// THE REAL STYLESHEET, NOT A COPY OF ITS NUMBERS. A test that hardcoded "#666" would
// keep passing after someone lightened the CSS, which is the exact regression it exists
// to catch. So the values are read out of style.css and the ratios computed from what
// ships.
test("A3: every muted ink in style.css clears AA on the surface it prints on", () => {
  const css = require("node:fs").readFileSync(`${__dirname}/style.css`, "utf8");
  const declared = (selector) => {
    const block = css.match(new RegExp(`\\${selector}\\s*\\{([^}]*)\\}`));
    assert.ok(block, `${selector} must still exist in style.css`);
    const color = block[1].match(/(?:^|[;\s])color:\s*([^;]+);/);
    assert.ok(color, `${selector} must still declare a color`);
    return color[1].trim();
  };
  /* MR1 SPLIT THIS TEST IN TWO HALVES, because the chrome was tokenised and the popups were not:
     "the popups keep their literal white surface UNTIL MR5 RESTYLES THEM", it said, and half one
     measured three popup greys against a literal `#ffffff`. MR5 is that stage, so the halves
     converge: there is no literal surface left to measure against and both halves resolve tokens
     per theme. The claim is unchanged throughout, and it is the claim that caught this: a muted
     ink clears AA on the surface it is ACTUALLY painted on. MR5's popup greys were #666 on white,
     which is 4.74 on the light surface and 2.45 on the dark one, and axe named the dark one a
     serious violation the moment the popup stopped being white.

     THE SURFACES THIS TEST BELIEVES IN ARE ASSERTED TOO, so a stylesheet that moved one cannot
     make a pairing pass while the real thing got worse. */

  // Half two: the chrome, resolved per theme out of the two :root blocks. The tokens are
  // read from the stylesheet rather than repeated here, so a token edited without measuring
  // fails this test instead of shipping.
  const tokens = (selector) => {
    const block = css.match(new RegExp(`${selector}\\s*\\{([\\s\\S]*?)\\n\\}`));
    assert.ok(block, `${selector} must declare the token set`);
    const out = {};
    for (const [, name, value] of block[1].matchAll(/--([\w-]+):\s*([^;]+);/g)) out[name] = value.trim();
    return out;
  };
  const light = tokens(":root,\n:root\\[data-theme=\"light\"\\]");
  const dark = tokens(':root\\[data-theme="dark"\\]');

  /* MR5: a declared value may now be `var(--muted)` rather than a hex, because the popup rules are
     tokenised. Resolved against the theme's own token map, one level, which is all this stylesheet
     ever nests: a var() whose token the block does not declare fails loudly rather than resolving
     to undefined and comparing as null, which is how a tokenised rule would otherwise pass this
     test by being unmeasurable. */
  const resolved = (value, t, what) => {
    const ref = /^var\(--([\w-]+)\)$/.exec(String(value).trim());
    if (!ref) return value;
    const token = t[ref[1]];
    assert.ok(token, `${what}: style.css declares var(--${ref[1]}), which this theme does not define`);
    return token;
  };

  for (const [theme, t] of [["light", light], ["dark", dark]]) {
    // TEXT owes 4.5. Each of these is a real string in the chrome: the muted feed names and
    // the note, the ink the rows are set in, the accent variant the stale note and the OFF
    // mark are set in, and the filled button's label on its own background.
    for (const [ink, surface, what] of [
      [t.muted, t.surface, "muted text on the header surface"],
      [t.ink, t.surface, "body text on the header surface"],
      [t["accent-ink"], t.surface, "the accent AS TEXT on the header surface"],
      [t.chipink, t["accent-ink"], "the filled button's label on its own fill"],
      [t.surface, t.ink, "an active view preset's label on its inverted fill"],
      // MR5: the popup's own strings, on the popup's own surface, which is the same --surface
      // token the header uses. Read out of the stylesheet by selector so a grey reintroduced in
      // either rule fails here rather than shipping.
      [declared(".popup-sub"), t.surface, "the popup's kicker and sub text"],
      [declared(".arr-none"), t.surface, "the popup's \"No trains\" line"],
      [declared(".popup-stale"), t.surface, "the popup's freshness hedge"],
      [declared(".arr-qualifier"), t.surface, "a board row's own age qualifier"],
      [declared(".alert-block"), t.surface, "the popup's service alert text"],
      [declared(".xlink"), t.surface, "the cross-link button's label"],
      [declared(".leaflet-popup-content .alert-stale"), t.surface, "the popup's alerts-stale hedge"],
      // MR5 (Q2): the footer's WORDS take --muted, not the state's colour. --accent as text reads
      // 3.47 in the light theme, below the 4.5 a string owes, and every other honesty line in this
      // app uses --muted; the state's colour goes on the square, which is measured below as a
      // graphic. Read by selector so a later edit that "matches the words to the dot" fails here.
      [declared(".fresh"), t.surface, "the popup freshness footer's words"],
      /* MR5: AND THE COUNTDOWN THAT READS "now", for the same reason one rule down in the
         stylesheet and because a reviewer found that rule's comment claiming this sweep already
         measured it. It did not: every other popup ink here is read BY SELECTOR and `.arr .now` was
         not among them, so the pairing that was actually measured was the bare `--accent-ink`
         token. An edit taking section 5's own instruction literally ("the countdown reads in the
         accent") would have shipped 3.47 in the light theme with nothing failing. */
      [declared(".arr .now"), t.surface, "an arrival row that reads \"now\""],
    ]) {
      const paint = resolved(ink, t, `${theme}: ${what}`);
      const ratio = contrastRatio(paint, resolved(surface, t, `${theme}: ${what} surface`));
      assert.ok(
        ratio != null && ratio >= 4.5,
        `${theme}: ${what} is ${paint} on ${surface} = ${ratio}, below the 4.5 it owes`,
      );
    }
    // NON-TEXT owes 3. The freshness dots and the focus ring are graphics: each carries a
    // fact (live, stale, scheduled-only, focused) that a rider has to be able to SEE, and
    // none of them is text.
    for (const [mark, surface, what] of [
      [t.accent, t.surface, "the stale freshness dot"],
      // MR5: the popup's alert rule, its accessibility glyph and the freshness footer's square are
      // graphics, not prose, so each owes 3 rather than 4.5. The square is the feed strip's dot at
      // the popup and carries the same three tokens, measured here on the POPUP's surface rather
      // than the header's: a rider reads them in both places and only one was ever measured.
      [t.accent, t.surface, "the popup alert block's 3px accent rule"],
      [declared(".popup-access"), t.surface, "the dock popup's wheelchair glyph"],
      [t.live, t.surface, "the popup footer's live square"],
      [t.scheduled, t.surface, "the popup footer's schedule-only square"],
      [t.accent, t.surface, "the popup footer's stale square"],
      [t.live, t.surface, "the live freshness dot"],
      [t.scheduled, t.surface, "the scheduled-only freshness dot"],
      [t.focus, t.surface, "the focus ring on the header surface"],
      [t.focus, t.bg, "the focus ring on the map's own backdrop"],
    ]) {
      const paint = resolved(mark, t, `${theme}: ${what}`);
      const ratio = contrastRatio(paint, resolved(surface, t, `${theme}: ${what} surface`));
      assert.ok(
        ratio != null && ratio >= 3,
        `${theme}: ${what} is ${paint} on ${surface} = ${ratio}, below the 3 it owes`,
      );
    }
  }

  // AND THE HEADER SURFACE IS OPAQUE, which is the A3 ruling this stage had the most reason
  // to lose: the handoff draws the header at 90% over blurred map tiles. axe cannot decide
  // the contrast of text over a translucent surface whose backdrop is an image, so every
  // string in the chrome would join the undecidable set that making this surface opaque took
  // from nine entries to one. Asserted as the absence of the two ways it would come back.
  const headerRule = css.match(/#panel \{([\s\S]*?)\n\}/);
  assert.ok(headerRule, "#panel must still exist in style.css");
  assert.match(headerRule[1], /background: var\(--surface\);/, "the header surface is a token");
  assert.doesNotMatch(headerRule[1], /backdrop-filter/, "the header must not blur its backdrop");
  assert.doesNotMatch(headerRule[1], /color-mix|rgba/, "the header surface must not be translucent");

  /* AND MR5's POPUP IS THE SAME RULING ON THE SAME GROUNDS. Section 5 asks for it at 94% over the
     same blurred tiles. Measured on this branch, that translucency did worse than make the popup
     undecidable: the dark theme's REAL violation on the head's ink and .popup-sub was reported
     ONLY as incomplete, so it hid the failure this test now catches. frontend/tokens.test.js
     holds the rule itself; this holds the pairing, and the two together are why the entry above
     stayed at one. The blur went with the alpha, as it did on the header, so the two surfaces are
     now asserted in exactly the same three ways. */
  const popupRule = css.match(/\.leaflet-popup-content-wrapper,\s*\.leaflet-popup-tip \{([\s\S]*?)\n\}/);
  assert.ok(popupRule, "the popup surface rule must still exist in style.css");
  assert.match(popupRule[1], /background: var\(--surface\);/, "the popup surface is a token");
  assert.doesNotMatch(popupRule[1], /backdrop-filter/, "the popup must not blur its backdrop");
  assert.doesNotMatch(popupRule[1], /color-mix|rgba/, "the popup surface must not be translucent");
});

test("A3: readableTextOn replaces the hand-curated dark-text set, and is never wrong", () => {
  // The four lines the old set named still get dark ink.
  for (const line of ["N", "Q", "R", "W"]) {
    assert.equal(readableTextOn(lineColor(line)), INK_DARK, `${line} should take dark ink`);
  }
  // AND THE SIX IT MISSED. Each of these carried white text at under 4.5 against its own
  // fill: B/D/F/M at 2.82, G at 2.97, L at 3.48. This is the assertion that would have
  // failed before the helper existed.
  for (const line of ["B", "D", "F", "M", "G", "L"]) {
    assert.equal(readableTextOn(lineColor(line)), INK_DARK, `${line} was missing from DARK_TEXT_LINES`);
  }
  // Whatever it picks, the pick clears AA. Asserted over every id in the palette rather
  // than over a sample, because a set that is right about ten entries and wrong about the
  // eleventh is exactly what this replaces.
  for (const line of Object.keys(LINE_COLORS)) {
    const bg = lineColor(line);
    const ratio = contrastRatio(readableTextOn(bg), bg);
    assert.ok(ratio >= 4.5, `subway ${line} (${bg}) ink is only ${ratio.toFixed(2)}`);
  }
  // FALLBACK FILLS INCLUDED ON PURPOSE. Each is what a rider sees when a feed omits a
  // route id, which is a degraded state and therefore when the label matters most.
  // The rail families' #607d8b failed when this test was written: 4.37 with white ink and
  // 3.98 with dark, so NEITHER choice could rescue it and the fill itself had to move. That is
  // railBranchPaint's whole argument, and its comment carries the measurement now.
  // routeColor is absent from this list for the reason given above.
  for (const bg of [RAIL_NEUTRAL_COLOR, lineColor(null), PATH_FALLBACK_COLOR, FERRY_FALLBACK_COLOR]) {
    const ratio = contrastRatio(readableTextOn(bg), bg);
    assert.ok(ratio >= 4.5, `fallback fill ${bg} ink is only ${ratio.toFixed(2)}`);
  }
  /* AND THE RAIL FAMILIES' PUBLISHED PAIRS, which is ruling R1's substitution for a sweep over a
     hash palette that no longer exists. The ids are gone because a rail colour is not a function of
     its id any more; what a rider reads is the agency's own fill with the agency's own ink, and
     railBranchPaint is what decides that pair.

     EE0034 IS IN THE LIST ON PURPOSE. It is Metro-North's New Haven red, four of that railroad's six
     routes, and readableTextOn gives it 4.48 with white: a badge that took the published colour
     through readableTextOn rather than through railBranchPaint would ship an AA failure on the
     common case at Grand Central, and no fixture serves that colour so no gate would say so.
     railtag.test.js sweeps all 26 published colours; this is the four that decide the shape. */
  for (const [hex, textColor] of [["", ""], ["00985F", "FFFFFF"], ["EE0034", "FFFFFF"], ["FFD411", ""]]) {
    const paint = railBranchPaint(hex, textColor);
    const ratio = contrastRatio(paint.ink, paint.fill);
    assert.ok(ratio >= 4.5, `rail paint "${hex}" (${paint.fill} on ${paint.ink}) is only ${ratio.toFixed(2)}`);
  }
  // DELIBERATELY NOT THE BUS WHEEL. hsl(h, 75%, 40%) has hues where neither ink clears
  // 4.5 (hue 30 tops out at 4.36), and the first draft of this test swept it and failed.
  // The failure was the test's, not the palette's: nothing prints text ON a bus colour.
  // It is a polyline colour and a heading colour, so it owes 3:1 as a non-text indicator,
  // and its heading use is covered by the readableInk sweep in the next test. Asserting
  // an obligation a colour does not have would have forced a redesign of the wheel to
  // satisfy a rule nobody was breaking.
});

test("A3: readableInk darkens only what must darken, and keeps the hue", () => {
  // Already readable: returned untouched, so a colour that needs nothing is not shifted.
  assert.equal(readableInk("#1f5fbf"), "#1f5fbf");
  assert.equal(readableInk("#c0392b"), "#c0392b");
  // The worst case in the palette. #e6b800 on white is 1.87, which is not text anyone
  // can read; the result must clear 4.5 and must still be recognisably yellow, meaning
  // red and green stay far ahead of blue.
  const yellow = parseColor(readableInk("#e6b800"));
  assert.ok(contrastRatio(readableInk("#e6b800"), "#ffffff") >= 4.5);
  assert.ok(yellow[0] > yellow[2] && yellow[1] > yellow[2], "an N heading must still read yellow");
  // Every palette colour, as text on white, after the helper.
  for (const line of Object.keys(LINE_COLORS)) {
    const ratio = contrastRatio(readableInk(lineColor(line)), "#ffffff");
    assert.ok(ratio >= 4.5, `subway ${line} heading ink is only ${ratio.toFixed(2)}`);
  }
  for (let hue = 0; hue < 360; hue += 5) {
    const ratio = contrastRatio(readableInk(`hsl(${hue}, 75%, 40%)`), "#ffffff");
    assert.ok(ratio >= 4.5, `bus hue ${hue} heading ink is only ${ratio.toFixed(2)}`);
  }
  // Unparseable input passes through rather than throwing: a caller that hands us
  // something odd gets its own colour back, not a crash in a popup.
  assert.equal(readableInk("chartreuse"), "chartreuse");
});

/* MR5: THE OTHER DIRECTION, WHICH THIS FUNCTION DID NOT HAVE AND SILENTLY FAILED WITHOUT.
   Every version before MR5 only ever darkened and fell back to `#000000`, under a comment saying
   "black fails nothing on a light surface". On a DARK background that is backwards and the
   fallback is the worst answer available: black reads 1.49:1 on the dark theme's --surface.
   It was latent until this stage because the popup was Leaflet's white in BOTH themes, even after
   MR4 shipped the dark one, so no caller had ever handed this function a dark background. The
   popup's surface is the first, and axe named the violation the moment it did. */
const DARK_SURFACE = "#2d2b2b"; // the dark theme's --surface, which tokens.test.js holds to style.css

test("MR5: readableInk lightens on a dark background, where darkening could only fail", () => {
  // The exact shape of the old bug: two thirds of the palette came back as unreadable black.
  assert.ok(contrastRatio("#000000", DARK_SURFACE) < 1.5, "black on this surface is the worst answer");
  for (const line of Object.keys(LINE_COLORS)) {
    const ink = readableInk(lineColor(line), DARK_SURFACE);
    const ratio = contrastRatio(ink, DARK_SURFACE);
    assert.ok(ratio >= 4.5, `subway ${line} on the dark popup is only ${ratio.toFixed(2)} (${ink})`);
    assert.notEqual(ink.toLowerCase(), "#000000", `subway ${line} came back black on a dark surface`);
  }
  for (let hue = 0; hue < 360; hue += 5) {
    const ratio = contrastRatio(readableInk(`hsl(${hue}, 75%, 40%)`, DARK_SURFACE), DARK_SURFACE);
    assert.ok(ratio >= 4.5, `bus hue ${hue} on the dark popup is only ${ratio.toFixed(2)}`);
  }
  // And the hue survives the tint, which is the whole reason it is a tint: the 1/2/3's red
  // lightens to a lighter red rather than washing to grey.
  const red = parseColor(readableInk("#c0392b", DARK_SURFACE));
  assert.ok(red[0] > red[1] && red[0] > red[2], "a lightened red must still read red");
  // A colour that already clears is returned untouched in this direction too. The app's own N
  // yellow rather than the authority's #FCCC0A: MR2's ruling R1 is that the palette is this app's,
  // and a test that reaches for the published value teaches the next reader the wrong colour.
  assert.equal(readableInk("#e6b800", DARK_SURFACE), "#e6b800");
});

test("MR5: readableInk's darkening path is unchanged, character for character", () => {
  /* THE REGRESSION GUARD THE REPAIR NEEDED, and it exists because the obvious rewrite fails it.
     Folding both directions into one loop with `1 - step` against `c + (255 - c) * step` looks
     identical and is not: 0.05 has no exact binary form, so counting DOWN by subtraction and UP by
     addition accumulate different error, and at a rounding boundary the two disagree by one unit
     per channel. Measured, thirteen of this app's own colours came back different on the light
     surfaces, which would have moved thirteen pins for a reason unrelated to the repair.

     SO THE OLD BODY IS TRANSCRIBED HERE AS THE ORACLE. This is the one place in this repo where a
     copy of an implementation is the right test: the claim is precisely "the new function agrees
     with the old one wherever the old one was right", and only the old one can say what that was.
     It is compared over every colour the app ships and every surface it prints on. */
  const wasReadableInk = (color, background = "#ffffff", target = 4.5) => {
    const rgb = parseColor(color);
    if (!rgb) return color;
    if ((contrastRatio(color, background) ?? 0) >= target) return color;
    for (let scale = 0.95; scale >= 0; scale -= 0.05) {
      const scaled = rgb.map((c) => Math.round(c * scale));
      const hex = `#${scaled.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
      if ((contrastRatio(hex, background) ?? 0) >= target) return hex;
    }
    return "#000000";
  };
  const colours = [
    ...new Set(Object.values(LINE_COLORS)),
    "#d93a30", "#546e7a", "#00839c", "#78909c", "#ffd100", "#e6b800", "#DD3439", "#08A652",
    "#e6b800", "#4a4e69", "#6d6e71", "#000", "#fff", "#123456", "#abcdef", "chartreuse", "",
  ];
  // Every LIGHT surface this app prints on: white (the old default), the popup's --surface, --bg,
  // the popup cream the alert block used to carry, the banner's amber, and a mid yellow as the
  // boundary case where the direction is decided closest to the line.
  const surfaces = ["#ffffff", "#eae9e9", "#f3f2f2", "#fdf6e3", "#fde8b0", "#ffd100"];
  let compared = 0;
  for (const bg of surfaces) {
    for (const c of colours) {
      assert.equal(readableInk(c, bg), wasReadableInk(c, bg), `${c} on ${bg} moved`);
      compared += 1;
    }
  }
  assert.ok(compared >= 120, `the comparison must be broad to mean anything, got ${compared}`);
  // And the NEW behaviour is genuinely new rather than the old one relabelled: on a dark surface
  // the two must disagree, or this repair changed nothing.
  const disagreements = colours.filter((c) => readableInk(c, DARK_SURFACE) !== wasReadableInk(c, DARK_SURFACE));
  assert.ok(disagreements.length >= 8, `the dark path must differ from the old one, got ${disagreements.length}`);
});

test("A3: the status line states its order and never truncates a problem", () => {
  const counts = "1,234 buses · 56 trains";
  const clock = "8:01:23 AM";
  // Healthy: counts, then the clock, and the word that says the clock is a freshness
  // stamp rather than a departure time.
  assert.equal(statusLineText({ counts, clock }), `${counts} · updated ${clock}`);
  // Problems: counts, clock, then every problem, joined and WHOLE.
  const problems = ["Bus: upstream 502", "Subway ACE: data 3m old"];
  assert.equal(
    statusLineText({ counts, clock, problems }),
    `${counts} · ${clock}: Bus: upstream 502; Subway ACE: data 3m old`,
  );
  // COMPACT DROPS THE SECONDS AND NOTHING ELSE. That is the only part of the line
  // carrying no information a rider acts on.
  assert.equal(statusLineText({ counts, clock }, { compact: true }), `${counts} · updated 8:01 AM`);
  const tight = statusLineText({ counts, clock, problems }, { compact: true });
  assert.ok(tight.includes("8:01 AM") && !tight.includes("8:01:23"));
  // THE HARD RULE, asserted rather than described: every problem survives compaction
  // character for character. "Bus: upstream 502" clipped to "Bus: upstr" reads as the
  // page running out of room rather than as something being broken.
  for (const problem of problems) assert.ok(tight.includes(problem), `${problem} was altered`);
  assert.ok(tight.includes(counts), "the counts survive too");
  // A very long problem is still whole; there is no cap anywhere in the composition.
  const long = "Railroad LIRR: upstream returned 503 Service Unavailable after three attempts";
  assert.ok(statusLineText({ counts, clock, problems: [long] }, { compact: true }).includes(long));
  // Falsy problems are dropped rather than rendered as empty segments.
  assert.equal(statusLineText({ counts, clock, problems: [null, "", undefined] }), `${counts} · updated ${clock}`);
});

test("A3: the mobile breakpoint is one number, and style.css agrees with it", () => {
  // Written twice by necessity: the media queries lay the page out, and the status
  // line's compaction is composed in JavaScript. A number written twice drifts, so this
  // reads the stylesheet rather than trusting the pair.
  const css = require("node:fs").readFileSync(`${__dirname}/style.css`, "utf8");
  assert.ok(
    css.includes(`@media (max-width: ${MOBILE_MAX_WIDTH_PX}px)`),
    `style.css must use the ${MOBILE_MAX_WIDTH_PX}px breakpoint declared in helpers.js`,
  );
  // The 1100px docked threshold is a DIFFERENT decision and A3 does not touch it; if it
  // ever collapses into the mobile number, this says so.
  assert.ok(css.includes("@media (min-width: 1100px)"), "the docked threshold is unchanged");
  assert.notEqual(MOBILE_MAX_WIDTH_PX, 1100);
  // narrowViewport takes an injected query so it is testable without a browser, and
  // returns the roomy answer when there is no matchMedia at all.
  assert.equal(narrowViewport({ matches: true }), true);
  assert.equal(narrowViewport({ matches: false }), false);
  assert.equal(narrowViewport(), false);
});

const { vanishingFocusPlan, vanishingFocusMessage } = require("./helpers.js");

// A4: the vanishing-focus decision, tested without a DOM. The predicate is the whole of
// the policy, so it is worth pinning away from Leaflet, viewports and timing.
test("A4: vanishingFocusPlan rescues only when the rider was inside the doomed subtree", () => {
  const inside = { tag: "button" };
  const elsewhere = { tag: "input" };
  const subtree = { contains: (node) => node === inside };

  assert.equal(vanishingFocusPlan(subtree, inside).rescue, true);
  assert.equal(vanishingFocusPlan(subtree, elsewhere).rescue, false, "focus elsewhere is not rescued");
  // The subtree itself counts as inside it: the banner case focuses a descendant, but a
  // future caller could hand us the focused node directly and the answer must not change.
  assert.equal(vanishingFocusPlan(subtree, subtree).rescue, true);
  // Nothing to destroy, or nothing focused, is never a rescue rather than an error.
  assert.equal(vanishingFocusPlan(null, inside).rescue, false);
  assert.equal(vanishingFocusPlan(subtree, null).rescue, false);
  // A subtree without contains() (a detached stub) must not throw.
  assert.equal(vanishingFocusPlan({}, inside).rescue, false);
});

test("A4: the wording names the vehicle from its own label, and never invents a noun", () => {
  // Built from the marker's accessible name rather than a hardcoded "train", because this
  // app carries buses and boats too and buildMarkerName puts the identity in the leading
  // clause. The decisions block's example wording is the subway case of this rule.
  assert.equal(
    vanishingFocusMessage("vehicle", "1 train, next stop Times Sq-42 St, Northbound"),
    "The 1 train you were following left the feed. Focus moved to the map.",
  );
  assert.equal(
    vanishingFocusMessage("vehicle", "M15 bus, northbound"),
    "The M15 bus you were following left the feed. Focus moved to the map.",
  );
  assert.equal(vanishingFocusMessage("alerts"), "Alerts cleared. Focus moved to the map.");
  // A nameless marker still gets a true sentence rather than "The undefined you were...".
  assert.equal(
    vanishingFocusMessage("vehicle", null),
    "The vehicle you were following left the feed. Focus moved to the map.",
  );
  assert.equal(vanishingFocusMessage("vehicle", "   "), vanishingFocusMessage("vehicle", null));
});

/* A4 ROUND 1: the popup-clearing geometry, with the three real viewports as its cases.
   These numbers are not invented: they are what the browser measured while the adversarial
   round was taking the first version apart, which is what makes them worth pinning. */
const { boxesOverlap, shiftBox, popupClearingShift, POPUP_CLEAR_GAP } = require("./helpers.js");
const box = (left, right, top, bottom) => ({ left, right, top, bottom, width: right - left, height: bottom - top });

test("popupClearingShift returns null when nothing is in the way", () => {
  const popup = box(100, 300, 400, 500);
  const legend = box(1030, 1270, 10, 710);
  assert.equal(popupClearingShift(popup, [legend], box(0, 1280, 0, 720)), null);
});

test("popupClearingShift goes LEFT at desktop, where nothing fits below a 700px legend", () => {
  // The measured desktop case: #panel spans y 10..710 of a 720px map, so the old
  // downward-only version bailed every time and the popup stayed under the legend.
  const popup = box(1001, 1276, 293, 419);
  const legend = box(1030, 1270, 10, 710);
  const shift = popupClearingShift(popup, [legend], box(360, 1280, 0, 720));
  assert.equal(shift.dy, 0);
  assert.equal(shift.dx, -(1276 - 1030) - POPUP_CLEAR_GAP);
  // And the result actually clears, which is the property the direction is chosen for.
  assert.equal(boxesOverlap(shiftBox(popup, shift.dx, shift.dy), legend), false);
});

test("popupClearingShift goes DOWN at 375, where left would run off the screen", () => {
  const popup = box(234, 370, 5, 84);
  const legend = box(140, 365, 10, 285);
  const shift = popupClearingShift(popup, [legend], box(0, 375, 0, 667));
  assert.equal(shift.dx, 0);
  assert.equal(shift.dy, 285 - 5 + POPUP_CLEAR_GAP);
  assert.equal(boxesOverlap(shiftBox(popup, shift.dx, shift.dy), legend), false);
});

test("popupClearingShift costs EVERY blocker, not just the first one it meets", () => {
  /* ROUND 4: the round-2 fix ("each blocker costed SEPARATELY") had no test that could
     tell it from "cost only the first blocker". The three tests round 2 added kill the
     max-over-all-blockers shape; a variant that seeds the candidate list from blocking[0]
     alone passed all 161 node tests. Non-equivalent, and found by searching the space:
     with both blockers costed the answer is dx 30; with only the first it is dx 30 dy 45,
     a strictly larger move, which breaks the "cheapest that actually clears" contract the
     function is built on. */
  const popup = box(299, 322, 104, 171);
  const a = box(182, 311, 116, 141);
  const b = box(272, 321, 83, 172);
  const viewport = box(0, 400, 0, 400);
  const both = popupClearingShift(popup, [a, b], viewport);
  assert.deepEqual(both, { dx: 30, dy: 0 }, "the cheapest move that clears both is one step sideways");
  // And it really clears: the answer above is not merely smaller, it is correct.
  assert.equal(boxesOverlap(shiftBox(popup, both.dx, both.dy), a), false);
  assert.equal(boxesOverlap(shiftBox(popup, both.dx, both.dy), b), false);
});

test("popupClearingShift will not move a popup out from under the legend and under the banner", () => {
  // The third defect the round found: the old guard knew only the map's height, so a tall
  // popup was panned down onto the alert banner, which paints over the popup pane.
  const popup = box(234, 370, 5, 305);
  const legend = box(140, 365, 10, 285);
  const banner = box(8, 367, 595, 645);
  const viewport = box(0, 375, 0, 667);
  const withoutBanner = popupClearingShift(popup, [legend], viewport);
  assert.equal(withoutBanner.dy > 0, true, "with only the legend, down is available");
  const withBanner = popupClearingShift(popup, [legend, banner], viewport);
  assert.equal(
    withBanner && boxesOverlap(shiftBox(popup, withBanner.dx, withBanner.dy), banner),
    false,
    "whatever it picks, it must not land on the banner",
  );
});

test("popupClearingShift returns null rather than moving a popup off the viewport", () => {
  // A popup wider than the clear space either side: every candidate leaves the map box, so
  // the honest answer is to leave it where Leaflet put it.
  const popup = box(20, 360, 5, 84);
  const legend = box(140, 365, 10, 285);
  assert.equal(popupClearingShift(popup, [legend], box(0, 375, 0, 300)), null);
});

test("popupClearingShift costs a direction by the obstacle that demands the most", () => {
  // Two blockers, and clearing only the nearer one is not clearing anything.
  const popup = box(200, 300, 100, 200);
  const near = box(280, 320, 50, 250);
  const far = box(290, 400, 50, 250);
  const shift = popupClearingShift(popup, [near, far], box(0, 500, 0, 500));
  const moved = shiftBox(popup, shift.dx, shift.dy);
  assert.equal(boxesOverlap(moved, near), false);
  assert.equal(boxesOverlap(moved, far), false);
});

test("popupClearingShift steps sideways when the first move lands on a second obstacle", () => {
  // ROUND 2: adding the banner as an obstacle could CANCEL the leftward desktop move,
  // because a candidate that collided with a non-blocking obstacle was discarded rather
  // than extended. With every candidate discarded the popup stayed fully under the legend,
  // so the defect-3 fix undid the defect-2 fix.
  const popup = box(1001, 1276, 20, 146);
  const legend = box(1030, 1270, 10, 710);
  const banner = box(400, 1020, 10, 60); // the left move alone would land under this
  const viewport = box(360, 1280, 0, 720);
  const shift = popupClearingShift(popup, [legend, banner], viewport);
  assert.notEqual(shift, null, "a two-axis move exists, so null would be giving up early");
  const moved = shiftBox(popup, shift.dx, shift.dy);
  assert.equal(boxesOverlap(moved, legend), false);
  assert.equal(boxesOverlap(moved, banner), false);
  assert.equal(moved.left >= viewport.left && moved.right <= viewport.right, true);
  assert.equal(moved.top >= viewport.top && moved.bottom <= viewport.bottom, true);
});

test("popupClearingShift still prefers a single-axis move when one clears everything", () => {
  // The L is a fallback, not a habit: a straight move that works must beat a longer pair.
  const popup = box(1001, 1276, 293, 419);
  const legend = box(1030, 1270, 10, 710);
  const shift = popupClearingShift(popup, [legend], box(360, 1280, 0, 720));
  assert.equal(shift.dy, 0, "one axis is enough here");
  assert.equal(shift.dx, -(1276 - 1030) - POPUP_CLEAR_GAP);
});

/* MR5 (ruling S3): THE CLAMP, WITH THE MEASUREMENT THAT MADE IT NECESSARY AS ITS FIRST CASE.
   Same discipline as the block above: these are browser measurements, not invented numbers. The
   claim under test is one inequality, `padA + padB + popupExtent <= mapExtent`, which is what
   Leaflet's two per-axis branches encode and what the design's recipe violates at phone widths. */
const { clampedAutoPanPadding, popupAutoPanWant, POPUP_AUTOPAN_GAP, POPUP_AUTOPAN_WANT } = require("./helpers.js");

test("S3: the design's recipe, unclamped, is the padding that pushes a popup off a phone", () => {
  const want = popupAutoPanWant(579); // the recipe verbatim: a measured edge plus its own gap
  assert.equal(want.top, 579 + POPUP_AUTOPAN_GAP, "the top padding is derived, not typed");
  assert.deepEqual(
    { left: want.left, right: want.right, bottom: want.bottom },
    POPUP_AUTOPAN_WANT,
    "and the other three are the recipe's fixed values",
  );
  // Leaflet can honour top and bottom together only while they fit around the popup: this is
  // the arithmetic the erratum records, 591 + 40 + 126 on a 667px map.
  assert.equal(want.top + want.bottom + 126 > 667, true, "unsatisfiable, which is the finding");
});

test("S3: the clamp cuts the padding that WINS, and keeps the one already being honoured", () => {
  const got = clampedAutoPanPadding({
    want: popupAutoPanWant(579),
    map: { width: 375, height: 667 },
    popup: { width: 256, height: 126 },
  });
  const [left, top] = got.topLeft;
  const [right, bottom] = got.bottomRight;
  // Vertically Leaflet's TOP branch assigns second and therefore wins, so the top is what is
  // cut and the 40px below the popup survives intact.
  assert.equal(bottom, 40, "the small fixed padding is kept whole");
  assert.equal(top, 667 - 126 - 40, "and the derived one takes exactly the slack that is left");
  assert.equal(top + bottom + 126, 667, "which makes the pair satisfiable, with nothing spare");
  // Horizontally the LEFT branch is the one that wins, and the 110 clears the control stack
  // while the 24 is a margin, so the margin is what gives way.
  assert.equal(right, 110, "the padding that clears actual chrome is kept whole");
  assert.equal(left, 375 - 256 - 110, "and the margin takes the slack");
  assert.deepEqual(got.clamped, { top: true, left: true }, "and it says which ends it cut");
});

test("S3: at desktop nothing is clamped, so the clamp cannot be hiding a bug", () => {
  const got = clampedAutoPanPadding({
    want: popupAutoPanWant(60),
    map: { width: 1280, height: 720 },
    popup: { width: 320, height: 200 },
  });
  assert.deepEqual(got.topLeft, [24, 72], "the recipe's own numbers, untouched");
  assert.deepEqual(got.bottomRight, [110, 40]);
  assert.deepEqual(got.clamped, { top: false, left: false });
});

test("S3: a popup bigger than the map asks for no padding rather than choosing an edge", () => {
  const got = clampedAutoPanPadding({
    want: popupAutoPanWant(579),
    map: { width: 375, height: 640 },
    popup: { width: 400, height: 700 },
  });
  // There is no satisfiable padding for a popup that does not fit, and demanding one would only
  // decide which edge it hangs off. Zero leaves that to Leaflet, which at least keeps the
  // popup's own anchor in view.
  assert.deepEqual(got.topLeft, [0, 0]);
  assert.deepEqual(got.bottomRight, [0, 0]);
  assert.equal(got.usable, true, "the boxes were measurable; it is the fit that failed");
});

test("S3: an unmeasurable box stands the padding down instead of guessing at one", () => {
  for (const args of [{}, { map: { width: 375, height: 667 } }, { popup: { width: 1, height: 1 } }]) {
    const got = clampedAutoPanPadding({ want: popupAutoPanWant(100), ...args });
    assert.equal(got.usable, false, JSON.stringify(args));
    assert.deepEqual(got.topLeft, [0, 0]);
    assert.deepEqual(got.bottomRight, [0, 0]);
  }
  // A NaN or an Infinity is the same case: a measurement that did not happen.
  const nan = { want: popupAutoPanWant(100), map: { width: NaN, height: 667 }, popup: { width: 1, height: 1 } };
  assert.equal(clampedAutoPanPadding(nan).usable, false);
});

test("S3: every padding the clamp returns is a non-negative integer", () => {
  // Leaflet does arithmetic on these and writes the result into a transform, so a fraction or a
  // negative would land in the page as a sub-pixel pan or a pan the wrong way.
  const cases = [
    { chrome: 579, map: { width: 375, height: 667 }, popup: { width: 256.4, height: 126.7 } },
    { chrome: 0, map: { width: 320.5, height: 640.5 }, popup: { width: 260, height: 300 } },
    { chrome: 1e6, map: { width: 1280, height: 720 }, popup: { width: 320, height: 200 } },
  ];
  for (const c of cases) {
    const got = clampedAutoPanPadding({ want: popupAutoPanWant(c.chrome), map: c.map, popup: c.popup });
    for (const n of [...got.topLeft, ...got.bottomRight]) {
      assert.equal(Number.isInteger(n) && n >= 0, true, `${JSON.stringify(c)} -> ${n}`);
    }
  }
});

test("popupClearingShift re-checks a move against obstacles that were NOT blocking it", () => {
  // A MUTATION SURVIVED THE FIRST VERSION OF THE TEST ABOVE, because there both obstacles
  // blocked from the start, so "re-check against all obstacles" and "re-check against the
  // blockers" were the same check. Here the banner is clear of the popup where it opens and
  // is only reachable by the move itself: a filter that looks at the blockers alone accepts
  // the straight leftward move and lands the popup on the banner.
  const popup = box(1001, 1276, 20, 146);
  const legend = box(1030, 1270, 10, 710);
  const banner = box(400, 900, 20, 146);
  assert.equal(boxesOverlap(popup, banner), false, "the banner must not block where the popup opens");
  const shift = popupClearingShift(popup, [legend, banner], box(360, 1280, 0, 720));
  const moved = shiftBox(popup, shift.dx, shift.dy);
  assert.equal(boxesOverlap(moved, legend), false, "clears what blocked it");
  assert.equal(boxesOverlap(moved, banner), false, "and does not land on what did not");
});

/* ---- 15c: NJ Transit Rail helpers ------------------------------------------
   The whole point of this block is the AMENDMENT-A CASE: a route that has no line.
   NJ Transit's routes.txt carries twelve routes and route 17, the event-only
   Meadowlands Rail Line, has trips in no publication anyone has seen, so it never
   reaches /api/njt-routes at all. Every colour and name lookup on the layer has to
   survive that, and so does every lookup made in the seconds between the first
   /api/njt-trains poll painting markers and loadNjtRoutes resolving, which is the
   same hole reached by a different road.
--------------------------------------------------------------------------- */

test("njtColor validates the feed's bare hex and falls back on anything else", () => {
  assert.equal(njtColor("075AAA"), "#075AAA");
  assert.equal(njtColor("dd3439"), "#dd3439");
  // The three shapes the endpoint can actually serve or a caller can reach it with.
  assert.equal(njtColor(null), NJT_FALLBACK_COLOR, "route_color is nullable");
  assert.equal(njtColor(""), NJT_FALLBACK_COLOR, "an empty string is what an absent column parses to");
  assert.equal(njtColor("#075AAA"), NJT_FALLBACK_COLOR, "already-prefixed is malformed for this feed");
  assert.equal(njtColor("nothex"), NJT_FALLBACK_COLOR);
  assert.equal(njtColor("075AA"), NJT_FALLBACK_COLOR, "five digits is not a colour");
});

test("the NJT fallback colour is none of the twelve published route colours", () => {
  // Stated as a test rather than as a comment because the fallback's whole job is to
  // look like a placeholder, and the route most likely to reach it (17) publishes a
  // khaki that a warm neutral would have been indistinguishable from.
  const published = [
    "075AAA", "E66859", "FFD411", "FFD411", "08A652", "A4C9AA",
    "DD3439", "03A3DF", "94219A", "E87725", "F2A537", "C1AA72",
  ];
  for (const hex of published) {
    assert.notEqual(njtColor(hex).toLowerCase(), NJT_FALLBACK_COLOR.toLowerCase());
  }
  // And it is not another mode's fallback either: a placeholder that looked like
  // PATH's or the ferry's would be read as that mode rather than as a placeholder.
  assert.notEqual(NJT_FALLBACK_COLOR, PATH_FALLBACK_COLOR);
  assert.notEqual(NJT_FALLBACK_COLOR, FERRY_FALLBACK_COLOR);
});

test("AMENDMENT A: a route with no line still gets a colour and a head, never a blank", () => {
  // The tables as they exist for a publication with no Meadowlands trips: eleven
  // routes with geometry, and route 17 in neither table.
  const { colors, names, index } = njtRouteTables([
    { route: "9", name: "Northeast Corridor", color: "DD3439", polylines: [[[40.7, -74.1], [40.75, -73.99]]] },
  ]);
  assert.equal(njtRouteColor("17", colors), NJT_FALLBACK_COLOR);
  assert.equal(njtRouteName("17", names), null, "no name is null, not the string 'undefined'");
  assert.equal(formatNjtHead("17", njtRouteName("17", names)), "NJ Transit route 17");
  assert.equal(index.get("17"), undefined, "and no geometry, which computeRouteSlice reads as no glide");
  // The popup a rider would actually see. No crash, and no blank heading: the
  // amendment's "no blank popup" made checkable.
  const html = njtTrainPopupHtml(
    { route_id: "17", train_num: "1701", headsign: "Meadowlands", stop_name: "Secaucus Junction" },
    njtRouteName("17", names),
    njtRouteColor("17", colors),
    positionQualifier({ observed_at: 995, provenance: "placed" }, { now: 1000, servedAt: 1000, system: "njt" }),
  );
  assert.match(html, /NJ Transit route 17/);
  // MR5: section 5's rows, so the noun in front of each fact is a label cell now.
  assert.match(html, /<div class="k">Train<\/div>\n<div class="v">1701<\/div>/);
  assert.match(html, /<div class="k">To<\/div>\n<div class="v">Meadowlands<\/div>/);
  assert.match(html, /scheduled position \(no GPS\)/);
  assert.doesNotMatch(html, /undefined|null/, "a missing route must not leak a placeholder word");
  // The accessible name takes the same fallback, so the marker a screen reader
  // reaches is not "undefined, NJ Transit" either.
  assert.match(njtTrainName({ route_id: "17" }, njtRouteName("17", names)), /^NJ Transit route 17, NJ Transit/);
});

test("AMENDMENT A: the same fallbacks hold before the route table has loaded at all", () => {
  // The empty-Map case is not the same code path as the missing-key case only by
  // accident: it is the state the page is in for several seconds on every cold
  // start, while /api/njt-trains has answered and /api/njt-routes has not.
  const empty = new Map();
  assert.equal(njtRouteColor("9", empty), NJT_FALLBACK_COLOR);
  assert.equal(njtRouteName("9", empty), null);
  // And the shapes a caller can reach these with before anything is assigned.
  assert.equal(njtRouteColor("9", null), NJT_FALLBACK_COLOR);
  assert.equal(njtRouteColor("9", undefined), NJT_FALLBACK_COLOR);
  assert.equal(njtRouteName("9", null), null);
  assert.equal(njtRouteColor(null, empty), NJT_FALLBACK_COLOR, "a train with no route_id at all");
  assert.equal(formatNjtHead(null, null), "NJ Transit");
});

test("njtRouteTables builds the drawn geometry and the glide index from ONE payload", () => {
  const cumCalls = [];
  const { names, colors, index } = njtRouteTables(
    [
      { route: "9", name: "Northeast Corridor", color: "DD3439", polylines: [[[40.7, -74.1], [40.75, -73.99]]] },
      // Two kept variants, which is what the backend's dedup leaves on the branchy
      // routes (2, 7, 8 and 10 in the committed fixture).
      { route: "10", name: "North Jersey Coast Line", color: "03A3DF", polylines: [[[40.3, -74.0]], [[40.2, -74.0]]] },
      // A route the payload named but drew nothing for. It keeps its name and its
      // colour and gets no index entry: the popup can still say "Pascack Valley
      // Line" while the train glides its straight chord.
      { route: "13", name: "Pascack Valley Line", color: "94219A", polylines: [] },
      { route: null, name: "junk", color: "000000", polylines: [[[0, 0]]] },
    ],
    (points) => {
      cumCalls.push(points.length);
      return points.map((_, i) => i);
    },
  );
  assert.deepEqual([...names.keys()], ["9", "10", "13"], "a route with no id is skipped entirely");
  assert.deepEqual([...colors.keys()], ["9", "10", "13"]);
  assert.equal(colors.get("13"), "#94219A", "a lineless route still colours its badge");
  assert.deepEqual([...index.keys()], ["9", "10"], "and contributes no glide geometry");
  assert.equal(index.get("10").length, 2, "both kept variants reach the glide index");
  assert.deepEqual(cumCalls, [2, 1, 1], "arc lengths are computed once per drawn polyline");
  // THE PROPERTY THE FUNCTION EXISTS FOR: the points the map draws and the points a
  // train glides along are the same arrays, not two reads of one payload.
  assert.equal(index.get("9")[0].points[0][0], 40.7);
});

test("njtKey is the backend id, which is what makes ADDED trips distinct markers", () => {
  // NJ Transit emits ADDED trips with an EMPTY trip_id (36 of them in the first
  // capture that caught a disrupted evening). Keying on trip_id would collapse
  // every one of them onto a single marker, which is the 15b finding reappearing on
  // the client; models.NjtTrain guarantees `id` is never empty for exactly this.
  const added = [
    { id: "njt:9001", trip_id: "", route_id: "9" },
    { id: "njt:9002", trip_id: "", route_id: "9" },
  ];
  assert.equal(new Set(added.map(njtKey)).size, 2, "two added trains are two markers");
  assert.equal(new Set(added.map((t) => t.trip_id)).size, 1, "and would have been one under trip_id");
  assert.equal(njtKey({ id: "NJ_1234", trip_id: "NJ_1234" }), "NJ_1234");
  // The family's null guards, asserted rather than assumed. A review round deleted
  // each of these one at a time and watched the whole suite stay green, which under
  // this project's rule made them decoration; one line each turns them back into
  // guards. They are kept rather than removed because every one of these helpers is
  // also reachable from a node test or a future caller with nothing in hand.
  assert.equal(njtKey(null), undefined);
  assert.equal(njtKey(undefined), undefined);
  assert.match(njtTrainPopupHtml(null, null, "#DD3439"), /NJ Transit/);
  // 6.3: a row with nothing in it says "age unknown" about its position, the pessimistic
  // fail-safe (design 3.1), rather than a constant that would be false of half the layer.
  assert.equal(njtTrainName(null, null, positionQualifier(null, {})), "NJ Transit, NJ Transit, age unknown");
  assert.deepEqual([...njtRouteTables(null).names], [], "a payload that never arrived");
  assert.deepEqual(
    [...njtRouteTables([{ route: "9", name: "Northeast Corridor", color: "DD3439" }]).index],
    [],
    "and a route entry with no polylines key at all",
  );
});

test("njtDelayText says late, early, or nothing, and rounds to the minute", () => {
  assert.equal(njtDelayText(250), "4 min late");
  // NEAREST-MINUTE, NOT TRUNCATION, and only these two values say so: every other
  // case in this test reads the same under Math.floor, which a review round proved
  // by mutating Math.round away and watching all 183 tests stay green. A truncating
  // rewrite would silently drop every 30-to-59 second delay from both the popup and
  // the accessible name.
  assert.equal(njtDelayText(40), "1 min late");
  assert.equal(njtDelayText(-40), "1 min early");
  assert.equal(njtDelayText(29), "", "and under half a minute is still nothing");
  // NJ Transit publishes negative delays. Rendering one as "late" would be a lie
  // about the direction, which is worse than saying nothing.
  assert.equal(njtDelayText(-250), "4 min early");
  assert.equal(njtDelayText(0), "", "on time is the unremarkable case and prints nothing");
  assert.equal(njtDelayText(20), "", "under half a minute rounds to zero");
  assert.equal(njtDelayText(null), "");
  assert.equal(njtDelayText(undefined), "");
  assert.equal(njtDelayText(NaN), "");
  assert.equal(njtDelayText(3600), "60 min late");
});

test("njtTrainPopupHtml and njtTrainName word one train the same way", () => {
  const train = {
    route_id: "7", train_num: "6633", headsign: "Dover",
    stop_name: "Summit", delay: 250,
  };
  const position = positionQualifier({ ...train, observed_at: 995, provenance: "placed" }, { now: 1000, servedAt: 1000, system: "njt" });
  const html = njtTrainPopupHtml(train, "Morris & Essex Line", "#08A652", position);
  // MR5: the position is the LAST row of the grid, which is where the popup's last line was.
  assert.match(html, /<div class="k">Position<\/div>\n<div class="v">scheduled position \(no GPS\)<\/div><\/div>\n$/);
  assert.match(html, /Morris &amp; Essex Line/, "the ampersand in a real route name is escaped");
  // AND ESCAPED ONCE, which is the trap of moving escaping into a builder: a caller that also
  // escaped would print "&amp;amp;" and no test that only looked for the escaped form would say so.
  assert.doesNotMatch(html, /&amp;amp;/);
  assert.match(html, /<div class="k">Train<\/div>\n<div class="v">6633<\/div>/);
  assert.match(html, /<div class="k">To<\/div>\n<div class="v">Dover<\/div>/);
  assert.match(html, /<div class="k">Next stop<\/div>\n<div class="v">Summit<\/div>/);
  assert.match(html, /<div class="k">Delay<\/div>\n<div class="v">4 min late<\/div>/);
  assert.equal(
    njtTrainName(train, "Morris & Essex Line", position),
    "Morris & Essex Line, NJ Transit, train 6633, to Dover, next stop Summit, 4 min late, scheduled position, no GPS",
  );
});

test("EVERY NJT train popup says how its position was derived, from the served provenance, and none says GPS (6.3)", () => {
  // NJ Transit's vehicle positions feed is deliberately never fetched, so no train on
  // this layer is GPS. WHICH derivation a train is comes from the backend: a train at or
  // approaching a stop is served `placed`, one feeds/njt.py interpolated between two
  // stops `estimated`. Before 6.3 this test pinned "scheduled position" for every train,
  // the in-transit one included, which was the constant positionQualifier replaced.
  const at = { now: 1000, servedAt: 1000, system: "njt" };
  const cases = [
    [
      { route_id: "9", status: "at-station", stop_name: "Trenton", provenance: "placed", observed_at: 995 },
      "scheduled position (no GPS)",
      "scheduled position, no GPS",
    ],
    [
      { route_id: "9", status: "in-transit", latitude: 40.7, longitude: -74.1, provenance: "estimated", observed_at: 995 },
      "estimated from a prediction",
      "estimated from a prediction",
    ],
    [
      { route_id: "9", provenance: "placed", observed_at: 700 },
      "scheduled position (no GPS), as of 5m ago",
      "scheduled position, no GPS, as of 5m ago",
    ],
  ];
  for (const [train, words, spoken] of cases) {
    const position = positionQualifier(train, at);
    const html = njtTrainPopupHtml(train, "Northeast Corridor", "#DD3439", position);
    assert.ok(html.endsWith(`<div class="k">Position</div>\n<div class="v">${words}</div></div>\n`), html);
    assert.doesNotMatch(html, /live GPS/);
    assert.ok(njtTrainName(train, "Northeast Corridor", position).endsWith(`, ${spoken}`));
  }
});

test("njtTrainPopupHtml escapes every feed-derived string", () => {
  const html = njtTrainPopupHtml(
    {
      route_id: "<script>", train_num: "<img src=x>", headsign: "a\"b",
      stop_name: "<b>Newark</b>", delay: null,
    },
    null,
    "#DD3439",
  );
  assert.doesNotMatch(html, /<script>|<img|<b>Newark/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /&quot;/);
});

test("njtStationName names the railroad, which is what a shared platform needs", () => {
  assert.equal(njtStationName({ id: "109", name: "New York Penn Station" }), "New York Penn Station, NJ Transit, station");
  assert.equal(njtStationName({ id: "109", name: null }), "109, NJ Transit, station", "id when the feed has no name");
  assert.equal(njtStationName(null), "NJ Transit, NJ Transit, station");
});

test("njtArrivalsHtml renders a flat chronological board with badges and countdowns", () => {
  const station = { id: "109", name: "New York Penn Station" };
  const body = {
    fetched_at: 100,
    arrivals: [
      { route_id: "9", headsign: "Trenton", train_num: "3800", arrival: 190, departure: 200, delay: 0 },
      { route_id: "2", headsign: "Dover", train_num: "6633", arrival: 400, departure: 410, delay: 250 },
    ],
  };
  const colorFor = (id) => ({ 9: "#DD3439", 2: "#E66859" })[id] || NJT_FALLBACK_COLOR;
  const html = njtArrivalsHtml(station, body, 100, colorFor, (id) => (id === "9" ? "Northeast Corridor" : null));
  assert.match(html, /New York Penn Station/);
  assert.match(html, /NJ Transit/);
  // NO DIRECTION HEADINGS AT ALL. /api/njt-arrivals is flat and chronological;
  // inventing buckets here would be inventing a field the endpoint does not serve.
  assert.doesNotMatch(html, /class="dir"/);
  assert.match(html, /background:#DD3439/);
  assert.match(html, /Trenton/);
  assert.match(html, /3800/);
  assert.match(html, /2 min/);
  assert.match(html, /5 min/);
  // Row order is the payload's, which is the board's chronological order.
  assert.ok(html.indexOf("Trenton") < html.indexOf("Dover"), "chronological, not re-sorted");
});

test("an NJT board past the staleness threshold says how old its rows are", () => {
  // The R1 honesty, per ROW since 6.2. A departure board that keeps ticking on a feed
  // that stopped updating is the exact failure this exists to prevent: the rows carry
  // the TripUpdates header as their clock, and once that is past the threshold each
  // row says so beside its countdown. The board's own line stays out of it, because a
  // dated row speaks for itself.
  const body = {
    fetched_at: 100,
    served_at: 100,
    arrivals: [{ route_id: "9", headsign: "Trenton", arrival: 400, observed_at: 95, provenance: "reported" }],
  };
  const fresh = njtArrivalsHtml({ id: "109", name: "Penn" }, body, 100);
  assert.doesNotMatch(fresh, /as of|age unknown/, "a fresh board says nothing about its age");
  const stale = njtArrivalsHtml({ id: "109", name: "Penn" }, body, 100 + 200);
  assert.match(stale, /<span class="arr-qualifier">as of 3m ago<\/span>/);
  assert.doesNotMatch(stale, /popup-stale/, "the row speaks, not the board");
});

test("njtArrivalsHtml counts down to the DEPARTURE once the train has arrived", () => {
  // The dwell rule, which NJT rows carry both halves of. Before the train reaches
  // the platform the rider wants the arrival; once it is standing there, the thing
  // they need is when it leaves.
  const body = { fetched_at: 100, arrivals: [{ route_id: "9", headsign: "Trenton", arrival: 90, departure: 160 }] };
  const html = njtArrivalsHtml({ id: "109", name: "Penn" }, body, 100);
  assert.match(html, /departs 1 min/);
});

test("njtArrivalsHtml renders No trains for an empty board and escapes hostile fields", () => {
  const empty = njtArrivalsHtml({ id: "109", name: "Penn" }, { arrivals: [] }, 100);
  assert.match(empty, /No trains/);
  assert.doesNotMatch(empty, /arr-badge/);
  const hostile = njtArrivalsHtml(
    { id: "1", name: "<script>alert(1)</script>" },
    { fetched_at: 0, arrivals: [{ route_id: "<svg>", headsign: "<i>x</i>", train_num: "<u>", arrival: 60 }] },
    0,
  );
  // Tags chosen so none of them is one the renderer itself emits: <b> wraps the
  // station name legitimately, so asserting on it would pass or fail for the wrong
  // reason. Every tag below can only have come from the payload.
  assert.doesNotMatch(hostile, /<script>|<svg>|<i>x|<u>/);
});

test("njtRowLabel prefers the destination, falls back to the route name, else nothing", () => {
  const nameFor = (id) => (id === "9" ? "Northeast Corridor" : null);
  /* MR5: PLAIN TEXT, WITH NO LEADING SPACE AND NO ESCAPING. The row's grid cell escapes its own
     label and the space between cells is a column now, so a helper that kept doing either would
     double-escape an ampersand and print a stray space inside a flex cell. */
  assert.equal(njtRowLabel({ route_id: "9", headsign: "Trenton" }, nameFor), "Trenton");
  assert.equal(njtRowLabel({ route_id: "9" }, nameFor), "Northeast Corridor");
  assert.equal(njtRowLabel({ route_id: "9", headsign: "Penn & Broad" }, nameFor), "Penn & Broad");
  // A row with neither still renders (the caller keeps its countdown): the train is
  // real and the time is what the rider came for.
  assert.equal(njtRowLabel({ route_id: "17" }, nameFor), "");
  assert.equal(njtRowLabel({}, nameFor), "");
});

test("shapeStationArrivals gives the NJT board one bucket, with the destination on the row", () => {
  const body = {
    fetched_at: 100,
    served_at: 100,
    arrivals: [
      {
        route_id: "9", headsign: "Trenton", train_num: "3800", arrival: 190, departure: 200,
        observed_at: 95, provenance: "reported",
      },
    ],
  };
  const shaped = shapeStationArrivals("njt", body, 100, { nameFor: () => "Northeast Corridor" });
  assert.equal(shaped.buckets.length, 1);
  assert.equal(shaped.buckets[0].name, "Departures");
  assert.deepEqual(shaped.buckets[0].rows, [
    {
      routeId: "9", routeName: "Northeast Corridor", trainNum: "3800",
      headsign: "Trenton", mode: "arriving", seconds: 90, at: 190,
      qualifier: "", qualifierKind: "",
    },
  ]);
  // An empty board yields NO bucket, which is how the panel reaches its "No trains."
  assert.deepEqual(shapeStationArrivals("njt", { fetched_at: 100, arrivals: [] }, 100).buckets, []);
  assert.deepEqual(shapeStationArrivals("njt", {}, 100).buckets, []);
});

test("arrivalSentence reads the destination for NJT and is unchanged for everyone else", () => {
  assert.equal(
    arrivalSentence(
      { routeName: "Morris & Essex Line", headsign: "Dover", trainNum: "6633", mode: "arriving", seconds: 240, at: 1000 },
      "train",
      "UTC",
    ),
    "Morris & Essex Line to Dover train in 4 minutes, 12:16 AM arrival, train 6633",
  );
  // The other kinds carry no headsign at all (checked against every arrivals model),
  // so their sentence is byte-identical to what it was before the field existed.
  assert.equal(
    arrivalSentence({ routeName: "Babylon", trainNum: "8412", mode: "arriving", seconds: 240, at: 1000 }, "train", "UTC"),
    "Babylon train in 4 minutes, 12:16 AM arrival, train 8412",
  );
});

test("njtAtItsStation is true exactly when the decoder drew the train on its stop", () => {
  // backend/feeds/njt.py places a train four ways. Cases 1, 2 and 4 emit the STOP'S
  // coordinates with null anchors; only case 3 interpolates and carries them. So the
  // anchors are the discriminator, and every one of these is a real payload shape.
  assert.equal(njtAtItsStation({ prev_lat: null, stop_id: "109" }), true, "case 1, dwelling");
  assert.equal(njtAtItsStation({ prev_lat: null, stop_id: "112" }), true, "case 2, approaching");
  assert.equal(
    njtAtItsStation({ prev_lat: 40.73, prev_lon: -74.16, stop_id: "109" }),
    false,
    "case 3 carries the stop it is HEADING FOR, which is not where it is drawn",
  );
  // No stop at all: nothing to be at, and nothing to link to.
  assert.equal(njtAtItsStation({ prev_lat: null, stop_id: null }), false);
  assert.equal(njtAtItsStation(null), false);
});

test("njtGlideTrain puts the NEXT STATION where the glide helpers expect it", () => {
  /* THE f-SQUARED DEFECT, pinned. trainLatLng was written for the subway and
     railroad decoders, whose latitude/longitude is the FAR END of the segment. NJ
     Transit's decoder interpolates on the server, so its latitude/longitude is the
     train's CURRENT position, already f of the way along; feeding that to
     trainLatLng walks prev -> current-position by f a second time and draws the
     train at f squared. Measured below on the real Newark Penn to New York Penn
     leg. */
  const prev = [40.734924, -74.164581];
  const next = [40.750568, -73.993519];
  const now = 1786061700;
  const half = [(prev[0] + next[0]) / 2, (prev[1] + next[1]) / 2];
  const served = {
    prev_lat: prev[0], prev_lon: prev[1], prev_time: now - 600, next_time: now + 600,
    latitude: half[0], longitude: half[1], stop_id: "109",
  };
  const stops = new Map([["109", next], ["112", prev]]);

  // What the payload alone would draw: a quarter of the way, at the poll instant.
  const naive = trainLatLng(served, now, {});
  assert.ok(Math.abs((naive[0] - prev[0]) / (next[0] - prev[0]) - 0.25) < 1e-9, "f squared");

  // What the reconciliation draws: the half the server said.
  const glide = njtGlideTrain(served, stops);
  assert.deepEqual([glide.latitude, glide.longitude], next, "the far end is the next STOP");
  const drawn = trainLatLng(glide, now, {});
  assert.ok(Math.abs((drawn[0] - prev[0]) / (next[0] - prev[0]) - 0.5) < 1e-9);
  assert.deepEqual(drawn, half, "and it lands exactly where the server placed it");

  // Everything else it carries is untouched, so the caller can attach _route to it
  // and read stop_id for the segment key.
  assert.equal(glide.stop_id, "109");
  assert.equal(glide.prev_time, now - 600);
});

test("njtGlideTrain returns null rather than guessing, in every state that cannot glide", () => {
  const stops = new Map([["109", [40.750568, -73.993519]]]);
  const anchors = { prev_lat: 40.73, prev_lon: -74.16, prev_time: 1, next_time: 2, latitude: 40.74, longitude: -74.1 };
  // Drawn at its own stop: nothing to interpolate, and substituting the stop would
  // be substituting the position it is already at.
  assert.equal(njtGlideTrain({ ...anchors, prev_lat: null, stop_id: "109" }, stops), null);
  // Heading for a stop the table does not carry (the seconds before loadNjtStops
  // resolves, or a stop the static load dropped). Drawing at the served position is
  // right; interpolating toward an unknown point is not.
  assert.equal(njtGlideTrain({ ...anchors, stop_id: "999" }, stops), null);
  assert.equal(njtGlideTrain({ ...anchors, stop_id: "109" }, new Map()), null, "empty table");
  assert.equal(njtGlideTrain({ ...anchors, stop_id: "109" }, null), null, "no table at all");
  assert.equal(njtGlideTrain({ ...anchors, stop_id: null }, stops), null);
  // Half a pair of time anchors is not a segment.
  assert.equal(njtGlideTrain({ ...anchors, next_time: null, stop_id: "109" }, stops), null);
  assert.equal(njtGlideTrain({ ...anchors, prev_time: null, stop_id: "109" }, stops), null);
  assert.equal(njtGlideTrain(null, stops), null);
});

test("a route-less arrivals row renders its countdown rather than the word null", () => {
  // models.NjtArrival declares route_id as nullable and feeds/njt.py produces one:
  // an ADDED trip whose TripDescriptor omits route_id and joins no static trip has
  // no route to recover. The badge's "?" is the only thing between that and a
  // literal "null" in a rider-visible chip, and nothing exercised it until a review
  // round mutated the fallback away and watched the whole suite stay green.
  const html = njtArrivalsHtml(
    { id: "109", name: "New York Penn Station" },
    { fetched_at: 100, arrivals: [{ route_id: null, headsign: "Bay Head", train_num: null, arrival: 190 }] },
    100,
  );
  assert.match(html, /arr-badge[^>]*>\?</, "the badge says it does not know, in one character");
  assert.match(html, /Bay Head/, "and the row keeps the destination and its countdown");
  assert.match(html, /2 min/);
  assert.doesNotMatch(html, /null|undefined/);
  // The row's label still resolves without a route, and njtRowLabel survives a
  // missing row entirely, which its siblings in this family already did.
  assert.equal(njtRowLabel({ route_id: null, headsign: "Bay Head" }), "Bay Head");
  assert.equal(njtRowLabel(null), "");
  assert.equal(njtRowLabel(undefined), "");
});

test("the NJT panel gets the dwell rule too, not only the popup", () => {
  /* THE BRANCH A REVIEW ROUND FOUND UNTESTED. shapeStationArrivals takes the ferry's
     dwell rule for "njt" as well, and deleting `|| kind === "njt"` left every tier
     green: the one njt case in this file used arrival 190 / departure 200 against
     now = 100, where the dwell rule and the plain `arrival - now` fallback agree.
     The row that separates them is the one the backend deliberately keeps on the
     board: a train standing at the platform, arrival already past, departure ahead. */
  const dwelling = {
    route_id: "9", headsign: "Trenton", train_num: "3800", arrival: 70, departure: 340,
    observed_at: 95, provenance: "reported",
  };
  const shaped = shapeStationArrivals("njt", { fetched_at: 100, served_at: 100, arrivals: [dwelling] }, 100, {
    nameFor: () => "Northeast Corridor",
  });
  const row = shaped.buckets[0].rows[0];
  assert.equal(row.mode, "departing");
  assert.equal(row.seconds, 240, "counts to the DEPARTURE, not to an arrival in the past");
  assert.equal(row.at, 340);
  // And the sentence the panel speaks says so, rather than announcing the train as
  // due "now" while the popup says it leaves in four minutes.
  assert.equal(
    arrivalSentence(row, "train", "UTC"),
    "Northeast Corridor to Trenton train departs in 4 minutes, 12:05 AM departure, train 3800",
  );
  // The popup renders the same train the same way, from the same rule.
  assert.match(
    njtArrivalsHtml({ id: "109", name: "Penn" }, { fetched_at: 100, served_at: 100, arrivals: [dwelling] }, 100),
    /departs 4 min/,
  );
});

test("the NJT board is ordered by the number it prints, not by the backend's sort key", () => {
  /* THE SEAM. feeds/njt._trim_njt_arrivals sorts on max(arrival, departure) so a
     dwelling train does not sort by a past arrival; the countdown counts to the
     ARRIVAL while it is still ahead. Two trains with different dwell lengths make
     the two keys disagree, and these two rows are already in the order the endpoint
     serves them (sort keys 210 then 300, ascending). */
  const dover = { route_id: "2", headsign: "Dover", arrival: 180, departure: 210 };
  const trenton = { route_id: "9", headsign: "Trenton", arrival: 120, departure: 300 };
  assert.deepEqual(
    njtOrderedArrivals([dover, trenton], 0).map((r) => r.headsign),
    ["Trenton", "Dover"],
    "2 min must print above 3 min",
  );
  // The popup and the panel both take the ordering, so one station's board cannot
  // read two ways.
  const body = { fetched_at: 0, arrivals: [dover, trenton] };
  const html = njtArrivalsHtml({ id: "109", name: "Penn" }, body, 0);
  assert.ok(html.indexOf("Trenton") < html.indexOf("Dover"));
  assert.deepEqual(
    shapeStationArrivals("njt", body, 0).buckets[0].rows.map((r) => r.headsign),
    ["Trenton", "Dover"],
  );
  // A tie keeps the backend's order rather than whatever the sort produces, and a
  // row with no usable time sorts LAST rather than ahead of every real train.
  const a = { route_id: "1", headsign: "A", arrival: 60 };
  const b = { route_id: "2", headsign: "B", arrival: 60 };
  const unknown = { route_id: "3", headsign: "C", arrival: null, departure: null };
  assert.deepEqual(
    njtOrderedArrivals([a, unknown, b], 0).map((r) => r.headsign),
    ["A", "B", "C"],
  );
  assert.deepEqual(njtOrderedArrivals(null, 0), []);
});

test("a deployment that does not run NJ Transit is not an error on the status line", () => {
  // The backend's own words, quoted from routes/pollers so a reworded detail cannot
  // silently start reading as an outage.
  assert.equal(
    isNotConfigured(
      "NJ Transit is not configured (NJT_USERNAME/NJT_PASSWORD are unset); no realtime poll is attempted.",
    ),
    true,
  );
  // A WARMING cache answers 503 too, and that one IS worth surfacing, which is why
  // the match is on the words rather than on the status.
  assert.equal(isNotConfigured("Feed cache is warming up; try again in a few seconds."), false);
  assert.equal(isNotConfigured("HTTP 502"), false);
  assert.equal(isNotConfigured("timed out"), false);
  assert.equal(isNotConfigured(null), false);
  assert.equal(isNotConfigured(undefined), false);
});

test("the live region calls NJ Transit by name, never by its payload key", () => {
  // The guard is SOURCE_WORDS.njt, and nothing exercised it: deleting the entry left
  // all 183 node tests and the whole e2e suite green while a screen reader was told
  // "Live data delayed for njt." That is the same defect the railroads' own entry was
  // added for, one source later.
  assert.equal(statusAnnouncement([], ["njt|njt"]), "Live data delayed for NJ Transit.");
  assert.equal(describeIdentity("njt|njt"), "NJ Transit");
  assert.equal(statusAnnouncement(["njt|njt"], []), "Live data current again for NJ Transit.");
});

/* ==================================================================
   MR1: the feed strip, the note, and the theme
   ==================================================================

   The pure half of the map redesign's stage 1. What is here is everything the header decides
   from a count, an age and a hidden set; what is NOT here is the DOM wiring, which is in
   systems/shared.js and is covered by the hermetic e2e suite because it needs a browser.

   Separate require, additive, leaving the blocks above untouched. */
const {
  FEEDS, feedDotState, feedTooltip, feedStripModel, statusNoteText, themeChoice, nextTheme,
  // MR5 (Q2): the state's own words, lifted out of the tooltip, and the popup footer that says them.
  feedStateWords, popupFreshHtml,
} = require("./helpers.js");
// FEED_STALE_AFTER_S is already in this file's top import block; named here so the threshold
// assertions below read as what they are.
const FEED_STALE_S = require("./helpers.js").FEED_STALE_AFTER_S;

test("MR1: the feed strip is the design's eight feeds, in the design's order", () => {
  // THE ORDER IS THE SPEC'S, not an alphabetisation and not the old checkbox order. The
  // handoff's row 2 lists them in this order and the whole strip reads as one sentence about
  // the region: the two city systems, the three commuter railroads, then the three smaller
  // operators. Pinned as a list because a reordering is invisible to every other test.
  assert.deepEqual(
    FEEDS.map((f) => f.key),
    ["subway", "buses", "lirr", "mnr", "njt", "path", "ferry", "airtrain"],
  );
  assert.deepEqual(
    FEEDS.map((f) => f.name),
    ["Subway", "Buses", "LIRR", "Metro-North", "NJ Transit", "PATH", "Ferry", "AirTrain"],
  );
  // LIRR AND METRO-NORTH ARE TWO FEEDS INSIDE ONE SOURCE, which is the reason this table
  // exists at all: the old panel had one "Railroads" checkbox for both, and the freshness
  // index, the status line and the C6 dimming specs have always treated them separately.
  const railroads = FEEDS.filter((f) => f.source === "railroads");
  assert.deepEqual(railroads.map((f) => f.system), ["LIRR", "MNR"]);
  // And exactly one feed has no source behind it.
  assert.deepEqual(FEEDS.filter((f) => f.source == null).map((f) => f.key), ["airtrain"]);
  // Every feed carries exactly one kind of leading mark: a colour tick or an agency glyph,
  // never both and never neither.
  for (const feed of FEEDS) {
    assert.equal(
      Boolean(feed.tick) !== Boolean(feed.glyph),
      true,
      `${feed.key} must carry a tick or a glyph, not both and not neither`,
    );
  }
  assert.deepEqual(
    FEEDS.filter((f) => f.glyph).map((f) => f.glyph),
    ["L", "M", "NJ"],
  );
});

test("MR1: the freshness dot is the FEED's, and a null age is never live", () => {
  // Three states, and they are about the poll rather than about the observations inside it:
  // the v3 brief says in as many words that a feed can be green while a third of its trains
  // are drawn dimmed, and that this is correct.
  assert.equal(feedDotState({ age: 12 }), "live");
  assert.equal(feedDotState({ age: FEED_STALE_S - 1 }), "live");
  // The threshold is the app's, not a second copy of it.
  assert.equal(feedDotState({ age: FEED_STALE_S }), "stale");
  assert.equal(feedDotState({ age: 600 }), "stale");
  // A FEED WITH NO REALTIME SOURCE IS SCHEDULED-ONLY, whatever else is true of it. AirTrain
  // is the one, and passing it an age would be a caller bug rather than a state.
  assert.equal(feedDotState({ scheduled: true }), "scheduled");
  assert.equal(feedDotState({ scheduled: true, age: 5 }), "scheduled");
  // AND A NULL AGE IS NOT LIVE. A feed that has never decoded has no freshness to report,
  // and "live" is the one answer that would be a lie. The status line calls this state "not
  // reporting"; the dot has a smaller vocabulary and says stale.
  assert.equal(feedDotState({ age: null }), "stale");
  assert.equal(feedDotState({}), "stale");
});

/* ===== MR5, ruling Q2: the state's words, said one way on two surfaces ====================

   THE TOOLTIP'S CLAUSE, LIFTED OUT SO THE POPUP FOOTER CAN HAVE IT. Section 5 gives every popup a
   freshness footer, and the ruling is that its square IS the feed strip's dot at the popup, with
   "its accessible name from the same helper the strip's dot uses so the state is said one way on
   both surfaces". feedTooltip could not BE that helper: its words end in what pressing the button
   will do, and a popup has no button. So the state's half came out, and the tooltip is now that
   half plus the action.

   THE FIRST TEST BELOW IS THEREFORE A REFACTOR PROOF and is deliberately redundant with the
   tooltip test that follows it: the same three design examples, asserted through the composition,
   so a change to feedStateWords that moved the tooltip fails twice and visibly. */
test("MR5 Q2: feedStateWords is the tooltip's own clause, and coins nothing", () => {
  // The three the design wrote down, without the action.
  assert.equal(feedStateWords({ state: "live", age: 12 }), "Live · 12s");
  assert.equal(feedStateWords({ state: "stale", age: 360 }), "As of 6m ago");
  assert.equal(feedStateWords({ state: "scheduled" }), "Scheduled");
  // And the fourth the design's examples could not show, because all three of them had an age.
  assert.equal(feedStateWords({ state: "stale", age: null }), "Not reporting");

  /* THE COMPOSITION IS THE CLAIM: every tooltip is these words plus the action, so no third
     spelling of a feed's state can exist without this failing. Asserted over the whole matrix
     rather than three examples, because "they happen to agree on the examples" is what a copy
     looks like from the outside. */
  for (const state of ["live", "stale", "scheduled"]) {
    for (const age of [8, 360, null]) {
      for (const hidden of [false, true]) {
        const verb = hidden ? "show" : "hide";
        assert.equal(
          feedTooltip({ name: "Subway", state, age, hidden }),
          `${feedStateWords({ state, age })} · ${verb} Subway`,
          `${state}/${age}/${hidden}`,
        );
      }
    }
  }
});

test("MR5 Q2: the popup's footer is the strip's dot, with no word for live", () => {
  const square = (state) => `<span class="fresh-dot" data-state="${state}" aria-hidden="true"></span>`;

  /* PRESENT IN ALL THREE STATES, which is the half a "show it when it is bad" footer gets wrong: a
     rider cannot tell "this feed is live" from "this popup forgot to say" unless the mark is always
     there. So the square is asserted for each state before anything about the words. */
  for (const [state, age] of [["live", 9], ["stale", 370], ["scheduled", null]]) {
    assert.match(popupFreshHtml({ state, age }), /^<div class="fresh">/);
    assert.ok(popupFreshHtml({ state, age }).includes(square(state)), `${state} draws its square`);
  }

  /* NO TEXT WHEN LIVE, because this app has no word for "live" on any surface (memo D9, silence
     means current), and the README's "LIVE · UPDATED 12S AGO" is the sentence that rule forbids. It
     is still SAID, through A1's visually-hidden class, so a screen reader gets the state exactly
     where an eye gets the square, WHERE NOTHING HAS SAID IT YET: the suppression case below takes
     it out of that channel too, and ruling R2's precedence is asserted at the end of this test. */
  const live = popupFreshHtml({ state: "live", age: 9 });
  assert.match(live, /<span class="visually-hidden">Live · 9s<\/span>/);
  assert.ok(!/>Live/.test(live.replace(/<span class="visually-hidden">[^<]*<\/span>/, "")), "no visible live text");

  // The two states that DO have something to show say the app's own strings, not a coined pair.
  assert.ok(popupFreshHtml({ state: "stale", age: 370 }).endsWith("As of 6m ago</div>"));
  assert.ok(popupFreshHtml({ state: "scheduled" }).endsWith("Scheduled</div>"));
  assert.ok(popupFreshHtml({ state: "stale", age: null }).endsWith("Not reporting</div>"));

  /* AND IT IS vehicleStaleLine's RULE, CARRIED OVER. Every vehicle popup already ended in that
     line, which prints the system's age and withholds itself when the vehicle's own position has
     already stated an age at least as old: an observation's age and a feed's differ by the
     provider's lag, so saying both is saying two ages about one train. The footer inherits that,
     with one difference the ruling requires: the SQUARE is never withheld, only the words.

     AND RULING R2 MADE THAT LITERALLY TRUE. Until R2 the withheld words went into the same
     visually-hidden span the live state uses, so they were withheld from an EYE and a screen reader
     heard the age twice, which is the thing the paragraph above forbids. Asserted as the whole
     markup rather than as an absence, because a footer that printed the suppressed words visibly
     would satisfy a bare absence and nothing else here would notice. */
  const older = { kind: "aged", words: "live GPS, as of 7m ago", age: 420 };
  const younger = { kind: "aged", words: "live GPS, as of 1m ago", age: 60 };
  const suppressed = popupFreshHtml({ state: "stale", age: 370, position: older });
  assert.ok(suppressed.includes(square("stale")), "the square survives suppression");
  assert.equal(
    suppressed,
    `<div class="fresh">${square("stale")}</div>`,
    "the position said the age, so the footer says it in neither channel",
  );
  assert.ok(
    popupFreshHtml({ state: "stale", age: 370, position: younger }).endsWith("As of 6m ago</div>"),
    "a position that stated a YOUNGER age does not suppress the feed's older one",
  );
  // A station popup passes no position, so nothing is ever suppressed on one.
  assert.ok(popupFreshHtml({ state: "stale", age: 370 }).endsWith("As of 6m ago</div>"));

  // A surface with no feed behind it gets no footer rather than an empty square: AirTrain is NOT
  // that case, because its feed row has no source and feedDotState calls it schedule-only.
  assert.equal(popupFreshHtml({ state: null }), "");
  assert.equal(popupFreshHtml({}), "");
  assert.equal(feedDotState({ scheduled: true }), "scheduled");

  /* THE PRECEDENCE, WHICH IS RULING R2 ITSELF AND HAS ONE OTHER PIN IN THE REPO. A LIVE feed under a
     position that already stated an older age is the case that distinguishes `said` winning from
     `live` winning: written the other way round, the footer puts "Live · 9s" in the tree beside a
     Position row that has just said "as of 7m ago", which is two ages about one train and is what
     the ruling closed. positions.test.js's loop holds the same case; each tier holds it alone. */
  assert.equal(
    popupFreshHtml({ state: "live", age: 9, position: older }),
    `<div class="fresh">${square("live")}</div>`,
    "a live feed whose position already stated an older age says nothing in either channel",
  );

  // Escaped, like every other builder in this file: the state reaches a data attribute.
  assert.ok(!popupFreshHtml({ state: 'x"><img>', age: null }).includes('"><img>'));
});

test("MR1: the tooltip says the state and the ACTION, in the design's words", () => {
  // The handoff's three examples, verbatim.
  assert.equal(
    feedTooltip({ name: "Subway", state: "live", age: 12 }),
    "Live · 12s · hide Subway",
  );
  assert.equal(
    feedTooltip({ name: "Metro-North", state: "stale", age: 360 }),
    "As of 6m ago · hide Metro-North",
  );
  assert.equal(
    feedTooltip({ name: "NJ Transit", state: "scheduled" }),
    "Scheduled · hide NJ Transit",
  );
  // THE VERB IS THE ACTION, NOT THE STATE, which none of the three examples could show
  // because all three are of a showing feed. A tooltip reading "hide" on a button that shows
  // would be wrong in the one place a rider looks to find out what pressing it does.
  assert.equal(
    feedTooltip({ name: "LIRR", state: "live", age: 8, hidden: true }),
    "Live · 8s · show LIRR",
  );
  assert.equal(
    feedTooltip({ name: "AirTrain", state: "scheduled", hidden: true }),
    "Scheduled · show AirTrain",
  );
  // AND A FEED WITH NO AGE SAYS SO rather than saying "As of null ago". The word is the
  // status line's own for the same state.
  assert.equal(
    feedTooltip({ name: "PATH", state: "stale", age: null }),
    "Not reporting · hide PATH",
  );
  // The age is humanizeAge's, so the strip and the status line never word one age two ways.
  assert.equal(feedTooltip({ name: "Ferry", state: "stale", age: 7200 }), "As of 2h ago · hide Ferry");
});

test("MR1: aria-pressed and the OFF mark come from one decision and cannot disagree", () => {
  const model = feedStripModel({
    counts: { subway: 1234, buses: 56, lirr: 7, mnr: 3, njt: 4, path: 2, ferry: 3 },
    ages: { subway: 12, buses: 20, lirr: 400, mnr: null, njt: 30, path: 40, ferry: 50 },
    hidden: new Set(["lirr", "ferry"]),
  });
  const by = Object.fromEntries(model.map((e) => [e.key, e]));

  // THE INVARIANT THAT KILLS MUTATIONS M1 AND M2: `pressed` (what aria-pressed is written
  // from) and `off` (what the strike and the visible mark are drawn from) are one boolean
  // read two ways. A stage that updated one without the other would have to break this.
  for (const entry of model) {
    assert.equal(entry.pressed, !entry.off, `${entry.key}: pressed and off disagree`);
  }
  assert.deepEqual(model.filter((e) => e.off).map((e) => e.key), ["lirr", "ferry"]);
  assert.deepEqual(model.filter((e) => e.pressed).map((e) => e.key), [
    "subway", "buses", "mnr", "njt", "path", "airtrain",
  ]);

  // The counts come from the caller's registries and are localised, so a five-figure bus
  // fleet reads as a number rather than as a digit run.
  assert.equal(by.subway.count, (1234).toLocaleString());
  assert.equal(by.lirr.count, "7");
  // AIRTRAIN HAS NO COUNT, and null rather than "0": it has no vehicles at all, and 0 would
  // be a claim about a fleet that does not exist.
  assert.equal(by.airtrain.count, null);
  assert.equal(by.airtrain.dot, "scheduled");

  // The dots follow the ages, per feed, including the two railroads separately.
  assert.equal(by.subway.dot, "live");
  assert.equal(by.lirr.dot, "stale");
  assert.equal(by.mnr.dot, "stale"); // null age: never decoded, so not live
  // And a hidden feed's tooltip offers to show it.
  assert.match(by.lirr.title, /show LIRR$/);
  assert.match(by.subway.title, /hide Subway$/);

  // A feed the caller said nothing about is not invented: no count, and an age of null.
  const quiet = feedStripModel({});
  assert.equal(quiet.length, FEEDS.length);
  for (const entry of quiet) {
    assert.equal(entry.count, null);
    assert.equal(entry.pressed, true);
    assert.equal(entry.off, false);
  }
});

test("MR1: the trailing note is the status line's tail, verbatim", () => {
  // ONE JOIN, TWO SURFACES. statusLineText composes the pre-MR1 line and statusNoteText is
  // the note the header renders; the note must BE the line's tail, because the alternative
  // is two functions that can word the same trouble two ways. This is the differential that
  // keeps the retained composition honest rather than decorative.
  const problems = [
    "railroad: MNR as of 6m ago; LIRR 24 trains not shown, last seen over 10m ago",
    "trains: ACE group as of 10m ago",
  ];
  const note = statusNoteText(problems);
  const line = statusLineText({ counts: "2 buses", clock: "8:01:23 AM", problems });
  assert.equal(line, `2 buses · 8:01:23 AM: ${note}`);
  assert.ok(line.endsWith(note), "the note is the line's tail, character for character");

  // Nothing is truncated or abbreviated, at any length. The F01 world's real sentence is the
  // longest this app produces and it survives whole.
  const f01 =
    "railroad: MNR as of 6m ago; LIRR 24 trains not shown, last seen over 10m ago; " +
    "MNR position age unavailable";
  assert.equal(statusNoteText([f01]), f01);

  // Falsy entries are dropped rather than rendered as empty segments, so a source with
  // nothing to say does not contribute a stray separator.
  assert.equal(statusNoteText([null, "a: b", "", undefined, "c: d"]), "a: b; c: d");
  // AND A HEALTHY DAY IS EMPTY, which the v3 brief singles out: "nothing at all on a healthy
  // day, which is the common case and must not get noisier".
  assert.equal(statusNoteText([]), "");
  assert.equal(statusNoteText(), "");
  assert.equal(statusNoteText(null), "");
});

test("MR1: the theme renders with storage empty, and an unrecognised value is no value", () => {
  // THE PAGE MUST RENDER WITH STORAGE EMPTY. index.html authors data-theme="light", so that
  // is the second-chance answer rather than a guess, and a null store (nothing saved, or a
  // private window where the read threw and the caller passed null) lands on it.
  assert.equal(themeChoice(null, "light"), "light");
  assert.equal(themeChoice(undefined, "light"), "light");
  // A stored choice wins over the markup, in both directions.
  assert.equal(themeChoice("dark", "light"), "dark");
  assert.equal(themeChoice("light", "dark"), "light");
  // AN UNRECOGNISED VALUE IS TREATED AS NO VALUE rather than passed through. A stored "Dark"
  // or "" reaching the root attribute would match neither token block and leave the page in
  // the unqualified light set by accident instead of by choice.
  assert.equal(themeChoice("Dark", "dark"), "dark");
  assert.equal(themeChoice("", "dark"), "dark");
  assert.equal(themeChoice("solarized", "light"), "light");
  // No markup value either: light, which is what the stylesheet's unqualified block is.
  assert.equal(themeChoice(null, null), "light");
  assert.equal(themeChoice(null, "nonsense"), "light");
  // And the toggle has exactly two destinations.
  assert.equal(nextTheme("light"), "dark");
  assert.equal(nextTheme("dark"), "light");
  assert.equal(nextTheme(null), "dark"); // an absent attribute reads as light, so pressing darkens
});
