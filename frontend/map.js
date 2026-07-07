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
const pathTrains = L.layerGroup().addTo(map); // trains placed at their next station

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

const statusEl = document.getElementById("status");

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

// esc, routeColor, lineColor, staleness and friends live in helpers.js,
// loaded just before this script.

/* ---------------- Buses ---------------- */

// Arrow rotated to the bearing (GTFS bearing = degrees clockwise from north,
// which matches CSS rotate with an up-pointing arrow). Dot when bearing is null.
function busIcon(bus) {
  const color = routeColor(bus.route_id);
  const html =
    bus.bearing != null
      ? `<svg viewBox="0 0 20 20" style="transform: rotate(${Number(bus.bearing)}deg)">
           <path d="M10 2 L16 17 L10 13 L4 17 Z" fill="${color}" stroke="#fff" stroke-width="1.2"/>
         </svg>`
      : `<svg viewBox="0 0 20 20">
           <circle cx="10" cy="10" r="5.5" fill="${color}" stroke="#fff" stroke-width="1.5"/>
         </svg>`;
  return L.divIcon({ className: "bus-marker", html, iconSize: [20, 20], iconAnchor: [10, 10] });
}

function busPopup(record) {
  const b = record.latest;
  const heading = b.bearing != null ? `${Math.round(b.bearing)}°` : "unknown";
  const note = busRouteNotes.get(b.route_id);
  const showNote = note && Date.now() - note.at < NOTE_TTL_MS;
  return (
    // Bus alerts are route-only (no stop selectors); "bus" route ids share the
    // bus layer's id space, so the match is by route_id under system "bus".
    routeAlertsBlock("bus", b.route_id) +
    `<b style="color:${routeColor(b.route_id)}">${esc(b.route_id ?? "Unknown route")}</b>` +
    `<br>Bus ${esc(b.id)}<br>Heading: ${heading}` +
    (showNote ? `<br><span class="popup-sub">${esc(note.message)}</span>` : "")
  );
}

/* ----- On-demand bus route line (click a bus to draw its route) ----- */

let shownBusRoute = null; // { routeId, busId }
let pendingBusId = null; // bus whose route fetch is in flight
let busRouteSeq = 0; // request token: bumped by every new request AND by clear
const busRouteNotes = new Map(); // route_id -> { message, at } shown in the popup
const NOTE_TTL_MS = 60000; // a transient failure shouldn't haunt popups all session

// Notes are only ever added on fetch failures (below), so sweeping expired
// entries on each set bounds the map for the session without any timer.
function setBusRouteNote(routeId, message) {
  const now = Date.now();
  for (const [id, note] of busRouteNotes) {
    if (now - note.at >= NOTE_TTL_MS) busRouteNotes.delete(id);
  }
  busRouteNotes.set(routeId, { message, at: now });
}

function refreshOpenPopup(busId) {
  const record = buses.get(busId);
  if (record?.marker.isPopupOpen()) record.marker.getPopup().update();
}

function clearBusRoute() {
  busRouteSeq++; // invalidate any in-flight fetch
  pendingBusId = null;
  busRouteLayer.clearLayers();
  shownBusRoute = null;
  document.getElementById("route-banner").hidden = true;
}

async function toggleBusRoute(bus, marker) {
  if (!bus?.route_id) return;

  // Leaflet's own popup toggle runs before this handler, so isPopupOpen()
  // reflects the popup's NEW state. For a re-click on the selected (or
  // pending) bus: popup just closed -> remove the line; popup just reopened
  // (it was closed by a map click earlier) -> keep the line as is.
  const sameBus =
    (shownBusRoute &&
      shownBusRoute.busId === bus.id &&
      shownBusRoute.routeId === bus.route_id) ||
    pendingBusId === bus.id;
  if (sameBus) {
    if (!marker.isPopupOpen()) clearBusRoute();
    return;
  }

  clearBusRoute(); // a different bus replaces any current line
  const requestId = ++busRouteSeq;
  pendingBusId = bus.id;

  let geometry;
  try {
    const res = await fetch(`/api/bus-route/${encodeURIComponent(bus.route_id)}`);
    if (requestId !== busRouteSeq) return; // superseded by a newer click/clear
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      pendingBusId = null;
      setBusRouteNote(bus.route_id, body?.detail ?? `Route line unavailable (HTTP ${res.status})`);
      refreshOpenPopup(bus.id);
      return;
    }
    geometry = await res.json();
  } catch {
    if (requestId !== busRouteSeq) return;
    pendingBusId = null;
    setBusRouteNote(bus.route_id, "Route line unavailable (network error)");
    refreshOpenPopup(bus.id);
    return;
  }
  if (requestId !== busRouteSeq) return; // superseded while parsing
  pendingBusId = null;
  busRouteNotes.delete(bus.route_id);
  refreshOpenPopup(bus.id);

  for (const points of geometry.directions ?? []) {
    L.polyline(points, {
      color: routeColor(bus.route_id),
      weight: 3.5,
      opacity: 0.65,
      interactive: false,
      renderer: lineRenderer,
    }).addTo(busRouteLayer);
  }
  shownBusRoute = { routeId: bus.route_id, busId: bus.id };
  const banner = document.getElementById("route-banner");
  document.getElementById("route-banner-label").textContent = `Bus route ${bus.route_id}`;
  document.getElementById("route-banner-label").style.color = routeColor(bus.route_id);
  banner.hidden = false;
}

