// Bus layer: markers, the on-demand route line, and the per-poll apply. A plain
// <script> after systems/shared.js; reads the shared map/layers/helpers globals.

// esc, routeColor, lineColor, staleness and friends live in helpers.js, loaded
// (with systems/shared.js) before this script, so they are globals here.

/* ---------------- Buses ---------------- */

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
  const note = busRouteNotes.get(b.route_id);
  const showNote = note && Date.now() - note.at < NOTE_TTL_MS;
  return (
    // Bus alerts are route-only (no stop selectors); "bus" route ids share the
    // bus layer's id space, so the match is by route_id under system "bus".
    routeAlertsBlock("bus", b.route_id) +
    `<b style="color:${readableInk(routeColor(b.route_id))}">${esc(b.route_id ?? "Unknown route")}</b>` +
    `<br>Bus ${esc(b.id)}<br>Heading: ${heading}` +
    (showNote ? `<br><span class="popup-sub">${esc(note.message)}</span>` : "") +
    // C2: buses are a single feed, so their system is the synthesized one named
    // after the source. Same age line as every other vehicle popup, so the
    // single-feed sources are not quietly exempt from the freshness rules.
    stalePopupLine(systemAgeOf("buses", "buses"))
  );
}

// Re-dim every bus from its source's age (C2). Buses do not glide, so there is no
// freeze clock here: a bus sits at its last reported position either way.
staleTreatments.push(() => {
  for (const record of buses.values()) dimMarker(record.marker, systemAgeOf("buses", "buses"));
});

/* ----- On-demand bus route line (click a bus to draw its route) ----- */

let shownBusRoute = null; // { routeId, busId }
let pendingBusId = null; // bus whose route fetch is in flight
let busRouteSeq = 0; // request token: bumped by every new request AND by clear
const busRouteNotes = new Map(); // route_id -> { message, at } shown in the popup
const NOTE_TTL_MS = 60000; // a transient failure shouldn't haunt popups all session

// Notes are only ever added on fetch failures (below), so sweeping expired
// entries on each set bounds the map for the session without any timer.
function setBusRouteNote(routeId, message) {
  const now = Date.now();
  for (const [id, note] of busRouteNotes) {
    if (now - note.at >= NOTE_TTL_MS) busRouteNotes.delete(id);
  }
  busRouteNotes.set(routeId, { message, at: now });
}

