const POLL_INTERVAL_MS = 15000;

const map = L.map("map").setView([40.7128, -74.006], 12);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const statusEl = document.getElementById("status");

// One marker per vehicle id, updated in place between polls.
const markers = new Map();

async function refreshBuses() {
  let buses;
  try {
    const res = await fetch("/api/buses");
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail ?? `HTTP ${res.status}`);
    }
    buses = await res.json();
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
    statusEl.classList.add("error");
    return; // keep last known markers on screen
  }

  const seen = new Set();
  for (const bus of buses) {
    seen.add(bus.id);
    const existing = markers.get(bus.id);
    if (existing) {
      existing.setLatLng([bus.latitude, bus.longitude]);
    } else {
      const marker = L.circleMarker([bus.latitude, bus.longitude], {
        radius: 5,
        weight: 1,
        color: "#ffffff",
        fillColor: "#1d4ed8",
        fillOpacity: 0.9,
      })
        .bindPopup(() => `<b>${bus.route_id ?? "Unknown route"}</b><br>Bus ${bus.id}`)
        .addTo(map);
      markers.set(bus.id, marker);
    }
  }

  // Drop buses that left the feed.
  for (const [id, marker] of markers) {
    if (!seen.has(id)) {
      map.removeLayer(marker);
      markers.delete(id);
    }
  }

  statusEl.textContent = `${buses.length} buses · updated ${new Date().toLocaleTimeString()}`;
  statusEl.classList.remove("error");
}

refreshBuses();
setInterval(refreshBuses, POLL_INTERVAL_MS);