document.getElementById("route-clear").addEventListener("click", clearBusRoute);

// Keep the banner honest when the Buses toggle hides the route line layer.
document.getElementById("toggle-buses").addEventListener("change", (e) => {
  document.getElementById("route-banner").hidden = !e.target.checked || !shownBusRoute;
});

const buses = new Map(); // bus id -> { marker, routeId, bearing, latest }

function applyBuses(data) {
  const seen = new Set();
  for (const bus of data) {
    seen.add(bus.id);
    const record = buses.get(bus.id);
    if (record) {
      record.marker.setLatLng([bus.latitude, bus.longitude]);
      // Vehicle reassigned to a different route: its drawn line is now stale.
      if (record.routeId !== bus.route_id && shownBusRoute?.busId === bus.id) {
        clearBusRoute();
      }
      const shapeChanged =
        record.routeId !== bus.route_id ||
        (record.bearing == null) !== (bus.bearing == null);
      if (shapeChanged) {
        record.marker.setIcon(busIcon(bus));
      } else if (record.bearing !== bus.bearing && bus.bearing != null) {
        // Mutate the existing SVG so the CSS rotation transition animates;
        // setIcon would recreate the element and snap to the new angle.
        const icon = busIcon(bus); // built once, reused by both branches below
        const svg = record.marker.getElement()?.firstElementChild;
        if (svg) {
          svg.style.transform = `rotate(${Number(bus.bearing)}deg)`;
          // Keep the stored html current so Leaflet recreates the element
          // correctly if the layer is toggled off and back on.
          record.marker.options.icon.options.html = icon.options.html;
        } else {
          record.marker.setIcon(icon); // not in the DOM (layer hidden)
        }
      }
      record.bearing = bus.bearing;
      record.routeId = bus.route_id;
      record.latest = bus;
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { bearing: bus.bearing, routeId: bus.route_id, latest: bus };
      newRecord.marker = L.marker([bus.latitude, bus.longitude], { icon: busIcon(bus) })
        .bindPopup(() => busPopup(newRecord))
        .on("click", () => toggleBusRoute(newRecord.latest, newRecord.marker))
        .addTo(busLayer);
      buses.set(bus.id, newRecord);
    }
  }
  for (const [id, record] of buses) {
    if (!seen.has(id)) {
      busLayer.removeLayer(record.marker);
      buses.delete(id);
      // If the vehicle whose route line is drawn (or has a fetch in flight) drops
      // out of the feed, clear that line and its banner too, so a bounded
      // empty-feed sweep (or a lone reassignment) does not leave a ghost route
      // pointing at a bus no longer on the map. clearBusRoute also invalidates
      // any in-flight route fetch via its request token.
      if (shownBusRoute?.busId === id || pendingBusId === id) clearBusRoute();
    }
  }
}

/* ---------------- Subways ---------------- */

function trainIcon(train) {
  const route = train.route_id ?? "";
  const label = /^[A-Za-z0-9]{1,3}$/.test(route) ? route : "?";
  const color = lineColor(route);
  const textColor = DARK_TEXT_LINES.has(route[0]) ? "#1a1a1a" : "#ffffff";
  const html = `<svg viewBox="0 0 18 18">
      <rect x="1.5" y="1.5" width="15" height="15" rx="3" fill="${color}" stroke="#fff" stroke-width="1.5"/>
      <text x="9" y="9.5" text-anchor="middle" dominant-baseline="central"
            font-size="${label.length > 1 ? 7 : 9}" font-weight="700"
            font-family="system-ui, sans-serif" fill="${textColor}">${esc(label)}</text>
    </svg>`;
  return L.divIcon({ className: "train-marker", html, iconSize: [18, 18], iconAnchor: [9, 9] });
}

