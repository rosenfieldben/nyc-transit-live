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
// systems (C2): the per-system freshness blocks ingested from the payload, or a
// synthesized single system for the single-feed sources (see ingestSystems). This is
// the ONE place a payload's block is read, so every surface that judges freshness
// (status line, marker dimming, popup age lines, glide freeze) reads the same map.
// systemNoun is the word that makes a system name read naturally in the status line:
// the subway's systems are feed GROUPS ("ACE group as of 4m ago"), while a railroad
// system is just itself ("MNR as of 6m ago"), so only the subway sets it.
// onSystems is an optional per-source hook run when a block lands, used by the subway
// to invert the payload's route coverage into its route -> group lookup once per poll
// rather than once per marker.
const sources = {
  buses: { url: "/api/buses", apply: applyBuses, label: "buses", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, systems: null, emptyRunStart: null, inFlight: false },
  subways: { url: "/api/subways", apply: applyTrains, label: "trains", systemNoun: "group", onSystems: noteSubwaySystems, count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, systems: null, emptyRunStart: null, inFlight: false },
  railroads: { url: "/api/railroads", apply: applyRailroads, label: "railroad", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, systems: null, emptyRunStart: null, inFlight: false },
  path: { url: "/api/path", apply: applyPath, label: "PATH", dataKey: "trains", count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, systems: null, emptyRunStart: null, inFlight: false },
  // Ferry boats carry the `boats` envelope key, and clearOnEmpty flips the empty
  // handling: a successful empty poll REPLACES the boats immediately (see the
  // refreshSource branch) rather than riding out the transient-blip grace the
  // other feeds use, preserving 14b's empty-replaces / failure-retains split.
  ferry: { url: "/api/ferry", apply: applyFerryBoats, label: "ferries", dataKey: "boats", clearOnEmpty: true, count: 0, error: null, fetchedAt: null, feedTimestamp: null, servedAt: null, systems: null, emptyRunStart: null, inFlight: false },
};

// Which key in `sources` a descriptor is, for the "<sourceKey>|<system>" freshness
// index. Derived once rather than duplicated as a field on every row.
const sourceKeys = new Map(Object.entries(sources).map(([key, source]) => [source, key]));

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
    // The per-system freshness blocks (C2), or one synthesized system for a
    // single-feed source, so everything downstream reads one shape. Ingested BEFORE
    // apply() below, because the apply paths dim and freeze from these ages and a
    // marker created this poll has to be dim on its first frame.
    source.systems = ingestSystems(body, sourceKeys.get(source));
    if (source.onSystems) source.onSystems(source.systems);
    // Rebuild the age index NOW, before apply() runs: the apply paths read it to dim
    // and to freeze the glide, and a marker created from retained data has to be dim
    // on the very first frame it exists. The sweep over EXISTING markers is left to
    // the caller's tail / the animation tick, which is where a change in the stale
    // set is detected.
    refreshSystemFreshness();
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
  // If EVERY source is still in flight (fired is empty, e.g. a backend-wide slowdown
  // has all five fetches trickling toward their deadline at once), there is no new
  // poll result this tick, so skip the whole tail: repainting would either paint a
  // false-green "updated <now>" over a hung map (no error is recorded until the
  // in-flight fetches actually abort) or fire a duplicate popup refresh. The in-flight
  // fetches hit their AbortSignal.timeout, record their errors, and a later tick whose
  // fired set is non-empty (once they free up) repaints the honest state.
  if (!fired.length) return;
  await Promise.all(fired.map(refreshSource));
  // Re-dim every marker from the ages this tick's responses produced (C2). Runs
  // unconditionally rather than only when the stale set changed, because a poll can
  // also have ADDED markers to an already-stale system through a path that did not
  // create them (a route relabel moving a train between groups).
  refreshSystemFreshness();
  applyStaleTreatment();
  // A2: the page's live region, from the same index the dimming just used. Judged on
  // degraded-set membership in helpers.js, so a poll that only makes an already-stale
  // system older says nothing.
  announceStatusTransition(systemFreshnessIndex);
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
  // A3: composition moved into statusLineText so the order and the never-truncate rule
  // are stated once and tested, rather than living in a template literal here. compact
  // drops the clock's seconds on a narrow screen; see the rule beside the helper.
  setStatus(statusLineText({ counts, clock: now, problems }, { compact: narrowViewport() }), problems.length > 0);

  // Refresh whichever station popup is open (subway or railroad) so the train
  // list (not just the countdowns) stays current on the same ~15s cadence as the
  // markers. openStationArrivals reads the open descriptor, so it is kind-agnostic.
  if (openStation) openStationArrivals({ refresh: true });
  // The station panel refreshes on the SAME cadence, so the popup and the panel
  // never show arrivals of different ages for the same station (A1).
  refreshPanelArrivals();

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

