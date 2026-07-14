// NYC Ferry layer (14c): route lines, clickable docks with live arrivals, and
// GPS boat markers. A plain <script> after path.js and before map.js, reading the
// shared map/layers/helpers globals (ferryRouteLines, ferryDocks, ferryBoats,
// lineRenderer, stationRenderer, bindStationPopup, and the pure helpers from
// helpers.js) exactly the way the other system files do.

/* ---------------- NYC Ferry ---------------- */

// route_id -> css color / rider-facing long name, from /api/ferry-routes; read by
// the boat icons/popups and the dock arrivals headings. pathColor validates the
// feed's bare-hex route_color (it is the generic bare-hex-to-css validator, not
// PATH-specific), with the ferry neutral fallback for a malformed or missing one.
const ferryRouteColors = new Map();
const ferryRouteNames = new Map();

// A boat's badge/icon color: its route color, or the neutral fallback for a boat
// with no route (a 14b join miss, kept on the map and shown "Unassigned").
function ferryColorFor(routeId) {
  return ferryRouteColors.get(routeId) ?? FERRY_FALLBACK_COLOR;
}

async function loadFerryRoutes() {
  let routes;
  try {
    const res = await fetch("/api/ferry-routes");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    routes = await res.json();
  } catch {
    return false;
  }
  // Same contract as the PATH/subway loaders: ferry is a single-system warmup
  // group, so a failed-warmup [] (served no-cache) always means "ask again later".
  if (!routes.length) return false;
  for (const route of routes) {
    const color = pathColor(route.color, FERRY_FALLBACK_COLOR);
    ferryRouteColors.set(route.id, color);
    if (route.name) ferryRouteNames.set(route.id, route.name);
    // Every entry of the shape list draws (the modal polyline per direction);
    // non-interactive like the PATH/AirTrain guideways so clicks fall through to
    // the dock dots that sit on the station pane above these lines.
    for (const points of route.shape) {
      L.polyline(points, {
        color,
        weight: 2.5,
        opacity: 0.5,
        interactive: false,
        renderer: lineRenderer,
      }).addTo(ferryRouteLines);
    }
  }
  return true;
}

async function loadFerryStops() {
  let stops;
  try {
    const res = await fetch("/api/ferry-stops");
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stops = await res.json();
  } catch {
    return false;
  }
  if (!stops.length) return false; // failed-warmup []: retry until the backend heals
  for (const stop of stops) {
    // Deep-cyan solid dot under a white ring, on the shared station pane/renderer
    // (click priority + cheap canvas). WHY this styling: ferry docks sit on the
    // water, but the Rockaway and Soundview docks neighbor subway/railroad stops,
    // and the subway/railroad dots are white-filled rings while PATH is a slate
    // solid, so a fourth dot needs its own read. Deep cyan belongs to no rail
    // palette and evokes water, making a dock legible at a glance, the same
    // shape-or-fill distinction the AirTrain square and the PATH inverted fill use.
    const marker = L.circleMarker([stop.lat, stop.lon], {
      radius: 4.5,
      color: "#fff",
      weight: 1.5,
      fillColor: "#0e7490",
      fillOpacity: 1,
      renderer: stationRenderer,
    });
    bindStationPopup(marker, (m) => ({
      station: stop,
      marker: m,
      body: null,
      url: `/api/ferry-arrivals/${encodeURIComponent(stop.id)}`,
      // Prepend a dock's ferry alerts, joined through the shared alertsIndex: the
      // UNION of STOP-scoped alerts (ferry, stop_id) and ROUTE-scoped alerts for
      // every route serving this dock. s.routes is the routes-per-station index the
      // backend now derives from stop_times (H5), so a route-scoped ferry alert
      // reaches the DOCK, not only the boats on that route. This is the same static
      // routes-per-station join subway and railroad stations use through
      // stationAlertsBlock; ferry keeps its own call because its arrivals shape
      // (route-name buckets) differs from the directions shape that helper reads.
      // The countdown tick, refresh, and supersession machinery are inherited from
      // bindStationPopup / openStationArrivals.
      render: (s, b) =>
        alertsBlockHtml(matchStationAlerts(alertsIndex, "ferry", s.id, s.routes ?? [])) +
        ferryArrivalsHtml(
          s,
          b,
          Date.now() / 1000 - (minClockOffset ?? 0),
          (routeId) => ferryColorFor(routeId),
        ),
    })).addTo(ferryDocks);
  }
  return true;
}

