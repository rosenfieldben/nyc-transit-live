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

// Upstream-staleness threshold, mirroring the backend FEED_STALE_AFTER_S: how
// far the feed CONTENT time may lag the poll time before we flag it.
const FEED_STALE_AFTER_S = 90;

// fetched_at and feed_timestamp are BOTH the server's recorded values (poll
// time and the feed's content time). Their difference is the feed's staleness,
// measured entirely server-side — so the browser clock never enters the
// staleness check and clock skew can't cause false warnings. Returns null when
// it can't be computed (no successful fetch, or the feed omits its timestamp).
function staleness(source) {
  if (source.fetchedAt == null || source.feedTimestamp == null) return null;
  const age = source.fetchedAt - source.feedTimestamp;
  if (age < FEED_STALE_AFTER_S) return null;
  const human = age < 120 ? `${Math.round(age)}s` : `${Math.round(age / 60)}m`;
  return `${source.label} data ${human} old`;
}

// The clock-offset estimate is still needed by the station arrivals countdown
// (map.js), which compares absolute MTA arrival timestamps to the browser
// clock — that genuinely needs skew correction. The staleness check above does
// not. minClockOffset = the minimum observed (clientNow - fetched_at), which
// approximates skew plus minimal latency.
let minClockOffset = null;

function noteClockOffset(fetchedAt) {
  if (fetchedAt == null) return;
  const offset = Date.now() / 1000 - fetchedAt;
  if (minClockOffset == null || offset < minClockOffset) minClockOffset = offset;
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
    LINE_COLORS, DARK_TEXT_LINES, FEED_STALE_AFTER_S,
  };
}
