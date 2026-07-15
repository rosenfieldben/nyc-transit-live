// AirTrain JFK layer (static-only, no realtime): guideway lines, stations, and
// the scheduled-headway popup. Shared global scope.

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
    const res = await fetch("/api/airtrain", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
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

