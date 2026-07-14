// Run with: node --test "frontend/*.test.js"  (from the repo root)
// Tests the pure helpers shared with the browser via plain <script> loading.
// NOTE: minClockOffset is module state that only ratchets downward, so the
// staleness tests run in a deliberate order (node:test runs them serially).

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  esc,
  routeColor,
  lineColor,
  staleness,
  emptyFeedDecision,
  noteClockOffset,
  formatCountdown,
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

// `now` is passed explicitly for determinism; minClockOffset is null here
// (nothing calls noteClockOffset before these), so the poll-age term is exactly
// now - fetchedAt.
test("staleness flags upstream lag (skew-free) at/over the threshold", () => {
  const now = 10_000;
  // Fresh: content 15s old at a poll 5s ago.
  assert.equal(staleness({ label: "buses", fetchedAt: now - 5, feedTimestamp: now - 15 }, now), null);
  // Upstream stale: content was 100s old at the (recent) last poll. The diff of
  // the two server timestamps drives this, so the browser clock can't skew it.
  assert.equal(
    staleness({ label: "buses", fetchedAt: now - 5, feedTimestamp: now - 105 }, now),
    "buses data 100s old",
  );
})

test("staleness flags a stuck backend via poll age even when upstream lag is tiny", () => {
  const now = 10_000;
  // Backend stopped polling 200s ago; content was fresh (5s) at that last poll.
  // Upstream-lag alone (5s) would stay silent — poll-age (200s) must flag it.
  assert.equal(
    staleness({ label: "trains", fetchedAt: now - 200, feedTimestamp: now - 205 }, now),
    "trains data 3m old",
  );
  // Works with a missing feed_timestamp too (upstream lag unknown -> 0).
  assert.equal(
    staleness({ label: "buses", fetchedAt: now - 200, feedTimestamp: null }, now),
    "buses data 3m old",
  );
})

test("staleness is null when fresh or never fetched", () => {
  const now = 10_000;
  assert.equal(staleness({ label: "buses", fetchedAt: null, feedTimestamp: now }, now), null);
  assert.equal(staleness({ label: "buses", fetchedAt: now - 5, feedTimestamp: now - 5 }, now), null);
  assert.equal(staleness({ label: "buses", fetchedAt: now - 5, feedTimestamp: null }, now), null);
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
