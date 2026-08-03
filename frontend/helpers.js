// Pure helpers shared by map.js. Loaded as a plain <script> before map.js,
// so the top-level declarations land in the shared global scope — no build
// step. The CommonJS guard at the bottom makes the same file loadable by
// `node --test` for unit testing.

// Feed data goes into HTML popups/icons — escape it.
function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Deterministic color per bus route: hash the route id onto the hue wheel.
// NO TEXT IS EVER PRINTED ON A BUS COLOUR: it is a polyline colour and a heading colour,
// never a chip fill, so this wheel owes 3:1 as a non-text indicator rather than 4.5 as a
// background for ink. The heading use goes through readableInk, which the node test
// sweeps across all 360 hues. An earlier draft of this comment changed the fallback to
// #666666 on the grounds that white text on #777 measures 4.48; that reasoning was
// borrowed from the chips and does not apply here, so the value is unchanged.
function routeColor(routeId) {
  if (!routeId) return "#777777";
  let h = 0;
  for (const c of routeId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360}, 75%, 40%)`;
}

// Our own palette, grouped by trunk line (deliberately not the MTA's official
// colors — see README note on MTA branding).
const LINE_COLORS = {
  1: "#c0392b", 2: "#c0392b", 3: "#c0392b",
  4: "#1e8449", 5: "#1e8449", 6: "#1e8449",
  7: "#8e44ad",
  A: "#1f5fbf", C: "#1f5fbf", E: "#1f5fbf",
  B: "#d68910", D: "#d68910", F: "#d68910", M: "#d68910",
  G: "#58a832",
  J: "#7d5a3c", Z: "#7d5a3c",
  L: "#7f8c8d",
  N: "#e6b800", Q: "#e6b800", R: "#e6b800", W: "#e6b800",
  GS: "#566573", FS: "#566573", H: "#566573", S: "#566573",
  SI: "#34495e",
};

function lineColor(routeId) {
  if (!routeId) return "#555555";
  return LINE_COLORS[routeId] ?? LINE_COLORS[routeId[0]] ?? "#555555";
}

// Railroad route ids (LIRR branch codes, MNR line numbers) collide with subway
// ids and with each other, so they get their own palette rather than reusing
// lineColor. Deterministic per id from a fixed palette, with a neutral default
// for a missing id.
const RAILROAD_COLORS = [
  "#7b1fa2", "#00838f", "#c2185b", "#1565c0", "#ef6c00",
  "#4527a0", "#2e7d32", "#ad1457", "#00695c", "#5d4037",
];

// The no-id fallback is the same neutral PATH already uses, and it moved for a measured
// reason: #607d8b carries white text at 4.37 and dark text at 3.98, so NEITHER ink can
// make it readable. That is a fill that has to move rather than an ink that has to be
// chosen, which is the one case readableTextOn cannot rescue and the reason the node
// test asserts the chosen ink's ratio rather than merely that a choice was made.
function railroadColor(routeId) {
  if (!routeId) return "#546e7a";
  let h = 0;
  for (const c of routeId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return RAILROAD_COLORS[h % RAILROAD_COLORS.length];
}

/* ----- A3: one breakpoint, named once ----------------------------------------
   700px is the mobile boundary, and it is declared here as well as in style.css because
   two things need it: the media queries that lay the page out, and the status line's
   compaction, which is composed in JavaScript. A number written twice drifts, so the
   node test asserts style.css actually uses this value. The 1100px docked threshold is a
   different decision and is deliberately untouched by A3.

   Injectable like the motion gate above, for the same reason: a test needs to state the
   viewport rather than resize a real window, and node has no matchMedia at all. */
const MOBILE_MAX_WIDTH_PX = 700;
const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH_PX}px)`;

function narrowViewport(mql = null) {
  const query = mql || (typeof matchMedia === "function" ? matchMedia(MOBILE_QUERY) : null);
  if (!query) return false; // no matchMedia (node): the roomy layout, same as before A3
  return !!query.matches;
}

/* ----- A3: the status line's composition rule --------------------------------
   ONE STATED ORDER: counts, then clock, then problems. Counts are what a rider glances
   at, the clock is ambient, and a problem is the thing they need to read whole. The
   line WRAPS rather than truncating, so nothing is ever cut off at any width; what
   compaction does is drop the clock's SECONDS, which is the only part of the line that
   carries no information a rider acts on.

   NEVER TRUNCATE A PROBLEM STATEMENT. This is the hard rule and the reason composition
   lives in one testable function instead of a template literal at the call site: "Bus:
   upstream 502" clipped to "Bus: upstr" is worse than no status line at all, because it
   looks like the page merely ran out of room rather than that something is broken. If a
   future narrow layout needs to shed something, it sheds the clock and then the counts,
   in that order, and the problems stay whole.

   THE PROBLEM SEPARATOR IS A COLON, NOT AN EM DASH, by ruling, and the reason is the one
   that kept the middot out of the spoken train names in A2: an em dash is visual
   punctuation a screen reader renders as noise or as the words "em dash", while a colon
   is read as the pause it looks like. The line shipped with a U+2014 here before A3;
   recomposing it was the moment to fix that rather than carry it forward.
   The middot between counts and clock stays. It separates two glanceable facts rather
   than introducing a statement, and it is what shipped. */
function statusLineText({ counts, clock, problems = [] } = {}, { compact = false } = {}) {
  const time = compact ? String(clock ?? "").replace(/:\d\d(?=(\s|$))/, "") : clock;
  const stated = (problems ?? []).filter(Boolean);
  const head = [counts, time].filter(Boolean).join(" \u00b7 ");
  if (!stated.length) return counts ? `${counts} \u00b7 updated ${time}` : `updated ${time}`;
  return `${head}: ${stated.join("; ")}`;
}

/* ----- A3: contrast, computed rather than curated ----------------------------
   ONE luminance path for the whole app. Before this there was none: the only
   contrast logic anywhere was DARK_TEXT_LINES above, a hand-written set of four
   subway lines "that need dark text". Hand-curated sets are wrong the moment a
   palette gains an entry, and this one already was. Measured against white text at
   the 4.5 a chip's 11px owes: B/D/F/M #d68910 at 2.82, G #58a832 at 2.97, L #7f8c8d
   at 3.48, and in the railroad palette #ef6c00 at 3.08 and the no-id default #607d8b
   at 4.37. Six subway lines and two railroad colours carrying unreadable text, none
   of them in the set.

   The formulas are WCAG 2.x relative luminance and contrast ratio, verbatim. They
   are here rather than in a stylesheet because the decision is per route colour and
   the route colours are computed. */

// "#rgb", "#rrggbb", "rgb(...)" and the "hsl(h, s%, l%)" that routeColor emits.
// Returns null for anything unparseable rather than guessing, so a caller gets a
// visible failure instead of a silently wrong colour.
function parseColor(value) {
  const text = String(value ?? "").trim();
  const short = text.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i);
  if (short) return short.slice(1, 4).map((c) => parseInt(c + c, 16));
  const long = text.match(/^#([0-9a-f]{6})$/i);
  if (long) return [0, 2, 4].map((i) => parseInt(long[1].slice(i, i + 2), 16));
  const rgb = text.match(/^rgba?\(([^)]+)\)$/i);
  if (rgb) {
    const parts = rgb[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    if (parts.length >= 3 && parts.slice(0, 3).every((n) => Number.isFinite(n))) return parts.slice(0, 3);
    return null;
  }
  const hsl = text.match(/^hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)$/i);
  if (hsl) {
    const [h, s, l] = [Number(hsl[1]), Number(hsl[2]) / 100, Number(hsl[3]) / 100];
    const a = s * Math.min(l, 1 - l);
    const f = (n) => {
      const k = (n + h / 30) % 12;
      return l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    };
    return [f(0) * 255, f(8) * 255, f(4) * 255];
  }
  return null;
}

function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// WCAG contrast ratio, 1 to 21. Order-independent by construction.
function contrastRatio(a, b) {
  const [x, y] = [parseColor(a), parseColor(b)];
  if (!x || !y) return null;
  const [hi, lo] = [relativeLuminance(x), relativeLuminance(y)].sort((m, n) => n - m);
  return (hi + 0.05) / (lo + 0.05);
}

// The ink to print ON a filled shape: whichever of the two carries further. This is
// what replaces DARK_TEXT_LINES, and it cannot fall behind a palette because it reads
// the palette.
const INK_LIGHT = "#ffffff";
const INK_DARK = "#1a1a1a";
function readableTextOn(background) {
  const light = contrastRatio(INK_LIGHT, background);
  const dark = contrastRatio(INK_DARK, background);
  if (light == null || dark == null) return INK_DARK; // unparseable: the safer default
  return light >= dark ? INK_LIGHT : INK_DARK;
}

