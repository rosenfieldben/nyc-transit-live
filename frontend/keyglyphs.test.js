// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// MR4 ROUND 2: THE KEY PANEL'S GLYPHS, ASSERTED AGAINST THE MARKS THEY CLAIM TO BE.
//
// The Key panel is a picture of the map, and for two stages it was a picture of a map this app
// had stopped drawing: MR3 gave three railroads one tag and one commuter square and six rail
// rows went on showing purple squares, white rings and flat lines (finding Q9). Every guard the
// panel had would have let that stand for another two stages. `A1x` measures a row's INK,
// `A1z` measures a glyph's type against what is behind it, `P1e` reads the accessible names and
// strips the glyph on purpose, and the row counts count rows. NOTHING compared a glyph to the
// mark it is a picture of, so the one defect this panel can actually have was the one thing
// unguarded.
//
// SO THE ORACLE IS THE APP, NEVER A LIST. Each assertion below reads the drawn mark's own source
// (a builder's output, an exported constant, or the weights written at the draw site in
// systems/) and compares the Key's markup to it. A test that merely named "stroke-width 1.6"
// would be a second copy of a number and would agree with itself forever; this fails when the
// MAP changes and the panel does not follow, which is the direction the defect actually travels.
//
// IN NODE RATHER THAN IN THE BROWSER, because the claim is about the markup and node can read
// both files at once. What needs a browser is the one claim markup cannot carry, the RENDERED
// SIZE of the tag's type, and that is `a11y.spec.js A1x2`.
//
// THE LITERALS ARE H3's. Every Key glyph paints in the light theme's resolved tokens because
// `--glyph-plate` is #f3f2f2 in BOTH themes, so a glyph in var(--paper) would be a dark plate on
// a light one the moment a rider chose dark. The substitution is therefore part of the claim and
// is applied here rather than assumed: PAPER and INK below are the values style.css declares,
// read from style.css so a token edited without the glyphs following fails here too.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");

const {
  railStationSvg,
  railTagSvg,
  railTagChevronPath,
  railTagGeometry,
  railBranchPaint,
  railBranchColor,
  RAIL_TAG_HEIGHT,
  RIBBON_CASING_WEIGHT,
  RIBBON_CASING_OPACITY,
  RIBBON_LINE_WEIGHT,
  RIBBON_LINE_OPACITY,
  STATION_LOCAL_RADIUS,
  STATION_TRANSFER_RADIUS,
  STATION_TRANSFER_WEIGHT,
  AIRTRAIN_LINE_WEIGHT,
  AIRTRAIN_LINE_DASH,
} = require("./helpers.js");

