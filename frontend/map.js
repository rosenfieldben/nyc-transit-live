const POLL_INTERVAL_MS = 15000;

const map = L.map("map").setView([40.7128, -74.006], 12);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const statusEl = document.getElementById("status");

// One record per vehicle id: { marker, routeId, bearing, latest }.
// `latest` holds the most recent feed data so popups always show current info.
const buses = new Map();

// Deterministic color per route: hash the route id onto the hue wheel.
function routeColor(routeId) {
  if (!routeId) return "#777777";
  let h = 0;
  for (const c of routeId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360}, 75%, 40%)`;
}

// Arrow rotated to the bearing (GTFS bearing = degrees clockwise from north,
// which matches CSS rotate with an up-pointing arrow). Dot when bearing is null.
function busIcon(bus) {
  const color = routeColor(bus.route_id);
  const html =
    bus.bearing != null
      ? `<svg viewBox="0 0 20 20" style="transform: rotate(${bus.bearing}deg)">
           <path d="M10 2 L16 17 L10 13 L4 17 Z" fill="${color}" stroke="#fff" stroke-width="1.2"/>
         </svg>`
      : `<svg viewBox="0 0 20 20">
           <circle cx="10" cy="10" r="5.5" fill="${color}" stroke="#fff" stroke-width="1.5"/>
         </svg>`;
  return L.divIcon({ className: "bus-marker", html, iconSize: [20, 20], iconAnchor: [10, 10] });
}

function popupContent(record) {
  const b = record.latest;
  const heading = b.bearing != null ? `${Math.round(b.bearing)}°` : "unknown";
  return (
    `<b style="color:${routeColor(b.route_id)}">${b.route_id ?? "Unknown route"}</b>` +
    `<br>Bus ${b.id}<br>Heading: ${heading}`
  );
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

async function refreshBuses() {
  let data;
  try {
    const res = await fetch("/api/buses");
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail ?? `HTTP ${res.status}`);
    }
    data = await res.json();
  } catch (err) {
    // Keep last known markers on screen; just surface the problem.
    setStatus(`Error: ${err.message} — showing last known positions`, true);
    return;
  }

  const now = new Date().toLocaleTimeString();

  if (data.length === 0) {
    // Temporarily empty feed: don't wipe the map, just say so.
    setStatus(`Feed returned no buses at ${now} — showing last known positions`, true);
    return;
  }

  const seen = new Set();
  for (const bus of data) {
    seen.add(bus.id);
    const record = buses.get(bus.id);
    if (record) {
      record.marker.setLatLng([bus.latitude, bus.longitude]);
      // Only rebuild the icon when its appearance actually changed.
      if (record.bearing !== bus.bearing || record.routeId !== bus.route_id) {
        record.marker.setIcon(busIcon(bus));
        record.bearing = bus.bearing;
        record.routeId = bus.route_id;
      }
      record.latest = bus;
    } else {
      const newRecord = { bearing: bus.bearing, routeId: bus.route_id, latest: bus };
      newRecord.marker = L.marker([bus.latitude, bus.longitude], { icon: busIcon(bus) })
        .bindPopup(() => popupContent(newRecord))
        .addTo(map);
      buses.set(bus.id, newRecord);
    }
  }

  // Drop buses that left the feed.
  for (const [id, record] of buses) {
    if (!seen.has(id)) {
      map.removeLayer(record.marker);
      buses.delete(id);
    }
  }

  setStatus(`${data.length.toLocaleString()} buses · updated ${now}`);
}

refreshBuses();
setInterval(refreshBuses, POLL_INTERVAL_MS);
