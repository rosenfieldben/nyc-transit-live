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
    const res = await fetch("/api/path-routes", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
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
      /* MR4: the design's weight 3.5 at full opacity, round caps, NO CASING (README). PATH
         is the one family the design gives no paper casing, which is why it needs no theme
         registry entry for its lines: the colour is the feed's own and a theme swap does not
         move it. The caps are stated rather than left to Leaflet's default, which happens to
         be round, so the drawn mark and the written design say the same thing. */
      L.polyline(points, {
        color,
        weight: 3.5,
        opacity: 1,
        lineCap: "round",
        lineJoin: "round",
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
    const res = await fetch("/api/path-stops", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stations = await res.json();
  } catch {
    return false;
  }
  if (!stations.length) return false; // failed-warmup []: retry until the backend heals
  for (const station of stations) {
    /* MR4: THE SUBWAY'S LOCAL DOT, which is the operator's ruling and the design's word
       ("Stations: subway 'local' dot style"), drawn through stationMarkStyle so there is one
       expression of what a local dot is rather than a second copy of its radius and fill.
       PATH is a subway-style system and keeps a circle; the square still means regional rail.

       WHAT THIS REPLACES, AND THE COST, recorded rather than quietly dropped. The mark was a
       slate-blue disc under a white ring, and the paragraph that stood here argued for it:
       PATH stations sit among subway stations in Manhattan (33rd St, WTC, 14th St), so an
       identical mark makes the mode illegible at a glance where the two coincide. That cost
       is real and is now paid: a PATH local dot and a subway local dot are the same mark.
       The station's IDENTITY is still reachable (its popup, its panel entry and its accessible
       name all say PATH), and the design's answer to "which mode is this" on this map is the
       LINE under the dot rather than the dot itself. Carried to the operator as a finding.

       ROUTES: [] ON PURPOSE, so stationMarkStyle gives the LOCAL form. A PATH station is not
       a subway transfer station and must never take the hub ring, which isTransferStation
       decides from subway trunks it has no business being asked about.

       AND THE FILL IS A TOKEN NOW, so this dot joins the canvas families the theme swap has
       to reach (registerCanvasFamily below). That is the trade the local dot makes: it loses
       a literal that was theme-blind and gains one that is not. */
    const marker = L.circleMarker([station.lat, station.lon], {
      ...stationMarkStyle([], inkColor(), paperColor()),
      renderer: stationRenderer,
    });
    // Built once, used by the popup descriptor and the A1 registry alike.
    const arrivalsUrl = `/api/path-arrivals/${encodeURIComponent(station.id)}`;
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url: arrivalsUrl,
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
          // No title mark: a PATH station is a canvas circle with no icon string to borrow.
          "",
          // R3: the routes calling here, as the diamonds the map draws for them, in the published
          // colour this same call site already resolves for the row badges.
          (routeId) => ({
            svg: pathDiamondSvg(pathRouteColors.get(routeId) ?? PATH_FALLBACK_COLOR),
            name: pathRouteNames.get(routeId) || routeId,
          }),
        ),
    })).addTo(pathStations);
    registerStation({
      key: `PATH|${station.id}`,
      kind: "path",
      systemLabel: "PATH",
      noun: "train",
      id: station.id,
      name: station.name ?? station.id,
      lat: station.lat,
      lon: station.lon,
      routes: station.routes ?? [],
      wheelchair: false, // the PATH stops endpoint carries no accessibility field
      arrivalsUrl,
      marker,
      layer: pathStations,
      // Same route-name resolution the popup renderer uses, so the panel's
      // sentences say "Newark - World Trade Center" rather than "862".
      nameFor: (routeId) => pathRouteNames.get(routeId) || null,
      colorFor: (routeId) => pathRouteColors.get(routeId) ?? PATH_FALLBACK_COLOR,
    });
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
/* MR4: PATH's station dots are the family's one canvas mark drawn from a token, so they are
   the family's one theme registry entry. The lines are not here on purpose: they take the
   feed's own route colour and the design gives them no casing, so a theme swap does not move
   them.

   THE DRAW PATH AND THE REPAINT CALL THE SAME FUNCTION. stationMarkStyle([], ink, paper) is
   what loadPathStops drew with and what this hands to setStyle, so "what colour is a PATH
   dot" has one expression rather than two that can drift. */
registerCanvasFamily("path stations", ({ paper, ink }) => {
  const style = stationMarkStyle([], ink, paper);
  for (const layer of pathStations.getLayers()) layer.setStyle(style);
});

