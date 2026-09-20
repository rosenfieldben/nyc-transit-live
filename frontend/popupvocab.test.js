// MR5: the popup's vocabulary, asked one builder at a time.
//
// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// WHAT THIS FILE IS FOR. README section 5 gives every popup one grammar, and stage MR5 builds it
// as pure string builders in helpers.js so that the grammar is checkable without a browser. The
// browser tier (tests/e2e/popups.spec.js) measures what the rules DRAW; this measures what the
// markup SAYS, which is the half a screenshot cannot fail on.
//
// THE THREE CLAIMS WORTH STATING UP FRONT, because they are the ones a later stage could break
// without noticing:
//
//   1. A POPUP'S MARK IS THE MAP'S MARK. popupMarkHtml re-wraps a builder's own string at the
//      popup's size, and the body is copied byte for byte. A popup that drew its own route mark
//      would be a second answer to "what does this family look like" and the two would drift,
//      which is finding N6 one surface out. Asserted of all six builders.
//   2. THE ESCAPING HAPPENS ONCE, in the builder. Every text parameter is escaped there, so a
//      caller that pre-escaped would double-escape an ampersand (a silent, rider-visible defect)
//      rather than fail loudly. Every builder here is given an ampersand.
//   3. A ROW WITH NOTHING TO SAY IS NOT PRINTED, which is positionWords' silence rule
//      generalised to the whole grid. "Delay: unknown" is a claim; silence is the truth.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const {
  POPUP_SYSTEM_WORDS,
  POPUP_MARK_TITLE,
  POPUP_MARK_ROW,
  popupMarkHtml,
  popupKickerHtml,
  popupTitleHtml,
  popupRowsHtml,
  popupDirHtml,
  popupArrRowsHtml,
  railroadHeadParts,
  subwayPlateSvg,
  railTagSvg,
  railStationSvg,
  busMarkSvg,
  pathDiamondSvg,
  ferryHullSvg,
  FEEDS,
  ferryBoatName,
  airtrainStationName,
} = require("./helpers.js");

/* THE SIX MARKS THIS MAP DRAWS AS STRINGS, each with the arguments its own caller gives it. The
   rail tag's `state` is railTagState's three fields written out: that machine has its own test
   file (railtag.test.js) and what matters here is only that the body survives the re-wrap. */
const SOLID_STATE = { body: "solid", head: "filled", headingTrusted: true };
const MARKS = [
  { name: "subway plate", natural: 18, svg: subwayPlateSvg("1", "#c0392b", "#ffffff") },
  {
    name: "rail tag",
    natural: 30,
    svg: railTagSvg({ system: "LIRR", code: "BABY", color: "#00985f", state: SOLID_STATE, bearing: 90 }),
  },
  { name: "rail station square", natural: 20, svg: railStationSvg() },
  { name: "bus arrow", natural: 14, svg: busMarkSvg("#605d5d", 45) },
  { name: "bus dot", natural: 14, svg: busMarkSvg("#605d5d", null) },
  { name: "PATH diamond", natural: 16, svg: pathDiamondSvg("#d93a30") },
  { name: "ferry hull", natural: 14, svg: ferryHullSvg("#00839c") },
];

// An svg string split where the opening tag ends. `indexOf(">")` is the whole parse and it is
// enough: no attribute value in any of these builders contains a ">".
function parts(svg) {
  const end = svg.indexOf(">");
  return { open: svg.slice(0, end), body: svg.slice(end + 1) };
}

test("MR5: a popup's mark is the map's own markup, re-sized and not redrawn", () => {
  for (const mark of MARKS) {
    const wrapped = popupMarkHtml(mark.svg, POPUP_MARK_TITLE);
    assert.ok(
      wrapped.startsWith('<span class="pmark" aria-hidden="true"><svg '),
      `${mark.name}: the wrapper hides the mark, because the title says the same thing in words`,
    );
    assert.ok(wrapped.endsWith("</svg></span>"), `${mark.name}: and closes both`);
    const inner = wrapped.slice('<span class="pmark" aria-hidden="true">'.length, -"</span>".length);
    // THE BODY, BYTE FOR BYTE. This is the claim: every path, every fill, every transform the map
    // draws is what the popup draws, so a change to a family's mark reaches both surfaces at once.
    assert.equal(parts(inner).body, parts(mark.svg).body, `${mark.name}: the body is copied, not rebuilt`);
  }
});

