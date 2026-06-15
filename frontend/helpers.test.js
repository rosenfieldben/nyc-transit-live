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

test("staleness with no offset samples: fresh null, stale in s then m", () => {
  const now = Date.now() / 1000;
  assert.equal(staleness({ label: "buses", fetchedAt: null }), null);
  assert.equal(staleness({ label: "buses", fetchedAt: now - 20 }), null);
  assert.equal(staleness({ label: "buses", fetchedAt: now - 90 }), "buses data 90s old");
  assert.equal(staleness({ label: "trains", fetchedAt: now - 300 }), "trains data 5m old");
  // Server clock ahead of client: age clamps to 0, never negative.
  assert.equal(staleness({ label: "buses", fetchedAt: now + 500 }), null);
});

test("staleness corrects for client clock skew via the min offset", () => {
  const now = Date.now() / 1000;
  // Server clock 120s behind the client: every fetched_at looks 120s old.
  noteClockOffset(now - 120);
  assert.equal(staleness({ label: "buses", fetchedAt: now - 120 }), null);
  // Genuinely 90s of staleness on top of the skew is still detected.
  assert.equal(staleness({ label: "buses", fetchedAt: now - 210 }), "buses data 90s old");
});

test("a smaller offset sample tightens the baseline", () => {
  const now = Date.now() / 1000;
  noteClockOffset(now); // ~zero offset replaces the 120s sample
  assert.equal(staleness({ label: "buses", fetchedAt: now - 90 }), "buses data 90s old");
});