// A route colour used AS TEXT, on a light surface, darkened only as far as it must be.
// The brand colour stays on the SHAPES that carry identity (bullets, chips, route
// lines), where 3:1 applies and the label carries the meaning; a heading rendered in
// the same hue is text and owes 4.5, and #e6b800 on white is 1.87. Scaling the channels
// toward black preserves the hue, so an N heading still reads yellow, just readably so.
// Returns the input unchanged when it already clears the target.
function readableInk(color, background = "#ffffff", target = 4.5) {
  const rgb = parseColor(color);
  if (!rgb) return color;
  if ((contrastRatio(color, background) ?? 0) >= target) return color;
  for (let scale = 0.95; scale >= 0; scale -= 0.05) {
    const scaled = rgb.map((c) => Math.round(c * scale));
    const hex = `#${scaled.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
    if ((contrastRatio(hex, background) ?? 0) >= target) return hex;
  }
  return "#000000"; // black fails nothing on a light surface
}

// A railroad train placed at its next station (vs one drawn at a live GPS
// position). stop_id is the authoritative discriminator: the placement decode
// always emits a resolved stop_id (and stop_name), while the GPS decode
// contractually emits null for both. Keying off stop_id (rather than the
// time/direction anchors) keeps a no-times placement, e.g. an MNR train whose
// stops carry no times and no direction_id, correctly classified, so the marker
// fill, the GPS/scheduled label, and the next-stop popup line all stay consistent.
function isPlacedRailroad(t) {
  return t.stop_id != null;
}

/* A4 ROUND 1: WHERE A POPUP SHOULD MOVE TO GET OUT FROM UNDER THE PAGE'S CHROME.
   Pure geometry, here rather than in systems/shared.js so it can be reasoned about and
   tested without a browser, which is the same split the rest of this file exists for. The
   caller supplies three boxes and gets back a translation or null; it knows nothing about
   Leaflet, the legend or the banner.
   The first version of this lived inside the map file, only ever moved DOWN, and knew about
   one obstacle. All three were wrong and all three were caught by measurement rather than by
   reading: see the comment at panPopupClearOfChrome for what each cost. */
const POPUP_CLEAR_GAP = 8; // the gap the layout uses between two surfaces that must not touch

function boxesOverlap(a, b) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

function shiftBox(box, dx, dy) {
  return {
    left: box.left + dx,
    right: box.right + dx,
    top: box.top + dy,
    bottom: box.bottom + dy,
  };
}

/* THE GEOMETRY, KEPT SEPARATE FROM THE MAP so it can be reasoned about and tested without
   a browser: given where the popup is, what the obstacles are and how much room the map
   has, which way should the world move and by how much? Returns null when the popup is
   already clear or when no direction can clear it, and the difference between those two
   is deliberately not encoded: both mean "do nothing", and a caller that treated them
   differently would be inventing a distinction the rider cannot see. */
function popupClearingShift(popup, obstacles, viewport) {
  const blocking = obstacles.filter((box) => boxesOverlap(popup, box));
  if (!blocking.length) return null;
  const fits = (box) =>
    box.left >= viewport.left && box.right <= viewport.right && box.top >= viewport.top && box.bottom <= viewport.bottom;

  /* THE SEARCH, AND WHY IT IS A SEARCH RATHER THAN A FORMULA.
     The first version costed each direction as "the most any blocking obstacle demands" and
     discarded the result if it landed on something else. Round 2 showed that fights itself
     two ways. Taking the max over every blocker turns two small obstacles into one enormous
     move that leaves the viewport; discarding a landing collision throws away the answer
     when the honest response is "then also step sideways". Together they could cancel the
     desktop leftward move outright and leave the popup fully under the legend.
     So each blocker is costed SEPARATELY, in each direction, and any collision the result
     lands in is resolved on the OTHER axis. The candidate set is small (obstacles times four,
     twice) and every candidate is checked against every obstacle before it can win, so the
     search cannot return a move that does not actually clear. */
  const axisMoves = (box, blockers) =>
    blockers.flatMap((b) => [
      { dx: b.left - box.right - POPUP_CLEAR_GAP, dy: 0 },
      { dx: b.right - box.left + POPUP_CLEAR_GAP, dy: 0 },
      { dx: 0, dy: b.top - box.bottom - POPUP_CLEAR_GAP },
      { dx: 0, dy: b.bottom - box.top + POPUP_CLEAR_GAP },
    ]);

  const candidates = [];
  for (const first of axisMoves(popup, blocking)) {
    const moved = shiftBox(popup, first.dx, first.dy);
    candidates.push({ move: first, moved });
    const stillBlocking = obstacles.filter((box) => boxesOverlap(moved, box));
    if (!stillBlocking.length) continue;
    // The second axis only: a move along x is followed by a y move and vice versa, so the
    // pair is always an L and never doubles back along the axis just cleared.
    for (const second of axisMoves(moved, stillBlocking)) {
      if ((first.dx !== 0) === (second.dx !== 0)) continue;
      const move = { dx: first.dx + second.dx, dy: first.dy + second.dy };
      candidates.push({ move, moved: shiftBox(popup, move.dx, move.dy) });
    }
  }

  const accepted = candidates
    // Re-checked against EVERY obstacle, not just the ones that were blocking: moving out
    // from under the legend must not move under the banner.
    .filter(({ moved }) => fits(moved) && !obstacles.some((box) => boxesOverlap(moved, box)))
    .map(({ move }) => move);
  if (!accepted.length) return null;
  // The smallest accepted move, so the map shifts as little as the rider will tolerate.
  return accepted.reduce((best, move) =>
    Math.abs(move.dx) + Math.abs(move.dy) < Math.abs(best.dx) + Math.abs(best.dy) ? move : best,
  );
}

// ---- Staleness thresholds, and the one test seam in this file (C6) ----
//
// WHY A SEAM EXISTS HERE AT ALL. The contract tier runs the REAL page against a
// real backend, so it cannot monkeypatch these the way the node tests do. Waiting
// out the production thresholds would put a single scenario at 90 or 300 seconds,
// which no CI budget survives, so the page has to be able to start dimming sooner
// when a test asks it to.
//
// THE SAFETY ARGUMENT, in full, because a query-string input into a live page
// deserves one:
//   1. It is COSMETIC AND CLIENT-LOCAL. It changes only when THIS visitor's own
//      view starts dimming markers and showing "may be out of date". It alters no
//      request, no data, and nothing any other visitor sees. A visitor who wanted
//      their own view to dim sooner could already do it from devtools in one line,
//      so this grants no capability that did not already exist.
//   1a. IT CAN ONLY EVER DIM SOONER, and that is enforced rather than asserted.
//      The accepted range is [1, the production value]. An earlier version took any
//      positive number, which made the sentence above FALSE in the direction that
//      matters: "?contract=1&feedStaleAfterS=99999999" RAISED the thresholds, so a
//      crafted link could suppress every staleness surface on the page and leave a
//      visitor reading hours-old positions as if they were live. Suppressing a
//      disclosure is a different act from accelerating one, and only one of them is
//      cosmetic. The floor of 1 closes the same hole from below: 0 is rejected
//      because everything would read permanently stale, and 1e-9 does that too.
//   2. THE FLAG IS NOT AN ACCESS CONTROL and is not pretending to be one; a query
//      parameter cannot be. It exists so the parse cannot fire by ACCIDENT: a
//      stray or copied "?feedStaleAfterS=5" in a shared link does nothing without
//      the companion flag, so the production page's behavior is unconditional in
//      practice rather than one typo away from changing.
//   3. THE PARSE IS DELIBERATELY NARROW, and this is the part worth reviewing: it
//      reads exactly two named parameters, accepts only finite positive numbers,
//      and returns a two-key object. It is not a general "read config from the
//      query string" channel, and it must not be allowed to become one, because
//      that is the change that would turn a cosmetic seam into a real surface.
const CONTRACT_FLAG_PARAM = "contract";

// Pure and node-testable: the caller passes the query string, so the inertness
// test can assert directly that anything without the flag yields no overrides.
const PRODUCTION_FEED_STALE_AFTER_S = 90;
const PRODUCTION_ALERTS_STALE_AFTER_S = 300;

function thresholdOverrides(search) {
  const params = new URLSearchParams(search ?? "");
  if (params.get(CONTRACT_FLAG_PARAM) !== "1") return {};
  const out = {};
  for (const [param, key, ceiling] of [
    ["feedStaleAfterS", "feed", PRODUCTION_FEED_STALE_AFTER_S],
    ["alertsStaleAfterS", "alerts", PRODUCTION_ALERTS_STALE_AFTER_S],
  ]) {
    const value = Number(params.get(param));
    if (Number.isFinite(value) && value >= 1 && value <= ceiling) out[key] = value;
  }
  return out;
}

// `location` is absent when this file is require()d by the node tests, so the
// browser read is guarded rather than assumed.
const THRESHOLD_OVERRIDES = thresholdOverrides(
  typeof location === "undefined" ? "" : location.search,
);

// Staleness threshold, mirroring the backend FEED_STALE_AFTER_S.
const FEED_STALE_AFTER_S = THRESHOLD_OVERRIDES.feed ?? PRODUCTION_FEED_STALE_AFTER_S;

// Whole-fetch deadline for every live browser fetch (R2). The browser fetch has
// no built-in whole-request timeout, so a wedged or trickling upstream would
// otherwise leave a request pending forever; each fetch passes
// AbortSignal.timeout(FETCH_DEADLINE_MS) so a stuck request is cut off and becomes
// an ordinary failed poll (keep-last-known + the R1 staleness surfaces), never a
// permanent hang. 15s matches the POLL_INTERVAL_MS cadence: a wedged source is
// aborted at about the time the next tick fires, so it retries on a later tick
// instead of holding a slot indefinitely. AbortSignal.timeout is a modern-baseline
// API (Chromium-tested here; supported across current evergreen browsers). This
// lives in helpers.js, loaded before every systems/*.js and map.js, so the constant
// is in scope for the static loaders and the shared.js fetches at call time (a const
// in map.js would not be a binding those earlier files can see).
const FETCH_DEADLINE_MS = 15000;

// Longitude is compressed by latitude; scale lon deltas so planar distances are
// roughly isotropic across NYC. We only need internally consistent arc-length,
// not true meters, so a single fixed factor at the city's latitude is plenty.
const _COS_LAT = Math.cos((40.7 * Math.PI) / 180);
// A station must project within this distance of a route polyline to be used.
const ROUTE_ACCEPT_DIST = 0.0025;
// Reject an implausibly long slice (misprojection onto a far lobe of a line that
// doubles back, e.g. the Pelham loop): fall back to the straight line instead.
const ROUTE_MAX_SLICE = 0.05;

// Railroad inter-station gaps dwarf subway ones: the LIRR's longest real gap,
// Amagansett to Montauk, is about 0.15 in the isotropic basis (roughly 3x
// ROUTE_MAX_SLICE), and several MNR gaps (Poughkeepsie to New Hamburg) exceed
// 0.1. With the subway cap those segments fail the length gate and fall back to
// the straight chord, defeating the point. This looser cap admits them while
// staying well under any doubling-back lobe: railroad lines are radial with
// branches, not looped like the Pelham 6, so a far misprojection is still
// rejected.
const RAILROAD_ROUTE_MAX_SLICE = 0.3;
// Start equal to the subway projection tolerance. Loosen only if placed-train
// platform coordinates prove to sit too far off the modeled track, which would
// show up as straight-chord fallback on segments that should glide.
const RAILROAD_ROUTE_ACCEPT_DIST = 0.0025;

// PATH's longest real inter-station gap, Journal Square to Harrison, is about
// 0.071 in the isotropic basis: too long for the subway cap (0.05) but far
// short of the railroad's branch-scale gaps (0.3 admits Montauk-length runs
// PATH never has). 0.15 admits every real PATH segment with 2x headroom while
// still rejecting a far misprojection; PATH lines are simple end-to-end runs
// with no loops, so the nearest lobe is always the right one.
const PATH_ROUTE_MAX_SLICE = 0.15;
// Same starting tolerance as the subway/railroad projection; loosen only if
// PATH station coordinates prove to sit off the modeled track, which would
// show up as straight-chord fallback on segments that should glide.
const PATH_ROUTE_ACCEPT_DIST = 0.0025;

// PATH's slice picker. WHY not computeRouteSlice directly: PATH keeps BOTH
// direction polylines for most routes (the reverse shape is a parallel track
// a few meters offset, so the added-geometry dedup keeps it), and
// computeRouteSlice projects each endpoint onto its own nearest polyline
// independently. With twin polylines that near each other, the two endpoints
// can each win on a different twin by a micro-distance coin flip (observed
// live: 0.00057 vs 0.00058), which fails the same-polyline requirement and
// drops the glide to the straight chord for no reason. This variant scores
// each polyline with BOTH endpoints together and slices along the best one,
// so twins can never split a segment; the acceptDist and maxSlice gates are
// unchanged, and picking the reverse-direction twin is harmless because the
// arc is walked in the sign of (s1 - s0). The other systems keep
// computeRouteSlice: their reverse shapes mostly collapse in the dedup, so
// the split cannot occur there and their behavior must not change.
function computePathRouteSlice(
  train,
  geom,
  { maxSlice = PATH_ROUTE_MAX_SLICE, acceptDist = PATH_ROUTE_ACCEPT_DIST } = {},
) {
  if (train.prev_lat == null || !geom) return null;
  let best = null;
  for (const poly of geom) {
    const p0 = projectOntoRoute([poly], train.prev_lat, train.prev_lon, acceptDist);
    const p1 = projectOntoRoute([poly], train.latitude, train.longitude, acceptDist);
    if (!p0 || !p1) continue;
    if (Math.abs(p1.s - p0.s) > maxSlice) continue;
    const score = Math.max(p0.dist, p1.dist);
    if (best === null || score < best.score) {
      best = { score, points: poly.points, cum: poly.cum, s0: p0.s, s1: p1.s };
    }
  }
  return best && { points: best.points, cum: best.cum, s0: best.s0, s1: best.s1 };
}

// minClockOffset = the minimum observed (clientNow - SERVED_AT), approximating
// browser-vs-server skew plus minimal latency. It calibrates off served_at (the
// instant the response left the server), NOT fetched_at (the backend's last poll):
// a response served from a poll N seconds ago would inflate a fetched_at-based
// offset by N, which then (a) cancelled the poll-age staleness term, so a stale
// backend looked fresh, and (b) shifted every arrivals countdown by N. served_at
// is skew + latency only, so this offset is clean. Used to skew-correct the
// arrivals countdown (map.js, which compares absolute MTA timestamps to the
// browser clock) and the client-elapsed term of staleness() below.
let minClockOffset = null;

// `now` is injected only for testability (noteClockOffset otherwise reads the wall
// clock, unlike staleness which always takes an explicit now).
function noteClockOffset(servedAt, now = Date.now() / 1000) {
  if (servedAt == null) return;
  const offset = now - servedAt;
  if (minClockOffset == null || offset < minClockOffset) minClockOffset = offset;
}

// ---- Per-system freshness (C2) ----

// Opacity for a marker whose system's data has gone stale. Dim enough to read as
// "not current" at a glance next to a live marker, light enough that the marker is
// still legible and clickable: a rider looking at a partial outage should be able to
// tell that these trains are the old ones, and still open one to see how old.
const STALE_MARKER_OPACITY = 0.45;

// ONE INGESTION PATH FOR BOTH PAYLOAD SHAPES, which is the whole point of this
// function. The aggregate feeds carry a per-system block (subways: 8 feed groups;
// railroads: LIRR + MNR; alerts: 5 systems); the single-feed ones (buses, PATH,
// ferry) carry only the envelope's own fetched_at. Rather than have every consumer
// below branch on which kind of payload it is holding, a single-feed payload gets a
// SYNTHESIZED one-system block named after its source, so the status line, the
// dimming, the popups and the glide freeze all read one shape and a single-feed
// source can never quietly skip a rule the aggregates follow.
//
// A single-feed source therefore reads exactly as it did pre-C2 in the status line
// ("buses: as of 3m ago", never "buses: buses as of 3m ago"), because naming every
// system of a source is just naming the source: see staleness().
//
// DEFENSIVE READS, because this parses a payload: a block entry with no numeric
// fetched_at reports a null age (unknown, not fresh and not stale) and is surfaced
// through `ok` instead; a missing `ok` reads as healthy, so a malformed field cannot
// dim the whole map; an EMPTY systems object falls back to the synthesized single
// system rather than yielding a source with no freshness at all.
function ingestSystems(body, sourceKey) {
  const raw = body == null ? null : body.systems;
  const names = raw != null && typeof raw === "object" ? Object.keys(raw) : [];
  if (!names.length) {
    const fetchedAt = body == null ? null : body.fetched_at;
    return {
      [sourceKey]: {
        fetchedAt: typeof fetchedAt === "number" ? fetchedAt : null,
        ok: true,
        retainedSince: null,
        routes: null,
      },
    };
  }
  const systems = {};
  for (const name of names) {
    const block = raw[name] ?? {};
    systems[name] = {
      fetchedAt: typeof block.fetched_at === "number" ? block.fetched_at : null,
      ok: block.ok !== false,
      retainedSince: typeof block.retained_since === "number" ? block.retained_since : null,
      // Null means this envelope does no route coverage at all (the railroad and
      // alerts blocks, whose entities name their own system); an array, possibly
      // empty, means it does.
      routes: Array.isArray(block.routes) ? block.routes : null,
    };
  }
  return systems;
}

// A source's systems, falling back to the synthesized single system when a caller
// hands over a descriptor that has not ingested a payload yet (boot) or a plain row
// in a unit test. Keeps every consumer free of null checks.
//
// The fallback names its system after the source's LABEL, which is not the key
// refreshSource ingests under; that would be a trap if the name mattered here, so
// the freshness index deliberately skips a source with no payload yet (there is
// nothing to say about it) and only staleness() uses this path, where the single
// system is never named separately.
function sourceSystems(source) {
  return source.systems ?? ingestSystems({ fetched_at: source.fetchedAt }, source.label ?? "feed");
}

// The poll-age term of staleness(), lifted out so the per-system judgment and the
// whole-source one cannot drift apart. `now` is the RAW client clock (the skew
// correction happens here, via minClockOffset), matching staleness() below.
function pollAge(fetchedAt, servedAt, now) {
  if (fetchedAt == null) return null;
  if (servedAt == null) return now - fetchedAt - (minClockOffset ?? 0);
  // Server cache age is skew-free by construction; client elapsed is clamped at 0
  // (data cannot be fresher than when it was served). See staleness().
  return servedAt - fetchedAt + Math.max(now - servedAt - (minClockOffset ?? 0), 0);
}

// Age of EACH of a source's systems, keyed by system name; null for a system that
// has never decoded (no fetched_at to age against).
//
// The upstream-lag term stays a SOURCE-level floor rather than a per-system one:
// feed_timestamp is the oldest content time across the feeds that decoded, so it
// cannot be attributed to one system, and if the content behind this response is
// old then everything drawn from it is old. On a fully healthy source every
// system's fetched_at equals the envelope's, so the worst of these ages is exactly
// the age R1 computed, which is what keeps the healthy case rendering unchanged.
function systemAges(source, now = Date.now() / 1000) {
  const lag =
    source.feedTimestamp == null || source.fetchedAt == null
      ? 0
      : source.fetchedAt - source.feedTimestamp;
  const ages = {};
  for (const [name, system] of Object.entries(sourceSystems(source))) {
    const poll = pollAge(system.fetchedAt, source.servedAt, now);
    ages[name] = poll == null ? null : Math.max(lag, poll, 0);
  }
  return ages;
}

// Is this age stale? One predicate so the marker dimming, the popup age line, the
// glide freeze and the status line can never disagree about the boundary.
function staleAge(age) {
  return age != null && age >= FEED_STALE_AFTER_S;
}

// The marker opacity a system's age earns: dimmed once stale, otherwise fully
// opaque. Pure so the dimming rule is node-testable; the Leaflet call sits in the
// system files.
//
// `base` is a marker's own resting opacity, which COMPOUNDS with staleness rather
// than being replaced by it. The ferry layer has one (a STOPPED_AT boat rides at
// FERRY_DOCKED_OPACITY to read as parked), and a docked boat on a stale feed is
// legitimately both. This has to be one number per marker: the docked dimming used
// to come from a css class, and an inline opacity written for staleness would have
// silently overridden it, un-dimming every docked boat the moment C2 started
// setting opacities.
function markerOpacity(age, base = 1) {
  return base * (staleAge(age) ? STALE_MARKER_OPACITY : 1);
}

// THE INSTANT EACH SYSTEM'S GLIDE MUST STOP, on the skew-corrected clock (the same
// axis trainLatLng runs on), or null while a system may still be interpolated.
// Three ways a system reaches that instant:
//
//   1. Its data is being RETAINED. retained_since says the backend is serving a
//      generation it could not refresh, so the anchors behind any interpolation are
//      known dead from that moment. This fires as soon as retention starts, before
//      the age threshold, which is the point: gliding is a PREDICTION from a fresh
//      observation, and predicting from data we already know is not being refreshed
//      is dead reckoning no matter how young it is. (Opacity is deliberately NOT
//      moved here: dimming states how OLD the data is, and the app's definition of
//      old is FEED_STALE_AFTER_S for every source alike.)
//   2. Its own poll age reaches the threshold, at fetchedAt + FEED_STALE_AFTER_S.
//   3. The source's upstream content was ALREADY past the threshold when it was
//      polled (the lag term). Nothing may advance past the observation itself, so
//      the deadline is fetchedAt.
//
// REVIEW FIX. This used to be glideClock(now, age) subtracting (age - threshold),
// which only froze while the POLL term dominated: with the upstream-lag term
// dominating, age is a constant across a poll interval, so `now - constant` advanced
// at full speed and every marker on the source kept dead-reckoning while dimmed.
// Expressing the freeze as an absolute instant cannot drift that way, and it needs no
// clock, so it is a pure function of the payload.
function systemStaleAts(source) {
  const lag =
    source.feedTimestamp == null || source.fetchedAt == null
      ? 0
      : source.fetchedAt - source.feedTimestamp;
  const deadlines = {};
  for (const [name, system] of Object.entries(sourceSystems(source))) {
    if (system.fetchedAt == null) {
      deadlines[name] = null; // never decoded: no anchor, so nothing to freeze
      continue;
    }
    const aged = system.fetchedAt + (lag >= FEED_STALE_AFTER_S ? 0 : FEED_STALE_AFTER_S);
    deadlines[name] =
      system.retainedSince == null ? aged : Math.min(aged, system.retainedSince);
  }
  return deadlines;
}

// THE GLIDE FREEZE (C2). The clock an interpolated marker may use: the live clock
// while its system may still be predicted from, pinned at that system's deadline
// (systemStaleAts) once it may not.
//
// WHY freezing rather than letting the glide run: a train would keep sliding
// confidently along its route for as long as the outage lasted, which is a worse lie
// than the frozen position (the frozen one is at least somewhere the train really
// was). A system with no deadline gets `now` back untouched, so normal gliding is
// bit-for-bit unchanged.
function glideClock(now, staleAt) {
  return staleAt == null ? now : Math.min(now, staleAt);
}

// Two independent staleness signals, flag if EITHER crosses the threshold:
//   1. upstream lag = fetched_at - feed_timestamp — both server-recorded, so
//      this is clock-skew free; detects the MTA feed itself going stale.
//   2. poll age = how long ago the data behind this response was actually polled
//      upstream, detecting OUR backend having stopped polling while it keeps
//      serving frozen last-good data (upstream lag alone would stay constant and
//      silent). Two skew-clean parts:
//        - server cache age (served_at - fetched_at): both server clocks, so it is
//          skew-free BY CONSTRUCTION and honest on the very first observation,
//          before any client calibration settles. This is the term the old model
//          was blind to.
//        - client elapsed since the response arrived (now - served_at, skew-
//          corrected by the now-clean minClockOffset), clamped to >= 0 (the data
//          cannot be fresher than when it was served).
//      When served_at is absent (a response predating the served_at contract),
//      fall back to the old single term so a stuck backend is still caught.
//
// C2 MADE THIS PER SYSTEM. The two terms above are unchanged; what changed is what
// they are computed against. A partial outage is a SUCCESSFUL poll, so the
// envelope's own fetched_at keeps advancing and this phrase could never fire for
// the system that was actually down. It now ages every system separately
// (systemAges) and reports the worst, which is why a down MNR surfaces while a
// healthy LIRR stays quiet.
//
// THE COMMON CASE MUST NOT GET NOISIER, so the wording is graded:
//   - every system fresh: null, exactly as before.
//   - the WHOLE source stale (one synthesized system, or every system of an
//     aggregate): "railroad: as of 6m ago", the pre-C2 wording untouched.
//   - a strict SUBSET stale: the degraded systems are named, worst age reported,
//     e.g. "railroad: MNR as of 6m ago" or "trains: ACE group as of 4m ago"
//     (source.systemNoun supplies the "group", which reads wrong for a system).
//   - a system that has NEVER decoded cannot be aged, so it is reported by name
//     with no age rather than silently dropped.
// A system that merely failed its last poll is NOT named until its age crosses the
// threshold: single failed polls are routine, and naming them would make the status
// line chatter during normal operation.
// `now` is injected for testability (defaults to the wall clock).
function staleness(source, now = Date.now() / 1000) {
  const systems = sourceSystems(source);
  const ages = systemAges(source, now);
  const names = Object.keys(ages).sort();
  const stale = names.filter((name) => staleAge(ages[name]));
  // Never decoded (null age) AND reported down: no age to print, but real.
  const blind = names.filter((name) => ages[name] == null && !systems[name].ok);
  if (!stale.length && !blind.length) return null;
  const noun = source.systemNoun ? ` ${source.systemNoun}` : "";
  // Naming every system of a source is just naming the source, so fall back to the
  // pre-C2 wording; that is also what keeps a single-feed source reading unchanged.
  const whole = stale.length + blind.length === names.length;
  const subject = (group) =>
    whole && !(stale.length && blind.length)
      ? ""
      : `${group.map((n) => `${n}${noun}`).join(", ")} `;
  // TWO CLAUSES, NEVER ONE. REVIEW FIX: the stale and blind sets used to be merged
  // into a single subject that then took its age from the stale set alone, so a
  // system which had never reported anything was announced with another system's
  // age ("ACE group, SIR group as of 4m ago" when SIR had no data at all). They
  // collide exactly during a broad incident, which is when the line gets read.
  const clauses = [];
  if (stale.length) {
    const worst = Math.max(...stale.map((name) => ages[name]));
    clauses.push(`${subject(stale)}as of ${humanizeAge(worst)} ago`);
  }
  if (blind.length) clauses.push(`${subject(blind)}not reporting`);
  return `${source.label}: ${clauses.join("; ")}`;
}

// A compact age string: seconds under two minutes, whole minutes above. Shared by
// the status-line staleness and the popup age line so the two read the same.
function humanizeAge(age) {
  return age < 120 ? `${Math.round(age)}s` : `${Math.round(age / 60)}m`;
}

// The "as of Xm ago" age line for a station/dock popup whose arrivals data has
// gone stale: when a background refresh fails the last-known rows keep ticking
// (see openStationArrivals), so past FEED_STALE_AFTER_S the popup must say how old
// they are rather than imply liveness. Empty while fresh, so a live popup shows
// nothing. `now` is the skew-corrected clock the caller already computes for its
// countdowns; fetchedAt is the arrivals body's poll time. Pure and node-testable.
function feedAgeLine(fetchedAt, now) {
  if (fetchedAt == null) return "";
  return stalePopupLine(now - fetchedAt);
}

// The same line from an AGE rather than a timestamp, for the C2 train/boat/bus
// popups: their staleness comes from a system block's age (already carrying the
// server cache-age and skew terms), not from subtracting a fetched_at on the spot.
// One renderer for both surfaces so they cannot word it or threshold it differently.
function stalePopupLine(age) {
  if (!staleAge(age)) return "";
  return `<div class="popup-stale">as of ${humanizeAge(age)} ago</div>`;
}

// Decide what a successful-but-EMPTY poll should do. Keeping the last-known
// markers protects against a TRANSIENT empty feed (a blip that would otherwise
// flicker every marker off and back on), but it must be bounded or a real lull
// (an overnight railroad gap) leaves ghost markers frozen forever. We bound it
// by TIME, not poll count: the poll cadence can change, so "N empty polls" is
// meaningless, whereas elapsed seconds is stable. `emptyRunStart` is the
// fetched_at of the FIRST empty poll in the current empty run (null when the
// previous poll was non-empty); `fetchedAt` is this poll's. Both are the
// server-recorded fetched_at, not the wall clock, so the decision is skew-free
// and consistent with staleness() above. Within FEED_STALE_AFTER_S of the run's
// start, keep the markers and warn "showing last known"; at or past that
// threshold, apply the empty dataset (the callers' unseen-marker sweeps clear
// the layer) and drop the now-false "showing last known" clause. Returns the
// decision plus the run start to store back (unchanged reset happens on the
// caller's non-empty path). A null fetched_at cannot be timed, so it holds
// last-known without starting or advancing a run.
function emptyFeedDecision(emptyRunStart, fetchedAt) {
  if (fetchedAt == null) {
    return { applyEmpty: false, error: "feed empty, showing last known", emptyRunStart };
  }
  const start = emptyRunStart ?? fetchedAt; // first empty poll of this run
  if (fetchedAt - start >= FEED_STALE_AFTER_S) {
    return { applyEmpty: true, error: "feed empty", emptyRunStart: start };
  }
  return { applyEmpty: false, error: "feed empty, showing last known", emptyRunStart: start };
}

// Per-source refresh gate (R2). refreshAll fires a refresh only for sources that
// are NOT already in flight, so a single slow source (bounded by its own
// AbortSignal.timeout) can never starve the others the way the old whole-cycle
// `refreshing` lock did. A source clears its own inFlight flag in refreshSource's
// finally, so this stays a pure function of the descriptor: given the row, is it
// eligible to be refreshed this tick. Pure and node-testable; the fetch/apply it
// gates stays in map.js (browser fetch + DOM).
function shouldRefresh(source) {
  return !source.inFlight;
}

function _segLen(aLat, aLon, bLat, bLon) {
  return Math.hypot((bLon - aLon) * _COS_LAT, bLat - aLat);
}

// Cumulative arc-length along a polyline: cum[0] = 0, cum[i] = cum[i-1] +
// segLen(points[i-1], points[i]). cum.length === points.length.
function polylineCumLengths(points) {
  const cum = [0];
  for (let i = 1; i < points.length; i++) {
    cum.push(cum[i - 1] + _segLen(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]));
  }
  return cum;
}

// [lat, lon] at arc-length s along the polyline, clamped to [0, total]. Binary
// search the segment containing s, then lerp the real coords within it.
function pointAtArcLength(points, cum, s) {
  const total = cum[cum.length - 1];
  if (!(total > 0) || s <= 0) return points[0].slice();
  if (s >= total) return points[points.length - 1].slice();
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid] <= s) lo = mid;
    else hi = mid;
  }
  const seg = cum[hi] - cum[lo];
  const u = seg > 0 ? (s - cum[lo]) / seg : 0;
  const [aLat, aLon] = points[lo];
  const [bLat, bLon] = points[hi];
  return [aLat + (bLat - aLat) * u, aLon + (bLon - aLon) * u];
}

// Closest point on one polyline to P: { s, dist } in the same basis as cum, or
// null for a degenerate (<2-point) polyline.
function _projectOntoPolyline(points, cum, pLat, pLon) {
  if (points.length < 2) return null;
  let best = null;
  const px = pLon * _COS_LAT, py = pLat;
  for (let i = 1; i < points.length; i++) {
    const ax = points[i - 1][1] * _COS_LAT, ay = points[i - 1][0];
    const bx = points[i][1] * _COS_LAT, by = points[i][0];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    const u = len2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
    const dist = Math.hypot(px - (ax + dx * u), py - (ay + dy * u));
    if (best === null || dist < best.dist) best = { dist, s: cum[i - 1] + Math.sqrt(len2) * u };
  }
  return best;
}

// Project P onto a route's polylines (each { points, cum }); return
// { poly, s, dist } for the closest one within maxDist, else null. maxDist is
// parameterized (default = the subway constant) so a later increment can pass a
// looser railroad tolerance without touching callers.
function projectOntoRoute(routeGeom, pLat, pLon, maxDist = ROUTE_ACCEPT_DIST) {
  let best = null;
  for (let i = 0; i < routeGeom.length; i++) {
    const r = _projectOntoPolyline(routeGeom[i].points, routeGeom[i].cum, pLat, pLon);
    if (r && (best === null || r.dist < best.dist)) best = { poly: i, s: r.s, dist: r.dist };
  }
  return best && best.dist <= maxDist ? best : null;
}

// Slice a train's route polyline between its prev and next station. `geom` is the
// resolved [{points, cum}, ...] for the train's route (the CALLER looks it up, so
// this stays pure and serves both the subway and railroad route indexes); maxSlice
// / acceptDist default to the subway constants. Returns { points, cum, s0, s1 }
// when both stations project onto the SAME polyline within tolerance and the arc
// between them is plausible; null otherwise (trainLatLng then uses the straight line).
// s0/s1 are returned unordered (not min/max): the arc is walked in the sign of
// (s1 - s0), so a single stored shape serves both travel directions.
function computeRouteSlice(train, geom, { maxSlice = ROUTE_MAX_SLICE, acceptDist = ROUTE_ACCEPT_DIST } = {}) {
  if (train.prev_lat == null) return null;
  if (!geom) return null;
  const p0 = projectOntoRoute(geom, train.prev_lat, train.prev_lon, acceptDist);
  const p1 = projectOntoRoute(geom, train.latitude, train.longitude, acceptDist);
  if (!p0 || !p1 || p0.poly !== p1.poly) return null;
  if (Math.abs(p1.s - p0.s) > maxSlice) return null;
  const poly = geom[p0.poly];
  return { points: poly.points, cum: poly.cum, s0: p0.s, s1: p1.s };
}

// v2 train position: walk the route polyline from the previous-station offset to
// the next-station offset, parameterized by time. train._route ({ points, cum,
// s0, s1 }) is attached per poll by map.js when both stations projected cleanly
// onto the SAME polyline; absent otherwise, so this falls back to the v1 straight
// line. `now` is skew-corrected epoch seconds. `state` carries the monotonic-f
// clamp across calls: f may not decrease within a segment (so a growing next_time
// on a dwelling train can't drag the marker backward); it resets per segment.
function trainLatLng(train, now, state = {}) {
  const { prev_lat, prev_lon, prev_time, next_time, latitude, longitude } = train;
  // Unusable timing: sit at the static next-station position (v1 behavior).
  if (prev_lat == null || prev_time == null || next_time == null || next_time <= prev_time) {
    return [latitude, longitude];
  }
  const segKey = `${prev_time}|${train.stop_id}`;
  if (state.segKey !== segKey) {
    state.segKey = segKey;
    state.lastF = 0;
  }
  const rawF = (now - prev_time) / (next_time - prev_time);
  const f = Math.min(1, Math.max(rawF, state.lastF));
  state.lastF = f;
  const r = train._route;
  if (r) return pointAtArcLength(r.points, r.cum, r.s0 + (r.s1 - r.s0) * f);
  return [prev_lat + (latitude - prev_lat) * f, prev_lon + (longitude - prev_lon) * f];
}

// The countdown DECISION, separated from its wording. A1 needs the same
// thresholds spoken rather than abbreviated ("4 minutes", not "4 min", which a
// screen reader reads as "four min"), and the one thing that must not happen is
// two functions rounding or bucketing time differently. So the rounding lives
// here once and both formatters below are thin wordings of this result.
// Returns {kind: "blank" | "now" | "min" | "hm", mins, hours, rem}.
function countdownParts(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return { kind: "blank" };
  if (seconds < 30) return { kind: "now" };
  const mins = Math.round(seconds / 60);
  // Hours tier for the long railroad branch-end horizons (e.g. 6000s -> "1 h 40
  // min"); only fires at 100+ minutes, which subway countdowns never reach.
  if (mins < 100) return { kind: "min", mins };
  return { kind: "hm", hours: Math.floor(mins / 60), rem: mins % 60 };
}

// Arrival countdown label from a seconds-until-arrival delta: "now" when due
// (or past), else rounded to whole minutes. The VISUAL wording, used by every
// popup; its output is unchanged by the countdownParts extraction above and the
// existing tests are what prove that.
function formatCountdown(seconds) {
  const p = countdownParts(seconds);
  if (p.kind === "blank") return "";
  if (p.kind === "now") return "now";
  if (p.kind === "min") return `${p.mins} min`;
  return `${p.hours} h ${p.rem} min`;
}

// The SPOKEN wording of the same decision, for the A1 station panel. Units are
// written out and pluralized, because this text is read aloud rather than
// scanned. Sharing countdownParts is what keeps "4 min" and "in 4 minutes" from
// ever disagreeing about which minute it is.
function spokenCountdown(seconds) {
  const p = countdownParts(seconds);
  if (p.kind === "blank") return "";
  if (p.kind === "now") return "now";
  const unit = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (p.kind === "min") return `in ${unit(p.mins, "minute")}`;
  if (!p.rem) return `in ${unit(p.hours, "hour")}`;
  return `in ${unit(p.hours, "hour")} ${unit(p.rem, "minute")}`;
}

// Arrivals buckets in a stable display order for a station popup. The backends
// send only the non-empty buckets, so this orders the ones that have trains and
// never fabricates empties. Returns [[name, arrivals], ...]. Any unexpected key
// is appended rather than dropped, so a backend change can never silently hide
// trains. Shared by the railroad and PATH orderings below, which differ only in
// their bucket-name lists.
function orderedBuckets(order, directions) {
  const present = directions || {};
  const known = order.filter((name) => (present[name] || []).length);
  const extra = Object.keys(present).filter(
    (name) => !order.includes(name) && (present[name] || []).length,
  );
  return [...known, ...extra].map((name) => [name, present[name]]);
}

// Railroad buckets: Inbound first (toward the NYC terminal, the common ask),
// then Outbound, then the residual "Trains" bucket (for trips whose direction
// the backend could neither read from direction_id nor infer from the MNR
// stop-progression heuristic).
const RAILROAD_BUCKET_ORDER = ["Inbound", "Outbound", "Trains"];

function orderedRailroadBuckets(directions) {
  return orderedBuckets(RAILROAD_BUCKET_ORDER, directions);
}

// Rider-facing head text for a railroad TRAIN popup: "LIRR · Babylon Branch"
// when the route name is known, else "LIRR route 5", else just the system.
// Returns PLAIN text (system, routeId, and name are all feed-derived, so the
// caller escapes the whole result before inserting it into markup).
function formatRailroadHead(system, routeId, name) {
  const sys = system || "";
  if (name) return `${sys} · ${name}`;
  if (routeId) return `${sys} route ${routeId}`;
  return sys;
}

// Full railroad station arrivals popup HTML. Lives here (not map.js) so node can
// test the escaping and ordering. `now` is the skew-corrected clock, passed in
// for testability (map.js computes it from minClockOffset). `nameFor(routeId)`
// resolves a route's rider-facing name for this station's system (map.js closes
// over the (system|route_id) name map), returning null when unknown. Header is
// the station name plus a muted system tag; each present bucket renders its
// heading and one row per train: a route badge (railroadColor, white text on the
// dark palette), the route name where known, the train number when the feed
// carries one, and the countdown. Every feed-derived string is escaped.
function railroadArrivalsHtml(station, body, now, nameFor = () => null) {
  const header =
    `<b>${esc(station.name ?? station.id)}</b> ` +
    `<span class="popup-sub">${esc(station.system ?? "")}</span>` +
    feedAgeLine(body.fetched_at, now); // "as of Xm ago" when the rows are stale (R1)
  const buckets = orderedRailroadBuckets(body.directions);
  if (!buckets.length) return `${header}<div class="arr-none">No trains</div>`;
  let html = header;
  for (const [dir, arrivals] of buckets) {
    html += `<div class="arr-dir">${esc(dir)}</div>`;
    html += arrivals
      .map((a) => {
        const route = a.route_id ?? "";
        const badge =
          `<span class="arr-badge" style="background:${railroadColor(route)};color:${readableTextOn(railroadColor(route))}">` +
          `${esc(route || "?")}</span>`;
        const routeName = a.route_id ? nameFor(a.route_id) : null;
        const label = routeName ? ` ${esc(routeName)}` : "";
        const num = a.train_num ? ` <span class="popup-sub">#${esc(a.train_num)}</span>` : "";
        return `${badge}${label}${num} ${esc(formatCountdown(a.arrival - now))}`;
      })
      .join("<br>");
  }
  return html;
}

