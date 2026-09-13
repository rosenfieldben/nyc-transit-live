// 6.3: a vehicle's position, qualified by its own observation.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// THE HELPERS ARE INERT IN THIS COMMIT: no surface calls positionQualifier,
// observationAge or observationStaleAt yet, because the words, the per-observation
// dimming and the glide freeze have to land in the same commit as the backend gate that
// makes them true. So these pin what they WILL say before any surface is taught to say
// it, the way frontend/boards.test.js pinned the boards before 6.2 touched them.
//
// THE EXPECTATIONS ARE LITERAL STRINGS, section 3.2's vocabulary for a position as the
// implementation memo's word table (D9) settles it, one case per row of that table, in
// both forms the helper returns: `words`, and `compact`, the railroad popup's shorter
// form, which is the words themselves except for a placed row.
// Nothing here recomputes an answer from the helper it is checking.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const {
  positionQualifier,
  arrivalQualifier,
  observationAge,
  observationStaleAt,
  OBS_MAX_S,
  UNDATED_SYSTEMS,
  FEED_STALE_AFTER_S,
  humanizeAge,
  servedAge,
} = require("./helpers.js");

const NOW = 50_000;
// A vehicle row as the backend serves it since 6.1: the contract pair.
const row = (at, provenance) => ({ observed_at: at, provenance });
// LIRR's positions are age-gated; Metro-North's are not (section 3.3).
const lirr = { now: NOW, servedAt: NOW, system: "LIRR", gated: true };
const mnr = { now: NOW, servedAt: NOW, system: "MNR", gated: false };
const q = (r, board = lirr) => positionQualifier(r, board);
// An expected answer, spelled out: its compact form is its words unless one is given.
const said = (kind, words, compact = words) => ({ kind, words, compact });
const UNKNOWN_GPS = said("unknown", "live GPS, age unknown");

test("6.3 reported: 'live GPS' while fresh, and its age once it is not", () => {
  assert.equal(FEED_STALE_AFTER_S, 90, "the design's OBS_FRESH_S, with no override in node");
  // Fresh: the words the railroad popup says today, and kind "" so a surface that has
  // never said "live GPS" adds nothing.
  assert.deepEqual(q(row(NOW - 5, "reported")), said("", "live GPS"));
  assert.deepEqual(q(row(NOW - 89.9, "reported")), said("", "live GPS"));
  // At OBS_FRESH_S it is aged: >=, as staleAge draws the line everywhere.
  assert.deepEqual(q(row(NOW - 90, "reported")), said("aged", "live GPS, as of 90s ago"));
  // The oldest of the capture's eleven qualified vehicles, 593 s.
  assert.deepEqual(q(row(NOW - 593, "reported")), said("aged", "live GPS, as of 10m ago"));
  // The capture's oldest fix, 53676 s: the hours tier, never "895m".
  assert.deepEqual(q(row(NOW - 53676, "reported")), said("aged", "live GPS, as of 14h 55m ago"));
});

test("6.3 reported with no clock: said on a gated system, silent where the provider never dates", () => {
  // Clause (c): LIRR dates every position, so a null one is an anomaly said at the marker.
  assert.deepEqual(q(row(null, "reported")), UNKNOWN_GPS);
  assert.deepEqual(q(row(undefined, "reported")), UNKNOWN_GPS);
  assert.deepEqual(q({ provenance: "reported" }), UNKNOWN_GPS);
  // Metro-North dates none: "live GPS" and nothing more, because its status line says it.
  assert.deepEqual(q(row(null, "reported"), mnr), said("", "live GPS"));
});

test("6.3 an omitted `gated` is read off UNDATED_SYSTEMS, the set boards.test.js holds to the backend's", () => {
  const board = (system) => ({ now: NOW, servedAt: NOW, system });
  assert.deepEqual(q(row(null, "reported"), board("MNR")), said("", "live GPS"));
  assert.deepEqual(q(row(null, "reported"), board("LIRR")), UNKNOWN_GPS);
  // A system the set does not name is gated, the pessimistic default boardFreshness takes.
  assert.deepEqual(q(row(null, "reported"), board("buses")), UNKNOWN_GPS);
  assert.deepEqual(q(row(null, "reported"), board(undefined)), UNKNOWN_GPS);
  // THE SET DECIDES, NOT THE NAME: admit LIRR to it and LIRR goes quiet the same way.
  UNDATED_SYSTEMS.add("LIRR");
  try {
    assert.deepEqual(q(row(null, "reported"), board("LIRR")), said("", "live GPS"));
  } finally {
    UNDATED_SYSTEMS.delete("LIRR");
  }
  // An explicit `gated` is the caller's and wins over the set.
  assert.deepEqual(q(row(null, "reported"), { ...board("MNR"), gated: true }), UNKNOWN_GPS);
  assert.deepEqual([...UNDATED_SYSTEMS], ["MNR"]);
});

