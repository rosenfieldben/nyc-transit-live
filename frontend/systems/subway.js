// Subway layer: train markers, route lines, station arrivals, and the per-poll
// apply. A plain <script> after systems/shared.js in the shared global scope.

/* ---------------- Subways ---------------- */

/* MR2: THE BULLET, LIFTED ABOVE ITS RIBBON.

   THE SHAPE IS THIS APP'S OWN, by ruling R1 of docs/reviews/map-redesign-rounds.md. The
   handoff draws this as two concentric circles with a letter in them, which is the MTA's
   route symbol; the repository's README says route symbols require a license and to use our
   own markers. So the geometry ports and the artwork does not: an 18-unit box, a halo behind
   the body, the design's type sizes and the design's anchors, drawn as the rounded rectangle
   this file has always drawn and MR1's Key already shows.

   THE HALO IS A TOKEN RATHER THAN #fff, which is the only thing about the body that moved.
   It used to be a 1.5px white stroke ON the body; it is now a 1.5-unit paper ring BEHIND it,
   at the design's 0.95, which is the same visual weight and follows a theme swap through the
   cascade at no cost (style.css says why the canvas cannot do the same and has to resolve
   the token in script). The body's geometry is unchanged: rect 1.5,1.5,15,15 rx 3.

   AND IT SITS ONE PIXEL HIGHER, iconAnchor [9,21] rather than [9,22], which is the design's
   number for a mark lifted to clear the ribbon under it rather than the station dot. The
   24x24 hit target and its bottom anchoring are style.css's and are unchanged. */
function trainIcon(train) {
  const route = train.route_id ?? "";
  const label = /^[A-Za-z0-9]{1,3}$/.test(route) ? route : "?";
  const color = lineColor(route);
  const textColor = readableTextOn(color);
  const html = `<svg viewBox="0 0 18 18">
      <rect x="0" y="0" width="18" height="18" rx="4" style="fill: var(--paper)" opacity="0.95"/>
      <rect x="1.5" y="1.5" width="15" height="15" rx="3" fill="${color}"/>
      <text x="9" y="9.5" text-anchor="middle" dominant-baseline="central"
            font-size="${label.length > 1 ? 8.5 : 10.5}" font-weight="800"
            font-family="Archivo, system-ui, sans-serif" fill="${textColor}">${esc(label)}</text>
    </svg>`;
  // A2: OFFSET, not centred, so the square floats above its point instead of covering
  // the station dot underneath. A subway train's position is DERIVED (placed at its
  // stop by stop_id, or interpolated between two stops), so moving the drawing a few
  // pixels states nothing false; PATH set this precedent for the same reason and the
  // same geometry. See the principle comment at crossLinkHtml in shared.js for why
  // measured positions get a popup link instead. popupAnchor follows the anchor so the
  // popup still points at the train rather than floating away from it.
  return L.divIcon({
    className: "train-marker",
    html,
    iconSize: [18, 18],
    iconAnchor: [9, 21],
    popupAnchor: [0, -21],
  });
}

function trainPopup(record) {
  const t = record.latest;
  const position = subwayPosition(t);
  return (
    routeAlertsBlock("subway", t.route_id) +
    `<b style="color:${readableInk(lineColor(t.route_id), popupSurfaceColor())}">${esc(t.route_id ?? "?")} train</b>` +
    `<br>Next stop: ${esc(t.stop_name ?? t.stop_id ?? "unknown")}` +
    (t.direction ? `<br>${esc(t.direction)}` : "") +
    // 6.3: HOW THIS POSITION WAS OBTAINED, a word this popup never carried. Every subway
    // train is placed from its trip update (feeds/subway.py), so its provenance is
    // `placed` and section 3.2's rule (a) says a surface carries its word whenever the
    // provenance is not `reported`: "scheduled position (no GPS)", with its age once the
    // clock that dates it (the group header, or the joined vehicle's) is past OBS_FRESH_S.
    positionLineHtml(position) +
    `<br><span class="popup-sub">Trip ${esc(t.trip_id ?? "?")}</span>` +
    // C2 restyled as MR5's footer (ruling Q2): how old this train's own FEED GROUP is, when that
    // group has gone stale and the line above has not already said so. A dimmed marker says "not
    // current"; this says how far from current. The group's age and not the source's, which is the
    // measured reason popupFreshLine takes an age rather than resolving one.
    popupFreshLine(subwaySystemAge(t), position)
  );
}

