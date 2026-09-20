// Railroad (LIRR + Metro-North) layer: GPS and placed train markers, route
// lines, station arrivals, and the per-poll apply. Shared global scope.

// `${system}|${route_id}` -> [{ points, cum }], the geometry placed railroad
// trains glide along. Keyed by (system, route_id) because LIRR and MNR route ids
// collide; populated by loadRailroadRoutes and read by applyRailroads.
const railroadRouteIndex = new Map();
// `system|route_id` -> rider-facing route name (e.g. "Babylon Branch"), from
// /api/railroad-routes; used to label the railroad train and station popups.
const railroadRouteNames = new Map();
/* `system|route_id` -> { color, textColor }, the agency's own two colours as
   /api/railroad-routes serves them (hex with no leading "#", null when the feed leaves a
   column blank). MR3 draws the branch lines and the train tags from these instead of from
   railroadColor's hash, which was never the agency's palette and could not be: it was a hash
   of the route id, so two branches sharing one published colour got two different ones and a
   branch whose id moved changed colour.

   SEPARATE FROM railroadRouteNames RATHER THAN A FIELD ON IT, because the two are filled
   under different conditions: a name is set only `if (route.name)` and a colour is set for
   every route, so folding them into one map would mean either dropping the colour of a
   nameless route or inventing a name to hold it. */
const railroadRouteColors = new Map();

async function loadRailroadRoutes() {
  /* THROUGH fetchRoutesPayload FOR THE FIELD THIS LAYER NEEDS (R-d, shared.js carries the whole
     argument): `color` arrived on this endpoint one branch ago and MR3 draws every branch line
     and every tag from it, so a response cached from before that deploy would paint the entire
     commuter railroad in the neutral grey for up to an hour. One re-read past the HTTP cache,
     then the neutral, which is what railBranchColor returns for a route with no colour anyway. */
  const routes = await fetchRoutesPayload("/api/railroad-routes", "color");
  if (routes == null) return false; // warming 503, or a network error: retry
  // RAILROAD NUANCE: the backend's railroad warmup is lenient PER SYSTEM. It
  // settles "ready" even when one system's static failed to load, and this
  // endpoint then serves only the loaded system's entries under the normal
  // hour-long cache. A non-empty one-system payload is therefore a SETTLED state
  // the server-side warmup will not revisit, so accepting it and stopping is
  // correct: further frontend retries would just re-read the same cached partial,
  // never a fuller one. Only a fully empty payload means "ask again later".
  if (!routes.length) return false;
  // { system, points, branch } per polyline, drawn by railDrawRibbons below.
  const ribbons = [];
  for (const route of routes) {
    const key = `${route.system}|${route.route}`;
    // Key by (system, route): LIRR and MNR route ids collide, so route_id alone
    // would merge two systems' geometry. Matches the endpoint's {system, route,
    // name, color, text_color, polylines} shape and the (system, route_id) lookup in
    // applyRailroads. THE TWO COLOUR FIELDS ARRIVED WITH claude/railroad-route-colors;
    // this comment said {system, route, name, polylines} until MR3, which is the stale
    // sentence that branch deliberately left for this stage to correct.
    railroadRouteIndex.set(
      key,
      route.polylines.map((points) => ({ points, cum: polylineCumLengths(points) })),
    );
    // The rider-facing route name (e.g. "Babylon Branch"), for the train and
    // station-arrivals popups; only routes with geometry reach here (see the
    // endpoint's KNOWN GAP), which is fine since a geometry-less route has no
    // trains to label either.
    if (route.name) railroadRouteNames.set(key, route.name);
    // The agency's own colours, for every route including a nameless one.
    railroadRouteColors.set(key, { color: route.color ?? null, textColor: route.text_color ?? null });
    const branch = railBranchColor(route.color);
    // Collected rather than drawn here, so ONE function owns which renderer each mark lands on
    // for all three rail families (railDrawRibbons, below, and njt.js calls the same one).
    for (const points of route.polylines) ribbons.push({ system: route.system, points, branch });
  }
  railDrawRibbons(ribbons, railroadLineLayer);
  return true;
}