const ROOT = join(__dirname, "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const HTML = read("frontend/index.html");
const CSS = read("frontend/style.css");
const RAILROAD = read("frontend/systems/railroad.js");

/* THE TWO LITERALS, READ OFF style.css RATHER THAN TYPED HERE. `:root` declares the light
   theme's pair and `:root[data-theme="dark"]` swaps them, so the first declaration of each is
   the one a Key glyph must carry. A test that hard-coded #f3f2f2 would keep passing after
   someone changed the token, which is the failure this whole file exists to avoid. */
function firstDeclared(prop) {
  const match = CSS.match(new RegExp("--" + prop + ":\\s*([^;]+);"));
  assert.ok(match, `style.css declares --${prop}`);
  return match[1].trim();
}
const PAPER = firstDeclared("paper");
const INK = firstDeclared("ink");
const PLATE = firstDeclared("glyph-plate");

test("1. the plate does not move between themes, which is why every glyph below is a literal", () => {
  // H3, asserted rather than cited. `--glyph-plate` is declared twice and both are the same
  // value; the moment one changes, a glyph drawn in the light theme's ink is wrong in one theme
  // and this file's whole substitution has to be reconsidered.
  const declared = [...CSS.matchAll(/--glyph-plate:\s*([^;]+);/g)].map((m) => m[1].trim());
  assert.equal(declared.length, 2, "one declaration per theme");
  assert.deepEqual(new Set(declared), new Set([PLATE]), "the plate is the same colour in both themes");
  assert.equal(PAPER, "#f3f2f2");
  assert.equal(INK, "#201e1d");
});

/* ===== THE PANEL, PARSED ==============================================================

   Rows are keyed by their ACCESSIBLE NAME, which is the text with the aria-hidden glyph
   removed: the same reading P1e pins, so the two specs cannot disagree about which row is
   which. Comments are stripped first, because this panel's markup carries more prose than
   markup and a comment quoting a hex would otherwise answer a question about a glyph. */
function keyRows() {
  const start = HTML.indexOf('<div id="legend"');
  const end = HTML.indexOf("</div><!-- /#legend -->");
  assert.ok(start > 0 && end > start, "the Key panel is still #legend");
  const panel = HTML.slice(start, end).replace(/<!--[\s\S]*?-->/g, "");
  const rows = new Map();
  for (const m of panel.matchAll(/<div class="legend-(?:row|note)"[^>]*>([\s\S]*?)<\/div>/g)) {
    const svg = (m[1].match(/<svg[\s\S]*?<\/svg>/) ?? [""])[0];
    const name = m[1].replace(/<svg[\s\S]*?<\/svg>/g, "").trim().replace(/\s+/g, " ");
    rows.set(name, svg);
  }
  return rows;
}

const ROWS = keyRows();
const row = (name) => {
  const svg = ROWS.get(name);
  assert.ok(svg !== undefined, `the Key panel still has the row "${name}"`);
  assert.ok(svg, `the row "${name}" still draws a glyph`);
  return svg;
};

// One element's attributes, as numbers where they parse as numbers. Positional: the nth
// element of that tag inside the glyph, because a route line row draws two <path>s and which
// one is the casing is decided by document order (the casing is drawn first, under).
function el(svg, tag, nth = 0) {
  const all = [...svg.matchAll(new RegExp("<" + tag + "\\b([^>]*)>", "g"))];
  assert.ok(all[nth], `the glyph has a ${nth + 1}th <${tag}>`);
  const attrs = {};
  for (const a of all[nth][1].matchAll(/([\w:-]+)="([^"]*)"/g)) {
    attrs[a[1]] = /^-?\d*\.?\d+$/.test(a[2]) ? Number(a[2]) : a[2];
  }
  return attrs;
}
const count = (svg, tag) => [...svg.matchAll(new RegExp("<" + tag + "\\b", "g"))].length;

/* ===== THE REGIONAL RAIL STATION SQUARE ================================================ */

test("2. the regional rail station row IS railStationSvg, with the tokens resolved", () => {
  const svg = row(
    "Regional rail station: LIRR, Metro-North, NJ Transit, AirTrain JFK " +
      "(click for arrivals or departures; AirTrain is scheduled only)",
  );
  // THE ORACLE IS THE BUILDER'S OWN OUTPUT, token for literal. One row, one builder, four
  // families: if railStationSvg ever draws something else, this fails without anyone having
  // to remember that the Key had a copy of it.
  const drawn = el(railStationSvg(), "rect");
  const keyed = el(svg, "rect");
  assert.equal(keyed.x, drawn.x, "same x as the mark");
  assert.equal(keyed.y, drawn.y, "same y");
  assert.equal(keyed.width, drawn.width, "same width");
  assert.equal(keyed.height, drawn.height, "same height");
  assert.equal(keyed["stroke-width"], drawn["stroke-width"], "same stroke weight");
  assert.equal(keyed.fill, PAPER, "the fill is the light theme's paper, H3");
  assert.equal(keyed.stroke, INK, "and the stroke its ink");
  assert.equal(el(svg, "svg").viewBox, el(railStationSvg(), "svg").viewBox, "same viewBox, so same apparent size");
  // ONE SQUARE, not four. The whole point of the merge is that the map has one mark here.
  assert.equal(count(svg, "rect"), 1);
});