function trainPopup(record) {
  const t = record.latest;
  return (
    routeAlertsBlock("subway", t.route_id) +
    `<b style="color:${lineColor(t.route_id)}">${esc(t.route_id ?? "?")} train</b>` +
    `<br>Next stop: ${esc(t.stop_name ?? t.stop_id ?? "unknown")}` +
    (t.direction ? `<br>${esc(t.direction)}` : "") +
    `<br><span class="popup-sub">Trip ${esc(t.trip_id ?? "?")}</span>`
  );
}

// Static route geometry, fetched once at startup (not polled). Canvas
// renderer keeps ~22k points cheap; lines are decorative, so failures are
// silent and the map just shows markers without them.
const lineRenderer = L.canvas({ padding: 0.3 });
const routeIndex = new Map(); // route_id -> [{ points, cum }] for interpolation

// The static loaders below each return true only once they have populated
// their layer from a NON-EMPTY payload, and false otherwise, so retryUntil can
// keep asking. WHY an empty 200 is not success: while a static group's warmup has
// FAILED (and is retrying server-side), its endpoints serve [] under Cache-Control
// no-cache, precisely so a browser will come back and see the healed state; and
// while it is still LOADING they 503. None of these endpoints has a legitimately
// empty steady state, so emptiness always means "ask again later". Each attempt
// stays all-or-nothing (populate only after the full payload parsed), so a retry
// can never double-add markers or polylines; once a loader returns true it is
// never called again.
async function loadRouteLines() {
  let routes;
  try {
    const res = await fetch("/api/subway-routes");
    if (!res.ok) return false;
    routes = await res.json();
  } catch {
    return false;
  }
  if (!routes.length) return false; // failed-warmup []: retry until the backend heals
  for (const route of routes) {
    routeIndex.set(
      route.route,
      route.polylines.map((points) => ({ points, cum: polylineCumLengths(points) })),
    );
    for (const points of route.polylines) {
      L.polyline(points, {
        color: lineColor(route.route),
        weight: 2.5,
        opacity: 0.5,
        interactive: false,
        renderer: lineRenderer,
      }).addTo(routeLinesLayer);
    }
  }
  return true;
}

// `${system}|${route_id}` -> [{ points, cum }], the geometry placed railroad
// trains glide along. Keyed by (system, route_id) because LIRR and MNR route ids
// collide; populated by loadRailroadRoutes and read by applyRailroads.
const railroadRouteIndex = new Map();
// `system|route_id` -> rider-facing route name (e.g. "Babylon Branch"), from
// /api/railroad-routes; used to label the railroad train and station popups.
const railroadRouteNames = new Map();

async function loadRailroadRoutes() {
  let routes;
  try {
    const res = await fetch("/api/railroad-routes");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    routes = await res.json();
  } catch {
    return false;
  }
  // RAILROAD NUANCE: the backend's railroad warmup is lenient PER SYSTEM. It
  // settles "ready" even when one system's static failed to load, and this
  // endpoint then serves only the loaded system's entries under the normal
  // hour-long cache. A non-empty one-system payload is therefore a SETTLED state
  // the server-side warmup will not revisit, so accepting it and stopping is
  // correct: further frontend retries would just re-read the same cached partial,
  // never a fuller one. Only a fully empty payload means "ask again later".
  if (!routes.length) return false;
  for (const route of routes) {
    // Key by (system, route): LIRR and MNR route ids collide, so route_id alone
    // would merge two systems' geometry. Matches the endpoint's {system, route,
    // name, polylines} shape and the (system, route_id) lookup in applyRailroads.
    railroadRouteIndex.set(
      `${route.system}|${route.route}`,
      route.polylines.map((points) => ({ points, cum: polylineCumLengths(points) })),
    );
    // The rider-facing route name (e.g. "Babylon Branch"), for the train and
    // station-arrivals popups; only routes with geometry reach here (see the
    // endpoint's KNOWN GAP), which is fine since a geometry-less route has no
    // trains to label either.
    if (route.name) railroadRouteNames.set(`${route.system}|${route.route}`, route.name);
    for (const points of route.polylines) {
      L.polyline(points, {
        color: railroadColor(route.route),
        weight: 2.5,
        opacity: 0.5,
        interactive: false,
        renderer: lineRenderer,
      }).addTo(railroadRouteLinesLayer);
    }
  }
  return true;
}

/* ----- Subway stations + live arrivals (click a station for countdowns) ----- */

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