/* THE CASING AND THE LINE, ON TWO PANES (round 4, the panel's finding on N2), which is the only
   ordering that survives two branches sharing track AND two endpoints landing in a race.

   README's mark is "casing paper weight 5 opacity .9 plus line weight 2.5 opacity 1, round
   caps": a wide paper stroke under a thin coloured one. MR2's F6 measured what happens when a
   wide paper stroke lands AFTER a thin line on a canvas renderer, which draws in INSERTION order
   and knows nothing of LayerGroups: it ERASES it. Drawing each branch as a back-to-back pair
   fixes that only WITHIN a branch. Where the Babylon and Montauk branches run the same rails out
   of Jamaica, Montauk's casing is inserted after Babylon's line and wipes it, which is F6
   reopened one pane down.

   AND TWO PASSES OVER ONE PAYLOAD IS NOT ENOUGH EITHER, which is the measurement that settled
   this. subway.js's drawRibbons draws every casing then every line, and that works there because
   the subway's geometry arrives in ONE response. Three rail families arrive in TWO:
   /api/railroad-routes and /api/njt-routes are separate fetches kicked off together, and on the
   hermetic world the one-renderer draw chain came out [5, 5, 2.5, 2.5, 5, 5, 5, 2.5, 2.5, 2.5],
   with all three NJ Transit casings stroked after both railroad lines. Neither loader can order
   the other's marks, so no per-loader pass can close it.

   SO THE TIER IS THE PANE: railroadCasingPane at 394 for every casing and railroadLinePane at
   395 for every line. The relation then holds in every arrival order rather than in the order
   that happened, which is the same standard finding N2 was settled to. shared.js's pane block
   carries the whole argument and the numbers.

   PAPER COMES FROM paperColor(), NOT FROM "var(--paper)". Canvas2D resolves no custom properties:
   `ctx.strokeStyle = "var(--paper)"` is not an error, it is a SILENT no-op that leaves the context
   holding whatever colour it last had, so the casing strokes in the previous branch's ink.
   Measured in-page on this branch, which is how the defect was found. shared.js's paperColor
   reads the computed value once per draw and is the repo's answer to exactly this; subway.js has
   used it since MR2.

   ONE FUNCTION FOR BOTH RAIL FILES, because the panes only help if every family uses them: NJ
   Transit passing railroadLineRenderer for its casing would put a 5px paper stroke back on the
   line pane and erase an LIRR line at Penn. njt.js calls this with its own layer resolver, and
   the group a mark is added to still owns whether it SHOWS, so the two feeds toggle separately
   exactly as MR1 split them. */
/* MR4: THE DEBT MR3 NAMED, PAID. The three rail families' 5px casings are drawn from
   paperColor() resolved once per draw, so they keep the theme they were drawn under until
   something restyles them. This is that something, and it is registered beside the draw so
   the two cannot drift.

   FILTERED BY RENDERER, NOT SWEPT OVER THE GROUP, and that is the whole care of this entry.
   Each family's layer group holds the casing AND the branch line together, so a blind
   `group.eachLayer(l => l.setStyle({ color: paper }))` would paint every 2.5px branch line in
   paper as well and erase the agency's published colours, which are the entire subject of
   MR3. MR3 put the casings on their own renderer (railroadCasingRenderer, pane 394) for the
   drawing order, and that same split is what makes them identifiable here: a layer is a
   casing if and only if it is on the casing canvas.

   COLOUR ONLY, NEVER OPACITY. The one other setStyle in this app is route focus, which owns
   opacity and guards on it; a swap that passed opacity would overwrite a rider's focus with
   the casing's resting 0.9 the moment they changed theme. */
registerCanvasFamily("rail casings", ({ paper }) => {
  for (const group of [lirrRouteLinesLayer, mnrRouteLinesLayer, njtRouteLines]) {
    for (const layer of group.getLayers()) {
      if (layer.options.renderer === railroadCasingRenderer) layer.setStyle({ color: paper });
    }
  }
});