test("3. no row claims a station square for one agency alone", () => {
  /* THE MERGE'S OTHER HALF, and the one a row count cannot see. Three rows drew three different
     station marks for families the map draws identically, and the reversal that matters is not
     "a row came back" (A1x and D2l catch that) but "a row names one agency's square again". */
  for (const name of ROWS.keys()) {
    if (!/station/i.test(name) || !/LIRR|Metro-North|NJ Transit|AirTrain/.test(name)) continue;
    assert.match(
      name,
      /^Regional rail station: LIRR, Metro-North, NJ Transit, AirTrain JFK/,
      `"${name}" claims a regional rail station for some of the four families rather than all of them`,
    );
  }
});

/* ===== THE TWO RAIL ROUTE LINES, AND THE SUBWAY'S ===================================== */

/* The rail ribbon's weights are written at the draw site rather than exported, so they are read
   from there. Two polylines per ribbon: paper casing first, then the branch colour. */
function railRibbonNumbers() {
  const body = RAILROAD.slice(RAILROAD.indexOf("function railDrawRibbons("));
  const stop = body.indexOf("\n}");
  const src = body.slice(0, stop);
  const nums = (after) => {
    const seg = src.slice(src.indexOf(after));
    return {
      weight: Number(seg.match(/weight:\s*([\d.]+)/)[1]),
      opacity: Number(seg.match(/opacity:\s*([\d.]+)/)[1]),
      cap: seg.match(/lineCap:\s*"(\w+)"/)[1],
    };
  };
  return { casing: nums("color: paper"), line: nums("color: ribbon.branch") };
}

for (const [name, colour, why] of [
  ["LIRR / Metro-North route line", "#00985F", "LIRR's published Babylon green"],
  ["NJ Transit route line", "#075AAA", "a published NJ Transit route colour"],
]) {
  test(`4. the ${name} row is a casing and a line, at railDrawRibbons' own numbers`, () => {
    const svg = row(name);
    const map = railRibbonNumbers();
    assert.equal(count(svg, "path"), 2, "a ribbon is two strokes, and a flat line is the Q9 defect");
    const casing = el(svg, "path", 0);
    const line = el(svg, "path", 1);
    assert.equal(casing["stroke-width"], map.casing.weight, "the casing's weight is the map's");
    assert.equal(casing["stroke-opacity"], map.casing.opacity, "and its opacity");
    assert.equal(casing.stroke, PAPER, "the casing is paper, H3's literal");
    assert.equal(casing["stroke-linecap"], map.casing.cap, "and round-capped like the map's");
    assert.equal(line["stroke-width"], map.line.weight, "the line's weight is the map's");
    assert.equal(line["stroke-linecap"], map.line.cap);
    assert.equal(line.stroke, colour, `the line is ${why}`);
    /* AND NO opacity ATTRIBUTE ON THE LINE. This is the second half of the Q9 defect and the
       half a casing check alone would miss: both rows carried opacity="0.6", a number the map
       has never drawn for a rail line. RIBBON_LINE_OPACITY is 1, and 1 is written by leaving
       the attribute off, so its PRESENCE is the failure whatever its value. */
    assert.equal(RIBBON_LINE_OPACITY, map.line.opacity, "the two ribbon families draw at one line opacity");
    assert.equal(line.opacity, undefined, "a rail line at less than full opacity is the Q9 defect");
    assert.equal(line["stroke-opacity"], undefined);
    assert.equal(casing.d, line.d, "one curve in two strokes, which is what a casing is");
  });
}

