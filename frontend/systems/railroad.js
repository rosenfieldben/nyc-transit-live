// Railroad (LIRR + Metro-North) layer: GPS and placed train markers, route
// lines, station arrivals, and the per-poll apply. Shared global scope.

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