function refreshOpenPopup(busId) {
  const record = buses.get(busId);
  if (record?.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
}

function clearBusRoute() {
  busRouteSeq++; // invalidate any in-flight fetch
  pendingBusId = null;
  busRouteLayer.clearLayers();
  shownBusRoute = null;
  document.getElementById("route-banner").hidden = true;
}

// Does the line currently on the map (or the fetch in flight) belong to this bus?
//
// HONEST ABOUT WHAT THIS GUARD DOES TODAY: nothing observable. Leaflet closes the old
// popup BEFORE opening the new one, so bus A's clear always lands before bus B's draw,
// and an unconditional clear would behave identically. Mutation testing said exactly
// that: removing this check leaves every bus-route spec green. It is kept because it
// encodes the ordering the code depends on rather than assuming it silently, and A7e
// asserts that ordering directly, so the day a Leaflet upgrade fires open before close
// the suite says so and this check becomes the only thing between a rider and a popup
// naming a route with no route drawn.
function busRouteOwnedBy(busId) {
  return (shownBusRoute && shownBusRoute.busId === busId) || pendingBusId === busId;
}

/* A3: THE ROUTE LINE FOLLOWS THE POPUP, NOT THE CLICK.
   This was bound to the marker's `click` event, so the line was drawn by the gesture
   rather than by the state it produced. Every other way of opening the popup drew
   nothing: a programmatic openPopup (which is what any panel or keyboard path uses)
   opened a popup describing a route with no route on the map, and Leaflet's own
   keyboard activation path does not synthesise a click on the layer either. The defect
   was recorded twice in earlier phases and had no owning surface until this one.

   popupopen and popupclose are the honest seam because they fire for every opener,
   including ones that do not exist yet. Leaflet fires both on the source layer as well
   as the map (verified in the vendored source), so binding them here needs no
   map-level bookkeeping.

   THE DOUBLE-FIRE THIS HAD TO AVOID: keeping the old click handler alongside these
   would have drawn on click AND on popupopen for a mouse rider, and since a
   same-bus re-click closes the popup, the pair would have raced draw against clear on
   one gesture. The click handler is gone rather than guarded, because a guard would
   have left two things able to draw and only one of them tested.

   The toggle logic goes with it. A re-click closes the popup, which now clears the line
   through popupclose; that used to be inferred from isPopupOpen() reading the state
   Leaflet had just changed. BEHAVIOUR CHANGE WORTH NAMING: dismissing the popup by
   clicking the map used to leave the line drawn, and now clears it. That is what "and
   closing clears it" asks for, and it is more consistent: the banner naming the route
   is part of the same popup-shaped thing. */
function releaseBusRoute(bus, marker) {
  if (!bus || !busRouteOwnedBy(bus.id)) return;
  // A POPUP THAT CLOSED BECAUSE ITS MARKER LEFT THE MAP IS NOT A RIDER DISMISSING IT,
  // and the review caught this as a regression the popupopen move introduced. Hiding the
  // Buses layer calls map.removeLayer(busLayer), which removes every bus marker, and
  // Leaflet binds `remove: this.closePopup` on any layer with a popup. So unchecking
  // Buses fired popupclose and DESTROYED the drawn route line; re-checking could not
  // bring it back, because the geometry was gone and only a fresh fetch would restore
  // it. Measured before this guard: uncheck -> {lines: 0}, re-check -> {lines: 0}. On
  // the pre-A3 tree the same sequence gave {lines: 1} both times.
  //
  // Asking whether the marker is still on the map separates the two causes exactly: a
  // rider closing a popup leaves the marker where it is, while hiding a layer or a
  // vehicle leaving the feed takes the marker with it. The layer case must PRESERVE the
  // line (busRouteLayer is hidden by the same toggle and comes back with it), and the
  // departed-vehicle case is already handled explicitly in the removal sweep below,
  // which clears the route when the drawn bus itself leaves.
  if (marker && typeof map !== "undefined" && map && !map.hasLayer(marker)) return;
  clearBusRoute();
}

async function showBusRoute(bus) {
  if (!bus?.route_id) return;
  // Already drawn for this exact bus and route: a refresh that reopens nothing should
  // not refetch. popup.update() does not fire popupopen, so this is belt for a future
  // path rather than the common case.
  if (shownBusRoute && shownBusRoute.busId === bus.id && shownBusRoute.routeId === bus.route_id) return;

  clearBusRoute(); // a different bus replaces any current line
  const requestId = ++busRouteSeq;
  pendingBusId = bus.id;

  let geometry;
  try {
    // AbortSignal.timeout bounds this click-driven fetch too (R2). Like the station
    // popup, the timeout (a fetch that never lands) is orthogonal to the busRouteSeq
    // guard (a fetch superseded by a newer click/clear); an abort rejects into the
    // catch below and shows the same "unavailable" note a network error does.
    const res = await fetch(`/api/bus-route/${encodeURIComponent(bus.route_id)}`, {
      signal: AbortSignal.timeout(FETCH_DEADLINE_MS),
    });
    if (requestId !== busRouteSeq) return; // superseded by a newer click/clear
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      pendingBusId = null;
      setBusRouteNote(bus.route_id, body?.detail ?? `Route line unavailable (HTTP ${res.status})`);
      refreshOpenPopup(bus.id);
      return;
    }
    geometry = await res.json();
  } catch {
    if (requestId !== busRouteSeq) return;
    pendingBusId = null;
    setBusRouteNote(bus.route_id, "Route line unavailable (network error)");
    refreshOpenPopup(bus.id);
    return;
  }
  if (requestId !== busRouteSeq) return; // superseded while parsing
  pendingBusId = null;
  busRouteNotes.delete(bus.route_id);
  refreshOpenPopup(bus.id);

  for (const points of geometry.directions ?? []) {
    L.polyline(points, {
      color: routeColor(bus.route_id),
      weight: 3.5,
      opacity: 0.65,
      interactive: false,
      renderer: lineRenderer,
    }).addTo(busRouteLayer);
  }
  shownBusRoute = { routeId: bus.route_id, busId: bus.id };
  const banner = document.getElementById("route-banner");
  document.getElementById("route-banner-label").textContent = `Bus route ${bus.route_id}`;
  document.getElementById("route-banner-label").style.color = routeColor(bus.route_id);
  banner.hidden = false;
}