/* A4: THE ESCAPE LADDER, and it is the page's ONLY Escape handler.

   WHAT REPLACED WHAT. Before this, Escape was a focus-location switch rather than a
   ladder, and the two behaviours lived in different files: stations.js bound a handler on
   the panel (focus inside the panel closed the PANEL and left an open popup alone), while
   Leaflet's own Map.Keyboard handler closed the POPUP but only while document.activeElement
   was the #map container itself, so focus in a popup, on the toggle or on the banner did
   nothing at all. Measured across 22 presses at 375 and 1280, identical at both widths.
   Which surface closed depended on where the rider happened to be standing, which is not
   a rule anyone can learn.

   THE RULE: THE RIDER'S OWN SURFACE FIRST, then the topmost transient.

     focus inside a transient  ->  that transient closes
     focus anywhere else       ->  open popup first, then the panel

   The banner is on neither branch: it is ambient status rather than a dialog, and its
   dismiss button stays the deliberate act, per the phase decision.

   WHY NOT LITERAL POPUP-FIRST FROM EVERYWHERE, which is what the phase decision said
   before this was measured. Selecting a station in the PANEL opens that station's popup on
   the map: A1's syncMapToStation does it on purpose, so a sighted keyboard rider sees one
   application rather than two. That means a rider using the panel always has a popup open,
   and a literal popup-first rung would close that popup while they are looking at the
   list. Measured: it broke A1a, A1b, A1m and A1q, all of which assert that one Escape
   closes the panel and returns focus to the toggle. At 375 it is worse than a test
   failure, because the popup is behind the opaque overlay, so the rider's first Escape
   closes something they cannot see and appears to do nothing at all.

   So the rung is judged relative to the rider. It is still one rule in one place, it is
   still learnable ("Escape closes what you are in"), and it leaves the A1 contract exactly
   as A1 shipped it rather than silently editing it from another deliverable.

   CAPTURE PHASE, for a smaller reason than the first draft of this comment claimed.
   Leaflet has its own Escape-closes-popup handler that acts only while the map container
   holds focus, so the worry was a race: if Leaflet ran first it would close the popup, and
   a bubble-phase ladder would then find no popup and take the next rung, closing the panel
   too. One Escape, two surfaces.

   MEASURED, THAT DOES NOT HAPPEN. With the ladder moved to the bubble phase, a popup open
   and focus on #map, the event propagates untouched through the container and on to the
   document (instrumented: doc-capture, container-bubble and a late doc-bubble listener all
   fire), the ladder closes the popup itself, and the panel stays open. Leaflet's handler
   did not act at all in that state. So capture is not what prevents a double close, and
   saying otherwise would leave a future reader defending an invariant nothing depends on.

   What capture actually buys is that the ladder's decision is not CONTINGENT on another
   handler's behaviour: it decides first, always, and stops the event once it has acted.
   That is worth keeping as a deliberate property rather than relying on Leaflet continuing
   to decline. frontend/keyboard.test.js pins the flag, because it is a one-word edit.

   AND WHEN THERE IS NOTHING TRANSIENT OPEN, this handler does nothing at all: no
   preventDefault, no stopPropagation. The event proceeds exactly as it did before this
   phase, reaching Leaflet's handler, which finds no popup and returns without touching
   the event. That is what keeps the native path intact rather than swallowed. */
function openPopupOnMap() {
  // ASKED OF LEAFLET, WITH THE STALE HALF GUARDED. map._popup is the same field Leaflet's
  // own handler consults, but it is NOT cleared on close: measured, it still holds the
  // reference after the popup has closed and even after the fade has removed the element.
  // map.hasLayer is the public and truthful half, and it was measured across all five
  // states (fresh, open, closed under a paused clock with the corpse still in the DOM,
  // closed after the fade, and a station popup open): false, true, false, false, true.
  // A DOM query would have answered 1 in the third state, which is the fade-corpse trap
  // tests/e2e/popup.js exists for, one layer down.
  const popup = map._popup;
  return popup && map.hasLayer(popup) ? popup : null;
}

// Which surface, if any, the rider is standing in. Asked of the DOM rather than remembered,
// so there is no third copy of either surface's state to drift out of date.
function transientHoldingFocus() {
  const active = document.activeElement;
  if (!active) return null;
  const popup = openPopupOnMap();
  const popupEl = popup && popup.getElement ? popup.getElement() : null;
  if (popupEl && popupEl.contains(active)) return "popup";
  const panel = document.getElementById("stations-panel");
  if (panel && !panel.hidden && panel.contains(active)) return "panel";
  return null;
}

function closeStationsPanelIfOpen() {
  if (typeof stationsPanelOpen !== "function" || !stationsPanelOpen()) return false;
  closeStationsPanel(); // owns the A1 focus return and the A4 inertness release
  return true;
}

function closeOpenPopup() {
  const popup = openPopupOnMap();
  if (!popup) return false;
  // Through the shared helper rather than map.closePopup, so the rung that closes the popup
  // the rider is standing in also puts them somewhere. See closePopupReturningFocus.
  return closePopupReturningFocus(popup);
}

document.addEventListener(
  "keydown",
  (event) => {
    if (event.key !== "Escape") return;
    const inside = transientHoldingFocus();
    const closed =
      inside === "panel"
        ? closeStationsPanelIfOpen()
        : inside === "popup"
          ? closeOpenPopup()
          : closeOpenPopup() || closeStationsPanelIfOpen();
    if (!closed) return; // nothing transient: leave the event entirely alone
    event.preventDefault();
    event.stopPropagation();
  },
  true,
);


loadAlerts();
refreshAll();
setInterval(refreshAll, POLL_INTERVAL_MS);
setInterval(loadAlerts, ALERT_POLL_INTERVAL_MS);
requestAnimationFrame(animateTrains); // glide trains between polls
