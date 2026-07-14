// Shared map infrastructure for the ordered-script frontend: the Leaflet map
// and every layer group, the toggle wiring, the status line, the reusable station
// popup machinery (used by subway, railroad and PATH stations), the service-alert
// index and banner, and the shared train-animation loop. Loaded as a plain
// <script> right after helpers.js and before the per-system files, so its
// top-level const/let bindings are in the shared global scope they all read (the
// same buildless model helpers.js -> map.js already uses; no bundler).

const map = L.map("map").setView([40.7128, -74.006], 12);

// Station dots get their own canvas pane sandwiched between the route lines
// (overlayPane, 400) and the train/bus markers (markerPane, 600), so the
// station canvas — not the route-line canvas it overlaps — receives clicks.
map.createPane("stationPane");
map.getPane("stationPane").style.zIndex = 450;

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// Buses and subways live in separate layer groups so they toggle independently.
// Route lines are vectors (canvas), which Leaflet draws beneath marker panes.
const busLayer = L.layerGroup().addTo(map);
const subwayLayer = L.layerGroup().addTo(map);
const routeLinesLayer = L.layerGroup().addTo(map);
const busRouteLayer = L.layerGroup().addTo(map); // the one clicked bus route
const stationLayer = L.layerGroup().addTo(map);
const railroadLayer = L.layerGroup().addTo(map); // LIRR + MNR GPS markers
const railroadRouteLinesLayer = L.layerGroup().addTo(map); // LIRR + MNR route geometry
const railroadStationLayer = L.layerGroup().addTo(map); // LIRR + MNR clickable stations
// AirTrain JFK is static-only (no realtime feed exists). Its own layers so it
// toggles independently of the railroad group.
const airtrainRouteLinesLayer = L.layerGroup().addTo(map); // 3 branch guideways
const airtrainStationLayer = L.layerGroup().addTo(map); // 10 clickable stations
// PATH gets its own three groups (mirroring the railroad trio) so the whole
// system toggles as one.
const pathRouteLines = L.layerGroup().addTo(map); // route geometry, both directions per route
const pathStations = L.layerGroup().addTo(map); // 13 clickable parent stations
const pathTrains = L.layerGroup().addTo(map); // trains gliding between (or placed at) stations
// NYC Ferry gets its own three groups (the same trio shape) so the whole system
// toggles as one: route geometry, clickable docks, and live GPS boats.
const ferryRouteLines = L.layerGroup().addTo(map); // route geometry, modal polyline per direction
const ferryDocks = L.layerGroup().addTo(map); // clickable landing docks
const ferryBoats = L.layerGroup().addTo(map); // live GPS boat markers

function bindToggle(checkboxId, layers) {
  const box = document.getElementById(checkboxId);
  const sync = () => {
    for (const layer of layers) {
      if (box.checked) map.addLayer(layer);
      else map.removeLayer(layer);
    }
  };
  box.addEventListener("change", sync);
  sync(); // some browsers restore checkbox state across reloads without firing change
}
bindToggle("toggle-buses", [busLayer, busRouteLayer]);
bindToggle("toggle-subways", [subwayLayer, routeLinesLayer]);
bindToggle("toggle-stations", [stationLayer]);
bindToggle("toggle-railroads", [railroadLayer, railroadRouteLinesLayer, railroadStationLayer]);
bindToggle("toggle-airtrain", [airtrainRouteLinesLayer, airtrainStationLayer]);
bindToggle("toggle-path", [pathRouteLines, pathStations, pathTrains]);
bindToggle("toggle-ferries", [ferryRouteLines, ferryDocks, ferryBoats]);

const statusEl = document.getElementById("status");

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}


/* ----- Station popups + live arrivals, shared by subway, railroad and PATH ----- */

// Canvas-rendered so ~470 circle markers stay cheap and hit-testable; on its
// own pane (above the route-line canvas) so station clicks land here.
const stationRenderer = L.canvas({ padding: 0.5, pane: "stationPane" });

// Shared popup machinery for BOTH station kinds (subway + railroad). One popup
// is open at a time (Leaflet closes others). A request token guards against a
// slow fetch landing after the user clicked a different station (of either
// kind, since the token is shared), and a 1s timer ticks countdowns down from
// absolute arrival timestamps without re-fetching. The last good arrivals
// payload lives on openStation so the tick and the 15s refresh share one source
// of truth (no captured-body closure that a later call could leave firing over
// newer state). openStation carries the station, its marker, the fetched body,
// the arrivals fetch `url`, and a kind-specific `render(station, body)`; the
// fetch/guard/timer skeleton below is otherwise kind-agnostic.
let stationSeq = 0;
let stationTimer = null;
let openStation = null; // { station, marker, body, url, render } while open

