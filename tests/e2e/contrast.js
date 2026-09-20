/* MR4: every mark on the map, paint by paint, against the theme's own surfaces.
   ==========================================================================

   ONE MEASUREMENT, TWO CALLERS, because the alternative is two copies that drift. theme.spec.js
   D5d asserts the floor from it and pins.spec.js P4c records the numbers from it, and a claim
   asserted from one arithmetic and recorded from another would be two claims.

   WHAT IT MEASURES, stated here because the measurement is only worth its definition.

   THE SURFACE IS THE THEME'S `--paper`, read live off the page. A basemap tile is an IMAGE and
   can be any colour, which is why a11y.spec.js A1z3 bounds the station LABELS over both
   extremes a tile can be; a mark cannot be bounded that way, because nothing clears 3:1 against
   every possible pixel. What the design promises instead is that every mark carries a casing or
   stroke in the theme's own paper, and that paper is what MR1's dark tile filter takes an OSM
   tile close to: `grayscale(0.7) invert(1) hue-rotate(180deg) brightness(0.7)` turns the
   near-white land fill into near-black. `--surface` is measured beside it as the lighter of the
   two dark greys the theme actually paints, so a mark that only just clears paper is visible as
   only just clearing.

   A MARK IS FOUND BY ITS STRONGEST PAINT. A rail station square is `fill: var(--paper)` with
   `stroke: var(--ink)`: its fill IS the surface by construction and its outline is what a rider
   sees. A subway train is an agency's route colour under a white letter. So every paint of
   every mark is measured and the caller decides what to do with the best of them; the per-paint
   numbers are what make that decision readable rather than a single number that hides which
   half of a mark is carrying it.

   AN ALPHA IS COMPOSITED RATHER THAN IGNORED, and round 1 found that this was only half true.
   The code composited an rgba() COLOUR's alpha, which no mark on this map has, and ignored the
   one alpha that is actually here: the subway's plate carries `opacity="0.95"` as an ELEMENT
   attribute, so its computed fill is an opaque rgb() and the compositing branch never ran. Both
   are handled now: a paint's effective alpha is its colour's alpha times the element's own
   `opacity` and its `fill-opacity` or `stroke-opacity`, and that is composited over the surface
   before the ratio. It changes no verdict today (the plate's paint IS the surface colour, so
   compositing it over the surface returns the surface), which is exactly why it had to be
   measured rather than assumed. */

// Every family on the map, by the class its marks carry. NJ Transit is absent on purpose: MR3
// made the rail tag and the commuter square one grammar for all three rail agencies, so it has
// no mark of its own to measure.
const MARK_FAMILIES = {
  "subway train": ".train-marker",
  bus: ".bus-marker",
  "PATH train": ".path-marker",
  "ferry boat": ".ferry-marker",
  "rail tag": ".rail-tag-marker",
  "rail station square": ".rail-stn-marker:not(.rail-airtrain-stn)",
  "AirTrain station square": ".rail-airtrain-stn",
};

// And the three canvas station families, which have no element at all: their options are what
// Leaflet hands to the 2D context, so the option IS the paint.
const CANVAS_FAMILIES = {
  "subway station dot": "subway",
  "PATH station dot": "path",
  "ferry dock": "ferry",
};

