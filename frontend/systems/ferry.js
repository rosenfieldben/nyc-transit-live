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
    const res = await fetch("/api/ferry-routes", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
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
      /* MR4: DASHED, which is the ferry's whole signature on this map (README: "routes dashed
         weight 2; opacity .9; dashArray '6 5'", and the Key panel's own row is literally
         called "dashed ferry route"). A ferry route is not track: the dash says the service
         crosses water on no fixed way, which is the one thing a solid line of any colour
         cannot. The colour stays the feed's, so these lines need no theme registry entry. */
      L.polyline(points, {
        color,
        weight: 2,
        opacity: 0.9,
        dashArray: "6 5",
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
    const res = await fetch("/api/ferry-stops", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stops = await res.json();
  } catch {
    return false;
  }
  if (!stops.length) return false; // failed-warmup []: retry until the backend heals
  for (const stop of stops) {
    /* MR4: the design's dock (README: "circleMarker radius 4, fill #00839c, paper stroke
       1.5"). Deep cyan under a paper ring, on the shared station pane and renderer for click
       priority and a cheap canvas, which is unchanged.

       THE CYAN MOVES FROM #0e7490 TO #00839c AND THAT SETTLES A DISAGREEMENT rather than
       starting one. The ferry was already two cyans: the feed strip's tick has been #00839c
       since MR1 (helpers.js FEEDS) and the Key panel's dock glyph was #0e7490, so the strip
       and the legend pointed at different colours for one family. The design names #00839c,
       the strip already draws it, and the dock and the Key glyph now join them.

       AND THE RING IS A TOKEN, which is what puts this dot in the theme registry below. A
       white ring is a light halo on a dark map: this is one of the two marks ledger finding
       G15 measured at 2.63 against the dark surface, and paper resolves to the dark theme's
       own surface colour instead. That is the whole reason R2 held the toggle back until the
       marks had their casings, and the dock is one of the last of them. */
    const marker = L.circleMarker([stop.lat, stop.lon], {
      ...ferryDockStyle(paperColor()),
      renderer: stationRenderer,
    });
    // The dock's name, which the design asks for and no dock has ever had. Its own class, so
    // no count over `.stn-label` can mistake a dock for a subway station (shared.js says why).
    bindFerryDockLabel(marker, stop.name ?? stop.id);
    // Built once, used by the popup descriptor and the A1 registry alike.
    const arrivalsUrl = `/api/ferry-arrivals/${encodeURIComponent(stop.id)}`;
    bindStationPopup(marker, (m) => ({
      station: stop,
      marker: m,
      body: null,
      url: arrivalsUrl,
      // Prepend a dock's ferry alerts: the UNION of STOP-scoped alerts (ferry,
      // stop_id) and ROUTE-scoped alerts for every route serving this dock. s.routes
      // is the routes-per-station index the backend derives from stop_times (H5), so
      // a route-scoped ferry alert reaches the DOCK, not only the boats on that
      // route. The countdown tick, refresh, and supersession machinery are inherited
      // from bindStationPopup / openStationArrivals.
      //
      // THIS USED TO BE A HAND-WRITTEN COPY OF stationAlertsBlock, because that
      // helper read route ids out of body.directions and a ferry arrivals body
      // buckets by route NAME instead. The copy paid for the difference by dropping
      // the arrivals side of the union entirely: a dock whose static routes list was
      // empty or behind showed no route-scoped alert even for a route with a boat
      // inbound. F11 taught the helper all three body shapes, so the copy is gone and
      // this renders the same answer the panel does. The node pins in
      // frontend/stationalerts.test.js hold the two outputs identical.
      render: (s, b) =>
        stationAlertsBlock("ferry", s, b) + // includes the R1 freshness marker
        ferryArrivalsHtml(
          s,
          b,
          Date.now() / 1000 - (minClockOffset ?? 0),
          (routeId) => ferryColorFor(routeId),
        ),
    })).addTo(ferryDocks);
    registerStation({
      key: `ferry|${stop.id}`,
      kind: "ferry",
      systemLabel: "Ferry",
      noun: "boat",
      id: stop.id,
      name: stop.name ?? stop.id,
      lat: stop.lat,
      lon: stop.lon,
      routes: stop.routes ?? [],
      // THE ONE SYSTEM WITH REAL ACCESSIBILITY DATA. Ferry stops carry
      // wheelchair_boarding from GTFS; no other stops endpoint does, so the panel
      // shows the indicator here and stays silent elsewhere rather than implying
      // the others are inaccessible.
      wheelchair: stop.wheelchair === true,
      arrivalsUrl,
      marker,
      layer: ferryDocks,
      nameFor: (routeId) => ferryRouteNames.get(routeId) || null,
    });
  }
  return true;
}

// A horizontal rounded "hull" shape: a boat reads as a boat, distinct from every
// existing marker (bus arrow/dot, subway rounded square, railroad square, PATH
// diamond, and the station rings), which matters where a Rockaway dock neighbors
// a subway stop. NO rotation: the feed reports no usable bearing (14b: always
// 0.0), so the shape is orientation-neutral rather than pretending to point
// somewhere. The docked/active state rides on a css class as a state MARKER, while
// the dimming that goes with it is a marker opacity (ferryBaseOpacity): a STOPPED_AT
// boat reads as parked, an under-way boat is full opacity.
/* MR4: the dock dots are the ferry's one canvas mark drawn from a token, so they are the
   family's one registry entry. The dashed route lines take the feed's own colour and are not
   here; the hulls are divIcons whose stroke is `var(--paper)` and follow the cascade. */
registerCanvasFamily("ferry docks", ({ paper }) => {
  const style = ferryDockStyle(paper);
  for (const layer of ferryDocks.getLayers()) layer.setStyle(style);
});

function ferryBoatIcon(boat, color) {
  /* MR4: A HULL, AND THE PARAGRAPH ABOVE FINALLY MEANS IT. This file has always argued that
     a boat should read as a boat beside a subway square, a railroad square, a PATH diamond
     and a bus arrow, and then drew a rounded rectangle. The design's path is a trapezoid
     with a flat deck and a tapered bottom, which is that argument carried out. */
  return ferryBoatIconFor(color, ferryBoatIconState(boat.status));
}

// A2 FOLLOWUP, DELIBERATELY NOT DONE HERE: a docked boat gets no "Also here" link.
//
// A boat sitting at its dock covers the dock dot exactly as a placed railroad train
// covers its station (markerPane z 600 over stationPane z 450), and the cross-link at
// crossLinkHtml in shared.js is the resolution for a measured position. It is not used
// here because THE PAYLOAD NAMES NO DOCK. FerryBoat carries `status` but no stop id,
// and that is not an omission in this repo's decoder: the captured upstream
// VehiclePositions feeds carry stop_id on 0 of 28 vehicles while 14 of them report
// STOPPED_AT. NYC Ferry says a boat is docked and never says where.
//
// Guessing the nearest dock is forbidden, and rightly: a wrong "Also here" hands a
// rider confidently incorrect arrivals with nothing on screen to contradict them.
//
// THE HONEST UNBLOCK IS A BACKEND DERIVATION, out of A2's scope by the zero-backend
// rule. A STOPPED_AT boat's dock is the stop on its own trip whose dwell window
// contains now: arrival in the past, departure in the future. The TripUpdates feed
// already carries both times per stop (14b decodes exactly that for the dock arrivals
// endpoint), so the dock falls out of payload semantics with no distance math at all.
// Until then, dock arrivals remain reachable by name through the A1 station panel.
function ferryBoatPopup(record) {
  const b = record.latest;
  // Reads record.latest so a popup a rider holds open across polls always renders the
  // newest status/route, like the other systems. Prepend ROUTE-scoped ferry alerts,
  // joined on (ferry, route_id) exactly as the subway train popup joins by route; a
  // null-route boat matches nothing (matchRouteAlerts guards on a falsy route id).
  const position = ferryPosition(b);
  return (
    routeAlertsBlock("ferry", b.route_id) +
    ferryBoatPopupHtml(b, ferryRouteNames.get(b.route_id) || null, ferryColorFor(b.route_id), position) +
    // C2: single-feed source, synthesized system, same age line as every other
    // vehicle popup, unless the boat's own words already stated an age that old.
    vehicleStaleLine(systemAgeOf("ferry", "ferry"), position)
  );
}

// The words one boat carries about its position (6.3): GPS, dated by its own
// vehicle.timestamp (served as observed_at, and as updated_at before that), read
// against the ferry's one system. A fresh fix says nothing, as it always has.
function ferryPosition(boat, now = correctedNow()) {
  return vehiclePosition("ferry", ["ferry"], boat, now);
}

// A boat's accessible name at `now`, the one composition the apply path and the stale
// sweep both write (railroadMarkerName in railroad.js says why the sweep writes it too).
function ferryMarkerName(boat, now = correctedNow()) {
  return ferryBoatName(boat, ferryRouteNames.get(boat.route_id), ferryPosition(boat, now));
}

// A boat's resting opacity: dimmed while it is parked at a dock. This USED TO be a
// css class rule (.ferry-docked), and C2 had to move it here, because the staleness
// dimming writes an inline opacity on the same element and an inline value beats a
// stylesheet one: every docked boat would have been silently un-dimmed. One opacity
// authority per marker, and the two reasons to dim compound (see markerOpacity).
function ferryBaseOpacity(boat) {
  return ferryBoatIconState(boat.status) === "docked" ? FERRY_DOCKED_OPACITY : 1;
}

// Re-dim every boat from the larger of its source's age and its own fix's, compounded
// with its own docked state (C2, 6.3).
staleTreatments.push(() => {
  const now = correctedNow();
  const age = systemAgeOf("ferry", "ferry");
  for (const record of ferryBoatRecords.values()) {
    dimMarker(record.marker, vehicleMarkerAge("ferry", age, record.latest, now), ferryBaseOpacity(record.latest));
    setMarkerName(record.marker, ferryMarkerName(record.latest, now));
  }
});

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
      // THE LABEL TRACKS THE DATA: a boat's status is the field that changes most (at
      // dock, arriving, under way) and it is the one a rider is listening for. Like
      // the re-icon above, this reads the RESOLVED route name, so a boat named before
      // the route table landed gets its real route once it does.
      setMarkerName(record.marker, ferryMarkerName(boat));
      // Re-applied every poll, not only when the icon changes: a boat that docks or
      // departs changes its resting opacity, and its feed, or since 6.3 its own fix, may
      // have gone stale.
      dimMarker(record.marker, vehicleMarkerAge("ferry", systemAgeOf("ferry", "ferry"), boat), ferryBaseOpacity(boat));
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const color = ferryColorFor(boat.route_id);
      const newRecord = {
        color,
        iconState: ferryBoatIconState(boat.status),
        latest: boat,
      };
      newRecord.marker = labeledMarker([boat.latitude, boat.longitude], {
        icon: ferryBoatIcon(boat, color),
        // Dim on the first frame, for staleness (the feed's or the fix's) and/or for
        // being docked.
        opacity: markerOpacity(
          vehicleMarkerAge("ferry", systemAgeOf("ferry", "ferry"), boat),
          ferryBaseOpacity(boat),
        ),
      }, ferryMarkerName(boat))
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