function railDrawRibbons(ribbons, layerFor) {
  const paper = paperColor();
  for (const ribbon of ribbons) {
    const group = layerFor(ribbon.system);
    L.polyline(ribbon.points, {
      color: paper,
      weight: 5,
      opacity: 0.9,
      lineCap: "round",
      interactive: false,
      renderer: railroadCasingRenderer,
    }).addTo(group);
    L.polyline(ribbon.points, {
      color: ribbon.branch,
      weight: 2.5,
      opacity: 1,
      lineCap: "round",
      interactive: false,
      renderer: railroadLineRenderer,
    }).addTo(group);
  }
}

// A branch's code and paint, from what /api/railroad-routes served for it. One lookup, so the
// tag, the popup and anything later cannot resolve a branch two ways. A route the endpoint
// never carried (no geometry, so no entry) falls back to its id in the neutral colour, which
// railBranchCode and railBranchColor already do.
function railroadBranch(system, routeId) {
  const key = `${system}|${routeId}`;
  const paint = railroadRouteColors.get(key) ?? { color: null, textColor: null };
  return {
    code: railBranchCode(system, routeId, railroadRouteNames.get(key) ?? null),
    color: paint.color,
    textColor: paint.textColor,
  };
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
    /* MR3: THE PAPER SQUARE, so a square always means regional rail and a circle always
       means subway. An L.marker with a 10x10 shape inside a 20x20 box, on stationPane by the
       marker's OWN pane option rather than through a canvas renderer, which is the one thing
       that changes about where it draws: it was a canvas circleMarker whose options.pane was
       Leaflet's overlayPane default and whose renderer put it on stationPane, and it is now
       an L.marker on stationPane directly. The pane it DRAWS on is the same either way, which
       is what the click order and the z-index depend on and what pin P3a holds.

       THROUGH labeledMarker, WHICH markers.test.js REQUIRES OF EVERY L.marker IN THE APP, and
       the first draft of this did not: it called L.marker directly on the reasoning that a
       station carries no accessible name by design. That reasoning is the SUBWAY's, and it
       holds there because a canvas circleMarker has no element to name. This square is an
       L.marker, so it has DOM either way, and a nameless div with its role stripped is worse
       than a named one. NJ Transit's three station squares have gone through the factory
       since 15c for exactly this reason; the guard caught the divergence in one run.

       IT IS STILL NOT A TAB STOP. labeledMarker owns keyboard:false, so 300 rail stations
       gain a name for element navigation and add nothing to the tab order, which is the
       policy shared.js states: the A1 station panel is the keyboard path.

       Keyed by (system, id) in the fetch url because LIRR and MNR stop_id namespaces can
       collide. */
    const marker = labeledMarker(
      [station.lat, station.lon],
      { icon: railStationIcon(station.system), pane: "stationPane" },
      railroadStationName(station),
    );
    // The name, on the label pane the subway's names use, gated from zoom 11 by its own band
    // and by the Names toggle. Never the hub class: a hub is a subway transfer station.
    bindRailStationLabel(marker, station.name ?? station.id);
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
          // MR5: the paper square this station is drawn as, at the title's size.
          popupMarkHtml(markerMarkHtml(m)),
        ),
    })).addTo(railroadStopLayer(station.system));
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
      layer: railroadStopLayer(station.system),
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

/* MR3: THE TWO-PART TAG, in place of the 16x16 rounded square.

   WHAT THE SQUARE COULD SAY AND THE TAG CAN. The square had one bit of information, filled
   or hollow, carrying "GPS or not". The brief's 3.1 table needs three: where the position
   came from (the body), whether the heading is trusted (the head), and how old it is (the
   dimming, which is markerOpacity's and is untouched). So an `estimated` train, whose
   position is inferred but whose heading is real, was drawn identically to a `placed` one
   whose heading is not; now it is an outlined body with a FILLED chevron, which is the one
   combination the whole table exists to draw.

   AND THE BRANCH IS NAMED ON THE MARK. The square was a colour and nothing else, so a rider
   could not tell a Babylon train from a Montauk one without opening it. The tag carries the
   agency's letter and the branch's code, in the agency's own colour.

   EVERY DECISION IS READ FROM THE SERVED ROW, exactly as before: railTagState takes the
   provenance, the `before` a retained row is drawn as, and positionQualifier's kind, and
   railTrainBearing takes the served bearing, the slice the glide already built, and the
   served direction. Nothing here reads the shape of a field. */