function subwayArrivalsHtml(station, body) {
  // Skew-corrected now, reusing the staleness baseline from helpers.js.
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  let html = `<b>${esc(station.name ?? station.id)}</b>`;
  for (const dir of ["Northbound", "Southbound"]) {
    const arrivals = body.directions?.[dir] ?? [];
    html += `<div class="arr-dir">${dir}</div>`;
    if (!arrivals.length) {
      html += `<div class="arr-none">No trains</div>`;
      continue;
    }
    html += arrivals
      .map((a) => {
        const route = a.route_id ?? "";
        const textColor = DARK_TEXT_LINES.has(route[0]) ? "#1a1a1a" : "#fff";
        const badge =
          `<span class="arr-badge" style="background:${lineColor(route)};color:${textColor}">` +
          `${esc(route || "?")}</span>`;
        return `${badge} ${esc(formatCountdown(a.arrival - now))}`;
      })
      .join("<br>");
  }
  return html;
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
  noteClockOffset(body.fetched_at); // keep the skew baseline fresh
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

// Poll /api/alerts on the alerts cadence. WHY a failed or non-200 fetch is swallowed
// and keeps the last-known index: alerts are a decorative overlay, so their
// staleness or absence must never surface an error or delay the arrivals a rider
// clicked for. There is no user-facing alerts error state, by design.
async function loadAlerts() {
  try {
    const res = await fetch("/api/alerts");
    if (!res.ok) return; // keep the last-known index + banner silently
    const body = await res.json();
    const list = body.alerts ?? [];
    alertsIndex = indexAlerts(list);
    // The banner re-renders every poll (unlike popups, which render on open), so a
    // resolved agency-wide alert disappears on the next poll and a new one appears.
    renderAlertBanner(bannerAlerts(list));
  } catch {
    // network error: keep the last-known index + banner, no user-facing error
  }
}

// The alerts block for a station popup: match the current index (read fresh as a
// global, so a popup re-render picks up whatever the store holds now) against the
// station, scoped by system, plus the route ids present in its current arrivals.
// Returns "" when nothing matches, so no empty container is rendered.
function stationAlertsBlock(system, station, body) {
  const routeIds = new Set();
  for (const arrivals of Object.values(body?.directions ?? {})) {
    for (const arr of arrivals ?? []) if (arr.route_id) routeIds.add(arr.route_id);
  }
  return alertsBlockHtml(matchStationAlerts(alertsIndex, system, station.id, routeIds));
}

// The alerts block for a route surface (bus / subway train / railroad train popup):
// match the current index against the popup's system and route. WHY read alertsIndex
// fresh each call: these popups are bound as functions and render at OPEN time (and
// on the marker poll's popup.update()), so they show the store as of open/refresh,
// not a live stream. A newly-arrived alert appears the next time the popup opens or
// updates, the same contract the arrivals popups follow.
function routeAlertsBlock(system, routeId) {
  return alertsBlockHtml(matchRouteAlerts(alertsIndex, system, routeId));
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
  const key = shown.map(alertKey).join("\n");
  if (key === lastBannerKey) return; // unchanged since the last render: leave the DOM alone
  lastBannerKey = key;
  if (!shown.length) {
    el.replaceChildren(); // nothing to show: no banner strip in the DOM
    return;
  }
  const rows = shown.map((a) => `<div class="alert-banner-row">${esc(a.header)}</div>`).join("");
  el.innerHTML =
    `<div class="alert-banner-strip">` +
    `<div class="alert-banner-rows">${rows}</div>` +
    `<button type="button" id="alert-banner-dismiss" title="Dismiss">&times;</button>` +
    `</div>`;
  el.querySelector("#alert-banner-dismiss").addEventListener("click", () => {
    for (const alert of shown) dismissedAlertIds.add(alertKey(alert));
    renderAlertBanner(alerts); // re-render: the dismissed ids drop out, emptying the strip
  });
}

async function loadStations() {
  let stations;
  try {
    const res = await fetch("/api/subway-stops");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stations = await res.json();
  } catch {
    return false;
  }
  if (!stations.length) return false; // failed-warmup []: retry until the backend heals
  for (const station of stations) {
    const marker = L.circleMarker([station.lat, station.lon], {
      radius: 4,
      color: "#333",
      weight: 1.5,
      fillColor: "#fff",
      fillOpacity: 1,
      renderer: stationRenderer,
    });
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url: `/api/subway-arrivals/${encodeURIComponent(station.id)}`,
      // Prepend any active subway alerts affecting this station above the arrivals.
      render: (s, b) => stationAlertsBlock("subway", s, b) + subwayArrivalsHtml(s, b),
    })).addTo(stationLayer);
  }
  return true;
}

