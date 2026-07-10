// Bus layer: markers, the on-demand route line, and the per-poll apply. A plain
// <script> after systems/shared.js; reads the shared map/layers/helpers globals.

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

