// PATH layer: route lines, parent-station markers, gliding train markers, and
// the per-poll apply. Shared global scope.

/* ---------------- PATH ---------------- */

// route_id -> css color / rider-facing name, from /api/path-routes; read by the
// PATH train icons/popups and the station arrivals badges. pathColor validates
// the feed's bare-hex route_color, so every stored value is a safe css color.
const pathRouteColors = new Map();
const pathRouteNames = new Map();

// route_id -> [{ points, cum }], the geometry PATH trains glide along: the
// same interpolation index structure the subway uses (routeIndex), built from
// the same shape list the polylines below draw. Populated by loadPathRoutes
// and read by applyPath; empty until the static loader lands, during which a
// train with anchors glides the straight chord (the subway's fallback too).
const pathRouteIndex = new Map();

// PATH's inter-station gaps outgrow the subway cap (Journal Square to
// Harrison is ~0.071 isotropic) but never reach railroad branch scale, so the
// glide uses PATH's own tolerances from helpers.js.
const PATH_SLICE_OPTS = { maxSlice: PATH_ROUTE_MAX_SLICE, acceptDist: PATH_ROUTE_ACCEPT_DIST };

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
    // The same shapes feed the glide index (13d): one build per load, so the
    // interpolation and the drawn line can never disagree about geometry.
    pathRouteIndex.set(
      route.id,
      route.shape.map((points) => ({ points, cum: polylineCumLengths(points) })),
    );
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

function pathTrainPopup(record) {
  const t = record.latest;
  // No routeAlertsBlock prepend (the subway trainPopup's alert join): PATH
  // has no alerts feed. Reads record.latest so the popup a rider holds open
  // across polls always renders the newest placement, like the other systems.
  return pathTrainPopupHtml(
    t,
    pathRouteNames.get(t.route_id) || null,
    pathRouteColors.get(t.route_id) ?? PATH_FALLBACK_COLOR,
  );
}

// Stable backend id -> { marker, routeId, latest, fState, _segId }. 13c had no
// such map on purpose: only raw bridge trip ids existed then, and they churn
// 100% across upstream refreshes, so applyPath rebuilt the whole layer every
// poll rather than key markers on them (phantom add/remove churn, popups dying
// every poll, all documented here at the time). 13d's backend now owns a
// synthetic cross-poll identity (feeds.match_path_identities) and serves its
// stable `id` instead of the hash, which is what makes keyed diffing safe:
// markers persist, popups survive polls, and anchored trains glide.
const pathTrainRecords = new Map();

function applyPath(data) {
  // Skew-corrected now, the same basis as the other apply* paths; anchored
  // trains interpolate prev -> next via trainLatLng, anchorless trains sit
  // placed at their station (trainLatLng's own fallback), exactly the payload
  // contract /api/path documents.
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  const seen = new Set();
  for (const train of data) {
    seen.add(train.id);
    const record = pathTrainRecords.get(train.id);
    if (record) {
      // Same slice caching as the subway/railroad paths: recompute only when
      // the (route, anchor, next stop) segment changes. computePathRouteSlice
      // (not computeRouteSlice) because PATH keeps twin direction polylines
      // that must never split a segment between them.
      const segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route =
        record._segId === segId && record.latest._route
          ? record.latest._route
          : computePathRouteSlice(train, pathRouteIndex.get(train.route_id), PATH_SLICE_OPTS);
      record._segId = segId;
      record.latest = train;
      record.marker.setLatLng(trainLatLng(train, now, record.fState));
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(pathIcon(train));
        record.routeId = train.route_id;
      }
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { routeId: train.route_id, latest: train, fState: {} };
      newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route = computePathRouteSlice(train, pathRouteIndex.get(train.route_id), PATH_SLICE_OPTS);
      newRecord.marker = L.marker(trainLatLng(train, now, newRecord.fState), {
        icon: pathIcon(train),
      })
        .bindPopup(() => pathTrainPopup(newRecord))
        .addTo(pathTrains);
      pathTrainRecords.set(train.id, newRecord);
    }
  }
  // Identities the backend expired (terminal arrivals) leave the map; the
  // matcher's stability guarantee is what keeps this sweep from ever churning
  // a train that merely changed bridge hashes.
  for (const [id, record] of pathTrainRecords) {
    if (!seen.has(id)) {
      pathTrains.removeLayer(record.marker);
      pathTrainRecords.delete(id);
    }
  }
}

