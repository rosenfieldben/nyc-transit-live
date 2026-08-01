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
  feedAgeLine,
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
  railroadColor,
  isPlacedRailroad,
  orderedRailroadBuckets,
  railroadArrivalsHtml,
  formatRailroadHead,
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
  ROUTE_MAX_SLICE,
  RAILROAD_ROUTE_MAX_SLICE,
  FEED_STALE_AFTER_S,
  ingestSystems,
  systemAges,
  systemStaleAts,
  staleAge,
  markerOpacity,
  glideClock,
  stalePopupLine,
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
  const html = railroadArrivalsHtml(station, body, 40, () => "Bab<script>Branch");
  assert.ok(html.includes("Bab&lt;script&gt;Branch"));
  assert.ok(!html.includes("<script>"));
  // Absent name (resolver returns null) just omits the label, no crash.
  const plain = railroadArrivalsHtml(station, body, 40, () => null);
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
  const html = pathTrainPopupHtml(train, "Newark - World Trade Center", "#d93a30");
  assert.ok(html.includes("Newark - World Trade Center"));
  assert.ok(html.includes("Next stop: Journal Square"));
  assert.ok(html.includes("To New Jersey"));
  assert.ok(html.includes("scheduled position (no GPS)"));
  assert.ok(html.includes("#d93a30"));
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

test("formatRailroadHead prefers the route name, falls back to route id, then system", () => {
  assert.equal(formatRailroadHead("LIRR", "1", "Babylon Branch"), "LIRR · Babylon Branch");
  assert.equal(formatRailroadHead("LIRR", "1", null), "LIRR route 1");
  assert.equal(formatRailroadHead("MNR", null, null), "MNR");
  // Returns plain text (the caller escapes); it does not itself inject markup.
  assert.equal(formatRailroadHead("MNR", "3", "New Haven"), "MNR · New Haven");
});

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

test("railroadColor is deterministic, from the palette, and null-safe", () => {
  assert.equal(railroadColor("3"), railroadColor("3")); // deterministic
  assert.match(railroadColor("3"), /^#[0-9a-f]{6}$/);
  assert.equal(railroadColor(null), "#607d8b"); // neutral default
  assert.equal(railroadColor(""), "#607d8b");
  // A railroad route id is colored on its own scale, not the subway's.
  assert.notEqual(railroadColor("1"), lineColor("1"));
});

test("isPlacedRailroad keys off stop_id (the authoritative placed-vs-GPS signal)", () => {
  // A GPS train: the decode emits stop_id/stop_name null even though it has a
  // real position, so it is NOT placed.
  assert.equal(isPlacedRailroad({ stop_id: null, stop_name: null, next_time: null }), false);
  // A normal placed train (timed next stop).
  assert.equal(isPlacedRailroad({ stop_id: "12", stop_name: "Jamaica", next_time: 1000 }), true);
  // The case the old time/direction-based check missed: a no-times MNR placement
  // has next_time/prev_lat/direction all null but a real stop_id, and must still
  // read as placed so its marker, label, and next-stop popup line stay correct.
  assert.equal(
    isPlacedRailroad({ stop_id: "1", stop_name: "Grand Central", next_time: null, prev_lat: null, direction: null }),
    true,
  );
});

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

test("feedAgeLine is empty while fresh and shows 'as of Xm ago' once stale", () => {
  const now = 10_000;
  // Fresh (under the threshold) and a null fetched_at both render nothing, so a live
  // popup is unchanged.
  assert.equal(feedAgeLine(now - 30, now), "");
  assert.equal(feedAgeLine(null, now), "");
  // Past FEED_STALE_AFTER_S (a failed refresh keeping last-known rows), the age line
  // appears: seconds under two minutes, whole minutes above.
  assert.match(feedAgeLine(now - 100, now), /popup-stale/);
  assert.match(feedAgeLine(now - 100, now), /as of 100s ago/);
  assert.match(feedAgeLine(now - 200, now), /as of 3m ago/);
})

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
  assert.match(html, /Jamaica: every ~4 min/);
  assert.match(html, /\(scheduled\)/);
  assert.doesNotMatch(html, /Howard Beach/); // 160565 is served only by the Jamaica branch
});

