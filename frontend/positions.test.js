// 6.3: a vehicle's position, qualified by its own observation.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// WIRED SINCE THE GATE'S COMMIT: every vehicle surface on the map (the railroad, subway,
// bus, PATH, NJ Transit and ferry popups and accessible names) renders its position
// through positionQualifier, dims by markerAge, freezes its glide at glideDeadline, and
// the railroad glyph, glide and cross-link read the served provenance through
// railroadHollow, drawnFromPrediction and railroadAtItsStation. The first twelve tests
// below pinned the word table one commit before any surface said it, the way
// frontend/boards.test.js pinned the boards before 6.2 touched them; the rest pin the
// wiring's pure half. The browser half is tests/e2e/smoke.spec.js "C2j" to "C2n" and
// tests/e2e/announce.spec.js "A2j" to "A2l".
//
// THE EXPECTATIONS ARE LITERAL STRINGS, section 3.2's vocabulary for a position as the
// implementation memo's word table (D9) settles it, one case per row of that table, in
// every form the helper returns: `words`, `compact` (the railroad popup's shorter form),
// `spoken` (an accessible name's clause), and `age`, the age those words state.
// Nothing here recomputes an answer from the helper it is checking.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, readdirSync } = require("node:fs");
const { join } = require("node:path");

const {
  positionQualifier,
  arrivalQualifier,
  observationAge,
  observationStaleAt,
  OBS_MAX_S,
  UNDATED_SYSTEMS,
  FEED_STALE_AFTER_S,
  STALE_MARKER_OPACITY,
  humanizeAge,
  servedAge,
  ingestSystems,
  staleness,
  markerOpacity,
  glideClock,
  trainLatLng,
  positionBoard,
  markerAge,
  glideDeadline,
  observationGated,
  OBSERVATION_GATED,
  observationDimAge,
  AGE_UNKNOWN,
  staleAge,
  drawnFromPrediction,
  railroadHollow,
  railroadAtItsStation,
  withheldFix,
  vanishingFocusMessage,
  vanishingFocusPlan,
  positionLineHtml,
  positionClause,
  popupFreshHtml,
  feedStateWords,
  feedDotState,
  composeAnnouncements,
  railroadTrainName,
  subwayTrainName,
  pathTrainName,
  njtTrainName,
  busName,
  ferryBoatName,
  pathTrainPopupHtml,
  njtTrainPopupHtml,
  ferryBoatPopupHtml,
} = require("./helpers.js");

const NOW = 50_000;
// A vehicle row as the backend serves it since 6.1: the contract pair.
const row = (at, provenance) => ({ observed_at: at, provenance });
// LIRR's positions are age-gated; Metro-North's are not (section 3.3).
const lirr = { now: NOW, servedAt: NOW, system: "LIRR", gated: true };
const mnr = { now: NOW, servedAt: NOW, system: "MNR", gated: false };
const q = (r, board = lirr) => positionQualifier(r, board);
// An expected answer, spelled out: its compact and spoken forms are its words unless
// given, and it states no age unless given.
const said = (kind, words, { compact = words, spoken = words, age = null } = {}) => ({
  kind,
  words,
  compact,
  spoken,
  age,
});
// A placed row's three forms, which are the only ones that differ from each other.
const placed = (suffix = "", age = null) =>
  said("placed", `scheduled position (no GPS)${suffix}`, {
    compact: `scheduled (no GPS)${suffix}`,
    spoken: `scheduled position, no GPS${suffix}`,
    age,
  });
const UNKNOWN_GPS = said("unknown", "live GPS, age unknown");

test("6.3 reported: 'live GPS' while fresh, and its age once it is not", () => {
  assert.equal(FEED_STALE_AFTER_S, 90, "the design's OBS_FRESH_S, with no override in node");
  // Fresh: the words the railroad popup says today, and kind "" so a surface that has
  // never said "live GPS" adds nothing. A fresh position states no age.
  assert.deepEqual(q(row(NOW - 5, "reported")), said("", "live GPS"));
  assert.deepEqual(q(row(NOW - 89.9, "reported")), said("", "live GPS"));
  // At OBS_FRESH_S it is aged: >=, as staleAge draws the line everywhere.
  assert.deepEqual(q(row(NOW - 90, "reported")), said("aged", "live GPS, as of 90s ago", { age: 90 }));
  // The oldest of the capture's eleven qualified vehicles, 593 s.
  assert.deepEqual(q(row(NOW - 593, "reported")), said("aged", "live GPS, as of 10m ago", { age: 593 }));
  // The capture's oldest fix, 53676 s: the hours tier, never "895m".
  assert.deepEqual(
    q(row(NOW - 53676, "reported")),
    said("aged", "live GPS, as of 14h 55m ago", { age: 53676 }),
  );
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
  assert.deepEqual(
    q(row(NOW - 120, "estimated")),
    said("estimated", "estimated from a prediction, as of 2m ago", { age: 120 }),
  );
  assert.deepEqual(q(row(null, "estimated")), said("estimated", "estimated from a prediction, age unknown"));
  assert.deepEqual(q(row(null, "estimated"), mnr), said("estimated", "estimated from a prediction"));
});

test("6.3 placed: 'scheduled position (no GPS)', the railroad popup's 'scheduled (no GPS)', and the name's 'scheduled position, no GPS', aged alike", () => {
  // D9 keeps the railroad popup's compact form, and every accessible name has shipped
  // the comma form. All three come out of this same table, so no surface can word a
  // placed train's age differently from every other surface.
  assert.deepEqual(q(row(NOW - 4, "placed")), placed());
  // 887 s, the median age of the capture's timestamped trip updates.
  assert.deepEqual(q(row(NOW - 887, "placed")), placed(", as of 15m ago", 887));
  assert.deepEqual(q(row(null, "placed")), placed(", age unknown"));
  // Metro-North's placed train is undated by policy: its word, and nothing about age.
  assert.deepEqual(q(row(null, "placed"), mnr), placed());
});

