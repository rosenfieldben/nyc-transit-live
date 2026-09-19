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

   AN ALPHA IS COMPOSITED RATHER THAN IGNORED, which is the mistake the attribution's own parser
   in a11y.spec.js records having made: the subway's plate is paper at 0.95 and is not opaque. */

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
      const ratio = (colour, base) => {
        const c = parse(colour);
        const b = parse(base);
        if (!c || !b) return null;
        const over = c.rgb.map((v, i) => v * c.alpha + b.rgb[i] * (1 - c.alpha));
        const [hi, lo] =
          lum(over) >= lum(b.rgb) ? [lum(over), lum(b.rgb)] : [lum(b.rgb), lum(over)];
        return (hi + 0.05) / (lo + 0.05);
      };
      // Any colour the app chose, handed back through the browser's own parser, so a hex from a
      // canvas option and a computed style are compared in one form rather than two.
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
          .map(({ kind, css }) => {
            const value = resolve(css);
            return { kind, css: value, onPaper: ratio(value, paper), onSurface: ratio(value, surface) };
          })
          .filter((p) => p.onPaper != null);
        rows.push({ family, paints: measured });
      };

      for (const [family, selector] of Object.entries(markFamilies)) {
        for (const el of document.querySelectorAll(selector)) {
          const paints = [];
          for (const shape of el.querySelectorAll("path, circle, rect, text, line")) {
            const style = getComputedStyle(shape);
            paints.push({ kind: `${shape.tagName.toLowerCase()} fill`, css: style.fill });
            paints.push({ kind: `${shape.tagName.toLowerCase()} stroke`, css: style.stroke });
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
      return { paper, surface, rows };
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

module.exports = { measureMarkContrast, bestPerFamily, MARK_FAMILIES, CANVAS_FAMILIES };