async function measureMarkContrast(page) {
  return page.evaluate(
    ({ markFamilies, canvasFamilies }) => {
      const srgb = (c) => {
        const v = c / 255;
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
      };
      const lum = ([r, g, b]) => 0.2126 * srgb(r) + 0.7152 * srgb(g) + 0.0722 * srgb(b);
      const parse = (css) => {
        const parts = (String(css).match(/rgba?\(([^)]+)\)/) || [, ""])[1]
          .split(/[\s,/]+/)
          .filter(Boolean)
          .map(Number);
        if (parts.length < 3) return null;
        return { rgb: parts.slice(0, 3), alpha: parts.length > 3 ? parts[3] : 1 };
      };
      const ratio = (colour, base, alpha = 1) => {
        const c = parse(colour);
        const b = parse(base);
        if (!c || !b) return null;
        const a = Math.max(0, Math.min(1, c.alpha * alpha));
        const over = c.rgb.map((v, i) => v * a + b.rgb[i] * (1 - a));
        const [hi, lo] =
          lum(over) >= lum(b.rgb) ? [lum(over), lum(b.rgb)] : [lum(b.rgb), lum(over)];
        return (hi + 0.05) / (lo + 0.05);
      };
      // The element's own transparency, which is where this map's only non-opaque paint lives.
      const alphaOf = (style, which) => {
        const own = Number(style.opacity);
        const paint = Number(which === "fill" ? style.fillOpacity : style.strokeOpacity);
        return (Number.isFinite(own) ? own : 1) * (Number.isFinite(paint) ? paint : 1);
      };
      /* A PAINT IS ONLY MEASURED IF IT IS A COLOUR, and this guard is the round's own repair.

         `resolve` hands a value back through the browser's own parser so a hex from a canvas
         option and a computed style are compared in one form rather than two. It does that by
         assigning to a probe's `color`, and CSSOM DROPS an assignment it cannot parse: the
         probe then keeps its INHERITED colour and that colour is measured as if it were the
         mark's. `none` is the value this bites on, and it is everywhere: it is the computed
         `stroke` of every shape that sets no stroke, and it is a legal SVG paint rather than a
         colour.

         MEASURED, IN THE COMMITTED GOLDEN. The subway train icon is two rects and a text with
         no stroke anywhere, so three of its paints computed to `none`, each resolved to the
         document's inherited black, and P4c recorded "subway train" and "rail tag" in the light
         theme as carrying `rgb(0, 0, 0)` at 18.79. No mark on this map paints black. The floor
         in theme.spec.js D5d is a maximum over a mark's paints, so a phantom at 18.79 would
         have carried any mark past it: a test that cannot fail, which is one of the four defect
         shapes this phase keeps producing, in the test written to measure the others.

         CSS.supports IS THE RIGHT QUESTION because it asks the browser what it will accept as a
         colour rather than re-implementing a parser here. `none`, `context-fill`, a url() paint
         server and an empty string all answer false and are dropped; every real colour, in any
         notation, answers true. */
      const isColour = (value) => {
        try {
          return CSS.supports("color", String(value));
        } catch {
          return false;
        }
      };
      const resolve = (value) => {
        const probe = document.createElement("span");
        probe.style.color = value;
        document.body.append(probe);
        const out = getComputedStyle(probe).color;
        probe.remove();
        return out;
      };
      const paper = resolve("var(--paper)");
      const surface = resolve("var(--surface)");

      const rows = [];
      const push = (family, paints) => {
        const measured = paints
          // The guard above, applied BEFORE the probe: a value the browser will not take as a
          // colour is not a paint this measurement can say anything about, and pretending
          // otherwise is how a mark gets credit for a colour it does not carry.
          .filter(({ css }) => isColour(css))
          .map(({ kind, css, alpha = 1 }) => {
            const value = resolve(css);
            return {
              kind,
              css: value,
              onPaper: ratio(value, paper, alpha),
              onSurface: ratio(value, surface, alpha),
            };
          })
          .filter((p) => p.onPaper != null);
        rows.push({ family, paints: measured });
      };

      /* WHICH PAINTS A SHAPE ACTUALLY PAINTS, which is the second half of the same repair and
         was found the same way: by reading the golden after regenerating it. A `<line>` has no
         area, so it paints its stroke and nothing else, but its COMPUTED fill is the property's
         initial value, `rgb(0, 0, 0)`, which IS a real colour, so the guard above admits
         it. The rail tag carries one `<line>` (the divider between its agency and branch
         blocks), and P4c recorded that family in the light theme as carrying `line fill
         rgb(0, 0, 0)` at 18.79: a black no mark paints, on a shape that paints no fill.

         So the paints are enumerated per element KIND rather than uniformly. Everything else
         paints both: a `<rect>` or a `<path>` whose author set no fill really is drawn black,
         which is a defect worth measuring rather than a phantom worth dropping. */
      const PAINTS_FILL = new Set(["path", "circle", "rect", "text", "ellipse", "polygon"]);
      for (const [family, selector] of Object.entries(markFamilies)) {
        for (const el of document.querySelectorAll(selector)) {
          const paints = [];
          for (const shape of el.querySelectorAll("path, circle, rect, text, line, polyline")) {
            const tag = shape.tagName.toLowerCase();
            const style = getComputedStyle(shape);
            if (PAINTS_FILL.has(tag)) {
              paints.push({ kind: `${tag} fill`, css: style.fill, alpha: alphaOf(style, "fill") });
            }
            paints.push({ kind: `${tag} stroke`, css: style.stroke, alpha: alphaOf(style, "stroke") });
          }
          push(family, paints);
        }
      }
      for (const [family, kind] of Object.entries(canvasFamilies)) {
        for (const entry of stationRegistry.filter((e) => e.kind === kind)) {
          const o = entry.marker.options;
          const paints = [{ kind: "fill", css: o.fillColor }];
          if (o.stroke) paints.push({ kind: "stroke", css: o.color });
          push(family, paints);
        }
      }

      /* MR5: EVERY NON-OPAQUE PAINT ON THE PAGE, WHEREVER IT IS DRAWN, which is the guard the
         alpha repair above never had.

         WHY IT NEEDED ONE. R15 fixed the compositing and MR4 recorded it as UNGUARDED, because
         `bestPerFamily` reports a family's STRONGEST paint and the two alphas on this map are both
         a paper backing behind something else: the subway plate's at 0.95 and the rail tag's at
         0.9. A paper backing measured against paper reads about 1.04 either way, so it is never a
         family's best paint and mutation M47 (reverting the compositing) moved no number and
         survived, exactly as recorded.

         WHAT MR5 CHANGES. Section 5 puts those same two marks INSIDE A POPUP, where the surface
         under them is `--surface` rather than `--paper`: in the dark theme those are two different
         greys, so the composite is a different colour and the arithmetic stops being a no-op. So
         the rows below report each non-opaque paint BOTH ways, composited and as if it were
         opaque, on both surfaces, and pins.spec.js records them. M47 now moves four numbers.

         ONE READER, TWO ANSWERS, which is why this is the same evaluate rather than a second
         function: a separate measurement would be a second implementation of parse, ratio,
         resolve and alphaOf, and this phase has already paid for two implementations of one
         reader (the pins' popup reader, of which only one copy learned).

         `where` IS map OR popup, read from the element's own ancestry, so a popup left open by a
         caller is measured as popup content rather than silently counted as a map mark. */
      const alpha = [];
      for (const shape of document.querySelectorAll("svg path, svg circle, svg rect, svg text, svg line, svg polyline")) {
        const tag = shape.tagName.toLowerCase();
        const style = getComputedStyle(shape);
        for (const which of PAINTS_FILL.has(tag) ? ["fill", "stroke"] : ["stroke"]) {
          const effective = alphaOf(style, which);
          const css = which === "fill" ? style.fill : style.stroke;
          if (!(effective < 1) || !isColour(css)) continue;
          const value = resolve(css);
          const owner = shape.closest("svg");
          /* THREE PLACES, NOT TWO, and the first draft of this table is why: it labelled everything
             that was not in a popup "map", and the Key panel's own glyphs came back as map marks
             carrying the LIGHT paper in the dark theme. They are not map marks and that is not a
             defect: the Key draws its tags in H3's literals because the panel keeps one surface in
             both themes, which is a decision MR1 recorded. A row that called them map marks would
             be a number ledger telling a reader something false. */
          const place = shape.closest(".leaflet-popup-content")
            ? "popup"
            : shape.closest("#panel, #legend")
              ? "chrome"
              : "map";
          /* THE MARK'S NAME IS ITS FAMILY WHERE IT HAS ONE, because the subway's plate carries no
             class of its own: its `<svg>` has a viewBox and nothing else (pins.spec.js P1f pins that
             markup byte for byte, so this table asks the question elsewhere rather than adding a
             class to it). On the map the family selectors above answer; a popup's borrowed mark is
             outside them, so it falls back to the svg's own class, which five of the six builders
             set, and to "svg" for the plate. */
          let family = null;
          for (const [name, selector] of Object.entries(markFamilies)) {
            if (shape.closest(selector)) { family = name; break; }
          }
          alpha.push({
            where: place,
            mark: family || (owner && owner.getAttribute("class") ? owner.getAttribute("class").split(" ")[0] : "svg"),
            paint: `${tag} ${which}`,
            colour: value,
            alpha: Number(effective.toFixed(2)),
            compositedOnPaper: ratio(value, paper, effective),
            compositedOnSurface: ratio(value, surface, effective),
            opaqueOnPaper: ratio(value, paper, 1),
            opaqueOnSurface: ratio(value, surface, 1),
          });
        }
      }
      return { paper, surface, rows, alpha };
    },
    { markFamilies: MARK_FAMILIES, canvasFamilies: CANVAS_FAMILIES },
  );
}