async function loadRailroadStations() {
  let stations;
  try {
    const res = await fetch("/api/railroad-stops");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stations = await res.json();
  } catch {
    return false;
  }
  // Same settled-partial rule as loadRailroadRoutes: a one-system payload is a
  // state the lenient backend warmup will not revisit, so non-empty is success.
  if (!stations.length) return false;
  for (const station of stations) {
    // Same pane/renderer as subway stations (click priority + cheap canvas), but
    // visually distinct: heavier, darker slate stroke and a slightly smaller
    // radius over the shared white fill. Keyed by (system, id) in the fetch url
    // because LIRR and MNR stop_id namespaces can collide.
    const marker = L.circleMarker([station.lat, station.lon], {
      radius: 3.5,
      color: "#334155",
      weight: 2.5,
      fillColor: "#fff",
      fillOpacity: 1,
      renderer: stationRenderer,
    });
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url:
        `/api/railroad-arrivals/${encodeURIComponent(station.system)}` +
        `/${encodeURIComponent(station.id)}`,
      // Prepend any active alerts for this railroad station, scoped to its own
      // system (LIRR/MNR) so a colliding numeric id from another mode never leaks.
      render: (s, b) =>
        stationAlertsBlock(s.system, s, b) +
        railroadArrivalsHtml(
          s,
          b,
          Date.now() / 1000 - (minClockOffset ?? 0),
          (routeId) => railroadRouteNames.get(`${s.system}|${routeId}`) || null,
        ),
    })).addTo(railroadStationLayer);
  }
  return true;
}

/* ---------------- AirTrain JFK (static-only) ---------------- */

// One distinct color for the whole AirTrain system, deliberately OUTSIDE the
// subway (lineColor) and railroad (railroadColor) palettes so the guideway reads
// as its own mode. AirTrain is geographically isolated at JFK, so it never sits
// beside the lines it must be told apart from.
const AIRTRAIN_COLOR = "#b5179e";

// Square marker, a different SHAPE from the round subway/rail station dots, so the
// AirTrain mode is legible at a glance even where colors are close.
function airtrainIcon() {
  const html =
    `<svg viewBox="0 0 14 14"><rect x="1.5" y="1.5" width="11" height="11" rx="2" ` +
    `fill="#fff" stroke="${AIRTRAIN_COLOR}" stroke-width="2.5"/></svg>`;
  return L.divIcon({ className: "airtrain-marker", html, iconSize: [14, 14], iconAnchor: [7, 7] });
}

// Minutes since midnight in America/New_York, derived HERE (the caller) and passed
// into the pure headway helper. WHY force Eastern rather than the browser's local
// time: AirTrain runs on New York local time, so a rider viewing from another
// timezone (or a machine clock set to UTC) would otherwise be shown the wrong
// scheduled band.
function nyMinutesSinceMidnight(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const hh = Number(parts.find((p) => p.type === "hour").value) % 24; // Intl can emit "24" at midnight
  const mm = Number(parts.find((p) => p.type === "minute").value);
  return hh * 60 + mm;
}

async function loadAirtrain() {
  let data;
  try {
    const res = await fetch("/api/airtrain");
    if (!res.ok) return false;
    data = await res.json();
  } catch {
    return false;
  }
  // AirTrain serves a committed fixture, so only a transient network error can
  // fail it (there is no warmup 503 or failed-[] state). It still gets the same
  // non-empty check for uniformity with the other static loaders.
  if (!data.stations?.length || !data.routes?.length) return false;
  const routes = data.routes;
  // Branch guideways: one AirTrain color, non-interactive (clicks fall through to
  // the station markers, matching the subway/rail route lines).
  for (const route of routes) {
    if (!route.polyline?.length) continue;
    L.polyline(route.polyline, {
      color: AIRTRAIN_COLOR,
      weight: 3,
      opacity: 0.85,
      interactive: false,
      renderer: lineRenderer,
    }).addTo(airtrainRouteLinesLayer);
  }
  // Station markers with a PLAIN popup. bindPopup(fn) recomputes its content on
  // every open, so the scheduled band reflects the moment the rider opened it: a
  // long-lived tab never shows a stale band, and there is no timer to leak. This
  // deliberately does NOT use bindStationPopup / the live countdown machinery,
  // because AirTrain has no realtime feed to count down from.
  for (const station of data.stations) {
    // Render on stationPane (z-index 450) like the subway/rail station dots, so the
    // squares sit ABOVE route lines but BELOW the train/bus markers (markerPane 600),
    // matching the station-below-vehicles layering the rest of the map keeps.
    L.marker([station.lat, station.lon], { icon: airtrainIcon(), pane: "stationPane" })
      .bindPopup(() => airtrainStationPopupHtml(station, routes, nyMinutesSinceMidnight()))
      .addTo(airtrainStationLayer);
  }
  return true;
}