test("5. the subway route line row keeps the subway's own heavier ribbon", () => {
  // The control for the pair above: the rail ribbon must read THINNER than the subway's, which
  // is what the map draws, and a copy-paste that gave the rail rows the subway's 6.5 would
  // otherwise pass every assertion in this file.
  const svg = row("Subway route line");
  assert.equal(el(svg, "path", 0)["stroke-width"], RIBBON_CASING_WEIGHT);
  assert.equal(el(svg, "path", 0)["stroke-opacity"], RIBBON_CASING_OPACITY);
  assert.equal(el(svg, "path", 1)["stroke-width"], RIBBON_LINE_WEIGHT);
  const rail = railRibbonNumbers();
  assert.ok(rail.casing.weight < RIBBON_CASING_WEIGHT, "a rail casing is thinner than a subway trunk's");
  assert.ok(rail.line.weight < RIBBON_LINE_WEIGHT, "and so is its line");
});

test("6. the AirTrain guideway row is airtrainLineStyle's dash, weight and opacity", () => {
  // Unchanged by this round and asserted anyway: it is the one rail row that was already right,
  // so it is also the row a later stage could break while "fixing" its neighbours.
  const line = el(row("AirTrain JFK route line (scheduled service, no live tracking)"), "path");
  assert.equal(line["stroke-width"], AIRTRAIN_LINE_WEIGHT);
  assert.equal(line["stroke-dasharray"], AIRTRAIN_LINE_DASH);
  assert.equal(line.opacity, 0.9);
});

/* ===== THE TWO SUBWAY STATION ROWS (F16) ============================================== */

test("7. the two subway station rows are stationMarkStyle's two branches, one each", () => {
  const dot = row("Subway station (click for arrivals)");
  // The operator's words since claude/subway-hub-definition: what the ring means, not its rule.
  const ring = row("Transfer station: change between lines here");

  // ONE MARK PER ROW, which is finding F16 in one assertion: the defect was a glyph drawing two
  // marks beside a caption describing one thing.
  assert.equal(count(dot, "circle"), 1, "the station row draws the local dot alone");
  assert.equal(count(ring, "circle"), 1, "and the transfer row the ring alone");

  const d = el(dot, "circle");
  assert.equal(d.r, STATION_LOCAL_RADIUS, "the local dot's radius is the map's");
  assert.equal(d.fill, INK, "filled ink, as stationMarkStyle's local branch draws it");
  assert.equal(d.stroke, undefined, "and unstroked: weight 0, stroke false");

  const r = el(ring, "circle");
  assert.equal(r.r, STATION_TRANSFER_RADIUS, "the ring's radius is the map's");
  assert.equal(r["stroke-width"], STATION_TRANSFER_WEIGHT, "and its weight");
  assert.equal(r.fill, PAPER, "paper-filled");
  assert.equal(r.stroke, INK, "in an ink stroke");
  assert.ok(r.r > d.r, "the ring is the larger mark, as the map draws it");
});

test("7b. the PATH station row draws the same local dot, which MR4 decided on purpose", () => {
  /* NOT A CHANGED ROW, and asserted anyway. MR4 replaced PATH's slate disc with the subway's
     local dot through stationMarkStyle([], ink, paper), and recorded the cost out loud in
     path.js: "a PATH local dot and a subway local dot are the same mark". So these two Key rows
     drawing the identical glyph is the decision working rather than a copy-paste, and that is
     worth an assertion precisely because it LOOKS like a mistake: the next reader who "fixes"
     the duplication by giving PATH its disc back fails here, and reads why. */
  const path = el(row("PATH station (click for arrivals); trains are diamonds"), "circle");
  const subway = el(row("Subway station (click for arrivals)"), "circle");
  assert.deepEqual(path, subway, "one local dot, drawn by one branch of stationMarkStyle");
  assert.equal(path.r, STATION_LOCAL_RADIUS);
  assert.equal(path.fill, INK);
});

/* ===== THE TWO COMMUTER TAG ROWS ====================================================== */

/* THE ORACLE IS railTagSvg ITSELF. The Key's glyph is the tag's BODY with the tokens resolved
   and the stem and head dropped, so the comparison is made on the body's elements one at a
   time rather than on the whole string: dropping the stem and the head is a decision this round
   made deliberately (the captions name the body axis and only that), and a string comparison
   could not tell it apart from a glyph that had lost an element by accident. */