test("6.3 retained: always said, with its own observation's age, else its system's last poll", () => {
  assert.deepEqual(q(row(NOW - 30, "retained")), said("retained", "showing last known, as of 30s ago", { age: 30 }));
  assert.deepEqual(q(row(NOW - 240, "retained")), said("retained", "showing last known, as of 4m ago", { age: 240 }));
  // Metro-North serves no clock, and retention keeps the row's own (null), so every
  // retained Metro-North marker is as old as its system's last poll. That is the rule a
  // retained board row follows, and the same row reads the same words on both surfaces.
  const polled = { ...mnr, pollAge: 240 };
  assert.deepEqual(q(row(null, "retained"), polled), said("retained", "showing last known, as of 4m ago", { age: 240 }));
  assert.equal(q(row(null, "retained"), polled).words, arrivalQualifier(row(null, "retained"), polled).words);
  // A clock of its own wins over the poll's, as on a board.
  assert.deepEqual(
    q(row(NOW - 30, "retained"), { ...lirr, pollAge: 240 }),
    said("retained", "showing last known, as of 30s ago", { age: 30 }),
  );
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
  assert.deepEqual(
    q(row(served - 60, "reported"), { ...lirr, now: served + 30 }),
    said("aged", "live GPS, as of 90s ago", { age: 90 }),
  );
  // ...and from then the kind holds still while the words count on.
  assert.deepEqual(
    q(row(served - 60, "reported"), { ...lirr, now: served + 300 }),
    said("aged", "live GPS, as of 6m ago", { age: 360 }),
  );
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
  assert.deepEqual(q(row(old, "reported"), early), said("aged", "live GPS, as of 100s ago", { age: 100 }));
  assert.deepEqual(
    q(row(old, "estimated"), early),
    said("estimated", "estimated from a prediction, as of 100s ago", { age: 100 }),
  );
  assert.deepEqual(q(row(old, "placed"), early), placed(", as of 100s ago", 100));
  assert.deepEqual(q(row(old, "retained"), early), said("retained", "showing last known, as of 100s ago", { age: 100 }));
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
  // What the suppression clause prints for it.
  assert.equal(humanizeAge(OBS_MAX_S), "10m");
});

/* ---------------- the wiring's pure half ---------------- */

test("6.3 positionBoard reads the row's own systems' poll, gated by the set, and the source's worst for a system it does not name", () => {
  const source = {
    servedAt: NOW,
    systems: ingestSystems(
      {
        systems: {
          LIRR: { fetched_at: NOW - 20, ok: true },
          MNR: { fetched_at: NOW - 400, ok: false, retained_since: NOW - 385 },
        },
      },
      "railroads",
    ),
  };
  // Each system's own served poll age, counting on with the client clock.
  assert.deepEqual(positionBoard(source, ["LIRR"], NOW + 10), {
    now: NOW + 10,
    servedAt: NOW,
    system: "LIRR",
    gated: true,
    pollAge: 30,
  });
  assert.deepEqual(positionBoard(source, ["MNR"], NOW + 10), {
    now: NOW + 10,
    servedAt: NOW,
    system: "MNR",
    gated: false,
    pollAge: 410,
  });
  // A system this envelope does not name, and a row that names none (a subway train
  // whose route no group lists): the worst of the whole source, gated.
  assert.equal(positionBoard(source, ["njt"], NOW).pollAge, 400);
  assert.equal(positionBoard(source, ["njt"], NOW).gated, true);
  assert.equal(positionBoard(source, [], NOW).pollAge, 400);
  assert.equal(positionBoard(source, [], NOW).system, null);
  // A single-feed source's one synthesized system.
  const path = { servedAt: NOW, fetchedAt: NOW - 5, systems: ingestSystems({ fetched_at: NOW - 5 }, "path") };
  assert.equal(positionBoard(path, ["path"], NOW).pollAge, 5);
  // A descriptor with no payload yet: nothing to age.
  assert.deepEqual(positionBoard(null, ["LIRR"], NOW), { now: NOW, servedAt: null, system: "LIRR", gated: true, pollAge: null });
  // And what it is for: a retained Metro-North marker states its system's poll age, as its
  // board row does.
  assert.deepEqual(
    positionQualifier(row(null, "retained"), positionBoard(source, ["MNR"], NOW + 10)),
    said("retained", "showing last known, as of 7m ago", { age: 410 }),
  );
});

test("6.3 markerAge: a marker dims when either its system or its own observation is old, and an undated one by its system alone", () => {
  assert.equal(markerAge(10, 200), 200);
  assert.equal(markerAge(300, 20), 300);
  assert.equal(markerAge(null, 95), 95);
  assert.equal(markerAge(40, null), 40);
  assert.equal(markerAge(null, null), null);
  assert.equal(markerAge(undefined, null), null);
  // In a healthy system: the capture's youngest qualified fix (139 s) dims, the oldest
  // unqualified one (86 s) does not, and a Metro-North fix (no clock) does not.
  assert.equal(markerOpacity(markerAge(0, 139)), STALE_MARKER_OPACITY);
  assert.equal(markerOpacity(markerAge(0, 86)), 1);
  assert.equal(markerOpacity(markerAge(0, null)), 1);
  // And a stale system dims a fresh fix, as C2 always has.
  assert.equal(markerOpacity(markerAge(400, 5)), STALE_MARKER_OPACITY);
});

test("6.3 glideDeadline: the earlier of the system's freeze and the observation's, so an old prediction stops a healthy glide", () => {
  assert.equal(glideDeadline(null, row(1000, "placed")), 1090);
  assert.equal(glideDeadline(5000, row(1000, "placed")), 1090);
  assert.equal(glideDeadline(1050, row(1000, "placed")), 1050);
  assert.equal(glideDeadline(1050, row(null, "reported")), 1050);
  assert.equal(glideDeadline(null, row(null, "reported")), null);
  assert.equal(glideDeadline(null, null), null);
  // A train estimated from a prediction taken at 1000, gliding 1000 -> 1400 in a system
  // with no deadline of its own: it moves until 1090 and not a metre after.
  const train = {
    ...row(1000, "estimated"),
    prev_lat: 40,
    prev_lon: -74,
    prev_time: 1000,
    next_time: 1400,
    latitude: 41,
    longitude: -73,
  };
  const drawnAt = (now) => trainLatLng(train, glideClock(now, glideDeadline(null, train)), {})[0];
  assert.ok(drawnAt(1050) < drawnAt(1090), "it glides while its prediction is fresh");
  assert.equal(drawnAt(1200), drawnAt(1090), "and holds still once it is not");
  assert.equal(drawnAt(3600), drawnAt(1090));
  // Where the system's deadline alone would have put it, which is the dead reckoning.
  assert.ok(trainLatLng(train, glideClock(1200, null), {})[0] > drawnAt(1200));
});

test("6.3 the railroad glyph and glide read the served provenance, never the shape of the fields", () => {
  for (const provenance of ["placed", "estimated"]) {
    assert.equal(drawnFromPrediction(row(NOW, provenance)), true, provenance);
    assert.equal(railroadHollow(row(NOW, provenance)), true, provenance);
  }
  assert.equal(drawnFromPrediction(row(NOW, "reported")), false);
  assert.equal(railroadHollow(row(NOW, "reported")), false);
  // A row that claims nothing does not glide, and does not wear the glyph that says GPS.
  for (const provenance of ["unknown", undefined, null, "live-gps"]) {
    assert.equal(drawnFromPrediction(row(NOW, provenance)), false, String(provenance));
    assert.equal(railroadHollow(row(NOW, provenance)), true, String(provenance));
  }
  assert.equal(drawnFromPrediction(null), false);
  assert.equal(railroadHollow(null), true);
  // THE CASE isPlacedRailroad COULD NOT SEE: an estimated train names a stop exactly as
  // a placed one does, and a reported fix that named one would still be a fix.
  assert.equal(railroadHollow({ stop_id: null, provenance: "estimated" }), true);
  assert.equal(railroadHollow({ stop_id: "12", provenance: "reported" }), false);
});