// route_id -> the feed group(s) whose served data covers it, inverted from the
// per-system `routes` coverage the subways payload carries (C2). A subway train
// names no group, so this is the only handle from a marker to the block that
// describes its freshness. A list rather than a single group because two groups
// claiming one route (an upstream relabel mid-outage) must resolve to the WORST of
// them, never to whichever happened to be inverted last.
let subwayGroupsByRoute = new Map();
// Whether the payload does route coverage at all. False against a backend that
// sends `systems` without `routes`, which is the one case where a train's group is
// unknowable; see subwaySystemAge for what that falls back to.
let subwayRouteCoverage = false;

// Wired as the subways source's onSystems hook in map.js, so the inversion happens
// exactly once per poll where the block arrives, not per marker.
function noteSubwaySystems(systems) {
  const byRoute = new Map();
  let covered = false;
  for (const [group, system] of Object.entries(systems ?? {})) {
    if (!Array.isArray(system.routes)) continue;
    covered = true;
    for (const routeId of system.routes) {
      const groups = byRoute.get(routeId);
      if (groups) groups.push(group);
      else byRoute.set(routeId, [group]);
    }
  }
  subwayGroupsByRoute = byRoute;
  subwayRouteCoverage = covered;
}

// The freshness of the feed group behind one train: the worst age and the earliest
// freeze deadline among the groups whose coverage lists its route.
//
// THE FALL-BACK DIRECTION IS DELIBERATE, and it now covers BOTH ways the join can
// come up empty: a payload with no route coverage at all (an older backend), and a
// train whose route matches no group's list. Either way the train falls back to the
// source's WORST system, so a partial outage over-dims rather than under-dims.
// Under-dimming is how retained data ends up looking live, which is precisely what
// the retention gate exists to prevent, so the safe answer when we cannot say WHICH
// group is stale is to say some of this may be old.
//
// REVIEW FIX. The second case used to return "not stale", justified by the claim
// that a route in no group's list has no data on the map to describe. That is false
// for a train with a NULL route_id: the decoder emits those (a trip with no route in
// the feed), the payload's coverage lists deliberately skip them, and the train is
// on the map all the same. It rendered full opacity, unfrozen and with no age line,
// right next to its dimmed siblings from the same dead group.
function subwaySystemFreshness(train) {
  const groups = subwayGroupsFor(train);
  if (!groups.length) return worstSystemFreshness("subways");
  const worst = { age: null, staleAt: null };
  for (const group of groups) {
    const age = systemAgeOf("subways", group);
    const staleAt = systemStaleAtOf("subways", group);
    if (age != null && (worst.age == null || age > worst.age)) worst.age = age;
    if (staleAt != null && (worst.staleAt == null || staleAt < worst.staleAt)) {
      worst.staleAt = staleAt;
    }
  }
  return worst;
}

function subwaySystemAge(train) {
  return subwaySystemFreshness(train).age;
}

function subwaySystemStaleAt(train) {
  return subwaySystemFreshness(train).staleAt;
}

// The feed groups whose served coverage lists this train's route, or [] when the payload
// does no route coverage or no group lists it (subwaySystemFreshness says what [] means).
function subwayGroupsFor(train) {
  return subwayRouteCoverage ? (subwayGroupsByRoute.get(train.route_id) ?? []) : [];
}

// The words one subway train carries about its position (6.3), read against its own feed
// groups; positionBoard takes the worst of the source when none lists its route, the same
// direction subwaySystemFreshness takes for the same miss.
function subwayPosition(train, now = correctedNow()) {
  return vehiclePosition("subways", subwayGroupsFor(train), train, now);
}

