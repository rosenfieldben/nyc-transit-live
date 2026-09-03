// NJ Transit Rail layer: route lines, station markers, placed train markers, and
// the per-poll apply. Shared global scope.
//
// ITS OWN FILE RATHER THAN A FOURTH BRANCH IN railroad.js. NJ Transit is a
// commuter railroad and the temptation to fold it into the LIRR/MNR file is real,
// but the two share almost nothing below the surface: NJT has its own endpoints,
// its own per-route colours served by the feed (the railroads publish none), a
// route id namespace that needs no system qualifier (LIRR and MNR ids collide with
// each other and NJT's do not collide with themselves), a FLAT arrivals payload
// where the railroad's is bucketed by direction, and no GPS trains at all. What it
// does share is the machinery, and that is reused wholesale from systems/shared.js
// and helpers.js rather than copied.

/* ---------------- NJ Transit Rail ---------------- */

// route_id -> css colour, rider-facing name, and glide geometry, all three built
// in ONE pass over ONE /api/njt-routes payload by njtRouteTables. Reassigned
// together rather than filled in place, which is what makes "the line that draws
// and the line that glides came from the same response" structural instead of a
// promise in a comment.
//
// EVERY READ GOES THROUGH njtRouteColor / njtRouteName, never through a bare
// .get(): both tables are empty until loadNjtRoutes resolves (several seconds
// after the first /api/njt-trains poll has already painted markers on a cold
// start), and route 17, the event-only Meadowlands Rail Line, has no trips in an
// ordinary publication and so never appears here at all. A missing route is the
// normal case on this layer, not an error state.
let njtRouteColors = new Map();
let njtRouteNames = new Map();
let njtRouteIndex = new Map();

// stop_id -> [lat, lon], from /api/njt-stops. It exists for the glide rather than
// for the markers: njtGlideTrain needs the coordinates of the stop a train is
// heading for, because this feed's payload carries the train's CURRENT position
// where every other system's carries its next station. See that helper for what
// went wrong before it did.
const njtStopCoords = new Map();

// NJT inter-station gaps are commuter-railroad gaps, not subway ones (Trenton to
// Princeton Junction is further than any LIRR hop), so the glide takes the
// railroad tolerances rather than the subway's. Same constants, same reason they
// were widened for the railroads in the first place.
const NJT_SLICE_OPTS = { maxSlice: RAILROAD_ROUTE_MAX_SLICE, acceptDist: RAILROAD_ROUTE_ACCEPT_DIST };