test("6.3 estimated: the new marker string, with its prediction's age once that is old", () => {
  assert.deepEqual(q(row(NOW - 4, "estimated")), said("estimated", "estimated from a prediction"));
  assert.deepEqual(q(row(NOW - 120, "estimated")), said("estimated", "estimated from a prediction, as of 2m ago"));
  assert.deepEqual(q(row(null, "estimated")), said("estimated", "estimated from a prediction, age unknown"));
  assert.deepEqual(q(row(null, "estimated"), mnr), said("estimated", "estimated from a prediction"));
});

test("6.3 placed: 'scheduled position (no GPS)', and the railroad popup's 'scheduled (no GPS)', aged alike", () => {
  // D9 keeps the railroad popup's compact form. It comes out of this same table, so that
  // popup cannot word a placed train's age differently from every other surface.
  assert.deepEqual(q(row(NOW - 4, "placed")), said("placed", "scheduled position (no GPS)", "scheduled (no GPS)"));
  // 887 s, the median age of the capture's timestamped trip updates.
  assert.deepEqual(
    q(row(NOW - 887, "placed")),
    said("placed", "scheduled position (no GPS), as of 15m ago", "scheduled (no GPS), as of 15m ago"),
  );
  assert.deepEqual(
    q(row(null, "placed")),
    said("placed", "scheduled position (no GPS), age unknown", "scheduled (no GPS), age unknown"),
  );
  // Metro-North's placed train is undated by policy: its word, and nothing about age.
  assert.deepEqual(q(row(null, "placed"), mnr), said("placed", "scheduled position (no GPS)", "scheduled (no GPS)"));
});

test("6.3 retained: always said, with its own observation's age, else its system's last poll", () => {
  assert.deepEqual(q(row(NOW - 30, "retained")), said("retained", "showing last known, as of 30s ago"));
  assert.deepEqual(q(row(NOW - 240, "retained")), said("retained", "showing last known, as of 4m ago"));
  // Metro-North serves no clock, and retention keeps the row's own (null), so every
  // retained Metro-North marker is as old as its system's last poll. That is the rule a
  // retained board row follows, and the same row reads the same words on both surfaces.
  const polled = { ...mnr, pollAge: 240 };
  assert.deepEqual(q(row(null, "retained"), polled), said("retained", "showing last known, as of 4m ago"));
  assert.equal(q(row(null, "retained"), polled).words, arrivalQualifier(row(null, "retained"), polled).words);
  // A clock of its own wins over the poll's, as on a board.
  assert.deepEqual(q(row(NOW - 30, "retained"), { ...lirr, pollAge: 240 }), said("retained", "showing last known, as of 30s ago"));
  // With neither age to give, the clause alone, as on a board.
  assert.deepEqual(q(row(null, "retained"), mnr), said("retained", "showing last known"));
});

test("6.3 missing or unknown provenance reads 'age unknown', however fresh its clock", () => {
  // Design 3.1's pessimistic fail-safe, in words: the enumeration's own `unknown`, no
  // provenance at all (a backend from before 6.1), and values no position can carry.
  for (const provenance of ["unknown", undefined, null, "live-gps", "at-station"]) {
    for (const board of [lirr, mnr]) {
      assert.deepEqual(q(row(NOW - 5, provenance), board), said("unknown", "age unknown"), String(provenance));
    }
  }
  assert.deepEqual(positionQualifier(null, lirr), said("unknown", "age unknown"));
  assert.deepEqual(positionQualifier({}, lirr), said("unknown", "age unknown"));
});

