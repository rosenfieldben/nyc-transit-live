// Run with: node --test "frontend/*.test.js"  (from the repo root)
//
// MR4, the other four families and the dark theme's release. Everything here is pure, for
// the reason MR3's railtag.test.js gives: a mark built as a STRING can be asked one state at
// a time in node, and a mark built inside an L.divIcon can only be photographed.
//
// THE THEME REGISTRY IS ASSERTED AGAINST THE SOURCE, NOT AGAINST A LIST. The operator asked
// for the restyle list to be held "against a registry rather than a literal", and a node test
// that simply named the five families would be a second copy of the registry: it would agree
// with itself forever and catch nothing. So the test below SCRAPES every canvas style site
// that resolves a theme token out of systems/, and asserts that each file which has one also
// registers a family. A sixth family added in a later stage fails here until it registers.

const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync, readdirSync } = require("node:fs");
const { join } = require("node:path");
const { Script } = require("node:vm");

const {
  busMarkColor,
  busMarkColorAt,
  busMarkHue,
  BUS_MARK_SATURATION,
  BUS_MARK_LIGHTNESS,
  BUS_MARK_LIGHTNESS_DARK,
  BUS_MARK_LIGHTNESS_TOKEN,
  routeColor,
  pathDiamondSvg,
  PATH_DIAMOND_BOX,
  PATH_DIAMOND_PATH,
  ferryHullSvg,
  FERRY_HULL_BOX,
  FERRY_HULL_PATH,
  ferryDockStyle,
  FERRY_DOCK_COLOR,
  FERRY_DOCK_RADIUS,
  busMarkSvg,
  busHasHeading,
  BUS_MARK_BOX,
  BUS_ARROW_PATH,
  BUS_DOT_R,
  airtrainLineStyle,
  AIRTRAIN_LINE_DASH,
  AIRTRAIN_LINE_WEIGHT,
  contrastRatio,
  stationMarkStyle,
  STATION_LOCAL_RADIUS,
} = require("./helpers.js");

const SYSTEMS = join(__dirname, "systems");
const src = (name) => readFileSync(join(SYSTEMS, name), "utf8");

/* ---------------- the four marks ---------------- */

