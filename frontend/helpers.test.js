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