function railroadTagState(train, before = null, now = correctedNow()) {
  const position = railroadPosition(train, now);
  // NO AGE ARGUMENT (round 4). The table returned a `dim` field nothing read; the opacity a
  // rider sees is markerOpacity(vehicleMarkerAge(...)) applied to the marker, on the apply
  // path, the stale sweep and at creation. One dimming rule, and it is the contract's.
  return railTagState(train, before, position.kind);
}

function railroadIcon(train, before = null, now = correctedNow()) {
  const branch = railroadBranch(train.system, train.route_id);
  return railTagIcon({
    system: train.system,
    code: branch.code,
    color: branch.color,
    textColor: branch.textColor,
    state: railroadTagState(train, before, now),
    bearing: railTrainBearing(train),
  });
}

/* THE RE-SKIN GATE, WIDENED FROM ONE BOOLEAN TO THE WHOLE MARK.

   It was `record.hollow !== hollow`, which was enough when the square's only variable was
   filled-or-hollow. The tag has five: the branch code, its colour, its ink, the body, the
   head and the heading. A train that keeps its colour and changes provenance, or that picks
   up a bearing on a later poll, would have kept the icon it was born with. The NJT layer
   carries the same hazard from the other direction: its gate is the resolved colour, and
   /api/njt-trains answers while /api/njt-routes is still retrying, so a train drawn in the
   neutral would never re-skin once its real colour arrived.

   One string, compared, so adding a sixth variable means adding it here and nowhere else. */
