const POLL_INTERVAL_MS = 15000;

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
      busRouteNotes.set(bus.route_id, {
        message: body?.detail ?? `Route line unavailable (HTTP ${res.status})`,
        at: Date.now(),
      });
      refreshOpenPopup(bus.id);
      return;
    }
    geometry = await res.json();
  } catch {
    if (requestId !== busRouteSeq) return;
    pendingBusId = null;
    busRouteNotes.set(bus.route_id, {
      message: "Route line unavailable (network error)",
      at: Date.now(),
    });
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
        const svg = record.marker.getElement()?.firstElementChild;
        if (svg) {
          svg.style.transform = `rotate(${Number(bus.bearing)}deg)`;
          // Keep the stored html current so Leaflet recreates the element
          // correctly if the layer is toggled off and back on.
          record.marker.options.icon.options.html = busIcon(bus).options.html;
        } else {
          record.marker.setIcon(busIcon(bus)); // not in the DOM (layer hidden)
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

async function loadRouteLines() {
  let routes;
  try {
    const res = await fetch("/api/subway-routes");
    if (!res.ok) return;
    routes = await res.json();
  } catch {
    return;
  }
  for (const route of routes) {
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
}

/* ----- Subway stations + live arrivals (click a station for countdowns) ----- */

// Canvas-rendered so ~470 circle markers stay cheap and hit-testable; on its
// own pane (above the route-line canvas) so station clicks land here.
const stationRenderer = L.canvas({ padding: 0.5, pane: "stationPane" });

// One station popup is open at a time (Leaflet closes others). A request token
// guards against a slow fetch landing after the user clicked a different
// station; a 1s timer ticks countdowns down from absolute arrival timestamps
// without re-fetching.
let stationSeq = 0;
let stationTimer = null;
let openStation = null; // { station, marker } while a popup is open

function arrivalsHtml(station, body) {
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

async function openStationArrivals(station, marker) {
  const seq = ++stationSeq;
  marker.setPopupContent(`<b>${esc(station.name ?? station.id)}</b><br>Loading arrivals…`);
  let body;
  try {
    const res = await fetch(`/api/subway-arrivals/${encodeURIComponent(station.id)}`);
    if (seq !== stationSeq) return; // superseded by another station click
    if (!res.ok) {
      const err = await res.json().catch(() => null);
      marker.setPopupContent(
        stationError(station, err?.detail ?? `Arrivals unavailable (HTTP ${res.status})`),
      );
      return;
    }
    body = await res.json();
  } catch {
    if (seq !== stationSeq) return;
    marker.setPopupContent(stationError(station, "Arrivals unavailable (network error)"));
    return;
  }
  if (seq !== stationSeq) return;
  noteClockOffset(body.fetched_at); // keep the skew baseline fresh

  const render = () => {
    if (marker.isPopupOpen()) marker.setPopupContent(arrivalsHtml(station, body));
  };
  render();
  // Don't start (or replace) the tick if the popup closed while we fetched —
  // popupclose bumps stationSeq, but a close that lands exactly here would
  // otherwise strand an interval that nothing clears.
  if (!marker.isPopupOpen()) return;
  clearInterval(stationTimer);
  stationTimer = setInterval(render, 1000); // tick countdowns without re-fetch
}

async function loadStations() {
  let stations;
  try {
    const res = await fetch("/api/subway-stops");
    if (!res.ok) return;
    stations = await res.json();
  } catch {
    return;
  }
  for (const station of stations) {
    L.circleMarker([station.lat, station.lon], {
      radius: 4,
      color: "#333",
      weight: 1.5,
      fillColor: "#fff",
      fillOpacity: 1,
      renderer: stationRenderer,
    })
      .bindPopup("", { minWidth: 170 })
      .on("popupopen", function () {
        openStation = { station, marker: this };
        openStationArrivals(station, this);
      })
      .on("popupclose", function () {
        stationSeq++; // invalidate any in-flight arrivals fetch for this popup
        clearInterval(stationTimer);
        stationTimer = null;
        if (openStation?.marker === this) openStation = null;
      })
      .addTo(stationLayer);
  }
}

const trains = new Map(); // trip id -> { marker, routeId, latest }

function applyTrains(data) {
  // Skew-corrected now, same basis as arrivalsHtml; trainLatLng interpolates
  // each train between its prev and next station (static fallback otherwise).
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  const seen = new Set();
  for (const train of data) {
    seen.add(train.trip_id);
    const record = trains.get(train.trip_id);
    if (record) {
      record.latest = train;
      record.marker.setLatLng(trainLatLng(train, now));
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(trainIcon(train));
        record.routeId = train.route_id;
      }
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { routeId: train.route_id, latest: train };
      newRecord.marker = L.marker(trainLatLng(train, now), { icon: trainIcon(train) })
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
  if (ts - lastTrainTick >= TRAIN_TICK_MS && map.hasLayer(subwayLayer)) {
    lastTrainTick = ts;
    const now = Date.now() / 1000 - (minClockOffset ?? 0);
    for (const record of trains.values()) {
      record.marker.setLatLng(trainLatLng(record.latest, now));
    }
  }
  requestAnimationFrame(animateTrains);
}

/* ---------------- Polling ---------------- */

const sources = {
  buses: { url: "/api/buses", apply: applyBuses, label: "buses", count: 0, error: null, fetchedAt: null, feedTimestamp: null },
  subways: { url: "/api/subways", apply: applyTrains, label: "trains", count: 0, error: null, fetchedAt: null, feedTimestamp: null },
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
    const data = body.data ?? [];
    if (data.length === 0) {
      // Temporarily empty feed: keep last known markers on screen.
      source.error = "feed empty, showing last known";
      return;
    }
    source.apply(data);
    source.count = data.length;
    source.error = null;
  } catch (err) {
    // Keep last known markers on screen; just surface the problem.
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

  // Refresh the open station's arrivals so the train list (not just the
  // countdowns) stays current on the same ~15s cadence as the markers.
  if (openStation) openStationArrivals(openStation.station, openStation.marker);
}

loadRouteLines();
loadStations();
refreshAll();
setInterval(refreshAll, POLL_INTERVAL_MS);
requestAnimationFrame(animateTrains); // glide trains between polls