// ---- AirTrain JFK (static-only, no realtime feed) ----

// Parse an "HH:MM" band bound to minutes since midnight, accepting "24:00" (1440)
// as an end-of-day bound.
function hhmmToMinutes(hhmm) {
  const [h, m] = String(hhmm).split(":");
  return Number(h) * 60 + Number(m);
}

// Select the scheduled AirTrain headway band covering a minute-of-day, using
// HALF-OPEN [start, end) intervals so every minute maps to exactly one band (one
// band's end bound is the next band's start). `minutesSinceMidnight` is 0..1439.
// Returns the band (carrying headway_min) or null when NO band covers the minute.
// The null case is defensive on purpose: a future regenerated fixture could leave
// a gap, and returning null (so the caller can say "schedule unavailable") is safer
// than assuming the table always tiles the full day and guessing a nearest band.
function selectHeadwayBand(bands, minutesSinceMidnight) {
  for (const band of bands ?? []) {
    const start = hhmmToMinutes(band.start);
    const end = hhmmToMinutes(band.end);
    if (minutesSinceMidnight >= start && minutesSinceMidnight < end) return band;
  }
  return null;
}

// AirTrain JFK station popup HTML. WHY this is a plain static popup and NOT the
// live arrivals component (bindStationPopup / openStationArrivals / the 1s
// countdown tick): AirTrain has no realtime feed, so there is nothing to count
// down to, and a ticking "arriving in N min" would fabricate precision the data
// does not have. Instead we show the SCHEDULED headway band for the current time,
// clearly labeled "(scheduled)". `minutes` is minutes since NY midnight, computed
// by the CALLER and passed in (kept pure and testable with a plain numeric input).
// Every feed-derived string is escaped.
function airtrainStationPopupHtml(station, routes, minutes) {
  const serving = (routes ?? []).filter((r) => (r.stations ?? []).includes(station.id));
  const header =
    `<b>${esc(station.name ?? station.id)}</b>` +
    `<div class="popup-sub">AirTrain JFK &middot; scheduled service (no live tracking)</div>`;
  if (!serving.length) {
    return `${header}<div>No AirTrain branch serves this station.</div>`;
  }
  let html = header;
  for (const route of serving) {
    const band = selectHeadwayBand(route.headways, minutes);
    const name = esc(route.name ?? route.id);
    // headway_min is a validated integer (AirTrainHeadwayBand.headway_min: int), not
    // feed-derived text, so it is interpolated directly; esc() is reserved for the
    // untrusted string fields (station and route names).
    html += band
      ? `<div>${name}: every ~${band.headway_min} min <span class="popup-sub">(scheduled)</span></div>`
      : `<div>${name}: <span class="popup-sub">schedule unavailable</span></div>`;
  }
  return html;
}

