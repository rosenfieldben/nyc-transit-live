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
  ROUTE_MAX_SLICE,
  RAILROAD_ROUTE_MAX_SLICE,
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