test("6.3 a position is aged from served_at, and keeps counting between polls", () => {
  const served = NOW;
  // Served 60 s old. A client clock 30 s BEHIND served_at adds nothing, and takes
  // nothing away: the age is served_at minus the stamp, exactly.
  assert.equal(observationAge(row(served - 60, "reported"), served, served - 30), 60);
  assert.deepEqual(q(row(served - 60, "reported"), { ...lirr, now: served - 30 }), said("", "live GPS"));
  // 30 s after served_at the same row has crossed OBS_FRESH_S with no new poll...
  assert.deepEqual(q(row(served - 60, "reported"), { ...lirr, now: served + 30 }), said("aged", "live GPS, as of 90s ago"));
  // ...and from then the kind holds still while the words count on.
  assert.deepEqual(q(row(served - 60, "reported"), { ...lirr, now: served + 300 }), said("aged", "live GPS, as of 6m ago"));
});

test("6.3 the served_at anchor alone decides the kind while the client clock is behind", () => {
  // Served 100 s old, and read with the client clock 30 s BEHIND served_at. Anchored,
  // the fix is 100 s old and aged. Aged from the client clock alone, which is what a
  // board without its servedAt would give, it is 70 s old and fresh: an old fix drawn
  // as a current "live GPS", which is F01 itself. Everywhere else in this file the
  // anchor and the bare clock agree, so it is here that every provenance that states an
  // age is held to the anchor.
  const early = { ...lirr, now: NOW - 30 };
  const old = NOW - 100;
  assert.deepEqual(q(row(old, "reported"), early), said("aged", "live GPS, as of 100s ago"));
  assert.deepEqual(q(row(old, "estimated"), early), said("estimated", "estimated from a prediction, as of 100s ago"));
  assert.deepEqual(
    q(row(old, "placed"), early),
    said("placed", "scheduled position (no GPS), as of 100s ago", "scheduled (no GPS), as of 100s ago"),
  );
  assert.deepEqual(q(row(old, "retained"), early), said("retained", "showing last known, as of 100s ago"));
});

test("6.3 observationAge is servedAge over the row's own clock, and null without one", () => {
  assert.equal(observationAge(row(1000, "reported"), 1600, 1630), servedAge(1000, 1600, 1630));
  assert.equal(observationAge(row(1000, "reported"), 1600, 1630), 630);
  assert.equal(observationAge(row(1000, "reported"), null, 1100), 100, "a response from before 6.1");
  for (const bad of [row(null, "reported"), row("1000", "reported"), row(Number.NaN, "reported"), {}, null]) {
    assert.equal(observationAge(bad, 1600, 1600), null);
  }
});

test("6.3 observationStaleAt: a fix is glided from for OBS_FRESH_S after it was taken, and no longer", () => {
  assert.equal(observationStaleAt(row(1000, "reported")), 1000 + FEED_STALE_AFTER_S);
  assert.equal(observationStaleAt(row(1000, "estimated")), 1090);
  assert.equal(observationStaleAt(row(1000, "placed")), 1090);
  for (const at of [null, undefined, "1000", Number.NaN, Infinity]) {
    assert.equal(observationStaleAt(row(at, "reported")), null, String(at));
  }
  assert.equal(observationStaleAt(null), null);
});

test("6.3 OBS_MAX_S is the backend's, read out of backend/cache.py", () => {
  // The way boards.test.js reads railroad.py: the mirror is held to the source.
  const src = readFileSync(join(__dirname, "..", "backend", "cache.py"), "utf8");
  const max = src.match(/^OBS_MAX_S = ([0-9.]+)$/m);
  assert.ok(max, "cache.py states OBS_MAX_S as a literal, where this test reads it");
  assert.equal(Number(max[1]), OBS_MAX_S);
  // And OBS_FRESH_S is the rider's threshold by name, so observationStaleAt's
  // FEED_STALE_AFTER_S is the backend's number too.
  assert.match(src, /^OBS_FRESH_S = FEED_STALE_AFTER_S$/m);
  const fresh = src.match(/^FEED_STALE_AFTER_S = (\d+)$/m);
  assert.ok(fresh, "cache.py states FEED_STALE_AFTER_S as a literal");
  assert.equal(Number(fresh[1]), FEED_STALE_AFTER_S);
  // What the suppression clause will print for it.
  assert.equal(humanizeAge(OBS_MAX_S), "10m");
});