// ---- PATH (phase 13c: map layer over the 13a/13b endpoints) ----

// PATH buckets: "To New York" first (the dominant commute ask, mirroring the
// railroad's Inbound-first choice), then "To New Jersey", then the residual
// "Trains" bucket for trips the bridge feed served without a direction_id.
const PATH_BUCKET_ORDER = ["To New York", "To New Jersey", "Trains"];

function orderedPathBuckets(directions) {
  return orderedBuckets(PATH_BUCKET_ORDER, directions);
}

// Neutral slate for a PATH route the color table doesn't know; belongs to no
// real PATH route color, so a fallback is visually honest about being one.
const PATH_FALLBACK_COLOR = "#546e7a";

// /api/path-routes serves route_color verbatim from routes.txt: bare hex, no
// "#", possibly null. Validate before prefixing rather than trusting the feed,
// so a malformed value falls back instead of reaching a style attribute.
function pathColor(hex, fallback = PATH_FALLBACK_COLOR) {
  return /^[0-9a-fA-F]{6}$/.test(hex ?? "") ? `#${hex}` : fallback;
}

// Rider-facing head text for a PATH train popup: the route's rider-facing name
// ("Newark - World Trade Center") when known, else the route id, else just
// "PATH". Returns PLAIN text; the caller escapes it (the railroad precedent).
function formatPathHead(routeId, name) {
  if (name) return name;
  if (routeId) return `PATH route ${routeId}`;
  return "PATH";
}