/* ---------------- PATH ---------------- */

// route_id -> css color / rider-facing name, from /api/path-routes; read by the
// PATH train icons/popups and the station arrivals badges. pathColor validates
// the feed's bare-hex route_color, so every stored value is a safe css color.
const pathRouteColors = new Map();
const pathRouteNames = new Map();

async function loadPathRoutes() {
  let routes;
  try {
    const res = await fetch("/api/path-routes");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    routes = await res.json();
  } catch {
    return false;
  }
  // Same contract as the subway loaders: PATH is a single-system warmup group,
  // so a failed-warmup [] (served no-cache) always means "ask again later".
  if (!routes.length) return false;
  for (const route of routes) {
    const color = pathColor(route.color);
    pathRouteColors.set(route.id, color);
    if (route.name) pathRouteNames.set(route.id, route.name);
    // Every entry of the shape list draws (the modal polyline per direction,
    // usually two per route); non-interactive like the AirTrain guideways so
    // clicks fall through to the station dots.
    for (const points of route.shape) {
      L.polyline(points, {
        color,
        weight: 2.5,
        opacity: 0.5,
        interactive: false,
        renderer: lineRenderer,
      }).addTo(pathRouteLines);
    }
  }
  return true;
}

async function loadPathStops() {
  let stations;
  try {
    const res = await fetch("/api/path-stops");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stations = await res.json();
  } catch {
    return false;
  }
  if (!stations.length) return false; // failed-warmup []: retry until the backend heals
  for (const station of stations) {
    // Same pane/renderer as the other station dots (click priority + cheap
    // canvas), but INVERTED fill: a solid slate-blue dot under a white ring,
    // where subway and railroad stations are both white-filled rings. PATH
    // stations sit directly among subway stations in Manhattan (33rd St, WTC),
    // so a third white-filled ring variant would be indistinguishable at a
    // glance; flipping the fill makes the mode legible the way the AirTrain
    // square does by shape. The slate-blue belongs to neither the subway nor
    // the railroad palette nor any real PATH route color.
    const marker = L.circleMarker([station.lat, station.lon], {
      radius: 4,
      color: "#fff",
      weight: 1.5,
      fillColor: "#3d5a80",
      fillOpacity: 1,
      renderer: stationRenderer,
    });
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url: `/api/path-arrivals/${encodeURIComponent(station.id)}`,
      // Unlike the subway/railroad renders there is NO alerts prepend: PATH
      // publishes no service alerts feed, so there is nothing to join. The
      // countdown tick, refresh, and supersession machinery are all inherited
      // from bindStationPopup / openStationArrivals unchanged.
      render: (s, b) =>
        pathArrivalsHtml(
          s,
          b,
          Date.now() / 1000 - (minClockOffset ?? 0),
          (routeId) => pathRouteColors.get(routeId) ?? PATH_FALLBACK_COLOR,
          (routeId) => pathRouteNames.get(routeId) || null,
        ),
    })).addTo(pathStations);
  }
  return true;
}

// Diamond markers, a different SHAPE from the subway's rounded squares and the
// railroad's squares: PATH trains sit at the same Manhattan stations as subway
// trains, so shape (not just color, which varies per route on both modes) is
// what keeps them apart at a glance.
//
// Anchored ABOVE the station point rather than centered on it. Every PATH
// train is placed at exactly its station's coordinates (no GPS, no gliding
// until 13d), so a centered diamond on the higher marker pane would cover the
// station dot and steal every click meant for the arrivals popup; with ~50
// trains over 13 stations, that blocked arrivals at essentially every station.
// Floating the diamond just above the dot, tip pointing at it like a map pin,
// keeps BOTH click targets alive: the dot for arrivals, the diamond for the
// train. popupAnchor lifts the train popup to the diamond rather than the
// station point beneath it.
function pathIcon(train) {
  const color = pathRouteColors.get(train.route_id) ?? PATH_FALLBACK_COLOR;
  const html =
    `<svg viewBox="0 0 16 16"><path d="M8 1.5 L14.5 8 L8 14.5 L1.5 8 Z" ` +
    `fill="${color}" stroke="#fff" stroke-width="1.5"/></svg>`;
  return L.divIcon({
    className: "path-marker",
    html,
    iconSize: [16, 16],
    iconAnchor: [8, 20],
    popupAnchor: [0, -20],
  });
}

