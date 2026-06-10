const POLL_INTERVAL_MS = 15000;

const map = L.map("map").setView([40.7128, -74.006], 12);

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// Buses and subways live in separate layer groups so they toggle independently.
const busLayer = L.layerGroup().addTo(map);
const subwayLayer = L.layerGroup().addTo(map);

function bindToggle(checkboxId, layer) {
  const box = document.getElementById(checkboxId);
  const sync = () => {
    if (box.checked) map.addLayer(layer);
    else map.removeLayer(layer);
  };
  box.addEventListener("change", sync);
  sync(); // some browsers restore checkbox state across reloads without firing change
}
bindToggle("toggle-buses", busLayer);
bindToggle("toggle-subways", subwayLayer);

const statusEl = document.getElementById("status");

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

// Feed data goes into HTML popups/icons — escape it.
function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/* ---------------- Buses ---------------- */

// Deterministic color per bus route: hash the route id onto the hue wheel.
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
  return (
    `<b style="color:${routeColor(b.route_id)}">${esc(b.route_id ?? "Unknown route")}</b>` +
    `<br>Bus ${esc(b.id)}<br>Heading: ${heading}`
  );
}

const buses = new Map(); // bus id -> { marker, routeId, bearing, latest }

function applyBuses(data) {
  const seen = new Set();
  for (const bus of data) {
    seen.add(bus.id);
    const record = buses.get(bus.id);
    if (record) {
      record.marker.setLatLng([bus.latitude, bus.longitude]);
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

const trains = new Map(); // trip id -> { marker, routeId, latest }

function applyTrains(data) {
  const seen = new Set();
  for (const train of data) {
    seen.add(train.trip_id);
    const record = trains.get(train.trip_id);
    if (record) {
      record.marker.setLatLng([train.latitude, train.longitude]);
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(trainIcon(train));
        record.routeId = train.route_id;
      }
      record.latest = train;
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { routeId: train.route_id, latest: train };
      newRecord.marker = L.marker([train.latitude, train.longitude], { icon: trainIcon(train) })
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

/* ---------------- Polling ---------------- */

const sources = {
  buses: { url: "/api/buses", apply: applyBuses, label: "buses", count: 0, error: null },
  subways: { url: "/api/subways", apply: applyTrains, label: "trains", count: 0, error: null },
};

async function refreshSource(source) {
  try {
    const res = await fetch(source.url);
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error(body?.detail ?? `HTTP ${res.status}`);
    }
    const data = await res.json();
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
  const errors = Object.values(sources)
    .filter((s) => s.error)
    .map((s) => `${s.label}: ${s.error}`);
  const now = new Date().toLocaleTimeString();
  if (errors.length) setStatus(`${counts} · ${now} — ${errors.join("; ")}`, true);
  else setStatus(`${counts} · updated ${now}`);
}

refreshAll();
setInterval(refreshAll, POLL_INTERVAL_MS);