async function loadNjtRoutes() {
  let routes;
  try {
    const res = await fetch("/api/njt-routes", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return false; // warming 503 (or transient error): retry
    routes = await res.json();
  } catch {
    return false;
  }
  // AN EMPTY PAYLOAD IS AMBIGUOUS HERE IN A WAY IT IS NOT ELSEWHERE, and the
  // resolution is to retry. /api/njt-routes serves [] for three states: a failed
  // (and retrying) load, a deployment with no NJT credentials, and a READY load
  // whose publication carried no shapes.txt, which is a legitimate steady state
  // rather than a warming one. Retrying costs one cheap cached request per backoff
  // step against a static endpoint and stops for good the moment a line arrives;
  // accepting [] as final would leave the map permanently lineless the first time
  // a cold start served it. The stations and the trains do not wait on this: they
  // are separate loaders and a separate poll, so an NJT with no geometry still
  // draws its whole railroad minus the lines, which is decision (a) of this phase.
  if (!routes.length) return false;
  const tables = njtRouteTables(routes, polylineCumLengths);
  njtRouteColors = tables.colors;
  njtRouteNames = tables.names;
  njtRouteIndex = tables.index;
  for (const route of routes) {
    const color = njtRouteColor(route.route, njtRouteColors);
    // Every kept variant draws (the backend's dedup already collapsed the
    // reverse-direction and shared-track duplicates, and what survives is the
    // branches: North Jersey Coast to Long Branch AND to Bay Head).
    // Non-interactive like every other route line, so clicks fall through to the
    // station squares and the train markers above them.
    for (const points of route.polylines || []) {
      L.polyline(points, {
        color,
        weight: 2.5,
        opacity: 0.5,
        interactive: false,
        renderer: lineRenderer,
      }).addTo(njtRouteLines);
    }
  }
  return true;
}

// Station squares, FILLED slate under a white ring, and both halves of that are
// decisions rather than defaults.
//
// SQUARE, because NJ Transit shares platforms with the modes whose station markers
// are round: PATH's inverted dot at Hoboken and Newark Penn, and a Long Island Rail
// Road circle at New York Penn Station. Shape survives at a glance where a colour
// difference between two 8px dots does not, which is the same call path.js made
// about its diamonds and airtrain.js about its squares.
//
// FILLED, because the TRAIN markers on this layer are hollow squares: every NJT
// position is schedule-derived, so every train takes the railroad's placed variant
// (see njtIcon), and a hollow station square underneath a hollow train square would
// be two of the same shape in the same style at the same pixel. Filled also reads
// correctly on its own terms: a station is a fixed, certain thing and a scheduled
// train position is an estimate.
//
// The slate is the railroad station stroke inverted (white ring on #334155 rather
// than #334155 ring on white), which keeps NJT inside the commuter-rail family a
// rider already reads while making it its own member of it.
const NJT_STATION_COLOR = "#334155";

function njtStationIcon() {
  const html =
    `<svg viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" rx="1.5" ` +
    `fill="${NJT_STATION_COLOR}" stroke="#fff" stroke-width="1.5"/></svg>`;
  return L.divIcon({ className: "njt-station-marker", html, iconSize: [12, 12], iconAnchor: [6, 6] });
}

async function loadNjtStops() {
  let stations;
  try {
    const res = await fetch("/api/njt-stops", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stations = await res.json();
  } catch {
    return false;
  }
  if (!stations.length) return false; // failed-warmup []: retry until the backend heals
  for (const station of stations) {
    njtStopCoords.set(station.id, [station.lat, station.lon]);
    // Rendered on stationPane (z-index 450) like every other station marker, so the
    // squares sit above the route lines and below the train markers, which is the
    // station-below-vehicles layering the whole map keeps.
    const arrivalsUrl = `/api/njt-arrivals/${encodeURIComponent(station.id)}`;
    const marker = labeledMarker(
      [station.lat, station.lon],
      { icon: njtStationIcon(), pane: "stationPane" },
      njtStationName(station),
    );
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url: arrivalsUrl,
      // NO ALERTS PREPEND, and the first draft of this comment gave a reason that
      // was not true. It claimed a bare-id join would attach Metro-North's alerts
      // to New Jersey platforms; there is no bare-id join available to make, since
      // indexAlerts keys on `${system}|${id}` and stationAlertsBlock takes the
      // system as its first argument. NJ Transit's alerts are polled, they are
      // stamped with their own system, and their selectors are in NJT's own id
      // space, so the join is straightforward and would be correct.
      //
      // THE REAL REASON IS THE SHAPE OF THE BODY. stationAlertsBlock unions the
      // route ids out of `body.directions` to decide which route-scoped alerts also
      // apply at this station, and an NJT arrivals body has no `directions`: it is
      // flat, by the endpoint's own design. Wiring the join means teaching that
      // helper the flat shape, which changes a function four other systems depend
      // on, and doing it here would be widening this phase into theirs. Deferred
      // with its reason rather than quietly skipped.
      render: (s, b) =>
        njtArrivalsHtml(
          s,
          b,
          Date.now() / 1000 - (minClockOffset ?? 0),
          (routeId) => njtRouteColor(routeId, njtRouteColors),
          (routeId) => njtRouteName(routeId, njtRouteNames),
        ),
    })).addTo(njtStations);
    registerStation({
      // Qualified by the system like every other registry key. NJT ids are bare
      // integers 1..176 and collide freely with the railroad and ferry id spaces,
      // which the contract tier has already measured happening.
      key: `NJT|${station.id}`,
      kind: "njt",
      systemLabel: "NJ Transit",
      noun: "train",
      id: station.id,
      name: station.name ?? station.id,
      lat: station.lat,
      lon: station.lon,
      // The routes-per-station index the endpoint derives from trips (H5). A route
      // in this list need not have a LINE: it is derived from trips and the line is
      // derived from shapes, so the panel's chips go through the same two fallbacks
      // the popups use rather than assuming the two tables agree.
      routes: station.routes ?? [],
      wheelchair: false, // NJ Transit's GTFS publishes no accessibility data at all
      arrivalsUrl,
      marker,
      layer: njtStations,
      nameFor: (routeId) => njtRouteName(routeId, njtRouteNames),
      colorFor: (routeId) => njtRouteColor(routeId, njtRouteColors),
    });
  }
  return true;
}

