// AirTrain JFK layer (static-only, no realtime): guideway lines, stations, and
// the scheduled-headway popup. Shared global scope.

/* ---------------- AirTrain JFK (static-only) ---------------- */

/* MR4: THE GUIDEWAY IS GRAY AND DASHED, and the magenta is gone.

   The design gives AirTrain `#6d6e71` light / `#9a9a9a` dark, weight 3, dashArray "8 5". The
   magenta it replaces was chosen to read as its own mode where nothing else on the map is
   magenta, and it did that; what it could not do is move with the theme, and it said "this
   is a mode with its own identity" where the design says something more useful: a DASH means
   a service that is not heavy rail, which the ferry's dashed routes say too, and the GRAY is
   the same `--scheduled` token the app already uses for "scheduled, no live feed", which is
   exactly what AirTrain is (it has no realtime feed at all and its popups say so).

   THE COLOUR IS NOT A CONSTANT ANY MORE, because it has two values. It is read through the
   registry's tokens at draw time and re-read on a theme swap; `--scheduled` has carried both
   since MR1 with no canvas able to read it until now. */
/* THE COMMUTER SQUARE, which is the design's word ("Stations use the commuter square") and
   makes AirTrain the fourth family to draw it.

   "A SQUARE ALWAYS MEANS REGIONAL RAIL" BECOMES "REGIONAL RAIL OR AIRTRAIN", and the map has
   the collision to prove it: Jamaica has an AirTrain station and an LIRR station, and after
   this they are the same mark. That is the design's instruction and it is recorded rather
   than softened; what still tells them apart is everything except the glyph (the popup, the
   panel entry, the accessible name, and the line each one sits on).

   IT JOINS `rail-stn-marker` DELIBERATELY, which is the class six e2e specs count as NOT a
   vehicle. An AirTrain station has never been a vehicle and was counted as one by every
   sentinel that said `.leaflet-marker-icon:not(.rail-stn-marker)`; joining the class makes
   those counts more correct, not less, and the P4a census records the move. */
/* MR4: the guideway is AirTrain's one canvas mark and its one registry entry. The station
   squares are divIcons whose paper and ink are `var()` in an inline style, so they follow a
   theme change through the cascade and need nothing here. */
registerCanvasFamily("airtrain lines", ({ scheduled }) => {
  const style = airtrainLineStyle(scheduled);
  for (const layer of airtrainRouteLinesLayer.getLayers()) layer.setStyle(style);
});

function airtrainIcon() {
  return railStationIcon("airtrain");
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
      ...airtrainLineStyle(scheduledColor()),
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
    const marker = labeledMarker([station.lat, station.lon], {
      icon: airtrainIcon(),
      pane: "stationPane",
    }, airtrainStationName(station))
      .bindPopup(
        // MR5: the paper square this station is drawn as, at the title's size. `marker` is in
        // scope here because this family binds its popup directly rather than through
        // bindStationPopup (AirTrain has no feed to count down from).
        () => airtrainStationPopupHtml(station, routes, nyMinutesSinceMidnight(), popupMarkHtml(markerMarkHtml(marker))),
        POPUP_OPTIONS,
      )
      .addTo(airtrainStationLayer);
    registerStation({
      key: `airtrain|${station.id}`,
      kind: "airtrain",
      systemLabel: "AirTrain",
      noun: "train",
      id: station.id,
      name: station.name ?? station.id,
      lat: station.lat,
      lon: station.lon,
      // AirTrain stations carry no routes field: the relationship runs the other
      // way, with each ROUTE listing the stations it serves. Derived here the same
      // way airtrainStationPopupHtml derives it, so the chips and the detail view
      // agree about which branches call here.
      routes: routes.filter((r) => (r.stations ?? []).includes(station.id)).map((r) => r.id),
      wheelchair: false,
      // NO LIVE ARRIVALS AT ALL. AirTrain publishes no realtime feed, so there is
      // nothing to count down to and a ticking countdown would fabricate precision
      // the data does not have. A null arrivalsUrl is how the panel knows to render
      // the SCHEDULED headway bands instead, clearly labeled as scheduled. That is
      // the branch any future feedless system takes, not a special case for this one.
      arrivalsUrl: null,
      airtrainRoutes: routes,
      marker,
      layer: airtrainStationLayer,
    });
  }
  return true;
}