function applyPath(data) {
  // WHOLESALE REBUILD, deliberately unlike every other apply*: PATH bridge trip
  // ids churn 100% when the upstream refreshes (recorded in path_static.py's
  // module docstring), so the keyed diffing the bus/subway/railroad paths use
  // would see every train as removed AND re-added on such a poll: phantom
  // add/remove churn over what is physically the same handful of trains.
  // Rebuilding ~50 markers is cheap, and a failed /api/path poll never reaches
  // here (refreshSource keeps last-known markers on error, like the other
  // systems). Cost accepted knowingly: an open PATH train popup survives at
  // most one poll, since without stable identity there is nothing to reattach
  // it to. 13d may add a synthetic cross-poll identity; until then nothing
  // keys on trip_id.
  pathTrains.clearLayers();
  for (const train of data) {
    // Marker at the placed (next/current) station, the railroad position-less
    // precedent: the bridge feed carries no vehicle positions, no prev anchors
    // (null in 13b by design), so there is no interpolation and no gliding.
    L.marker([train.latitude, train.longitude], { icon: pathIcon(train) })
      .bindPopup(() =>
        // No routeAlertsBlock prepend here either (the subway trainPopup's
        // alert join): PATH has no alerts feed.
        pathTrainPopupHtml(
          train,
          pathRouteNames.get(train.route_id) || null,
          pathRouteColors.get(train.route_id) ?? PATH_FALLBACK_COLOR,
        ),
      )
      .addTo(pathTrains);
  }
}

const trains = new Map(); // trip id -> { marker, routeId, latest }

// computeRouteSlice (slice a train's route polyline between its prev and next
// station) lives in helpers.js so it is node-testable and shared with the
// railroad route index; it is pure, so the caller resolves the route geometry
// (routeIndex.get) and passes it in.

function applyTrains(data) {
  // Skew-corrected now, same basis as the arrivals popups; trainLatLng interpolates
  // each train between its prev and next station (static fallback otherwise).
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  const seen = new Set();
  for (const train of data) {
    seen.add(train.trip_id);
    const record = trains.get(train.trip_id);
    if (record) {
      // route_id is in the key: a mid-trip route relabel must re-project onto the
      // new route's geometry rather than reuse the old route's cached slice.
      const segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route =
        record._segId === segId && record.latest._route
          ? record.latest._route
          : computeRouteSlice(train, routeIndex.get(train.route_id));
      record._segId = segId;
      record.latest = train;
      record.marker.setLatLng(trainLatLng(train, now, record.fState));
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(trainIcon(train));
        record.routeId = train.route_id;
      }
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { routeId: train.route_id, latest: train, fState: {} };
      newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route = computeRouteSlice(train, routeIndex.get(train.route_id));
      newRecord.marker = L.marker(trainLatLng(train, now, newRecord.fState), { icon: trainIcon(train) })
        .bindPopup(() => trainPopup(newRecord))
        .addTo(subwayLayer);
      trains.set(train.trip_id, newRecord);
    }
  }
  for (const [id, record] of trains) {
    if (!seen.has(id)) {
      subwayLayer.removeLayer(record.marker);
      trains.delete(id);
    }
  }
}

// Glide trains between polls: recompute every marker's interpolated position
// from the current skew-corrected time. Throttled to ~10 fps (trains are slow
// and there can be a few hundred markers), and skipped entirely while the
// subway layer is hidden. rAF keeps rescheduling so it resumes on re-toggle.
const TRAIN_TICK_MS = 100;
let lastTrainTick = 0;

function animateTrains(ts) {
  // Glides both subway trains and placed railroad trains between polls. GPS
  // railroad trains are not animated here: they move by their reported position
  // in applyRailroads. Each layer is gated on its own visibility; rAF keeps
  // rescheduling so animation resumes on re-toggle.
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
  }
  requestAnimationFrame(animateTrains);
}

/* ---------------- Railroads (LIRR + MNR) ---------------- */

// isPlacedRailroad (placed-at-station vs live GPS) lives in helpers.js so it is
// node-testable; it keys off the authoritative stop_id the GPS decode leaves null.