// PATH train popup HTML. `name` is the rider-facing route name (null when
// unknown) and `color` a css color, both resolved by the caller from the
// /api/path-routes tables so this stays pure. Two deliberate omissions against
// the subway train popup: no trip id line, because PATH bridge trip ids are
// unstable across upstream refreshes and display-poor (the API contract says
// clients never show or key on them), and no alerts block, because PATH
// publishes no alerts feed. Every feed-derived string is escaped.
function pathTrainPopupHtml(train, name, color) {
  return (
    `<b style="color:${readableInk(color)}">${esc(formatPathHead(train.route_id, name))}</b>` +
    ` <span class="popup-sub">PATH</span>` +
    (train.stop_name ? `<br>Next stop: ${esc(train.stop_name)}` : "") +
    (train.direction ? `<br>${esc(train.direction)}` : "") +
    `<br><span class="popup-sub">scheduled position (no GPS)</span>`
  );
}

// PATH station arrivals popup HTML, the railroad renderer's shape minus
// train_num (the bridge feed carries none). `colorFor(routeId)` resolves a
// route's css badge color and `nameFor(routeId)` its rider-facing name; map.js
// closes both over the /api/path-routes tables, keeping this pure and
// node-testable. An empty directions dict renders the shared "No trains"
// treatment. Every feed-derived string is escaped.
function pathArrivalsHtml(station, body, now, colorFor = () => PATH_FALLBACK_COLOR, nameFor = () => null) {
  const header =
    `<b>${esc(station.name ?? station.id)}</b> ` +
    `<span class="popup-sub">PATH</span>` +
    feedAgeLine(body.fetched_at, now); // "as of Xm ago" when the rows are stale (R1)
  const buckets = orderedPathBuckets(body.directions);
  if (!buckets.length) return `${header}<div class="arr-none">No trains</div>`;
  let html = header;
  for (const [dir, arrivals] of buckets) {
    html += `<div class="arr-dir">${esc(dir)}</div>`;
    html += arrivals
      .map((a) => {
        const route = a.route_id ?? "";
        const badge =
          `<span class="arr-badge" style="background:${colorFor(a.route_id)};color:${readableTextOn(colorFor(a.route_id))}">` +
          `${esc(route || "?")}</span>`;
        const routeName = a.route_id ? nameFor(a.route_id) : null;
        const label = routeName ? ` ${esc(routeName)}` : "";
        return `${badge}${label} ${esc(formatCountdown(a.arrival - now))}`;
      })
      .join("<br>");
  }
  return html;
}

// ---- NYC Ferry (phase 14c: map layer over the 14a/14b endpoints) ----

// Neutral blue-gray for a boat whose route the color table doesn't know (a 14b
// join miss, kept on the map and labeled "Unassigned"): belongs to no real NYC
// Ferry route color, so a fallback reads as honestly being one. Distinct from
// PATH's slate so a stray unassigned boat isn't mistaken for a PATH marker.
const FERRY_FALLBACK_COLOR = "#78909c";

// Ferry arrivals bucket order: the /api/ferry-arrivals feed has NO direction_id,
// so buckets are ROUTE NAMES (a dynamic set, unlike the fixed direction lists the
// other systems use). Sort them alphabetically for a stable, predictable popup;
// only buckets that actually carry boats are returned (the backend sends only
// populated ones, and this filters defensively). Returns [[routeName, rows], ...].
function orderedFerryBuckets(routes) {
  const present = routes || {};
  return Object.keys(present)
    .filter((name) => (present[name] || []).length)
    .sort()
    .map((name) => [name, present[name]]);
}

// Pick the countdown a ferry arrivals ROW should show. Before the boat reaches
// the dock, count down to its ARRIVAL. Once it has arrived and is dwelling at
// the dock (arrival already passed but departure still ahead), or the dock is an
// origin with no arrival at all, count down to its DEPARTURE instead: at that
// point the rider cares when it LEAVES, not that it technically docked a moment
// ago. This is the dwell data (both times, from 14b) earning its passage.
// Returns { mode: "arriving" | "departing", seconds }, and never drops a row: a
// terminal dock with only an arrival keeps the arrival countdown even once past.
function ferryArrivalDisplay(row, now) {
  const arrival = row.arrival;
  const departure = row.departure;
  if (arrival != null && arrival - now >= 0) {
    return { mode: "arriving", seconds: arrival - now };
  }
  if (departure != null) {
    return { mode: "departing", seconds: departure - now };
  }
  return { mode: "arriving", seconds: arrival != null ? arrival - now : null };
}

// A docked boat's resting opacity: dimmed so it reads as parked at a dock rather
// than under way. It lives here, next to the other opacity rule, because it is now an
// input to markerOpacity rather than a css class of its own: one opacity authority
// per marker element, so the C2 staleness dimming compounds with it instead of
// overriding it. The .ferry-docked / .ferry-active classes remain as state markers.
const FERRY_DOCKED_OPACITY = 0.55;

// Map a boat's GTFS current_status to the icon variant. STOPPED_AT means the boat
// is sitting at a dock (render docked/dimmed); everything else (IN_TRANSIT_TO,
// INCOMING_AT, or a missing/unknown status) means under way (render active). The
// default is deliberately "active": a boat with GPS that is not explicitly
// STOPPED_AT should not be frozen-looking, and an unknown future enum value is
// safer shown moving than parked.
function ferryBoatIconState(status) {
  return status === "STOPPED_AT" ? "docked" : "active";
}

// Plain-words status for a boat popup, or null when the feed omits/uses an
// unknown status (the popup then shows no status line rather than asserting a
// guess). The three values 14b observed map to rider-facing phrases.
function ferryStatusText(status) {
  switch (status) {
    case "STOPPED_AT":
      return "At dock";
    case "INCOMING_AT":
      return "Arriving at dock";
    case "IN_TRANSIT_TO":
      return "Under way";
    default:
      return null;
  }
}

// GTFS-RT Position.speed is meters per second; boat popups show it in knots, the
// convention for vessels. 1 m/s = 1.94384 kn.
const MS_TO_KNOTS = 1.94384;
// Below this the reading is GPS jitter, not travel: a boat sitting at a dock still
// reports a few tenths of a knot of drift. 0.5 m/s is ~1 kn, comfortably above that
// noise and well below any real ferry cruising speed (10-25 kn).
const FERRY_SPEED_FLOOR_MS = 0.5;

// A boat's speed as an "N.N kn" string, or null when it should not be shown. Shown
// ONLY for an under-way boat (IN_TRANSIT_TO) moving above the jitter floor: a docked
// boat, or one whose reading is sub-floor drift, shows no speed rather than a
// misleading fraction of a knot. Pure and node-testable; the popup renders the line
// only when this returns a value.
function ferrySpeedKnots(status, speedMs) {
  if (status !== "IN_TRANSIT_TO") return null;
  if (typeof speedMs !== "number" || !Number.isFinite(speedMs) || speedMs < FERRY_SPEED_FLOOR_MS) {
    return null;
  }
  return `${(speedMs * MS_TO_KNOTS).toFixed(1)} kn`;
}

// Ferry BOAT popup HTML. `name` is the route long name (null when the boat did
// not join a route: 14b keeps it on the map, and here it reads "Unassigned" in
// the neutral fallback color) and `color` a css color, both resolved by the
// caller from the /api/ferry-routes tables so this stays pure and node-testable.
// Speed is shown in knots for an under-way boat above the jitter floor (H4; see
// ferrySpeedKnots): the GTFS-RT unit is meters per second, confirmed by the observed
// 0-13 m/s = 0-25 kn range matching NYC Ferry hull speeds. NO alerts block IN THIS
// FUNCTION: route-scoped ferry alerts are shown, but the caller (ferryBoatPopup)
// prepends them via routeAlertsBlock so this stays a pure HTML builder, exactly as
// the subway/bus popup HTML helpers keep their route-alert prepend in the caller.
// Every feed-derived string is escaped.
function ferryBoatPopupHtml(boat, name, color) {
  const routeText = name || "Unassigned";
  const status = ferryStatusText(boat.status);
  const speed = ferrySpeedKnots(boat.status, boat.speed);
  return (
    `<b style="color:${readableInk(color)}">${esc(routeText)}</b>` +
    ` <span class="popup-sub">NYC Ferry</span>` +
    (boat.label ? `<br>Boat ${esc(boat.label)}` : "") +
    (status ? `<br>${esc(status)}` : "") +
    (speed ? `<br>${esc(speed)}` : "")
  );
}

// Ferry DOCK arrivals popup HTML. Buckets are route names (orderedFerryBuckets);
// each row is a countdown, shown as "departs …" when the boat is dwelling or the
// dock is an origin (ferryArrivalDisplay), else the plain arrival countdown. The
// bucket heading is colored by its route (all rows in a bucket share a route, so
// the color comes from the first row's route_id via colorFor). The station's
// `wheelchair` flag surfaces as a small accessibility marker in the header, the
// first such display in the app. An empty routes dict renders "No boats". Every
// feed-derived string is escaped; colorFor returns a validated css color.
function ferryArrivalsHtml(station, body, now, colorFor = () => FERRY_FALLBACK_COLOR) {
  const access = station.wheelchair
    ? ' <span class="popup-access" title="Wheelchair accessible">&#9855;</span>'
    : "";
  const header =
    `<b>${esc(station.name ?? station.id)}</b> ` +
    `<span class="popup-sub">NYC Ferry</span>${access}` +
    feedAgeLine(body.fetched_at, now); // "as of Xm ago" when the rows are stale (R1)
  const buckets = orderedFerryBuckets(body.routes);
  if (!buckets.length) return `${header}<div class="arr-none">No boats</div>`;
  let html = header;
  for (const [routeName, rows] of buckets) {
    const color = rows[0] && rows[0].route_id ? colorFor(rows[0].route_id) : FERRY_FALLBACK_COLOR;
    html += `<div class="arr-dir" style="color:${readableInk(color)}">${esc(routeName)}</div>`;
    html += rows
      .map((row) => {
        const d = ferryArrivalDisplay(row, now);
        const prefix = d.mode === "departing" ? "departs " : "";
        return `${prefix}${esc(formatCountdown(d.seconds))}`;
      })
      .join("<br>");
  }
  return html;
}

// ---- Service alerts in the station popups (phase 12b) ----

// Alerts staleness threshold. The alerts feed polls every 60s (vs 15s for the vehicle
// feeds) and its content changes slowly: a service alert persists for hours, so a
// slightly old alert index is far less misleading than slightly old vehicle
// positions. The honesty bar is therefore higher than the 90s feed bar. The alerts
// loop swallows failures by design (it never surfaces an error or blocks arrivals),
// so this marker is the one honest signal that the index may have stopped updating.
//
// WHAT THE 300s IS MEASURED AGAINST changed in C1, so read it as five missed BACKEND
// polls, not five missed client fetches. The comment here used to say "five missed
// polls" while the gate keyed on served_at, i.e. on the client's own fetches; it now
// keys on the backend's fetched_at, which advances only on a poll that decoded. Both
// cadences are 60s (ALERT_POLL_INTERVAL_S here and in pollers.py), so the count is
// unchanged, but the quantity is now the age of the DATA and includes any time the
// backend spent failing or timing out (up to REFRESH_DEADLINE_S per poll).
//
// THE PARTIAL CASE IS COVERED AS OF C2, and this is where the limit used to be
// documented. It read: fetched_at is poll-level, a poll where four of five feeds
// decode is a SUCCESS that advances it, so one system down for hours never tripped
// this marker even after the backend's retention cap had dropped that system's
// alerts entirely. That limit existed because /api/alerts carried nothing per
// system for the client to key on. It now carries a `systems` block, so the basis
// is the WORST (oldest) system's fetched_at rather than the envelope's: one down
// alert system trips the marker at the same threshold. See alertsFreshnessBasis.
const ALERTS_STALE_AFTER_S = THRESHOLD_OVERRIDES.alerts ?? PRODUCTION_ALERTS_STALE_AFTER_S;