// Hollow squares in the route's own colour. HOLLOW UNCONDITIONALLY, with no
// GPS/placed branch to switch on: NJ Transit's vehicle positions feed is
// deliberately never fetched (the numbers are at the poller registry in
// pollers.py), so every position on this layer is computed from the TripUpdates
// times against 15a's stop coordinates. The railroad's hollow variant is exactly
// the "this is a schedule estimate" signal a rider has already learned on the
// LIRR and Metro-North markers, so NJT borrows it rather than inventing a second
// vocabulary for the same fact.
function njtIcon(train) {
  const color = njtRouteColor(train.route_id, njtRouteColors);
  const html =
    `<svg viewBox="0 0 16 16"><rect x="2" y="2" width="12" height="12" rx="1.5" ` +
    `fill="#fff" stroke="${color}" stroke-width="2.5"/></svg>`;
  return L.divIcon({ className: "njt-marker", html, iconSize: [16, 16], iconAnchor: [8, 8] });
}

function njtTrainPopup(record) {
  const t = record.latest;
  return (
    njtTrainPopupHtml(
      t,
      njtRouteName(t.route_id, njtRouteNames),
      njtRouteColor(t.route_id, njtRouteColors),
    ) +
    // A2: the station this train is drawn on, reachable. A train drawn at its stop
    // covers the station square entirely, so without this the departures a rider
    // came for are unreachable at that pixel.
    //
    // GATED ON njtAtItsStation, NOT ON stop_id ALONE, which is what the first draft
    // did and what a review round caught. models.NjtTrain documents stop_id as
    // "where it is, OR the stop it is heading for", so an in-transit train carries
    // the stop it is running toward: measured, NJ_3800 was drawn 0.079 degrees
    // (about 8.7 km) from New York Penn while its popup said "Also here: New York
    // Penn Station", one line under "Next stop: New York Penn Station". That is
    // exactly what the principle comment at crossLinkHtml forbids, a link naming a
    // station the vehicle is not at. "At" is still read from the payload rather
    // than from distance.
    (njtAtItsStation(t) ? crossLinkHtml(`NJT|${t.stop_id}`) : "") +
    // C2: how old this train's data is when NJ Transit has gone dark.
    stalePopupLine(njtSystemAge())
  );
}

// NJ Transit's one system. The envelope carries a single-entry `systems` block
// keyed "njt" (models.NjtFeed says so and says why a degenerate map beats a
// scalar), so this is a real per-system read and not the synthesized fallback PATH
// takes. Wrapped in functions so the key pair is written in one place and read from
// four: the popup's age line, the dimming sweep, the create path, and njtPointFor,
// which is also what animateTrains glides through.
function njtSystemAge() {
  return systemAgeOf("njt", "njt");
}

function njtSystemStaleAt() {
  return systemStaleAtOf("njt", "njt");
}

// Re-dim every NJT marker from its own system's age (C2). ITS OWN REGISTRATION,
// which is the point: a dark NJ Transit dims NJ Transit and leaves the railroads,
// PATH, the ferries and the subway at full opacity. This is not hypothetical
// housekeeping. NJ Transit caps getToken at ten mints per account per Eastern day
// (learned 2026-09-02) and production shares that budget with every developer
// running the generator or the monitor, so an NJT that has gone dark while every
// other feed keeps decoding is a recurring production state rather than an
// outage scenario.
staleTreatments.push(() => {
  const age = njtSystemAge();
  for (const record of njtTrainRecords.values()) dimMarker(record.marker, age);
});

// train.id -> { marker, color, latest, glide, fState, _segId }.
//
// `color` is the resolved route colour the icon was last drawn with (the re-skin
// gate); `glide` is the reconciled train object njtGlideTrain returns, or null for a
// train that must not be interpolated at all. There is deliberately NO routeId
// field: the first draft carried one and nothing read it, because the re-skin gates
// on the colour and the re-projection gates on _segId, which embeds the route id
// itself.
//
// KEYED BY `id`, NOT trip_id, and njtKey is where that decision is written down:
// NJ Transit emits ADDED trips with an EMPTY trip_id, so a trip_id key would
// collapse every added train in the system onto one marker.
const njtTrainRecords = new Map();