// A subway train's accessible name at `now`, the one composition the apply path and the
// stale sweep both write (railroadMarkerName in railroad.js says why the sweep does).
function subwayMarkerName(train, now = correctedNow()) {
  return subwayTrainName(train, subwayPosition(train, now));
}

// The clock a subway train glides by: the earlier of its groups' freeze deadline and its
// own observation's (glideDeadline), so a train dated by an old header or an old joined
// vehicle holds still in a group whose other trains are current.
function subwayGlideAt(train, now = correctedNow()) {
  return glideClock(now, glideDeadline(subwaySystemStaleAt(train), train));
}

// Re-dim every subway marker from the larger of its own group's current age and its own
// observation's (C2, 6.3).
staleTreatments.push(() => {
  const now = correctedNow();
  for (const record of trains.values()) {
    dimMarker(
      record.marker,
      vehicleMarkerAge("subways", subwaySystemAge(record.latest), record.latest, now),
      // MR2: the sweep re-derives the product rather than erasing the focus. Without this
      // base a focused route would un-dim itself on the next poll, and a focus spec that
      // pressed a button and looked immediately would still have passed.
      subwayFocusBase(record.latest),
    );
    setMarkerName(record.marker, subwayMarkerName(record.latest, now));
  }
});

// Static route geometry, fetched once at startup (not polled). Canvas
// renderer keeps ~22k points cheap; lines are decorative, so failures are
// silent and the map just shows markers without them.
const lineRenderer = L.canvas({ padding: 0.3 });

/* THE SUBWAY'S RIBBONS DRAW ON THEIR OWN CANVAS, in the pane systems/shared.js creates at
   z-index 390, below the one every other family shares. The comment at that createPane call
   is the whole reason: one canvas draws its layers in insertion order, the static fetches
   land in a race, and a 6.5px paper casing arriving last erases a 2.5px line beside it. */
const subwayLineRenderer = L.canvas({ padding: 0.3, pane: "subwayLinePane" });

/* AND THE THREE COMMUTER RAIL FAMILIES DRAW ON THEIRS, at 394 and 395, for the same reason one
   zoom further down the stack: MR3's branch casing is 5px in --paper at 0.9, which is thicker
   than PATH's 3.5, the AirTrain's 3 and the ferry's 2, and those three share the canvas at 400.
   Declared here beside their sibling rather than in systems/railroad.js, because the four
   renderers are one decision and a reader looking for "which canvas does this family use"
   should find all of them in one place. Used by systems/railroad.js and systems/njt.js.

   TWO OF THEM, AND THE SPLIT IS LOAD-BEARING (round 4). The subway keeps ONE renderer because
   drawRibbons orders its whole drawing in one pass over one payload; the rail families cannot,
   because their geometry arrives from TWO endpoints in a race, so neither loader can put its
   casings under the other's lines. A pane per tier is the only ordering that does not depend on
   which response lands first, and shared.js's pane block carries the measurement. */
const railroadCasingRenderer = L.canvas({ padding: 0.3, pane: "railroadCasingPane" });
const railroadLineRenderer = L.canvas({ padding: 0.3, pane: "railroadLinePane" });
const routeIndex = new Map(); // route_id -> [{ points, cum }] for interpolation

