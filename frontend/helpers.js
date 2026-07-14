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
// Yellow squares need dark text for contrast.
const DARK_TEXT_LINES = new Set(["N", "Q", "R", "W"]);

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

function railroadColor(routeId) {
  if (!routeId) return "#607d8b";
  let h = 0;
  for (const c of routeId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return RAILROAD_COLORS[h % RAILROAD_COLORS.length];
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

// Staleness threshold, mirroring the backend FEED_STALE_AFTER_S.
const FEED_STALE_AFTER_S = 90;

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

// minClockOffset = the minimum observed (clientNow - fetched_at), approximating
// browser-vs-server skew plus minimal latency. Used to skew-correct the
// arrivals countdown (map.js, which compares absolute MTA timestamps to the
// browser clock) and the poll-age term of staleness() below.
let minClockOffset = null;

function noteClockOffset(fetchedAt) {
  if (fetchedAt == null) return;
  const offset = Date.now() / 1000 - fetchedAt;
  if (minClockOffset == null || offset < minClockOffset) minClockOffset = offset;
}

// Two independent staleness signals, flag if EITHER crosses the threshold:
//   1. upstream lag = fetched_at - feed_timestamp — both server-recorded, so
//      this is clock-skew free; detects the MTA feed itself going stale.
//   2. poll age = now - fetched_at (skew-corrected via minClockOffset) — detects
//      OUR backend having stopped polling, where it keeps serving frozen
//      last-good data so the upstream-lag term alone would stay constant and
//      silent. `now` is injected for testability (defaults to the wall clock).
function staleness(source, now = Date.now() / 1000) {
  if (source.fetchedAt == null) return null;
  const upstreamLag =
    source.feedTimestamp == null ? 0 : source.fetchedAt - source.feedTimestamp;
  const pollAge = now - source.fetchedAt - (minClockOffset ?? 0);
  const age = Math.max(upstreamLag, pollAge, 0);
  if (age < FEED_STALE_AFTER_S) return null;
  const human = age < 120 ? `${Math.round(age)}s` : `${Math.round(age / 60)}m`;
  return `${source.label} data ${human} old`;
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

// Arrival countdown label from a seconds-until-arrival delta: "now" when due
// (or past), else rounded to whole minutes.
function formatCountdown(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "";
  if (seconds < 30) return "now";
  const mins = Math.round(seconds / 60);
  if (mins < 100) return `${mins} min`;
  // Hours tier for the long railroad branch-end horizons (e.g. 6000s -> "1 h 40
  // min"). This is the only change from the minutes-only version and only fires
  // at 100+ minutes, which subway countdowns effectively never reach.
  return `${Math.floor(mins / 60)} h ${mins % 60} min`;
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
    `<span class="popup-sub">${esc(station.system ?? "")}</span>`;
  const buckets = orderedRailroadBuckets(body.directions);
  if (!buckets.length) return `${header}<div class="arr-none">No trains</div>`;
  let html = header;
  for (const [dir, arrivals] of buckets) {
    html += `<div class="arr-dir">${esc(dir)}</div>`;
    html += arrivals
      .map((a) => {
        const route = a.route_id ?? "";
        const badge =
          `<span class="arr-badge" style="background:${railroadColor(route)};color:#fff">` +
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
    `<b style="color:${color}">${esc(formatPathHead(train.route_id, name))}</b>` +
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
    `<span class="popup-sub">PATH</span>`;
  const buckets = orderedPathBuckets(body.directions);
  if (!buckets.length) return `${header}<div class="arr-none">No trains</div>`;
  let html = header;
  for (const [dir, arrivals] of buckets) {
    html += `<div class="arr-dir">${esc(dir)}</div>`;
    html += arrivals
      .map((a) => {
        const route = a.route_id ?? "";
        const badge =
          `<span class="arr-badge" style="background:${colorFor(a.route_id)};color:#fff">` +
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
    `<b style="color:${color}">${esc(routeText)}</b>` +
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
    `<span class="popup-sub">NYC Ferry</span>${access}`;
  const buckets = orderedFerryBuckets(body.routes);
  if (!buckets.length) return `${header}<div class="arr-none">No boats</div>`;
  let html = header;
  for (const [routeName, rows] of buckets) {
    const color = rows[0] && rows[0].route_id ? colorFor(rows[0].route_id) : FERRY_FALLBACK_COLOR;
    html += `<div class="arr-dir" style="color:${color}">${esc(routeName)}</div>`;
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

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    esc, routeColor, lineColor, staleness, emptyFeedDecision, noteClockOffset,
    formatCountdown, trainLatLng, polylineCumLengths, pointAtArcLength, projectOntoRoute,
    computeRouteSlice, railroadColor, isPlacedRailroad, orderedRailroadBuckets,
    railroadArrivalsHtml, formatRailroadHead, ROUTE_ACCEPT_DIST, ROUTE_MAX_SLICE,
    indexAlerts, matchStationAlerts, matchRouteAlerts, bannerAlerts, alertsBlockHtml,
    RAILROAD_ROUTE_MAX_SLICE, RAILROAD_ROUTE_ACCEPT_DIST, RAILROAD_BUCKET_ORDER,
    LINE_COLORS, DARK_TEXT_LINES, FEED_STALE_AFTER_S,
    selectHeadwayBand, airtrainStationPopupHtml, retryUntil,
    PATH_BUCKET_ORDER, PATH_FALLBACK_COLOR, orderedPathBuckets, pathColor,
    formatPathHead, pathTrainPopupHtml, pathArrivalsHtml,
    PATH_ROUTE_MAX_SLICE, PATH_ROUTE_ACCEPT_DIST, computePathRouteSlice,
    FERRY_FALLBACK_COLOR, orderedFerryBuckets, ferryArrivalDisplay, ferryBoatIconState,
    ferryStatusText, ferrySpeedKnots, ferryBoatPopupHtml, ferryArrivalsHtml,
  };
}