// Square markers, colored by railroadColor (railroad route ids collide with the
// subway palette, so they get their own). GPS trains are filled; placed trains
// (a station estimate from the schedule, no live position) are hollow, so the
// two are visually distinct.
function railroadIcon(train) {
  const color = railroadColor(train.route_id);
  const rect = isPlacedRailroad(train)
    ? `<rect x="2" y="2" width="12" height="12" rx="1.5" fill="#fff" stroke="${color}" stroke-width="2.5"/>`
    : `<rect x="1.5" y="1.5" width="13" height="13" rx="1.5" fill="${color}" stroke="#fff" stroke-width="1.5"/>`;
  const html = `<svg viewBox="0 0 16 16">${rect}</svg>`;
  return L.divIcon({ className: "railroad-marker", html, iconSize: [16, 16], iconAnchor: [8, 8] });
}

function railroadPopup(record) {
  const t = record.latest;
  const head = formatRailroadHead(t.system, t.route_id, railroadRouteNames.get(`${t.system}|${t.route_id}`));
  return (
    // Scoped to the train's OWN system (LIRR/MNR) so a numeric route id shared with
    // another mode never leaks in.
    routeAlertsBlock(t.system, t.route_id) +
    `<b style="color:${railroadColor(t.route_id)}">${esc(head)}</b>` +
    (t.train_num ? `<br>Train ${esc(t.train_num)}` : "") +
    // Placed trains carry a next/current station; GPS trains do not.
    (isPlacedRailroad(t) && t.stop_name ? `<br>Next stop: ${esc(t.stop_name)}` : "") +
    (t.direction ? `<br>${esc(t.direction)}` : "") +
    `<br><span class="popup-sub">${isPlacedRailroad(t) ? "scheduled (no GPS)" : "live GPS"}</span>`
  );
}

// Keyed by (system, trip_id): LIRR and MNR trip_id namespaces are independent, so
// trip_id alone would collide (the backend dedups by the same composite). Placed
// trains glide between their prev and next station via trainLatLng (the subway v2
// path), animated by animateTrains; GPS trains move by their reported position
// via setLatLng each poll and are never routed through trainLatLng.
const railroads = new Map(); // `${system}|${trip_id}` -> { marker, routeId, placed, latest, fState, _segId }

function railroadKey(train) {
  return `${train.system}|${train.trip_id}`;
}

// Railroad gliding reuses computeRouteSlice / trainLatLng unchanged; it differs
// from the subway path only in the geometry it looks up (railroadRouteIndex, by
// (system, route_id)) and these looser tolerances (railroad inter-station gaps
// dwarf subway ones).
const RAILROAD_SLICE_OPTS = { maxSlice: RAILROAD_ROUTE_MAX_SLICE, acceptDist: RAILROAD_ROUTE_ACCEPT_DIST };

function applyRailroads(data) {
  // Skew-corrected now, same basis as applyTrains; placed trains interpolate
  // between their prev and next station, GPS trains use their reported position.
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  const seen = new Set();
  for (const train of data) {
    const key = railroadKey(train);
    seen.add(key);
    const placed = isPlacedRailroad(train);
    const record = railroads.get(key);
    if (record) {
      if (placed) {
        // Same slice caching as applyTrains, but key geometry by (system,
        // route_id) and pass the railroad tolerances. A mid-trip route relabel
        // changes segId and re-projects onto the new route's geometry.
        const segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
        train._route =
          record._segId === segId && record.latest._route
            ? record.latest._route
            : computeRouteSlice(
                train,
                railroadRouteIndex.get(`${train.system}|${train.route_id}`),
                RAILROAD_SLICE_OPTS,
              );
        record._segId = segId;
      }
      record.latest = train;
      record.marker.setLatLng(
        placed ? trainLatLng(train, now, record.fState) : [train.latitude, train.longitude],
      );
      // Re-skin when the route color or the GPS/placed status flips (a placed
      // train can pick up a GPS position on a later poll, or lose one).
      if (record.routeId !== train.route_id || record.placed !== placed) {
        record.marker.setIcon(railroadIcon(train));
        record.routeId = train.route_id;
        record.placed = placed;
      }
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { routeId: train.route_id, placed, latest: train, fState: {} };
      if (placed) {
        newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
        train._route = computeRouteSlice(
          train,
          railroadRouteIndex.get(`${train.system}|${train.route_id}`),
          RAILROAD_SLICE_OPTS,
        );
      }
      newRecord.marker = L.marker(
        placed ? trainLatLng(train, now, newRecord.fState) : [train.latitude, train.longitude],
        { icon: railroadIcon(train) },
      )
        .bindPopup(() => railroadPopup(newRecord))
        .addTo(railroadLayer);
      railroads.set(key, newRecord);
    }
  }
  for (const [key, record] of railroads) {
    if (!seen.has(key)) {
      railroadLayer.removeLayer(record.marker);
      railroads.delete(key);
    }
  }
}

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