document.getElementById("route-clear").addEventListener("click", clearBusRoute);

// Keep the banner honest when the Buses toggle hides the route line layer.
document.getElementById("toggle-buses").addEventListener("change", (e) => {
  document.getElementById("route-banner").hidden = !e.target.checked || !shownBusRoute;
});

const buses = new Map(); // bus id -> { marker, routeId, bearing, latest }

function applyBuses(data) {
  const seen = new Set();
  for (const bus of data) {
    seen.add(bus.id);
    const record = buses.get(bus.id);
    if (record) {
      record.marker.setLatLng([bus.latitude, bus.longitude]);
      // Vehicle reassigned to a different route: its drawn line is now stale.
      //
      // A3 review: ASKED OF busRouteOwnedBy, NOT OF shownBusRoute, because the drawn
      // line is only half the state. Between the popup opening and the geometry landing
      // there is a fetch in flight and nothing drawn yet, and a reassignment arriving in
      // that window passed this check untouched: the fetch then completed and drew the
      // OLD route, for a bus the poll had just moved to a new one. Reproduced with a
      // delayed /api/bus-route response: bus MTA NYCT_101 opened on M15, reassigned
      // mid-flight, and the result was {lines: 1, label: "Bus route M15"} with the
      // record on the new route. busRouteOwnedBy covers both halves, and clearBusRoute
      // bumps the sequence so the in-flight response is discarded rather than drawn.
      //
      // Cleared and not redrawn, which is the same choice already made for the drawn
      // case: the rider asked for the line that bus was on, and the honest answer to
      // "it is not on that route any more" is no line, not a different one they did not
      // ask for.
      if (record.routeId !== bus.route_id && busRouteOwnedBy(bus.id)) {
        clearBusRoute();
      }
      const shapeChanged =
        record.routeId !== bus.route_id ||
        (record.bearing == null) !== (bus.bearing == null);
      if (shapeChanged) {
        record.marker.setIcon(busIcon(bus));
      } else if (record.bearing !== bus.bearing && bus.bearing != null) {
        // Mutate the existing SVG so the CSS rotation transition animates;
        // setIcon would recreate the element and snap to the new angle.
        const icon = busIcon(bus); // built once, reused by both branches below
        const svg = record.marker.getElement()?.firstElementChild;
        if (svg) {
          svg.style.transform = `rotate(${Number(bus.bearing)}deg)`;
          // Keep the stored html current so Leaflet recreates the element
          // correctly if the layer is toggled off and back on.
          record.marker.options.icon.options.html = icon.options.html;
        } else {
          record.marker.setIcon(icon); // not in the DOM (layer hidden)
        }
      }
      record.bearing = bus.bearing;
      record.routeId = bus.route_id;
      record.latest = bus;
      // THE LABEL TRACKS THE DATA. Route and bearing both change on a REUSED marker
      // (the bearing-only branch above even rewrites the svg in place rather than
      // re-iconing), so a name written once at creation would describe a bus that
      // turned twenty minutes ago. Refreshed here, after every field it reads is
      // settled and before the popup update that reads the same record.
      setMarkerName(record.marker, busName(bus));
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = { bearing: bus.bearing, routeId: bus.route_id, latest: bus };
      newRecord.marker = labeledMarker([bus.latitude, bus.longitude], {
        icon: busIcon(bus),
        opacity: markerOpacity(systemAgeOf("buses", "buses")), // dim on the first frame
      }, busName(bus))
        .bindPopup(() => busPopup(newRecord))
        .on("popupopen", () => showBusRoute(newRecord.latest))
        .on("popupclose", () => releaseBusRoute(newRecord.latest, newRecord.marker))
        .addTo(busLayer);
      buses.set(bus.id, newRecord);
    }
  }
  for (const [id, record] of buses) {
    if (!seen.has(id)) {
      busLayer.removeLayer(record.marker);
      buses.delete(id);
      // If the vehicle whose route line is drawn (or has a fetch in flight) drops
      // out of the feed, clear that line and its banner too, so a bounded
      // empty-feed sweep (or a lone reassignment) does not leave a ghost route
      // pointing at a bus no longer on the map. clearBusRoute also invalidates
      // any in-flight route fetch via its request token.
      if (shownBusRoute?.busId === id || pendingBusId === id) clearBusRoute();
    }
  }
}