function pathIcon(train) {
  // MR4: the design's diamond, built in helpers.js so node can read it, and wrapped in
  // shared.js beside the other three families' wrappers. The anchor and the box are
  // unchanged; what changed is the path, the stroke width and the stroke becoming a token.
  return pathTrainIcon(pathRouteColors.get(train.route_id) ?? PATH_FALLBACK_COLOR);
}

function pathTrainPopup(record) {
  const t = record.latest;
  const position = pathPosition(t);
  // No routeAlertsBlock prepend (the subway trainPopup's alert join): PATH
  // has no alerts feed. Reads record.latest so the popup a rider holds open
  // across polls always renders the newest placement, like the other systems.
  return (
    pathTrainPopupHtml(
      t,
      pathRouteNames.get(t.route_id) || null,
      pathRouteColors.get(t.route_id) ?? PATH_FALLBACK_COLOR,
      position,
      // MR5: the surface the popup actually prints on, so readableInk walks the head's colour
      // against it rather than against the white a Leaflet popup used to be.
      popupSurfaceColor(),
      // And the mark this train is drawn with, off its own marker, at the title's size.
      popupMarkHtml(markerMarkHtml(record.marker)),
    ) +
    // C2 restyled as MR5's footer (ruling Q2): PATH is single-feed, so its system is the
    // synthesized one named after the source (ingestSystems). It gets the SAME footer as the
    // aggregate systems rather than being exempt from staleness for lacking a systems block, with
    // the words withheld where the position's own already stated an age that old.
    popupFreshLine(pathSystemAge(), position)
  );
}

// PATH's one system. Wrapped in a function rather than inlined at four call sites so
// the "single-feed source still goes through the per-system path" decision is stated
// in one place.
function pathSystemAge() {
  return systemAgeOf("path", "path");
}

function pathSystemStaleAt() {
  return systemStaleAtOf("path", "path");
}

// The words one PATH train carries about its position (6.3): `placed`, dated by its own
// trip update (section 3.3), read against PATH's one system.
function pathPosition(train, now = correctedNow()) {
  return vehiclePosition("path", ["path"], train, now);
}

// The clock a PATH train glides by: the earlier of PATH's freeze deadline and its own
// trip update's (glideDeadline), so a train whose prediction stopped moving holds still
// in a feed whose other trains are current.
function pathGlideAt(train, now = correctedNow()) {
  return glideClock(now, glideDeadline(pathSystemStaleAt(), train));
}

// A PATH train's accessible name at `now`, the one composition the apply path and the
// stale sweep both write (railroadMarkerName in railroad.js says why the sweep writes it).
function pathMarkerName(train, now = correctedNow()) {
  return pathTrainName(train, pathRouteNames.get(train.route_id), pathPosition(train, now));
}

// Re-dim every PATH train from the larger of its source's age and its own trip
// update's (C2, 6.3).
staleTreatments.push(() => {
  const now = correctedNow();
  const age = pathSystemAge();
  for (const record of pathTrainRecords.values()) {
    dimMarker(record.marker, vehicleMarkerAge("path", age, record.latest, now));
    setMarkerName(record.marker, pathMarkerName(record.latest, now));
  }
});

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
      // THE LABEL TRACKS THE DATA, AND IS NOT GATED ON route_id CHANGING. The re-icon
      // below is gated that way, and the step-1 inventory proved what that costs: when
      // the route table loads late, a diamond keeps the fallback colour permanently
      // because route_id never changed. A name gated the same way would strand
      // "PATH route 862" forever after the real route name arrived. Recomputed every
      // poll instead, which is what ferry.js already does for its colour.
      setMarkerName(record.marker, pathMarkerName(train, now));
      // Frozen glide clock while the feed is stale, so an anchored train stops
      // advancing along its route instead of dead-reckoning on a dead feed (C2), and
      // since 6.3 while its own trip update is past OBS_FRESH_S, however healthy PATH is.
      record.marker.setLatLng(trainLatLng(train, pathGlideAt(train, now), record.fState));
      dimMarker(record.marker, vehicleMarkerAge("path", pathSystemAge(), train, now));
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(pathIcon(train));
        record.routeId = train.route_id;
      }
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = { routeId: train.route_id, latest: train, fState: {} };
      newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route = computePathRouteSlice(train, pathRouteIndex.get(train.route_id), PATH_SLICE_OPTS);
      newRecord.marker = labeledMarker(
        trainLatLng(train, pathGlideAt(train, now), newRecord.fState),
        {
          icon: pathIcon(train),
          // Dim on the first frame, as elsewhere, from the system's age or the train's own.
          opacity: markerOpacity(vehicleMarkerAge("path", pathSystemAge(), train, now)),
        },
        pathMarkerName(train, now),
      )
        .bindPopup(() => pathTrainPopup(newRecord), POPUP_OPTIONS)
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