test("MR5: the re-wrap keeps the viewBox and the aspect ratio, and writes both dimensions", () => {
  for (const mark of MARKS) {
    const source = parts(mark.svg).open;
    const inner = popupMarkHtml(mark.svg, POPUP_MARK_TITLE).replace(/^<span[^>]*>/, "");
    const open = parts(inner).open;
    const box = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(source);
    assert.ok(box, `${mark.name}: every builder declares a viewBox`);
    const expected = Number(((POPUP_MARK_TITLE * Number(box[1])) / Number(box[2])).toFixed(2));
    assert.match(open, new RegExp(`width="${expected}" height="${POPUP_MARK_TITLE}"`), `${mark.name}: sized`);
    assert.equal(Number(box[2]), mark.natural, `${mark.name}: its natural height is what this file claims`);
    // Everything the source declared BUT a size is still declared: the class the specs read, the
    // bus arrow's rotate transform, the viewBox itself.
    for (const attr of source.matchAll(/\s([a-zA-Z-]+)="([^"]*)"/g)) {
      if (attr[1] === "width" || attr[1] === "height") continue;
      assert.ok(open.includes(attr[0]), `${mark.name}: kept ${attr[1]}`);
    }
    // And no dimension is declared twice, which is what a builder that already had one would do.
    assert.equal((open.match(/\swidth="/g) || []).length, 1, `${mark.name}: one width`);
    assert.equal((open.match(/\sheight="/g) || []).length, 1, `${mark.name}: one height`);
  }
});

test("MR5: a title mark is never smaller than the map draws it, which is why the rail box passes its own", () => {
  /* THE ONE CLAMP IN THE VOCABULARY, stated as arithmetic rather than as a sentence. 24 is the
     design's `.bul.lg`, and it is an ENLARGEMENT for every family whose box is smaller. The two
     rail families are the exception and the reason is geometric: their 30-unit box holds a 13-unit
     tag with a stem and a head hanging below it, so scaling the box to 24 would draw the tag's two
     blocks at 10.4 units with 7px type, thinner than the map's own. Those callers pass 30. */
  const taller = MARKS.filter((m) => m.natural > POPUP_MARK_TITLE).map((m) => m.name);
  assert.deepEqual(taller, ["rail tag"], "only the rail tag's box is taller than a title mark");
  assert.equal(POPUP_MARK_TITLE, 24, "the design's .bul.lg");
  assert.equal(POPUP_MARK_ROW, 17, "and its .bul.sm, for a kicker's marks and an arrivals row");

  /* A TITLE ASKS FOR NO HEIGHT, which is how a caller gets the clamp without knowing its
     family's geometry: the vehicle popups hand over whatever their own marker is wearing
     (systems/shared.js markerMarkHtml) and never name a size. */
  for (const mark of MARKS) {
    const want = Math.max(POPUP_MARK_TITLE, mark.natural);
    assert.match(popupMarkHtml(mark.svg), new RegExp(`height="${want}"`), `${mark.name}: default height`);
  }
  // AND A ROW ASKS FOR ONE AND GETS IT, unclamped: at 17 a mark is a decoration beside words.
  assert.match(popupMarkHtml(MARKS[0].svg, POPUP_MARK_ROW), /height="17"/);
  assert.match(popupMarkHtml(MARKS[1].svg, POPUP_MARK_ROW), /height="17"/);
});

test("MR5: popupMarkHtml refuses anything that is not one of this app's marks", () => {
  // A builder that returned a fragment, a caller that passed a label, a height that arrived as a
  // string from an attribute: each would otherwise reach a popup as broken markup.
  assert.equal(popupMarkHtml("", POPUP_MARK_TITLE), "");
  assert.equal(popupMarkHtml(null, POPUP_MARK_TITLE), "");
  assert.equal(popupMarkHtml("<div>1</div>", POPUP_MARK_TITLE), "", "not an svg");
  assert.equal(popupMarkHtml('<svg class="x"><circle/></svg>', POPUP_MARK_TITLE), "", "no viewBox");
  assert.equal(popupMarkHtml(pathDiamondSvg("#000"), 0), "", "a zero height draws nothing");
  assert.equal(popupMarkHtml(pathDiamondSvg("#000"), "tall"), "", "and a word is not a height");
  // A numeric string IS a height, because that is what an attribute read back would be.
  assert.match(popupMarkHtml(pathDiamondSvg("#000"), "17"), /height="17"/);
});

test("MR5: the subway plate is the map's, byte for byte, and its label is validated in the builder", () => {
  /* AGAINST THE PIN RATHER THAN AGAINST A COPY OF THE STRING, because the pin is what the drawn
     page produced before this builder existed: `pins.spec.js` P1f captured the train marker's html
     and did not change when the markup moved out of trainIcon. A literal here would agree with
     itself; the golden agrees with the map. */
  const pins = JSON.parse(readFileSync(join(__dirname, "..", "tests", "e2e", "fixtures", "mr_pins.json"), "utf8"));
  const pinned = pins.markers.subway.subwayTrains[0].html;
  const fill = /rx="3" fill="([^"]+)"/.exec(pinned)[1];
  const ink = /fill="([^"]+)">1<\/text>/.exec(pinned)[1];
  assert.equal(subwayPlateSvg("1", fill, ink), pinned, "the extracted builder draws the pinned plate");

  // The validation trainIcon used to do inline, now where the plate is: one to three
  // alphanumerics, and anything else is the honest "?".
  for (const route of ["1", "SIR", "FS"]) {
    assert.match(subwayPlateSvg(route, "#000", "#fff"), new RegExp(`>${route}</text>`));
  }
  for (const route of ["", null, undefined, "TOOLONG", "A B", "<b>"]) {
    assert.match(subwayPlateSvg(route, "#000", "#fff"), />\?<\/text>/, `${String(route)} is not a route`);
  }
  // And the type shrinks at two characters, which is a decision about the validated label and not
  // about the served route: "?" is one character and takes the wide size.
  assert.match(subwayPlateSvg("1", "#000", "#fff"), /font-size="10.5"/);
  assert.match(subwayPlateSvg("FS", "#000", "#fff"), /font-size="8.5"/);
  assert.match(subwayPlateSvg("TOOLONG", "#000", "#fff"), /font-size="10.5"/);
});

test("MR5: the kicker prints both sides or no row at all", () => {
  // THE NEWLINES ARE THE CELL SEPARATORS and they are asserted here rather than tolerated: CSS
  // ignores a whitespace-only text node in a flex or grid container, and textContent does not, so
  // they are what makes the popup's text read as words ("Train 1797", not "Train1797").
  assert.equal(popupKickerHtml({ left: "Subway" }), '<div class="pk"><span>Subway</span>\n<span></span></div>\n');
  assert.equal(
    popupKickerHtml({ left: "", rightHtml: '<span class="popup-access">x</span>' }),
    '<div class="pk"><span></span>\n<span><span class="popup-access">x</span></span></div>\n',
  );
  // BOTH SPANS WHENEVER THE ROW EXISTS, because the row is a flex with space-between: one span
  // would sit at the left edge instead of at the right, which is the whole point of the row.
  assert.equal(popupKickerHtml({}), "", "and a surface with neither prints no kicker");
  assert.equal(popupKickerHtml(), "");
  assert.match(popupKickerHtml({ left: "Penn & Broad" }), /Penn &amp; Broad/, "the left side is text");
  assert.doesNotMatch(popupKickerHtml({ left: "<b>x</b>" }), /<b>/, "and cannot carry markup");
});

test("MR5: the title carries the mark, the words, and the route's ink on the words alone", () => {
  const mark = popupMarkHtml(pathDiamondSvg("#d93a30"), POPUP_MARK_TITLE);
  const html = popupTitleHtml({ text: "Newark - World Trade Center", markHtml: mark, color: "#c3342b" });
  assert.ok(html.includes("</span>\n<span style="), "a newline between the mark and the words, for textContent");
  assert.ok(html.startsWith('<div class="pt"><span class="pmark"'), "the mark comes first");
  assert.match(html, /<span style="color:#c3342b">Newark - World Trade Center<\/span><\/div>\n$/);
  /* THE INK IS ON THE TEXT SPAN AND NOT ON THE ROW, and this is the assertion that keeps it there:
     a `color` on .pt would be inherited by anything inside the mark drawn in currentColor, so a
     route colour would reach a mark that paints itself from the theme's tokens. */
  assert.doesNotMatch(html, /<div class="pt" style/);
  assert.equal(popupTitleHtml({ text: "M15" }), '<div class="pt"><span>M15</span></div>\n', "ink is optional");
  assert.equal(popupTitleHtml({ markHtml: mark }), `<div class="pt">${mark}</div>\n`, "and so are the words");
  /* THE BLOCK'S OWN TRAILING NEWLINE IS ALWAYS THERE (it separates this block from the next), so
     what this asserts is the absence of the INNER one: with nothing to separate from the words,
     there is no separator inside the row. */
  assert.equal(popupTitleHtml({ text: "M15" }), '<div class="pt"><span>M15</span></div>\n');
  assert.equal(popupTitleHtml({}), "", "a title with neither is no title");
  assert.match(popupTitleHtml({ text: "Penn & Broad" }), /Penn &amp; Broad/);
});

test("MR5: the facts grid drops a row with nothing to say, and prints no grid when none survives", () => {
  const html = popupRowsHtml([
    { k: "Next stop", v: "Jamaica" },
    { k: "Delay", v: "" },
    { k: "Position", v: "scheduled position (no GPS)" },
  ]);
  assert.equal(
    html,
    '<div class="kv">' +
      '<div class="k">Next stop</div>\n<div class="v">Jamaica</div>\n' +
      '<div class="k">Position</div>\n<div class="v">scheduled position (no GPS)</div>' +
      "</div>\n",
  );
  // A VALUE WITH NO LABEL IS ALSO DROPPED, and for a reason of its own: in a two-column grid it
  // would land in the label column and read as a heading.
  assert.equal(popupRowsHtml([{ k: "", v: "Jamaica" }]), "");
  assert.equal(popupRowsHtml([{ k: "Delay", v: null }, null, undefined]), "");
  assert.equal(popupRowsHtml([]), "");
  assert.equal(popupRowsHtml(null), "");
  // The order is the caller's, which is the order a rider reads.
  const order = popupRowsHtml([{ k: "B", v: "2" }, { k: "A", v: "1" }]);
  assert.ok(order.indexOf(">B<") < order.indexOf(">A<"));
  assert.match(popupRowsHtml([{ k: "Stop", v: "Penn & Broad" }]), /Penn &amp; Broad/);
});

test("MR5: a bucket heading takes the ferry's route colour and nothing else takes a colour at all", () => {
  assert.equal(popupDirHtml("Northbound"), '<div class="dir">Northbound</div>\n');
  assert.equal(popupDirHtml("East River", "#006f85"), '<div class="dir" style="color:#006f85">East River</div>\n');
  assert.equal(popupDirHtml(""), "", "an unnamed bucket prints no heading");
  assert.match(popupDirHtml("Penn & Broad"), /Penn &amp; Broad/);
});

test("MR5: an arrivals row is always three cells, and 'now' is accented from the app's own word", () => {
  const badge = '<span class="arr-badge" style="background:#0039A6;color:#ffffff">1</span>';
  const html = popupArrRowsHtml([
    { markHtml: badge, label: "Times Sq-42 St", countdown: "4 min" },
    { markHtml: badge, label: "", countdown: "now" },
  ]);
  assert.equal(
    html,
    '<div class="arr">' +
      `<span>${badge}</span>\n<span>Times Sq-42 St</span>\n<span class="n">4 min</span>\n` +
      `<span>${badge}</span>\n<span></span>\n<span class="n now">now</span>` +
      "</div>\n",
  );
  /* THREE CELLS EVEN WHEN TWO ARE EMPTY, because the grid places cells in order: a row that
     emitted two spans would slide its countdown into the middle column and print a board whose
     numbers do not line up under each other.

     AND THE ACCENT FOLLOWS THE WORD. Section 5 says a countdown under 30s reads "now" in the
     accent; formatCountdown is what decides a row reads "now" (countdownParts, at 30s), so the
     class is derived from the word and the threshold is not decided twice. */
  assert.equal(
    popupArrRowsHtml([{ countdown: "" }]),
    '<div class="arr"><span></span>\n<span></span>\n<span class="n"></span></div>\n',
  );
  assert.equal(popupArrRowsHtml([]), "", "and no rows means no grid");
  assert.equal(popupArrRowsHtml(null), "");
  // extraHtml is the row's own markup: the train number a feed carries, the freshness qualifier a
  // row earned. It goes in the flexible middle cell, never in the nowrap number cell.
  const extra = popupArrRowsHtml([
    { label: "Babylon", extraHtml: ' <span class="popup-sub">#514</span>', countdown: "6 min" },
  ]);
  assert.match(extra, /<span>Babylon <span class="popup-sub">#514<\/span><\/span>\n<span class="n">6 min<\/span>/);
  assert.match(popupArrRowsHtml([{ label: "Penn & Broad", countdown: "now" }]), /Penn &amp; Broad/);
});

test("MR5: the railroad head's two parts carry exactly the words the one string carried", () => {
  /* THE WORDS ARE THE SAME WORDS. formatRailroadHead joined an agency and a line for the one caller
     that wanted them joined, and MR5's popup wants them as a kicker and a title. These three cases
     are its three cases, and the joins below are its three outputs: a change that dropped a part
     would pass a test that only looked at the parts. */
  assert.deepEqual(railroadHeadParts("LIRR", "1", "Babylon Branch"), { agency: "LIRR", line: "Babylon Branch" });
  assert.deepEqual(railroadHeadParts("LIRR", "1", null), { agency: "LIRR", line: "route 1" });
  assert.deepEqual(railroadHeadParts("MNR", null, null), { agency: "MNR", line: "" });
  assert.deepEqual(railroadHeadParts(null, null, null), { agency: "", line: "" });

  /* AND JOINED BACK INTO THE THREE STRINGS THE DELETED FORMATTER RETURNED, which is the half a
     parts-only test would miss: a change that dropped the "route " prefix, or that returned the
     agency twice, would satisfy every assertion above. The three literals here are
     formatRailroadHead's own outputs, and they are the last place in this repository that holds
     them. Its own test was in helpers.test.js and came here with the words. */
  const named = railroadHeadParts("LIRR", "1", "Babylon Branch");
  assert.equal(`${named.agency} · ${named.line}`, "LIRR · Babylon Branch");
  const numbered = railroadHeadParts("LIRR", "1", null);
  assert.equal(`${numbered.agency} ${numbered.line}`, "LIRR route 1");
  assert.equal(railroadHeadParts("MNR", null, null).agency, "MNR");
  const newHaven = railroadHeadParts("MNR", "3", "New Haven");
  assert.equal(`${newHaven.agency} · ${newHaven.line}`, "MNR · New Haven");
});

test("MR5: every system word a kicker prints is one the app already says elsewhere", () => {
  /* THE DEFECT SHAPE THIS STAGE WAS TOLD TO WATCH FOR IS "a word that came from a literal instead
     of the app", and six literals in a table is exactly what that looks like from outside. So each
     one is asserted against the surface it came from:

       Subway, Buses, NJ Transit, PATH   the feed strip's own names (FEEDS)
       NYC Ferry                         the ferry popups' system tag and ferryBoatName's word,
                                         where the strip's button says the shorter "Ferry"
       AirTrain JFK                      airtrainStationName's and the AirTrain popup's own

     THE RAILROAD IS DELIBERATELY ABSENT: its kicker is the train's served `system` field, which is
     what its head has printed since phase 9. That word is the feed's code ("MNR") where every
     spoken surface says "Metro-North", and the divergence is recorded as an MR5 finding rather
     than quietly fixed here. */
  const stripNames = new Set(FEEDS.map((f) => f.name));
  for (const key of ["subway", "buses", "njt", "path"]) {
    assert.ok(stripNames.has(POPUP_SYSTEM_WORDS[key]), `${key}: the strip says ${POPUP_SYSTEM_WORDS[key]}`);
  }
  assert.ok(
    ferryBoatName({ label: "H201" }, "East River").includes(POPUP_SYSTEM_WORDS.ferry),
    "the ferry's word is the one its boat's name says",
  );
  assert.ok(
    airtrainStationName({ name: "Federal Circle" }).includes(POPUP_SYSTEM_WORDS.airtrain),
    "and AirTrain's is the one its station's name says",
  );
  assert.equal(Object.keys(POPUP_SYSTEM_WORDS).length, 6, "six words, and the railroad's is the payload's");
});
