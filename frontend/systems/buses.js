// Bus layer: markers, the on-demand route line, and the per-poll apply. A plain
// <script> after systems/shared.js; reads the shared map/layers/helpers globals.

// esc, routeColor, lineColor, staleness and friends live in helpers.js, loaded
// (with systems/shared.js) before this script, so they are globals here.

/* ---------------- Buses ---------------- */

/* MR4: the design's arrow and dot, at the muted hue, in helpers.js so node can read both
   states (README: "smallest marks on the map").

   THE MUTED HUE IS A LEGIBILITY FIX AND NOT A TASTE, measured over all 360 hues with the
   repo's own contrastRatio: against light paper, `hsl(h, 75%, 40%)` leaves 147 of 360 hues
   under the 3:1 mark floor (worst 2.01), and `hsl(h, 45%, 38%)` leaves NONE (worst 3.16).
   Since a bus route's colour is a hash of its id, the old hue meant whether a given route's
   arrow was legible was luck. That is also why this matters for the stage that releases the
   dark theme rather than for the one that drew the arrow.

   busMarkColor AND NOT routeColor. routeColor is unchanged and still paints the bus POPUP's
   route name and the clicked route's line, and the popups are stage MR5's, pinned byte for
   byte by P1g. Muting it in place would have moved a popup this stage may not touch.

   A HEADING IS A NUMBER OR IT IS NOTHING: busHasHeading decides, so a served null, an absent
   field and a NaN all draw the dot. An arrow pointing somewhere is a claim, and a missing
   field is not a direction. */
function busIcon(bus) {
  return busMarkIcon(busMarkColor(bus.route_id), busHasHeading(bus) ? bus.bearing : null);
}

/* MR4 ROUND 1: the clicked route line is the seventh canvas family, and the only one whose
   colour depends on something besides the theme. The route id was written onto each layer at
   draw for exactly this: the painter recomputes per route rather than per family.

   COLOUR ONLY. The line's 0.65 opacity is the design's constant and belongs to nothing else,
   but it is not passed, because there is no reason to write it twice. */
registerCanvasFamily("bus route lines", ({ busLightness }) => {
  for (const layer of busRouteLayer.getLayers()) {
    if (!layer.options.routeId || !layer.setStyle) continue;
    layer.setStyle({ color: busMarkColorAt(layer.options.routeId, busLightness) });
  }
});

function busPopup(record) {
  const b = record.latest;
  const position = busPosition(b);
  const heading = b.bearing != null ? `${Math.round(b.bearing)}°` : "unknown";
  const note = busRouteNotes.get(b.route_id);
  const showNote = note && Date.now() - note.at < NOTE_TTL_MS;
  return (
    // Bus alerts are route-only (no stop selectors); "bus" route ids share the
    // bus layer's id space, so the match is by route_id under system "bus".
    routeAlertsBlock("bus", b.route_id) +
    `<b style="color:${readableInk(routeColor(b.route_id), popupSurfaceColor())}">${esc(b.route_id ?? "Unknown route")}</b>` +
    `<br>Bus ${esc(b.id)}<br>Heading: ${heading}` +
    // 6.3: a bus is GPS, so a fresh one says nothing here, and one whose own fix is past
    // OBS_FRESH_S says "live GPS, as of 2m ago" (positionLineHtml).
    positionLineHtml(position) +
    (showNote ? `<br><span class="popup-sub">${esc(note.message)}</span>` : "") +
    // C2: buses are a single feed, so their system is the synthesized one named
    // after the source. Same age line as every other vehicle popup, so the
    // single-feed sources are not quietly exempt from the freshness rules.
    vehicleStaleLine(systemAgeOf("buses", "buses"), position)
  );
}

// The words one bus carries about its position, read against the buses' one system.
function busPosition(bus, now = correctedNow()) {
  return vehiclePosition("buses", ["buses"], bus, now);
}

// A bus's accessible name at `now`, the one composition the apply path and the stale
// sweep both write (railroadMarkerName in railroad.js says why the sweep writes it too).
function busMarkerName(bus, now = correctedNow()) {
  return busName(bus, busPosition(bus, now));
}

