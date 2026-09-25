// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// FOLLOW-UP 1, the bus markers' zoom rule: drawn from City zoom (13) up and not below it.
//
// THE BAND IS DECIDED IN ONE PURE FUNCTION AND ASKED HERE, which is the only place its
// threshold can be asked directly. The stylesheet's display rule and buses.js's aria-hidden and
// pointer-events both read the root attribute paintZoomBand writes from it, so a threshold that
// moved would move both readers together and no browser spec could tell 12 from 13 without
// visiting 12. What the drawn page does with the answer is tests/e2e/buszoom.spec.js D7's.

const test = require("node:test");
const assert = require("node:assert/strict");

const {
  busMarkerBand,
  BUS_MARKER_ZOOM,
  BUS_ZOOM_WORDS,
  FEEDS,
  feedTooltip,
  feedStateWords,
  feedStripModel,
} = require("./helpers.js");

test("Follow-up 1: a bus marker is drawn from zoom 13 and hidden at 12", () => {
  assert.equal(BUS_MARKER_ZOOM, 13);
  // The boundary, from both sides, which is the whole of the rule.
  assert.equal(busMarkerBand(12), "hidden");
  assert.equal(busMarkerBand(13), "drawn");
  // The presets either side of it: Region and Rail hide, City draws. And the map's own range.
  assert.equal(busMarkerBand(10), "hidden");
  assert.equal(busMarkerBand(11), "hidden");
  assert.equal(busMarkerBand(18), "drawn");
  assert.equal(busMarkerBand(0), "hidden");
  // A threshold and not a list: an integer zoom the map never reaches still has an answer.
  assert.equal(busMarkerBand(20), "drawn");
  /* NO FRACTIONAL CASE, AND THAT IS THE CONTRACT RATHER THAN A GAP. The page asks this function
     one question only: the zoom paintZoomBand has already ROUNDED, the same integer it writes as
     data-zoom and hands every other band. The first draft of this test asserted 12.5 is hidden,
     which is true of the function and false of the page, where a map resting at 12.6 rounds to
     13 and draws; the review of the bus rule called that two readers of one zoom disagreeing at
     the band's edge. The page's answer for a resting fractional zoom is buszoom.spec.js D7h's. */
});

test("Follow-up 1: a zoom that is not a finite number is hidden", () => {
  /* The same fail-safe answer every band in helpers.js gives. A STRING IS NOT COERCED: the page
     passes the number it read off the map, and a "13" here would mean a caller read the
     attribute back instead, which is a second reader of the zoom this rule does not have. */
  for (const zoom of [Number.NaN, Infinity, -Infinity, undefined, null, "13"]) {
    assert.equal(busMarkerBand(zoom), "hidden", String(zoom));
  }
});

test("Follow-up 1: the Buses button's tooltip says the rule in every state, and no other feed's does", () => {
  assert.equal(BUS_ZOOM_WORDS, "shown from City zoom");
  const bus = FEEDS.find((feed) => feed.key === "buses");
  assert.equal(bus.note, BUS_ZOOM_WORDS);
  // The note is the buses' alone: it explains a count the map does not draw, and no other feed
  // has one.
  for (const feed of FEEDS) {
    if (feed.key !== "buses") assert.equal(feed.note, undefined, feed.key);
  }

  /* THE COMPOSITION, OVER THE WHOLE MATRIX: the state's words, then the note, then the action,
     so the action stays last and the note can never displace a word the popup footer shares. */
  assert.equal(
    feedTooltip({ name: "Buses", state: "live", age: 12, note: BUS_ZOOM_WORDS }),
    "Live · 12s · shown from City zoom · hide Buses",
  );
  for (const state of ["live", "stale"]) {
    for (const age of [8, 360, null]) {
      for (const hidden of [false, true]) {
        const verb = hidden ? "show" : "hide";
        assert.equal(
          feedTooltip({ name: "Buses", state, age, hidden, note: BUS_ZOOM_WORDS }),
          `${feedStateWords({ state, age })} · ${BUS_ZOOM_WORDS} · ${verb} Buses`,
          `${state}/${age}/${hidden}`,
        );
      }
    }
  }
  // And no note is no clause, so every other feed's tooltip is the two parts it always was.
  assert.equal(feedTooltip({ name: "Subway", state: "live", age: 12 }), "Live · 12s · hide Subway");

  // Through the model the strip is painted from, shown and hidden, with a count on it.
  for (const hidden of [new Set(), new Set(["buses"])]) {
    const model = feedStripModel({ counts: { buses: 2136, subway: 9 }, ages: { buses: 20, subway: 20 }, hidden });
    const buses = model.find((entry) => entry.key === "buses");
    assert.equal(buses.title, `Live · 20s · ${BUS_ZOOM_WORDS} · ${hidden.size ? "show" : "hide"} Buses`);
    assert.equal(buses.count, (2136).toLocaleString());
    for (const entry of model) {
      if (entry.key !== "buses") assert.ok(!entry.title.includes(BUS_ZOOM_WORDS), entry.key);
    }
  }
});