test("6.3 a retained train is drawn as it was drawn before retention, and one never seen before as a prediction", () => {
  const retained = row(NOW, "retained");
  // A retained FIX (its record last saw it `reported`): filled, and it snaps, as before.
  assert.equal(railroadHollow(retained, "reported"), false);
  assert.equal(drawnFromPrediction(retained, "reported"), false);
  // A retained placement or estimate keeps its hollow glyph and its glide, which its
  // system's retained_since freezes (C2). Reading `retained` as a fix sent it to its next
  // stop wearing the GPS glyph, the review's reproduction below.
  for (const before of ["placed", "estimated"]) {
    assert.equal(railroadHollow(retained, before), true, before);
    assert.equal(drawnFromPrediction(retained, before), true, before);
  }
  // Never seen before retention (the page loaded mid-outage): read as a prediction, the
  // pessimistic glyph (design 3.1), and a glide that draws a former fix where a snap would.
  assert.equal(railroadHollow(retained), true);
  assert.equal(drawnFromPrediction(retained), true);
  // `before` means nothing to a row that is not retained: its own provenance decides.
  assert.equal(railroadHollow(row(NOW, "reported"), "placed"), false);
  assert.equal(drawnFromPrediction(row(NOW, "placed"), "reported"), true);

  // THE REVIEW'S REPRODUCTION: a placed LIRR train with the anchors the poller carries
  // forward, heading for Jamaica, and the same row stamped retained at T0 + 15 the way
  // feeds/shared.py's _stamp_retained does it (every other field kept).
  const T0 = 1_782_993_600;
  const served = {
    system: "LIRR", stop_id: "12", latitude: 40.7005, longitude: -73.8095,
    prev_lat: 40.69, prev_lon: -73.79, prev_time: T0 - 120, next_time: T0 + 180,
    observed_at: T0 - 5, provenance: "placed",
  };
  const kept = { ...served, provenance: "retained" };
  // Drawn at T0 + 60, its system's deadline pinned at retained_since (systemStaleAts).
  const at = glideClock(T0 + 60, glideDeadline(T0 + 15, kept));
  const drawn = drawnFromPrediction(kept, "placed") ? trainLatLng(kept, at, {}) : [kept.latitude, kept.longitude];
  assert.deepEqual(drawn, trainLatLng(served, T0 + 15, {}), "held where retention found it");
  assert.notDeepEqual(drawn, [kept.latitude, kept.longitude], "not jumped to its next stop");
  assert.equal(railroadHollow(kept, "placed"), true, "still the glyph that says no GPS");
  assert.equal(railroadAtItsStation(kept, at, "placed"), false, "and no link to a station it is not on");
  // A former fix the page never saw before retention has no anchors and no stop, so the
  // glide it is read with draws it at its own served position, exactly where a snap would.
  const fix = {
    system: "LIRR", stop_id: null, latitude: 40.76, longitude: -73.6,
    prev_lat: null, prev_lon: null, prev_time: null, next_time: null,
    observed_at: T0 - 5, provenance: "retained",
  };
  assert.deepEqual(trainLatLng(fix, at, {}), [40.76, -73.6]);
  assert.equal(railroadAtItsStation(fix, at), false);
});

test("6.3 railroadAtItsStation: a train names its station AND is drawn on it, or it gets no cross-link", () => {
  const anchorless = { stop_id: "12", provenance: "placed", prev_lat: null, prev_time: null, next_time: 2000 };
  // The placement decode's first poll: no anchors, so trainLatLng draws it at its stop.
  assert.equal(railroadAtItsStation(anchorless), true);
  // Gliding toward its stop: the link would name a station it is not at.
  const gliding = {
    stop_id: "12",
    provenance: "estimated",
    prev_lat: 40,
    prev_lon: -74,
    prev_time: 1000,
    next_time: 1300,
    latitude: 41,
    longitude: -73,
  };
  assert.equal(railroadAtItsStation(gliding), false, "without the clock it is glided by, not assumed");
  assert.equal(railroadAtItsStation(gliding, 1200), false);
  assert.equal(railroadAtItsStation({ ...gliding, provenance: "placed" }, 1299), false);
  // ...until its glide has reached the stop, where trainLatLng draws it on the station.
  assert.equal(railroadAtItsStation(gliding, 1300), true);
  assert.equal(railroadAtItsStation(gliding, 1500), true);
  assert.deepEqual(trainLatLng(gliding, 1300, {}), [41, -73]);
  // A segment trainLatLng cannot interpolate is drawn at its stop, so it is at it.
  assert.equal(railroadAtItsStation({ ...gliding, next_time: 900 }), true);
  // A GPS fix names no station, whatever its clock.
  assert.equal(railroadAtItsStation({ stop_id: null, provenance: "reported" }, 5000), false);
  // A retained placement is the placement it was, frozen on its glide (`before`, the
  // provenance its record last saw): at its station only where that glide put it. One the
  // page never saw before retention is read the same way; a retained fix names no station.
  const kept = { ...gliding, provenance: "retained" };
  assert.equal(railroadAtItsStation(kept, 1200, "placed"), false);
  assert.equal(railroadAtItsStation(kept, 1300, "placed"), true);
  assert.equal(railroadAtItsStation(kept, 1200), false);
  assert.equal(railroadAtItsStation({ stop_id: null, provenance: "retained" }, 5000, "reported"), false);
  assert.equal(railroadAtItsStation(null), false);
});

test("6.3 a popup's position line and a name's clause are one answer, and silent for a fresh fix", () => {
  const fresh = q(row(NOW - 5, "reported"));
  const aged = q(row(NOW - 300, "reported"));
  const sched = q(row(NOW - 5, "placed"));
  assert.equal(positionLineHtml(fresh), "");
  assert.equal(positionLineHtml(aged), '<br><span class="popup-sub">live GPS, as of 5m ago</span>');
  assert.equal(positionLineHtml(sched), '<br><span class="popup-sub">scheduled position (no GPS)</span>');
  assert.equal(positionLineHtml(null), "");
  assert.equal(positionClause(fresh), null);
  assert.equal(positionClause(aged), "live GPS, as of 5m ago");
  assert.equal(positionClause(sched), "scheduled position, no GPS");
  assert.equal(positionClause(null), null);
});

/* MR5 (ruling Q2): THE SAME SEVEN CASES, ASKED OF THE FOOTER, because the footer took the line's
   job. vehicleStaleLine is deleted; popupFreshHtml keeps its rule exactly and adds the two states it
   never had. The cases below are 6.3's own, unchanged in what they assert and re-pointed at the
   function that answers now, so the coverage this rule has always had survives the move rather than
   being rewritten into something easier.

   WHAT "SAID ONCE" LOOKS LIKE NOW is the one difference, and it is the ruling's: the line returned
   the empty string, the footer returns its SQUARE with the words in a visually-hidden span. A rider
   cannot tell "this feed is live" from "this popup forgot to say" unless the mark is always there.
   So `spoken` below is the footer's form of "said once", and `shown` is its form of "says its own",
   and both are asserted against the words the state actually has rather than against a literal. */