// Repaint the open popup from openStation.body. Reading the shared body (rather
// than a value captured per fetch) is what stops a stale tick from overwriting
// newer content: there is only ever one body to draw, the current one.
function renderStation() {
  if (!openStation || !openStation.body) return;
  const { station, marker, body, render } = openStation;
  if (marker.isPopupOpen()) marker.setPopupContent(render(station, body));
}


function stationError(station, message) {
  return (
    `<b>${esc(station.name ?? station.id)}</b>` +
    `<br><span class="popup-sub">${esc(message)}</span>`
  );
}

// refresh=false is a fresh popup open (show a Loading state, surface errors).
// refresh=true is the 15s background refresh of an already-open popup: keep the
// current arrivals ticking, swap in new data when it lands, and stay quiet on a
// failed poll rather than blanking good data with a Loading or error message.
// Reads the current openStation descriptor for the url/render, so it is the same
// skeleton for either station kind.
async function openStationArrivals({ refresh = false } = {}) {
  const open = openStation;
  if (!open) return;
  const { station, marker, url } = open;
  const seq = ++stationSeq;
  if (!refresh) {
    // Stop the previous tick up front so it cannot fire during this fetch.
    clearInterval(stationTimer);
    stationTimer = null;
    marker.setPopupContent(`<b>${esc(station.name ?? station.id)}</b><br>Loading arrivals…`);
  }
  let body;
  try {
    const res = await fetch(url);
    if (seq !== stationSeq) return; // superseded by another station click or a close
    if (!res.ok) {
      if (!refresh) {
        const err = await res.json().catch(() => null);
        marker.setPopupContent(
          stationError(station, err?.detail ?? `Arrivals unavailable (HTTP ${res.status})`),
        );
      }
      return; // a failed background refresh keeps the last-known arrivals ticking
    }
    body = await res.json();
  } catch {
    if (seq !== stationSeq) return;
    if (!refresh) {
      marker.setPopupContent(stationError(station, "Arrivals unavailable (network error)"));
    }
    return;
  }
  if (seq !== stationSeq) return;
  // NOTE: the skew baseline is NOT calibrated here. The arrivals endpoints carry
  // no served_at (only the five vehicle feeds do, R1), and calibrating off their
  // fetched_at was the audit poison this PR removes. The 15s vehicle-feed poll keeps
  // minClockOffset fresh. BOUNDED BOOT RACE: a client whose wall clock is materially
  // wrong that opens a station popup in the sub-second window before the first
  // vehicle poll resolves sees an uncalibrated countdown (and possibly a false age
  // line); it self-corrects on the very next 1s tick once a poll lands. The complete
  // fix (served_at on the arrivals endpoints, or gating the countdown on a settled
  // baseline) is deferred to R3's cold-start work.
  if (openStation === open) openStation.body = body;
  renderStation();
  if (!marker.isPopupOpen()) return;
  // (Re)start the single tick now that fresh data is in place.
  clearInterval(stationTimer);
  stationTimer = setInterval(renderStation, 1000);
}

// Wire one station circleMarker to the shared popup lifecycle. makeDescriptor(marker)
// builds the openStation descriptor (kind-specific url + render); the seq bump,
// timer teardown, and one-popup-at-a-time invalidation are identical for both
// kinds, so they live here once.
function bindStationPopup(marker, makeDescriptor) {
  return marker
    .bindPopup("", { minWidth: 170 })
    .on("popupopen", function () {
      openStation = makeDescriptor(this);
      openStationArrivals();
    })
    .on("popupclose", function () {
      stationSeq++; // invalidate any in-flight arrivals fetch for this popup
      clearInterval(stationTimer);
      stationTimer = null;
      if (openStation?.marker === this) openStation = null;
    });
}

/* ---------------- Service alerts (station popups) ---------------- */

// Active alerts indexed by (system, stop) and (system, route), rebuilt each poll.
// Starts empty, so a popup opened before the first fetch simply shows no alerts.
let alertsIndex = indexAlerts([]);