// Re-dim every bus from the larger of its source's age and its own fix's (C2, 6.3).
// Buses do not glide, so there is no freeze clock here: a bus sits at its last
// reported position either way.
// The name is re-derived with the opacity, so a fix that crosses OBS_FRESH_S between
// polls says its age in the name on the tick that dims it.
staleTreatments.push(() => {
  const now = correctedNow();
  const age = systemAgeOf("buses", "buses");
  for (const record of buses.values()) {
    dimMarker(record.marker, vehicleMarkerAge("buses", age, record.latest, now));
    setMarkerName(record.marker, busMarkerName(record.latest, now));
  }
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
  //
  // AND ONLY WHEN THERE IS A LINE TO PRESERVE, which round 2 of the review caught as a
  // defect in the guard above. Ownership is two states, not one: DRAWN, and a fetch still
  // in flight with nothing drawn yet. Preserving the pending state was wrong, because the
  // early return also skips the sequence bump that supersedes that fetch, so hiding the
  // layer mid-fetch let the response land and run to completion against a layer that was
  // no longer on the map. Reproduced: uncheck Buses during the fetch and the result was
  // {lines: 1, bannerHidden: false, label: "Bus route M15", busesChecked: false} -- the
  // banner naming a route for a popup the rider can no longer see, which is the exact
  // state A7f pins as forbidden for the drawn case. Falling through to clearBusRoute
  // discards the in-flight response instead, and there is no drawn geometry to lose:
  // that is what shownBusRoute being null in this branch means.
  const drawnForThisBus = shownBusRoute && shownBusRoute.busId === bus.id;
  if (drawnForThisBus && marker && typeof map !== "undefined" && map && !map.hasLayer(marker)) return;
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
  let failure = null;
  try {
    // AbortSignal.timeout bounds this click-driven fetch too (R2). Like the station
    // popup, the timeout (a fetch that never lands) is orthogonal to the busRouteSeq
    // guard (a fetch superseded by a newer click/clear); an abort rejects into the
    // catch below and shows the same "unavailable" note a network error does.
    const res = await fetch(`/api/bus-route/${encodeURIComponent(bus.route_id)}`, {
      signal: AbortSignal.timeout(FETCH_DEADLINE_MS),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      failure = body?.detail ?? `Route line unavailable (HTTP ${res.status})`;
    } else {
      geometry = await res.json();
    }
  } catch {
    failure = "Route line unavailable (network error)";
  }
  // ONE SEQUENCE CHECK, AFTER EVERY AWAIT, and it is the fix rather than a tidy-up
  // (N3, the bus half of F12). The error branch used to read its detail line, a
  // SECOND await, and then clear pendingBusId with no re-check between them. So a
  // 503 from a route the rider had already navigated away from answered
  // busRouteOwnedBy(newBus) FALSE for a request that was genuinely still in flight,
  // and reopened the exact hole the reassignment comment in applyBuses records
  // closing. Measured on the real code: pendingBusId "MTA NYCT_0002" -> null while
  // that bus's own fetch was still in the air, and the stale note went back onto a
  // route that was drawn.
  //
  // Every await feeds one variable and nothing else, so the guard below is the only
  // one this function needs and an await added inside the try is covered by it.
  if (requestId !== busRouteSeq) return; // superseded by a newer click/clear
  pendingBusId = null;
  if (failure !== null) {
    setBusRouteNote(bus.route_id, failure);
    refreshOpenPopup(bus.id);
    return;
  }
  busRouteNotes.delete(bus.route_id);
  refreshOpenPopup(bus.id);

  for (const points of geometry.directions ?? []) {
    L.polyline(points, {
      /* MR4 ROUND 1: THE SAME WHEEL THE ARROW IS DRAWN FROM. This was routeColor's raw
         hsl(h, 75%, 40%) while the mark beside it had been muted and tokenised, so clicking a
         bus drew a line in a different colour from the arrow that was clicked. It is also the
         colour helpers.js says out loud owes 3:1 BECAUSE it is a polyline colour, and in the
         dark theme the raw wheel leaves 169 of 360 hues under that floor (worst 1.45), 217 of
         them once this line's own 0.65 opacity is composited.

         RESOLVED AT DRAW AND REGISTERED BELOW, because a canvas cannot read the token the mark
         uses. THE ROUTE ID RIDES ON THE LAYER so the repaint can recompute per route: the
         colour depends on the route as well as the theme, which is what makes this family
         different from the other six. */
      routeId: bus.route_id,
      color: busMarkColorAt(bus.route_id, busMarkLightness()),
      weight: 3.5,
      opacity: 0.65,
      interactive: false,
      renderer: lineRenderer,
    }).addTo(busRouteLayer);
  }
  shownBusRoute = { routeId: bus.route_id, busId: bus.id };
  const banner = document.getElementById("route-banner");
  /* MR1 ROUND 2: THE COLOUR MOVED FROM THE TEXT TO A SWATCH, which is the rule this app
     already states at readableInk: "the brand colour stays on the SHAPES that carry identity,
     where 3:1 applies and the label carries the meaning". The label was set directly to the
     route's hashed hue, which was measured against the old panel's opaque white; the header
     surface is a token now and can be #2d2b2b, where an hsl(h, 75%, 40%) hue has no chance.
     So the route's colour is a mark before the words and the words are --ink, legible in both
     themes by construction. The custom property is what the stylesheet paints the mark with. */
  const label = document.getElementById("route-banner-label");
  label.textContent = `Bus route ${bus.route_id}`;
  label.style.setProperty("--route-ink", routeColor(bus.route_id));
  banner.hidden = false;
}

document.getElementById("route-clear").addEventListener("click", clearBusRoute);

// Keep the banner honest when the Buses toggle hides the route line layer.
//
// MR1: A CLICK ON A BUTTON, NOT A CHANGE ON A CHECKBOX, and the state comes from the strip
// rather than from the control. The feed toggles are buttons with aria-pressed now, which
// fire no `change` event and carry no `.checked`, so the old listener went silent and the
// banner went on claiming a route line that was no longer drawn. Asking feedShowing() rather
// than reading the button's attribute keeps this on the same side of the truth as the layer
// itself; this listener is registered after the strip's own, so the set is already updated.
document.getElementById("toggle-buses").addEventListener("click", () => {
  document.getElementById("route-banner").hidden = !feedShowing("buses") || !shownBusRoute;
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
      // THE PENDING HALF WAS ONLY TRUE ON PAPER UNTIL N3 WAS FIXED, and this comment
      // was the thing that recorded a hole it did not actually close. showBusRoute's
      // error branch cleared pendingBusId with no sequence re-check after the awaited
      // detail line, so a 503 belonging to an abandoned route fetch answered this
      // check FALSE for a bus whose own fetch was still in the air: the reassignment
      // then passed straight through and the superseded geometry drew after all. The
      // guard now sits after every await in that function, so "covers both halves"
      // means what it says.
      //
      // Cleared and not redrawn, which is the same choice already made for the drawn
      // case: the rider asked for the line that bus was on, and the honest answer to
      // "it is not on that route any more" is no line, not a different one they did not
      // ask for.
      if (record.routeId !== bus.route_id && busRouteOwnedBy(bus.id)) {
        clearBusRoute();
      }
      /* MR4: THE GATE ASKS THE SAME PREDICATE THE ICON DOES. It compared `bearing == null`,
         which agrees with busHasHeading on null and undefined and disagrees on a NaN: a bus
         whose served bearing went from a number to NaN would keep its arrow, now pointing at
         rotate(NaN) rather than swapping to the dot. One predicate, so the gate and the glyph
         cannot disagree about whether this bus is pointed anywhere.

         ASKED OF record.bearing, WHICH IS THE PREVIOUS POLL'S VALUE at this point in the
         block: record.latest is not reassigned until the bottom, so reading it here would
         compare this poll against itself and the gate would never fire. */
      const shapeChanged =
        record.routeId !== bus.route_id ||
        busHasHeading({ bearing: record.bearing }) !== busHasHeading(bus);
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
      // settled and before the popup update that reads the same record. Since 6.3 it
      // also carries the bus's own fix's age once that is past OBS_FRESH_S.
      setMarkerName(record.marker, busMarkerName(bus));
      // 6.3: the reuse path dims too, from this poll's own fix, so a bus whose fix went
      // fresh again is bright on the poll that says so rather than at the sweep after it.
      dimMarker(record.marker, vehicleMarkerAge("buses", systemAgeOf("buses", "buses"), bus));
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = { bearing: bus.bearing, routeId: bus.route_id, latest: bus };
      newRecord.marker = labeledMarker([bus.latitude, bus.longitude], {
        icon: busIcon(bus),
        // Dim on the first frame, from the larger of the system's age and the fix's own.
        opacity: markerOpacity(vehicleMarkerAge("buses", systemAgeOf("buses", "buses"), bus)),
      }, busMarkerName(bus))
        .bindPopup(() => busPopup(newRecord), POPUP_OPTIONS)
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