test("6.3 / MR5 Q2: a vehicle's footer speaks only for what its position's words did not say", () => {
  const footer = (age, position) => popupFreshHtml({ state: feedDotState({ age }), age, position });
  const words = (age) => feedStateWords({ state: feedDotState({ age }), age });
  const shown = (age) => footer(age, null).endsWith(`${words(age)}</div>`);
  const spoken = (out, age) => out.includes(`<span class="visually-hidden">${words(age)}</span>`);
  const square = (age) => `data-state="${feedDotState({ age })}"`;

  const undated = q(row(null, "reported"), mnr);
  // Metro-North's undated fix states no age, so a stale MNR's footer is the only age there is.
  assert.ok(footer(400, undated).endsWith(`${words(400)}</div>`), "an undated fix leaves the footer to speak");
  // A fix whose words state an age at least as old as the feed's: said once, and still marked.
  for (const [age, pos] of [[400, q(row(NOW - 420, "reported"))], [30, q(row(NOW - 300, "reported"))], [null, q(row(NOW - 300, "reported"))]]) {
    const out = footer(age, pos);
    assert.ok(spoken(out, age), `age ${age}: the words are said once, not shown twice`);
    assert.ok(out.includes(square(age)), `age ${age}: the square is never withheld`);
  }
  // Words younger than the feed's age leave the footer to say its own.
  assert.ok(footer(400, q(row(NOW - 100, "reported"))).endsWith(`${words(400)}</div>`));
  // A fresh fix in a stale feed, and nothing stale at all, exactly as C2 drew them.
  assert.ok(footer(400, q(row(NOW - 5, "reported"))).endsWith(`${words(400)}</div>`));
  assert.ok(shown(400), "a stale feed with no position shows its age");
  /* AND THE LAST OF C2's CASES IS THE ONE THE FOOTER CHANGES ON PURPOSE. vehicleStaleLine rendered
     NOTHING for a fresh system; the footer renders its square with "Live · 5s" in the tree, because
     this app has no word for live on any surface (memo D9) and a rider still needs to see that the
     feed is current. feedDotState is what decides that, and it is the strip's own judgment. */
  const fresh = footer(30, q(row(NOW - 5, "reported")));
  assert.equal(feedDotState({ age: 30 }), "live");
  assert.ok(fresh.includes('data-state="live"'), "a fresh feed still draws its square");
  assert.ok(spoken(fresh, 30), "and says Live in the tree, where an eye sees only the square");
  assert.ok(!/>Live/.test(fresh.replace(/<span class="visually-hidden">[^<]*<\/span>/, "")), "and nowhere else");
});

test("6.3 one write per render: a poll's announcements compose into one sentence, in order", () => {
  assert.equal(
    composeAnnouncements([
      "The LIRR Babylon Branch you were following left the feed. Focus moved to the map.",
      "Live data current again for Metro-North.",
    ]),
    "The LIRR Babylon Branch you were following left the feed. Focus moved to the map. " +
      "Live data current again for Metro-North.",
  );
  assert.equal(composeAnnouncements(["Live data delayed for Subway."]), "Live data delayed for Subway.");
  assert.equal(composeAnnouncements([]), "");
  assert.equal(composeAnnouncements(null), "");
  assert.equal(composeAnnouncements(["", null, "   ", "One."]), "One.");
});

test("6.3 a railroad fix the ladder stopped drawing is 'no longer shown' in the status line's words; any other departure 'left the feed'", () => {
  const block = (suppressed) => ({
    ok: true,
    retainedSince: null,
    positions: { reported: 0, estimated: 0, qualified: 0, placed: 0, suppressed },
  });
  const fix = (age) => ({ system: "LIRR", observed_at: NOW - age, provenance: "reported" });
  assert.equal(withheldFix(fix(605), block(1), NOW, NOW), "withheld");
  // Aged from the envelope that dropped it: a fix 590 s old at one poll is 605 s old at
  // the poll served 15 s later, which is the one that withheld it.
  assert.equal(withheldFix(fix(590), block(1), NOW + 15, NOW), "withheld");
  // Its own fix within OBS_MAX_S ("within" is inclusive, as the ladder's is): it was not
  // withheld for its age, whatever the count says, so it left the feed.
  assert.equal(withheldFix(fix(600), block(1), NOW, NOW), null);
  assert.equal(withheldFix(fix(30), block(24), NOW, NOW), null);
  // A block counting none withheld: the status line says nothing, and neither does this.
  assert.equal(withheldFix(fix(605), block(0), NOW, NOW), null);
  assert.equal(withheldFix(fix(605), null, NOW, NOW), null);
  // A placed or estimated row is dated by its prediction, not its fix, and Metro-North
  // dates nothing: the general sentence.
  for (const provenance of ["placed", "estimated", "retained"]) {
    assert.equal(withheldFix({ ...fix(605), provenance }, block(1), NOW, NOW), null, provenance);
  }
  assert.equal(withheldFix({ system: "MNR", observed_at: null, provenance: "reported" }, block(1), NOW, NOW), null);
  assert.equal(withheldFix(null, block(1), NOW, NOW), null);

  const label = "LIRR Babylon Branch, train 2751, Eastbound, live GPS, as of 9m ago";
  assert.equal(
    vanishingFocusMessage("vehicle", label, "withheld"),
    "The LIRR Babylon Branch you were following is no longer shown, last seen over 10m ago. Focus moved to the map.",
  );
  assert.equal(
    vanishingFocusMessage("vehicle", label),
    "The LIRR Babylon Branch you were following left the feed. Focus moved to the map.",
  );
  assert.equal(
    vanishingFocusMessage("vehicle", null, "withheld"),
    "The vehicle you were following is no longer shown, last seen over 10m ago. Focus moved to the map.",
  );
  assert.equal(vanishingFocusMessage("alerts", null, "withheld"), "Alerts cleared. Focus moved to the map.");
  const inside = { tag: "button" };
  const subtree = { contains: (node) => node === inside };
  assert.equal(
    vanishingFocusPlan(subtree, inside, { label, reason: "withheld" }).message,
    vanishingFocusMessage("vehicle", label, "withheld"),
  );
});