test("airtrainStationPopupHtml: multi-branch station lists every serving branch", () => {
  const station = { id: "160564", name: "Federal Circle-Station C" };
  const html = airtrainStationPopupHtml(station, AIRTRAIN_ROUTES, 720);
  assert.match(html, /Jamaica: every ~4 min/);
  assert.match(html, /Howard Beach: every ~4 min/);
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
  // None of the CSS classes the live-arrivals countdown popups use.
  for (const cls of ["arr-dir", "arr-badge", "arr-none"]) {
    assert.ok(!html.includes(cls), `must not use live-arrivals class ${cls}`);
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
  assert.ok(html.includes("#00839c"));
  assert.ok(html.includes("Boat H201"));
  assert.ok(html.includes("Under way"));
  assert.ok(html.includes("NYC Ferry"));
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
  // Docked boat: no speed line at all (dock jitter is noise, not motion).
  assert.ok(!html.includes("kn"));
});

test("ferryBoatPopupHtml labels a null-route boat Unassigned and omits an unknown status", () => {
  const html = ferryBoatPopupHtml({ label: "H099", status: null }, null, FERRY_FALLBACK_COLOR);
  assert.ok(html.includes("Unassigned"));
  assert.ok(html.includes(FERRY_FALLBACK_COLOR));
  assert.ok(html.includes("Boat H099"));
  // Unknown status -> no status line at all (ferryStatusText returned null).
  assert.ok(!html.includes("At dock") && !html.includes("Under way"));
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
  assert.ok(html.includes("#00839c") && html.includes("#ffd100")); // route-colored headings
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

test("C2 systemAges ages each system separately and keeps the upstream lag a source floor", () => {
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
  assert.equal(ages.LIRR, 5); // upstream lag is the floor, so a fresh system reads 5
  assert.equal(ages.MNR, 400); // its own poll age, which the envelope's hides
  assert.equal(ages.FUTURE, null);
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
  assert.equal(staleness(railroads, now), "railroad: MNR as of 6m ago");
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

test("C2 stalePopupLine renders the shared age line only once stale", () => {
  assert.equal(stalePopupLine(null), "");
  assert.equal(stalePopupLine(10), "");
  assert.equal(stalePopupLine(240), '<div class="popup-stale">as of 4m ago</div>');
  // Same markup and wording as the arrivals-body line, which is the point of sharing
  // the renderer.
  assert.equal(stalePopupLine(240), feedAgeLine(10_000 - 240, 10_000));
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
  assert.equal(subway.ageSeconds, 3);
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
    { fetched_at: now, routes: { Astoria: [{ route_id: "AS", arrival: now - 30, departure: now + 360 }] } },
    now,
  );
  assert.deepEqual(ferry.buckets.map((b) => b.name), ["Astoria"]);
  assert.equal(ferry.buckets[0].rows[0].mode, "departing");
  assert.equal(ferry.buckets[0].rows[0].seconds, 360);
  // No fetched_at is "unknown", not "fresh": null, so the caller can tell them apart.
  assert.equal(shapeStationArrivals("subway", { directions: {} }, now).ageSeconds, null);
});

test("arrivalSentence reads as a sentence, and names the instant honestly", () => {
  const now = 1_700_000_000; // 2023-11-14T22:13:20Z, 5:13 PM in New York
  const rail = shapeStationArrivals(
    "railroad",
    { fetched_at: now, directions: { Inbound: [{ route_id: "5", train_num: "8412", arrival: now + 240 }] } },
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
    { fetched_at: now, directions: { Northbound: [{ route_id: "1", arrival: now + 10 }] } },
    now,
  );
  assert.equal(arrivalSentence(subway.buckets[0].rows[0]), "1 train now, 5:13 PM arrival");
  // Ferry: "departs" and a DEPARTURE label, because that is the field being counted.
  const ferry = shapeStationArrivals(
    "ferry",
    { fetched_at: now, routes: { Astoria: [{ route_id: "AS", arrival: now - 30, departure: now + 360 }] } },
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

  // Railroad: the popup's own head builder, and the GPS-versus-scheduled clause,
  // which is the part that tells a rider how much to trust the position.
  assert.equal(
    railroadTrainName({ system: "LIRR", route_id: "10", train_num: "2751", direction: "Eastbound" }, "Babylon Branch"),
    "LIRR Babylon Branch, train 2751, Eastbound, live GPS",
  );
  // A PLACED train (it carries stop_id) says so, and only a placed train has a next
  // stop to give.
  assert.equal(
    railroadTrainName({ system: "MNR", route_id: "1", train_num: "8801", stop_id: "1", stop_name: "Grand Central" }, "Hudson"),
    "Metro-North Hudson, train 8801, next stop Grand Central, scheduled position, no GPS",
  );
  // NO MIDDOT. The popup head joins system and route with "·", which is a visual
  // separator; spoken, it is noise or the words "middle dot". Same fields, spoken
  // shape. And "MNR" becomes the word a rider uses, as the A1 panel already does.
  assert.ok(!railroadTrainName({ system: "MNR", route_id: "1" }, "Hudson").includes("\u00b7"));
  assert.equal(railroadTrainName({ system: "LIRR", route_id: "10" }), "LIRR route 10, live GPS");

  // PATH: always a scheduled position, which the popup states and the name repeats.
  assert.equal(
    pathTrainName({ route_id: "862", stop_name: "Grove St", direction: "To Newark" }, "Newark - World Trade Center"),
    "Newark - World Trade Center, PATH, next stop Grove St, To Newark, scheduled position, no GPS",
  );
  // No route name resolved yet: formatPathHead's fallback, not a blank.
  assert.equal(pathTrainName({ route_id: "862" }), "PATH route 862, PATH, scheduled position, no GPS");

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