// Every system's static loader (this one and its siblings in the other system
// scripts) returns true only once it has populated its layer from a NON-EMPTY
// payload, and false otherwise, so retryUntil can keep asking. WHY an empty 200 is not success: while a static group's warmup has
// FAILED (and is retrying server-side), its endpoints serve [] under Cache-Control
// no-cache, precisely so a browser will come back and see the healed state; and
// while it is still LOADING they 503. None of these endpoints has a legitimately
// empty steady state, so emptiness always means "ask again later". Each attempt
// stays all-or-nothing (populate only after the full payload parsed), so a retry
// can never double-add markers or polylines; once a loader returns true it is
// never called again.
async function loadRouteLines() {
  let routes;
  try {
    // AbortSignal.timeout (R2) bounds the fetch; an abort lands in the catch below
    // and returns false, so a wedged static fetch just reports "not populated yet"
    // and retryUntil reschedules it, exactly like a warming 503 or a network error.
    const res = await fetch("/api/subway-routes", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return false;
    routes = await res.json();
  } catch {
    return false;
  }
  if (!routes.length) return false; // failed-warmup []: retry until the backend heals
  for (const route of routes) {
    routeIndex.set(
      route.route,
      route.polylines.map((points) => ({ points, cum: polylineCumLengths(points) })),
    );
  }
  drawRibbons(routes);
  // The key is derived from this list, so it cannot exist until the list does.
  refreshSubwayKey();
  return true;
}

/* MR2: EVERY SHAPE IS DRAWN TWICE, a casing in paper and a line in lineColor(), which is
   what makes a ribbon rather than a hairline. The casing is what separates a trunk from the
   basemap and from the trunk beside it; before this the lines were 2.5px at opacity 0.5
   straight onto the tiles, which is why every trunk reads better after this stage than
   before it even where the arithmetic against paper is poor (the ledger has the numbers).

   TWO PASSES, NOT ONE PER SHAPE, and that is a decision the design does not make explicitly.
   Casing and line interleaved per route means a route drawn later cuts a paper gap through
   every route already drawn, which is the classic bridge look at a crossing and a disaster
   where two trunks run together for miles: the yellow trunk is drawn last on purpose, so
   interleaving would have it erase a stripe out of every trunk it shares track with. All
   casings, then all lines, gives every line a paper edge against the map and lets no line be
   eaten by a later casing.

   THE YELLOW TRUNK IS LAST IN BOTH PASSES. trunkDrawOrder is in helpers.js with a node test,
   because the reference implementation's version of this sort tests only N and R and leaves
   Q and W under the darker trunks, which is exactly the kind of half-right that passes a
   test written against N.

   AND EVERY RIBBON IS REGISTERED, because route focus restyles these and a layer it cannot
   see is a route that never dims. The registry is built here rather than by a later sweep
   over routeLinesLayer, so a bus route line (busRouteLayer is its own group) can never be
   mistaken for a subway ribbon. */
const subwayRibbons = [];

function drawRibbons(routes) {
  const byRoute = new Map(routes.map((route) => [route.route, route]));
  const order = trunkDrawOrder(routes.map((route) => route.route));
  const paper = paperColor();
  const focused = currentFocusRoutes();
  /* EVERY POLYLINE CARRIES THE SET OF ROUTES THAT SHARE IT (round 2), not just the route it
     was drawn from: its own id plus every trunk-mate with no geometry of its own, which is
     what sharing track means in this data. helpers.js's ribbonRouteSet decides it and the
     long comment there says why. It is what lets a Z bullet light the J's ribbon. */
  const add = (routeId, part, style) => {
    const routeSet = ribbonRouteSet(routeId, routes, subwayKnownRoutes());
    for (const points of byRoute.get(routeId)?.polylines ?? []) {
      const layer = L.polyline(points, {
        ...style,
        opacity: focusOpacity(focused, routeSet, part),
        lineCap: "round",
        lineJoin: "round",
        interactive: false,
        renderer: subwayLineRenderer,
      });
      layer.addTo(routeLinesLayer);
      subwayRibbons.push({ route: routeId, routes: routeSet, part, layer });
    }
  };
  for (const routeId of order) add(routeId, "casing", { color: paper, weight: RIBBON_CASING_WEIGHT });
  for (const routeId of order) add(routeId, "line", { color: lineColor(routeId), weight: RIBBON_LINE_WEIGHT });
}

/* MR2: route focus, applied. Called by paintRouteFocus in systems/shared.js, which owns the
   state and the controls; this owns the marks.

   OPACITY ONLY. Every ribbon keeps its geometry, its renderer and its identity, and every
   train keeps its icon: what changes is one number per layer. subway.spec.js D2e holds that
   by comparing the layer objects themselves across a focus and a clear.

   THE TRAINS GO THROUGH dimMarker WITH A BASE, which is how focus composes with the
   freshness contract instead of fighting it. The same call is made at creation, on every
   reuse and by the stale sweep, so a poll fifteen seconds later re-derives the same product
   rather than erasing the focus, and a train that arrives while a route is focused is drawn
   dim on its first frame rather than at full for a beat. */
/* AN OFF-FOCUS TRAIN IS ALSO OUT OF REACH (round 3, F4), and this is the operator's ruling
   rather than a choice made here. Focus keeps multiplying on the freshness contract's own
   opacity and markerOpacity is untouched, so FOCUS_DIM_TRAIN stays 0.15 and an off-focus
   stale train still lands at 0.45 * 0.15 = 0.0675. What was wrong was not the number: it was
   that Leaflet's setOpacity writes nothing but style.opacity, so a marker at 0.0675 was
   invisible and still took the click at its 24px halo and still announced itself as a train
   to a screen reader. A mark the map has deliberately pushed into the background must not be
   the thing a rider's tap lands on, and must not be read out as though it were on offer.

   BOTH ARE REVERSIBLE AND BOTH ARE REMOVED ON CLEAR, which is the half a test has to hold in
   the other direction: a focus that leaves a marker permanently silent and unclickable would
   be worse than the dimming it was meant to complete. pointer-events on the element covers
   the ::before halo too, because that rule sets no pointer-events of its own. */
function paintSubwayFocusReach(record) {
  const el = record.marker.getElement();
  if (!el) return;
  const off = isOffFocus(currentFocusRoutes(), record.latest?.route_id);
  if (off) {
    el.setAttribute("aria-hidden", "true");
    el.style.pointerEvents = "none";
    return;
  }
  el.removeAttribute("aria-hidden");
  el.style.pointerEvents = "";
}

/* MR4: THE SUBWAY'S TWO CANVAS FAMILIES, which are the populations the MR2 comment beside
   rootToken has named since it was written.

   THE RIBBON CASINGS, AND ONLY THE CASINGS. subwayRibbons carries every drawn polyline with
   the `part` it was drawn as, so the casing half is identifiable without touching the line
   half, whose colour is lineColor()'s fixed palette and has nothing to do with the theme.

   COLOUR ONLY, NEVER OPACITY, and that is the sharp edge here rather than anywhere else:
   applySubwayFocus below owns every ribbon's opacity and guards on it, so a swap that passed
   opacity would silently undo a rider's route focus the moment they changed theme. The two
   setStyle calls in this file touch disjoint options on purpose.

   THE STATION CIRCLES go through stationMarkStyle, the same pure function loadSubwayStations
   draws them with, so the drawn dot and the repainted dot are one expression. A station's
   ROUTES decide dot-or-ring, and they are read back off the registry rather than remembered
   here, because the registry is what the draw used too. */
registerCanvasFamily("subway ribbons", ({ paper }) => {
  for (const ribbon of subwayRibbons) {
    if (ribbon.part === "casing") ribbon.layer.setStyle({ color: paper });
  }
});

registerCanvasFamily("subway stations", ({ paper, ink }) => {
  for (const entry of stationRegistry) {
    if (entry.kind !== "subway" || !entry.marker || !entry.marker.setStyle) continue;
    entry.marker.setStyle(stationMarkStyle(entry.routes ?? [], ink, paper));
  }
});

function applySubwayFocus() {
  const focused = currentFocusRoutes();
  for (const ribbon of subwayRibbons) {
    const want = focusOpacity(focused, ribbon.routes, ribbon.part);
    if (ribbon.layer.options.opacity !== want) ribbon.layer.setStyle({ opacity: want });
  }
  const now = correctedNow();
  for (const record of trains.values()) {
    dimMarker(
      record.marker,
      vehicleMarkerAge("subways", subwaySystemAge(record.latest), record.latest, now),
      subwayFocusBase(record.latest),
    );
    paintSubwayFocusReach(record);
  }
}

/* Every route id the app has heard of: the ones with geometry and the ones only a train
   names. The second half is why the tags cannot be computed once and left: the static route
   list usually resolves BEFORE the first poll, so at draw time no train route is known and
   every ribbon would carry only its own id. Measured in the fixture world shaped like the
   real archive: J's ribbon came out {J} rather than {J, Z} until this was split out. */
function subwayKnownRoutes() {
  const ids = new Set(routeIndex.keys());
  if (typeof trains !== "undefined") {
    for (const record of trains.values()) if (record.latest?.route_id) ids.add(record.latest.route_id);
  }
  return ids;
}

/* Re-tag every ribbon with the set of routes that share it, from what the app knows NOW.
   Called by refreshSubwayKey, which is called when the route list resolves and from the
   poll tail, so a W train arriving at 6am is what puts W on the Broadway ribbons. */
function retagSubwayRibbons() {
  if (!subwayRibbons.length) return;
  const routes = [...routeIndex.entries()].map(([route, variants]) => ({ route, polylines: variants }));
  const known = subwayKnownRoutes();
  const cache = new Map();
  for (const ribbon of subwayRibbons) {
    if (!cache.has(ribbon.route)) cache.set(ribbon.route, ribbonRouteSet(ribbon.route, routes, known));
    ribbon.routes = cache.get(ribbon.route);
  }
}

// One train's focus multiplier, read live so every path that dims a marker composes the
// same two numbers.
function subwayFocusBase(train) {
  return focusOpacity(currentFocusRoutes(), train.route_id, "train");
}


function subwayArrivalsHtml(station, body) {
  // Skew-corrected now, reusing the staleness baseline from helpers.js.
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  // THE BOARD F03 WAS FOUND ON. Each row is qualified by its own feed group's content
  // time, so on a station several groups serve, a lagging group's rows say how old
  // they are and a current group's say nothing (6.2). The system line sits where the
  // R1 age line did and speaks only for an empty board; empty while fresh, so a live
  // popup is unchanged.
  const board = boardFreshness("subway", body, now);
  let html =
    `<b>${esc(station.name ?? station.id)}</b>` +
    boardLineHtml(boardSystemLine(board, stationArrivalsRows(body)));
  for (const dir of ["Northbound", "Southbound"]) {
    const arrivals = body.directions?.[dir] ?? [];
    html += `<div class="arr-dir">${dir}</div>`;
    if (!arrivals.length) {
      html += `<div class="arr-none">No trains</div>`;
      continue;
    }
    html += arrivals
      .map((a) => {
        const route = a.route_id ?? "";
        const textColor = readableTextOn(lineColor(route));
        const badge =
          `<span class="arr-badge" style="background:${lineColor(route)};color:${textColor}">` +
          `${esc(route || "?")}</span>`;
        return `${badge} ${esc(formatCountdown(a.arrival - now))}${qualifierHtml(arrivalQualifier(a, board))}`;
      })
      .join("<br>");
  }
  return html;
}


async function loadStations() {
  let stations;
  try {
    const res = await fetch("/api/subway-stops", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return false; // warming 503 (or transient error): retry
    stations = await res.json();
  } catch {
    return false;
  }
  if (!stations.length) return false; // failed-warmup []: retry until the backend heals
  const ink = inkColor();
  const paper = paperColor();
  for (const station of stations) {
    /* MR2: A DOT WHERE ONE ROUTE CALLS AND A RING WHERE TWO OR MORE DO, so the map says
       which stations are interchanges without a rider having to open anything. The decision
       and the two theme colours are helpers.js's (stationMarkStyle), which keeps it node
       tested and keeps this file free of a second copy of the rule; the colours are passed
       in rather than reached for, which is what lets MR4 restyle these in place.

       `pane` is NOT set here and that is deliberate: it lives on the RENDERER
       (L.canvas({ pane: "stationPane" })), and writing it onto the circleMarker would look
       identical on screen while moving the marker's own pane option off Leaflet's default.
       P2a pins both, which is how that distinction got written down. */
    /* THE ROUTES THEMSELVES, NOT THEIR COUNT (round 3, F9): a dot or a ring is a question
       about how many TRUNKS call here, and the J and the Z are one line taking turns. */
    const stationRoutes = station.routes ?? [];
    const marker = L.circleMarker([station.lat, station.lon], {
      ...stationMarkStyle(stationRoutes, ink, paper),
      renderer: stationRenderer,
    });
    /* THE NAME, as a permanent tooltip, and OUT OF THE ACCESSIBILITY TREE.

       A canvas circleMarker has no element, which is why station dots have never had an
       accessible name (the A2 footnote in shared.js says so and names the station panel as
       the equivalent). A permanent tooltip is the first DOM these stations have ever had,
       and leaving it in the tree would put 496 bare place names into the reading order
       whose only information is WHERE they are, which is the one thing that does not
       survive being spoken. That is the same argument MR1 made for the subway key, and the
       same equivalent answers it: the station panel is the text surface, it is searchable,
       and it is one Tab away. So the label is aria-hidden and a sighted rider reads it.

       It is therefore invisible to axe as well, which is why a11y.spec.js A1z2 measures its
       ink against its halo directly. ACCESSIBILITY.md carries both statements. */
    marker.on("tooltipopen", (event) => {
      const el = event.tooltip?.getElement?.();
      if (el) el.setAttribute("aria-hidden", "true");
    });
    marker.bindTooltip(station.name ?? station.id, {
      permanent: true,
      direction: "right",
      offset: [7, 0],
      className: stationLabelClass(stationRoutes),
      interactive: false,
      // BELOW THE VEHICLES, not above them: the default tooltipPane is 650 and markerPane is
      // 600, so a name painted over the bullet that identifies a train. shared.js says what
      // was measured.
      pane: "stationLabelPane",
      /* AND AT FULL OPACITY. Leaflet's Tooltip defaults to opacity 0.9 and writes it as an
         INLINE style in onAdd, which no stylesheet rule can reach, so every name rendered at
         90% group alpha while a11y.spec.js A1z3 measured the ink at 100% and reported a ratio
         about 23% better than the drawn one. The .stn-label rule enumerates the other five
         tooltip defaults it takes back off; this is the sixth, and it has to be an option
         because it is not a rule. */
      opacity: 1,
    });
    // Built once and used twice: the popup descriptor below and the A1 station
    // registry both need it, and two copies of a URL template is how the two
    // surfaces would eventually poll different endpoints.
    const arrivalsUrl = `/api/subway-arrivals/${encodeURIComponent(station.id)}`;
    bindStationPopup(marker, (m) => ({
      station,
      marker: m,
      body: null,
      url: arrivalsUrl,
      // Prepend any active subway alerts affecting this station above the arrivals.
      render: (s, b) => stationAlertsBlock("subway", s, b) + subwayArrivalsHtml(s, b),
    })).addTo(stationLayer);
    registerStation({
      key: `subway|${station.id}`,
      kind: "subway",
      systemLabel: "Subway",
      noun: "train",
      id: station.id,
      name: station.name ?? station.id,
      lat: station.lat,
      lon: station.lon,
      routes: station.routes ?? [],
      wheelchair: false, // the subway stops endpoint carries no accessibility field
      arrivalsUrl,
      marker,
      layer: stationLayer,
    });
  }
  /* THE BAND IS REPAINTED NOW THE LABELS EXIST. It was painted once at load, when there were
     no stations and so no way to tell a network with hubs from one whose backend served no
     routes at all. The band's fallback for the second case (helpers.js, LABEL_NO_HUB_ZOOM)
     can only be decided once the labels are in the DOM, which is here. */
  paintZoomBand();
  return true;
}


const trains = new Map(); // trip id -> { marker, routeId, latest }

// computeRouteSlice (slice a train's route polyline between its prev and next
// station) lives in helpers.js so it is node-testable and shared with the
// railroad route index; it is pure, so the caller resolves the route geometry
// (routeIndex.get) and passes it in.

function applyTrains(data) {
  // Skew-corrected now, same basis as the arrivals popups; trainLatLng interpolates
  // each train between its prev and next station (static fallback otherwise).
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  const seen = new Set();
  for (const train of data) {
    seen.add(train.trip_id);
    const record = trains.get(train.trip_id);
    if (record) {
      // route_id is in the key: a mid-trip route relabel must re-project onto the
      // new route's geometry rather than reuse the old route's cached slice.
      const segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route =
        record._segId === segId && record.latest._route
          ? record.latest._route
          : computeRouteSlice(train, routeIndex.get(train.route_id));
      record._segId = segId;
      record.latest = train;
      // THE LABEL TRACKS THE DATA: a subway train's next stop and direction change on
      // every poll while the marker is reused, and those are the whole content of its
      // name.
      setMarkerName(record.marker, subwayMarkerName(train, now));
      // Placed through the freeze clock, not the raw one: a retained group's trains
      // must not advance on a poll that only re-served them (C2), and since 6.3 neither
      // may a train whose own observation is past OBS_FRESH_S (subwayGlideAt).
      const fresh = subwaySystemFreshness(train);
      record.marker.setLatLng(trainLatLng(train, subwayGlideAt(train, now), record.fState));
      dimMarker(record.marker, vehicleMarkerAge("subways", fresh.age, train, now), subwayFocusBase(train));
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(trainIcon(train));
        record.routeId = train.route_id;
      }
      if (record.marker.isPopupOpen()) updatePopupKeepingFocus(record.marker);
    } else {
      const newRecord = { routeId: train.route_id, latest: train, fState: {} };
      newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route = computeRouteSlice(train, routeIndex.get(train.route_id));
      const fresh = subwaySystemFreshness(train);
      newRecord.marker = labeledMarker(
        trainLatLng(train, subwayGlideAt(train, now), newRecord.fState),
        {
          icon: trainIcon(train),
          // Dimmed AT CREATION, not by the sweep afterwards: a retained train first
          // seen during an outage (a reload mid-outage) must be dim on the very
          // first frame it exists, never full-opacity for a beat. This is the
          // invariant the "C2b" e2e spec pins, and the reason the retention flag and
          // this rendering ship in one commit. Since 6.3 the same holds for a train
          // whose own observation is old in a group that is not.
          // MR2: AND THE FOCUS BASE AT CREATION TOO, for the same reason the dimming is
          // applied here rather than left to the sweep: a train that arrives while a route
          // is focused must be drawn dim on its very first frame, never at full for a beat.
          opacity: markerOpacity(vehicleMarkerAge("subways", fresh.age, train, now), subwayFocusBase(train)),
        },
        subwayMarkerName(train, now),
      )
        .bindPopup(() => trainPopup(newRecord), POPUP_OPTIONS)
        .addTo(subwayLayer);
      trains.set(train.trip_id, newRecord);
    }
  }
  for (const [id, record] of trains) {
    if (!seen.has(id)) {
      subwayLayer.removeLayer(record.marker);
      trains.delete(id);
    }
  }
  /* AND THE KEY LEARNS WHAT IS ON THE MAP (round 2). What a bullet can light changes with
     the feed: a W bullet lights nothing at 3am and three ribbons at 8am. refreshSubwayKey
     rebuilds only when the set of bullets changes and otherwise just repaints their enabled
     state, so this costs a signature comparison per poll rather than a rebuilt toolbar. */
  refreshSubwayKey();
  /* AND EVERY MARKER'S REACH IS RE-DERIVED, so a train that arrives while a route is focused
     is out of the tree and out of the way on its first frame rather than after the next press.
     setIcon replaces the element, which is the other reason this is re-applied rather than
     written once: a route relabel would otherwise drop both attributes silently. */
  for (const record of trains.values()) paintSubwayFocusReach(record);
}