test("6.3 ingestSystems reads a block's position counts, all five or none", () => {
  const steps = { reported: 27, estimated: 6, qualified: 11, placed: 0, suppressed: 24 };
  const systems = ingestSystems(
    {
      systems: {
        LIRR: { fetched_at: 1, positions: steps },
        MNR: { fetched_at: 1, positions: null },
        OLD: { fetched_at: 1 },
        HALF: { fetched_at: 1, positions: { suppressed: 24 } },
        NEG: { fetched_at: 1, positions: { ...steps, suppressed: -1 } },
        FRAC: { fetched_at: 1, positions: { ...steps, placed: 1.5 } },
        TEXT: { fetched_at: 1, positions: { ...steps, suppressed: "24" } },
      },
    },
    "railroads",
  );
  assert.deepEqual(systems.LIRR.positions, steps);
  for (const name of ["MNR", "OLD", "HALF", "NEG", "FRAC", "TEXT"]) assert.equal(systems[name].positions, null, name);
  // A synthesized single system has no ladder.
  assert.equal(ingestSystems({ fetched_at: 1 }, "path").path.positions, null);
});

test("6.3 the status line: the withheld count rides a line, and never raises one", () => {
  const now = 20_000;
  const steps = (suppressed) => ({ reported: 27, estimated: 6, qualified: 11, placed: 0, suppressed });
  const block = (over) => ({ fetched_at: now, ok: true, retained_since: null, ...over });
  const lirr = (over = {}) => block({ feed_timestamp: now - 5, positions: steps(24), ...over });
  const mnrBlock = (over = {}) =>
    block({ feed_timestamp: null, positions: { reported: 33, estimated: 0, qualified: 0, placed: 0, suppressed: 0 }, ...over });
  const rail = (blocks) => ({
    label: "railroad",
    fetchedAt: now,
    servedAt: now,
    feedTimestamp: now - 5,
    systems: ingestSystems({ fetched_at: now, systems: blocks }, "railroads"),
  });
  // THE COMMON CASE DOES NOT GET NOISIER, and on LIRR the common case is 24 withheld.
  // The committed capture withholds 24 of 68 with nothing wrong at all, so a clause that
  // could raise the line would raise it every poll and map.js would paint it red every
  // poll. REVIEW FIX: it raised until the whole-branch review measured what that meant.
  assert.equal(staleness(rail({ LIRR: lirr(), MNR: mnrBlock() }), now), null);
  assert.equal(staleness(rail({ LIRR: lirr({ positions: steps(1) }), MNR: mnrBlock() }), now), null);
  assert.equal(staleness(rail({ LIRR: lirr({ positions: steps(0) }), MNR: mnrBlock() }), now), null);
  assert.equal(staleness(rail({ LIRR: lirr({ positions: null }), MNR: mnrBlock({ positions: null }) }), now), null);
  // It rides every other population, in its own clause, never merged into one.
  assert.equal(
    staleness(rail({ LIRR: lirr({ fetched_at: now - 300 }), MNR: mnrBlock() }), now),
    "railroad: LIRR as of 5m ago; LIRR 24 trains not shown, last seen over 10m ago; MNR position age unavailable",
  );
  assert.equal(
    staleness(rail({ LIRR: lirr({ fetched_at: now - 300, positions: steps(0) }), MNR: mnrBlock() }), now),
    "railroad: LIRR as of 5m ago; MNR position age unavailable",
  );
  // Another system's population raises the line, and LIRR's count rides that one too.
  assert.equal(
    staleness(rail({ LIRR: lirr(), MNR: mnrBlock({ fetched_at: now - 360, ok: false, retained_since: now - 345 }) }), now),
    "railroad: MNR as of 6m ago; LIRR 24 trains not shown, last seen over 10m ago; MNR position age unavailable",
  );
  // The count speaks while the decode it describes is the one on the map. A failed LIRR
  // whose rows are retained still is, so its count rides the clause that names it...
  assert.equal(
    staleness(rail({ LIRR: lirr({ fetched_at: now - 300, ok: false, retained_since: now - 10 }), MNR: mnrBlock() }), now),
    "railroad: LIRR as of 5m ago; LIRR 24 trains not shown, last seen over 10m ago; MNR position age unavailable",
  );
  // ...and once the retention cap has taken every LIRR train, the stale clause speaks for
  // the system and a count of 24 would be a count of what is no longer drawn at all.
  assert.equal(
    staleness(rail({ LIRR: lirr({ fetched_at: now - 700, ok: false, retained_since: null }), MNR: mnrBlock() }), now),
    "railroad: LIRR as of 12m ago; MNR position age unavailable",
  );
  // A malformed count says nothing, on a raised line as well as an unraised one.
  assert.equal(
    staleness(rail({ LIRR: lirr({ fetched_at: now - 300, positions: { suppressed: 24 } }), MNR: mnrBlock() }), now),
    "railroad: LIRR as of 5m ago; MNR position age unavailable",
  );
  // UNDATED_SYSTEMS decides which system gets the clause, never the name.
  UNDATED_SYSTEMS.delete("MNR");
  try {
    assert.equal(
      staleness(rail({ LIRR: lirr({ fetched_at: now - 300 }), MNR: mnrBlock() }), now),
      "railroad: LIRR as of 5m ago; LIRR 24 trains not shown, last seen over 10m ago",
    );
  } finally {
    UNDATED_SYSTEMS.add("MNR");
  }
  // And the whole-source wording is decided exactly as before: every system stale in one
  // way reads as the source, and the ladder's clauses follow it.
  assert.equal(
    staleness(rail({ LIRR: lirr({ fetched_at: now - 300 }), MNR: mnrBlock({ fetched_at: now - 300 }) }), now),
    "railroad: as of 5m ago; LIRR 24 trains not shown, last seen over 10m ago; MNR position age unavailable",
  );
});

