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

// Staleness threshold, mirroring the backend FEED_STALE_AFTER_S.
const FEED_STALE_AFTER_S = 90;

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

// v1 train position: straight-line interpolation from the previous station to
// the next/current station, parameterized by time. `now` is skew-corrected
// epoch seconds. Falls back to the static next-station position
// [latitude, longitude] when the anchors are missing or non-monotonic. (v2 will
// follow the route polyline — keep that swap localized to this function.)
function trainLatLng(train, now) {
  const { prev_lat, prev_lon, prev_time, next_time, latitude, longitude } = train;
  if (prev_lat == null || prev_time == null || next_time == null || next_time <= prev_time) {
    return [latitude, longitude];
  }
  const f = Math.min(1, Math.max(0, (now - prev_time) / (next_time - prev_time)));
  return [prev_lat + (latitude - prev_lat) * f, prev_lon + (longitude - prev_lon) * f];
}

// Arrival countdown label from a seconds-until-arrival delta: "now" when due
// (or past), else rounded to whole minutes.
function formatCountdown(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "";
  if (seconds < 30) return "now";
  return `${Math.round(seconds / 60)} min`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    esc, routeColor, lineColor, staleness, noteClockOffset, formatCountdown,
    trainLatLng, LINE_COLORS, DARK_TEXT_LINES, FEED_STALE_AFTER_S,
  };
}
