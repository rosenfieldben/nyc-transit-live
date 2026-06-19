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
} = require("./helpers.js");

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

test("staleness uses the two server timestamps, not the browser clock", () => {
  // age = fetchedAt - feedTimestamp (both server-recorded); fresh < 90s -> null.
  assert.equal(staleness({ label: "buses", fetchedAt: 1000, feedTimestamp: 990 }), null);
  // Exactly at the threshold and beyond -> stale, formatted s then m.
  assert.equal(
    staleness({ label: "buses", fetchedAt: 1090, feedTimestamp: 1000 }),
    "buses data 90s old",
  );
  assert.equal(
    staleness({ label: "trains", fetchedAt: 1300, feedTimestamp: 1000 }),
    "trains data 5m old",
  );
  // The absolute magnitude of the timestamps is irrelevant — only their
  // difference matters, so a far-future browser clock can't change the verdict.
  assert.equal(staleness({ label: "buses", fetchedAt: 5_000_000_090, feedTimestamp: 5_000_000_000 }), "buses data 90s old");
});

test("staleness is null when a timestamp is missing or the feed is fresh", () => {
  assert.equal(staleness({ label: "buses", fetchedAt: null, feedTimestamp: 1000 }), null);
  assert.equal(staleness({ label: "buses", fetchedAt: 1000, feedTimestamp: null }), null);
  // A negative diff (clock quirk where content time is ahead of poll time) is
  // below threshold, so not flagged.
  assert.equal(staleness({ label: "buses", fetchedAt: 1000, feedTimestamp: 1100 }), null);
});

test("noteClockOffset accepts a timestamp without throwing", () => {
  // minClockOffset is internal (used by the map.js countdown, not staleness);
  // just confirm the exported helper is callable and null-safe.
  assert.doesNotThrow(() => noteClockOffset(Date.now() / 1000));
  assert.doesNotThrow(() => noteClockOffset(null));
});
