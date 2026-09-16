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
    const res = await fetch("/api/railroad-routes", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
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
    const res = await fetch("/api/railroad-stops", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
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
    // Built once, used by the popup descriptor and the A1 registry alike.
    const arrivalsUrl =
      `/api/railroad-arrivals/${encodeURIComponent(station.system)}` +
      `/${encodeURIComponent(station.id)}`;
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url: arrivalsUrl,
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
    registerStation({
      // The system is part of the key AND the label: LIRR and Metro-North have
      // independent id spaces that do collide, and a rider searching "Jamaica"
      // needs to know which railroad they are being offered.
      key: `${station.system}|${station.id}`,
      kind: "railroad",
      systemLabel: station.system === "MNR" ? "Metro-North" : station.system,
      noun: "train",
      id: station.id,
      system: station.system,
      name: station.name ?? station.id,
      lat: station.lat,
      lon: station.lon,
      routes: station.routes ?? [],
      wheelchair: false, // the railroad stops endpoint carries no accessibility field
      arrivalsUrl,
      marker,
      layer: railroadStationLayer,
      // The railroad renderer resolves route names per system; the panel needs the
      // same resolution so its sentences say "Babylon" rather than "5".
      nameFor: (routeId) => railroadRouteNames.get(`${station.system}|${routeId}`) || null,
    });
  }
  return true;
}


/* ---------------- Railroads (LIRR + MNR) ---------------- */

// WHAT A TRAIN IS DRAWN FROM IS SERVED, NOT INFERRED (contract 6.3). Every row carries
// its provenance (reported, estimated, placed or retained) and its own observation's
// clock, and every decision below reads those two fields: the glyph (railroadHollow),
// glide versus snap (drawnFromPrediction), the words (positionQualifier), the cross-link
// (railroadAtItsStation), the dimming (vehicleMarkerAge) and the glide freeze
// (railroadGlideAt). isPlacedRailroad read stop_id instead and is deleted: an estimated
// train names a stop exactly as a placed one does, and a field's shape cannot say which
// of section 3.4's steps a train reached. ONE MORE FIELD IS REMEMBERED, NOT SERVED: a
// `retained` row no longer says how it was drawn, because retention stamps over its
// provenance, so each record keeps the provenance its train was last served with
// (`drawnFrom`) and a retained train is drawn as it was before (drawnFromPrediction says
// why and what a never-seen retained row gets).

// Square markers, colored by railroadColor (railroad route ids collide with the
// subway palette, so they get their own). A position the train reported is filled; one
// derived from a prediction (placed at its stop, or estimated between two) is hollow,
// so the two are visually distinct, and a row that claims neither is hollow too, because
// the filled square is the glyph that says GPS. `before` is the record's drawnFrom.
function railroadIcon(train, before = null) {
  const color = railroadColor(train.route_id);
  const rect = railroadHollow(train, before)
    ? `<rect x="2" y="2" width="12" height="12" rx="1.5" fill="#fff" stroke="${color}" stroke-width="2.5"/>`
    : `<rect x="1.5" y="1.5" width="13" height="13" rx="1.5" fill="${color}" stroke="#fff" stroke-width="1.5"/>`;
  const html = `<svg viewBox="0 0 16 16">${rect}</svg>`;
  return L.divIcon({ className: "railroad-marker", html, iconSize: [16, 16], iconAnchor: [8, 8] });
}

// The words one train carries about its position (positionQualifier), read against its
// own system's block: LIRR's positions are age-gated and Metro-North's are not, which
// the board takes from UNDATED_SYSTEMS rather than from either name.
function railroadPosition(train, now = correctedNow()) {
  return vehiclePosition("railroads", [train.system], train, now);
}

// The clock a gliding train is drawn at: the live one until the earlier of its system's
// freeze deadline and its own prediction's (glideDeadline), and pinned there after, so a
// placed or estimated train riding an old trip update never dead-reckons in a healthy
// feed. On the committed capture 53 of LIRR's 56 placed trains ride a prediction older
// than 90 s, so those 53 hold still and dim where they used to glide at full opacity.
function railroadGlideAt(train, now = correctedNow()) {
  return glideClock(now, glideDeadline(systemStaleAtOf("railroads", train.system), train));
}

// A train's accessible name at `now`: the fields its popup renders (A2) and its
// position's words. THE ONE COMPOSITION the apply path and the stale sweep both write, so
// a fix that crosses OBS_FRESH_S between polls gains its age in the name on the same tick
// that dims its marker (observationCrossed), and a poll that fails re-applies nothing but
// still sweeps, so the name keeps saying what the popup says.
function railroadMarkerName(train, now = correctedNow()) {
  return railroadTrainName(train, railroadRouteNames.get(`${train.system}|${train.route_id}`), railroadPosition(train, now));
}

