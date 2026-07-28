// C6 PR 1: the one frontend seam, proven inert.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// The claim PR 1 makes about this file is narrow and absolute: without the
// companion flag parameter, the staleness thresholds are exactly what they were,
// and the parse is not a general config channel. Both halves are tested here,
// because "a query parameter can change how the live page renders" is the kind of
// sentence that deserves a test rather than a promise.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  thresholdOverrides,
  CONTRACT_FLAG_PARAM,
  FEED_STALE_AFTER_S,
  ALERTS_STALE_AFTER_S,
} = require("./helpers.js");

// The production values, written out here rather than imported from anywhere, so
// a drive-by edit to either constant fails THIS test by name.
const PRODUCTION_FEED_STALE_AFTER_S = 90;
const PRODUCTION_ALERTS_STALE_AFTER_S = 300;

test("the thresholds are the production values when nothing sets them", () => {
  // node has no `location`, which is the un-flagged case by construction: this is
  // the same code path a browser takes on a URL with no query string at all.
  assert.equal(FEED_STALE_AFTER_S, PRODUCTION_FEED_STALE_AFTER_S);
  assert.equal(ALERTS_STALE_AFTER_S, PRODUCTION_ALERTS_STALE_AFTER_S);
});

test("no flag means no override, however the parameters are spelled", () => {
  // Each of these is a URL a visitor could plausibly arrive on, by sharing a link
  // or by hand. None of them may move a threshold.
  for (const search of [
    "",
    "?",
    "?feedStaleAfterS=5",
    "?alertsStaleAfterS=5",
    "?feedStaleAfterS=5&alertsStaleAfterS=5",
    "?contract=0&feedStaleAfterS=5",
    "?contract=true&feedStaleAfterS=5",
    "?contract=&feedStaleAfterS=5",
    "?Contract=1&feedStaleAfterS=5", // the flag name is case sensitive
    "?xcontract=1&feedStaleAfterS=5",
  ]) {
    assert.deepEqual(thresholdOverrides(search), {}, `search ${JSON.stringify(search)}`);
  }
});

test("the flag alone changes nothing either", () => {
  assert.deepEqual(thresholdOverrides(`?${CONTRACT_FLAG_PARAM}=1`), {});
});

test("with the flag, exactly the two named numeric parameters are read", () => {
  assert.deepEqual(thresholdOverrides("?contract=1&feedStaleAfterS=5&alertsStaleAfterS=8"), {
    feed: 5,
    alerts: 8,
  });
  assert.deepEqual(thresholdOverrides("?contract=1&feedStaleAfterS=5"), { feed: 5 });
  assert.deepEqual(thresholdOverrides("?contract=1&alertsStaleAfterS=8"), { alerts: 8 });
});

test("the parse is not a general config channel", () => {
  // THE PROPERTY WORTH GUARDING. The seam is acceptable because it reads two named
  // cosmetic numbers and nothing else. If it ever grew into "read any key from the
  // query string", it would stop being cosmetic, so the shape is pinned: unknown
  // keys contribute nothing, and the result carries only the two known ones.
  const result = thresholdOverrides(
    "?contract=1&feedStaleAfterS=5&apiBase=http://evil.invalid&pollMs=1&debug=1",
  );
  assert.deepEqual(Object.keys(result).sort(), ["feed"]);
});

test("garbage values are ignored rather than propagated", () => {
  // A non-number, a negative, or a zero must leave the production value in place,
  // because a threshold of 0 or NaN would render every source permanently stale
  // and a threshold below zero is meaningless.
  for (const value of ["abc", "-5", "0", "", "NaN", "Infinity", "1e400"]) {
    const result = thresholdOverrides(`?contract=1&feedStaleAfterS=${value}`);
    assert.deepEqual(result, {}, `value ${JSON.stringify(value)}`);
  }
});