function railroadSkinKey(train, state, bearing) {
  const branch = railroadBranch(train.system, train.route_id);
  return [
    train.route_id,
    branch.code,
    branch.color,
    branch.textColor,
    state.body,
    state.head,
    state.headingTrusted && bearing != null ? Math.round(bearing) : "dot",
  ].join("|");
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
  const head = railroadHeadParts(t.system, t.route_id, railroadRouteNames.get(`${t.system}|${t.route_id}`));
  return (
    // Scoped to the train's OWN system (LIRR/MNR) so a numeric route id shared with
    // another mode never leaks in.
    routeAlertsBlock(t.system, t.route_id) +
    /* MR5: the head this popup has printed since phase 9, as section 5's kicker and title. The
       words are unchanged and the middot is gone with the joining: the agency is the kicker, the
       branch (or "route 5", or nothing) is the title, and a train whose feed named neither still
       gets its agency as the title so the popup is never headless.

       THE KICKER IS THE SERVED `system` FIELD, which is the feed's code: "MNR" here where every
       spoken surface in this app says railroadSystemLabel's "Metro-North". That divergence is
       older than this stage and it is recorded as an MR5 finding rather than reworded in passing.

       THE MARK IS THE TAG THIS TRAIN IS WEARING, taken off its own marker: the branch code, the
       body, and the chevron at the bearing it is drawn at. Rebuilding it here would need the
       train, its previous row and the clock a second time, and the two would disagree on exactly
       the trains whose state is worth looking at. */
    popupKickerHtml({ left: head.agency }) +
    popupTitleHtml({
      markHtml: popupMarkHtml(markerMarkHtml(record.marker)),
      text: head.line || head.agency,
      color: readableInk(railroadColor(t.route_id), popupSurfaceColor()),
    }) +
    popupRowsHtml([
      { k: "Train", v: t.train_num ?? "" },
      // A train drawn from a prediction names the stop it is at or heading for; a GPS fix
      // names none, so the row is there exactly when the field is.
      { k: "Next stop", v: t.stop_name ?? "" },
      { k: "Direction", v: t.direction ?? "" },
      /* HOW THIS POSITION WAS OBTAINED, AND HOW OLD IT IS. Before 6.3 this line said "live GPS"
         of a fix fifteen hours old, which is F01.
         MR5 (ruling Q1): THROUGH positionWords, LIKE EVERY OTHER POPUP. This was the app's one
         surface that rendered `position.compact` itself, from a line written here, and it did so
         UNCONDITIONALLY. Two things follow and both are rider-visible. A `placed` train said
         "scheduled (no GPS)" and now says the contract's "scheduled position (no GPS)". And a
         FRESH GPS fix said "live GPS" and now says nothing, because silence means current (memo
         D9) and this popup was the only place in the app that broke that rule. An aged fix still
         speaks. The before is pinned in the ledger; the pins were retaken after. */
      { k: "Position", v: positionWords(position) },
    ]) +
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
    // C2 restyled as MR5's footer (ruling Q2): how old this train's own SYSTEM is when LIRR or
    // MNR has gone stale, with the words withheld where the line above already said an age at
    // least that old. Scoped to the train's own system, which is why the age is passed rather
    // than looked up: the two railroads are two feeds.
    popupFreshLine(systemAgeOf("railroads", t.system), position)
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
// `skin` is railroadSkinKey's string, which replaced MR2's `hollow` boolean: the tag has five
// variables where the square had one (see railroadSkinKey).
const railroads = new Map(); // `${system}|${trip_id}` -> { marker, skin, drawnFrom, latest, fState, _segId }

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
      /* RE-SKIN WHEN ANY PART OF THE MARK CHANGED, which since MR3 is five things rather than
         one: the branch code, its colour, its ink, the body, the head and the heading. A train
         picks up a fresh fix on a later poll (body solid), loses it to a prediction (body
         outlined), gains a bearing once its slice is built (dot to chevron), or turns; and its
         branch colour arrives late, because /api/railroad-routes is a separate fetch that
         retries on a warming 503 while /api/railroads is already answering. Every one of
         those used to be invisible to a gate that compared route_id and a boolean.

         THE ROTATION GOES THROUGH THE ICON, NOT A CSS TRANSITION. The bearing is rounded to a
         degree in the skin key so a train wandering by hundredths does not rebuild its icon
         every poll, and a rotation that crosses north jumps rather than sweeping the long way
         round, which is why style.css gives this mark no transition. */
      const bearing = railTrainBearing(train);
      const state = railroadTagState(train, before, now);
      const skin = railroadSkinKey(train, state, bearing);
      if (record.skin !== skin) {
        record.marker.setIcon(railroadIcon(train, before, now));
        record.skin = skin;
      }
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = { drawnFrom: before, latest: train, fState: {} };
      if (glides) {
        newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
        train._route = computeRouteSlice(
          train,
          railroadRouteIndex.get(`${train.system}|${train.route_id}`),
          RAILROAD_SLICE_OPTS,
        );
      }
      const age = vehicleMarkerAge("railroads", systemAgeOf("railroads", train.system), train, now);
      // Seeded from the same two calls the gate above compares, so a train's first poll and
      // its second cannot disagree about what it is already wearing.
      const bearing = railTrainBearing(train);
      newRecord.skin = railroadSkinKey(train, railroadTagState(train, before, now), bearing);
      newRecord.marker = labeledMarker(
        glides ? trainLatLng(train, railroadGlideAt(train, now), newRecord.fState) : [train.latitude, train.longitude],
        // Dimmed at creation for the same reason as the subway: retained data, and
        // since 6.3 an old observation, must never render live, not even for one frame
        // (the "C2b" spec).
        { icon: railroadIcon(train, before, now), opacity: markerOpacity(age) },
        railroadMarkerName(train, now),
      )
        .bindPopup(() => railroadPopup(newRecord), POPUP_OPTIONS)
        .addTo(railroadVehicleLayer(train.system));
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
      railroadVehicleLayer(record.latest.system).removeLayer(record.marker);
      railroads.delete(key);
    }
  }
}