// Why this train is leaving the map, as the vanishing-focus rescue words it (withheldFix):
// its last served row read against its system's block and the envelope that dropped it.
function railroadWithheld(train, now = correctedNow()) {
  const source = sourceDescriptor("railroads");
  const block = source ? sourceSystems(source)[train.system] : null;
  return withheldFix(train, block, source ? source.servedAt : null, now);
}

function railroadPopup(record) {
  const t = record.latest;
  const now = correctedNow();
  const position = railroadPosition(t, now);
  const head = formatRailroadHead(t.system, t.route_id, railroadRouteNames.get(`${t.system}|${t.route_id}`));
  return (
    // Scoped to the train's OWN system (LIRR/MNR) so a numeric route id shared with
    // another mode never leaks in.
    routeAlertsBlock(t.system, t.route_id) +
    `<b style="color:${readableInk(railroadColor(t.route_id))}">${esc(head)}</b>` +
    (t.train_num ? `<br>Train ${esc(t.train_num)}` : "") +
    // A train drawn from a prediction names the stop it is at or heading for; a GPS fix
    // names none, so the line is there exactly when the field is.
    (t.stop_name ? `<br>Next stop: ${esc(t.stop_name)}` : "") +
    (t.direction ? `<br>${esc(t.direction)}` : "") +
    // HOW THIS POSITION WAS OBTAINED, AND HOW OLD IT IS, in the compact form this popup
    // has always used: "live GPS", "live GPS, as of 5m ago", "estimated from a
    // prediction", "scheduled (no GPS)", "showing last known, as of 7m ago". Before 6.3
    // this line said "live GPS" of a fix fifteen hours old, which is F01.
    `<br><span class="popup-sub">${esc(position.compact)}</span>` +
    // A2: the station this train is sitting on, reachable. A train drawn AT its
    // station's coordinates covers the dot entirely, so without this the arrivals a
    // rider came for are unreachable at that pixel. "At" is railroadAtItsStation, read
    // from the payload and the clock the marker is glided by, never from distance: a
    // placed or estimated train between two stations names only the stop it is heading
    // for, and gets no link until it is drawn there. The registry key is
    // system-qualified because LIRR and MNR id spaces are independent and both are bare
    // integers. See the principle comment at crossLinkHtml in shared.js. A retained
    // placement is asked as the placement it was (record.drawnFrom): still frozen where
    // its glide stopped, so it is at its station only if that is where it stopped.
    (railroadAtItsStation(t, railroadGlideAt(t, now), record.drawnFrom)
      ? crossLinkHtml(`${t.system}|${t.stop_id}`)
      : "") +
    // C2: how old this train's own SYSTEM is when LIRR or MNR has gone stale, unless the
    // line above already said an age at least that old (vehicleStaleLine).
    vehicleStaleLine(systemAgeOf("railroads", t.system), position)
  );
}

// Re-dim every railroad marker from the larger of its system's age and its own
// observation's (6.3). GPS and placed trains are treated alike on the first count: both
// are drawn from the same feed, so a stale LIRR dims them all. The second is what 6.3
// adds: an LIRR fix or prediction past OBS_FRESH_S dims its own marker in a healthy
// feed, which on the committed capture is 11 fixes and 53 placements. And the name is
// re-derived here too, so the marker that dims says why in its accessible name at the
// same moment (railroadMarkerName); setMarkerName writes only a name that changed.
staleTreatments.push(() => {
  const now = correctedNow();
  for (const record of railroads.values()) {
    const t = record.latest;
    dimMarker(record.marker, vehicleMarkerAge("railroads", systemAgeOf("railroads", t.system), t, now));
    setMarkerName(record.marker, railroadMarkerName(t, now));
  }
});

// Keyed by (system, trip_id): LIRR and MNR trip_id namespaces are independent, so
// trip_id alone would collide (the backend dedups by the same composite). Trains drawn
// from a prediction glide between their prev and next station via trainLatLng (the
// subway v2 path), animated by animateTrains; a reported position moves by setLatLng each
// poll and is never routed through trainLatLng. `hollow` is the glyph the icon was last
// drawn with, the re-skin gate below; `drawnFrom` is the provenance the train was last
// served with before any retention, which is how a retained row is drawn.
const railroads = new Map(); // `${system}|${trip_id}` -> { marker, routeId, hollow, drawnFrom, latest, fState, _segId }

function railroadKey(train) {
  return `${train.system}|${train.trip_id}`;
}