test("6.3 every vehicle surface says its position from one answer: the popup's words, the name's spoken form", () => {
  const fresh = q(row(NOW - 5, "reported"));
  const aged = q(row(NOW - 300, "reported"));
  const sched = q(row(NOW - 5, "placed"));
  const estimate = q(row(NOW - 4, "estimated"));
  // Railroad: the name ends with the spoken form for every position, "live GPS" included,
  // as the popup's compact line always says it.
  assert.equal(
    railroadTrainName({ system: "LIRR", route_id: "10", train_num: "2751" }, "Babylon Branch", fresh),
    "LIRR Babylon Branch, train 2751, live GPS",
  );
  assert.equal(
    railroadTrainName({ system: "LIRR", route_id: "10", train_num: "2751" }, "Babylon Branch", aged),
    "LIRR Babylon Branch, train 2751, live GPS, as of 5m ago",
  );
  assert.equal(
    railroadTrainName({ system: "LIRR", route_id: "7", stop_name: "Jamaica" }, null, estimate),
    "LIRR route 7, next stop Jamaica, estimated from a prediction",
  );
  assert.equal(
    railroadTrainName({ system: "MNR", route_id: "1", stop_name: "Grand Central" }, "Hudson", sched),
    "Metro-North Hudson, next stop Grand Central, scheduled position, no GPS",
  );
  // Subway, PATH and NJ Transit: the word the popup prints, said aloud.
  assert.equal(
    subwayTrainName({ route_id: "1", stop_name: "Times Sq-42 St", direction: "Northbound" }, sched),
    "1 train, next stop Times Sq-42 St, Northbound, scheduled position, no GPS",
  );
  assert.match(
    pathTrainPopupHtml({ route_id: "862" }, null, "#d93a30", sched),
    /<span class="popup-sub">scheduled position \(no GPS\)<\/span>$/,
  );
  assert.equal(pathTrainName({ route_id: "862" }, null, sched), "PATH route 862, PATH, scheduled position, no GPS");
  assert.match(
    njtTrainPopupHtml({ route_id: "9" }, "Northeast Corridor", "#DD3439", estimate),
    /<span class="popup-sub">estimated from a prediction<\/span>$/,
  );
  assert.equal(
    njtTrainName({ route_id: "9", train_num: "3800" }, "Northeast Corridor", estimate),
    "Northeast Corridor, NJ Transit, train 3800, estimated from a prediction",
  );
  // Bus and ferry: a fresh fix adds nothing to either surface, an aged one its age to both.
  assert.equal(busName({ route_id: "M15", bearing: 90 }, fresh), "M15 bus, heading east");
  assert.equal(busName({ route_id: "M15", bearing: 90 }, aged), "M15 bus, heading east, live GPS, as of 5m ago");
  assert.equal(
    ferryBoatName({ label: "H201", status: "STOPPED_AT" }, "East River", fresh),
    "East River, NYC Ferry, boat H201, at dock",
  );
  assert.equal(
    ferryBoatName({ label: "H201", status: "STOPPED_AT" }, "East River", aged),
    "East River, NYC Ferry, boat H201, at dock, live GPS, as of 5m ago",
  );
  assert.doesNotMatch(ferryBoatPopupHtml({ label: "H201" }, "East River", "#00839c", fresh), /GPS/);
  assert.match(ferryBoatPopupHtml({ label: "H201" }, "East River", "#00839c", aged), /live GPS, as of 5m ago/);
});