// R1 alerts freshness: served_at of the LAST SUCCESSFUL alerts fetch, and the last
// banner set. The alerts loop swallows failures (below), so without an explicit
// freshness signal the banner and popups silently imply the alert set is current
// when the feed may have stopped updating. alertsStale() gates the honesty marker on
// alertsServedAt; lastBannerAlerts lets tickAlertBanner re-render the marker while
// polls are failing (loadAlerts only re-renders on success).
let alertsServedAt = null;
let lastBannerAlerts = [];

// The skew-corrected client clock, matching the arrivals-countdown basis.
function alertsClockNow() {
  return Date.now() / 1000 - (minClockOffset ?? 0);
}

// The muted "alerts may be out of date" marker, or "" when the alerts feed is fresh.
// Honesty, not alarm: shared by the banner and every popup alert block so a stale
// alerts feed is disclosed everywhere the alert set is shown (or implied absent).
function staleAlertsMarker() {
  return alertsStale(alertsServedAt, alertsClockNow())
    ? `<div class="alert-stale">alerts may be out of date</div>`
    : "";
}

// Poll /api/alerts on the alerts cadence. WHY a failed or non-200 fetch is swallowed
// and keeps the last-known index: alerts are a decorative overlay, so their
// staleness or absence must never surface an error or delay the arrivals a rider
// clicked for. There is no user-facing alerts ERROR state, by design; the freshness
// marker (R1) is the one honest hedge that the index may have stopped updating.
async function loadAlerts() {
  try {
    const res = await fetch("/api/alerts");
    if (!res.ok) return; // keep the last-known index + banner silently
    const body = await res.json();
    const list = body.alerts ?? [];
    alertsIndex = indexAlerts(list);
    // This fetch succeeded, so the index is fresh as of served_at. Fall back to the
    // skew-corrected client now if an older backend omits served_at (both live on
    // the server-time axis alertsStale compares against).
    alertsServedAt = body.served_at ?? alertsClockNow();
    lastBannerAlerts = bannerAlerts(list);
    // The banner re-renders every poll (unlike popups, which render on open), so a
    // resolved agency-wide alert disappears on the next poll and a new one appears.
    renderAlertBanner(lastBannerAlerts);
  } catch {
    // network error: keep the last-known index + banner, no user-facing error
  }
}

// Re-render the banner from the last-known alerts so the "may be out of date" marker
// appears (or clears) as time crosses ALERTS_STALE_AFTER_S even while the alerts poll
// is FAILING (loadAlerts only re-renders on success). The stale flag is folded into
// the banner's dedup key, so this is a no-op until the flag actually flips. Driven by
// the 15s refreshAll tick in map.js.
function tickAlertBanner() {
  renderAlertBanner(lastBannerAlerts);
}

// The alerts block for a station popup: match the current index (read fresh as a
// global, so a popup re-render picks up whatever the store holds now) against the
// station, scoped by system, plus every route that serves the station. Returns ""
// when nothing matches, so no empty container is rendered.
//
// The served-routes set is the UNION of two sources (H5): the static
// routes-per-station index the backend now derives from stop_times (station.routes),
// and the route ids present in the station's CURRENT arrivals. The static list is
// the complete, always-present set, so a route-scoped alert reaches the station even
// with no imminent train; the arrivals ids are folded in too so a station whose
// static routes failed to load still shows alerts for routes with a live train (and
// so a brand-new route running before the next static refresh is covered). Either
// source alone is a strict subset of the intent, so both feed matchStationAlerts.
function stationAlertsBlock(system, station, body) {
  const routeIds = new Set(station.routes ?? []);
  for (const arrivals of Object.values(body?.directions ?? {})) {
    for (const arr of arrivals ?? []) if (arr.route_id) routeIds.add(arr.route_id);
  }
  // Append the alerts-freshness marker (R1): if the alerts feed itself is stale it
  // shows even when this station currently matches no alerts (the block is ""), so a
  // rider is never shown an empty-looking station while the alert feed is down.
  return alertsBlockHtml(matchStationAlerts(alertsIndex, system, station.id, routeIds)) +
    staleAlertsMarker();
}

// The alerts block for a route surface (bus / subway train / railroad train popup):
// match the current index against the popup's system and route. WHY read alertsIndex
// fresh each call: these popups are bound as functions and render at OPEN time (and
// on the marker poll's popup.update()), so they show the store as of open/refresh,
// not a live stream. A newly-arrived alert appears the next time the popup opens or
// updates, the same contract the arrivals popups follow.
function routeAlertsBlock(system, routeId) {
  return alertsBlockHtml(matchRouteAlerts(alertsIndex, system, routeId)) + staleAlertsMarker();
}