// Railroad gliding reuses computeRouteSlice / trainLatLng unchanged; it differs
// from the subway path only in the geometry it looks up (railroadRouteIndex, by
// (system, route_id)) and these looser tolerances (railroad inter-station gaps
// dwarf subway ones).
const RAILROAD_SLICE_OPTS = { maxSlice: RAILROAD_ROUTE_MAX_SLICE, acceptDist: RAILROAD_ROUTE_ACCEPT_DIST };

function applyRailroads(data) {
  // Skew-corrected now, same basis as applyTrains; a train drawn from a prediction
  // interpolates between its prev and next station, a reported one sits where it was.
  const now = correctedNow();
  const seen = new Set();
  for (const train of data) {
    const key = railroadKey(train);
    seen.add(key);
    const record = railroads.get(key);
    // HOW THE TRAIN WAS LAST SERVED BEFORE ANY RETENTION: this row's provenance, or, for a
    // retained row, whatever the record last remembered (null for a train first seen
    // retained). A retained train is drawn as it was drawn before (drawnFromPrediction):
    // a placement stays hollow and frozen on its glide, and does not jump to its next stop
    // wearing the GPS glyph.
    const before = train.provenance === "retained" ? (record ? record.drawnFrom : null) : train.provenance;
    // Both from the SERVED provenance (6.3): whether this train glides, and which glyph
    // it wears. A train can change ladder step between polls (its fix aging past
    // OBS_FRESH_S while a fresh prediction stands in for it), so both are re-read here.
    const glides = drawnFromPrediction(train, before);
    const hollow = railroadHollow(train, before);
    if (record) {
      record.drawnFrom = before;
      if (glides) {
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
      // THE LABEL TRACKS THE DATA, including the field a reader would not guess: a
      // train can change ladder step between polls, which changes the position clause
      // the name ends with ("live GPS", "live GPS, as of 5m ago", "estimated from a
      // prediction", "scheduled position, no GPS"), and that clause is how a rider knows
      // how much to trust the position.
      setMarkerName(record.marker, railroadMarkerName(train, now));
      const age = vehicleMarkerAge("railroads", systemAgeOf("railroads", train.system), train, now);
      // A train drawn from a prediction is glided through its freeze clock, so a
      // retained system's trains stop advancing (C2), a retained placement included (it
      // is still drawn as one), and so does one riding an old prediction in a healthy
      // system (6.3). A reported position has no interpolation to freeze: it sits where it
      // was last reported, which for a retained fix is the position it last reported, so
      // re-applying it is already the frozen answer.
      record.marker.setLatLng(
        glides ? trainLatLng(train, railroadGlideAt(train, now), record.fState) : [train.latitude, train.longitude],
      );
      dimMarker(record.marker, age);
      // Re-skin when the route color or the glyph flips (a train can pick up a fresh
      // fix on a later poll, or lose it to a prediction; retention flips neither).
      if (record.routeId !== train.route_id || record.hollow !== hollow) {
        record.marker.setIcon(railroadIcon(train, before));
        record.routeId = train.route_id;
        record.hollow = hollow;
      }
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = { routeId: train.route_id, hollow, drawnFrom: before, latest: train, fState: {} };
      if (glides) {
        newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
        train._route = computeRouteSlice(
          train,
          railroadRouteIndex.get(`${train.system}|${train.route_id}`),
          RAILROAD_SLICE_OPTS,
        );
      }
      const age = vehicleMarkerAge("railroads", systemAgeOf("railroads", train.system), train, now);
      newRecord.marker = labeledMarker(
        glides ? trainLatLng(train, railroadGlideAt(train, now), newRecord.fState) : [train.latitude, train.longitude],
        // Dimmed at creation for the same reason as the subway: retained data, and
        // since 6.3 an old observation, must never render live, not even for one frame
        // (the "C2b" spec).
        { icon: railroadIcon(train, before), opacity: markerOpacity(age) },
        railroadMarkerName(train, now),
      )
        .bindPopup(() => railroadPopup(newRecord))
        .addTo(railroadLayer);
      railroads.set(key, newRecord);
    }
  }
  for (const [key, record] of railroads) {
    if (!seen.has(key)) {
      // WHY IT LEFT, for the rescue's words if the rider was holding its popup (A4): a fix
      // the position ladder stopped drawing for its age is "no longer shown, last seen
      // over 10m ago", the status line's account of it, and not "left the feed"
      // (withheldFix). labeledMarker's `remove` hook reads it.
      record.marker._vanishReason = railroadWithheld(record.latest, now);
      railroadLayer.removeLayer(record.marker);
      railroads.delete(key);
    }
  }
}