test("6.3 isPlacedRailroad is gone, every sweep dims by the observation too, and every glide freezes at glideDeadline", () => {
  // THE SHAPE OF THE FIELDS DECIDES NOTHING: the helper that read stop_id is deleted
  // rather than fixed (design 4.3), and no surface calls it.
  assert.equal(require("./helpers.js").isPlacedRailroad, undefined);
  const systems = join(__dirname, "systems");
  const files = readdirSync(systems).filter((name) => name.endsWith(".js"));
  const src = (name) => readFileSync(join(systems, name), "utf8");
  // EVERY FRONTEND FILE, not only systems/. The export check above catches a re-added
  // helper only if it is exported, and the helper lived in helpers.js before this branch
  // deleted it, so scanning systems/ alone left its own birthplace unscanned: a private
  // copy in helpers.js called from helpers.js or stations.js would have satisfied both
  // halves. REVIEW FIX, and the design's sentence about this test ("gone from every
  // frontend file rather than merely unused") is what it now checks.
  // A CALL OR A DEFINITION, not a mention: the comments that say what was deleted and why
  // are the record of the decision and must keep naming it.
  const called = /isPlacedRailroad\s*\(/;
  for (const name of readdirSync(__dirname).filter((n) => n.endsWith(".js") && !n.endsWith(".test.js"))) {
    assert.doesNotMatch(readFileSync(join(__dirname, name), "utf8"), called, name);
  }
  for (const name of files) assert.doesNotMatch(src(name), called, name);
  /* The railroad's glyph, glide, words and cross-link come from the served provenance.

     THE GLYPH'S CALL MOVED IN MR3 AND THE RULE DID NOT, so this follows the chain rather than
     dropping the claim. railroadHollow used to be called from railroad.js directly, for a
     16x16 square whose only variable was filled-or-hollow. The tag needs three decisions, so
     railroad.js asks railTagState, and railTagState is what calls railroadHollow, keeping ONE
     expression of "did this train report this position". Both links are asserted: railroad.js
     must reach the table, and the table must still ask that helper. A copy of the rule inside
     railTagState would pass the first and fail the second. */
  const railroad = src("railroad.js");
  for (const call of ["railTagState(", "drawnFromPrediction(", "railroadAtItsStation(", "railroadPosition("]) {
    assert.ok(railroad.includes(call), `railroad.js no longer calls ${call}`);
  }
  const helpers = readFileSync(join(__dirname, "helpers.js"), "utf8");
  const table = helpers.slice(helpers.indexOf("function railTagState("));
  assert.match(
    table.slice(0, table.indexOf("\n}\n")),
    /railroadHollow\(/,
    "railTagState no longer asks railroadHollow, so the body rule has a second home",
  );
  // AND NJ TRANSIT REACHES THE SAME TABLE. Its icon was a byte-for-byte copy of the
  // railroad's hollow rect with no shared helper, so "one grammar for three families" is only
  // true while this holds.
  assert.ok(src("njt.js").includes("railTagState("), "njt.js draws its own glyph again");
  // EVERY STALE SWEEP DIMS BY THE OBSERVATION TOO, which is the site the animation tick
  // wakes when one observation crosses between polls: a sweep that dimmed by its
  // system's age alone would leave that marker bright.
  for (const name of ["subway.js", "railroad.js", "buses.js", "path.js", "njt.js", "ferry.js"]) {
    const body = src(name);
    const at = body.indexOf("staleTreatments.push(");
    assert.ok(at >= 0, `${name} registers no stale sweep`);
    const sweep = body.slice(at, body.indexOf("\n});", at));
    assert.match(sweep, /vehicleMarkerAge\(/, `${name}'s sweep dims by its system's age alone`);
    // AND RE-NAMES, so a marker that dims because its observation crossed between polls
    // says why in its accessible name at the same moment (the A2 rule: the name says what
    // the popup says), and a poll that fails, which re-applies nothing, still refreshes it.
    assert.match(sweep, /setMarkerName\(/, `${name}'s sweep dims a marker and leaves its name behind`);
  }
  // EVERY GLIDE FREEZES AT glideDeadline, the earlier of the system's deadline and the
  // observation's: a glideClock fed a system's deadline alone dead-reckons an old fix in
  // a healthy feed.
  for (const name of files) {
    for (const match of src(name).matchAll(/glideClock\(\s*now\s*,\s*([A-Za-z]+)/g)) {
      assert.equal(match[1], "glideDeadline", `${name}: glideClock(now, ${match[1]}...)`);
    }
  }
});

test("6.3 erratum: every family that dims through vehicleMarkerAge has a row in the 3.3 gating table", () => {
  /* THE OPERATOR'S RULING R-b, held by a test rather than by the table's own comment.

     The first cut of the gate read `!UNDATED_SYSTEMS.has(row.system)`, which was right for the
     wrong reason: bus, subway, PATH and ferry rows carry no `system` field at all, so
     `has(undefined)` came back false and those four families were gated by ACCIDENT. A table
     replaces it, and a table is only as good as the guarantee that nothing reaches it unlisted.
     So the call sites are enumerated from the source and every family one of them can name must
     have a row. A seventh system added in stage 4 fails here until it is listed. */
  const systems = join(__dirname, "systems");
  const files = readdirSync(systems).filter((name) => name.endsWith(".js"));
  const keys = new Set();
  for (const name of files) {
    for (const m of readFileSync(join(systems, name), "utf8").matchAll(/vehicleMarkerAge\(\s*"([a-z]+)"/g)) {
      keys.add(m[1]);
    }
  }
  // The six sources that dim a vehicle. AirTrain is absent because it has no vehicles at all
  // (its layer is stations and lines), which is why this is asserted as a set and not a floor:
  // a source that stops dimming is as much a change as one that starts.
  assert.deepEqual([...keys].sort(), ["buses", "ferry", "njt", "path", "railroads", "subways"]);

  /* "railroads" IS THE ONE SOURCE WITH NO ROW, and that is the table working rather than a gap:
     it is the only source whose two halves DIFFER, so the answer cannot be a property of the
     source. Every railroad row carries a `system` ("LIRR" or "MNR") and observationGated reads
     the row's own system first, so the source key is never consulted. Both systems are listed,
     and they disagree, which is the whole reason the table is keyed this way. */
  for (const key of keys) {
    if (key === "railroads") {
      assert.equal(OBSERVATION_GATED.railroads, undefined);
      continue;
    }
    assert.equal(typeof OBSERVATION_GATED[key], "boolean", `${key} has no row in OBSERVATION_GATED`);
  }
  assert.equal(observationGated("railroads", { system: "LIRR" }), true);
  assert.equal(observationGated("railroads", { system: "MNR" }), false);

  // EVERY UNDATED SYSTEM IS LISTED AS NOT GATED, in both directions: the two tables are one
  // policy and a system in UNDATED_SYSTEMS that this one gated would dim a live fleet.
  for (const name of UNDATED_SYSTEMS) {
    assert.equal(OBSERVATION_GATED[name], false, `${name} is undated but gated`);
  }
  for (const [name, gated] of Object.entries(OBSERVATION_GATED)) {
    assert.equal(gated, !UNDATED_SYSTEMS.has(name), `${name} disagrees with UNDATED_SYSTEMS`);
  }

  // AN UNLISTED FAMILY IS GATED, which is the pessimistic answer. The test above is what stops
  // that fallback being reached in practice; this is what it does when it is.
  assert.equal(observationGated("amtrak", null), true);
  assert.equal(observationGated("subways", {}), true);
});

test("6.3 erratum: a header-dated subway row with no vehicle.timestamp dims and says age unknown", () => {
  /* THE INTENDED CASE, ASSERTED (the operator's ruling R-b). The subway is the one gated family
     whose 3.3 row has a fallback: "vehicle.timestamp, else the contributing group header". So a
     subway row the backend could date only from a header reaches the page with observed_at null
     while /healthz still counts the feed as dated, and the rider is shown the pessimistic answer
     the erratum settles: dimmed, with "age unknown" in the words.

     THE TWO SURFACES DISAGREE ON PURPOSE, and the erratum in docs/design/freshness-contract.md
     says so: the OPERATOR's rule is about whether the feed dates its rows, which it does, and
     the RIDER's is about whether THIS row's position can be dated, which it cannot. A single
     answer would either hide a real anomaly from the rider or raise a false one for the
     operator. */
  const row = { provenance: "reported", observed_at: null };
  assert.equal(observationGated("subways", row), true);
  const age = observationDimAge(row, null, observationGated("subways", row));
  assert.equal(age, AGE_UNKNOWN);
  assert.equal(staleAge(age), true);
  assert.equal(markerOpacity(age), STALE_MARKER_OPACITY);
  assert.equal(markerAge(null, age), AGE_UNKNOWN);
  // And the words, from the same row read against a subway board. "live GPS, age unknown" is
  // clause (c)'s sentence and it is unchanged by the erratum: what changed is the opacity.
  assert.equal(positionQualifier(row, positionBoard({ servedAt: 1_800_000_000, systems: {} }, [], 1_800_000_000)).words, "live GPS, age unknown");
});

test("MR3: the re-skin gate covers every variable the tag is drawn from, in both rail files", () => {
  /* THE GATE HAD NO TEST AT ALL, which is how it came to be written twice. railroad.js and
     njt.js each build a skin key and compare it against the one the record is wearing; if a
     variable the icon is drawn from is missing from that string, a train that changes it keeps
     the icon it was born with, silently and forever. The old gate was `record.hollow !== hollow`,
     enough for a square whose only variable was filled-or-hollow, and the tag has six.

     SO THE INPUTS ARE READ OFF railTagIcon ITSELF rather than listed here. Add a seventh
     parameter to the icon and this fails until both keys carry it, which is exactly the failure
     that was missing. */
  const systems = join(__dirname, "systems");
  const src = (name) => readFileSync(join(systems, name), "utf8");
  const shared = src("shared.js");
  const signature = shared.slice(shared.indexOf("function railTagIcon("));
  const params = signature.slice(signature.indexOf("{") + 1, signature.indexOf("}"));
  const inputs = params.split(",").map((part) => part.split("=")[0].trim()).filter(Boolean);
  assert.deepEqual(inputs.sort(), ["bearing", "code", "color", "state", "system", "textColor"]);

  /* AND THE CLOCK REACHES THE WORDS AS WELL AS THE MARK (round 4). Both tag-state functions take
     a `now`, because the stale sweep pins one, and njtTagState used to spend it on the age term
     alone and call `njtPosition(train)` with none, so the sweep's pinned clock produced the live
     clock's words. With the age term gone the parameter would have been unread entirely. Asserted
     on the source rather than by behaviour, because "the parameter is threaded" is a structural
     claim and a behavioural one would need a fixture per surface. */
  for (const [file, fn, call] of [
    ["railroad.js", "railroadTagState", "railroadPosition(train, now)"],
    ["njt.js", "njtTagState", "njtPosition(train, now)"],
  ]) {
    const body = src(file);
    const at = body.indexOf(`function ${fn}(`);
    assert.ok(at >= 0, `${file} has no ${fn}`);
    const state = body.slice(at, body.indexOf("\n}\n", at));
    assert.match(state, /now = correctedNow\(\)/, `${fn} takes no clock`);
    assert.ok(state.includes(call), `${fn} answers at the live clock instead of the one it was passed`);
  }

  for (const [file, key] of [["railroad.js", "railroadSkinKey"], ["njt.js", "njtSkinKey"]]) {
    const body = src(file);
    const at = body.indexOf(`function ${key}(`);
    assert.ok(at >= 0, `${file} has no ${key}`);
    const fn = body.slice(at, body.indexOf("\n}\n", at));
    /* code, color and textColor come off the branch lookup; body and head off the state; the
       bearing is rounded to a degree so a train wandering by hundredths does not rebuild its
       icon every poll, and headingTrusted is in the key because a row that refuses a heading
       draws a dot where the same bearing would otherwise draw a chevron (row 6 of the table). */
    for (const part of ["branch.code", "branch.color", "branch.textColor", "state.body", "state.head", "state.headingTrusted", "Math.round(bearing)"]) {
      assert.ok(fn.includes(part), `${key} does not compare ${part}`);
    }
    /* `system` IS THE ONE INPUT WITH NO COMPONENT OF ITS OWN, and it is pinned instead by the
       RECORD key: railroadKey is `${train.system}|${train.trip_id}`, so a record's system is
       fixed for its whole life and a train cannot change family without becoming a different
       record and a different marker. The NJT layer has one system by construction. Asserted
       rather than assumed, because the day the record key drops the system is the day this
       exception stops holding and a Metro-North tag could keep an LIRR train's glyph.

       IT IS STILL READ, as the first half of the branch lookup: LIRR and MNR route ids collide,
       so railroadBranch needs both to resolve a code and a colour at all. Reading it and
       comparing it are different things, and this is the reading. */
    if (file === "railroad.js") {
      assert.match(body, /function railroadKey\(train\) \{\s*return `\$\{train\.system\}\|/);
      assert.match(fn, /railroadBranch\(train\.system, train\.route_id\)/);
    }
  }
});

test("6.3 a no-times Metro-North placement is still a placement: the provenance says so, not which fields are filled", () => {
  // The case isPlacedRailroad existed for: next_time, prev_lat and direction all null
  // and a real stop_id. Its successors read the served provenance, so the answer no
  // longer depends on which fields a decode happened to fill.
  const train = {
    provenance: "placed",
    observed_at: null,
    stop_id: "1",
    stop_name: "Grand Central",
    next_time: null,
    prev_lat: null,
    direction: null,
  };
  assert.equal(railroadHollow(train), true);
  assert.equal(drawnFromPrediction(train), true);
  assert.equal(railroadAtItsStation(train), true);
  assert.equal(q(train, mnr).compact, "scheduled (no GPS)");
});

/* ===== MR5, ruling Q1: every popup's position line is positionLineHtml's ==================

   THE ONE SURFACE THAT DID NOT USE IT, and the reason this needs a test in this file at all.
   systems/railroad.js's popup rendered `position.compact` from a line of its own,
   UNCONDITIONALLY, and it was the app's only surface that did. Two rider-visible differences
   followed: a `placed` train said "scheduled (no GPS)" instead of the contract's "scheduled
   position (no GPS)", and a FRESH GPS fix said "live GPS" where every other surface says nothing,
   because silence means current (memo D9).

   IN NODE, BY READING THE SOURCE, because systems/railroad.js needs Leaflet and a document and
   cannot be required here. That is the same escape frontend/railtag.test.js takes for the NJ
   Transit head (finding N6) and for the same reason: the claim is about which function a builder
   calls, which is a fact about the file. The e2e pins hold what the popup then SAYS.

   AND THE STRINGS ARE ASSERTED HERE TOO, from positionQualifier directly, so this test says what
   the two changes ARE rather than only which call site moved. A reader who wants the before can
   see it in the ledger; what is below is the after, derived from the contract rather than typed. */
/* COMMENTS STRIPPED FIRST, AND THAT IS NOT FUSSINESS: the comment this stage wrote at the changed
   line SAYS "position.compact", because explaining what moved requires naming it. Scraping the raw
   source found the word in the prose and failed a correct build. pins.spec.js P5b paid for the same
   thing in its literal extractor and ended up with a character scanner; a source this small needs
   only the two comment forms removed, in one pass, longest-first so a line comment inside a block
   comment cannot end it early. */
const withoutComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:])\/\/[^\n]*/g, "$1");

test("MR5 Q1: the railroad popup takes its position line from positionLineHtml, not position.compact", () => {
  const src = withoutComments(readFileSync(join(__dirname, "systems", "railroad.js"), "utf8"));
  const body = src.slice(src.indexOf("function railroadPopup("));
  const fn = body.slice(0, body.indexOf("\n}"));

  assert.match(fn, /positionLineHtml\(position\)/, "the railroad popup must render through positionLineHtml");
  assert.ok(
    !/position\.compact/.test(fn),
    "the railroad popup still reads position.compact, which is the one surface that chose its own form",
  );
  // And nowhere else in the app does either: positionLineHtml is the only popup reader of a
  // position's words now, and .compact has no popup caller at all.
  for (const rel of ["systems/subway.js", "systems/buses.js", "systems/njt.js", "systems/path.js", "systems/ferry.js"]) {
    const other = withoutComments(readFileSync(join(__dirname, rel), "utf8"));
    assert.ok(!/position\.compact/.test(other), `${rel} renders position.compact itself`);
  }
  // AND THE SCRAPE IS NOT VACUOUS: the slice really is the function, not an empty string that
  // trivially contains no forbidden text. This is the check that would have caught a bad indexOf.
  assert.ok(fn.length > 400, `the railroadPopup slice is only ${fn.length} characters, so it found the wrong thing`);
  assert.match(fn, /routeAlertsBlock\(/, "and it is the popup builder, which opens with its alerts block");
});

test("MR5 Q1: the two strings the unification changes, and the two it does not", () => {
  const board = { now: 1000, servedAt: 1000, system: "LIRR" };
  const line = (row, b = board) => positionLineHtml(positionQualifier(row, b));

  // ONE: a placed train gains the contract's own word. `.compact` is what the railroad popup used
  // to print and it still exists, so the difference is asserted rather than described.
  const placed = positionQualifier({ observed_at: 1000, provenance: "placed" }, board);
  assert.equal(placed.compact, "scheduled (no GPS)");
  assert.equal(placed.words, "scheduled position (no GPS)");
  assert.match(line({ observed_at: 1000, provenance: "placed" }), /scheduled position \(no GPS\)/);

  // TWO: a FRESH reported fix says nothing at all, which is the silence rule reaching this popup.
  const fresh = positionQualifier({ observed_at: 1000, provenance: "reported" }, board);
  assert.equal(fresh.kind, "", "a current fix is the unqualified kind");
  assert.equal(fresh.words, "live GPS", "the words exist; it is the LINE that is withheld");
  assert.equal(line({ observed_at: 1000, provenance: "reported" }), "", "and the popup prints none of it");

  // AND THE TWO THAT DO NOT MOVE, which is what keeps this a unification rather than a silencing.
  // An aged fix still speaks, and an estimate always did.
  const aged = line({ observed_at: 700, provenance: "reported" });
  assert.match(aged, /live GPS, as of 5m ago/, "an aged fix still says how old it is");
  assert.match(line({ observed_at: 1000, provenance: "estimated" }), /estimated from a prediction/);

  // The empty-words guard the ruling asked for, which positionQualifier cannot currently trigger:
  // asserted against a hand-made position so the guard is exercised rather than merely present.
  assert.equal(positionLineHtml({ kind: "placed", words: "" }), "");
  assert.equal(positionLineHtml({ kind: "", words: "live GPS" }), "");
  assert.equal(positionLineHtml(null), "");
});
