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
// servedAt: the response's build time (R1), fed to noteClockOffset for a clean
// skew baseline and to staleness() for the server cache-age term. Distinct from
// fetchedAt (the backend's last poll) precisely so a stuck poller shows up.
// inFlight (R2): true while this source's own refresh is running, so refreshAll
// skips a source already in flight instead of stacking a second fetch. It replaces
// the old whole-cycle `refreshing` lock: each source is now gated independently, so
// one slow source (bounded by AbortSignal.timeout) cannot freeze the others.
const sources = {
  buses: { url: "/api/buses", apply: applyBuses, label: "buses", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, emptyRunStart: null, inFlight: false },
  subways: { url: "/api/subways", apply: applyTrains, label: "trains", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, emptyRunStart: null, inFlight: false },
  railroads: { url: "/api/railroads", apply: applyRailroads, label: "railroad", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, emptyRunStart: null, inFlight: false },
  path: { url: "/api/path", apply: applyPath, label: "PATH", dataKey: "trains", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, emptyRunStart: null, inFlight: false },
  // Ferry boats carry the `boats` envelope key, and clearOnEmpty flips the empty
  // handling: a successful empty poll REPLACES the boats immediately (see the
  // refreshSource branch) rather than riding out the transient-blip grace the
  // other feeds use, preserving 14b's empty-replaces / failure-retains split.
  ferry: { url: "/api/ferry", apply: applyFerryBoats, label: "ferries", dataKey: "boats", clearOnEmpty: true, count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, emptyRunStart: null, inFlight: false },
};

async function refreshSource(source) {
  source.inFlight = true;
  try {
    // AbortSignal.timeout bounds the WHOLE fetch (the browser fetch has no built-in
    // whole-request timeout, so a trickling upstream would otherwise hang forever).
    // A timeout aborts the request and rejects into the catch below like any other
    // failed poll: last-known markers stay, the R1 staleness surfaces do the rest.
    const res = await fetch(source.url, { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail ?? `HTTP ${res.status}`);
    }
    const body = await res.json();
    source.fetchedAt = body.fetched_at ?? null;
    source.feedTimestamp = body.feed_timestamp ?? null; // server-side staleness signal
    source.servedAt = body.served_at ?? null; // this response's build time (R1)
    // Calibrate the skew baseline off served_at, NOT fetched_at: served_at is the
    // instant the response left the server, so (clientNow - served_at) is skew plus
    // latency only. Using fetched_at folded in the server cache age, which cancelled
    // the staleness signal and shifted every countdown (the audit finding).
    noteClockOffset(source.servedAt);
    const data = body[source.dataKey ?? "data"] ?? [];
    if (data.length === 0 && source.clearOnEmpty) {
      // Ferry: the backend serves an empty 200 ONLY when it successfully decoded
      // zero boats (overnight, the boats went home); a transient upstream problem
      // is a FAILED poll instead, which the catch below keeps last-known. So there
      // is no blip to ride out and no ghost-boats risk: apply the empty set
      // immediately (applyFerryBoats' sweep clears the markers). This is the one
      // deliberate divergence from the other feeds' transient grace, mirroring the
      // server-side empty-replaces / failure-retains split 14b implements. An empty
      // ferry poll is a NORMAL nightly state, so it records no error.
      source.apply([]);
      source.count = 0;
      source.error = null;
      source.emptyRunStart = null;
      return;
    }
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
    // neither starts nor advances the empty run (emptyRunStart is left as is). An
    // AbortSignal.timeout rejection (we cut off a wedged fetch at FETCH_DEADLINE_MS)
    // arrives here like any other failure; map its engine-specific DOMException
    // wording ("signal timed out" in Chromium) to a stable, plain "timed out" so the
    // status line reads the same across browsers. No new error state: a timed-out
    // fetch is just a failed poll.
    source.error = err.name === "TimeoutError" ? "timed out" : err.message;
  } finally {
    // Cleared here (not per-return) because the success path and both empty
    // branches return early out of the try: finally is the one place that always
    // runs, so the source is reliably freed for the next tick's shouldRefresh check.
    source.inFlight = false;
  }
}

async function refreshAll() {
  // No global lock (R2): the old `refreshing` flag gated the whole cycle, so a
  // single wedged fetch that never resolved kept it true forever and every later
  // tick early-returned, freezing the map. Now each source is gated on its own
  // inFlight flag (shouldRefresh): fire a refresh for every source NOT already in
  // flight, and leave the ones still running to be picked up on a later tick once
  // they settle or hit their AbortSignal.timeout. We await only the sources fired
  // THIS tick so the status tail below observes their settled state; this await
  // gates just this invocation's tail, never the next tick (a separate call gated
  // per-source), so an overlapping slow tick can no longer starve the loop.
  const fired = Object.values(sources).filter(shouldRefresh);
  await Promise.all(fired.map(refreshSource));
  const counts = Object.values(sources)
    .map((s) => `${s.count.toLocaleString()} ${s.label}`)
    .join(" · ");
  const problems = Object.values(sources)
    .filter((s) => s.error)
    .map((s) => `${s.label}: ${s.error}`)
    // Wrap in an arrow so staleness gets its default now = Date.now()/1000: a bare
    // .map(staleness) would pass the array INDEX as the `now` argument (the
    // .map(parseInt) footgun), leaving the client-elapsed term and the served_at-
    // absent fallback branch reading a nonsense clock.
    .concat(Object.values(sources).map((s) => staleness(s)).filter(Boolean));
  const now = new Date().toLocaleTimeString();
  if (problems.length) setStatus(`${counts} · ${now} — ${problems.join("; ")}`, true);
  else setStatus(`${counts} · updated ${now}`);

  // Refresh whichever station popup is open (subway or railroad) so the train
  // list (not just the countdowns) stays current on the same ~15s cadence as the
  // markers. openStationArrivals reads the open descriptor, so it is kind-agnostic.
  if (openStation) openStationArrivals({ refresh: true });

  // Re-render the alert banner so its "may be out of date" marker (R1) appears or
  // clears as the alerts feed crosses ALERTS_STALE_AFTER_S even while its own 60s
  // poll is failing (loadAlerts re-renders only on success). A no-op until the
  // stale flag flips, via the banner's dedup key.
  tickAlertBanner();
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
retryUntil(loadFerryRoutes, staticRetryOpts);
retryUntil(loadFerryStops, staticRetryOpts);
loadAlerts();
refreshAll();
setInterval(refreshAll, POLL_INTERVAL_MS);
setInterval(loadAlerts, ALERT_POLL_INTERVAL_MS);
requestAnimationFrame(animateTrains); // glide trains between polls