// Where one record's marker belongs at `now`. Reads the record rather than a train
// so the poll path, the creation path and animateTrains cannot drift about which
// object they interpolate, which is the drift that produced the f-squared defect in
// the first place. A record with no glide sits at the position the server served.
function njtPointFor(record, now) {
  return record.glide
    ? trainLatLng(record.glide, glideClock(now, njtSystemStaleAt()), record.fState)
    : [record.latest.latitude, record.latest.longitude];
}

function applyNjt(data) {
  // Skew-corrected now, the same basis as every other apply path. A train inside
  // its dwell window carries null anchors and sits placed at its station
  // (trainLatLng's own fallback); one between stops interpolates prev -> next.
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  const seen = new Set();
  for (const train of data) {
    const key = njtKey(train);
    seen.add(key);
    const record = njtTrainRecords.get(key);
    // Same slice caching as the railroad and subway paths: recompute the projection
    // only when the (route, anchor, next stop) segment changes. A mid-trip route
    // relabel changes segId and re-projects onto the new route's geometry.
    const segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
    // THE GLIDE RUNS ON A RECONCILED COPY, never on the payload itself: this feed
    // serves the train's current position where the glide helpers expect its next
    // station. njtGlideTrain says what that costs when it is skipped; null means
    // this train is drawn where the server put it and does not interpolate.
    const glide = njtGlideTrain(train, njtStopCoords);
    if (glide) {
      glide._route =
        record && record._segId === segId && record.glide && record.glide._route
          ? record.glide._route
          : computeRouteSlice(glide, njtRouteIndex.get(train.route_id), NJT_SLICE_OPTS);
    }
    if (record) {
      record._segId = segId;
      record.glide = glide;
      record.latest = train;
      // THE LABEL TRACKS THE DATA and is not gated on route_id changing, for the
      // reason path.js states at length: a name gated that way strands the fallback
      // wording ("NJ Transit route 7") permanently once the real route table lands,
      // because route_id never changed. It also carries the delay, which moves poll
      // to poll while nothing else about the train does.
      setMarkerName(record.marker, njtTrainName(train, njtRouteName(train.route_id, njtRouteNames)));
      // Frozen glide clock while the feed is stale, so a retained train stops
      // advancing along its route instead of dead-reckoning on a dead feed (C2).
      record.marker.setLatLng(njtPointFor(record, now));
      dimMarker(record.marker, njtSystemAge());
      // RE-SKINNED ON THE COLOUR, NOT ON THE ROUTE ID, and that is a deliberate
      // divergence from path.js and railroad.js rather than a copy that drifted.
      // Both of those gate the re-icon on route_id changing, and the step-1
      // inventory recorded what it costs there: a marker created before the static
      // route table lands keeps the fallback colour PERMANENTLY, because route_id
      // never changes afterwards. On this layer that is the likelier case rather
      // than the corner, since /api/njt-trains answers on the first poll while
      // /api/njt-routes is still retrying its way through a cold start, so every
      // NJT train on screen would be neutral grey until it left the feed. Reading
      // the resolved colour costs one Map lookup a poll and closes it. The route id
      // is still tracked, because a mid-trip relabel is what re-projects the glide.
      const color = njtRouteColor(train.route_id, njtRouteColors);
      if (record.color !== color) {
        record.marker.setIcon(njtIcon(train));
        record.color = color;
      }
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = {
        // The colour the icon was drawn with, so the re-skin above compares like
        // with like rather than re-iconing every poll.
        color: njtRouteColor(train.route_id, njtRouteColors),
        latest: train,
        glide,
        fState: {},
        _segId: segId,
      };
      const age = njtSystemAge();
      newRecord.marker = labeledMarker(
        njtPointFor(newRecord, now),
        // Dimmed at creation, like every other system: retained data must never
        // render live, not even for one frame (the C2b spec).
        { icon: njtIcon(train), opacity: markerOpacity(age) },
        njtTrainName(train, njtRouteName(train.route_id, njtRouteNames)),
      )
        .bindPopup(() => njtTrainPopup(newRecord))
        .addTo(njtTrains);
      njtTrainRecords.set(key, newRecord);
    }
  }
  // Trains the backend stopped serving (terminal arrivals, and the end of an ADDED
  // trip's life) leave the map.
  for (const [key, record] of njtTrainRecords) {
    if (!seen.has(key)) {
      njtTrains.removeLayer(record.marker);
      njtTrainRecords.delete(key);
    }
  }
}
