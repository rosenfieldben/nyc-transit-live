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
  // A non-number, a negative, a zero, or a sub-second value must leave the
  // production value in place: 0 and NaN would render every source permanently
  // stale, and so would 1e-9, which an earlier "value > 0" test accepted while its
  // own comment gave permanent staleness as the reason 0 was rejected.
  for (const value of ["abc", "-5", "0", "", "NaN", "Infinity", "1e400", "0.5", "1e-9", "5e-324"]) {
    const result = thresholdOverrides(`?contract=1&feedStaleAfterS=${value}`);
    assert.deepEqual(result, {}, `value ${JSON.stringify(value)}`);
  }
});

test("the override can only ever dim SOONER, never later", () => {
  // THE PROPERTY THE SAFETY ARGUMENT RESTS ON, and it used to be merely asserted.
  // An unbounded positive value RAISED the thresholds, so a crafted link could
  // suppress every staleness surface on the page and leave a visitor reading
  // hours-old positions as if they were live. Suppressing a disclosure is a
  // different act from accelerating one; only the second is cosmetic.
  for (const value of ["91", "300", "99999999", "1e6"]) {
    assert.deepEqual(
      thresholdOverrides(`?contract=1&feedStaleAfterS=${value}`),
      {},
      `feed value ${JSON.stringify(value)} must not raise the threshold`,
    );
  }
  for (const value of ["301", "99999999"]) {
    assert.deepEqual(
      thresholdOverrides(`?contract=1&alertsStaleAfterS=${value}`),
      {},
      `alerts value ${JSON.stringify(value)} must not raise the threshold`,
    );
  }
  // The production value itself is the ceiling and is accepted, so the boundary is
  // inclusive rather than off by one.
  assert.deepEqual(thresholdOverrides("?contract=1&feedStaleAfterS=90"), { feed: 90 });
  assert.deepEqual(thresholdOverrides("?contract=1&alertsStaleAfterS=300"), { alerts: 300 });
});