const tagRows = [
  {
    name: "LIRR / Metro-North / NJ Transit train (live GPS)",
    system: "LIRR",
    code: "BAB",
    colour: "00985F",
    state: { body: "solid", head: "filled", headingTrusted: true },
  },
  {
    name:
      "LIRR / Metro-North / NJ Transit train (scheduled or estimated, no GPS); " +
      "NJ Transit is always this",
    system: "NJT",
    code: "NEC",
    colour: "DD3439",
    state: { body: "outlined", head: "outlined", headingTrusted: false },
  },
];

for (const spec of tagRows) {
  test(`8. the ${spec.state.body} commuter tag row is railTagSvg's ${spec.state.body} body`, () => {
    const svg = row(spec.name);
    const geom = railTagGeometry(spec.system, spec.code);
    const mark = railTagSvg({ system: spec.system, code: spec.code, color: spec.colour, state: spec.state });
    const resolve = (s) =>
      s
        .replace(/style="fill: var\(--paper\)"/g, `fill="${PAPER}"`)
        .replace(/style="fill: var\(--ink\)"/g, `fill="${INK}"`)
        .replace(/style="stroke: var\(--ink\)"/g, `stroke="${INK}"`)
        .replace(/style="fill: var\(--paper\); stroke: var\(--ink\)"/g, `fill="${PAPER}" stroke="${INK}"`);

    // THE BLOCK WIDTHS ARE THE GEOMETRY'S, so a Key glyph cannot quietly become a square.
    if (spec.state.body === "solid") {
      const agency = el(svg, "rect", 1);
      const branch = el(svg, "rect", 2);
      assert.equal(agency.width, geom.agencyWidth, "the agency block's width is railTagGeometry's");
      assert.equal(agency.height, RAIL_TAG_HEIGHT);
      assert.equal(agency.fill, INK);
      assert.equal(branch.x, geom.agencyWidth, "the branch block starts where the agency block ends");
      assert.equal(branch.width, geom.codeWidth, "and its width is code.length x 5.6 + 7, rounded");
      assert.equal(branch.fill, railBranchPaint(spec.colour).fill, "the published colour, or the one it moved to");
      assert.equal(el(svg, "rect", 0).fill, PAPER, "the paper plate is still 1px larger at 0.9");
      assert.equal(el(svg, "rect", 0).opacity, 0.9);
    } else {
      const box = el(svg, "rect", 0);
      const stripe = el(svg, "rect", 1);
      assert.equal(box.width, geom.width - 1.2, "the outlined box is inset by its own stroke");
      assert.equal(box.fill, PAPER);
      assert.equal(box.stroke, INK);
      assert.equal(el(svg, "line").x1, geom.agencyWidth, "the divider sits at the block edge");
      assert.equal(stripe.fill, railBranchColor(spec.colour), "the stripe is the PUBLISHED colour, uncorrected");
      assert.ok(stripe.y > RAIL_TAG_HEIGHT / 2, "and it sits under the type rather than behind it");
    }

    // TWO TEXTS, THE AGENCY GLYPH AND THE CODE, and each carries its own font because
    // `.rail-tag-marker svg text` is scoped to the MARKER and does not reach this panel.
    const texts = [...svg.matchAll(/<text\b([^>]*)>([^<]*)</g)].map((m) => ({ attrs: m[1], body: m[2] }));
    assert.equal(texts.length, 2, "an agency glyph and a branch code");
    assert.equal(texts[0].body, geom.glyph, "the agency glyph railTagGeometry names");
    assert.equal(texts[1].body, spec.code, "and the branch code");
    for (const text of texts) {
      assert.match(text.attrs, /font-size="8"/, "8 user units, which is the map's own type size");
      assert.match(text.attrs, /font-weight="800"/);
      assert.match(text.attrs, /font-family="Archivo, system-ui, sans-serif"/, "inline, not inherited");
    }

    // AND THE STEM AND THE HEAD ARE DELIBERATELY ABSENT. The map's mark has both; these two
    // captions name the BODY axis only, and a glyph drawing a head its caption never explains
    // is finding F16 in a new row. Asserted so it reads as a decision rather than an omission,
    // and so the day a ruling adds the head axis this test is what has to be rewritten.
    assert.ok(resolve(mark).includes("r=\"3\"") || /<path d="M /.test(mark), "the MAP's tag has a head");
    assert.equal(count(svg, "circle"), 0, "no head in the Key glyph");
    assert.equal(count(svg, "path"), 0, "and no chevron");
    if (spec.state.body === "solid") assert.equal(count(svg, "line"), 0, "and no stem");

    // THE CELL IS THE TAG, CROPPED. The viewBox's height is the tag's 13 padded to 16, and its
    // width is the widest tag this panel draws, so both rows share one scale. The RENDERED size
    // that follows from it is a browser claim and belongs to a11y.spec.js A1x2.
    const view = String(el(svg, "svg").viewBox).split(/\s+/).map(Number);
    assert.equal(view[3], 16, "16 tall, which is the shared cell's height and the tag's 13 plus air");
    assert.ok(view[1] < 0 && view[1] > -RAIL_TAG_HEIGHT, "with the tag centred in it");
    assert.equal(view[2], 42, "42 wide, which is railTagGeometry('NJT','NEC').width + 2");
    assert.equal(view[2], railTagGeometry("NJT", "NEC").width + 2, "and that is where the number comes from");
    assert.equal(view[0], -(view[2] - geom.width) / 2, "each tag centred, so the two rows share one scale");
    // The class the CSS rule and A1x2 both key on, and NOT the map's own.
    assert.equal(el(svg, "svg").class, "key-rail-tag");
    assert.ok(new RegExp("\\.legend-row svg\\.key-rail-tag\\s*\\{[^}]*width:\\s*" + view[2] + "px").test(CSS),
      "style.css gives the cell the viewBox's own width, which is what makes the scale 1");
  });
}

test("9. no Key glyph takes the map's own rail-tag class", () => {
  // a11y.spec.js A1z4 counts svg.rail-tag and asserts every one belongs to a .rail-tag-marker.
  // A copy-paste that brought the class along would widen that exception silently on the node
  // tier and fail confusingly on the page tier; this says why, here.
  const panel = HTML.slice(HTML.indexOf('<div id="legend"'), HTML.indexOf("</div><!-- /#legend -->"));
  // The lookbehind is the point: `key-rail-tag` CONTAINS `rail-tag`, and a \b matches at the
  // hyphen, so the naive pattern reports the correct markup as the defect. A class token starts
  // at a word boundary that is not a hyphen.
  assert.equal(/class="[^"]*(?<![-\w])rail-tag\b/.test(panel), false, "the Key's tags are key-rail-tag");
  // AND THE CONTROL, so the pattern above is not a regex that can never match: the map's own
  // builder does carry the class, and this asserts the test would see it.
  assert.equal(/class="[^"]*(?<![-\w])rail-tag\b/.test(railTagSvg({
    system: "LIRR", code: "BAB", color: "00985F", state: { body: "solid", head: "filled", headingTrusted: true },
  })), true, "and the map's own tag is what the pattern is looking for");
});