// True when the alert DATA is older than the threshold, judged from the payload's
// fetched_at: the backend's last poll that actually decoded. `now` is the skew-
// corrected client clock, i.e. already on the same server-time axis as fetched_at.
// A null fetchedAt (no successful fetch yet, e.g. during boot) is NOT stale: the app
// simply shows no alerts, not a false "out of date". Pure and node-testable; the
// banner and popup alert blocks gate their "alerts may be out of date" marker on this.
//
// C1 CHANGED THE SIGNAL FROM served_at TO fetched_at, correcting the R1 choice.
// served_at is stamped at response build, so the served_at of a stale cache is fresh
// BY CONSTRUCTION: while the alert feeds were down the backend kept 200ing its frozen
// index with an ever-advancing served_at, which reset this gate on every poll. The
// marker therefore could not fire during the exact outage it exists to hedge. It was
// measuring DELIVERY (did a response arrive) when the honest question is about DATA
// (has the index been refreshed). fetched_at only advances on a poll that decoded,
// so a 200 whose fetched_at has not moved is precisely the outage signature.
// sinceAt is the fallback age basis for the case where the client has NEVER received a
// fetched_at: the instant it first tried. Without it, a null fetchedAt returned the
// healthy answer forever, so a backend whose alert index never filled (every feed down
// since boot, so /api/alerts serves an error and loadAlerts swallows it) showed riders
// a confident, alert-free map with no hedge, indefinitely. That is the same
// never-defaulted silence this whole change is about, just at the other end of the
// wire. A boot grace period is still right, so the null case ages against sinceAt on
// the same threshold rather than disclosing immediately; pass null for sinceAt to get
// the old unbounded-grace behavior.
// The alerts freshness basis extracted from an /api/alerts body: the backend's
// fetched_at, or null when a body omits it entirely. THE POINT OF THIS BEING A
// FUNCTION is that it makes the field CHOICE testable. A node test that hands
// alertsStale a fetched_at proves only arithmetic, because the test picked the field
// itself; a revert to served_at would sail past it. This is where the choice lives,
// so this is what a test has to pin.
//
// C2 MADE IT THE WORST SYSTEM'S fetched_at when the body carries a per-system block.
// The envelope's own fetched_at advances on any poll where at least one alert feed
// decoded, so it hid a partial outage completely (the F1 finding); the oldest system
// that HAS decoded is the honest basis, because the alert set a rider is looking at
// is only as current as its least-current contributor. Falls back to the envelope
// fetched_at when there is no systems block at all, and returns null only when
// nothing at all has decoded.
//
// A SYSTEM THAT HAS NEVER DECODED IS SKIPPED, NOT PROPAGATED AS NULL. REVIEW FIX:
// this first returned null as soon as any one system reported a non-numeric
// fetched_at, which threw away four known-old timestamps because a fifth was
// missing, and null re-bases the whole hedge on the client's first-attempt time.
// That was wrong in BOTH directions: a freshly loaded tab showed no hedge for the
// full threshold during a total freeze that the envelope timestamp used to disclose
// at once, and a tab open for hours latched the hedge permanently on a backend where
// four of five feeds were current. The backend really does serve that shape (a feed
// that fails its first poll of a process keeps fresh_at null while the others
// advance), so it is the common case, not a corner.
//
// KNOWN LIMIT, stated rather than papered over: a system that has never decoded
// contributes no age, so its alerts being entirely absent does not by itself trip
// the marker. Latching the marker forever on that signal is the behavior described
// above, and it contradicts the rule the status line applies to feeds (a system is
// not called out until its age crosses the threshold). It stays visible to an
// operator through degraded_systems on /api/status.
function alertsFreshnessBasis(body) {
  const systems = body == null ? null : body.systems;
  if (systems != null && typeof systems === "object") {
    const decoded = Object.values(systems)
      .map((system) => (system ?? {}).fetched_at)
      .filter((value) => typeof value === "number");
    if (decoded.length) return Math.min(...decoded);
    if (Object.keys(systems).length) return null; // a block, but nothing decoded yet
  }
  const fetchedAt = body == null ? null : body.fetched_at;
  return typeof fetchedAt === "number" ? fetchedAt : null;
}

function alertsStale(fetchedAt, now, sinceAt = null) {
  if (fetchedAt == null) {
    if (sinceAt == null) return false;
    return now - sinceAt >= ALERTS_STALE_AFTER_S;
  }
  return now - fetchedAt >= ALERTS_STALE_AFTER_S;
}

// Index the active-alerts list into two lookups, each keyed by "system|id": one by
// stop selector, one by route selector. WHY the key embeds the system: numeric ids
// collide ACROSS systems (LIRR route "1" vs subway route "1" vs MNR route "1"), so a
// join scoped only by id would leak alerts between modes. Every lookup below is
// therefore system-scoped.
function indexAlerts(alerts) {
  const byStop = new Map(); // "system|stop_id" -> [alert, ...]
  const byRoute = new Map(); // "system|route_id" -> [alert, ...]
  const push = (map, key, alert) => {
    const list = map.get(key);
    if (list) list.push(alert);
    else map.set(key, [alert]);
  };
  for (const alert of alerts ?? []) {
    for (const stop of alert.stops ?? []) push(byStop, `${alert.system}|${stop}`, alert);
    for (const route of alert.routes ?? []) push(byRoute, `${alert.system}|${route}`, alert);
  }
  return { byStop, byRoute };
}

// Shared deterministic order for an alerts list: open-ended (no end) first, then by
// starts_at (earliest first, a null start sorts first), then id. Reused by the
// station, route, and banner matchers so the ordering is identical everywhere.
function compareAlerts(a, b) {
  const aOpen = a.ends_at == null ? 0 : 1;
  const bOpen = b.ends_at == null ? 0 : 1;
  if (aOpen !== bOpen) return aOpen - bOpen;
  const aStart = a.starts_at ?? -Infinity;
  const bStart = b.starts_at ?? -Infinity;
  if (aStart !== bStart) return aStart - bStart;
  return String(a.id).localeCompare(String(b.id));
}

// Alerts affecting one station popup, deduped and sorted. An alert applies when
// alert.system === system AND either (a) the station's id is in alert.stops, or
// (b) alert.routes intersects `routeIds`, the routes serving this station. Everything
// is scoped by `system`, so a numeric id shared across modes never leaks.
//
// `routeIds` is the caller's union of the STATIC routes-per-station index (every
// route serving the stop, from stop_times, H5) and the routes present in the CURRENT
// arrivals. The static side closes the gap the old arrivals-only match left open: a
// route that serves the station but has no imminent train there (a suspended route, a
// long late-night headway, a between-trains moment) still surfaces its route-scoped
// alert, instead of relying on the stop-level selectors (a) to enumerate it.
//
// Deterministic sort so the block is stable across refreshes: open-ended alerts (no
// end) first, then by starts_at (earliest first, a null start sorts first), then id.
function matchStationAlerts(index, system, stationId, routeIds) {
  const matched = new Map(); // id -> alert; an alert matching by BOTH stop and route appears once
  for (const alert of index.byStop.get(`${system}|${stationId}`) ?? []) matched.set(alert.id, alert);
  for (const routeId of routeIds ?? []) {
    for (const alert of index.byRoute.get(`${system}|${routeId}`) ?? []) matched.set(alert.id, alert);
  }
  return [...matched.values()].sort(compareAlerts);
}

// Alerts for a route surface (a bus, subway train, or railroad train popup), from
// the SAME byRoute lookup, scoped by system so a numeric route id shared across
// modes never leaks. A null or missing route_id matches nothing. Deduped (an alert
// naming the route more than once appears once) and sorted like the station matcher.
function matchRouteAlerts(index, system, routeId) {
  if (!routeId) return [];
  const matched = new Map();
  for (const alert of index.byRoute.get(`${system}|${routeId}`) ?? []) matched.set(alert.id, alert);
  return [...matched.values()].sort(compareAlerts);
}

// Agency-wide alerts for the banner: those that name NO route and NO stop, across
// ALL systems, sorted the same way. A route-scoped or stop-scoped alert is excluded
// (it belongs on its route/station surface, not the banner), so nothing is ever
// double-shown. Takes the raw alerts list, since selector-less alerts appear in
// neither byStop nor byRoute.
function bannerAlerts(alerts) {
  return (alerts ?? [])
    .filter((a) => !(a.routes ?? []).length && !(a.stops ?? []).length)
    .sort(compareAlerts);
}

// A small deterministic string hash (FNV-1a, 32-bit, hex). Used only to keep the
// banner's dedup key bounded when it folds in alert TEXT; nothing security-relevant
// rides on it. Written out rather than pulled in so the frontend stays dependency
// free, and >>> 0 keeps every step unsigned (JS bitwise ops are signed 32-bit).
function hashString(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16);
}

// The banner's render-dedup key: the stale flag, then one line per shown alert
// carrying its system, id, and a hash of its header TEXT.
//
// C1 ADDED THE CONTENT HASH. The key used to be the shown ids alone, so an alert
// whose WORDING changed under the same id compared equal and the banner was left
// untouched, showing the superseded text indefinitely. That is a real upstream
// pattern: the MTA revises an ongoing incident's description in place rather than
// issuing a new id. Hashing rather than embedding the full text keeps the key short;
// the tradeoff is that a 32-bit collision between two wordings of the SAME id would
// still skip the re-render, which is a far smaller exposure than never re-rendering.
function bannerRenderKey(shown, stale) {
  return (
    (stale ? "S|" : "F|") +
    (shown ?? [])
      .map((a) => `${a.system}|${a.id}|${hashString(String(a.header ?? ""))}`)
      .join("\n")
  );
}

// Compact alerts block for a station popup: one escaped header line per alert, or ""
// when there is nothing to show (so the caller renders NO container at all). Header
// text only in this phase (description/effect omitted); the text is kept verbatim
// and escaped, so bracketed route tokens like [Q] render as plain text, no
// substitution. Alerts with no header contribute nothing.
function alertsBlockHtml(alerts) {
  const rows = (alerts ?? [])
    .filter((a) => a.header)
    .map((a) => `<div class="alert-row">${esc(a.header)}</div>`);
  if (!rows.length) return "";
  return `<div class="alert-block">${rows.join("")}</div>`;
}

// ---- Static-loader retry (phase 12d) ----

