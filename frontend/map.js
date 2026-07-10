// Entry point for the ordered-script frontend, loaded LAST (after helpers.js,
// systems/shared.js and every systems/<mode>.js), so every marker/layer/apply
// function it wires below is already defined in the shared global scope. Holds
// the poll cadence constants, the feed poll loop, and the startup kickoff (static
// loaders, first poll, intervals, and the animation frame).

const POLL_INTERVAL_MS = 15000;
// Service alerts change far slower than positions, so they poll on their own loop
// at the backend cadence (60s). Alerts are decorative: a failed fetch keeps the
// last-known set silently and never blocks or delays the arrivals popups.
const ALERT_POLL_INTERVAL_MS = 60000;
// Static loaders (route lines, station dots, AirTrain) retry with doubling backoff
// until they populate, so a visitor who lands during a backend cold start gets a
// map that fills in by itself once the static GTFS warms (see the retryUntil calls
// at the bottom). 1s catches a fast warmup quickly; 30s is the idle hum ceiling.
const STATIC_RETRY_BASE_MS = 1000;
const STATIC_RETRY_CAP_MS = 30000;


/* ---------------- Polling ---------------- */

// emptyRunStart: fetched_at of the first empty poll in the current empty run
// (null when the last poll carried data); drives emptyFeedDecision's time bound.
// path's dataKey: its envelope carries `trains` where the MTA feeds carry `data`
// (the backend keeps the shared warming contract under a different key).
const sources = {
  buses: { url: "/api/buses", apply: applyBuses, label: "buses", count: 0, error: null, fetchedAt: null, feedTimestamp: null, emptyRunStart: null },
  subways: { url: "/api/subways", apply: applyTrains, label: "trains", count: 0, error: null, fetchedAt: null, feedTimestamp: null, emptyRunStart: null },
  railroads: { url: "/api/railroads", apply: applyRailroads, label: "railroad", count: 0, error: null, fetchedAt: null, feedTimestamp: null, emptyRunStart: null },
  path: { url: "/api/path", apply: applyPath, label: "PATH", dataKey: "trains", count: 0, error: null, fetchedAt: null, feedTimestamp: null, emptyRunStart: null },
};

async function refreshSource(source) {
  try {
    const res = await fetch(source.url);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail ?? `HTTP ${res.status}`);
    }
    const body = await res.json();
    source.fetchedAt = body.fetched_at ?? null;
    source.feedTimestamp = body.feed_timestamp ?? null; // server-side staleness signal
    noteClockOffset(source.fetchedAt); // skew baseline for the arrivals countdown
    const data = body[source.dataKey ?? "data"] ?? [];
    if (data.length === 0) {
      // Empty successful poll. Keep last-known markers only while the empty run is
      // TRANSIENT (a blip); once it has lasted FEED_STALE_AFTER_S by server
      // fetched_at, apply the empty set so the unseen-marker sweeps clear the layer
      // rather than leaving ghost markers frozen at stale positions forever.
      const decision = emptyFeedDecision(source.emptyRunStart, source.fetchedAt);
      source.emptyRunStart = decision.emptyRunStart;
      source.error = decision.error;
      if (decision.applyEmpty) {
        source.apply([]); // seen-set sweep removes every marker
        source.count = 0;
      }
      return;
    }
    source.apply(data);
    source.count = data.length;
    source.error = null;
    source.emptyRunStart = null; // a non-empty poll ends the empty run
  } catch (err) {
    // Keep last known markers on screen; just surface the problem. A failed poll
    // neither starts nor advances the empty run (emptyRunStart is left as is).
    source.error = err.message;
  }
}

let refreshing = false; // don't let a slow poll overlap the next tick

async function refreshAll() {
  if (refreshing) return;
  refreshing = true;
  try {
    await Promise.all(Object.values(sources).map(refreshSource));
  } finally {
    refreshing = false;
  }
  const counts = Object.values(sources)
    .map((s) => `${s.count.toLocaleString()} ${s.label}`)
    .join(" · ");
  const problems = Object.values(sources)
    .filter((s) => s.error)
    .map((s) => `${s.label}: ${s.error}`)
    .concat(Object.values(sources).map(staleness).filter(Boolean));
  const now = new Date().toLocaleTimeString();
  if (problems.length) setStatus(`${counts} · ${now} — ${problems.join("; ")}`, true);
  else setStatus(`${counts} · updated ${now}`);

  // Refresh whichever station popup is open (subway or railroad) so the train
  // list (not just the countdowns) stays current on the same ~15s cadence as the
  // markers. openStationArrivals reads the open descriptor, so it is kind-agnostic.
  if (openStation) openStationArrivals({ refresh: true });
}

// Static loaders retry until they populate, so a visitor who lands during a
// backend cold start (warming 503s, or a failed warmup serving [] no-cache) gets
// a map that fills in on its own once the backend heals; each loader stops for
// good after its first successful populate. Live-data polling (refreshAll,
// loadAlerts) is untouched: it already self-heals on its own intervals.
const staticRetryOpts = { baseMs: STATIC_RETRY_BASE_MS, capMs: STATIC_RETRY_CAP_MS };
retryUntil(loadRouteLines, staticRetryOpts);
retryUntil(loadRailroadRoutes, staticRetryOpts);
retryUntil(loadStations, staticRetryOpts);
retryUntil(loadRailroadStations, staticRetryOpts);
retryUntil(loadAirtrain, staticRetryOpts);
retryUntil(loadPathRoutes, staticRetryOpts);
retryUntil(loadPathStops, staticRetryOpts);
loadAlerts();
refreshAll();
setInterval(refreshAll, POLL_INTERVAL_MS);
setInterval(loadAlerts, ALERT_POLL_INTERVAL_MS);
requestAnimationFrame(animateTrains); // glide trains between polls