/* MR5, finding F17: THE TWO HEAD ROWS, AND THEY ARE THE MAP'S OWN HEADS.

   MR4 round 2 added the two commuter train rows and drew no head on either, because those
   captions name the BODY state and a glyph drawing a mark its caption never explains is F16
   in a new row. It recorded the head as F17. These are the rows, and there are two because
   the head is two independent questions rather than a list of four shapes: is the heading
   TRUSTED (filled or outlined), and is a heading SERVED at all (chevron or dot).

   THE ORACLE IS railTagSvg'S OWN OUTPUT, not a number typed here. `RAIL_TAG_DOT_R` and
   `RAIL_TAG_TRACK_Y` are not exported, and exporting them to assert against would only move
   the copy: the head this panel draws is right if and only if it is the head the map draws,
   so the map's builder is asked for one and the two are compared. Same discipline as test 8
   above, where the tag body's oracle is railTagSvg rather than a transcription of it. */
test("10. the two commuter head rows draw the map's own chevron and dot", () => {
  const trusted = row("Commuter train heading: filled when it is trusted, outlined when it is not");
  const served = row("Commuter train heading: a chevron when one is served, a dot when none is");

  // The map's heads, asked for rather than transcribed. A chevron at bearing 0 is the same
  // path railTagChevronPath returns; a dot-headed tag carries the circle.
  const mapChevron = railTagSvg({
    system: "LIRR", code: "BAB", color: "00985F",
    state: { body: "solid", head: "filled", headingTrusted: true }, bearing: 0,
  });
  const mapDot = railTagSvg({
    system: "LIRR", code: "BAB", color: "00985F",
    state: { body: "outlined", head: "outlined", headingTrusted: false },
  });
  const mapCircle = el(mapDot, "circle");

  /* THE PATHS ARE railTagChevronPath'S, TRANSLATED AND NOT REDRAWN. The map centres its
     chevron on the tag's own centre; the Key places two of them side by side, so the claim
     is that each row's paths are exactly what the function returns for the cx it was given.
     A glyph that drew a chevron of its own shape would fail here even if it looked right. */
  for (const glyph of [trusted, served]) {
    const first = el(glyph, "path", 0);
    assert.equal(first.d, railTagChevronPath(5), "the left mark is railTagChevronPath at cx 5");
  }
  assert.equal(el(trusted, "path", 1).d, railTagChevronPath(18), "the right mark is the same path at cx 18");

  /* ROW ONE IS THE FILL AXIS: the same shape twice, in the map's two head paints. Filled is
     ink with a 1-unit paper stroke, outlined is paper with a 1.4-unit ink stroke, and the
     1.4 is the heavier edge a hollow shape this small needs to stay a shape. */
  const filled = el(trusted, "path", 0);
  const outlined = el(trusted, "path", 1);
  assert.equal(filled.fill, INK);
  assert.equal(filled.stroke, PAPER);
  assert.equal(outlined.fill, PAPER);
  assert.equal(outlined.stroke, INK);
  assert.equal(outlined["stroke-width"], mapCircle["stroke-width"],
    "the outlined head's edge is the weight the map's outlined head uses");
  assert.ok(outlined["stroke-width"] > filled["stroke-width"],
    "an outlined head carries a heavier edge than a filled one, as the map draws it");

  /* ROW TWO IS THE SHAPE AXIS, so its fill is held CONSTANT: both marks are the filled form
     and the only thing that varies is chevron against dot. A row that varied both at once
     would be a second list rather than an axis, which is the whole reason F17 is two rows. */
  const chevron = el(served, "path", 0);
  const dot = el(served, "circle");
  assert.equal(chevron.fill, dot.fill, "row two holds the fill constant; only the shape varies");
  assert.equal(chevron.stroke, dot.stroke);
  assert.equal(dot.r, mapCircle.r, "the dot's radius is the map's RAIL_TAG_DOT_R");
  assert.equal(dot.cy, mapCircle.cy, "and it sits on the map's own track line");

  // Both glyphs open their viewBox at the chevron's own y, so nothing is re-scaled to fit:
  // the marks are the map's geometry translated, which is what the two assertions above mean.
  for (const glyph of [trusted, served]) {
    const view = String(el(glyph, "svg").viewBox).split(/\s+/).map(Number);
    assert.equal(view[1], 15.5, "the viewBox starts where railTagChevronPath starts");
    assert.equal(view[3], 9, "and is the chevron's own height");
  }
});