// Retry fn until it resolves truthy, with doubling backoff from baseMs capped at
// capMs. A falsy resolution or a thrown error schedules the next attempt. WHY
// forever, with no attempt cap: the wrapped requests are cheap (the backend caches
// static payloads and serves 503/[] instantly while warming), and a map that never
// fills in is strictly worse than a slow retry hum in a background tab. WHY no
// jitter: jitter exists to de-synchronize a fleet of clients hammering a shared
// origin; here a handful of browsers each retry a cached endpoint every 30s at
// worst, so synchronized arrivals cost nothing and determinism keeps tests exact.
// `sleep` is injected so node tests resolve instantly and can assert the exact
// backoff sequence; the browser caller uses the default setTimeout sleep.
async function retryUntil(fn, { baseMs, capMs, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
  let wait = baseMs;
  for (;;) {
    let ok = false;
    try {
      ok = await fn();
    } catch {
      // thrown = falsy: a fetch/parse error is just another "not yet" signal
    }
    if (ok) return;
    await sleep(wait);
    wait = Math.min(wait * 2, capMs);
  }
}

/* ==================================================================
   A1: the accessible station surface
   ==================================================================

   Pure logic for frontend/stations.js: a searchable list of every system's
   stations and a text rendering of one station's live arrivals. The rendering
   half deliberately consumes the SAME bucket ordering and countdown decision the
   map popups use, so the two surfaces cannot drift into describing the same feed
   differently. Everything here is pure and node-tested; the DOM lives in
   stations.js.

   This is also the architectural parent of the future terminal boards, so
   nothing below knows what a terminal is. */

// How many result rows the panel shows before it stops and says how many it is
// holding back. Long enough that a real search is rarely truncated, short enough
// that a screen reader is never handed hundreds of rows to walk.
const STATION_RESULT_CAP = 50;

// Fold a name to its comparison form: decomposed, diacritics stripped,
// lowercased. Station names carry accents in the wild, and a rider typing
// unaccented ASCII must still find them, which is why this is not just
// toLowerCase.
function foldStationName(name) {
  return String(name ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

// Split a query into folded tokens. Tokenizing (rather than one substring test on
// the whole query) is what makes "grand cen" match "Grand Central": every token
// must appear somewhere in the name, in any order.
function stationQueryTokens(query) {
  return foldStationName(query).split(/\s+/).filter(Boolean);
}

function stationMatchesTokens(foldedName, tokens) {
  return tokens.every((token) => foldedName.includes(token));
}

// Search the union of every system's stations.
//
// Returns {prompt, rows, total, hidden}. `prompt` true means the query was empty
// and the caller should show its "type to search" line rather than a list: 900
// rows is not a useful answer to no question, and it is a hostile one to walk
// with a keyboard.
//
// ORDERING is prefix-matches-first, then by name, then by system, so typing
// "grand" puts "Grand Central" above "East Grand Street" and the order is total
// (never dependent on the input order of the stop tables, which load
// asynchronously and in a race). `rows` is capped; `hidden` is how many matches
// were withheld, so the caller can say so honestly instead of silently
// truncating.
function searchStations(stations, query, cap = STATION_RESULT_CAP) {
  const tokens = stationQueryTokens(query);
  if (!tokens.length) return { prompt: true, rows: [], total: 0, hidden: 0 };
  const matched = [];
  for (const station of stations || []) {
    const folded = foldStationName(station.name ?? station.id);
    if (stationMatchesTokens(folded, tokens)) matched.push({ station, folded });
  }
  const first = tokens[0];
  matched.sort((a, b) => {
    const ap = a.folded.startsWith(first) ? 0 : 1;
    const bp = b.folded.startsWith(first) ? 0 : 1;
    if (ap !== bp) return ap - bp;
    if (a.folded !== b.folded) return a.folded < b.folded ? -1 : 1;
    const asys = a.station.systemLabel ?? "";
    const bsys = b.station.systemLabel ?? "";
    return asys < bsys ? -1 : asys > bsys ? 1 : 0;
  });
  return {
    prompt: false,
    rows: matched.slice(0, cap).map((m) => m.station),
    total: matched.length,
    hidden: Math.max(0, matched.length - cap),
  };
}

// The overflow line's text, or "" when nothing was withheld. Worded as an
// instruction rather than a count alone, because the useful thing to tell someone
// who cannot see the list is what to DO about it.
function stationOverflowLine(hidden) {
  if (!hidden) return "";
  return `${hidden} more ${hidden === 1 ? "station" : "stations"} match; keep typing to narrow`;
}

// ---- Arrivals, shaped once and rendered twice ----

// Turn one arrivals payload into the structure both the popup markup and the
// panel text are built from: {ageSeconds, buckets: [{name, rows: [...]}]}.
//
// `kind` selects the bucket source and ordering, and the four cases are exactly
// the four the popups already implement: subway and railroad and PATH bucket
// body.directions (subway by compass, the others by their own orders), ferry
// buckets body.routes by name. The orderings come from the SAME helpers the
// popups call, so a change to either is a change to both.
//
// Each row carries what a rider needs and nothing derived from the clock except
// `seconds`: routeId, routeName (resolved by the caller), trainNum, mode
// ("arriving" or "departing", ferry only), and seconds-until. Keeping the clock
// out of the identity fields is what lets announcementWorthy below tell a real
// change from a tick.
const SUBWAY_BUCKET_ORDER = ["Northbound", "Southbound"];

function shapeStationArrivals(kind, body, now, opts = {}) {
  const nameFor = opts.nameFor || (() => null);
  const payload = body || {};
  let raw;
  if (kind === "ferry") {
    raw = orderedFerryBuckets(payload.routes);
  } else if (kind === "railroad") {
    raw = orderedRailroadBuckets(payload.directions);
  } else if (kind === "path") {
    raw = orderedPathBuckets(payload.directions);
  } else {
    raw = orderedBuckets(SUBWAY_BUCKET_ORDER, payload.directions);
  }
  const buckets = raw.map(([name, rows]) => ({
    name,
    rows: (rows || []).map((row) => {
      const display = kind === "ferry" ? ferryArrivalDisplay(row, now) : null;
      const seconds = display ? display.seconds : row.arrival - now;
      return {
        routeId: row.route_id ?? null,
        routeName: row.route_id ? nameFor(row.route_id) : null,
        trainNum: row.train_num ?? null,
        mode: display ? display.mode : "arriving",
        seconds,
        // The ABSOLUTE instant this row is about, kept alongside the countdown
        // rather than derived from it later. Two consumers need it and both would
        // otherwise reconstruct it as now + seconds: the clock label in the
        // sentence, and announcementWorthy, which can only tell a real change
        // from a tick by comparing absolute times.
        at: seconds == null || Number.isNaN(seconds) ? null : now + seconds,
      };
    }),
  }));
  return {
    // null rather than 0 when the payload carries no fetched_at, so the caller
    // can tell "fresh" from "unknown" the way feedAgeLine already does.
    ageSeconds: payload.fetched_at == null ? null : now - payload.fetched_at,
    buckets,
  };
}

// The wall-clock label for an arrival instant, in NEW YORK time regardless of
// where the browser is: the rider is asking about a New York train, and a
// countdown plus a clock time in a different zone is worse than no clock time.
// The zone is a parameter so the node tests can pin an exact string instead of
// asserting against the runner's locale.
function clockTimeLabel(epochSeconds, timeZone = "America/New_York") {
  if (epochSeconds == null || Number.isNaN(epochSeconds)) return "";
  return new Date(epochSeconds * 1000).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  });
}

// One arrival as a sentence a screen reader can read straight through.
//
// The shape is "<route> <noun> <countdown>, <clock> <what the clock is>":
// "Babylon train in 4 minutes, 8:12 AM arrival". Route NAME is preferred over
// route id because "Babylon" is what a rider calls it and "5" is not; the id is
// the fallback. The noun and verb carry the mode, so a ferry dwelling at its dock
// says "departs" rather than implying it is still inbound.
//
// THE CLOCK LABEL SAYS WHICH INSTANT IT IS, and for most systems that is the
// ARRIVAL, not the departure. The phase spec's example sentence reads "8:12
// departure", but the subway, railroad and PATH arrivals endpoints carry an
// `arrival` field and no departure, so calling it a departure would be a small
// lie of exactly the kind this codebase spends its comments avoiding. Ferry rows
// DO carry a departure and say so when that is the instant being counted down.
//
// The train number, where the feed has one, goes last as an aside: it identifies
// the train for someone who cares and is noise for everyone else.
function arrivalSentence(row, noun = "train", timeZone = "America/New_York") {
  const label = row.routeName || row.routeId || "";
  const countdown = spokenCountdown(row.seconds);
  const departing = row.mode === "departing";
  const parts = [label, noun, departing ? "departs" : "", countdown].filter(Boolean);
  let sentence = parts.join(" ");
  const clock = clockTimeLabel(row.at, timeZone);
  if (clock) sentence += `, ${clock} ${departing ? "departure" : "arrival"}`;
  return row.trainNum ? `${sentence}, train ${row.trainNum}` : sentence;
}

// ---- Live region discipline ----

// How far a bucket's next arrival must move before it is worth interrupting
// someone to say so. Under a minute is prediction jitter: the feeds revise
// arrival estimates by a few seconds on every poll, and announcing that is how a
// live region becomes something a rider turns off.
const ANNOUNCE_LEAD_SHIFT_S = 60;

// A bucket's comparable signature: which routes are present (as a SORTED
// multiset, so a reordered payload is not a change) and when the next one
// arrives. Sorted rather than sequential is deliberate: the backends do not
// promise a stable row order, and announcing on a reshuffle of the same trains
// would be noise.
function arrivalsSignature(shaped) {
  const buckets = {};
  for (const bucket of (shaped && shaped.buckets) || []) {
    // THE IDENTITY IS WHAT THE RIDER CAN SEE, which is route plus train number
    // where the feed carries one. That choice decides the swapped-lead case: a
    // railroad train 8412 replaced by 8414 at nearly the same minute changes the
    // rendered sentence, so it is news and this key changes with it. The same
    // swap on the subway, where no train number exists and every "1" train reads
    // identically, changes nothing a rider could perceive, so the key is stable
    // and the live region stays quiet. Announcing an invisible identity change
    // would be indistinguishable from noise to the person listening.
    const routes = bucket.rows
      .map((r) => `${r.routeId ?? "?"}|${r.trainNum ?? ""}`)
      .sort();
    // Lead arrival as the ABSOLUTE instant the shaped row already carries.
    // Comparing absolute times is what makes a tick a non-event: the same train
    // an hour from now is the same instant on every tick, while `seconds` counts
    // down by one each time.
    const leads = bucket.rows.map((r) => r.at).filter((t) => t != null);
    buckets[bucket.name] = { routes, lead: leads.length ? Math.min(...leads) : null };
  }
  return buckets;
}

// Should the live region speak?
//
// Announce when the ARRIVALS changed in a way a rider would care about:
//   1. a bucket appeared or vanished (a direction started or stopped running),
//   2. a bucket's set of routes changed (a train appeared, vanished, or the next
//      one is on a different line),
//   3. a bucket's next arrival moved by more than ANNOUNCE_LEAD_SHIFT_S (the
//      wait got materially longer or shorter).
//
// Stay silent on everything else, and the case that matters most is the
// countdown tick: none of the three clauses reads the clock, so a second passing
// can never trip them. Without that, a screen reader narrates "4 minutes...
// 3 minutes..." forever, which is hostile enough that the rider disables the
// feature and loses the arrivals with it.
//
// `prev` and `next` are shaped payloads. A first render (no prev) announces once,
// because the arrivals appearing IS the news.
function announcementWorthy(prev, next) {
  if (!next) return false;
  if (!prev) return true;
  const before = arrivalsSignature(prev);
  const after = arrivalsSignature(next);
  const names = new Set([...Object.keys(before), ...Object.keys(after)]);
  for (const name of names) {
    const a = before[name];
    const b = after[name];
    if (!a || !b) return true; // clause 1: a bucket came or went
    if (a.routes.length !== b.routes.length) return true; // clause 2
    for (let i = 0; i < a.routes.length; i++) {
      if (a.routes[i] !== b.routes[i]) return true; // clause 2
    }
    if (a.lead == null !== (b.lead == null)) return true;
    if (a.lead != null && b.lead != null && Math.abs(b.lead - a.lead) > ANNOUNCE_LEAD_SHIFT_S) {
      return true; // clause 3
    }
  }
  return false;
}

/* ---------------- A2: what a marker is called ---------------- */

// THE NAMES ON THE MAP. Every one of these builds from the SAME fields its system's
// popup renders, so a marker and its popup can never describe different trains. They
// are pure and take their lookups by injection, which is what lets the node tests pin
// the wording instead of asserting against a live map.
//
// They are also deliberately plain sentences rather than the popup's shorthand. A
// popup can afford "Next stop:" as a label above a value because it is laid out in
// two dimensions; a name is read as one line of speech, so it has to be a sentence a
// person would say. The rule for every builder below: name the vehicle, say where it
// is going or what it is doing, and stop. No trip ids, no coordinates, no counts.
//
// NOTHING HERE READS THE CLOCK OR THE FRESHNESS INDEX. A stale marker is already
// dimmed and its popup already carries the age line; folding "as of 4m ago" into the
// name would make every label change on a timer, which is the announcement problem
// A1 solved and has no business coming back through the marker layer.

// Join the parts of a name, dropping the empty ones, so a missing field leaves no
// double comma and no dangling "to".
function joinName(parts) {
  return parts.filter((part) => part != null && part !== "").join(", ");
}

// "1 train, next stop Times Sq-42 St, Northbound". route_id is the same bullet the
// icon shows; an unknown route says so rather than reading the literal "?" glyph.
function subwayTrainName(train) {
  const t = train || {};
  const route = t.route_id ? `${t.route_id} train` : "Subway train";
  const stop = t.stop_name || t.stop_id;
  return joinName([route, stop ? `next stop ${stop}` : null, t.direction || null]);
}

// "MNR" is what the feed calls it and what the popup prints; "Metro-North" is what a
// rider calls it, and what the A1 station panel already says. A name that is going to
// be SPOKEN uses the rider's word, because an initialism is read letter by letter.
function railroadSystemLabel(system) {
  return system === "MNR" ? "Metro-North" : system || "Railroad";
}

// "Metro-North Hudson, train 8801, next stop Grand Central, scheduled position, no
// GPS". Built from the same FIELDS as the popup head, but NOT from formatRailroadHead
// itself: that helper joins with a middot, which is a visual separator doing a job
// that punctuation cannot do in speech (a screen reader reads it as noise, or as the
// words "middle dot"). Same facts, spoken shape. The GPS-versus-scheduled clause is
// here for the same reason it is in the popup: it tells a rider how much to trust the
// position they are being told about.
function railroadTrainName(train, routeName = null) {
  const t = train || {};
  const system = railroadSystemLabel(t.system);
  const head = routeName ? `${system} ${routeName}` : t.route_id ? `${system} route ${t.route_id}` : system;
  const placed = isPlacedRailroad(t);
  return joinName([
    head,
    t.train_num ? `train ${t.train_num}` : null,
    placed && t.stop_name ? `next stop ${t.stop_name}` : null,
    t.direction || null,
    placed ? "scheduled position, no GPS" : "live GPS",
  ]);
}

// "Newark - World Trade Center, PATH, next stop Grove St, to Newark". PATH trains are
// always scheduled positions, which the popup states and the name repeats.
function pathTrainName(train, routeName = null) {
  const t = train || {};
  return joinName([
    formatPathHead(t.route_id, routeName),
    "PATH",
    t.stop_name ? `next stop ${t.stop_name}` : null,
    t.direction || null,
    "scheduled position, no GPS",
  ]);
}

// "East River, NYC Ferry, boat H201, at dock". ferryStatusText is the popup's own
// wording. The boat label is the rider-visible hull name, not the feed's vehicle id.
function ferryBoatName(boat, routeName = null) {
  const b = boat || {};
  // ferryStatusText returns null for a status the feed did not give or we do not
  // recognise, and the popup omits its line entirely in that case. The name does the
  // same: saying "under way" about a boat whose status is unknown would be inventing
  // the one fact a rider is actually asking about.
  const status = ferryStatusText(b.status);
  return joinName([
    routeName || "Unassigned route",
    "NYC Ferry",
    b.label ? `boat ${b.label}` : null,
    status ? status.toLowerCase() : null,
  ]);
}

// "M15 bus, heading east" or "M15 bus, heading unknown". The bearing is spoken as a
// COMPASS POINT, not as degrees: the marker's whole visual job is the arrow, and "142
// degrees" is a number a rider has to convert while standing at a stop.
function busName(bus) {
  const b = bus || {};
  const route = b.route_id ? `${b.route_id} bus` : "Bus";
  return joinName([route, `heading ${compassPoint(b.bearing)}`]);
}

const COMPASS_POINTS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"];

// Degrees to one of eight compass points. Wraps, so 350 and -10 both read "north".
function compassPoint(bearing) {
  if (bearing == null || Number.isNaN(Number(bearing))) return "unknown";
  const step = 360 / COMPASS_POINTS.length;
  const index = Math.round(Number(bearing) / step);
  return COMPASS_POINTS[((index % COMPASS_POINTS.length) + COMPASS_POINTS.length) % COMPASS_POINTS.length];
}

// "Federal Circle, AirTrain JFK station". AirTrain stations are the one STATION with a
// DOM element to name (every other system's stations are circleMarkers drawn on a
// shared canvas, which has no element and therefore no place to put a name; those
// stations are reachable as text through the A1 panel instead).
function airtrainStationName(station) {
  const s = station || {};
  return joinName([s.name || "AirTrain station", "AirTrain JFK station"]);
}

/* ---------------- A2: when the page itself should speak ---------------- */

// The page-level equivalent of announcementWorthy, and it follows the same rule A1
// settled: judge a TRANSITION in underlying state, never a rendered string. The status
// line contains a clock and rewrites itself every fifteen seconds by construction, so
// anything comparing its text would announce forever.
//
// THE UNIT OF JUDGEMENT IS SET MEMBERSHIP, not a count and not a string. The identity
// is "<sourceKey>|<systemName>", exactly the key the C2 freshness index already uses,
// so what a rider hears is derived from the same numbers the status line and the
// marker dimming read. Counting would be wrong in a way that shows up precisely during
// a spreading incident: LIRR going stale while MNR recovers leaves the count at one
// and says nothing, when two things a rider cares about just changed.

// Which (source, system) identities are degraded right now. Accepts the freshness
// index as a Map or a plain object so callers and tests can pass either.
function degradedIdentities(freshnessIndex) {
  const entries =
    freshnessIndex instanceof Map
      ? [...freshnessIndex.entries()]
      : Object.entries(freshnessIndex || {});
  return entries
    .filter(([, entry]) => entry && (staleAge(entry.age) || neverDecoded(entry)))
    .map(([key]) => key)
    .sort();
}

// A system with NO age at all, which its own source reports as down. This is not a
// healthy system and it is not merely a stale one: it has never produced data.
//
// THE REVIEW FOUND THIS BY REPRODUCTION, and the failure was the worst shape available.
// A backend restart while a feed is still failing republishes that system with
// fetched_at null (the previous value it would have carried forward is gone with the
// process). A null age is not >= the staleness threshold, so the system silently LEFT
// the degraded set, and the page announced "Live data current again" at the exact
// moment its trains disappeared from the map. It then never re-entered the set, so the
// one surface that exists to say otherwise stayed quiet for as long as the outage
// lasted. A rider was told a dead system was fine, once, and never corrected.
//
// The status line never had this bug: staleness() has always separated a `blind` set
// (no age AND not ok) from the stale one. This makes the spoken judgment read the same
// two fields the visible one does, which is the invariant that matters: the page must
// not say one thing and speak another.
function neverDecoded(entry) {
  return entry.age == null && entry.ok === false;
}

// The rider-facing word for a source, and the word to use when a source's system is
// only the source over again. Railroads take no qualifier in front of a real system
// name, because "LIRR" and "Metro-North" are already what a rider calls them and
// "Railroad LIRR" is the kind of phrase only a schema would produce. But they still
// need a WHOLE word for the case below, and getting that wrong is not hypothetical:
// the first draft announced "Live data delayed for railroads", lowercase and plural,
// straight out of the payload key.
//
// AND THE RAILROAD SYSTEM NAME GOES THROUGH railroadSystemLabel, which the review found
// missing here. Every other spoken surface says "Metro-North"; this one said "MNR",
// straight out of the feed, into the only region a screen reader reads aloud. It is the
// same defect railroadSystemLabel was written to prevent, one surface later. "LIRR" is
// unchanged by that helper, because LIRR is what a rider calls it.
const SOURCE_WORDS = {
  buses: { qualifier: "Bus", whole: "Bus" },
  subways: { qualifier: "Subway", whole: "Subway" },
  railroads: { qualifier: null, whole: "Railroad", spoken: railroadSystemLabel },
  path: { qualifier: "PATH", whole: "PATH" },
  ferry: { qualifier: "Ferry", whole: "Ferry" },
};

function describeIdentity(identity) {
  const [source, system] = String(identity).split("|");
  const words = SOURCE_WORDS[source] || { qualifier: source, whole: source };
  // A source with no per-system block synthesizes ONE system named after itself
  // (ingestSystems), so "buses|buses" is just the buses and naming it twice would be
  // noise. The railroads payload does this whenever its systems block is absent, which
  // is the path that produced the defect above.
  if (system === source || !system) return words.whole;
  const spoken = words.spoken ? words.spoken(system) : system;
  return words.qualifier ? `${words.qualifier} ${spoken}` : spoken;
}

// Join names the way a person would say them.
function sentenceList(names) {
  if (names.length <= 1) return names[0] || "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

// The status announcement, or null for silence. `prev` of null is the FIRST
// OBSERVATION: it seeds state and says nothing, because a page load must not read its
// own condition aloud before the rider has asked for anything.
//
// Silent by construction on: age ticks (an identity already in the set stays in it as
// it gets older), re-renders, and any change that alters the formatted line without
// altering membership. Recovery is worth one sentence, because a rider who was told
// the data was delayed is owed the news that it is not.
function statusAnnouncement(prev, next) {
  if (!next) return null;
  if (!prev) return null;
  const before = new Set(prev);
  const after = new Set(next);
  const entered = next.filter((key) => !before.has(key));
  const left = prev.filter((key) => !after.has(key));
  if (!entered.length && !left.length) return null;
  const clauses = [];
  if (entered.length) clauses.push(`Live data delayed for ${sentenceList(entered.map(describeIdentity))}`);
  if (left.length) clauses.push(`Live data current again for ${sentenceList(left.map(describeIdentity))}`);
  return `${clauses.join(". ")}.`;
}

// The banner's announcement. The identity of an alert is its id AND a hash of its
// wording, which is the same content-hash approach the C1 banner dedup fix
// established after an alert whose text was revised in place under an unchanged id
// left the banner showing superseded wording indefinitely. So a reworded alert is a
// new identity here too, and is announced once.
//
// Deliberately NOT sensitive to: ordering (the set is compared, and the render key is
// built from a sorted list), a refresh carrying identical alerts, and the staleness
// marker. That last one matters: the "may be out of date" flag is visual honesty
// about the feed, not news about the transit system, and folding it in would announce
// every time the alerts feed crossed its threshold with nothing having happened.
function alertIdentities(alerts) {
  return (alerts || [])
    .map((a) => `${a.system}|${a.id}|${hashString(String(a.header ?? ""))}`)
    .sort();
}

function bannerAnnouncement(prev, next) {
  if (!next) return null;
  if (!prev) return null; // first observation seeds silently
  const before = new Set(prev);
  const appeared = next.filter((key) => !before.has(key));
  // An alert CLEARING is not announced: the rider is not told about the absence of an
  // emergency, and the strip disappearing is the signal. Only new or revised alerts
  // are worth interrupting for.
  if (!appeared.length) return null;
  // A SUMMARY, NEVER THE BODY. The banner and the alerts block carry the wording; a
  // live region that read a full service alert aloud would be unusable during exactly
  // the incident it exists for.
  return appeared.length === 1
    ? "New service alert."
    : `${appeared.length} new service alerts.`;
}

/* ---------------- A2: motion ---------------- */

// THE ONE MOTION GATE. Returns false when the rider has asked their system for
// reduced motion.
//
// THE PRINCIPLE, and it is the whole reason this is a gate and not a feature flag:
// reduced motion changes HOW a position updates, never WHAT is shown. A gliding train
// and a stepping train are at the same place at the same time; one interpolates
// between polls and the other jumps when the truth arrives. Nothing here may hide a
// marker, drop a poll, freeze data, or change any text. If a change would make the
// map say something different rather than move differently, it does not belong behind
// this gate.
//
// matchMedia is injected so the node tests can drive both answers, and so the callers
// that need to REACT to a change (see motionPreferenceListener) share one definition
// of the query with the callers that only read it once.
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function motionAllowed(mql = null) {
  const query = mql || (typeof matchMedia === "function" ? matchMedia(REDUCED_MOTION_QUERY) : null);
  if (!query) return true; // no matchMedia (node, ancient browser): animate as before
  return !query.matches;
}

// Watch the preference for CHANGES, so a rider who turns reduced motion on does not
// have to reload to be believed. Returns an unsubscribe function.
//
// WHAT THIS CANNOT REACH, and it is stated here rather than discovered later: Leaflet
// reads its zoomAnimation, fadeAnimation and markerZoomAnimation options ONCE, when the
// map is constructed, and offers no supported way to change them afterwards. So a
// mid-session flip takes effect immediately for everything this app owns (the marker
// glide, the css transitions, the panel) and only at the next page load for Leaflet's
// own zoom and pan animations. Poking at map.options after construction would leave the
// handlers Leaflet already installed running against a lie, which is a worse failure
// than the honest limitation. The README says the same thing in a rider's words, since
// the person affected is a user rather than a maintainer.
//
// addEventListener is guarded because MediaQueryList only grew it in Safari 14; the
// older addListener is deliberately NOT used as a fallback, because a browser that old
// predates the app's other requirements anyway and a silent no-op is better than a
// deprecated path nobody tests.
function watchMotionPreference(onChange, mql = null) {
  const query = mql || (typeof matchMedia === "function" ? matchMedia(REDUCED_MOTION_QUERY) : null);
  if (!query || typeof query.addEventListener !== "function") return () => {};
  const handler = () => onChange(!query.matches);
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

/* A4: VANISHING FOCUS, the decision half.

   WHAT VANISHES AND WHY IT IS THE POPUP. A rider's focus can never be inside a marker
   element: the factory builds every marker with keyboard:false, so there is no tabindex
   and no tab stop, and A2 pinned that as the marker exclusion policy. Popups, though,
   live in Leaflet's popupPane as a SEPARATE subtree, and every popup contains at least
   Leaflet's own close button. So the thing that can be destroyed under a rider's fingers
   is the popup, and the same is true of the alert banner's dismiss button.

   THE PREDICATE IS THE ONE THE POPUP-REFRESH FIX PROVED OUT: did the subtree that is
   about to be destroyed contain document.activeElement? Not "is a popup open", not "did
   a vehicle leave" - the question is only ever whether the rider was holding something
   that is going away. That is what makes this silent in the common case: a layer toggle
   destroys every marker in a group, but the rider's focus is on the checkbox they just
   activated, so the predicate is false and nothing is said. The announcement is earned by
   a TRANSITION in the rider's own state, which is the same worthiness rule the live
   regions have followed since A1.

   Kept pure and here so node can test it without a DOM: the caller passes the subtree and
   the currently focused element, and gets back the decision plus the wording. */
function vanishingFocusPlan(subtree, active, { label = null, kind = "vehicle" } = {}) {
  if (!subtree || !active) return { rescue: false, message: null };
  const inside = subtree === active || (typeof subtree.contains === "function" && subtree.contains(active));
  if (!inside) return { rescue: false, message: null };
  return { rescue: true, message: vanishingFocusMessage(kind, label) };
}

/* The wording. The decisions block gave "The train you were following left the feed" and
   "Alerts cleared"; the vehicle half is built from the marker's OWN accessible name
   rather than from a hardcoded noun, because this app carries buses, boats and PATH
   trains as well as subway trains and a fixed "train" would be false for most of them.
   The name's leading clause is exactly the vehicle identity ("1 train", "M15 bus",
   "Rockaway ferry"), because that is how buildMarkerName composes it.

   Both sentences name where focus went. That is not decoration: a rider who was reading a
   popup and is silently moved somewhere else has lost their place, and "focus moved to
   the map" is the one piece of orientation that makes the move recoverable. */
function vanishingFocusMessage(kind, label) {
  if (kind === "alerts") return "Alerts cleared. Focus moved to the map.";
  const lead = typeof label === "string" && label.trim() ? label.split(",")[0].trim() : null;
  return lead
    ? `The ${lead} you were following left the feed. Focus moved to the map.`
    : "The vehicle you were following left the feed. Focus moved to the map.";
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    vanishingFocusPlan,
    vanishingFocusMessage,
    esc, routeColor, lineColor, staleness, emptyFeedDecision, noteClockOffset,
    formatCountdown, trainLatLng, polylineCumLengths, pointAtArcLength, projectOntoRoute,
    computeRouteSlice, railroadColor, isPlacedRailroad, orderedRailroadBuckets,
    railroadArrivalsHtml, formatRailroadHead, ROUTE_ACCEPT_DIST, ROUTE_MAX_SLICE,
    indexAlerts, matchStationAlerts, matchRouteAlerts, bannerAlerts, alertsBlockHtml,
    hashString, bannerRenderKey,
    RAILROAD_ROUTE_MAX_SLICE, RAILROAD_ROUTE_ACCEPT_DIST, RAILROAD_BUCKET_ORDER,
    LINE_COLORS, FEED_STALE_AFTER_S, FETCH_DEADLINE_MS, shouldRefresh,
    // A3: one luminance path for the whole app.
    parseColor, relativeLuminance, contrastRatio, readableTextOn, readableInk, statusLineText,
    MOBILE_MAX_WIDTH_PX, MOBILE_QUERY, narrowViewport,
    INK_LIGHT, INK_DARK,
    feedAgeLine, humanizeAge, alertsStale, alertsFreshnessBasis, ALERTS_STALE_AFTER_S,
    ingestSystems, systemAges, systemStaleAts, staleAge, markerOpacity, glideClock,
    thresholdOverrides, CONTRACT_FLAG_PARAM,
    stalePopupLine, STALE_MARKER_OPACITY, FERRY_DOCKED_OPACITY,
    selectHeadwayBand, airtrainStationPopupHtml, retryUntil,
    PATH_BUCKET_ORDER, PATH_FALLBACK_COLOR, orderedPathBuckets, pathColor,
    formatPathHead, pathTrainPopupHtml, pathArrivalsHtml,
    PATH_ROUTE_MAX_SLICE, PATH_ROUTE_ACCEPT_DIST, computePathRouteSlice,
    FERRY_FALLBACK_COLOR, orderedFerryBuckets, ferryArrivalDisplay, ferryBoatIconState,
    ferryStatusText, ferrySpeedKnots, ferryBoatPopupHtml, ferryArrivalsHtml,
    // A1: the accessible station surface.
    countdownParts, spokenCountdown, STATION_RESULT_CAP, SUBWAY_BUCKET_ORDER,
    foldStationName, stationQueryTokens, stationMatchesTokens, searchStations,
    stationOverflowLine, shapeStationArrivals, arrivalSentence, clockTimeLabel,
    ANNOUNCE_LEAD_SHIFT_S, arrivalsSignature, announcementWorthy,
    // A2: map semantics and the interaction floor.
    joinName, subwayTrainName, railroadTrainName, pathTrainName, ferryBoatName,
    busName, compassPoint, airtrainStationName, COMPASS_POINTS, railroadSystemLabel,
    degradedIdentities, neverDecoded, describeIdentity, sentenceList, statusAnnouncement,
    alertIdentities, bannerAnnouncement,
    motionAllowed, watchMotionPreference, REDUCED_MOTION_QUERY,
    // A4: the popup-clearing geometry.
    boxesOverlap, shiftBox, popupClearingShift, POPUP_CLEAR_GAP,
  };
}