/* The same measurement as one row per FAMILY rather than one per mark: the best paint each
   family has, and which paint that is. This is the shape a reader can hold, and the shape a
   golden should record, because "which of a mark's paints is carrying it" is exactly what a
   later stage would change without noticing. */
function bestPerFamily(measured, round = 2) {
  const out = {};
  for (const row of measured.rows) {
    const best = row.paints.reduce((a, b) => (b.onPaper > a.onPaper ? b : a));
    const prior = out[row.family];
    // The WORST of a family's marks, so a family whose one bad mark hides behind five good ones
    // is reported at its bad one: a rider looking for that train does not average.
    if (!prior || best.onPaper < prior.onPaper) {
      out[row.family] = {
        paint: best.kind,
        colour: best.css,
        onPaper: Number(best.onPaper.toFixed(round)),
        onSurface: Number(best.onSurface.toFixed(round)),
      };
    }
  }
  return out;
}

/* The alpha rows as one row per (where, mark, paint), because a map draws 136 rail tags and 27
   subway plates and they carry one arithmetic between them. Numbers are rounded here rather than in
   the page, for the reason bestPerFamily rounds here: the measurement says what it measured and the
   golden says what a reader can compare.

   THREE DECIMALS, NOT TWO, and the difference this table exists to show is why: a --paper backing at
   0.95 composited over --surface moves the ratio by about five thousandths in the light theme, which
   two decimals rounds away entirely. bestPerFamily's numbers are ratios in double digits and two is
   right for them; these are ratios near 1 whose interesting part is the third place. */
function alphaPaints(measured, round = 3) {
  const out = {};
  for (const row of measured.alpha) {
    const key = `${row.where} ${row.mark} ${row.paint}`;
    if (out[key]) continue;
    out[key] = {
      colour: row.colour,
      alpha: row.alpha,
      compositedOnPaper: Number(row.compositedOnPaper.toFixed(round)),
      compositedOnSurface: Number(row.compositedOnSurface.toFixed(round)),
      opaqueOnPaper: Number(row.opaqueOnPaper.toFixed(round)),
      opaqueOnSurface: Number(row.opaqueOnSurface.toFixed(round)),
    };
  }
  return out;
}

module.exports = { measureMarkContrast, bestPerFamily, alphaPaints, MARK_FAMILIES, CANVAS_FAMILIES };