// Agency-wide (selector-less) alerts get a dismissible banner over the map instead
// of a popup, since they belong to no single route or station. WHY dismissal is per
// alert and in-memory for the session: dismissing hides the currently-shown alerts,
// a later poll re-showing the SAME ones keeps them hidden, but a NEW one (never
// dismissed) reopens the banner. So a rider can clear a standing incident without
// losing the next, distinct one, and a page reload starts fresh. The key is
// "system|id", scoped like every other alert join, so a bare id reused across two
// feeds cannot make dismissing one hide an unrelated agency-wide alert.
const dismissedAlertIds = new Set();
const alertKey = (a) => `${a.system}|${a.id}`;

// Signature of the last-rendered banner, so an unchanged banner is NOT rebuilt every
// 60s poll: reassigning innerHTML would drop any text the rider has selected and
// re-parse identical markup for no visual change.
let lastBannerKey = null;

function renderAlertBanner(alerts) {
  const el = document.getElementById("alert-banner");
  const shown = alerts.filter((a) => a.header && !dismissedAlertIds.has(alertKey(a)));
  // R1: the banner also carries the alerts-freshness marker, so a stale alerts feed
  // is disclosed even when there are no agency-wide alerts to show. Fold the stale
  // flag into the dedup key, or the marker would never paint/clear on an unchanged
  // alert set (the key is otherwise just the shown ids).
  const stale = alertsStale(alertsServedAt, alertsClockNow());
  const key = (stale ? "S|" : "F|") + shown.map(alertKey).join("\n");
  if (key === lastBannerKey) return; // unchanged since the last render: leave the DOM alone
  lastBannerKey = key;
  if (!shown.length && !stale) {
    el.replaceChildren(); // nothing to show and alerts are current: no banner strip
    return;
  }
  const rows = shown.map((a) => `<div class="alert-banner-row">${esc(a.header)}</div>`).join("");
  const staleRow = stale
    ? `<div class="alert-banner-row alert-stale">alerts may be out of date</div>`
    : "";
  // The dismiss button only appears when there ARE dismissible alerts; it clears the
  // shown alerts but never the freshness marker (dismissing incidents must not hide
  // the honesty hedge that the feed is down).
  const dismiss = shown.length
    ? `<button type="button" id="alert-banner-dismiss" title="Dismiss">&times;</button>`
    : "";
  el.innerHTML =
    `<div class="alert-banner-strip">` +
    `<div class="alert-banner-rows">${rows}${staleRow}</div>` +
    dismiss +
    `</div>`;
  const dismissBtn = el.querySelector("#alert-banner-dismiss");
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      for (const alert of shown) dismissedAlertIds.add(alertKey(alert));
      renderAlertBanner(alerts); // re-render: dismissed ids drop out (marker, if any, stays)
    });
  }
}


// Glide trains between polls: recompute every marker's interpolated position
// from the current skew-corrected time. Throttled to ~10 fps (trains are slow
// and there can be a few hundred markers), and skipped entirely while the
// subway layer is hidden. rAF keeps rescheduling so it resumes on re-toggle.
const TRAIN_TICK_MS = 100;
let lastTrainTick = 0;

function animateTrains(ts) {
  // Glides subway trains, placed railroad trains, and PATH trains between
  // polls. GPS railroad trains are not animated here: they move by their
  // reported position in applyRailroads. Anchorless PATH trains cost one
  // trainLatLng fallback each and stay put, so no per-record gate is needed.
  // Each layer is gated on its own visibility; rAF keeps rescheduling so
  // animation resumes on re-toggle.
  if (ts - lastTrainTick >= TRAIN_TICK_MS) {
    lastTrainTick = ts;
    const now = Date.now() / 1000 - (minClockOffset ?? 0);
    if (map.hasLayer(subwayLayer)) {
      for (const record of trains.values()) {
        record.marker.setLatLng(trainLatLng(record.latest, now, record.fState));
      }
    }
    if (map.hasLayer(railroadLayer)) {
      for (const record of railroads.values()) {
        if (record.placed) {
          record.marker.setLatLng(trainLatLng(record.latest, now, record.fState));
        }
      }
    }
    if (map.hasLayer(pathTrains)) {
      for (const record of pathTrainRecords.values()) {
        record.marker.setLatLng(trainLatLng(record.latest, now, record.fState));
      }
    }
  }
  requestAnimationFrame(animateTrains);
}