// A horizontal rounded "hull" shape: a boat reads as a boat, distinct from every
// existing marker (bus arrow/dot, subway rounded square, railroad square, PATH
// diamond, and the station rings), which matters where a Rockaway dock neighbors
// a subway stop. NO rotation: the feed reports no usable bearing (14b: always
// 0.0), so the shape is orientation-neutral rather than pretending to point
// somewhere. The docked/active state comes from a css class (see style.css): a
// STOPPED_AT boat is dimmed to read as parked, an under-way boat is full opacity.
function ferryBoatIcon(boat, color) {
  const state = ferryBoatIconState(boat.status);
  const html =
    `<svg viewBox="0 0 22 14"><rect x="1" y="3" width="20" height="8" rx="4" ` +
    `fill="${color}" stroke="#fff" stroke-width="1.5"/></svg>`;
  return L.divIcon({
    className: `ferry-marker ferry-${state}`,
    html,
    iconSize: [22, 14],
    iconAnchor: [11, 7],
  });
}

function ferryBoatPopup(record) {
  const b = record.latest;
  // Reads record.latest so a popup a rider holds open across polls always renders the
  // newest status/route, like the other systems. Prepend ROUTE-scoped ferry alerts,
  // joined on (ferry, route_id) exactly as the subway train popup joins by route; a
  // null-route boat matches nothing (matchRouteAlerts guards on a falsy route id).
  return (
    routeAlertsBlock("ferry", b.route_id) +
    ferryBoatPopupHtml(b, ferryRouteNames.get(b.route_id) || null, ferryColorFor(b.route_id))
  );
}

// Stable vehicle id -> { marker, color, iconState, latest }. Boats are the
// BUSES model, not the PATH model: 14b vehicle ids are stable across polls, so
// markers are keyed on id and moved to their reported GPS position each poll (the
// railroad GPS precedent), never rebuilt. There is no glide interpolation and no
// animateTrains entry: a boat snaps to its reported position each poll, the way
// the bus and railroad GPS markers do (a smooth inter-poll glide is a follow-up:
// it would need a zoom-event hook, since a css transition on the marker element
// makes Leaflet slide every boat across the map after a zoom).
const ferryBoatRecords = new Map();

function applyFerryBoats(data) {
  const seen = new Set();
  for (const boat of data) {
    seen.add(boat.id);
    const record = ferryBoatRecords.get(boat.id);
    if (record) {
      record.marker.setLatLng([boat.latitude, boat.longitude]);
      // Re-icon only when a VISUAL input changed: the RESOLVED route color, or the
      // docked/active state (not the raw status string, so a STOPPED_AT that stays
      // STOPPED_AT never churns the icon). Keying on the resolved color rather than
      // the raw route_id is deliberate, and the one place the ferry model diverges
      // from the buses precedent's always-available color hash: ferryColorFor reads
      // ferryRouteColors, which loadFerryRoutes fills ASYNCHRONOUSLY, so a boat first
      // seen before /api/ferry-routes resolves is created with the neutral fallback.
      // Keying on route_id would then never recolor it once the routes land (the id
      // never changed), stranding it gray; keying on the color re-icons it on the
      // first poll after the routes populate. setIcon recreates the marker element
      // (DOM churn), so it is still skipped on a poll where neither input changed.
      const iconState = ferryBoatIconState(boat.status);
      const color = ferryColorFor(boat.route_id);
      if (record.color !== color || record.iconState !== iconState) {
        record.marker.setIcon(ferryBoatIcon(boat, color));
        record.color = color;
        record.iconState = iconState;
      }
      record.latest = boat;
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const color = ferryColorFor(boat.route_id);
      const newRecord = {
        color,
        iconState: ferryBoatIconState(boat.status),
        latest: boat,
      };
      newRecord.marker = L.marker([boat.latitude, boat.longitude], {
        icon: ferryBoatIcon(boat, color),
      })
        .bindPopup(() => ferryBoatPopup(newRecord))
        .addTo(ferryBoats);
      ferryBoatRecords.set(boat.id, newRecord);
    }
  }
  // Boats gone from the feed leave the map. An EMPTY data array therefore clears
  // every boat, which is exactly what an overnight empty poll must do (they went
  // home): map.js routes a successful empty ferry poll straight here rather than
  // through the transient-blip grace the other feeds use, so 14b's server-side
  // empty-replaces / failure-retains split is preserved on the client too.
  for (const [id, record] of ferryBoatRecords) {
    if (!seen.has(id)) {
      ferryBoats.removeLayer(record.marker);
      ferryBoatRecords.delete(id);
    }
  }
}