test("MR4 PATH: the diamond is the design's path, route fill, paper stroke, and no casing", () => {
  const svg = pathDiamondSvg("#abc123");
  assert.equal(PATH_DIAMOND_BOX, 16);
  assert.equal(PATH_DIAMOND_PATH, "M8 1 L15 8 L8 15 L1 8 Z");
  assert.match(svg, /viewBox="0 0 16 16"/);
  assert.ok(svg.includes(PATH_DIAMOND_PATH));
  assert.ok(svg.includes("fill: #abc123"));
  /* THE STROKE IS A CUSTOM PROPERTY IN AN INLINE STYLE, which is the whole theme mechanism
     for this family and the one way of writing it that works. `stroke="var(--paper)"` as an
     SVG 1.1 presentation attribute is not a paint value: it resolves to nothing and draws
     nothing, silently. THIS IS THE MUTATION: move the paint to an attribute. */
  assert.ok(svg.includes("stroke: var(--paper)"), "the stroke must be a token in a style");
  assert.doesNotMatch(svg, /stroke="var\(/, "an SVG attribute cannot carry a custom property");
  assert.match(svg, /stroke-width="1\.2"/);
  // NO CASING, which is the one thing the design says PATH does not get: one path, not two.
  assert.equal(svg.match(/<path /g).length, 1);
});

test("MR4 ferry: the boat is a hull and not a rounded rect, which is what the file always claimed", () => {
  const svg = ferryHullSvg("#00839c");
  assert.deepEqual(FERRY_HULL_BOX, [22, 14]);
  assert.equal(FERRY_HULL_PATH, "M1 3 H21 L17.5 11 H4.5 Z");
  assert.ok(svg.includes(FERRY_HULL_PATH));
  assert.ok(svg.includes("stroke: var(--paper)"));
  assert.match(svg, /stroke-width="1"/);
  // A TRAPEZOID, NOT A RECTANGLE: the deck is wider than the keel, which is the whole reason
  // a boat reads as a boat beside four other shapes. Asserted on the geometry rather than on
  // the string, so a path that happened to contain the right characters could not pass.
  assert.doesNotMatch(svg, /<rect/, "the hull is a path; a rect is the shape it replaced");
  const deck = 21 - 1;
  const keel = 17.5 - 4.5;
  assert.ok(keel < deck, "the keel must be narrower than the deck");
});

test("MR4 ferry: a dock's options are the design's, and the ring is the caller's paper", () => {
  const style = ferryDockStyle("#f3f2f2");
  assert.equal(FERRY_DOCK_RADIUS, 4);
  assert.equal(style.radius, 4);
  // THE DESIGN'S CYAN, which is also the colour the feed strip's ferry tick has drawn since
  // MR1. The dock used to be #0e7490 while the strip said #00839c, so one family pointed at
  // two colours; this is the one that was already right everywhere else.
  assert.equal(FERRY_DOCK_COLOR, "#00839c");
  assert.equal(style.fillColor, "#00839c");
  /* THE RING IS THE CALLER'S, NOT A LITERAL AND NOT READ IN HERE. A dock is a canvas
     circleMarker and a canvas cannot resolve a custom property, so the token is resolved at
     the call site and passed in; this function stays pure and node-askable. THE MUTATION is
     hardcoding "#fff" here, which would draw a light halo on a dark map: this is one of the
     two marks ledger finding G15 measured at 2.63 against the dark surface. */
  assert.equal(style.color, "#f3f2f2");
  assert.equal(ferryDockStyle("#201e1d").color, "#201e1d");
  assert.equal(style.weight, 1.5);
  assert.equal(style.stroke, true);
});

test("MR4 AirTrain: the guideway is gray, dashed, and takes its gray from the caller", () => {
  const style = airtrainLineStyle("#6d6e71");
  assert.equal(AIRTRAIN_LINE_WEIGHT, 3);
  assert.equal(AIRTRAIN_LINE_DASH, "8 5");
  assert.equal(style.weight, 3);
  assert.equal(style.dashArray, "8 5");
  /* TWO VALUES, WHICH IS WHY IT IS A PARAMETER. The design gives AirTrain #6d6e71 light and
     #9a9a9a dark, and style.css has carried both as `--scheduled` since MR1 with nothing able
     to read it: a polyline takes a colour STRING. THE MUTATION is a module constant, which
     would leave the guideway magenta-era-style theme-blind. */
  assert.equal(style.color, "#6d6e71");
  assert.equal(airtrainLineStyle("#9a9a9a").color, "#9a9a9a");
});

test("MR4 buses: an arrow when a heading is served, a dot when one is not, and never the reverse", () => {
  assert.equal(BUS_MARK_BOX, 14);
  assert.equal(BUS_ARROW_PATH, "M7 1 L12 13 L7 10 L2 13 Z");
  assert.equal(BUS_DOT_R, 3.5);

  const arrow = busMarkSvg("#123456", 90);
  assert.ok(arrow.includes(BUS_ARROW_PATH));
  assert.match(arrow, /transform: rotate\(90deg\)/);
  assert.match(arrow, /stroke-width="0\.8"/);

  const dot = busMarkSvg("#123456", null);
  assert.match(dot, /<circle /);
  assert.match(dot, /r="3\.5"/);
  assert.match(dot, /stroke-width="1"/);
  assert.doesNotMatch(dot, /rotate\(/, "a dot has no heading to be rotated to");

  /* THE PREDICATE, AND IT IS THE MUTATION THE OPERATOR NAMED: the arrow drawn when no heading
     is served would be a direction invented out of a missing field. A served null, an absent
     field, a NaN and a non-number are all the dot. */
  assert.equal(busHasHeading({ bearing: 0 }), true, "due north is a heading, not a missing one");
  assert.equal(busHasHeading({ bearing: 270 }), true);
  assert.equal(busHasHeading({ bearing: null }), false);
  assert.equal(busHasHeading({}), false);
  assert.equal(busHasHeading({ bearing: NaN }), false);
  assert.equal(busHasHeading({ bearing: "90" }), false, "a string is not a bearing");
  assert.equal(busHasHeading(null), false);
  // ZERO IS A HEADING. `bearing != null` and `Number.isFinite` agree here and a truthiness
  // test would not: a bus pointed due north would have lost its arrow.
  assert.match(busMarkSvg("#123456", 0), /rotate\(0deg\)/);
  // And the rotation is normalised into [0, 360), so a served 450 or -90 is a real angle.
  assert.match(busMarkSvg("#123456", 450), /rotate\(90deg\)/);
  assert.match(busMarkSvg("#123456", -90), /rotate\(270deg\)/);

  // ONE BOX FOR BOTH STATES, so a bus gaining or losing a heading swaps its glyph without
  // moving its anchor under the rider's pointer between polls.
  assert.match(arrow, /viewBox="0 0 14 14"/);
  assert.match(dot, /viewBox="0 0 14 14"/);
});

/* ---------------- the muted hue ---------------- */

test("MR4 buses: the muted hue keeps routeColor's hue, and its lightness is the theme's", () => {
  assert.equal(BUS_MARK_SATURATION, 45);
  assert.equal(BUS_MARK_LIGHTNESS, 38, "the README's value, which is the light theme's");
  assert.equal(BUS_MARK_LIGHTNESS_DARK, 60);
  // THE HUE IS UNCHANGED, which is what "the existing hashed hue but muted" means: two buses
  // on one route are one colour and two routes are two, whichever function asks.
  for (const route of ["M15", "B46", "Bx12", "Q58", "SIM1"]) {
    const [, hue] = /^hsl\((\d+),/.exec(routeColor(route));
    assert.equal(busMarkHue(route), Number(hue), route);
    assert.equal(busMarkColorAt(route, 38), `hsl(${hue}, 45%, 38%)`, route);
  }
  assert.equal(busMarkColor("M15"), busMarkColor("M15"));
  assert.notEqual(busMarkColor("M15"), busMarkColor("B46"));
  // A route with no id has no hue to mute, so it keeps routeColor's flat grey at either end.
  assert.equal(busMarkColor(null), routeColor(null));
  assert.equal(busMarkColorAt(null, 60), routeColor(null));
  assert.equal(busMarkColor(""), routeColor(""));

  /* THE LIGHTNESS THE PAGE DRAWS IS THE TOKEN, not a literal, and that is the whole mechanism:
     a custom property is substituted before the value is parsed, so one string is a real
     colour in both themes and follows a swap through the cascade with no rebuild, exactly as
     the `var(--paper)` stroke beside it does. THE FALLBACK IS THE README'S 38%, so a context
     with no stylesheet (node, frontend/boards.test.js) still gets a real colour. */
  assert.equal(busMarkColor("M15"), "hsl(329, 45%, var(--bus-mark-lightness, 38%))");
  assert.match(BUS_MARK_LIGHTNESS_TOKEN, /^var\(--bus-mark-lightness, 38%\)$/);
});

test("MR4 buses: every one of the 360 hashed hues clears 3:1 in BOTH themes, and neither end alone does", () => {
  /* THE MEASUREMENT THIS STAGE OWES G15, RUN RATHER THAN QUOTED. A bus route's colour is a
     HASH of its id, so whether a given route's arrow is legible used to be luck; and the
     answer is different in each theme because the surface is. Sweeping all 360 hues against
     both papers is the only form this claim has.

     THIS IS ALSO THE MUTATION THE OPERATOR NAMED (the raw hashed hue in place of the muted
     one) AND ONE MORE BESIDE IT (one lightness for both themes), and the second is the reason
     the token exists: 38% is perfect in light and worst in dark, 60% the reverse. */
  const PAPER_LIGHT = "#f3f2f2";
  const PAPER_DARK = "#201e1d";
  const SURFACE_DARK = "#2d2b2b"; // the dark panel plate, a lighter surface than its paper
  const sweep = (lightness, base, saturation = BUS_MARK_SATURATION) => {
    let worst = Infinity;
    let under = 0;
    for (let h = 0; h < 360; h++) {
      const ratio = contrastRatio(`hsl(${h}, ${saturation}%, ${lightness}%)`, base);
      if (ratio == null) continue;
      worst = Math.min(worst, ratio);
      if (ratio < 3) under += 1;
    }
    return { under, worst: Number(worst.toFixed(2)) };
  };

  // EACH THEME'S OWN VALUE CLEARS ITS OWN SURFACE, every hue, with nothing under.
  const light = sweep(BUS_MARK_LIGHTNESS, PAPER_LIGHT);
  assert.equal(light.under, 0, `light: ${light.under} hues under 3:1 (worst ${light.worst})`);
  assert.ok(light.worst >= 3, `light worst is ${light.worst}`);
  const dark = sweep(BUS_MARK_LIGHTNESS_DARK, PAPER_DARK);
  assert.equal(dark.under, 0, `dark: ${dark.under} hues under 3:1 (worst ${dark.worst})`);
  assert.ok(dark.worst >= 3, `dark worst is ${dark.worst}`);
  // AND THE DARK END CLEARS THE LIGHTER OF THE TWO DARK SURFACES TOO, which is the margin
  // against a basemap tile that is not exactly the theme's paper.
  const onPlate = sweep(BUS_MARK_LIGHTNESS_DARK, SURFACE_DARK);
  assert.equal(onPlate.under, 0, `dark on --surface: ${onPlate.under} under (worst ${onPlate.worst})`);

  /* AND NEITHER END WOULD DO ON ITS OWN, which is what makes the token necessary rather than
     tidy. A stage that deleted the dark value and left the README's 38% everywhere would draw
     188 of 360 hues under the floor on a dark map, and the mirror mistake is just as bad. */
  const lightValueInDark = sweep(BUS_MARK_LIGHTNESS, PAPER_DARK);
  assert.ok(
    lightValueInDark.under > 150,
    `the light value in the dark theme leaves ${lightValueInDark.under} hues under 3:1, which is why there are two`,
  );
  const darkValueInLight = sweep(BUS_MARK_LIGHTNESS_DARK, PAPER_LIGHT);
  assert.ok(
    darkValueInLight.under > 150,
    `the dark value in the light theme leaves ${darkValueInLight.under} hues under 3:1`,
  );

  /* AND THE HUE IT REPLACES, MEASURED, because "muted" has to be worth something. routeColor's
     own hsl(h, 75%, 40%) is what every bus arrow was drawn in before this stage, and on the
     light paper it is stroked against it leaves a large minority of the hashed hues under the
     floor. Both numbers move: the saturation is what makes the worst hues dark and the
     lightness is what makes them close to the paper, so the sweep is given both. */
  const raw = sweep(40, PAPER_LIGHT, 75);
  assert.ok(raw.under > 100, `the unmuted hue leaves ${raw.under} hues under 3:1 (worst ${raw.worst})`);
  // AND THE MUTED ONE IS STRICTLY BETTER AT THE SAME LIGHTNESS, which separates the two moves:
  // lowering the saturation alone already rescues most of them.
  assert.ok(sweep(40, PAPER_LIGHT).under < raw.under);
});

/* ---------------- PATH's station, which is the subway's local dot ---------------- */

test("MR4 PATH: a station is the subway's LOCAL dot, and never the transfer ring", () => {
  /* THE OPERATOR'S RULING AND THE DESIGN'S WORD ("Stations: subway 'local' dot style"), drawn
     through the one function that knows what a local dot is rather than through a second copy
     of its radius and fill.

     ROUTES: [] IS LOAD-BEARING. A PATH station is not a subway transfer station, and
     isTransferStation decides the ring from subway TRUNKS it has no business being asked
     about; passing the station's own routes would let a PATH stop with two PATH services draw
     an interchange ring it has not earned. */
  const style = stationMarkStyle([], "#201e1d", "#f3f2f2");
  assert.equal(style.radius, STATION_LOCAL_RADIUS);
  assert.equal(style.fillColor, "#201e1d");
  assert.equal(style.stroke, false);
  assert.equal(style.weight, 0);
  // AND IT FOLLOWS THE THEME, which the slate-blue disc it replaces could not: both colours
  // are the caller's, so the repaint and the draw are one expression.
  assert.equal(stationMarkStyle([], "#f3f2f2", "#201e1d").fillColor, "#f3f2f2");
});

/* ---------------- the theme registry, asserted against the source ---------------- */

/* THE SCRAPE READS CODE, NOT PROSE, and it is worth saying why this is a comment stripper and
   not a looser regex. The first draft of the test below matched the resolver anywhere in a
   file and went red on njt.js, whose only mention of paperColor() is a sentence in the MR3
   comment explaining that railDrawRibbons "gets paperColor() for free". njt.js resolves no
   token and owes no registration: the match was prose. The honest repair is to ask the
   question of the program rather than of the file, so the scrape strips comments first.
   Strings are LEFT ALONE: a stripper's failure mode is eating code, and keeping more than it
   must can only make the test stricter. */
function stripComments(source) {
  let out = "";
  let i = 0;
  const n = source.length;
  while (i < n) {
    const c = source[i];
    const next = source[i + 1];
    if (c === "/" && next === "/") {
      while (i < n && source[i] !== "\n") i += 1;
      continue;
    }
    if (c === "/" && next === "*") {
      i += 2;
      while (i < n && !(source[i] === "*" && source[i + 1] === "/")) {
        if (source[i] === "\n") out += "\n"; // the stripped file keeps its line numbers
        i += 1;
      }
      i += 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      out += c;
      i += 1;
      while (i < n) {
        if (source[i] === "\\") {
          out += source.slice(i, i + 2);
          i += 2;
          continue;
        }
        out += source[i];
        i += 1;
        if (source[i - 1] === c) break;
      }
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

test("MR4 theme: the scrape below reads code and is not fooled by prose, nor eats a string", () => {
  /* THE STRIPPER IS TESTED RATHER THAN TRUSTED. A stripper that quietly ate a line of code
     would hide a call site and leave the registry test passing over nothing, which is one of
     the four shapes this phase's defects keep taking: a test that cannot fail. */
  const stripped = stripComments(
    [
      "// paperColor() named in a line comment",
      "/* paperColor() named in a block comment",
      "   and carried onto a second line */",
      'const label = "paperColor() inside a string";',
      "const ink = inkColor(); // and a trailing comment after real code",
      "const ratio = a / b / c;",
    ].join("\n"),
  );
  assert.doesNotMatch(stripped, /paperColor\(\) named/, "prose is not a call site");
  assert.match(stripped, /const ink = inkColor\(\);/, "a call site survives a trailing comment");
  assert.match(stripped, /"paperColor\(\) inside a string"/, "a stripper must not eat a string");
  assert.match(stripped, /a \/ b \/ c/, "division is not a comment");
  // AND THE LINE NUMBERS SURVIVE, so a syntax error reported against the stripped file points
  // at the line the real file has it on.
  assert.equal(stripped.split("\n").length, 6);
});

test("MR4 theme: every file that resolves a theme token for a canvas mark registers a family", () => {
  /* THE REGISTRY IS CHECKED AGAINST THE SOURCE AND NOT AGAINST A LIST WRITTEN HERE, which is
     what the operator asked for. A test that named the five families would be a second copy of
     the registry: it would agree with itself forever and catch nothing. What this asks instead
     is a question the source can answer wrongly.

     THE RULE. A canvas mark cannot read a custom property: Leaflet hands a colour STRING to
     the 2D context, so any file that resolves paperColor(), inkColor() or scheduledColor() and
     puts the result into a Leaflet style keeps that colour until something sets it again. Such
     a file owes a registerCanvasFamily call. A divIcon owes nothing, because its markup says
     `var(--paper)` in an inline style and the cascade repaints it for free.

     THE MUTATION THE OPERATOR NAMED is the rail casings left out of the restyle, and it dies
     here as well as on the page: delete railroad.js's registration and this fails by name. */
  const RESOLVERS = /\b(paperColor|inkColor|scheduledColor)\(\)/;
  const files = readdirSync(SYSTEMS).filter((n) => n.endsWith(".js"));
  const owes = [];
  const registers = [];
  for (const name of files) {
    // shared.js DEFINES the resolvers and the registry; it draws no family of its own.
    if (name === "shared.js") continue;
    const code = stripComments(src(name));
    /* COMPILED, NOT JUST STRIPPED. If the stripper mangled a regex literal or ran two
       statements together, the stripped file would no longer parse, and a scrape over a
       mangled file is a scrape that can miss a call site. Script() parses without running
       anything, which is all these files would tolerate outside a browser anyway. */
    new Script(code, { filename: `stripped:${name}` });
    if (RESOLVERS.test(code)) owes.push(name);
    if (code.includes("registerCanvasFamily(")) registers.push(name);
  }
  assert.deepEqual(
    owes.slice().sort(),
    registers.slice().sort(),
    `these files resolve a theme token for a canvas mark but register no family: ${owes
      .filter((f) => !registers.includes(f))
      .join(", ") || "(none)"}`,
  );
  // AND THE FOUR THAT MUST BE THERE, named, because "the sets are equal" is also true of two
  // empty sets and a stage that deleted every registration would pass it.
  for (const name of ["subway.js", "railroad.js", "path.js", "ferry.js", "airtrain.js"]) {
    assert.ok(registers.includes(name), `${name} must register its canvas family`);
  }
});

test("MR4 theme: the rail casings are restyled by RENDERER, never by sweeping their group", () => {
  /* THE CARE THIS ENTRY NEEDS, held as a test because getting it wrong is invisible on a
     light-theme page. Each rail family's layer group holds the 5px paper CASING and the 2.5px
     branch LINE together, so a blind `group.eachLayer(l => l.setStyle({ color: paper }))`
     would paint every branch line in paper too and erase the agency's published colours,
     which are the entire subject of stage MR3. MR3 put the casings on their own renderer for
     the drawing order, and that split is what identifies them here. */
  const body = src("railroad.js");
  const at = body.indexOf('registerCanvasFamily("rail casings"');
  assert.ok(at >= 0, "railroad.js must register the rail casings");
  const entry = body.slice(at, body.indexOf("\n});", at));
  assert.match(entry, /railroadCasingRenderer/, "the casings are identified by their renderer");
  /* COLOUR ONLY, NEVER OPACITY, and this is the sharp one. The only other setStyle in the app
     is route focus, which owns every ribbon's opacity and guards on it; a swap that passed
     opacity would silently undo a rider's focus the moment they changed theme. */
  assert.doesNotMatch(entry, /opacity/, "a theme swap sets colour and must not touch opacity");
  const subway = src("subway.js");
  const ribbons = subway.slice(subway.indexOf('registerCanvasFamily("subway ribbons"'));
  assert.doesNotMatch(ribbons.slice(0, ribbons.indexOf("\n});")), /opacity/);
});
