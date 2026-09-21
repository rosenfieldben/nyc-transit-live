// Pure helpers shared by map.js. Loaded as a plain <script> before map.js,
// so the top-level declarations land in the shared global scope — no build
// step. The CommonJS guard at the bottom makes the same file loadable by
// `node --test` for unit testing.

// Feed data goes into HTML popups/icons — escape it.
function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Deterministic color per bus route: hash the route id onto the hue wheel.
// NO TEXT IS EVER PRINTED ON A BUS COLOUR: it is a polyline colour and a heading colour,
// never a chip fill, so this wheel owes 3:1 as a non-text indicator rather than 4.5 as a
// background for ink. The heading use goes through readableInk, which the node test
// sweeps across all 360 hues. An earlier draft of this comment changed the fallback to
// #666666 on the grounds that white text on #777 measures 4.48; that reasoning was
// borrowed from the chips and does not apply here, so the value is unchanged.
function routeColor(routeId) {
  if (!routeId) return "#777777";
  let h = 0;
  for (const c of routeId) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `hsl(${h % 360}, 75%, 40%)`;
}

// Our own palette, grouped by trunk line (deliberately not the MTA's official
// colors — see README note on MTA branding).
const LINE_COLORS = {
  1: "#c0392b", 2: "#c0392b", 3: "#c0392b",
  4: "#1e8449", 5: "#1e8449", 6: "#1e8449",
  7: "#8e44ad",
  A: "#1f5fbf", C: "#1f5fbf", E: "#1f5fbf",
  B: "#d68910", D: "#d68910", F: "#d68910", M: "#d68910",
  G: "#58a832",
  J: "#7d5a3c", Z: "#7d5a3c",
  L: "#7f8c8d",
  N: "#e6b800", Q: "#e6b800", R: "#e6b800", W: "#e6b800",
  GS: "#566573", FS: "#566573", H: "#566573", S: "#566573",
  SI: "#34495e",
};

function lineColor(routeId) {
  if (!routeId) return "#555555";
  return LINE_COLORS[routeId] ?? LINE_COLORS[routeId[0]] ?? "#555555";
}

/* ---------------- MR2: how the subway is drawn ----------------

   THE PURE HALF OF STAGE MR2, here rather than in systems/subway.js so a node test can
   ask it directly and so the two surfaces that have to agree (the map and the Key panel)
   read one answer. Nothing here touches the DOM, Leaflet or a clock; the caller resolves
   the two theme colours and passes them in, which is also what lets a theme swap restyle
   a canvas layer without rebuilding it.

   THE PALETTE IS THIS APP'S OWN, and that is a ruling rather than an oversight. The v3.1
   handoff gives the authority's official trunk hexes and a circular lettered bullet; the
   repository's own README says route symbols require a license and to use our own colours
   and markers, and round 3 of the map-redesign review ruled the README wins (R1). So every
   ribbon takes lineColor()'s answer and every bullet keeps the rounded rectangle
   systems/subway.js has always drawn. */

// The yellow trunk, which is the one that has to be drawn last: its #e6b800 disappears
// under any of the darker trunks it shares track with, and a rider reading Broadway would
// see the 1-2-3's red where the N-Q-R-W runs beside it.
const YELLOW_TRUNK_ROUTES = ["N", "Q", "R", "W"];

// lineColor's rule, asked of the trunk rather than of the palette: an exact id first, then
// its first character, so a lettered variant lands on the same trunk as its parent.
function isYellowTrunk(routeId) {
  const id = String(routeId ?? "");
  if (!id) return false;
  return YELLOW_TRUNK_ROUTES.includes(id) || YELLOW_TRUNK_ROUTES.includes(id[0]);
}

// Route ids ordered for drawing: yellow last, everything else in the order it arrived.
// STABLE INSIDE EACH GROUP, so a static payload that lists its routes differently moves
// nothing a rider can see except the one thing this function exists to decide.
function trunkDrawOrder(routeIds) {
  const ids = [...routeIds];
  return [...ids.filter((id) => !isYellowTrunk(id)), ...ids.filter((id) => isYellowTrunk(id))];
}

// The two polylines one shape is drawn as. The casing is the paper the line is printed on:
// it is what separates a trunk from the basemap and from the trunk beside it.
const RIBBON_CASING_WEIGHT = 6.5;
const RIBBON_CASING_OPACITY = 0.9;
const RIBBON_LINE_WEIGHT = 4;
const RIBBON_LINE_OPACITY = 1;

/* ROUTE FOCUS IS OPACITY AND NOTHING ELSE, and it COMPOSES with the freshness contract's
   dimming rather than replacing it. markerOpacity and dimMarker already take a `base`
   multiplier, so a train's opacity is its own observation's answer times this one: a stale
   train on the focused route stays dim, and a stale train off it is dimmed twice. Nothing
   in section 3.3's rendering is touched to make that true.

   The ribbons are not markers and have no age, so for them these ARE the opacities, which
   is why the unfocused casing is 0 rather than a factor: a casing at 18% of paper is a grey
   smear, and the design asks for the line alone to survive. */
const FOCUS_DIM_LINE = 0.18;
const FOCUS_DIM_CASING = 0;
const FOCUS_DIM_TRAIN = 0.15;

/* The opacity (for a ribbon) or the base (for a train) one part draws at, given the SET of
   routes currently focused. An empty set means nothing is focused and every part is at full.

   ROUND 2 MADE THIS MEMBERSHIP RATHER THAN EQUALITY. Focusing a route used to compare one id
   against one id, which is wrong wherever two routes share track: the archive draws J and
   not Z, so a Z bullet compared against J's ribbon matched nothing and dimmed the whole map.
   A ribbon carries the SET of routes that share it and a bullet focuses the union of the
   sets it belongs to, so this takes a set on one side and one id on the other.

   `routeIds` is accepted as an array (a ribbon's set) or a single id (a train's route), so
   one function answers for both surfaces. */
function focusOpacity(focusRoutes, routeIds, part) {
  const full = { line: RIBBON_LINE_OPACITY, casing: RIBBON_CASING_OPACITY, train: 1 };
  const dimmed = { line: FOCUS_DIM_LINE, casing: FOCUS_DIM_CASING, train: FOCUS_DIM_TRAIN };
  if (!(part in full)) return 1;
  const focus = Array.isArray(focusRoutes) ? focusRoutes : focusRoutes ? [focusRoutes] : [];
  if (!focus.length) return full[part];
  const mine = (Array.isArray(routeIds) ? routeIds : [routeIds]).map((id) => String(id ?? "")).filter(Boolean);
  return mine.some((id) => focus.map(String).includes(id)) ? full[part] : dimmed[part];
}

// What the page live region says when focus moves. The bullet's own label does not change
// with the state (it stays "Focus route 4"), which is what lets it carry aria-pressed; this
// is the sentence that tells a rider what just happened and how to undo it.
/* WHETHER ONE MARK IS OUTSIDE THE CURRENT FOCUS. The same membership question focusOpacity
   answers, asked as a boolean, because round 3's F4 remedy needs it for something other than
   an opacity: an off-focus marker also leaves the accessibility tree and stops taking clicks.
   Nothing focused means nothing is off focus. */
function isOffFocus(focusRoutes, routeId) {
  const focus = (Array.isArray(focusRoutes) ? focusRoutes : focusRoutes ? [focusRoutes] : []).map(String);
  if (!focus.length) return false;
  return !focus.includes(String(routeId ?? ""));
}

function routeFocusAnnouncement(routeId) {
  return routeId ? `Focused on the ${routeId}; press again to clear.` : "Route focus cleared.";
}

function routeFocusLabel(routeId) {
  return `Focus route ${routeId}`;
}

/* ----- MR2 round 2: the key, built from the data rather than from a table -----------------

   THE TABLE WAS WRONG AND COULD NOT BE RIGHT. MR1 hard-coded ten trunks and twenty-three
   bullets. Measured against the real static archive, that table and the network disagree in
   both directions: Z, W and S draw no ribbon at all, so pressing one dimmed the whole map
   and highlighted nothing, while FS, GS, H and SI are drawn and had no bullet, so the
   Staten Island Railway and every shuttle were unfocusable. MR1's bullets were display only,
   so the mismatch was invisible; MR2 made them controls, which is what made three of
   twenty-three controls do nothing.

   SO THE KEY IS DERIVED. The universe of bullets is the loaded route list, plus every route
   id seen on a train, plus the declared aliases below; the groups are the trunks, which are
   the routes that share lineColor()'s answer; and focus is MEMBERSHIP in a ribbon's route
   set rather than equality with a ribbon's route id.

   WHAT A RIBBON'S ROUTE SET IS. A drawn route's ribbon carries its own id and the ids of
   every trunk-mate that has no geometry of its own, because that is what sharing track
   means in this data: the archive draws J and not Z, N/Q/R and not W, GS/FS/H and not S,
   and in each case the missing route runs on its neighbour's line. So J's ribbon is {J, Z},
   each of N, Q and R's is {that, W}, and each shuttle's is {that, S}.

   AND A BULLET FOCUSES THE UNION of the sets of every ribbon it belongs to. Z lights the
   J/Z ribbon and the trains on either; W lights all three Broadway ribbons and their trains;
   1 lights only the 1, because the 1's ribbon is shared with nothing. A bullet whose set
   draws nothing right now is still drawn, disabled and with a reason, and it never dims the
   map: a control that silently does nothing is worse than one that says why. */

// One bullet standing for several feed route ids. The shuttles are the case: the map draws
// three of them (GS, FS, H) and a rider knows one S. An alias COLLAPSES ids that exist; it
// never invents a bullet for ids that do not, so a world with no shuttles has no S.
const SUBWAY_KEY_ALIASES = { S: ["GS", "FS", "H"] };

// Digits before letters, then lexicographic: the order the MTA prints and the order MR1's
// table happened to be written in, derived rather than transcribed.
function compareRouteIds(a, b) {
  const digit = (id) => /^\d/.test(id);
  if (digit(a) !== digit(b)) return digit(a) ? -1 : 1;
  return a < b ? -1 : a > b ? 1 : 0;
}

// Every route id the key could show, alias-collapsed and ordered. `routes` is the loaded
// route list ([{route, polylines}]); `trainRoutes` is whatever is on the map.
function subwayRouteUniverse(routes, trainRoutes = []) {
  const ids = new Set();
  for (const entry of routes ?? []) if (entry?.route) ids.add(String(entry.route));
  for (const id of trainRoutes) if (id) ids.add(String(id));
  // Collapse an alias only when at least one of its targets is really here.
  for (const [alias, targets] of Object.entries(SUBWAY_KEY_ALIASES)) {
    if (targets.some((target) => ids.has(target))) {
      for (const target of targets) ids.delete(target);
      ids.add(alias);
    }
  }
  return [...ids].sort(compareRouteIds);
}

// The ids a bullet stands for: itself, or its alias targets.
function bulletRouteIds(bullet) {
  return SUBWAY_KEY_ALIASES[bullet] ?? [bullet];
}

// Which routes have geometry of their own, from the loaded list.
function drawnRouteIds(routes) {
  return new Set((routes ?? []).filter((entry) => (entry?.polylines ?? []).length > 0).map((entry) => String(entry.route)));
}

/* The set of routes one drawn route's ribbon carries: itself, plus every route in the same
   trunk that has no geometry of its own. `known` is every route id the app has heard of,
   BEFORE aliasing, because the orphans are feed ids (GS is drawn, S is not a feed id at
   all, so S is attached through its alias by the caller). */
function ribbonRouteSet(routeId, routes, known) {
  const drawn = drawnRouteIds(routes);
  const trunk = lineColor(routeId);
  const orphans = [...known].filter((id) => id !== routeId && !drawn.has(id) && lineColor(id) === trunk);
  return [routeId, ...orphans].sort(compareRouteIds);
}

/* THE FOCUS SET IS THE BULLET'S OWN IDS, and that is a correction the round's own new spec
   made rather than a thing designed in. The first version focused the TRANSITIVE CLOSURE of
   the ribbons a bullet touches, which is right for Z (the J/Z ribbon) and wrong for N: N's
   ribbon carries W because the app has no W shape, W's neighbours Q and R carry W for the
   same reason, so focusing N reached Q and R through W and lit the whole Broadway trunk when
   a rider asked for one route of it. Membership is therefore asymmetric on purpose: a RIBBON
   lights when its route set contains one of the bullet's ids, and the bullet's ids never
   grow. focusRoutesForBullet is bulletRouteIds under another name, and it has that name so
   the call sites read as the decision rather than as a coincidence. */
const focusRoutesForBullet = bulletRouteIds;

/* The routes a press would leave lit, which is NOT the focus set: it is the focus set plus
   whoever else rides the ribbons those ids ride. Pressing Z lights the ribbon J is drawn as,
   so the Z bullet's title says "shares track with J" even though no J TRAIN lights. Used for
   the title and, as a boolean, for the enabled state. */
function bulletTrackSet(bullet, routes, trainRoutes = []) {
  const ids = bulletRouteIds(bullet);
  const known = new Set([
    ...(routes ?? []).map((entry) => String(entry?.route)).filter(Boolean),
    ...trainRoutes.map(String).filter(Boolean),
    ...ids,
  ]);
  const out = new Set(ids);
  for (const drawnId of drawnRouteIds(routes)) {
    const set = ribbonRouteSet(drawnId, routes, known);
    if (set.some((id) => ids.includes(id))) for (const id of set) out.add(id);
  }
  return [...out].sort(compareRouteIds);
}

/* Whether pressing a bullet would light anything: a ribbon whose set it is in, or a train of
   its own. A bullet that would light nothing is drawn disabled rather than left to dim the
   map for no reason. This asks the RIBBONS and not the ids, because Z has no shape and no
   train of its own and still lights the ribbon J is drawn as. */
function bulletDrawsSomething(bullet, routes, trainRoutes = []) {
  const ids = bulletRouteIds(bullet);
  const known = new Set([
    ...(routes ?? []).map((entry) => String(entry?.route)).filter(Boolean),
    ...trainRoutes.map(String).filter(Boolean),
    ...ids,
  ]);
  for (const drawnId of drawnRouteIds(routes)) {
    if (ribbonRouteSet(drawnId, routes, known).some((id) => ids.includes(id))) return true;
  }
  return trainRoutes.map(String).some((id) => ids.includes(id));
}

// The sentence a disabled bullet carries, and the one an S-shaped alias carries whether or
// not it is disabled. Both are titles rather than labels: the accessible NAME stays
// "Focus route S" so it does not move with the state (the aria-pressed rule MR1 round 2
// settled), and the title is where the detail goes.
function bulletTitle(bullet, trackSet, enabled) {
  const ids = bulletRouteIds(bullet);
  const stands = ids.length > 1 ? `${bullet} is ${ids.join(", ")}. ` : "";
  if (!enabled) return `${stands}Nothing on the map right now for ${bullet}.`;
  const others = trackSet.filter((id) => !ids.includes(id));
  const shares = others.length ? ` Shares track with ${others.join(", ")}.` : "";
  return `${stands}Focus ${bullet}.${shares}`.trim();
}

/* The whole key, grouped by trunk. Groups are ordered by their first bullet, and bullets
   inside a group by the same comparator, so the key's order is a function of the data. */
function subwayKeyModel(routes, trainRoutes = []) {
  const universe = subwayRouteUniverse(routes, trainRoutes);
  const groups = new Map();
  for (const bullet of universe) {
    const enabled = bulletDrawsSomething(bullet, routes, trainRoutes);
    const color = lineColor(bulletRouteIds(bullet)[0]);
    const entry = {
      id: bullet,
      focus: focusRoutesForBullet(bullet),
      enabled,
      title: bulletTitle(bullet, bulletTrackSet(bullet, routes, trainRoutes), enabled),
    };
    if (groups.has(color)) groups.get(color).bullets.push(entry);
    else groups.set(color, { color, bullets: [entry] });
  }
  return [...groups.values()];
}

/* A station is a LOCAL dot when the routes calling there belong to ONE trunk and a TRANSFER
   ring when they belong to two or more.

   IT COUNTS TRUNKS, NOT ROUTE IDS (round 3, F9). Counting ids made a skip-stop pair into an
   interchange: Marcy Av is served by the J and the Z, which are one line taking turns at the
   same platform, and Hewes St, Lorimer St and the rest of the Jamaica line are the same. So
   are the local/express pairs, every ["A","C"] and ["4","5"] stop. All of them were drawn
   with the paper transfer ring and, worse, given the `hub` class that the zoom-12 band exists
   to keep sparse. lineColor() already knows which ids are one line, because they share a
   colour by definition, so the trunk count is the question and the id count was a proxy for
   it that is wrong in exactly the cases the network has most of.

   ZERO ROUTES IS LOCAL, not transfer, and that is the direction that matters: the routes
   field is optional on the stops endpoint, and a backend serving none would otherwise turn
   all 496 stations into transfer rings, which is a claim about the network rather than a
   missing value. Measured against the real static archive, 171 stations list one route and
   325 list two or more; none lists zero, which is exactly why the fallback has to be chosen
   deliberately rather than discovered. */
const STATION_LOCAL_RADIUS = 3.5;
const STATION_TRANSFER_RADIUS = 4.5;
const STATION_TRANSFER_WEIGHT = 2;

// The distinct trunks calling at a station. Two unknown ids collapse into one trunk, which is
// the conservative direction: an id lineColor() cannot place must not invent an interchange.
function stationTrunks(routes) {
  return new Set(
    (routes ?? [])
      .map((id) => String(id ?? ""))
      .filter(Boolean)
      .map((id) => lineColor(id)),
  );
}

function isTransferStation(routes) {
  return stationTrunks(routes).size >= 2;
}

// The circleMarker options one station is drawn with. `ink` and `paper` are resolved by the
// caller from the theme tokens, so this stays pure and a theme swap is a setStyle rather
// than a rebuild.
function stationMarkStyle(routes, ink, paper) {
  return isTransferStation(routes)
    ? {
        radius: STATION_TRANSFER_RADIUS,
        fillColor: paper,
        fillOpacity: 1,
        color: ink,
        weight: STATION_TRANSFER_WEIGHT,
        stroke: true,
      }
    : { radius: STATION_LOCAL_RADIUS, fillColor: ink, fillOpacity: 1, color: ink, weight: 0, stroke: false };
}

/* The tooltip class one station's name is drawn with. A hub is the same station a transfer
   ring is, so the two read one predicate rather than two.

   `subway` IS A POSITIVE CLASS AND MR4 ADDED IT, which is the carry-forward paid at the
   source. `.stn-label` began as the subway's alone, so a count of it meant "subway station
   names". MR3 put ~300 commuter-rail names in the class and every sentinel became
   `.stn-label:not(.rail)`; MR4 put the ferry's dock names in it and those sentinels were
   wrong again, silently, because a dock is not `.rail` either. The census pin caught it on
   its first outing (2 subway labels read as 4).

   An exclusion list grows with every family and is wrong once per stage. A family's own
   class cannot be widened by a family that does not carry it, so `.stn-label.subway` is the
   last version of this selector anyone has to write. */
function stationLabelClass(routes) {
  return isTransferStation(routes) ? "stn-label subway hub" : "stn-label subway";
}

/* THE ZOOM GATE, as a band rather than a number, because CSS cannot compare integers. The
   root carries data-zoom="<n>" and the stylesheet enumerates the zooms in each band, which
   is what the reference stylesheet does; this is the same decision in one place a node test
   can ask, so the enumeration in the stylesheet and the attribute the map writes cannot
   drift apart without a test saying so. */
const LABEL_HUB_ZOOM = 12;
const LABEL_ALL_ZOOM = 14;

/* AND ONE FALLBACK ZOOM, for the world where no station is a hub (round 3, F1). The routes
   per station come from stop_times.txt, which is NOT a required member of the subway static
   archive: load_subway_station_routes returns {} on any failure and the endpoint then serves
   routes: [] for all 496 stations while the status stays "ready". Every station is then a
   local, no label carries the `hub` class, and "hubs from 12" correctly reveals nothing. That
   is the right answer to that data and the wrong thing to show a rider, who gets a map with
   no names at the opening zoom and at the City preset while the Names button reads pressed.

   So with no hubs the band skips the hubs step and shows every name from 13: one zoom later
   than the hub band, because with no hub to thin the field 12 is the zoom the collision
   measurements found worst (89% of painted labels overlapping another), and one zoom earlier
   than the all band, because a rider should not have to reach 14 to see any name at all.
   THE BACKEND HALF IS ITS OWN BRANCH: stop_times.txt should be a required member, the rule
   PATH and the ferry already apply to shapes.txt, and the ledger records it with its three
   consumers named. */
const LABEL_NO_HUB_ZOOM = 13;

function labelZoomBand(zoom, hasHubs = true) {
  if (!Number.isFinite(zoom)) return "none";
  if (!hasHubs) return zoom >= LABEL_NO_HUB_ZOOM ? "all" : "none";
  if (zoom < LABEL_HUB_ZOOM) return "none";
  return zoom >= LABEL_ALL_ZOOM ? "all" : "hubs";
}

/* AND THE RAIL BAND, WHICH IS ITS OWN NUMBER AND ITS OWN ATTRIBUTE (MR3). A commuter rail
   station shows its name from zoom 11, three zooms before the subway shows all of its and one
   before the subway shows any, and the reason is density rather than importance: there are
   about 300 rail stations across the whole region against 496 subway stations inside the city,
   so at 11 the rail names are readable where the subway's would be a wall. That is the zoom
   the design gives ("Shown from zoom 11") and it is one number, not a band of three, because
   a rail station is never a hub: isTransferStation is a subway predicate over subway trunks,
   and the hub class is what the middle band reveals. So there is nothing for a middle step to
   do here and two values are the honest shape.

   ITS OWN ROOT ATTRIBUTE, data-rail-label-band, rather than a third value on data-label-band:
   the two bands overlap (at zoom 14 both say "all") and one attribute cannot hold two answers.
   Written by the same paintZoomBand call, read by its own pair of rules in style.css, and
   gated by the same Names toggle, which is a preference over both. */
const RAIL_LABEL_ZOOM = 11;

function railLabelBand(zoom) {
  if (!Number.isFinite(zoom)) return "none";
  return zoom >= RAIL_LABEL_ZOOM ? "all" : "none";
}

/* THE FERRY'S DOCK NAMES, ON THEIR OWN ATTRIBUTE FOR MR3's REASON AND A SHARPER ONE.

   The design puts dock names at the same zoom as subway station names ("names from 14"), so
   MR4 first hung them on `data-label-band` and wrote down that they were riding the subway's
   answer. Round 1's review measured what that costs: the subway's band has a DEGRADED value
   the ferry has no business inheriting. When the backend serves no routes for any subway
   station (a state helpers.js documents at LABEL_NO_HUB_ZOOM and D2z exercises), the subway's
   band reads "all" from 13 instead of hubs from 12, and every dock name came on one zoom
   early with it, for a reason that has nothing to do with the ferry.

   SAME ZOOM, DIFFERENT QUESTION, which is exactly what MR3 said when it gave the rail names
   `data-rail-label-band`: "the two bands overlap and one attribute cannot hold two answers".
   The ferry's answer is the zoom and nothing else, because a dock has no interchange to
   reveal and no degraded state to fall back to. */
const FERRY_LABEL_ZOOM = LABEL_ALL_ZOOM;

function ferryLabelBand(zoom) {
  if (!Number.isFinite(zoom)) return "none";
  return zoom >= FERRY_LABEL_ZOOM ? "all" : "none";
}

/* THE NAMES TOGGLE'S SENTENCE, round 3. The button flips a preference that outlives the
   zoom, so it stays operable everywhere; what it must not do is claim an effect it does not
   have. Below zoom 12 the band is "none" and no name can show whatever the preference says.
   In the "hubs" band a network whose stations carry no routes has no hub either, and that is
   a reachable backend state rather than a hypothetical: stop_times.txt is not a required
   member of the static archive (backend/static_data.py), load_subway_station_routes returns
   {} on any failure, and the endpoint then serves routes: [] for all 496 stations while the
   status stays "ready". Every station is a local, no label carries the hub class, and at the
   opening zoom 12 and the City preset's 13 nothing renders while the button reads pressed.

   The band's arithmetic is not the bug and is not changed here: "hubs from 12" showing no
   hubs is the right answer to that data. What was wrong is a control claiming otherwise in
   silence, so the toggle says which of the three it is. THE BACKEND HALF IS NOT THIS
   BRANCH'S: the railroad warmup gates its ready on a non-empty index and the subway warmup
   does not, and that is one line in a file this stage does not touch. */
/* MR3 round 4: EVERY BAND IT IS GIVEN, not the subway's alone. The toggle hides commuter-rail
   names too (style.css's `:root[data-labels="off"] .stn-label.rail`), and the two bands do not
   agree: rail names show from zoom 11 and the subway's first band opens at 12. Reading only the
   subway's, the sentence said "none at this zoom, zoom in to see them" at zoom 11 while the
   press had just hidden or revealed every rail name on screen, which is the one thing this
   sentence exists to prevent. Variadic rather than a second named parameter so that stage 4's
   families can be added at the call site without touching the grammar.

   NO BANDS AT ALL IS "ON", NOT "NONE". [].every() is vacuously true, so the obvious spelling
   would make a caller that passes nothing claim the zoom shows no names. A caller that names no
   band has told us nothing about the zoom, and the unqualified sentence is the honest answer. */
function namesToggleAnnouncement(on, ...bands) {
  if (!on) return "Station names off.";
  const shown = bands.filter((band) => band !== "none");
  if (bands.length && !shown.length) return "Station names on; none at this zoom, zoom in to see them.";
  return "Station names on.";
}

/* And the tooltip that says why the map looks different, in the one state a rider cannot work
   out from what is on screen: no station lists its routes at all, so there is no ring anywhere
   and no name is marked as an interchange. Empty otherwise, because a control that explains
   itself when there is nothing to explain is noise.

   THE CONDITION IS "NOTHING LISTS ANY ROUTES", NOT "NOTHING IS A HUB", and the difference is a
   sentence that would otherwise be false. A network can legitimately have no interchange while
   every station lists its routes: the hermetic fixture is exactly that, two stations of three
   ids on one trunk each. Keyed on the hub count this tooltip appeared there and said "no station
   lists the routes that call there" over a map where both of them did. The band's fallback is
   keyed on hubs, correctly, because "is there a hub label to reveal" is what the band asks; the
   SENTENCE is about the data, so it is keyed on the data. Caught by writing D2z. */
function namesToggleTitle(routedCount, stationCount = 1) {
  if (routedCount || !stationCount) return "";
  return "No station lists the routes that call there, so every name shows from zoom 13 and none is marked as an interchange.";
}

// Whether one station's name is on screen: the band, the station's own kind, and the Names
// toggle, which overrides both.
function stationLabelShown(zoom, routes, labelsOn, hasHubs = true) {
  if (!labelsOn) return false;
  const band = labelZoomBand(zoom, hasHubs);
  if (band === "all") return true;
  return band === "hubs" && isTransferStation(routes);
}

/* RAILROAD COLOURS ARE THE AGENCY'S, AND THE HASH THAT STOOD HERE IS GONE (ruling R1).
   `railroadColor(routeId)` hashed a route id into a fixed ten-colour palette, which was never any
   agency's palette and could not be: MR3 added `route_color` to /api/railroad-routes and drew the
   branch lines and the tags from it, and this function stayed behind on three surfaces, so LIRR|1
   and MNR|1 (two agencies, two published greens) both drew one brown. Finding N6 is that shape,
   "two answers for one judgment", and R1 is the ruling that paid it: the board badge, the popup
   title's ink and the panel chip all resolve `railroadBranch` now (systems/railroad.js), through
   `railBranchColor` and `railBranchPaint` below, which is the same lookup the tag uses.

   ITS NOTE IS CARRIED, not deleted with it, because two comments cited this one as the authority
   for "a fill that has to move rather than an ink that has to be chosen": v2's #607d8b carried
   white at 4.37 and dark at 3.98, so neither ink could rescue it. That measurement and its
   conclusion now live at railBranchPaint, which is the function that implements the remedy. */

/* ----- A3: one breakpoint, named once ----------------------------------------
   700px is the mobile boundary, and it is declared here as well as in style.css because
   two things need it: the media queries that lay the page out, and the status line's
   compaction, which is composed in JavaScript. A number written twice drifts, so the
   node test asserts style.css actually uses this value. The 1100px docked threshold is a
   different decision and is deliberately untouched by A3.

   Injectable like the motion gate above, for the same reason: a test needs to state the
   viewport rather than resize a real window, and node has no matchMedia at all. */
const MOBILE_MAX_WIDTH_PX = 700;
const MOBILE_QUERY = `(max-width: ${MOBILE_MAX_WIDTH_PX}px)`;

function narrowViewport(mql = null) {
  const query = mql || (typeof matchMedia === "function" ? matchMedia(MOBILE_QUERY) : null);
  if (!query) return false; // no matchMedia (node): the roomy layout, same as before A3
  return !!query.matches;
}

/* ----- A3: the status line's composition rule --------------------------------
   ONE STATED ORDER: counts, then clock, then problems. Counts are what a rider glances
   at, the clock is ambient, and a problem is the thing they need to read whole. The
   line WRAPS rather than truncating, so nothing is ever cut off at any width; what
   compaction does is drop the clock's SECONDS, which is the only part of the line that
   carries no information a rider acts on.

   NEVER TRUNCATE A PROBLEM STATEMENT. This is the hard rule and the reason composition
   lives in one testable function instead of a template literal at the call site: "Bus:
   upstream 502" clipped to "Bus: upstr" is worse than no status line at all, because it
   looks like the page merely ran out of room rather than that something is broken. If a
   future narrow layout needs to shed something, it sheds the clock and then the counts,
   in that order, and the problems stay whole.

   THE PROBLEM SEPARATOR IS A COLON, NOT AN EM DASH, by ruling, and the reason is the one
   that kept the middot out of the spoken train names in A2: an em dash is visual
   punctuation a screen reader renders as noise or as the words "em dash", while a colon
   is read as the pause it looks like. The line shipped with a U+2014 here before A3;
   recomposing it was the moment to fix that rather than carry it forward.
   The middot between counts and clock stays. It separates two glanceable facts rather
   than introducing a statement, and it is what shipped. */
function statusLineText({ counts, clock, problems = [] } = {}, { compact = false } = {}) {
  const time = compact ? String(clock ?? "").replace(/:\d\d(?=(\s|$))/, "") : clock;
  const stated = statusNoteText(problems);
  const head = [counts, time].filter(Boolean).join(" \u00b7 ");
  if (!stated) return counts ? `${counts} \u00b7 updated ${time}` : `updated ${time}`;
  return `${head}: ${stated}`;
}

/* MR1: THE PROBLEMS, ON THEIR OWN, and the one place the "; " join and the drop-empties
   rule live.

   The redesign's header renders the status line's three parts in three places: the counts
   become the feed strip's per-feed counts, the clock becomes row 1's, and the problems
   become the strip's TRAILING NOTE. So the page no longer composes the line above, and
   this is what it renders instead. Extracted rather than reimplemented at the call site
   because the two must not be able to word the same trouble two ways: statusLineText now
   calls this, and the node test asserts the composed line's tail IS this string, so the
   retained composition is the oracle the note is checked against rather than dead code.

   Every rule the line owed the problems it still owes: nothing is truncated, nothing is
   abbreviated, falsy entries are dropped rather than rendered as empty segments, and the
   separator is a semicolon and a space. What this deliberately does NOT do is decide
   anything: `problems` arrives already composed by staleness() (plus any hard upstream
   error), and re-deriving it from the freshness index is the defect mutation M4 exists to
   catch. Empty string when there is nothing wrong, which is the common case. */
function statusNoteText(problems = []) {
  return (problems ?? []).filter(Boolean).join("; ");
}

/* ----- MR1: the theme's two decisions ---------------------------------------------------
   The only two parts of theming that are decisions rather than plumbing, lifted out so they
   are testable under `node --test` and so systems/shared.js has nothing to get wrong.

   THE PAGE MUST RENDER WITH STORAGE EMPTY, which is what themeChoice is for. localStorage
   returns null when nothing was ever stored and THROWS in a private window with site data
   blocked, so the caller passes whatever it managed to read (or null) and this decides. An
   unrecognised value is treated as no value rather than passed through: a stored "Dark" or
   "" would otherwise reach the root attribute and match neither token block, leaving the
   page in the unqualified light set by accident rather than by choice. The authored value is
   the second-chance answer because index.html writes data-theme="light" itself, so the
   markup is a real answer and not a guess. */
function themeChoice(stored, authored) {
  if (stored === "dark" || stored === "light") return stored;
  return authored === "dark" ? "dark" : "light";
}

// The other side of the toggle. Two values, so this is one line, and it is here rather than
// inline so the toggle and any future caller cannot disagree about what "the other one" is.
function nextTheme(current) {
  return current === "dark" ? "light" : "dark";
}

/* ----- MR1: the feed strip -----------------------------------------------------------
   The eight feeds of the redesign's row 2, in the order the design lists them, and the
   pure part of what each button shows. The DOM wiring is in systems/shared.js; everything
   decidable from a count and an age is decided here, so it is testable under `node --test`
   without a browser.

   ONE BUTTON PER FEED, WHICH IS NOT ONE BUTTON PER SOURCE. LIRR and Metro-North are two
   feeds inside the one `railroads` source: they poll together, they fail apart, and the
   per-system freshness index has always carried them separately (`railroads|LIRR` and
   `railroads|MNR`). The old panel's single "Railroads" checkbox was the odd one out, and
   splitting it is why MR1 splits the railroad layer groups per agency.

   AIRTRAIN HAS NO SOURCE AT ALL. It is static timetable data loaded once, with no
   realtime feed anywhere behind it, so it has no age to report and no vehicles to count.
   Its dot is the scheduled-only gray permanently, and it shows no count, because a count
   of its stations would be a different kind of number wearing the same badge. */
const FEEDS = [
  { key: "subway", name: "Subway", source: "subways", system: null, tick: "#0039A6" },
  { key: "buses", name: "Buses", source: "buses", system: null, tick: "#605d5d" },
  { key: "lirr", name: "LIRR", source: "railroads", system: "LIRR", glyph: "L" },
  { key: "mnr", name: "Metro-North", source: "railroads", system: "MNR", glyph: "M" },
  { key: "njt", name: "NJ Transit", source: "njt", system: "njt", glyph: "NJ" },
  { key: "path", name: "PATH", source: "path", system: "path", tick: "#d93a30" },
  { key: "ferry", name: "Ferry", source: "ferry", system: "ferry", tick: "#00839c" },
  { key: "airtrain", name: "AirTrain", source: null, system: null, tick: "#6d6e71" },
];

/* The freshness dot's three states, and they are the FEED's, not its observations'.
   A feed can be green while a third of its trains are drawn dimmed, and the v3 brief says
   in as many words that this is correct: the dot reflects the poll, the dimming reflects
   each observation.

   A NULL AGE IS NOT LIVE. A feed that has never decoded anything has no freshness to
   report, and "live" is the one answer that would be a lie; the status line has always
   called that state `not reporting` and this returns "stale" for it, which is the same
   judgment in the dot's smaller vocabulary. That is also what a rider sees for the first
   instant after load, beside a count of zero, which is honest. */
function feedDotState({ scheduled = false, age = null } = {}) {
  if (scheduled) return "scheduled";
  return age != null && !staleAge(age) ? "live" : "stale";
}

/* MR5 (ruling Q2): WHAT A FEED'S STATE IS CALLED, lifted out of the tooltip so that one answer
   can be given on two surfaces. The words are unchanged and nothing here is coined: they are the
   clause feedTooltip has carried since MR1, with the button's action taken off the end.

   WHY IT HAD TO COME OUT. Section 5 gives every popup a freshness footer, and the ruling is that
   the footer's square IS the feed strip's dot at the popup, with "its accessible name from the same
   helper the strip's dot uses so the state is said one way on both surfaces". feedTooltip cannot be
   that helper, because its words END in what pressing the button will do ("hide Subway"), and a
   popup footer has no button to press. Re-deriving the clause in the footer would have been a
   second answer to one question, which is the shape of finding N6 one stage earlier.

   AND A FEED WITH NO AGE SAYS SO. "As of null ago" is the failure this avoids; the word is
   `not reporting`, which is the status line's own for the same state. */
function feedStateWords({ state, age = null } = {}) {
  if (state === "scheduled") return "Scheduled";
  if (state === "live") return `Live · ${humanizeAge(age)}`;
  if (age == null) return "Not reporting";
  return `As of ${humanizeAge(age)} ago`;
}

/* The tooltip, in the design's words ("Live · 12s · hide Subway", "As of 6m ago · hide
   Metro-North", "Scheduled · hide NJ Transit"), which is now the state's words plus the
   button's action.

   THE VERB IS THE ACTION, NOT THE STATE. A hidden feed's button shows it again, so its tooltip
   says "show". A tooltip that said "hide" on a button that shows would be wrong in the one place a
   rider looks to find out what pressing it does. That clause is the reason feedStateWords exists
   separately: it is the half a popup must not repeat. */
function feedTooltip({ name, state, age = null, hidden = false } = {}) {
  return `${feedStateWords({ state, age })} · ${hidden ? "show" : "hide"} ${name}`;
}

/* MR5 (ruling Q2): THE POPUP'S FRESHNESS FOOTER, which is the feed strip's dot at the popup.

   PRESENT IN ALL THREE STATES, which is the half a "show it when it is bad" footer would get
   wrong: a rider cannot tell "this feed is live" from "this popup forgot to say" unless the mark is
   always there. So the square is unconditional and only the WORDS come and go.

   NO TEXT WHEN LIVE, because this app has no word for "live" on any surface (memo D9, "silence
   means current"), and the README's "LIVE · UPDATED 12S AGO" is exactly the sentence that rule
   forbids. Stale and schedule-only get the app's own strings, from feedStateWords.

   AND THE STATE IS SAID ONCE, IN WHICHEVER CHANNELS IT HAS NOT BEEN SAID IN. The square repeats
   whatever the words say, so it is decorative and carries aria-hidden; labelling it as well would
   say "As of 6m ago" twice to a screen reader and nothing at all to an eye. The WORDS have three
   destinations rather than two, and an earlier draft of this paragraph named only two, which is the
   defect ruling R2 corrects:

     SHOWN AND SPOKEN, where this footer is the first thing to state the age: a stale or
     schedule-only feed under a position whose words state no age.
     SPOKEN ONLY, where there is nothing for an eye because the app has no visible word for live
     (memo D9) and a screen reader still needs the state. `.visually-hidden` is A1's own class.
     NEITHER, where the popup's Position row has ALREADY stated an age at least as old as the feed's.
     The old expression put this case in the visually-hidden span with the live one, so a screen
     reader heard two ages about one train: the row's and the footer's. `said` therefore wins over
     `live`, and the four cases are pinned in positions.test.js and in helpers.test.js.

   NO POPUP RENDERS THE SCHEDULE-ONLY STATE, and an earlier draft of this comment said the opposite.
   It read "AirTrain gets one and it says Scheduled", which presumes AirTrain has a popup that calls
   this: it does not. The footer is a VEHICLE popup's line (popupFreshLine, systems/shared.js) and
   AirTrain has no vehicles, so `scheduled` is reachable here only through a caller that passes it,
   and none exists. A rider reads that word on the feed strip's tooltip instead, where chrome.spec.js
   D1a holds it, and pins.spec.js waives it in UNREACHED_STATES with the same reason. The branch stays
   because feedStateWords defines three states and this renders whichever it is given; what is not
   true is that a popup gives it that one. A caller that passes no state at all gets nothing, because
   inventing a square for a surface with no feed behind it would be a claim.

   AND IT IS vehicleStaleLine RESTYLED, NOT A SECOND VOICE, which is the one thing a footer added
   naively would have got wrong. Every vehicle popup used to end in vehicleStaleLine, which printed
   "as of 5m ago" when the vehicle's SYSTEM had gone stale; a footer that also said "As of 6m ago"
   would have put one fact on screen twice in two capitalisations. So this takes that line's job and
   its rule, and THE RULE'S REASON MOVED HERE WITH IT, because that function is gone and a rule whose
   explanation lives in a deleted comment is a rule nobody can check:

     A vehicle's footer speaks only for what its position's words did not say. A position whose
     words already state an age at least as old as the feed's has said it, and saying it again would
     say it twice, or, since an observation's age and a feed's differ by the provider's lag, say two
     ages about one train. A position whose words state no age at all (Metro-North's undated fixes,
     and every fresh one) leaves the footer to say the only age there is, exactly as C2 drew it.

   THE ONE DIFFERENCE FROM THAT LINE is the square, and it follows from the ruling: the words are
   withheld, the square never is, because a rider cannot tell "this feed is live" from "this popup
   forgot to say" unless the mark is always there. What the footer ADDS is the two states that line
   never had, live and schedule-only.

   THE SQUARE IS NEVER WITHHELD, because the ruling is that it is present in all three states: a
   rider cannot tell "this feed is live" from "this popup forgot to say" unless the mark is always
   there. Only the words come and go. (A station popup would pass no position and suppress nothing,
   but no station popup calls this at all: see above.)

   ONE WIDENING RULING R2 ACCEPTS RATHER THAN HIDES. `said` is true when the FEED's age is null,
   which is the feed that has never decoded, whose words are "Not reporting": a claim about the feed
   rather than an age, so a Position row's "as of 5m ago" has not actually said it. Suppressing it is
   what the rule as written does, and it reaches no rider today (no fixture feed fails its first poll;
   pins.spec.js waives the state as unreached). The narrower rule would be
   `position.age != null && age != null && position.age >= age`, which moves no golden; R2 did not
   ask for it, so the wider one ships and this is the sentence that says so.

   WHAT SURVIVES SUPPRESSION is the square and the rule above it, which is what the footer has drawn
   in that case since the words were only visually hidden: `.visually-hidden` is `position: absolute`,
   so it was never a flex item and took no share of `.fresh`'s gap and no height. Measured before and
   after, the suppressed footer's box is unchanged. */
function popupFreshHtml({ state, age = null, position = null } = {}) {
  if (!state) return "";
  const words = feedStateWords({ state, age });
  const said = position && position.age != null && (age == null || position.age >= age);
  // `said` FIRST, which is ruling R2 in one line: already stated beats nothing-to-show, so a
  // suppressed footer's words are in neither channel rather than in the hidden one.
  const body = said ? "" : state === "live" ? `<span class="visually-hidden">${esc(words)}</span>` : esc(words);
  return `<div class="fresh"><span class="fresh-dot" data-state="${esc(state)}" aria-hidden="true"></span>${body}</div>`;
}

/* ===== MR5: THE POPUP'S VOCABULARY, AS BUILDERS ==========================================

   README section 5 gives every popup one grammar and this is it: `.pk` the kicker row, `.pt`
   the title with its family's mark, `.kv` the label/value grid a vehicle's facts sit in,
   `.dir` an arrivals bucket's heading and `.arr` its rows. `.fresh` is above (popupFreshHtml),
   and `.alert` and `.xlink` stay with the callers that already build them.

   PURE STRINGS IN helpers.js, WHICH IS THE SEAM THIS FILE ALREADY KEEPS: nothing here touches
   Leaflet, so node loads all of it and every popup in the app is a string built by functions a
   unit test can ask one at a time. It is the same seam the marks keep, where railTagSvg is a
   string and only the L.divIcon wrapper is in systems/shared.js.

   THE ESCAPING IS HERE, ONCE. Every text parameter is run through esc() by the builder, so no
   caller composes markup out of feed text on its way in, and a caller that pre-escaped would
   double-escape an ampersand rather than fail loudly. The parameters that DO take markup say
   so in their names (markHtml, rightHtml, extraHtml) and every value they are given is built
   by another builder in this file.

   WHAT IS NOT HERE: words. Every string a popup prints is the caller's, which is this stage's own
   defect shape ("a word that came from a literal instead of the app") stated as a rule about THIS
   FILE: no builder here coins a word, so a word a rider reads can always be traced to the popup
   that chose it.

   AND THE RULE STOPS THERE, which is a reviewer's correction of a sentence that went further. It
   used to add that the callers take their words "from the app's own vocabulary rather than from
   section 5's prose", and five of the twelve grid LABELS are section 5's prose: Position, Direction,
   Delay, Status and Speed appear nowhere else in the app as rider text. Four of those are disclosed
   in the README's own erratum as labels the app's fields needed, and the fifth (Position) is the
   design's word for a row memo D9 and ruling Q1 rewrote the VALUES of. They are deliberate and they
   are recorded; what was wrong was a file claiming a closure over callers it does not own.

   AND THERE IS A NEWLINE BETWEEN EVERY CELL AND AFTER EVERY BLOCK, which is not formatting: it is
   what makes the popup's textContent read as words. A grid of adjacent elements with no whitespace between them
   concatenates to "Train1797", which is what a rider's text reads like to anything that walks
   the DOM rather than the layout: Playwright's toContainText, a scraper, the specs this stage
   inherited. CSS ignores a whitespace-only text node in a grid or a flex container (it generates
   no box and no item), so the separator costs nothing on screen and the pins' reader drops it
   too, having normalised it away. Measured: without it, A3c read "MNRMHUDHudsonTrain1797", and with
   it between cells but not after blocks, the AirTrain popup read "Federal Circlescheduled service".
   The blocks a popup builds outside this section (an alerts block, a board's freshness line, a
   "No trains" notice, a cross-link button, the bus's route note) end the same way for the same
   reason. */

/* THE SYSTEM WORD A POPUP'S KICKER CARRIES, and none of the six is coined here.

   Four are the feed strip's own names for the same feed (FEEDS above: Subway, Buses,
   NJ Transit, PATH), so a rider reads one word for a system on both surfaces. "NYC Ferry" is
   the word the ferry popups and ferryBoatName already print, where the strip's button says
   the shorter "Ferry"; "AirTrain JFK" is airtrainStationName's and the AirTrain popup's own.
   popupvocab.test.js asserts each of the six against those sources, which is what keeps this
   from becoming six literals that agree with nothing.

   THE RAILROAD IS NOT HERE, because its kicker is the train's own served `system` field, as
   its head has printed since phase 9. That word is the feed's code ("MNR"), and every SPOKEN
   surface says railroadSystemLabel's "Metro-North" for the same agency: the divergence is
   older than this stage, it is recorded as a finding in the MR5 ledger, and it is not quietly
   changed here. */
const POPUP_SYSTEM_WORDS = {
  subway: "Subway",
  buses: "Buses",
  njt: "NJ Transit",
  path: "PATH",
  ferry: "NYC Ferry",
  airtrain: "AirTrain JFK",
};

/* THE TWO MARK SIZES, WHICH ARE THE DESIGN'S OWN. Section 5 draws a title's route mark at
   `.bul.lg` 24px and the small form at `.bul.sm` 17px, and those are the two heights a popup
   asks for: 24 in a title, 17 in a kicker or an arrivals row.

   A TITLE MARK IS NEVER SMALLER THAN THE MAP DRAWS IT, which is the one clamp in this section
   and the reason the rail families pass their own box height instead of this constant: the
   tag's box is 30 units tall because the stem and the head hang below the 13-unit tag, so
   scaling that box to 24 would draw the two blocks at 10.4 units with 7px type, smaller and
   thinner than the map's own. Every other family's box is at most 20, so 24 scales it up. */
const POPUP_MARK_TITLE = 24;
const POPUP_MARK_ROW = 17;

/* THE MAP'S OWN MARK AT THE POPUP'S SIZE, and "the map's own" is the whole point: a popup that
   drew its own route mark would be a second answer to "what does this family look like", and
   the two would drift the first time either was edited, which is finding N6 one surface out.

   SO THE BODY IS NOT TOUCHED. This re-wraps a builder's string: the opening `<svg>` tag keeps
   its viewBox, its class and its style, its width and height are replaced with the popup's,
   and every byte after that tag is copied. popupvocab.test.js asserts exactly that of all six
   mark builders, which is what makes "the mark in the popup is the mark on the map" a claim a
   test can check rather than a sentence in a comment.

   WIDTH FOLLOWS THE viewBox, so a mark that is not square (the ferry's 22x14 hull, the rail
   tag's variable width) keeps its aspect ratio instead of being squashed into a box.

   BOTH DIMENSIONS ARE STRIPPED BEFORE THEY ARE WRITTEN, because the builders disagree about
   whether they carry them at all: the rail tag and the rail station square set width and
   height, the bus mark, the PATH diamond and the ferry hull set neither and take their size
   from the divIcon's box.

   A TITLE ASKS FOR NO HEIGHT AND GETS THE LARGER OF 24 AND THE MARK'S OWN, which is the clamp
   this section's constants describe, applied here as arithmetic so no caller has to know its
   family's geometry: the rail tag's 30-unit box holds a 13-unit tag with a stem and a head
   below it, and scaling that to 24 would draw the tag's two blocks at 10.4 units with 7px
   type, thinner than the map's own. A kicker or an arrivals row asks for POPUP_MARK_ROW and
   gets exactly that, because at 17 a mark is a decoration beside words rather than the
   popup's subject, and the design draws it at `.bul.sm` deliberately.

   aria-hidden ON THE WRAPPER, because the mark repeats in a picture what the title says in
   words. The subway's plate carries its route letter as SVG TEXT, which a screen reader would
   otherwise read out before the title says "1 train"; the other five hide themselves already
   and the attribute changes nothing for them. */
function popupMarkHtml(svg, height = null) {
  const source = typeof svg === "string" ? svg : "";
  const end = source.indexOf(">");
  if (!source.startsWith("<svg") || end < 0) return "";
  const open = source.slice(0, end);
  const box = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(open);
  if (!box) return "";
  const h = height == null ? Math.max(POPUP_MARK_TITLE, Number(box[2])) : Number(height);
  if (!Number.isFinite(h) || h <= 0) return "";
  const width = Number(((h * Number(box[1])) / Number(box[2])).toFixed(2));
  const sized = open.replace(/\s(?:width|height)="[^"]*"/g, "");
  return `<span class="pmark" aria-hidden="true">${sized} width="${width}" height="${h}"${source.slice(end)}</span>`;
}

/* THE KICKER. Left is the surface's system word, right is whatever small marks it carries: the
   route plates a subway station is served by, the wheelchair glyph a ferry dock publishes.

   BOTH SPANS ARE EMITTED WHENEVER THE ROW IS, because the row is a flex with space-between and
   one span would sit at the left edge rather than at the right. An empty row is no row at all,
   which is the silence rule this whole stage keeps: a surface with no system word and no marks
   prints no kicker. */
function popupKickerHtml({ left = "", rightHtml = "" } = {}) {
  if (!left && !rightHtml) return "";
  return `<div class="pk"><span>${esc(left)}</span>\n<span>${rightHtml}</span></div>\n`;
}

/* THE TITLE. `markHtml` is popupMarkHtml's answer (or "" for the three families whose map mark
   is a canvas circle and therefore has no string builder to borrow), `text` is the head the
   surface has always printed, and `color` is the ink it printed it in.

   THE INK GOES ON THE TEXT SPAN AND NOT ON THE ROW, so a route colour cannot reach the mark:
   the marks paint themselves from their own fills and the theme's tokens, and a `color` on
   their parent would be inherited by anything inside them drawn in currentColor. The callers
   resolve that ink through readableInk against the popup's own surface, which is what
   popups.spec.js D6i measures. */
function popupTitleHtml({ text = "", markHtml = "", color = null } = {}) {
  if (!text && !markHtml) return "";
  const tint = color ? ` style="color:${color}"` : "";
  const words = text ? `<span${tint}>${esc(text)}</span>` : "";
  return `<div class="pt">${markHtml}${markHtml && words ? "\n" : ""}${words}</div>\n`;
}

/* THE VEHICLE POPUP'S FACTS, as the design's two-column grid. Rows are [{k, v}] in the order
   they are to be read, both plain text, both escaped here.

   A ROW WITH NOTHING TO SAY IS NOT PRINTED, which is positionWords' rule generalised to
   every row in the grid: the app has no word for a fact it does not have, and a "Delay" row
   reading "unknown" would be a claim where silence is the truth. A row with a value and no
   LABEL is dropped for the same reason and one of its own: in a two-column grid it would land
   in the label column and read as a heading.

   AND NO GRID AT ALL WHEN NO ROW SURVIVES, so a popup whose every fact is missing does not
   print an empty box with a rule across it. */
function popupRowsHtml(rows) {
  const cells = (rows || [])
    .filter((row) => row && row.k && row.v)
    .map((row) => `<div class="k">${esc(row.k)}</div>\n<div class="v">${esc(row.v)}</div>`)
    .join("\n");
  return cells ? `<div class="kv">${cells}</div>\n` : "";
}

/* AN ARRIVALS BUCKET'S HEADING. `color` is the ferry's: its buckets are ROUTE names, so the
   heading is the one place a dock's popup can carry a route's colour, and it arrives already
   resolved against the popup's surface by its caller. Every other family's buckets are
   directions and take the ink. */
function popupDirHtml(text, color = null) {
  if (!text) return "";
  const tint = color ? ` style="color:${color}"` : "";
  return `<div class="dir"${tint}>${esc(text)}</div>\n`;
}

/* THE ARRIVALS ROWS, as the design's three-column grid: the mark, what the row is, and the
   countdown. One `.arr` per bucket, one row of three spans per arrival.

   THREE SPANS ALWAYS, even when a family has no mark to put in the first one or nothing to
   name in the second: the grid places cells in order, and a row that emitted two spans would
   slide its countdown into the middle column and print a board whose numbers do not line up.

   THE ACCENT COMES FROM THE APP'S OWN WORD. Section 5 says a countdown under 30s "reads 'now'
   in the accent", and formatCountdown is what decides a row reads "now" (countdownParts, at
   30s). So the class follows the word rather than re-testing the seconds, and the two cannot
   disagree about which rows are accented.

   `extraHtml` IS THE ROW'S OWN MARKUP: the train number a feed carries and the freshness
   qualifier arrivalQualifier earned, both already built by helpers in this file. It sits in the
   middle cell rather than beside the countdown because that cell is the flexible one: the
   number cell is nowrap and tabular by design, and a parenthetical inside it widens every row
   in the popup. */
function popupArrRowsHtml(rows) {
  const cells = (rows || [])
    .filter(Boolean)
    .map((row) => {
      const countdown = row.countdown ?? "";
      /* THE ACCENT IS ON THE ROW WHOSE COUNTDOWN READS "now", AND A DEPARTING ROW READS IT TOO.
         The first draft compared the whole cell to "now", and two of the five boards compose their
         cell as "departs " plus the countdown (a dwelling boat, a boarding NJ Transit train), so a
         row leaving in under thirty seconds printed an unaccented "departs now" beside an accented
         "now" two rows up. Measured on both boards. The test is on the countdown's own last word,
         which is still formatCountdown's answer and still not a second threshold. */
      const cls = /(^|\s)now$/.test(countdown) ? "n now" : "n";
      return (
        `<span>${row.markHtml ?? ""}</span>\n` +
        `<span>${row.label ? esc(row.label) : ""}${row.extraHtml ?? ""}</span>\n` +
        `<span class="${cls}">${esc(countdown)}</span>`
      );
    })
    .join("\n");
  return cells ? `<div class="arr">${cells}</div>\n` : "";
}

/* THE RAILROAD HEAD'S TWO PARTS, which is what the popup's kicker and title need from the
   three fields formatRailroadHead used to join into one string.

   IT REPLACES THAT FORMATTER RATHER THAN SITTING BESIDE IT. Two functions over the same three
   inputs is the shape this stage keeps finding (N6 in the app, the pins reader in the tests),
   and the formatter's only caller was the head this stage restyles. Its three cases are this
   function's three cases and its test moved with them.

   `agency` IS THE SERVED FIELD, unchanged: the popup has printed "LIRR" and "MNR" since phase
   9 and this stage does not quietly reword a rider-facing string. `line` is the route's name
   where the tables have one, "route <id>" where they do not, and empty where the feed gave
   neither: a system with no line still gets a kicker, and the title then carries the agency
   itself so the popup is never headless. */
function railroadHeadParts(system, routeId, name) {
  const agency = system || "";
  if (name) return { agency, line: name };
  if (routeId) return { agency, line: `route ${routeId}` };
  return { agency, line: "" };
}

/* One entry per feed, ready to render: the count as text, the dot's state, the tooltip,
   and whether the OFF treatment applies.

   `counts` and `ages` are read by feed key. A feed with no count (AirTrain) gets null
   rather than 0, because 0 vehicles and no such number are different claims. `hidden` is
   the set of feed keys the rider has switched off, and it drives BOTH `pressed` (which is
   what aria-pressed is written from) and `off` (which is what the strike and the visible
   mark are drawn from), so the two cannot disagree: that disagreement is mutations M1 and
   M2, and it is the whole reason this is one function and not two. */
function feedStripModel({ counts = {}, ages = {}, hidden = null } = {}) {
  const off = hidden ?? new Set();
  return FEEDS.map((feed) => {
    const isOff = off.has(feed.key);
    const scheduled = feed.source == null;
    const age = scheduled ? null : (ages[feed.key] ?? null);
    const state = feedDotState({ scheduled, age });
    const count = Object.prototype.hasOwnProperty.call(counts, feed.key) ? counts[feed.key] : null;
    return {
      key: feed.key,
      name: feed.name,
      tick: feed.tick ?? null,
      glyph: feed.glyph ?? null,
      count: count == null ? null : Number(count).toLocaleString(),
      dot: state,
      pressed: !isOff,
      off: isOff,
      title: feedTooltip({ name: feed.name, state, age, hidden: isOff }),
    };
  });
}

/* ----- A3: contrast, computed rather than curated ----------------------------
   ONE luminance path for the whole app. Before this there was none: the only
   contrast logic anywhere was DARK_TEXT_LINES above, a hand-written set of four
   subway lines "that need dark text". Hand-curated sets are wrong the moment a
   palette gains an entry, and this one already was. Measured against white text at
   the 4.5 a chip's 11px owes: B/D/F/M #d68910 at 2.82, G #58a832 at 2.97, L #7f8c8d
   at 3.48, and in the railroad palette #ef6c00 at 3.08 and the no-id default #607d8b
   at 4.37. Six subway lines and two railroad colours carrying unreadable text, none
   of them in the set.

   The formulas are WCAG 2.x relative luminance and contrast ratio, verbatim. They
   are here rather than in a stylesheet because the decision is per route colour and
   the route colours are computed. */

// "#rgb", "#rrggbb", "rgb(...)" and the "hsl(h, s%, l%)" that routeColor emits.
// Returns null for anything unparseable rather than guessing, so a caller gets a
// visible failure instead of a silently wrong colour.
function parseColor(value) {
  const text = String(value ?? "").trim();
  const short = text.match(/^#([0-9a-f])([0-9a-f])([0-9a-f])$/i);
  if (short) return short.slice(1, 4).map((c) => parseInt(c + c, 16));
  const long = text.match(/^#([0-9a-f]{6})$/i);
  if (long) return [0, 2, 4].map((i) => parseInt(long[1].slice(i, i + 2), 16));
  const rgb = text.match(/^rgba?\(([^)]+)\)$/i);
  if (rgb) {
    const parts = rgb[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    if (parts.length >= 3 && parts.slice(0, 3).every((n) => Number.isFinite(n))) return parts.slice(0, 3);
    return null;
  }
  const hsl = text.match(/^hsl\(\s*([\d.]+)\s*,\s*([\d.]+)%\s*,\s*([\d.]+)%\s*\)$/i);
  if (hsl) {
    const [h, s, l] = [Number(hsl[1]), Number(hsl[2]) / 100, Number(hsl[3]) / 100];
    const a = s * Math.min(l, 1 - l);
    const f = (n) => {
      const k = (n + h / 30) % 12;
      return l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)));
    };
    return [f(0) * 255, f(8) * 255, f(4) * 255];
  }
  return null;
}

function relativeLuminance(rgb) {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// WCAG contrast ratio, 1 to 21. Order-independent by construction.
function contrastRatio(a, b) {
  const [x, y] = [parseColor(a), parseColor(b)];
  if (!x || !y) return null;
  const [hi, lo] = [relativeLuminance(x), relativeLuminance(y)].sort((m, n) => n - m);
  return (hi + 0.05) / (lo + 0.05);
}

// The ink to print ON a filled shape: whichever of the two carries further. This is
// what replaces DARK_TEXT_LINES, and it cannot fall behind a palette because it reads
// the palette.
const INK_LIGHT = "#ffffff";
const INK_DARK = "#1a1a1a";
function readableTextOn(background) {
  const light = contrastRatio(INK_LIGHT, background);
  const dark = contrastRatio(INK_DARK, background);
  if (light == null || dark == null) return INK_DARK; // unparseable: the safer default
  return light >= dark ? INK_LIGHT : INK_DARK;
}

// A route colour used AS TEXT, on a light surface, darkened only as far as it must be.
// The brand colour stays on the SHAPES that carry identity (bullets, chips, route
// lines), where 3:1 applies and the label carries the meaning; a heading rendered in
// the same hue is text and owes 4.5, and #e6b800 on white is 1.87. Scaling the channels
// toward black preserves the hue, so an N heading still reads yellow, just readably so.
// Returns the input unchanged when it already clears the target.
/* MR5: THE BACKGROUND A POPUP'S ROUTE-COLOURED HEAD IS ACTUALLY PRINTED ON, as this file's own
   fallback for it. readableInk defaults to `#ffffff` and every popup head took that default,
   which was true while a Leaflet popup was white and is not true now: section 5 makes the popup
   --surface, and in the dark theme it was never white at all.

   THE LIVE VALUE IS RESOLVED FROM THE TOKEN, in systems/shared.js's popupSurfaceColor(), and is
   passed in. This is the value to use when nothing passed one: the light theme's own --surface,
   for the same case paperColor()'s and inkColor()'s literals cover, a caller with no stylesheet
   applied (which is every node test in this repo). frontend/tokens.test.js asserts it against
   style.css, so it cannot drift from the token it stands in for.

   MEASURED, WHICH IS WHY IT IS NOT STILL WHITE: layout.spec.js A4g renders the N train, whose
   #e6b800 readableInk walked to rgb(138, 110, 0) against white. That reads 4.02 against this
   surface, and A4g failed on it the moment the surface changed. */
const POPUP_SURFACE_FALLBACK = "#eae9e9";

/* MR5: AND IT MOVES IN WHICHEVER DIRECTION THE BACKGROUND LEAVES ROOM IN, which it did not.

   THE BUG, MEASURED. Every version of this before MR5 only ever DARKENED (`c * scale`, scale
   falling from 0.95 to 0) and fell back to `#000000` under a comment that said "black fails
   nothing on a light surface". On a DARK background that is exactly backwards and the fallback is
   the worst answer available: black reads 1.49:1 against the dark theme's --surface. Measured
   across the app's twenty-seven subway route colours, nine clear 4.5 on that surface as published
   and **the other eighteen all came back `#000000` at 1.49**. A function whose contract is "the
   ink to print ON this background" was returning an unreadable answer for two thirds of its
   inputs and reporting no failure.

   IT WAS LATENT AND THIS STAGE MADE IT LIVE. Until section 5 the popup was Leaflet's white in
   BOTH themes, so the only background this function was ever handed was a light one, even after
   MR4 shipped the dark theme. The popup's surface is the first dark background any caller has
   passed, and axe named the resulting violation on `[b, .popup-sub]` at all three widths the
   moment it did.

   THE DIRECTION IS ASKED OF THE BACKGROUND, not of a flag or a theme name: whichever of black and
   white carries further against it is the way there is room to move. That keeps the decision in
   the one place that can answer it and costs nothing on a light background.

   THE DARKENING PATH IS THE OLD LOOP, CHARACTER FOR CHARACTER, and that is a deliberate refusal
   to tidy it. The obvious rewrite is one loop with the direction folded into the step, `1 - step`
   against `c + (255 - c) * step`. Measured, that is not the same function: 0.05 has no exact
   binary form, so counting DOWN from 0.95 by subtraction and counting UP from 0.05 by addition
   accumulate different error, and at a rounding boundary the two disagree by one unit per channel.
   Thirteen of the app's own colours came back different on the light surfaces (#e6b800 on white
   went #8b7005 to #8b7006, and so on), which would have moved thirteen pins for a reason that has
   nothing to do with this repair. So the two directions are two loops, and the darkening one is
   the one this function has always run. Every light-surface answer is therefore unchanged, proven
   in helpers.test.js against a transcription of the old body; only the dark-surface answers move,
   and every one of those was `#000000`.

   THE LIGHTENING PATH IS THE SAME OPERATION MIRRORED, `c + (255 - c) * scale`, which is a tint
   rather than a hue shift for the reason A3 gives for the scaling: it preserves the hue, so the
   route stays recognisably its own colour. Measured on the dark surface, eight of the eleven
   distinct subway colours move and three already clear: #c0392b becomes #d67e75 at 4.76, #1e8449
   becomes #56a377 at 4.63, and #e6b800 is left alone at 7.52. */
function readableInk(color, background = "#ffffff", target = 4.5) {
  const rgb = parseColor(color);
  if (!rgb) return color;
  if ((contrastRatio(color, background) ?? 0) >= target) return color;
  const hexOf = (channels) => `#${channels.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
  const clears = (hex) => (contrastRatio(hex, background) ?? 0) >= target;
  if ((contrastRatio("#ffffff", background) ?? 0) > (contrastRatio("#000000", background) ?? 0)) {
    // Toward white, because that is the direction this background leaves room in. The loop may
    // stop a hair short of 1 through the same float accumulation described above; it does not
    // matter, because scale 1 IS white and white is the fallback below.
    for (let scale = 0.05; scale <= 1; scale += 0.05) {
      const hex = hexOf(rgb.map((c) => Math.round(c + (255 - c) * scale)));
      if (clears(hex)) return hex;
    }
    return "#ffffff"; // white fails nothing on a dark surface
  }
  for (let scale = 0.95; scale >= 0; scale -= 0.05) {
    const hex = hexOf(rgb.map((c) => Math.round(c * scale)));
    if (clears(hex)) return hex;
  }
  return "#000000"; // black fails nothing on a light surface
}

/* A4 ROUND 1: WHERE A POPUP SHOULD MOVE TO GET OUT FROM UNDER THE PAGE'S CHROME.
   Pure geometry, here rather than in systems/shared.js so it can be reasoned about and
   tested without a browser, which is the same split the rest of this file exists for. The
   caller supplies three boxes and gets back a translation or null; it knows nothing about
   Leaflet, the legend or the banner.
   The first version of this lived inside the map file, only ever moved DOWN, and knew about
   one obstacle. All three were wrong and all three were caught by measurement rather than by
   reading: see the comment at panPopupClearOfChrome for what each cost. */
const POPUP_CLEAR_GAP = 8; // the gap the layout uses between two surfaces that must not touch

function boxesOverlap(a, b) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

function shiftBox(box, dx, dy) {
  return {
    left: box.left + dx,
    right: box.right + dx,
    top: box.top + dy,
    bottom: box.bottom + dy,
  };
}

/* THE GEOMETRY, KEPT SEPARATE FROM THE MAP so it can be reasoned about and tested without
   a browser: given where the popup is, what the obstacles are and how much room the map
   has, which way should the world move and by how much? Returns null when the popup is
   already clear or when no direction can clear it, and the difference between those two
   is deliberately not encoded: both mean "do nothing", and a caller that treated them
   differently would be inventing a distinction the rider cannot see. */
function popupClearingShift(popup, obstacles, viewport) {
  const blocking = obstacles.filter((box) => boxesOverlap(popup, box));
  if (!blocking.length) return null;
  const fits = (box) =>
    box.left >= viewport.left && box.right <= viewport.right && box.top >= viewport.top && box.bottom <= viewport.bottom;

  /* THE SEARCH, AND WHY IT IS A SEARCH RATHER THAN A FORMULA.
     The first version costed each direction as "the most any blocking obstacle demands" and
     discarded the result if it landed on something else. Round 2 showed that fights itself
     two ways. Taking the max over every blocker turns two small obstacles into one enormous
     move that leaves the viewport; discarding a landing collision throws away the answer
     when the honest response is "then also step sideways". Together they could cancel the
     desktop leftward move outright and leave the popup fully under the legend.
     So each blocker is costed SEPARATELY, in each direction, and any collision the result
     lands in is resolved on the OTHER axis. The candidate set is small (obstacles times four,
     twice) and every candidate is checked against every obstacle before it can win, so the
     search cannot return a move that does not actually clear. */
  const axisMoves = (box, blockers) =>
    blockers.flatMap((b) => [
      { dx: b.left - box.right - POPUP_CLEAR_GAP, dy: 0 },
      { dx: b.right - box.left + POPUP_CLEAR_GAP, dy: 0 },
      { dx: 0, dy: b.top - box.bottom - POPUP_CLEAR_GAP },
      { dx: 0, dy: b.bottom - box.top + POPUP_CLEAR_GAP },
    ]);

  const candidates = [];
  for (const first of axisMoves(popup, blocking)) {
    const moved = shiftBox(popup, first.dx, first.dy);
    candidates.push({ move: first, moved });
    const stillBlocking = obstacles.filter((box) => boxesOverlap(moved, box));
    if (!stillBlocking.length) continue;
    // The second axis only: a move along x is followed by a y move and vice versa, so the
    // pair is always an L and never doubles back along the axis just cleared.
    for (const second of axisMoves(moved, stillBlocking)) {
      if ((first.dx !== 0) === (second.dx !== 0)) continue;
      const move = { dx: first.dx + second.dx, dy: first.dy + second.dy };
      candidates.push({ move, moved: shiftBox(popup, move.dx, move.dy) });
    }
  }

  const accepted = candidates
    // Re-checked against EVERY obstacle, not just the ones that were blocking: moving out
    // from under the legend must not move under the banner.
    .filter(({ moved }) => fits(moved) && !obstacles.some((box) => boxesOverlap(moved, box)))
    .map(({ move }) => move);
  if (!accepted.length) return null;
  // The smallest accepted move, so the map shifts as little as the rider will tolerate.
  return accepted.reduce((best, move) =>
    Math.abs(move.dx) + Math.abs(move.dy) < Math.abs(best.dx) + Math.abs(best.dy) ? move : best,
  );
}

/* MR5 (ruling S3): THE AUTOPAN PADDING LEAFLET IS ALLOWED TO HAVE, which is not the one the
   design asked for. The design's recipe reserves the whole page header above the popup
   (`autoPanPaddingTopLeft = [24, headerBottom + 12]`) and the control stack's width to its
   right (`[110, 40]`), and it is measured broken on this app twice over. The erratum beside the
   recipe in docs/design/map-redesign/README.md and ruling S3 in the ledger carry both
   measurements; what matters here is the ONE arithmetic fact this function exists for.

   LEAFLET'S PADDING ARITHMETIC IS LAST-WRITE-WINS PER AXIS, so an unsatisfiable pair does not
   split the difference: it silently honours one end and pushes the popup off the other.
   Vertically the TOP assignment is second and therefore wins; horizontally the LEFT one is.
   Measured: with the Key panel open at 375x667 the header's bottom edge is 579, the recipe
   asks for a top padding of 591, and `_adjustPan()` puts a 126px popup at top 592, bottom 718,
   which is 51px past the bottom of a 667px map. The app's own `panPopupClearOfChrome` cannot
   rescue that, because it is a collision solver and a popup that has left the viewport is not
   colliding with anything.

   SO EACH PADDING IS CLAMPED TO WHAT THE MEASURED MAP AND POPUP CAN ACTUALLY SATISFY. A pair
   is satisfiable on an axis exactly when `padA + padB + popupExtent <= mapExtent`, which is
   the same inequality Leaflet's two branches encode; when it fails, the padding that WINS is
   the one cut, because the loser is already being honoured anyway. That spends the slack on
   the large derived padding and keeps the small fixed ones, which is the right direction: the
   110 beside the popup clears the control stack and the 24 is only a margin.

   WHAT IS NOT DECIDED HERE. This says nothing about WHERE the popup ends up, only about how
   much room Leaflet may demand. `panPopupClearOfChrome` remains the authority for the real
   boxes and for a popup that grows after its first paint, because it reads rects rather than
   one header's bottom edge, and because a station popup is 29px tall at popupopen.

   Returns integers, never negative, never more than the axis can hold. `clamped` names the
   ends that were cut, which is what the e2e pin reads: a cap that had stopped firing would
   otherwise look exactly like a cap that was never needed. */
function clampedAutoPanPadding({ want, map: mapBox, popup: popupBox } = {}) {
  const w = want || {};
  const m = mapBox || {};
  const p = popupBox || {};
  // A missing measurement is not a reason to guess at a padding: with no map or no popup to
  // fit, the honest answer is Leaflet's own default, which this expresses as no padding at all.
  // The caller reads `usable: false` and leaves the options alone.
  const usable = [m.width, m.height, p.width, p.height].every((n) => typeof n === "number" && isFinite(n) && n >= 0);
  const floor = (n) => Math.max(0, Math.round(typeof n === "number" && isFinite(n) ? n : 0));
  const [wantTop, wantLeft, wantBottom, wantRight] = [w.top, w.left, w.bottom, w.right].map(floor);
  if (!usable) return { usable: false, topLeft: [0, 0], bottomRight: [0, 0], clamped: { top: false, left: false } };
  // The room each axis has for padding once the popup itself is placed in it. Negative when the
  // popup is larger than the map, which the max turns into "no padding at all": a popup that
  // cannot fit has no satisfiable padding, and demanding one would only choose which edge it
  // hangs off.
  const vertical = Math.max(0, Math.floor(m.height - p.height));
  const horizontal = Math.max(0, Math.floor(m.width - p.width));
  const top = Math.min(wantTop, Math.max(0, vertical - wantBottom));
  const left = Math.min(wantLeft, Math.max(0, horizontal - wantRight));
  return {
    usable: true,
    topLeft: [left, top],
    bottomRight: [Math.min(wantRight, horizontal), Math.min(wantBottom, vertical)],
    clamped: { top: top < wantTop, left: left < wantLeft },
  };
}

/* The design's own numbers, named so the recipe and the clamp can be read against each other
   rather than against four literals buried in a handler. `top` is derived from a MEASURED edge
   (the rendered header and alert strip's bottom) and the rest are the recipe's fixed values.
   POPUP_AUTOPAN_GAP is the recipe's own 12 rather than POPUP_CLEAR_GAP's 8, because the two are
   different distances: this one sits between the popup and the page's chrome as the design drew
   it, and the 8 is the collision solver's step. */
/* MR5: THE ONE SET OF POPUP OPTIONS, applied at every bind site in this app.

   maxWidth IS AN OPTION AND NOT A STYLE, which is why it cannot live in style.css with the rest
   of section 5's metrics: Leaflet reads it in _updateLayout and writes the result as an INLINE
   width on .leaflet-popup-content, so a CSS max-width loses to the very declaration it is trying
   to bound. The 220px floor IS in the stylesheet, because Leaflet's minWidth writes an inline
   width too and a floor is a floor either way; keeping it in CSS keeps it beside the margin and
   the type it has to agree with.

   IT REPLACES A minWidth OF 170 on the station popups, the one bind site that carried options
   before this. That number predates the design and is below its floor, so leaving it would have
   made station popups the one surface section 5's metrics did not reach. */
const POPUP_OPTIONS = { maxWidth: 320, autoPan: false };

/* AND autoPan IS OFF, WHICH IS THE APP TAKING THE PAN RATHER THAN DECLINING IT. Leaflet's own
   autopan runs inside the open, BEFORE any popupopen handler, so the design's padding cannot be in
   place for it: the padding is derived from the popup's rendered size and the popup has no rendered
   size until it is in the document. Left on, that meant TWO pans per open, Leaflet's with its
   default 5px strip and then ours with the clamped padding. Measured, motion.spec.js A5e caught it:
   `distinct` centres went from 1 to 2, and its claim is that the map "must not travel through
   intermediate positions". Both pans are synchronous and unanimated, so no frame is painted between
   them, but a proxy that has to be argued with is a proxy worth satisfying instead.

   SO THERE IS EXACTLY ONE PAN AND THE APP OWNS IT. systems/shared.js's applyPopupAutoPan sets the
   clamped padding, turns autoPan on for the length of one _adjustPan() call and off again. That
   also closes the SECOND break the README's erratum records, structurally rather than by guard:
   popup.update() re-ran Leaflet's autopan on every fifteen-second poll for every open vehicle
   popup, with no equivalent of this app's riderOwnsTheView rule. With autoPan off at rest a poll
   cannot pan at all, and the only autopan that exists is one the app asked for and brackets. */
/* IT LIVES IN helpers.js AND NOT BESIDE THE BIND SITES, which is a factoring decision a broken
   audit record made for me. It is pure data with no Leaflet and no DOM in it, so it belongs with
   this file's other popup constants; and docs/reviews/audit-2026-09-05/f04's node:vm driver loads
   helpers.js and systems/airtrain.js WITHOUT systems/shared.js, so a constant declared there and
   referenced in airtrain.js is a ReferenceError in that sandbox. The record failed on this commit
   with "the node:vm driver ran :: exit 3", which is the harness earning its keep. */

const POPUP_AUTOPAN_GAP = 12;
const POPUP_AUTOPAN_WANT = { left: 24, right: 110, bottom: 40 };

function popupAutoPanWant(chromeBottom) {
  const edge = typeof chromeBottom === "number" && isFinite(chromeBottom) ? Math.max(0, chromeBottom) : 0;
  return { ...POPUP_AUTOPAN_WANT, top: Math.round(edge + POPUP_AUTOPAN_GAP) };
}

// ---- Staleness thresholds, and the one test seam in this file (C6) ----
//
// WHY A SEAM EXISTS HERE AT ALL. The contract tier runs the REAL page against a
// real backend, so it cannot monkeypatch these the way the node tests do. Waiting
// out the production thresholds would put a single scenario at 90 or 300 seconds,
// which no CI budget survives, so the page has to be able to start dimming sooner
// when a test asks it to.
//
// THE SAFETY ARGUMENT, in full, because a query-string input into a live page
// deserves one:
//   1. It is COSMETIC AND CLIENT-LOCAL. It changes only when THIS visitor's own
//      view starts dimming markers and showing "may be out of date". It alters no
//      request, no data, and nothing any other visitor sees. A visitor who wanted
//      their own view to dim sooner could already do it from devtools in one line,
//      so this grants no capability that did not already exist.
//   1a. IT CAN ONLY EVER DIM SOONER, and that is enforced rather than asserted.
//      The accepted range is [1, the production value]. An earlier version took any
//      positive number, which made the sentence above FALSE in the direction that
//      matters: "?contract=1&feedStaleAfterS=99999999" RAISED the thresholds, so a
//      crafted link could suppress every staleness surface on the page and leave a
//      visitor reading hours-old positions as if they were live. Suppressing a
//      disclosure is a different act from accelerating one, and only one of them is
//      cosmetic. The floor of 1 closes the same hole from below: 0 is rejected
//      because everything would read permanently stale, and 1e-9 does that too.
//   2. THE FLAG IS NOT AN ACCESS CONTROL and is not pretending to be one; a query
//      parameter cannot be. It exists so the parse cannot fire by ACCIDENT: a
//      stray or copied "?feedStaleAfterS=5" in a shared link does nothing without
//      the companion flag, so the production page's behavior is unconditional in
//      practice rather than one typo away from changing.
//   3. THE PARSE IS DELIBERATELY NARROW, and this is the part worth reviewing: it
//      reads exactly two named parameters, accepts only finite positive numbers,
//      and returns a two-key object. It is not a general "read config from the
//      query string" channel, and it must not be allowed to become one, because
//      that is the change that would turn a cosmetic seam into a real surface.
const CONTRACT_FLAG_PARAM = "contract";

// Pure and node-testable: the caller passes the query string, so the inertness
// test can assert directly that anything without the flag yields no overrides.
const PRODUCTION_FEED_STALE_AFTER_S = 90;
const PRODUCTION_ALERTS_STALE_AFTER_S = 300;

function thresholdOverrides(search) {
  const params = new URLSearchParams(search ?? "");
  if (params.get(CONTRACT_FLAG_PARAM) !== "1") return {};
  const out = {};
  for (const [param, key, ceiling] of [
    ["feedStaleAfterS", "feed", PRODUCTION_FEED_STALE_AFTER_S],
    ["alertsStaleAfterS", "alerts", PRODUCTION_ALERTS_STALE_AFTER_S],
  ]) {
    const value = Number(params.get(param));
    if (Number.isFinite(value) && value >= 1 && value <= ceiling) out[key] = value;
  }
  return out;
}

// `location` is absent when this file is require()d by the node tests, so the
// browser read is guarded rather than assumed.
const THRESHOLD_OVERRIDES = thresholdOverrides(
  typeof location === "undefined" ? "" : location.search,
);

// Staleness threshold, mirroring the backend FEED_STALE_AFTER_S.
const FEED_STALE_AFTER_S = THRESHOLD_OVERRIDES.feed ?? PRODUCTION_FEED_STALE_AFTER_S;

// Whole-fetch deadline for every live browser fetch (R2). The browser fetch has
// no built-in whole-request timeout, so a wedged or trickling upstream would
// otherwise leave a request pending forever; each fetch passes
// AbortSignal.timeout(FETCH_DEADLINE_MS) so a stuck request is cut off and becomes
// an ordinary failed poll (keep-last-known + the R1 staleness surfaces), never a
// permanent hang. 15s matches the POLL_INTERVAL_MS cadence: a wedged source is
// aborted at about the time the next tick fires, so it retries on a later tick
// instead of holding a slot indefinitely. AbortSignal.timeout is a modern-baseline
// API (Chromium-tested here; supported across current evergreen browsers). This
// lives in helpers.js, loaded before every systems/*.js and map.js, so the constant
// is in scope for the static loaders and the shared.js fetches at call time (a const
// in map.js would not be a binding those earlier files can see).
const FETCH_DEADLINE_MS = 15000;

/* MR3 (R-d): DOES THIS STATIC PAYLOAD KNOW THE FIELD WE ARE ABOUT TO READ?

   The deploy hazard systems/shared.js's fetchRoutesPayload exists for, as a pure predicate so it
   can be asked here rather than in a browser. /api/railroad-routes and /api/njt-routes are
   static-derived and served under an hour-long cache, so a release that ADDS a field to one of
   them ships a frontend reading that field against a response held from before the backend
   rolled. Absent, not malformed: nothing errors, and every tag silently prints a fallback.

   "SOME ENTRY CARRIES IT", NOT "EVERY ENTRY DOES", and that is the whole subtlety. A payload
   where some entries have the field came from a backend that knows it, and the entries without
   it are the FEED's own gaps, which are real and permanent: NJ Transit's route 17 has no trips in
   an ordinary publication, and the railroads publish no colour for some routes. Keyed on "every"
   this would re-read on every load forever and never stop.

   null AND undefined BOTH COUNT AS ABSENT (`!= null`), because a backend that knows the field and
   has nothing to put in it serves null, and that is indistinguishable here from a backend that
   has never heard of it. The pessimistic reading costs one request; the optimistic one would skip
   the re-read for a payload where every entry's value is null, which is the exact shape a
   half-rolled deploy of a nullable column takes. */
function staticPayloadHasField(routes, field) {
  return Array.isArray(routes) && routes.some((route) => route && route[field] != null);
}

// Longitude is compressed by latitude; scale lon deltas so planar distances are
// roughly isotropic across NYC. We only need internally consistent arc-length,
// not true meters, so a single fixed factor at the city's latitude is plenty.
const _COS_LAT = Math.cos((40.7 * Math.PI) / 180);
// A station must project within this distance of a route polyline to be used.
const ROUTE_ACCEPT_DIST = 0.0025;
// Reject an implausibly long slice (misprojection onto a far lobe of a line that
// doubles back, e.g. the Pelham loop): fall back to the straight line instead.
const ROUTE_MAX_SLICE = 0.05;

// Railroad inter-station gaps dwarf subway ones: the LIRR's longest real gap,
// Amagansett to Montauk, is about 0.15 in the isotropic basis (roughly 3x
// ROUTE_MAX_SLICE), and several MNR gaps (Poughkeepsie to New Hamburg) exceed
// 0.1. With the subway cap those segments fail the length gate and fall back to
// the straight chord, defeating the point. This looser cap admits them while
// staying well under any doubling-back lobe: railroad lines are radial with
// branches, not looped like the Pelham 6, so a far misprojection is still
// rejected.
const RAILROAD_ROUTE_MAX_SLICE = 0.3;
// Start equal to the subway projection tolerance. Loosen only if placed-train
// platform coordinates prove to sit too far off the modeled track, which would
// show up as straight-chord fallback on segments that should glide.
const RAILROAD_ROUTE_ACCEPT_DIST = 0.0025;

// PATH's longest real inter-station gap, Journal Square to Harrison, is about
// 0.071 in the isotropic basis: too long for the subway cap (0.05) but far
// short of the railroad's branch-scale gaps (0.3 admits Montauk-length runs
// PATH never has). 0.15 admits every real PATH segment with 2x headroom while
// still rejecting a far misprojection; PATH lines are simple end-to-end runs
// with no loops, so the nearest lobe is always the right one.
const PATH_ROUTE_MAX_SLICE = 0.15;
// Same starting tolerance as the subway/railroad projection; loosen only if
// PATH station coordinates prove to sit off the modeled track, which would
// show up as straight-chord fallback on segments that should glide.
const PATH_ROUTE_ACCEPT_DIST = 0.0025;

// PATH's slice picker. WHY not computeRouteSlice directly: PATH keeps BOTH
// direction polylines for most routes (the reverse shape is a parallel track
// a few meters offset, so the added-geometry dedup keeps it), and
// computeRouteSlice projects each endpoint onto its own nearest polyline
// independently. With twin polylines that near each other, the two endpoints
// can each win on a different twin by a micro-distance coin flip (observed
// live: 0.00057 vs 0.00058), which fails the same-polyline requirement and
// drops the glide to the straight chord for no reason. This variant scores
// each polyline with BOTH endpoints together and slices along the best one,
// so twins can never split a segment; the acceptDist and maxSlice gates are
// unchanged, and picking the reverse-direction twin is harmless because the
// arc is walked in the sign of (s1 - s0). The other systems keep
// computeRouteSlice: their reverse shapes mostly collapse in the dedup, so
// the split cannot occur there and their behavior must not change.
function computePathRouteSlice(
  train,
  geom,
  { maxSlice = PATH_ROUTE_MAX_SLICE, acceptDist = PATH_ROUTE_ACCEPT_DIST } = {},
) {
  if (train.prev_lat == null || !geom) return null;
  let best = null;
  for (const poly of geom) {
    const p0 = projectOntoRoute([poly], train.prev_lat, train.prev_lon, acceptDist);
    const p1 = projectOntoRoute([poly], train.latitude, train.longitude, acceptDist);
    if (!p0 || !p1) continue;
    if (Math.abs(p1.s - p0.s) > maxSlice) continue;
    const score = Math.max(p0.dist, p1.dist);
    if (best === null || score < best.score) {
      best = { score, points: poly.points, cum: poly.cum, s0: p0.s, s1: p1.s };
    }
  }
  return best && { points: best.points, cum: best.cum, s0: best.s0, s1: best.s1 };
}

// minClockOffset = the minimum observed (clientNow - SERVED_AT), approximating
// browser-vs-server skew plus minimal latency. It calibrates off served_at (the
// instant the response left the server), NOT fetched_at (the backend's last poll):
// a response served from a poll N seconds ago would inflate a fetched_at-based
// offset by N, which then (a) cancelled the poll-age staleness term, so a stale
// backend looked fresh, and (b) shifted every arrivals countdown by N. served_at
// is skew + latency only, so this offset is clean. Used to skew-correct the
// arrivals countdown (map.js, which compares absolute MTA timestamps to the
// browser clock) and the client-elapsed term of staleness() below.
let minClockOffset = null;

// `now` is injected only for testability (noteClockOffset otherwise reads the wall
// clock, unlike staleness which always takes an explicit now).
function noteClockOffset(servedAt, now = Date.now() / 1000) {
  if (servedAt == null) return;
  const offset = now - servedAt;
  if (minClockOffset == null || offset < minClockOffset) minClockOffset = offset;
}

// ---- Per-system freshness (C2) ----

// Opacity for a marker whose system's data has gone stale. Dim enough to read as
// "not current" at a glance next to a live marker, light enough that the marker is
// still legible and clickable: a rider looking at a partial outage should be able to
// tell that these trains are the old ones, and still open one to see how old.
const STALE_MARKER_OPACITY = 0.45;

// ONE INGESTION PATH FOR BOTH PAYLOAD SHAPES, which is the whole point of this
// function. The aggregate feeds carry a per-system block (subways: 8 feed groups;
// railroads: LIRR + MNR; alerts: 5 systems); the single-feed ones (buses, PATH,
// ferry) carry only the envelope's own fetched_at. Rather than have every consumer
// below branch on which kind of payload it is holding, a single-feed payload gets a
// SYNTHESIZED one-system block named after its source, so the status line, the
// dimming, the popups and the glide freeze all read one shape and a single-feed
// source can never quietly skip a rule the aggregates follow.
//
// A single-feed source therefore reads exactly as it did pre-C2 in the status line
// ("buses: as of 3m ago", never "buses: buses as of 3m ago"), because naming every
// system of a source is just naming the source: see staleness().
//
// DEFENSIVE READS, because this parses a payload: a block entry with no numeric
// fetched_at reports a null age (unknown, not fresh and not stale) and is surfaced
// through `ok` instead; a missing `ok` reads as healthy, so a malformed field cannot
// dim the whole map; an EMPTY systems object falls back to the synthesized single
// system rather than yielding a source with no freshness at all.
//
// 6.2 WIDENED THIS DOOR BY ONE NAME, and it is the door in the literal sense: it read
// exactly four names (fetched_at, ok, retained_since, routes), and nothing else in a
// block reaches any surface. So the per-system content clock contract 6.1 put on every
// block reached none, and a system whose provider was ten minutes behind looked exactly
// like its current siblings, which is F03. It now reads feed_timestamp as well. A
// synthesized single system carries the ENVELOPE's feed_timestamp, because for a
// single-feed source (buses, PATH, ferry, and every arrivals board without a systems
// block) the envelope's content clock is that one system's.
//
// feedTimestamp HAS THREE STATES, and the two that are not numbers mean different
// things (see contentClock and systemLag): null is the backend saying this system has
// no content clock at all, undefined is a payload that predates the per-system clock.
//
// 6.3 WIDENED IT BY ONE MORE NAME: positions, the position ladder's counts a railroad
// block carries (models.PositionSteps). The one the rider's surfaces need is
// `suppressed`, the trains the gate stopped drawing, and it has no other way in: the
// client never fetches /api/status, so the status line can only say how many trains the
// map is not showing if the envelope it already reads says so. Null on every block
// without a ladder, on a synthesized system, and on a malformed one (positionSteps).
function ingestSystems(body, sourceKey) {
  const raw = body == null ? null : body.systems;
  const names = raw != null && typeof raw === "object" ? Object.keys(raw) : [];
  if (!names.length) {
    const fetchedAt = body == null ? null : body.fetched_at;
    return {
      [sourceKey]: {
        fetchedAt: typeof fetchedAt === "number" ? fetchedAt : null,
        ok: true,
        retainedSince: null,
        routes: null,
        feedTimestamp: contentClock(body == null ? undefined : body.feed_timestamp),
        positions: null,
      },
    };
  }
  const systems = {};
  for (const name of names) {
    const block = raw[name] ?? {};
    systems[name] = {
      fetchedAt: typeof block.fetched_at === "number" ? block.fetched_at : null,
      ok: block.ok !== false,
      retainedSince: typeof block.retained_since === "number" ? block.retained_since : null,
      // Null means this envelope does no route coverage at all (the railroad and
      // alerts blocks, whose entities name their own system); an array, possibly
      // empty, means it does.
      routes: Array.isArray(block.routes) ? block.routes : null,
      feedTimestamp: contentClock(block.feed_timestamp),
      positions: positionSteps(block.positions),
    };
  }
  return systems;
}

// The five counts of models.PositionSteps, in the backend's order: reported, estimated,
// qualified, placed and suppressed (section 3.4's steps 1 to 5).
const POSITION_STEP_KEYS = ["reported", "estimated", "qualified", "placed", "suppressed"];

// A block's position counts, or null. ALL FIVE OR NONE, because this parses a payload
// and the one count that reaches a surface raises the status line: a malformed field
// must not put a sentence about missing trains in front of a rider, which is the same
// direction the defensive reads above take for `ok` and fetched_at.
function positionSteps(value) {
  if (value == null || typeof value !== "object") return null;
  const steps = {};
  for (const key of POSITION_STEP_KEYS) {
    const count = value[key];
    if (!Number.isInteger(count) || count < 0) return null;
    steps[key] = count;
  }
  return steps;
}

// A block's content clock, read into its three states. A finite number is the content
// time. NULL IS A REAL ANSWER and is kept as one: the backend publishes null for a
// system whose header is not a usable freshness signal (Metro-North, whose header is a
// lagging copy; feeds.RAILROAD_FRESHNESS_SYSTEMS decides it, and this file must not
// restate it). Anything else, including a key that is simply absent, is UNDEFINED: a
// payload from before the per-system clock existed, or a malformed value, which
// systemLag answers with the envelope's one number exactly as the code did before 6.2.
function contentClock(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return value === null ? null : undefined;
}

// THE ENVELOPE'S OWN CLOCKS, through the same door as its per-system blocks. Every
// vehicle feed and, since 6.1, every arrivals board carries fetched_at, feed_timestamp
// and served_at beside its systems map, and before 6.2 only the vehicle feeds' reached
// a surface: refreshSource read them inline and the boards read fetched_at alone. One
// function now reads all four for both kinds of envelope, so a board ages its rows from
// the same served_at, read the same way, that the status line ages the map from. Each
// clock is a finite number or null; a payload's other fields are none of its business.
function ingestEnvelope(body, sourceKey) {
  const clock = (key) => {
    const value = body == null ? null : body[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  return {
    fetchedAt: clock("fetched_at"),
    feedTimestamp: clock("feed_timestamp"),
    servedAt: clock("served_at"),
    systems: ingestSystems(body, sourceKey),
  };
}

// A source's systems, falling back to the synthesized single system when a caller
// hands over a descriptor that has not ingested a payload yet (boot) or a plain row
// in a unit test. Keeps every consumer free of null checks.
//
// The fallback names its system after the source's LABEL, which is not the key
// refreshSource ingests under; that would be a trap if the name mattered here, so
// the freshness index deliberately skips a source with no payload yet (there is
// nothing to say about it) and only staleness() uses this path, where the single
// system is never named separately.
function sourceSystems(source) {
  return source.systems ?? ingestSystems({ fetched_at: source.fetchedAt }, source.label ?? "feed");
}

// The poll-age term of staleness(), lifted out so the per-system judgment and the
// whole-source one cannot drift apart. `now` is the RAW client clock (the skew
// correction happens here, via minClockOffset), matching staleness() below.
function pollAge(fetchedAt, servedAt, now) {
  if (fetchedAt == null) return null;
  if (servedAt == null) return now - fetchedAt - (minClockOffset ?? 0);
  // Server cache age is skew-free by construction; client elapsed is clamped at 0
  // (data cannot be fresher than when it was served). See staleness().
  return servedAt - fetchedAt + Math.max(now - servedAt - (minClockOffset ?? 0), 0);
}

// ONE SYSTEM'S CONTENT LAG at its own last decode: its fetched_at minus its own
// feed_timestamp, both from the same block, which is the backend's _feed_age asked of
// one system rather than of one envelope.
//
// THIS WAS THE SHARED LAG TERM, and 6.2 is what made it per system. It used to be the
// ENVELOPE's one number, fetched_at minus feed_timestamp for the whole response, on the
// reasoning that the envelope's feed_timestamp "cannot be attributed to one system".
// That was true until 6.1, and it had a cost the reasoning did not state: for the
// subway the envelope's feed_timestamp is the MINIMUM header across every group that
// decoded, so one group ten minutes behind aged all eight. Every marker on the map
// dimmed and the status line spoke for the whole source while seven feeds were
// current, which is F03's second clause one surface over. Each block now carries its
// own content clock, so each system answers for its own.
//
// The three states of feedTimestamp (contentClock): a number is aged here; NULL means
// this system has no content clock, so it contributes no lag and its age is its poll
// age alone (Metro-North, which must not borrow LIRR's clock through the envelope);
// UNDEFINED means the payload predates the per-system clock, so the envelope's number
// stands in, exactly as it did before, and an older backend reads as it always did.
function systemLag(source, system) {
  if (system.feedTimestamp === undefined) {
    return source.feedTimestamp == null || source.fetchedAt == null
      ? 0
      : source.fetchedAt - source.feedTimestamp;
  }
  if (system.feedTimestamp === null || system.fetchedAt == null) return 0;
  return system.fetchedAt - system.feedTimestamp;
}

// Age of EACH of a source's systems, keyed by system name; null for a system that
// has never decoded (no fetched_at to age against). The larger of its own content lag
// (systemLag) and its poll age: either alone makes what a system drew old.
//
// On a fully healthy source every system's fetched_at equals the envelope's and every
// lag is a few seconds, so the worst of these ages is exactly the age R1 computed,
// which is what keeps the healthy case rendering unchanged.
function systemAges(source, now = Date.now() / 1000) {
  const ages = {};
  for (const [name, system] of Object.entries(sourceSystems(source))) {
    const poll = pollAge(system.fetchedAt, source.servedAt, now);
    ages[name] = poll == null ? null : Math.max(systemLag(source, system), poll, 0);
  }
  return ages;
}

// Is this age stale? One predicate so the marker dimming, the popup age line, the
// glide freeze and the status line can never disagree about the boundary.
/* 6.3 ERRATUM (MR3, the operator's ruling on finding N3): AN OBSERVATION THAT SHOULD CARRY A
   CLOCK AND DOES NOT IS NOT FRESH.

   The contract's 3.2 clause (c) already makes a null `observed_at` on an age-gated row SAYABLE
   ("age unknown"), and calls it an anomaly: "The provider normally dates this and did not." It
   did not say what such a row is DRAWN as, and the answer the code fell into was "bright",
   because staleAge read an age and there was none. That made the brief's 3.1 row 6 undrawable
   and, worse, made a row the contract calls an anomaly render exactly like a fix five seconds
   old.

   THE RULE IS THAT DIMMING CARRIES NOT-FRESH, NOT AN AGE. A dimmed marker has never meant "this
   is N seconds old"; it has meant "do not read this as current", which is precisely what an
   observation with no clock earns on a row whose provider normally sends one. So AGE_UNKNOWN is
   not a large age, it is the ABSENCE of one, and it is stale by this rule rather than by
   arithmetic. It is written as its own clause below rather than left to `Infinity >= 90` being
   true, so the rule is greppable and a reader meets it instead of inferring it.

   WHAT THIS DOES NOT TOUCH. The WORDS are unchanged: positionQualifier already said "age
   unknown" and still does, and no surface gains or loses a sentence. The position ladder's five
   states are unchanged, since a step is decided in the backend from the served row and nothing
   here reaches it (the F01 world's 60/11/6/59/24 are the same before and after). The glide
   freeze is unchanged: observationStaleAt still returns null for a clockless row, so such a
   marker freezes on its SYSTEM's deadline exactly as before. This is opacity, and only opacity.

   AND IT DOES NOT REACH A SYSTEM THAT DATES NOTHING. Metro-North's rows are not age-gated, by
   the 3.3 policy table, so they never take this value: a qualifier a rider sees on all 33
   Metro-North markers always is one they stop reading, which is the same argument clause (c) is
   narrow for. `gated` is the caller's, read from UNDATED_SYSTEMS and never from a system's name. */
const AGE_UNKNOWN = Infinity;

function staleAge(age) {
  if (age === AGE_UNKNOWN) return true;
  return age != null && age >= FEED_STALE_AFTER_S;
}

/* The age DIMMING reads, which is the observation's own age or, for the anomaly above,
   AGE_UNKNOWN. Pure and separate from vehicleMarkerAge (which needs the page's envelope) so the
   erratum's one rule can be asked directly by a node test.

   IT KEYS ON THE ROW'S OWN STAMP, not on the derived age being null, and the distinction is the
   point: observationAge also returns null when the page has no envelope to measure against,
   which is a gap in the PAGE rather than an anomaly in the ROW, and dimming every marker on the
   map for it would be the opposite of honest. So a row that carries a clock keeps whatever age
   was computed from it, including none. */
/* WHICH FAMILIES OWE AN OBSERVATION CLOCK, AS A TABLE (the operator's ruling on R-b).

   The first cut read `!UNDATED_SYSTEMS.has(row.system)`, and that was wrong in a way that only
   showed up one layer out: bus, subway, PATH and ferry rows carry NO `system` field at all, so
   `has(undefined)` was false and every one of them was gated by accident rather than by a
   decision. The answer happened to be right and the reasoning was not, which is the shape that
   survives a review and breaks on the next system added.

   SO IT IS THE CONTRACT'S 3.3 TABLE, TRANSCRIBED, keyed by the family name each layer already
   passes to vehicleMarkerAge, plus the two railroad systems, which are the one source whose two
   halves differ. Every row here has a row there:

     subways  gated   vehicle.timestamp, else the contributing group header
     buses    gated   vehicle.timestamp, 2136 of 2136 self-dated
     path     gated   trip_update.timestamp, 53 of 53 and 55 of 55
     ferry    gated   vehicle.timestamp (served as updated_at), 28 of 28
     njt      gated   the TripUpdates header, lag 9s to 23s at peak
     LIRR     gated   vehicle.timestamp, 69 of 69 independent
     MNR      NOT     "The stamp is a copy of a header that lags 2 to 4 minutes.
                       Gating on it would mark a live fleet stale."

   AN UNLISTED FAMILY IS GATED, which is the pessimistic answer and the one that matches what
   every listed family but Metro-North says; a node test enumerates the call sites so a new
   family cannot arrive unlisted without failing. */
const OBSERVATION_GATED = {
  subways: true,
  buses: true,
  path: true,
  ferry: true,
  njt: true,
  LIRR: true,
  MNR: false,
};

// `family` is the row's own system where it has one (the railroads are two systems under one
// source and they differ), else the source key its layer passes.
function observationGated(sourceKey, row) {
  const family = (row && row.system) || sourceKey;
  const gated = OBSERVATION_GATED[family];
  return gated === undefined ? true : gated;
}

function observationDimAge(row, observationAge, gated) {
  if (observationAge != null) return observationAge;
  const stamp = row ? row.observed_at : null;
  const dated = typeof stamp === "number" && Number.isFinite(stamp);
  return !dated && gated ? AGE_UNKNOWN : observationAge;
}

// The marker opacity a system's age earns: dimmed once stale, otherwise fully
// opaque. Pure so the dimming rule is node-testable; the Leaflet call sits in the
// system files.
//
// `base` is a marker's own resting opacity, which COMPOUNDS with staleness rather
// than being replaced by it. The ferry layer has one (a STOPPED_AT boat rides at
// FERRY_DOCKED_OPACITY to read as parked), and a docked boat on a stale feed is
// legitimately both. This has to be one number per marker: the docked dimming used
// to come from a css class, and an inline opacity written for staleness would have
// silently overridden it, un-dimming every docked boat the moment C2 started
// setting opacities.
// `age` may be AGE_UNKNOWN, which is not an age but the 6.3 erratum's answer for an
// observation that should have carried a clock and did not; staleAge says why it is stale.
function markerOpacity(age, base = 1) {
  return base * (staleAge(age) ? STALE_MARKER_OPACITY : 1);
}

// THE INSTANT EACH SYSTEM'S GLIDE MUST STOP, on the skew-corrected clock (the same
// axis trainLatLng runs on), or null while a system may still be interpolated.
// Three ways a system reaches that instant:
//
//   1. Its data is being RETAINED. retained_since says the backend is serving a
//      generation it could not refresh, so the anchors behind any interpolation are
//      known dead from that moment. This fires as soon as retention starts, before
//      the age threshold, which is the point: gliding is a PREDICTION from a fresh
//      observation, and predicting from data we already know is not being refreshed
//      is dead reckoning no matter how young it is. (Opacity is deliberately NOT
//      moved here: dimming states how OLD the data is, and the app's definition of
//      old is FEED_STALE_AFTER_S for every source alike.)
//   2. Its own poll age reaches the threshold, at fetchedAt + FEED_STALE_AFTER_S.
//   3. Its upstream content was ALREADY past the threshold when it was polled (its
//      own lag term, systemLag). Nothing may advance past the observation itself, so
//      the deadline is fetchedAt. Per system since 6.2, for the reason systemLag
//      gives: this deadline and the age above must agree about which system is old,
//      or a lagging group would freeze its healthy siblings while leaving them bright.
//
// REVIEW FIX. This used to be glideClock(now, age) subtracting (age - threshold),
// which only froze while the POLL term dominated: with the upstream-lag term
// dominating, age is a constant across a poll interval, so `now - constant` advanced
// at full speed and every marker on the source kept dead-reckoning while dimmed.
// Expressing the freeze as an absolute instant cannot drift that way, and it needs no
// clock, so it is a pure function of the payload.
function systemStaleAts(source) {
  const deadlines = {};
  for (const [name, system] of Object.entries(sourceSystems(source))) {
    if (system.fetchedAt == null) {
      deadlines[name] = null; // never decoded: no anchor, so nothing to freeze
      continue;
    }
    const lag = systemLag(source, system);
    const aged = system.fetchedAt + (lag >= FEED_STALE_AFTER_S ? 0 : FEED_STALE_AFTER_S);
    deadlines[name] =
      system.retainedSince == null ? aged : Math.min(aged, system.retainedSince);
  }
  return deadlines;
}

// THE GLIDE FREEZE (C2). The clock an interpolated marker may use: the live clock
// while its system may still be predicted from, pinned at that system's deadline
// (systemStaleAts) once it may not.
//
// WHY freezing rather than letting the glide run: a train would keep sliding
// confidently along its route for as long as the outage lasted, which is a worse lie
// than the frozen position (the frozen one is at least somewhere the train really
// was). A system with no deadline gets `now` back untouched, so normal gliding is
// bit-for-bit unchanged.
function glideClock(now, staleAt) {
  return staleAt == null ? now : Math.min(now, staleAt);
}

// Two independent staleness signals, flag if EITHER crosses the threshold:
//   1. upstream lag = fetched_at - feed_timestamp — both server-recorded, so
//      this is clock-skew free; detects the MTA feed itself going stale.
//   2. poll age = how long ago the data behind this response was actually polled
//      upstream, detecting OUR backend having stopped polling while it keeps
//      serving frozen last-good data (upstream lag alone would stay constant and
//      silent). Two skew-clean parts:
//        - server cache age (served_at - fetched_at): both server clocks, so it is
//          skew-free BY CONSTRUCTION and honest on the very first observation,
//          before any client calibration settles. This is the term the old model
//          was blind to.
//        - client elapsed since the response arrived (now - served_at, skew-
//          corrected by the now-clean minClockOffset), clamped to >= 0 (the data
//          cannot be fresher than when it was served).
//      When served_at is absent (a response predating the served_at contract),
//      fall back to the old single term so a stuck backend is still caught.
//
// C2 MADE THIS PER SYSTEM. The two terms above are unchanged; what changed is what
// they are computed against. A partial outage is a SUCCESSFUL poll, so the
// envelope's own fetched_at keeps advancing and this phrase could never fire for
// the system that was actually down. It now ages every system separately
// (systemAges) and reports the worst, which is why a down MNR surfaces while a
// healthy LIRR stays quiet.
//
// THE COMMON CASE MUST NOT GET NOISIER, so the wording is graded:
//   - every system fresh: null, exactly as before.
//   - the WHOLE source degraded in ONE way (one synthesized system, or every system
//     of an aggregate in the same population): "railroad: as of 6m ago", the pre-C2
//     wording untouched.
//   - otherwise the degraded systems are named, each population reporting its own
//     worst age, e.g. "railroad: MNR as of 6m ago" or "trains: ACE group as of 4m ago"
//     (source.systemNoun supplies the "group", which reads wrong for a system).
//   - a system that has NEVER decoded cannot be aged, so it is reported by name
//     with no age rather than silently dropped.
// A system that merely failed its last poll is NOT named until its age crosses the
// threshold: single failed polls are routine, and naming them would make the status
// line chatter during normal operation.
//
// THREE POPULATIONS, EACH ITS OWN CLAUSE, and 6.2 added the middle one. A system is in
// at most one of them:
//   1. STALE: its poll age has crossed the threshold, whatever its content was.
//   2. CONTENT OLD: its poll is fresh and what that poll fetched is not, because its
//      own content lag (systemLag) has crossed the threshold. This is F03's state, a
//      provider serving old content to polls that keep succeeding. Before 6.2 the line
//      could say it only through the envelope's one lag, which spoke for every system
//      of the source at once; with the lag per system it names the one that is behind.
//   3. BLIND: never decoded, and reported down.
// The first two share the words "as of {age} ago" (design 4.4 reuses them verbatim)
// and never share a clause, for the reason stale and blind were split: a system in one
// state announced with another state's age is exactly the defect that split them. A
// feed whose poll stopped five minutes ago and a feed serving ten minute old content
// are two facts with two ages, and one clause would hand one of them the other's.
//
// A system with NO content clock (Metro-North's null) is never CONTENT OLD: it has no
// content age to be old, and its line stays exactly as quiet as it always was.
//
// 6.3 ADDS TWO MORE CLAUSES, and they are built the two opposite ways on purpose.
//   4. WITHHELD (design Q7): "LIRR 24 trains not shown, last seen over 10m ago", for a
//      system whose position ladder drew nothing for some of its vehicles (section 3.4's
//      step 5): each one's own fix is older than OBS_MAX_S, and its trip has no
//      prediction within OBS_MAX_S that can still place it. On the committed LIRR capture
//      that is 24 trains: 20 whose trip has no prediction that recent, and 4 whose trip
//      update is recent (27 s to 529 s old) but names no stop still ahead. So "last
//      seen" is about the train's own position, the one observation all 24 share. It
//      RIDES a line that is already rendering and never raises one, for the reason the
//      UNDATED clause below rides: WITHHOLDING IS LIRR'S STEADY STATE, NOT A FAULT. The
//      committed capture has 24 of 68 withheld on a wholly healthy railroad
//      (backend/tests/test_f01_positions.py), and design 2.1 measures the distribution
//      behind that as structural rather than incidental. A clause that could raise the
//      line would therefore raise it on every poll of a healthy feed, and map.js paints
//      any rendered line with the error class, so a rider would see a red status bar
//      every day and the red would stop telling them anything. That is the hazard
//      design 3.2 names ("a status line that always says something is a status line
//      nobody reads"), and it is the same argument this branch makes on the operator's
//      side, where the monitor's railroad-positions line refuses to WARN on any count
//      for exactly this reason (README, and _check_production_railroad_positions).
//      REVIEW FIX: it raised the line until the whole-branch review measured what that
//      meant, which was a permanently red status bar and, worse, a permanently raised
//      line for the UNDATED clause to ride, defeating the trade that clause was built
//      for. The count itself is not lost when nothing else renders: it is served on
//      every railroad block and on /api/status, and the monitor prints it every run.
//      Its own clause, never merged into another, in the grammar of "not reporting" (a
//      count beside the system's name), and "10m" is OBS_MAX_S through humanizeAge. It
//      speaks only while that system's last decode is what the map draws (its poll
//      decoded, or its rows are retained): once the retention cap has taken every train
//      the counts describe, "24 not shown" would undercount a system the STALE clause
//      already names.
//   5. UNDATED: "MNR position age unavailable", for a system in UNDATED_SYSTEMS, the set
//      frontend/boards.test.js holds to the backend's RAILROAD_FRESHNESS_SYSTEMS. This one
//      may only RIDE a line that is already rendering and never raise one (design 3.2 and
//      Q5): Metro-North dates none of its positions on any day, so a clause that could
//      raise the line would raise it forever, and a status line that always says
//      something is one nobody reads. So it is appended last, after every other clause,
//      and only when there is one. It names the system the way the line already does
//      ("MNR", the feed code the other clauses use), which is what 3.2 writes.
// Neither enters `populated` or `whole`: they are about the ladder and the provider, not
// about a poll's age, so the whole-source wording above is decided exactly as before.
// `now` is injected for testability (defaults to the wall clock).
function staleness(source, now = Date.now() / 1000) {
  const systems = sourceSystems(source);
  const names = Object.keys(systems).sort();
  const stale = [];
  const content = [];
  const blind = [];
  const withheld = [];
  const ages = {};
  for (const name of names) {
    const system = systems[name];
    if (withheldTrains(system) > 0) withheld.push(name);
    const poll = pollAge(system.fetchedAt, source.servedAt, now);
    if (poll == null) {
      // Never decoded (null age) AND reported down: no age to print, but real.
      if (!system.ok) blind.push(name);
      continue;
    }
    const lag = systemLag(source, system);
    if (staleAge(poll)) {
      stale.push(name);
      ages[name] = Math.max(lag, poll);
    } else if (staleAge(lag)) {
      content.push(name);
      ages[name] = lag;
    }
  }
  const populated = [stale, content, blind].filter((group) => group.length).length;
  // The withheld and undated clauses ride; only a poll-age population raises the line.
  if (!populated) return null;
  const noun = source.systemNoun ? ` ${source.systemNoun}` : "";
  // Naming every system of a source is just naming the source, so fall back to the
  // pre-C2 wording; that is also what keeps a single-feed source reading unchanged.
  // Only when ONE population covers the source, though: two populations have to be
  // told apart, and naming them is how.
  //
  // REVIEW FIX, kept from when there were two populations: the stale and blind sets
  // used to be merged into a single subject that then took its age from the stale set
  // alone, so a system which had never reported anything was announced with another
  // system's age ("ACE group, SIR group as of 4m ago" when SIR had no data at all). They
  // collide exactly during a broad incident, which is when the line gets read.
  const whole = stale.length + content.length + blind.length === names.length && populated === 1;
  const subject = (group) => (whole ? "" : `${group.map((n) => `${n}${noun}`).join(", ")} `);
  const worst = (group) => humanizeAge(Math.max(...group.map((name) => ages[name])));
  const clauses = [];
  if (stale.length) clauses.push(`${subject(stale)}as of ${worst(stale)} ago`);
  if (content.length) clauses.push(`${subject(content)}as of ${worst(content)} ago`);
  if (blind.length) clauses.push(`${subject(blind)}not reporting`);
  // Riding, never raising: a population above has already decided that this line renders.
  for (const name of withheld) clauses.push(withheldClause(name, withheldTrains(systems[name])));
  for (const name of names) if (UNDATED_SYSTEMS.has(name)) clauses.push(`${name} position age unavailable`);
  return `${source.label}: ${clauses.join("; ")}`;
}

// How many of a system's trains the position ladder drew nothing for, as far as the map
// can still be told: the served `suppressed` count while the decode it describes is the
// one on the map (the system decoded, or its rows are being retained), and 0 otherwise.
function withheldTrains(system) {
  const steps = system ? system.positions : null;
  if (!steps || !(steps.suppressed > 0)) return 0;
  return system.ok || system.retainedSince != null ? steps.suppressed : 0;
}

// "LIRR 24 trains not shown, last seen over 10m ago": the one sentence that says a train
// has left the map. OBS_MAX_S is the backend's, mirrored, and humanizeAge words it.
function withheldClause(system, count) {
  const trains = count === 1 ? "train" : "trains";
  return `${system} ${count} ${trains} not shown, last seen over ${humanizeAge(OBS_MAX_S)} ago`;
}

// A compact age string: seconds under two minutes, whole minutes above, and hours with
// minutes from a hundred minutes on. The one age formatter, shared by the status line,
// the vehicle popups and (6.2) every board row, so no two surfaces word one age
// differently.
//
// THE HOURS TIER IS 6.2's, because 6.2 is the first step that shows ages this large:
// LIRR dates each prediction by its own trip update, and on the committed capture the
// oldest served prediction is 52538 seconds old, which the two-tier form rendered as
// "as of 876m ago". Section 3.2 of the freshness contract names the fix, a third tier
// borrowed from countdownParts, and borrowing its ROUNDING too is what keeps a board's
// "1 h 40 min" countdown and its "1h 40m" age from ever disagreeing about the minute.
// Below a hundred minutes the output is exactly what it always was.
function humanizeAge(age) {
  if (age < 120) return `${Math.round(age)}s`;
  const p = countdownParts(age);
  if (p.kind === "hm") return p.rem ? `${p.hours}h ${p.rem}m` : `${p.hours}h`;
  return `${p.mins}m`;
}

// The "as of Xm ago" line from an AGE, for the C2 train/boat/bus popups: their
// staleness comes from a system block's age (already carrying the server cache-age and
// skew terms). The station boards render their system line into the same markup
// (boardLineHtml), so a stale board and a stale train cannot be styled or worded apart.
/* MR5 (ruling Q2): stalePopupLine WAS HERE and is gone with its one caller. It rendered the age
   line gated on staleness, and vehicleStaleLine was the only thing that called it; the popup footer
   took that job and gates on feedDotState instead. boardLineHtml still renders the same `.popup-stale`
   element for a station board's system line, which is 6.2's and is untouched, so the string and the
   class both survive with one renderer rather than two.
   THE TWO SURFACES NOW WORD THE AGE DIFFERENTLY and that is a consequence of the ruling rather than
   an oversight: a board says the contract's "as of 4m ago" and a vehicle's footer says the feed
   strip's "As of 4m ago", because the ruling is that the footer says it the strip's way so the state
   is said one way on BOTH of those surfaces. helpers.test.js pins both forms side by side so the
   difference is a record rather than a surprise. */

// ---- 6.2: a board's rows, each qualified by its own age ----
//
// F03, the audit's finding: a board counted down to a prediction and never said how
// old the prediction was. Its only age line was now - fetched_at, the age of OUR POLL,
// so it spoke when our poller stopped and stayed silent when the provider did, and a
// ten minute old prediction read as a live two minute countdown. Contract 6.1 put the
// provider's own clock on every row (observed_at) and the served instant on every
// envelope (served_at). What follows reads them, per ROW, so that on a board served by
// several contributors a stale one's rows are marked and a current one's are not: the
// auditor's second clause, "other healthy contributors remain distinguishable".

// THE SYSTEMS WHOSE PROVIDER DATES NOTHING: section 3.3's two non-gated rows, both
// Metro-North's. A null observed_at on any other system's prediction is an anomaly,
// said at the row ("age unknown"); on these it is the provider's standing answer, so it
// is silent at the row and stated once on the board's system line (3.2 clause (c), as
// Q5 amended it). MIRRORED RATHER THAN DERIVED, because one payload cannot tell "this
// provider never dates" from "this provider dated nothing this time", and
// frontend/boards.test.js holds this set to exactly the railroad systems that
// backend/feeds/railroad.py's RAILROAD_FRESHNESS_SYSTEMS leaves out, so the two cannot
// drift. 6.3 reads the same set for positions.
const UNDATED_SYSTEMS = new Set(["MNR"]);

// The system a board's rows come from, for the age policy: a railroad board's own
// (its envelope names it), and otherwise the board's kind, since every other board
// draws from one provider.
function boardSystem(kind, body) {
  if (kind === "railroad") return (body && body.system) || null;
  return kind;
}

// How old a server- or provider-stamped instant is NOW, on the server's clock: its age
// when this response was built (served_at minus the stamp, both from the payload, so
// skew-free, the pairing the backend uses for feed_age), plus the time since, which is
// the client's elapsed on the skew-corrected clock and never negative. That second term
// is what keeps a board honest when its background refresh fails and the last-known
// rows keep ticking. Without a served_at (a response from before 6.1), the corrected
// clock alone. `now` is the skew-corrected clock every board computes for its
// countdowns. Null when there is no stamp to age.
function servedAge(stamp, servedAt, now) {
  if (typeof stamp !== "number" || !Number.isFinite(stamp)) return null;
  if (servedAt == null) return now - stamp;
  return servedAt - stamp + Math.max(now - servedAt, 0);
}

// A board's freshness, read through the door once per render: the served instant, the
// age of its worst system's last poll (what a row with no clock of its own is as old
// as), and whether its predictions are age-gated at all. An unknown system is gated,
// the pessimistic default: its null rows say "age unknown" rather than nothing.
function boardFreshness(kind, body, now) {
  const system = boardSystem(kind, body);
  const envelope = ingestEnvelope(body, system ?? kind);
  let pollAge = null;
  for (const block of Object.values(envelope.systems)) {
    const age = servedAge(block.fetchedAt, envelope.servedAt, now);
    if (age != null && (pollAge == null || age > pollAge)) pollAge = age;
  }
  return { now, system, servedAt: envelope.servedAt, pollAge, gated: !UNDATED_SYSTEMS.has(system) };
}

const NO_QUALIFIER = Object.freeze({ kind: "", words: "" });

// THE WORDS ONE BOARD ROW CARRIES BESIDE ITS COUNTDOWN, and the one helper the station
// popup and the station panel both render them through. Section 3.2's vocabulary for a
// prediction, exactly:
//
//   reported, fresh                    nothing: silence means current
//   reported, older than OBS_FRESH_S   "as of {age} ago"  (OBS_FRESH_S is
//                                      FEED_STALE_AFTER_S, one number, via staleAge)
//   retained                           "showing last known, as of {age} ago"
//   observed_at null, age-gated        "age unknown": this provider normally dates it
//   observed_at null, not gated        nothing at the row; see boardSystemLine
//   anything else                      "age unknown": the `unknown` provenance, a row
//                                      with none at all (an older backend), or a value
//                                      no prediction can carry
//
// The countdown still counts to the prediction; this sits beside it. A retained row
// with no clock of its own is as old as its system's last poll, which is when we last
// had it.
//
// Returns {kind, words}. `words` is what a rider reads, and changes as the age grows;
// `kind` ("", "aged", "retained" or "unknown") is what it IS, and changes only when
// the row crosses the threshold, never while its age counts on past it. The live
// region compares kinds and never words, which is what makes a qualifier that appears
// an announcement and a qualifier that counts up a non-event.
function arrivalQualifier(row, board) {
  const r = row || {};
  const age = servedAge(r.observed_at, board.servedAt, board.now);
  if (r.provenance === "retained") {
    const held = age ?? board.pollAge;
    return {
      kind: "retained",
      words: held == null ? "showing last known" : `showing last known, as of ${humanizeAge(Math.max(held, 0))} ago`,
    };
  }
  if (r.provenance !== "reported") return { kind: "unknown", words: "age unknown" };
  if (age == null) return board.gated ? { kind: "unknown", words: "age unknown" } : NO_QUALIFIER;
  return staleAge(age) ? { kind: "aged", words: `as of ${humanizeAge(age)} ago` } : NO_QUALIFIER;
}

// THE BOARD'S SYSTEM LINE: what a board says about its SYSTEM rather than about a row,
// or null. It speaks only for what the rows cannot, because a dated row carries its own
// age and a line repeating it would say everything twice:
//
//   * an EMPTY board, whose "No trains" is only as current as the poll behind it;
//   * rows whose provider does not date them (Metro-North's), which have no age at all.
//
// For those it gives the age of the board's last poll, once that age is stale, from the
// served values (servedAge over each system's fetched_at, the worst answering). That is
// the R1 honesty line these boards always had, now fed by the door rather than by
// subtracting fetched_at on the spot. For undated rows it adds "{system} prediction age
// unavailable", the prediction form of 3.2's per-system clause (3.2's table carries it
// since 6.2), naming the system in the RIDER'S word ("Metro-North", never the feed code
// "MNR", because the panel speaks this line and an initialism is read letter by
// letter), RIDING the line and never raising it: on a healthy day a Metro-North board says nothing at all, because a
// qualifier present on every board of a railroad is one a rider stops reading (Q5).
function boardSystemLine(board, rows) {
  const all = rows || [];
  const undated =
    !board.gated &&
    all.some((r) => r && r.provenance === "reported" && servedAge(r.observed_at, null, 0) == null);
  if (all.length && !undated) return null;
  if (!staleAge(board.pollAge)) return null;
  const line = `as of ${humanizeAge(board.pollAge)} ago`;
  return undated ? `${line}; ${railroadSystemLabel(board.system)} prediction age unavailable` : line;
}

// The popup's markup for the two: the system line in the slot the R1 age line used, in
// the same element stalePopupLine writes, and a row's qualifier beside its countdown.
// Escaped like every other string these renderers emit, though both are our own words.
function boardLineHtml(line) {
  return line ? `<div class="popup-stale">${esc(line)}</div>\n` : "";
}

function qualifierHtml(qualifier) {
  return qualifier.words ? ` <span class="arr-qualifier">${esc(qualifier.words)}</span>` : "";
}

// ---- 6.3: a vehicle's position, qualified by its own observation ----
//
// F01, the audit's finding: an LIRR vehicle's GPS fix is served with its own clock and
// drawn as "live GPS" at full opacity however old that clock is, so a coordinate nearly
// fifteen hours old reads as a live train. Section 3.4 of the contract orders what the
// backend draws instead (reported, estimated, reported but qualified, placed, or
// nothing), and the helpers below are what every vehicle surface reads to say which one
// a rider is looking at. They landed inert one commit before the gate and are wired in
// the gate's own commit, because the words, the per-observation dimming and the glide
// freeze have to arrive with the gate that makes them true (design 6.3), the pairing
// retention and its stale rendering keep (backend/cache.py, FEED_RETENTION_ENABLED).

// The backend's OBS_MAX_S (backend/cache.py): past it the ladder draws a vehicle only if
// a prediction still places the train, and otherwise counts it. MIRRORED RATHER THAN
// SERVED because the client's one use is stating it, in the suppression clause's "last
// seen over 10m ago", and frontend/positions.test.js reads it out of cache.py so the two
// cannot drift. Not overridable, like the constant it mirrors.
const OBS_MAX_S = 600;

// How old a vehicle's own observation is NOW, on the server's clock: servedAge over its
// observed_at, so a marker is aged by the rule that ages a board row (anchored at
// served_at, plus the client's elapsed time since, never negative). Null for a row with
// no clock, which dims nothing on its own; positionQualifier says it in words instead.
function observationAge(row, servedAt, now) {
  return servedAge(row ? row.observed_at : null, servedAt, now);
}

// THE INSTANT A VEHICLE'S OWN OBSERVATION STOPS BEING PREDICTABLE FROM, or null for a row
// with no clock: observed_at + FEED_STALE_AFTER_S (the design's OBS_FRESH_S), on the axis
// systemStaleAts uses. It is the per-observation form of that function's deadline: a
// marker may glide until the earlier of the two and no further, so a healthy system
// cannot dead-reckon a train from a fix that is itself old.
function observationStaleAt(row) {
  const stamp = row ? row.observed_at : null;
  if (typeof stamp !== "number" || !Number.isFinite(stamp)) return null;
  return stamp + FEED_STALE_AFTER_S;
}

// THE WORDS ONE VEHICLE CARRIES ABOUT ITS POSITION, and the one helper every vehicle
// surface renders them through, as arrivalQualifier is for a board row. Section 3.2's
// vocabulary for a position, read from the SERVED provenance and clock, never from the
// shape of the fields (isPlacedRailroad's stop_id test is what this replaces):
//
//   reported, fresh                    "live GPS", kind "": a surface that never said it
//                                      adds nothing
//   reported, older than OBS_FRESH_S   "live GPS, as of {age} ago" (OBS_FRESH_S is
//                                      FEED_STALE_AFTER_S, one number, via staleAge)
//   reported, no clock, age-gated      "live GPS, age unknown": this provider dates its
//                                      positions and did not date this one
//   reported, no clock, not gated      "live GPS" and nothing more: its system's status
//                                      line says the rest (Metro-North)
//   estimated                          "estimated from a prediction"
//   placed                             "scheduled position (no GPS)"; compact form
//                                      "scheduled (no GPS)"
//   retained                           "showing last known, as of {age} ago"
//   anything else                      "age unknown": the `unknown` provenance, a row with
//                                      none (an older backend), or a value no position
//                                      carries
//
// An estimated or placed row adds ", as of {age} ago" once its clock is aged, and ", age
// unknown" when it has none on a gated system: clause (c) of 3.2's rule holds for every
// provenance, and the reported row's form is the pattern. A retained row with no clock of
// its own is as old as its system's last poll (board.pollAge), which is when we last had
// it: the rule arrivalQualifier applies to a retained board row, so a retained
// Metro-North marker, which never has a clock, reads the age its board reads. Only with
// neither age does it say "showing last known" alone.
//
// board is { now, servedAt, system, gated, pollAge }: the skew-corrected clock, the
// envelope's served_at, the row's system, whether that system's position row is
// age-gated, and the age of that system's last poll (optional, and read only for a
// retained row; boardFreshness computes the same number for a board). A board without
// `gated` reads it off UNDATED_SYSTEMS by `system`, and an unknown system is gated, the
// pessimistic default boardFreshness takes.
//
// Returns {kind, words, compact, spoken, age}, the kind one of "", "aged", "unknown",
// "estimated", "placed" and "retained". As with arrivalQualifier, the kind is what the
// row IS and holds still while its age counts on, so a live region that compares kinds
// hears a train change state and never its clock tick. The three strings are ONE answer
// in the three shapes a surface prints, all built here from the same pieces, so no
// surface composes its own phrasing and none can disagree about an age:
//   words    every popup's line (design 3.2's vocabulary)
//   compact  the railroad popup's shorter form: "scheduled (no GPS)" for a placed row,
//            which that popup has always said and memo D9 keeps; otherwise the words
//   spoken   an accessible name's clause: "scheduled position, no GPS" for a placed row,
//            the form every marker name has shipped with (a comma, no parentheses,
//            because a screen reader reads a parenthesis as punctuation or as nothing);
//            otherwise the words
// `age` is the age those words STATE, or null when they state none (a fresh position,
// an undated one, a row with no provenance): what a popup reads to decide whether its
// feed's age line would only say the same thing twice (popupFreshHtml's suppression rule).
function positionQualifier(row, board) {
  const r = row || {};
  const b = board || {};
  const gated = typeof b.gated === "boolean" ? b.gated : !UNDATED_SYSTEMS.has(b.system);
  const age = observationAge(r, b.servedAt, b.now);
  const stale = staleAge(age);
  const aged = stale ? `, as of ${humanizeAge(age)} ago` : "";
  const undated = age == null && gated ? ", age unknown" : "";
  const answer = (kind, words, { compact = words, spoken = words, stated = stale ? age : null } = {}) => ({
    kind,
    words,
    compact,
    spoken,
    age: stated,
  });
  if (r.provenance === "reported") {
    if (age == null && gated) return answer("unknown", "live GPS, age unknown");
    return stale ? answer("aged", `live GPS${aged}`) : answer("", "live GPS");
  }
  if (r.provenance === "estimated") {
    return answer("estimated", `estimated from a prediction${aged}${undated}`);
  }
  if (r.provenance === "placed") {
    return answer("placed", `scheduled position (no GPS)${aged}${undated}`, {
      compact: `scheduled (no GPS)${aged}${undated}`,
      spoken: `scheduled position, no GPS${aged}${undated}`,
    });
  }
  if (r.provenance === "retained") {
    const held = age ?? b.pollAge;
    const shown = held == null ? null : Math.max(held, 0);
    return answer("retained", shown == null ? "showing last known" : `showing last known, as of ${humanizeAge(shown)} ago`, {
      stated: shown,
    });
  }
  // THE FAIL-SAFE BRANCH STATES NO AGE, so it must report none. `answer`'s default is
  // `stated = stale ? age : null`, which on a row with a stale clock and no usable
  // provenance would hand back age 400 beside the words "age unknown": the contract
  // above says `age` is the age the WORDS state, and popupFreshHtml reads it to
  // decide whether the footer's own age would repeat the position's (MR5; before
  // that it was vehicleStaleLine). A non-null age there deleted the one age the
  // popup actually knew, so the pessimistic branch lost information instead of
  // adding it. REVIEW FIX; positions.test.js covers it with a stale clock now,
  // where it only ever passed a fresh one.
  return answer("unknown", "age unknown", { stated: null });
}

// ---- 6.3: every vehicle surface, rendered from the served position ----
//
// WIRED, where commit 1 left the helpers above inert: from here on every vehicle marker,
// popup and name on the map reads what the backend SERVED about its position (provenance
// and observed_at) through positionQualifier, and nothing reads the shape of the fields.
// The helpers below are the pure half, node-testable; systems/shared.js holds the half
// that needs the page (the envelope descriptors and the corrected clock).

// THE BOARD A VEHICLE'S WORDS ARE READ AGAINST, the position form of boardFreshness: the
// corrected clock, the envelope's served_at, whether the row's system dates its positions
// (UNDATED_SYSTEMS, never a system's name), and the served age of the poll behind it,
// which positionQualifier reads only for a retained row with no clock of its own (so a
// retained Metro-North marker states its age exactly as its board row does). `names` are
// the systems the row belongs to: a railroad train's own system, a subway train's feed
// groups, the one synthesized system of a single-feed source. The worst of their poll
// ages answers; a row whose systems the envelope does not name takes the worst of the
// whole source, the pessimistic direction systemFreshnessOf takes for the same miss.
function positionBoard(source, names, now) {
  const src = source || {};
  const systems = sourceSystems(src);
  const wanted = (names || []).filter((name) => name != null);
  const own = wanted.filter((name) => systems[name]);
  const blocks = own.length ? own.map((name) => systems[name]) : Object.values(systems);
  let pollAge = null;
  for (const block of blocks) {
    const age = servedAge(block.fetchedAt, src.servedAt, now);
    if (age != null && (pollAge == null || age > pollAge)) pollAge = age;
  }
  return {
    now,
    servedAt: src.servedAt ?? null,
    system: wanted[0] ?? null,
    gated: !wanted.some((name) => UNDATED_SYSTEMS.has(name)),
    pollAge,
  };
}

// THE AGE A VEHICLE MARKER IS DIMMED BY: the larger of its system's age and its own
// observation's (memo D9), so a marker dims when EITHER is stale. Section 3.2's negative
// half is the reason: "a surface that cannot show the word must not show the
// observation", and a dot with no popup open cannot say "as of 6m ago", so before 6.3 an
// eight minute old LIRR fix inside a healthy feed sat at full opacity beside the live
// ones, which is F01 on the map itself. With neither age a marker has nothing to be dimmed
// by, exactly as before.
//
// AN UNKNOWN OBSERVATION AGE IS NOT "NO AGE" (the 6.3 erratum, MR3 round 4 correcting this
// paragraph). This used to read "an unknown observation age dims nothing on its own, because
// positionQualifier says it in words instead", and the operator's ruling on finding N3 reversed
// exactly that: a row whose family owes a clock and sent none arrives here as AGE_UNKNOWN, not
// as null, and Math.max carries it through, so the marker dims. The words are still said; they
// are no longer said INSTEAD. A row whose family owes no clock (Metro-North) still arrives null
// and still dims nothing, which is the same sentence this paragraph used to make for everyone.
function markerAge(systemAge, observationAge) {
  if (observationAge == null) return systemAge ?? null;
  if (systemAge == null) return observationAge;
  return Math.max(systemAge, observationAge);
}

// THE INSTANT A MARKER'S GLIDE MUST STOP: the earlier of its system's deadline
// (systemStaleAts) and its own observation's (observationStaleAt), so no marker
// dead-reckons from an old fix or an old prediction however healthy its system is. A
// row with no clock keeps its system's deadline alone. glideClock pins the marker there.
function glideDeadline(systemStaleAt, row) {
  const own = observationStaleAt(row);
  if (own == null) return systemStaleAt ?? null;
  if (systemStaleAt == null) return own;
  return Math.min(systemStaleAt, own);
}

// A POSITION DRAWN FROM A PREDICTION: `placed` at the stop a trip update names, or
// `estimated` between two of them (section 3.4's steps 4 and 2). These are the rows a
// client glides along their anchors; a `reported` position is drawn where it was served
// and snaps there, because gliding a real fix would move a measurement.
//
// A `retained` ROW IS DRAWN AS IT WAS DRAWN BEFORE RETENTION, which `before` carries: the
// provenance the page last saw this train served with, remembered by its caller. Retention
// stamps `retained` over every row's provenance (design Q3) and freezes the train where it
// was (C2), so the provenance it wore until then is the only record of how it was drawn.
// Reading `retained` as a fix instead, as the first cut of 6.3 did (memo D9's "retained
// filled and snap", which holds only for a row that was reported), sent every placed or
// estimated railroad train in an outage to its NEXT stop, the coordinates its row carries,
// for the whole retention window: the dead reckoning C2 exists to prevent. A retained row
// the page never saw otherwise (loaded mid-outage) is read as a prediction, which is
// right about where to draw it whichever it was: a reported row carries no anchors and no
// stop (the poller's carry_forward_prev never gives one either), so trainLatLng draws it
// at its served position exactly as a snap would, while a placed one holds still at its
// frozen glide.
function drawnFromPrediction(row, before = null) {
  const provenance = row ? row.provenance : null;
  if (provenance === "retained") return before !== "reported";
  return provenance === "placed" || provenance === "estimated";
}

// THE RAILROAD GLYPH, from the served provenance. Filled is "a position this train
// reported": `reported`, and a `retained` row whose earlier provenance (`before`, as
// drawnFromPrediction reads it) was `reported`. Hollow is everything else: placed and
// estimated, which are schedule-derived; a retained row that was one of those, which
// keeps the glyph it was drawn with; a retained row whose history the page never saw,
// which is read pessimistically (design 3.1), because the filled square is the one glyph
// that claims GPS; and a row whose provenance is missing or unknown, which claims nothing.
// isPlacedRailroad answered this from stop_id, the shape of the fields, and is deleted
// rather than fixed (design 4.3): an estimated row carries a stop_id exactly as a placed
// one does, and a future GPS row that named its stop would have been drawn as a schedule.
function railroadHollow(row, before = null) {
  const provenance = row ? row.provenance : null;
  if (provenance === "retained") return before !== "reported";
  return provenance !== "reported";
}

// IS THIS RAILROAD TRAIN DRAWN ON ITS OWN STATION, where the station's dot is under it?
// The cross-link's gate (the principle is at crossLinkHtml in systems/shared.js), asked
// the way njtAtItsStation asks it for NJ Transit, because the same thing is true here
// now: a `placed` or `estimated` train names the stop it is HEADING for and glides
// toward it, so a stop_id alone put "Also here: Jamaica" on a train drawn between
// stations. That is the link naming a station the vehicle is not at, which the principle
// forbids; NJ Transit's 15c review measured one 8.7 km away. So a train is at its station
// when it names one AND is drawn at its own coordinates, which for a placement are that
// station's: it snaps (reported, unknown, and a retained fix), or it has no anchors to
// glide from (trainLatLng's own fallback, glideAnchored), or its glide has already
// reached the stop (`at`, the clock it is glided by, is at or past next_time, where
// trainLatLng's fraction is 1). Without `at` the last case is not assumed. `before` is
// drawnFromPrediction's: a retained placement is still gliding, frozen, and is at its
// station only where that frozen glide put it.
function railroadAtItsStation(row, at = null, before = null) {
  const t = row || {};
  if (t.stop_id == null) return false;
  if (!drawnFromPrediction(t, before) || !glideAnchored(t)) return true;
  return typeof at === "number" && at >= t.next_time;
}

/* ===== MR3: THE COMMUTER RAIL GRAMMAR =================================================

   Stage 3 of the map redesign draws LIRR, Metro-North and NJ Transit as one family: a
   branch line in the agency's own colour, a paper square for a station, and a two-part tag
   for a train whose body and head say where its position came from. Everything in this
   block is PURE, so the table that decides a mark can be asked one row at a time by a node
   test rather than measured off a screenshot.

   THE ROUTE TABLES ARE NOT WRITTEN DOWN HERE, and that is the point of section 6 of the
   brief: "v2's branch table is not the feed's ... Build from these; do not hand-table." The
   names, the colours and the text colours all arrive from /api/railroad-routes and
   /api/njt-routes. What IS written down is the one thing no feed publishes, the short code
   a 5.6px-per-character block can hold, and it is keyed by NAME rather than by route id
   for a measured reason: the brief found the ids in the v2 table were sample data (there is
   no LIRR route 11; routes 12 and 13 are real and were missing; Metro-North has six routes
   and the table had three), while the names are what the feeds actually serve and what a
   rider reads. An id table would have been wrong on nine of eighteen rows the day it was
   written. */

// THE BRANCH CODES, keyed by the name /api/railroad-routes serves (brief 6.1 and 6.2).
// THIRTEEN LIRR branches and six Metro-North lines, which is what the live feeds carry: the
// brief proposed twelve because it believed there was no route 11, and there is.
const RAIL_BRANCH_CODES = {
  LIRR: {
    "Babylon Branch": "BAB",
    "Hempstead Branch": "HEM",
    "Oyster Bay Branch": "OB",
    "Ronkonkoma Branch": "RON",
    "Montauk Branch": "MTK",
    "Long Beach Branch": "LB",
    "Far Rockaway Branch": "FR",
    "West Hempstead Branch": "WH",
    "Port Washington Branch": "PW",
    "Port Jefferson Branch": "PJ",
    "City Terminal Zone": "CTZ",
    "Greenport Service": "GRN",
    // BELMONT PARK, which the brief said did not exist ("There is no route 11") and which the
    // live feed publishes as route 11 in 60269E. Probed 2026-09-19; the brief carries a dated
    // note saying so rather than having the claim edited away. Code BEL, the operator's.
    "Belmont Park": "BEL",
  },
  MNR: {
    Hudson: "HUD",
    Harlem: "HAR",
    "New Haven": "NH",
    "New Canaan": "NC",
    Danbury: "DAN",
    Waterbury: "WAT",
  },
};

// The neutral a route nothing names or colours falls back to (README: "Unknown route ->
// code = route id, colour #6d6e71"). It takes white ink at 5.10 and dark ink at 3.41, so
// readableTextOn's answer on it clears 4.5 and the other does not, which is why this hue and not
// v2's #607d8b (white 4.37, dark 3.98: nothing clears, and railBranchPaint's comment carries that
// measurement now that the hash palette it used to sit beside is deleted).
// RULING R1 MADE IT LOAD-BEARING ON TWO MORE SURFACES: it is the fallback for the NJ Transit board
// badge and for the panel chip, both of which used to fall back to NJT_FALLBACK_COLOR's #4a4e69.
// (The two ratios above read 4.83 and 4.35 until R1 re-measured them with this file's own
// contrastRatio. The conclusion never moved; the numbers were simply wrong.)
const RAIL_NEUTRAL_COLOR = "#6d6e71";

// The agency glyph and its block width (README: 11px for "L" and "M", 16px for "NJ").
const RAIL_AGENCY = {
  LIRR: { glyph: "L", width: 11 },
  MNR: { glyph: "M", width: 11 },
  NJT: { glyph: "NJ", width: 16 },
};

/* A BRANCH'S SHORT CODE, from whatever the feed gave us, with the id as the last resort.

   Three sources in one order, and the order is the brief's:
     1. NJ Transit's own route_short_name, which that feed publishes ("NEC", "NJCL") and
        which is already exactly this kind of code, so hand-tabling it would be inventing a
        second answer to a question the feed answers.
     2. The name-keyed table above, for LIRR and Metro-North, whose feeds publish no short
        name at all (the LIRR's routes.txt has no route_short_name COLUMN).
     3. The route id, which is the README's stated fallback and is never empty on a drawn
        train.

   A code the tables do not know is NOT an error and must not blank the tag: a branch the
   agency adds tomorrow draws as its id in the neutral colour until someone adds a row. */
function railBranchCode(system, routeId, routeName = null, shortName = null) {
  const short = (shortName ?? "").trim();
  if (short) return short;
  const table = RAIL_BRANCH_CODES[system];
  const named = table && routeName ? table[routeName.trim()] : null;
  if (named) return named;
  return routeId == null ? "" : String(routeId);
}

/* A FEED COLOUR AS A CSS COLOUR. routes.txt publishes hex with NO leading "#" and the
   backend carries it verbatim (that is the whole claim of claude/railroad-route-colors), so
   the "#" is added at the one place the value becomes a paint instruction. A blank or
   missing colour is the neutral, never a guess: three of NJ Transit's routes share one
   yellow and two of the LIRR's share one purple, so a hash of the id would be a colour the
   agency does not use, drawn as if it did. */
/* VALIDATED BEFORE PREFIXING, which njtColor's own guard does and which this dropped: the value
   is a FEED's, it is interpolated into marker markup that reaches innerHTML through L.divIcon,
   and what it replaced (the hash palette ruling R1 deleted) could only ever produce #rrggbb. A publication
   whose route_color is empty, a word, or anything with a quote or an angle bracket in it now
   takes the neutral rather than becoming part of the tag's markup. */
const RAIL_HEX = /^#?[0-9a-fA-F]{6}$/;

function railBranchColor(color) {
  const hex = (color ?? "").trim();
  if (!RAIL_HEX.test(hex)) return RAIL_NEUTRAL_COLOR;
  return hex.startsWith("#") ? hex : `#${hex}`;
}

/* THE BRANCH BLOCK'S PAINT: its fill and the ink printed on it, decided together.

   THE FEED'S OWN INK IS PREFERRED, and both branches are live. Measured on the live feeds:
   LIRR and Metro-North fill route_text_color on every route (13 of 13 and 6 of 6) and NJ
   Transit leaves it EMPTY on all twelve. So the railroads take the agency's chosen ink and
   NJ Transit computes one, and the same tag code serves both. Computing one for a feed that
   supplied it would throw away the agency's own answer; trusting one that is absent would
   print black on a dark line.

   AND ONE PUBLISHED COLOUR TODAY IS A FILL NEITHER INK CAN RESCUE, which is why this
   returns a pair rather than an ink. Measured 2026-09-19 over all 26 colours the three
   feeds publish:

     EE0034, Metro-North's New Haven red, takes white at 4.48 and dark at 3.88.

   Nothing clears 4.5 on it. That colour is shared by FOUR of Metro-North's six routes (New
   Haven, New Canaan, Danbury and Waterbury), and Metro-North's positions are not age-gated,
   so those trains draw with a SOLID body and their code printed on that block: it is the
   common case on that railroad, not a corner. The other 25 colours clear, the worst of them
   at 4.69.

   SO THE FILL MOVES, WHICH IS THIS REPOSITORY'S OWN REMEDY for exactly this class, and this is
   where that argument lives now. It was written against v2's #607d8b, beside the hash palette
   ruling R1 deleted: that fill carried white at 4.37 and dark at 3.98, so NEITHER ink could make it
   readable, which is a fill that has to move rather than an ink that has to be chosen, and it is
   the one case readableTextOn cannot rescue. The move is the same scaling readableInk uses for text, so it preserves the hue,
   and it is taken in 1% steps and stopped at the first that clears: EE0034 becomes #ec0033,
   a two-unit change in one channel, and white goes from 4.48 to 4.55.

   THE ROUTE LINE IS NOT TOUCHED. railBranchColor is what the polyline draws with, and it
   returns the published colour always. Only the 24-by-13 block with 8px type on it moves,
   and only when the type on it would otherwise be illegal, which is the narrowest place to
   pay for this and the only place the shortfall does any harm. A rider comparing the tag to
   the line on the New Haven family sees the same red; a meter does not. */
const RAIL_INK_TARGET = 4.5;

function railBranchPaint(color, textColor = null) {
  const published = railBranchColor(color);
  // The published ink is validated exactly as the colour is, and for the same reason: it is
  // printed into the same markup.
  const given = (textColor ?? "").trim();
  const preferred = RAIL_HEX.test(given)
    ? given.startsWith("#")
      ? given
      : `#${given}`
    : readableTextOn(published);
  if ((contrastRatio(preferred, published) ?? 0) >= RAIL_INK_TARGET) {
    return { fill: published, ink: preferred, moved: false };
  }
  // The feed's ink failed. Try the better of the two computed inks before moving the fill,
  // because an agency that published an unreadable ink over a readable fill is a wrong ink,
  // not a wrong colour.
  const computed = readableTextOn(published);
  if ((contrastRatio(computed, published) ?? 0) >= RAIL_INK_TARGET) {
    return { fill: published, ink: computed, moved: false };
  }
  // Neither ink clears the published fill, so the fill moves, as little as it can. Toward
  // black for white type and toward white for dark type, because those are the directions
  // that help; 1% steps, first that clears.
  const rgb = parseColor(published);
  if (!rgb) return { fill: published, ink: computed, moved: false };
  const toWhite = computed === INK_LIGHT;
  for (let step = 1; step <= 100; step++) {
    const f = step / 100;
    const moved = rgb.map((c) => Math.round(toWhite ? c * (1 - f) : c + (255 - c) * f));
    const hex = `#${moved.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
    if ((contrastRatio(computed, hex) ?? 0) >= RAIL_INK_TARGET) {
      return { fill: hex, ink: computed, moved: true };
    }
  }
  // Unreachable on any real colour (black takes white at 21 and white takes dark at 18.6),
  // and it returns the extreme rather than the unreadable pair if it ever is reached.
  return { fill: toWhite ? "#000000" : "#ffffff", ink: computed, moved: true };
}

// The ink alone, for a caller that has already decided its fill.
function railBranchInk(color, textColor = null) {
  return railBranchPaint(color, textColor).ink;
}

/* THE TAG'S WIDTH, which is arithmetic and therefore testable (README: "Agency block width
   11px (L, M) or 16px (NJ); branch block width code.length x 5.6 + 7").

   Rounded to a whole pixel at the END, once, rather than per block: 5.6 per character is
   not an integer and a tag whose two blocks were each rounded would not be as wide as the
   sum of its parts, which shows up as a one-pixel seam between two filled rects. The
   chevron's centre is the whole tag's centre, so an odd total width is what puts the head
   half a pixel off the line; iconAnchor takes w/2 unrounded for that reason. */
const RAIL_TAG_HEIGHT = 13;
const RAIL_TAG_CODE_PER_CHAR = 5.6;
const RAIL_TAG_CODE_PAD = 7;

function railTagGeometry(system, code) {
  const agency = RAIL_AGENCY[system] ?? RAIL_AGENCY.NJT;
  const codeWidth = Math.round(String(code ?? "").length * RAIL_TAG_CODE_PER_CHAR + RAIL_TAG_CODE_PAD);
  const width = agency.width + codeWidth;
  return {
    glyph: agency.glyph,
    agencyWidth: agency.width,
    codeWidth,
    width,
    height: RAIL_TAG_HEIGHT,
    // The glyph box is 30 tall and the chevron's centre sits at y 21, which is what puts
    // it on the track (README: "iconAnchor: [w/2, 21]").
    centre: width / 2,
  };
}

/* ===== THE STATE TABLE (brief 3.1), AS A FUNCTION, ONE CASE PER ROW ====================

   The deliverable of stage 3. Seven rows, three drawing decisions and the dimming rule:

     reported, unqualified   solid body     filled head      not dimmed
     reported, qualified     solid body     filled head      DIMMED
     estimated               OUTLINED body  FILLED head      not dimmed
     placed                  outlined body  OUTLINED head    not dimmed
     retained                the last state as last state    DIMMED
     unknown, age-gated      outlined body  outlined DOT     (see below)
     unknown, Metro-North    solid body     filled head      not dimmed

   WHAT EACH COLUMN MEANS, in the brief's own words: "solid versus outlined body (position
   from GPS versus from a prediction), and filled versus outlined chevron (heading trusted
   or not). Dimming carries age; the body carries source. A rider should be able to read
   'outlined body, filled chevron' as 'we know where it is going but not exactly where it
   is.'"

   IT IS KEYED ON positionQualifier's `kind`, NOT ON provenance ALONE, because two of the
   seven rows are the same provenance and the app already has a vocabulary that separates
   them: a `reported` fix is row 1 when fresh, row 2 when aged and row 6 when the provider
   sent no clock at all on a system that normally does. That is one function's answer
   (positionQualifier) rather than three re-derivations of it, which is the same discipline
   v3.1 states for the words: "the implementation calls positionQualifier() ... never
   re-derives it. Only the mapping of provenance + age to tag body, chevron and dimming is
   taken from posState()."

   ROW 7 IS ROW 1's CODE PATH, and that is the policy working rather than a special case.
   Metro-North's positions are not age-gated (the freshness contract's 3.3 table: "The
   stamp is a copy of a header that lags 2 to 4 minutes. Gating on it would mark a live
   fleet stale"), so an undated Metro-North fix has kind "" and draws solid and live. The
   node test asks it as its own row anyway, because it is a policy that could be broken by
   a change that left rows 1 through 6 alone.

   THE TABLE'S OPACITY COLUMN IS NOT HERE, and that is round 4's correction rather than an
   omission. This returned a `dim` field for three commits; nothing in the app ever read it.
   Every rail marker's opacity comes from markerOpacity(vehicleMarkerAge(...)) applied to the
   marker itself (railroad.js and njt.js each call dimMarker on the apply path, the stale sweep
   and at creation), so `dim` was a SECOND expression of the freshness contract's rule sitting
   in a table nothing drew from, and it rotted exactly the way an unread field does: the row 6
   paragraph that used to stand here argued at length for dim = false, and the operator's ruling
   on finding N3 made row 6 dim. The column survives as a test column, asserted in
   railtag.test.js against markerOpacity itself, which is stronger than asserting it against a
   copy: the oracle is now the function that actually dims the marker.

   WHAT ROW 6 STILL OWNS HERE IS ITS BODY, and it is the one place this goes past
   railroadHollow, which calls a `reported` row solid whatever its clock says. Clause (c) of the
   contract is an anomaly in the contract's own words ("The provider normally dates this and did
   not. An anomaly, so it is said at the observation"), so a GPS claim that cannot be dated is
   not one to draw solid. Outlined body and a dot head is the strongest "do not trust this" the
   tag can draw, and the dimming now arrives too, through AGE_UNKNOWN and staleAge. */

function railTagState(row, before = null, kind = null) {
  const provenance = row ? row.provenance : null;

  /* THE BODY IS railroadHollow's ANSWER, CALLED rather than restated, which is the whole
     reason this reads as one line. That helper already decides "did this train report this
     position": `reported` and a retained row that was reported are solid, and placed,
     estimated, unknown, missing and a retained row the page never saw are hollow, each for a
     reason its own comment gives. Writing the same rule again here would be a second
     expression of it, and the two would drift the first time either changed.

     PLUS THE ONE EXTENSION ROW 6 ASKS FOR, and it is the only place this goes past that
     helper: a `reported` row with no clock on a system that normally sends one. railroadHollow
     calls that solid, because its question is about provenance and the provenance says GPS.
     The table calls it outlined, and the freshness contract is why: clause (c) is an anomaly
     in the contract's own words, so a GPS claim we cannot date is not a GPS claim we can stand
     behind, which is the same pessimism railroadHollow already applies to a provenance it
     cannot read. */
  const hollow = railroadHollow(row, before) || kind === "unknown";
  const body = hollow ? "outlined" : "solid";

  // A retained row wears what it wore. `before` is drawnFromPrediction's argument: the
  // provenance the train was last SERVED with, because retention stamps over it.
  if (provenance === "retained" || kind === "retained") {
    return {
      body,
      // An estimate's heading was real, so a retained estimate keeps a filled head; a
      // placement's was not. A row with no remembered provenance has no heading to keep.
      head: before === "reported" || before === "estimated" ? "filled" : "outlined",
      headingTrusted: before != null,
      row: "retained",
    };
  }
  if (kind === "estimated") {
    // THE ONE ROW WHERE BODY AND HEAD DISAGREE, and the whole table exists to draw it: the
    // position is inferred and the heading is not.
    return { body, head: "filled", headingTrusted: true, row: "estimated" };
  }
  if (kind === "placed") {
    return { body, head: "outlined", headingTrusted: true, row: "placed" };
  }
  if (kind === "unknown") {
    return { body, head: "outlined", headingTrusted: false, row: "unknown" };
  }
  // kind "" (fresh) and "aged" (stale): a reported fix, solid and headed. Its opacity is its
  // age's and markerOpacity's, not this table's. Row 7 arrives here too, on a system whose
  // positions are not age-gated at all, which is why an undated fix reaches kind "".
  return {
    body,
    head: "filled",
    headingTrusted: true,
    row: kind === "aged" ? "reported-qualified" : "reported-unqualified",
  };
}

/* ===== THE TAG'S MARKUP, AS A STRING ==================================================

   The SVG a rail train wears, built here rather than in a system file so node can ask for
   it. That split is the whole reason this is a string and not an L.divIcon: the divIcon
   wrapper is four lines in systems/shared.js and needs a browser, while the markup is where
   every decision the state table made becomes visible, and a browser test that has to find
   a shape by screenshot is a test nobody will keep.

   THE BOX IS 30 TALL AND THE TRACK IS AT y 21 (README): the tag occupies y 0 to 13, a 1px
   stem runs from 13 to 16.5, and the head is centred on 21, which is what iconAnchor's
   [w/2, 21] puts on the line. So the tag hangs ABOVE the rail and the head sits ON it, and
   a rider reads the pair as one mark pointing somewhere.

   PAPER AND INK ARE THE THEME'S, as inline `style` rather than as SVG attributes, which is
   what MR2's subway bullet already does (systems/subway.js:30).

   AND THE REASON IS THE CASCADE, NOT A RESOLUTION FAILURE, which is a correction MR4 measured:
   the sentence here used to say a `fill="var(--paper)"` attribute "is not a paint value in SVG
   1.1 and does not resolve, so the marker would draw black". That is not what Chromium does.
   A presentation attribute is mapped into the cascade as a declaration, so the token resolves:
   measured on this branch, `stroke="var(--paper)"` computes to rgb(243, 242, 242), byte for
   byte what the style form computes to, and ledger finding H2 measured the same thing in MR2
   and wrote it down correctly ("works in a Chromium presentation attribute"). What is true is
   weaker and still decisive: a presentation attribute sits at the BOTTOM of the cascade, so any
   stylesheet rule beats it and a later `.rail-tag rect { fill: ... }` would silently win over
   the mark's own paint, and the style form also works in a browser that maps no attributes at
   all. An UNKNOWN token is where black comes from: `var(--nope)` falls back to the property's
   initial value, which is black for fill and none for stroke, in either form.

   The BRANCH ink is a literal instead, and deliberately: it
   depends on the branch colour, not on the theme, and it comes either from the feed's own
   route_text_color or from readableTextOn. A token there would have made a rider's ink
   follow the page's theme instead of the line it is printed on.

   THE SVG IS aria-hidden AND ITS INK IS MEASURED ELSEWHERE, which is MR2's ruling for the
   station labels applied to the one other mark that carries type. Two reasons, and the second
   is the one that made it necessary rather than merely correct:

     labeledMarker already puts the whole train's accessible name on the marker div as
       role="img" plus aria-label, so the two glyphs inside are a PICTURE of the branch code, a
       second copy of information a screen reader has already been given better;
     axe cannot resolve this text's background. Measured: with the tags on the map its
       color-contrast rule reported 37 findings at 1280 and 17 at 375, every one of them
       "Element's background color could not be determined because it is overlapped by another
       element". A rail tag is 35 to 45px wide where the old square was 16, so at regional zoom
       the tags overlap each other, and an overlapped node is one axe declines to judge rather
       than one it fails. Thirty-seven undecidable findings is not a measurement, and A1w's own
       rule is that an undecidable finding is never an exception on its own.

   SO THE RATIO IS MEASURED DIRECTLY, in node, over every colour the three feeds publish, which
   is a stronger check than axe could have made here: railtag.test.js asserts the ink actually
   printed against the fill actually painted for all 31 published pairs, and it is what found
   that eight of them do not clear 4.5 as published. ACCESSIBILITY.md carries both statements,
   as it does for the labels.

   WHY THE TEXT IS NOT CENTRED WITH text-anchor ALONE. It is, but the y is the baseline and
   Archivo 800 at 8px sits about 2.8px above it, so the constant below is measured from the
   block rather than derived: 8.9 is the baseline that puts the cap-height's centre on the
   13px block's centre. */
const RAIL_TAG_TEXT_BASELINE = 8.9;
const RAIL_TAG_BOX_HEIGHT = 30;
const RAIL_TAG_TRACK_Y = 21;
const RAIL_TAG_STEM_END = 16.5;
const RAIL_TAG_STRIPE = 2.5; // the branch colour's stripe on an outlined body
const RAIL_TAG_DOT_R = 3;

/* THE PER-FAMILY CLASS, decided in one place. One builder draws three agencies, which is the
   claim of this stage, and that makes the family class the only way anything downstream can
   still address ONE of them: style.css to give an agency its own rule, and the e2e suite to
   count the two railroads apart from NJ Transit, which it must, because they are two feeds
   with two freshness states and several specs turn on exactly that difference.

   LOWERCASED, because a class is a css identifier and `LIRR` beside `lirr` in two files is a
   bug that only shows up in one of them. */
function railFamilyClass(system) {
  return `rail-${String(system ?? "").toLowerCase() || "unknown"}`;
}

// The chevron, pointing north before rotation (README's path, written from the centre so
// one expression serves any tag width).
function railTagChevronPath(cx) {
  const n = (v) => Number(v.toFixed(2));
  return `M ${n(cx)} 15.5 L ${n(cx + 5)} 24.5 L ${n(cx)} 22 L ${n(cx - 5)} 24.5 Z`;
}

/* THE WHOLE MARK. `state` is railTagState's answer, `bearing` is railTrainBearing's (null
   when nothing on the row can say), and the two together decide the head: a chevron rotated
   to the bearing when the state trusts a heading AND there is one, a dot otherwise.

   A DOT IS NOT A FALLBACK, IT IS A STATEMENT, which is why both ways of reaching it draw
   the same shape: the row that has no heading to give and the row whose heading the table
   refuses to trust (kind "unknown") are both "we are not telling you which way this is
   going", and drawing a chevron at an arbitrary angle for either would be worse than
   drawing none. */
/* THE TAG'S TYPE, ON THE TEXT ELEMENTS THEMSELVES, and MR5 is the stage that had to put it there.

   It was `.rail-tag-marker svg text` in the stylesheet, which drew correctly for as long as every
   tag this builder made was inside a rail tag MARKER. Section 5 draws a popup's title with the map's
   own mark, so the same string is now also rendered inside `.pt`, where that selector does not
   match and the type inherits the title's `font: 800 17px`. Measured in the browser: the map's tag
   text computes to `800 8px` and the popup's to `800 17px`, with 17px glyphs drawn inside a
   13-unit block and clipped by the viewport. The shipped captures show it as a smear.

   THE LESSON WAS ALREADY IN THE LEDGER, one surface away: mutation M60 dropped the inline font from
   the KEY panel's hand-written copies "on the theory that a stylesheet supplies it" and died, with
   the note "it does not: `.rail-tag-marker svg text` is scoped to the MARKER". A mark that travels
   has to carry its own type, which is what `subwayPlateSvg` has always done (font-size, font-weight
   and font-family as presentation attributes) and what the Key's copies do.

   PRESENTATION ATTRIBUTES RATHER THAN AN INLINE STYLE, for the reason the fills next to them are
   inline styles: a style attribute would beat the theme's `var()` fills nothing here needs to beat,
   while a presentation attribute is the lowest-priority author form and can still be overridden by
   a stylesheet if a later surface ever needs to. */
const RAIL_TAG_TYPE = ' font-family="Archivo, system-ui, sans-serif" font-size="8" font-weight="800" letter-spacing="0.01"';

function railTagSvg({ system, code, color, textColor = null, state, bearing = null } = {}) {
  const geom = railTagGeometry(system, code);
  const { width: w, agencyWidth: aw, codeWidth: cw, glyph, centre: cx } = geom;
  // THE BLOCK'S paint, which may have moved off the published colour so its 8px type is
  // legible (railBranchPaint says which colour and why). The STRIPE on an outlined body
  // carries no type, so it takes the published colour: nothing is printed on it to rescue.
  const paint = railBranchPaint(color, textColor);
  const branch = paint.fill;
  const branchInk = paint.ink;
  const stripe = railBranchColor(color);
  const solid = state.body === "solid";
  const headFilled = state.head === "filled";
  const showChevron = state.headingTrusted && bearing != null;

  const body = solid
    ? // A paper backing 1px larger at 0.9, so the tag reads against any tile, then the two
      // blocks: the agency in ink with a paper glyph, the branch in its own colour.
      `<rect x="-0.5" y="-0.5" width="${w + 1}" height="14" style="fill: var(--paper)" opacity="0.9"/>` +
      `<rect x="0" y="0" width="${aw}" height="${RAIL_TAG_HEIGHT}" style="fill: var(--ink)"/>` +
      `<rect x="${aw}" y="0" width="${cw}" height="${RAIL_TAG_HEIGHT}" fill="${branch}"/>` +
      `<text x="${aw / 2}" y="${RAIL_TAG_TEXT_BASELINE}" text-anchor="middle"${RAIL_TAG_TYPE} style="fill: var(--paper)">${esc(glyph)}</text>` +
      `<text x="${aw + cw / 2}" y="${RAIL_TAG_TEXT_BASELINE}" text-anchor="middle"${RAIL_TAG_TYPE} fill="${branchInk}">${esc(code)}</text>`
    : // Outlined: one paper box in an ink stroke, a divider at the block edge, both texts in
      // ink, and the branch colour reduced to a stripe along the bottom of its own block.
      // The stroke is 1.2 and centred on the path, so the box is inset 0.6 to stay inside
      // the 13px it is allowed.
      `<rect x="0.6" y="0.6" width="${w - 1.2}" height="${RAIL_TAG_HEIGHT - 1.2}" style="fill: var(--paper); stroke: var(--ink)" stroke-width="1.2"/>` +
      `<line x1="${aw}" y1="0.6" x2="${aw}" y2="${RAIL_TAG_HEIGHT - 0.6}" style="stroke: var(--ink)" stroke-width="1.2"/>` +
      `<rect x="${aw + 0.6}" y="${RAIL_TAG_HEIGHT - 0.6 - RAIL_TAG_STRIPE}" width="${cw - 1.2}" height="${RAIL_TAG_STRIPE}" fill="${stripe}"/>` +
      `<text x="${aw / 2}" y="${RAIL_TAG_TEXT_BASELINE}" text-anchor="middle"${RAIL_TAG_TYPE} style="fill: var(--ink)">${esc(glyph)}</text>` +
      `<text x="${aw + cw / 2}" y="${RAIL_TAG_TEXT_BASELINE}" text-anchor="middle"${RAIL_TAG_TYPE} style="fill: var(--ink)">${esc(code)}</text>`;

  const stem =
    `<line x1="${cx}" y1="${RAIL_TAG_HEIGHT}" x2="${cx}" y2="${RAIL_TAG_STEM_END}"` +
    ` style="stroke: var(--ink)" stroke-width="1"/>`;

  // Filled: ink with a 1px paper stroke, so it reads on a dark tile. Outlined: paper with a
  // 1.4px ink stroke, which is the heavier stroke the README asks for because a hollow shape
  // this small needs more edge to stay a shape.
  const headPaint = headFilled
    ? `style="fill: var(--ink); stroke: var(--paper)" stroke-width="1"`
    : `style="fill: var(--paper); stroke: var(--ink)" stroke-width="1.4"`;
  const head = showChevron
    ? `<path d="${railTagChevronPath(cx)}" ${headPaint} transform="rotate(${Number(bearing.toFixed(1))} ${cx} ${RAIL_TAG_TRACK_Y})"/>`
    : `<circle cx="${cx}" cy="${RAIL_TAG_TRACK_Y}" r="${RAIL_TAG_DOT_R}" ${headPaint}/>`;

  return (
    `<svg viewBox="0 0 ${w} ${RAIL_TAG_BOX_HEIGHT}" width="${w}" height="${RAIL_TAG_BOX_HEIGHT}"` +
    ` class="rail-tag rail-tag-${state.body} rail-head-${state.head}" aria-hidden="true"` +
    ` focusable="false">${body}${stem}${head}</svg>`
  );
}

/* THE RAIL STATION SQUARE (README: "squares, so a square always means regional rail and a
   circle always means subway"). 10x10 box, an 8x8 paper rect in a 1.6 ink stroke, and the
   20x20 hit target is the icon's SIZE while the drawn square stays 10: a divIcon's size is
   its click box, so growing the box and centring the square inside it is how a 10px mark
   gets a 20px target without becoming a 20px blob at city zoom.

   A STRING HERE FOR THE SAME REASON THE TAG IS, and one shape for all three families, which
   is what makes "a square always means regional rail" a claim a test can count rather than a
   sentence in a design note. */
const RAIL_STATION_BOX = 20;
const RAIL_STATION_SQUARE = 10;

function railStationSvg() {
  const pad = (RAIL_STATION_BOX - RAIL_STATION_SQUARE) / 2;
  return (
    `<svg viewBox="0 0 ${RAIL_STATION_BOX} ${RAIL_STATION_BOX}" width="${RAIL_STATION_BOX}"` +
    ` height="${RAIL_STATION_BOX}" class="rail-stn" aria-hidden="true" focusable="false">` +
    `<rect x="${pad + 1}" y="${pad + 1}" width="8" height="8" style="fill: var(--paper); stroke: var(--ink)" stroke-width="1.6"/>` +
    `</svg>`
  );
}

/* ===== MR4: THE OTHER FOUR FAMILIES' MARKS, AS STRINGS ================================

   PATH's diamond, the ferry's hull, the bus arrow and the bus dot. Here rather than in the
   system files for the reason the rail tag is here: helpers.js is loaded by node with no `L`
   and no document, so a builder that returns a STRING can be asked one state at a time by a
   node test, and only the L.divIcon wrapper needs a browser. MR3's mutation table is the
   argument rather than the taste: M1, M2, M5 and M19 were all killed by node tests reading
   railTagSvg's markup directly, and none of them would have had anything to read if the
   markup had stayed inside a Leaflet call.

   EVERY STROKE IS `style="stroke: var(--paper)"`, NOT A LITERAL, and that is the whole
   mechanism of this stage's theme swap. A divIcon is HTML, so a custom property in an inline
   STYLE resolves through the cascade and follows a theme change at no cost: these four marks
   need no rebuild and no restyle sweep, they simply are the right colour. A canvas mark
   cannot do that (Leaflet hands a colour STRING to the 2D context and `var(--paper)` is not
   one), which is why the lines and the station circles are a registry and these are not.

   AND `style=` RATHER THAN THE `stroke=` ATTRIBUTE, on the cascade's grounds rather than on a
   resolution failure. MR4 measured what MR3 and this comment both used to assert: a
   presentation attribute DOES resolve a custom property in Chromium, and
   `stroke="var(--paper)"` computes to the same rgb the style form does. Ledger finding H2 had
   it right in MR2 and the sentence got stronger each time it was copied. The real reason to
   prefer the style form is that a presentation attribute is the lowest-priority author
   declaration there is, so any stylesheet rule beats it silently; the argument is written out
   above railTagSvg and is not repeated per builder. The rule is still enforced by
   frontend/families.test.js, on those grounds. */

/* THE MUTED HASHED HUE (README: "Route colour is the existing hashed hue but muted:
   hsl(h, 45%, 38%)"), AND ITS LIGHTNESS IS A TOKEN BECAUSE A DARK MAP NEEDS THE OTHER END.

   THE HUE IS routeColor's, UNCHANGED, and only the saturation and lightness move. That is
   what "the existing hashed hue" means and it is the property that matters: two buses on one
   route are one colour and two routes are two, whichever of the two functions asks.

   A SECOND FUNCTION RATHER THAN A CHANGE TO routeColor, because routeColor is ALSO what the
   bus POPUP prints its route name in (systems/buses.js) and the popups are stage MR5's,
   pinned byte for byte by P1g. Muting routeColor itself would have moved a popup this stage
   is not allowed to touch, which is exactly the kind of quiet reach the pins exist to catch.

   WHY THE LIGHTNESS IS `var(--bus-mark-lightness)` AND NOT THE README'S 38%, MEASURED. A bus
   route's colour is a HASH of its id, so "is this route's arrow legible" is not a question
   about one colour, it is a question about all 360 hues. Against the light theme's paper
   (#f3f2f2) the README's 38% is exactly right: every hue clears the 3:1 a mark owes, worst
   3.16, where the 75%/40% it replaces leaves 147 of 360 under (worst 2.01). Against the DARK
   theme's paper (#201e1d) the same 38% leaves 188 of 360 under 3:1, worst 1.62, because a
   mid-dark fill on a dark surface is the G15 arithmetic all over again. And no single
   lightness fixes both: 38% is perfect in light and worst in dark, 60% is perfect in dark
   (worst 3.60 on --paper, 3.05 on --surface, zero hues under either) and leaves 219 of 360
   under in light. The two ends are what the theme is for.

   SO THE LIGHTNESS IS THE TOKEN AND THE HUE STAYS THE ROUTE'S. A custom property is
   substituted before the value is parsed, so `hsl(329, 45%, var(--bus-mark-lightness))` is a
   real colour in either theme and follows a swap through the cascade with NO rebuild, exactly
   as the `var(--paper)` stroke beside it does. The README's 38% is unchanged: it is what
   `--bus-mark-lightness` resolves to in the light theme, and it is written here as the var's
   fallback so a context with no stylesheet (node, boards.test.js) still gets a real colour.

   busMarkColorAt IS THE SAME FUNCTION WITH THE TOKEN RESOLVED, and it exists so the
   measurement above can be RUN rather than quoted: frontend/families.test.js sweeps all 360
   hues at both ends against both papers. Nothing in the app calls it. */
const BUS_MARK_SATURATION = 45;
const BUS_MARK_LIGHTNESS = 38;
const BUS_MARK_LIGHTNESS_DARK = 60;
const BUS_MARK_LIGHTNESS_TOKEN = `var(--bus-mark-lightness, ${BUS_MARK_LIGHTNESS}%)`;

function busMarkHue(routeId) {
  const hue = /^hsl\((\d+),/.exec(routeColor(routeId));
  return hue ? Number(hue[1]) : null;
}

function busMarkColor(routeId) {
  const hue = busMarkHue(routeId);
  // A route with no id gets routeColor's flat grey, which has no hue to mute.
  return hue == null
    ? routeColor(routeId)
    : `hsl(${hue}, ${BUS_MARK_SATURATION}%, ${BUS_MARK_LIGHTNESS_TOKEN})`;
}

/* The same colour with the token resolved, for the two callers that cannot read a custom
   property: the measurement, and the CANVAS. A clicked bus's route line is a polyline, so
   Leaflet hands its colour to the 2D context as a STRING and `var(--bus-mark-lightness)` is not
   one; the mark beside it is HTML and takes the token directly. Round 1 is why this has a
   second caller at all: the stage tokenised the mark's lightness and left the route line at
   routeColor's raw wheel, so a rider clicking a bus got a line in a different colour from the
   arrow they clicked, and in the dark theme 169 of the 360 hashed hues read under 3:1 against
   the paper (worst 1.45), 217 of them once the line's own 0.65 opacity is composited. */
function busMarkColorAt(routeId, lightness) {
  const hue = busMarkHue(routeId);
  return hue == null ? routeColor(routeId) : `hsl(${hue}, ${BUS_MARK_SATURATION}%, ${lightness}%)`;
}

/* THE AIRTRAIN GUIDEWAY'S LINE, as options, for ferryDockStyle's reason: a polyline has no
   element either, and the draw and the theme repaint must be one expression. The gray is the
   caller's, resolved from `--scheduled` at draw time, because it has two values and a canvas
   can read neither. */
const AIRTRAIN_LINE_DASH = "8 5";
const AIRTRAIN_LINE_WEIGHT = 3;

function airtrainLineStyle(gray) {
  return { color: gray, weight: AIRTRAIN_LINE_WEIGHT, opacity: 0.9, dashArray: AIRTRAIN_LINE_DASH };
}

/* THE FERRY DOCK'S CIRCLE, as options rather than as markup, because a dock is a canvas
   circleMarker and has no element: its identity IS the options its renderer draws from. Pure
   and here so the one expression of "what a dock looks like" serves the draw, the theme
   repaint and a node test alike, which is what stops the drawn dot and the repainted dot
   drifting apart.

   THE FILL IS THE DESIGN'S #00839c, which is also the colour the feed strip's ferry tick has
   drawn since MR1. The STROKE is the caller's paper, resolved at draw time, because a canvas
   context cannot read a custom property. */
const FERRY_DOCK_COLOR = "#00839c";
const FERRY_DOCK_RADIUS = 4;
const FERRY_DOCK_STROKE = 1.5;

function ferryDockStyle(paper) {
  return {
    radius: FERRY_DOCK_RADIUS,
    fillColor: FERRY_DOCK_COLOR,
    fillOpacity: 1,
    color: paper,
    weight: FERRY_DOCK_STROKE,
    stroke: true,
  };
}

// PATH's diamond (README: 16x16, route fill, paper stroke 1.2). The vertices sit on the box
// edges and the 1.2 stroke spreads 0.6 either side, so the drawn mark reaches 0.4 to 15.6
// and nothing is clipped by the viewBox.
const PATH_DIAMOND_BOX = 16;
const PATH_DIAMOND_PATH = "M8 1 L15 8 L8 15 L1 8 Z";

function pathDiamondSvg(color) {
  return (
    `<svg viewBox="0 0 ${PATH_DIAMOND_BOX} ${PATH_DIAMOND_BOX}" class="path-diamond"` +
    ` aria-hidden="true" focusable="false">` +
    `<path d="${PATH_DIAMOND_PATH}" style="fill: ${color}; stroke: var(--paper)" stroke-width="1.2"/>` +
    `</svg>`
  );
}

/* The ferry's hull (README: 22x14, `M1 3 H21 L17.5 11 H4.5 Z`, route fill, paper stroke 1).

   A HULL AND NOT A ROUNDED RECT, which is the point of the shape. The file this replaces
   already argued that a boat should read as a boat beside a subway square, a railroad square,
   a PATH diamond and a bus arrow, and then drew a rounded rectangle; the design's path is a
   trapezoid with a flat deck and a tapered bottom, which is the argument actually carried
   out. */
const FERRY_HULL_BOX = [22, 14];
const FERRY_HULL_PATH = "M1 3 H21 L17.5 11 H4.5 Z";

/* THE HULL'S INK EDGE, AND WHY IT IS TWO STROKES ON ONE PATH (MR4 ruling Q1, paid in MR5).

   A boat is filled with the colour NYC Ferry publishes for its route, and the app does not
   move a published fill. Measured on the drawn page, that left exactly one paint on this map
   under the 3:1 a mark owes: South Brooklyn's #ffd100 at 1.31 against the light paper. The
   hull's only other paint was the paper casing, which cannot raise a fill's ratio against
   paper because it IS approximately the paper. So the ruling's answer is a third paint that
   the app DOES choose: an ink edge, inside the casing.

   TWO STROKES ON THE SAME `d`, WIDER FIRST. A stroke is centred on its path, so a single
   stroke cannot be both the casing and the edge. The paper goes to 2 (1 out, 1 in) and the
   ink follows at 0.8 on the same geometry, drawn second so it sits on the boundary with a
   full pixel of paper still outside it. Reading outward a rider gets: the route's published
   fill, the ink edge that finds it, the paper casing that separates it from the tile. The
   drawn mark grows half a pixel on each side and stays inside its 22x14 box.

   WHAT IT COSTS, named because it is a marker change in a popup stage: `markers/ferry` and
   `contrast/marks` both move, `theme.spec.js` D5d's exemption assertion INVERTS by design,
   and the captures that show a boat are regenerated. All four are in this stage's ledger
   entry. What it does not touch is the fill: `color` is still the feed's, unchanged. */
function ferryHullSvg(color) {
  return (
    `<svg viewBox="0 0 ${FERRY_HULL_BOX[0]} ${FERRY_HULL_BOX[1]}" class="ferry-hull"` +
    ` aria-hidden="true" focusable="false">` +
    `<path d="${FERRY_HULL_PATH}" style="fill: ${color}; stroke: var(--paper)" stroke-width="2"/>` +
    `<path d="${FERRY_HULL_PATH}" style="fill: none; stroke: var(--ink)" stroke-width="0.8"/>` +
    `</svg>`
  );
}

/* THE SUBWAY'S PLATE, which is the oldest mark in this app and the last one to become a string
   builder. It moved here from trainIcon in systems/subway.js in MR5, unchanged to the byte
   (`pins.spec.js` P1f pins the markup and did not move), for the reason the other five are here:
   a popup title draws its family's mark, helpers.js is where a mark that is not tied to Leaflet
   lives, and a popup that drew its own 18x18 rounded square would be a second answer to "what
   does a subway train look like".

   THE LABEL IS VALIDATED HERE AND NOT BY THE CALLER, which is what moving it buys: the plate
   holds one to three characters and a feed that served a route id of twenty would otherwise have
   drawn a plate with a sentence in it. trainIcon did that test inline and the popup would have
   had to do it again.

   THE TYPE SHRINKS FOR TWO CHARACTERS AND MORE (10.5 to 8.5), which is why the test is on the
   validated label rather than on the served route: "?" takes the wide size, because it is one
   character. */
function subwayPlateSvg(route, color, textColor) {
  const label = /^[A-Za-z0-9]{1,3}$/.test(route ?? "") ? String(route) : "?";
  return `<svg viewBox="0 0 18 18">
      <rect x="0" y="0" width="18" height="18" rx="4" style="fill: var(--paper)" opacity="0.95"/>
      <rect x="1.5" y="1.5" width="15" height="15" rx="3" fill="${color}"/>
      <text x="9" y="9.5" text-anchor="middle" dominant-baseline="central"
            font-size="${label.length > 1 ? 8.5 : 10.5}" font-weight="800"
            font-family="Archivo, system-ui, sans-serif" fill="${textColor}">${esc(label)}</text>
    </svg>`;
}

/* The bus mark, which is TWO marks and one box (README: "Heading known: 14x14 arrow
   `M7 1 L12 13 L7 10 L2 13 Z` rotated to bearing, fill = route colour, paper stroke 0.8.
   Heading unknown: 12x12 dot `r 3.5`, paper stroke 1").

   ONE 14x14 BOX FOR BOTH STATES, although the design gives the dot a 12x12 one. The DRAWN
   dot is r 3.5 either way, so the box size changes nothing a rider sees; what it would change
   is the marker's iconSize, and a marker whose box grows and shrinks as its feed gains and
   loses a heading is a marker whose anchor moves under the rider's pointer between polls. The
   two states already swap through one re-skin gate in systems/buses.js and keeping the box
   constant is what lets that gate stay a swap rather than a reposition.

   THE ROTATION IS ON THE `<svg>`, not on the path, and that is load-bearing: systems/buses.js
   animates a heading change by writing `svg.style.transform` on the existing element rather
   than re-iconing, so the CSS transition can run. A transform on an inner path would leave
   that write pointing at the wrong node and the arrow would snap.

   GTFS bearing is degrees clockwise from north and the arrow points north before rotation,
   which is what makes the rotation a straight pass-through with no offset. */
const BUS_MARK_BOX = 14;
const BUS_ARROW_PATH = "M7 1 L12 13 L7 10 L2 13 Z";
const BUS_DOT_R = 3.5;

function busMarkSvg(color, bearing = null) {
  const open =
    `<svg viewBox="0 0 ${BUS_MARK_BOX} ${BUS_MARK_BOX}" class="bus-mark" aria-hidden="true" focusable="false"`;
  if (Number.isFinite(bearing)) {
    const deg = ((Number(bearing) % 360) + 360) % 360;
    return (
      `${open} style="transform: rotate(${deg}deg)">` +
      `<path d="${BUS_ARROW_PATH}" style="fill: ${color}; stroke: var(--paper)" stroke-width="0.8"/>` +
      `</svg>`
    );
  }
  const c = BUS_MARK_BOX / 2;
  return (
    `${open}>` +
    `<circle cx="${c}" cy="${c}" r="${BUS_DOT_R}" style="fill: ${color}; stroke: var(--paper)" stroke-width="1"/>` +
    `</svg>`
  );
}

/* AND WHICH OF THE TWO A ROW EARNS, as its own predicate so the question "is this bus
   pointed anywhere" has one answer. A bearing is a number or it is nothing: a served null, an
   absent field and a NaN are all "we are not telling you which way this is going", and the
   dot says so. The mutation this kills is the arrow drawn when no heading is served, which
   would be a direction invented out of a missing field. */
function busHasHeading(bus) {
  return Number.isFinite(bus && bus.bearing);
}

/* ===== BEARING ALONG THE BRANCH =======================================================

   The heading the chevron is rotated to: the direction of travel along the branch the
   train is on, at the point it is drawn.

   IT REUSES THE PROJECTION THE PAGE ALREADY DID rather than doing a second one. Every
   gliding rail train already carries `_route`, the slice computeRouteSlice built for it
   (its points, its cumulative lengths and the polyline it was taken from), and that slice
   is by construction oriented prev-station-to-next-station: computeRouteSlice walks from
   the projection of `prev` to the projection of `next`. So the slice's own direction IS
   the direction of travel, and the bearing is the azimuth of the segment the train sits
   on. A second projectOntoRoute call here would be a second answer to a question already
   answered, and the two would disagree at exactly the places that matter, where two
   branches share track.

   AND IT TAKES THE SERVED DIRECTION, NOT A HEADSIGN. v3.1 overrules v2 on this in as many
   words: "NJ Transit heading comes from the served direction passed to bearingAlong; there
   is no headsign rule." v2 had `headsign === "New York"` as inbound, which is a string
   match on a rider-facing label: the same trip reads "New York Penn Station" on NJ Transit
   and "Penn Station" on the LIRR, an added trip's headsign is synthesized from the route
   and the train number, and a branch that terminates short of the city has no such
   headsign at all. What the railroads DO serve is `direction`, "Inbound" or "Outbound"
   from direction_id (backend/feeds/railroad.py's _RAILROAD_DIRECTION), and what NJ Transit
   serves is the anchor pair itself, prev and next, which is a direction of travel without
   needing a word for it. So:

     a slice, which is already prev-to-next, gives the heading directly;
     `direction` is read only to REVERSE it, and only when the row says "Inbound",
       because a branch polyline is stored in one direction and an inbound train runs
       against it;
     no slice and no anchors is no heading at all, which the tag draws as a dot.

   A GPS train is the one case with a better answer than any of this, and it is used when
   the feed gives it: RailroadTrain carries `bearing` from the vehicle itself. Reading a
   polyline for a train that just told us where it is pointing would be re-deriving a
   served fact, which is the mistake 6.3 exists to stop. */

// The compass azimuth from one [lat, lon] to another, in degrees clockwise from north.
// Flat-earth over a segment a few hundred metres long, with the longitude scaled by the
// latitude's cosine so a segment does not read as more east-west than it is.
function segmentBearing(from, to) {
  if (!Array.isArray(from) || !Array.isArray(to)) return null;
  const [lat1, lon1] = from;
  const [lat2, lon2] = to;
  if (![lat1, lon1, lat2, lon2].every((n) => Number.isFinite(n))) return null;
  const dy = lat2 - lat1;
  const dx = (lon2 - lon1) * Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  if (dx === 0 && dy === 0) return null; // a degenerate segment points nowhere
  const deg = (Math.atan2(dx, dy) * 180) / Math.PI;
  return (deg + 360) % 360;
}

/* NOTHING IS REVERSED, AND railDirectionReverses IS GONE (the operator's ruling on the
   adversarial round's R-a). It read: "Inbound is the only value that reverses ... a slice built
   prev-to-next is already travelling the way the train is and only a polyline stored
   city-outward needs flipping." The first half of that sentence was right and refuted the
   second: BOTH geometry sources below are travel-directed by construction, so there was
   nothing left for `direction` to correct and applying it turned correct headings around.

   MEASURED, on the F01 capture's own rows rather than on a hypothetical: GO201_26_6187_1 is an
   Inbound estimated LIRR train whose anchors run from [40.65746, -73.58232] to [40.69961,
   -73.80853], a true heading of 283.8 degrees, and the reversal drew it at 103.8. Three such
   rows are in every capture. The slice branch was wrong a second way and is fixed below.

   THE SERVED `bearing` IS STILL NEVER REVERSED, which was always right: a bearing the vehicle
   sent is already the way it points, and turning it around would turn the train around. */

/* THE HEADING FOR ONE TRAIN, or null when nothing on the row can say.

   Three sources, first that answers:
     1. the feed's own `bearing`, for a GPS train that sent one;
     2. the SLICE INTERVAL the glide is already riding, s0 to s1;
     3. the served anchors, prev station to drawn position.
   Nothing is reversed. Sources 2 and 3 are both directions of travel already; see the note
   above railFamilyClass's neighbours for what reversing them cost.

   SOURCE 2 READS s0 AND s1, NOT points[0] AND points[last], and that distinction is the whole
   defect the adversarial round found. computeRouteSlice returns `{points: poly.points, cum:
   poly.cum, s0, s1}`: `points` is the WHOLE stored branch polyline and the train's travel is
   the INTERVAL s0 to s1, which is exactly what trainLatLng interpolates (`s0 + (s1 - s0) * f`).
   Reading the endpoints instead gave the branch's end-to-end chord: one heading for every
   train on that branch, the same whichever way it was going and wherever it was. Measured: on
   a branch stored west to east it agreed with the truth, and on one stored east to west it was
   180 degrees out, so which trains were right depended on how the MTA happened to store each
   shape. A slice whose two offsets coincide (a train sitting at its previous station) yields no
   direction and falls through to the anchors rather than to a fabricated one. */
function railTrainBearing(row) {
  const t = row || {};
  if (Number.isFinite(t.bearing)) return ((t.bearing % 360) + 360) % 360;
  const slice = t._route;
  if (
    slice &&
    Array.isArray(slice.points) &&
    slice.points.length >= 2 &&
    Number.isFinite(slice.s0) &&
    Number.isFinite(slice.s1)
  ) {
    const from = pointAtArcLength(slice.points, slice.cum, slice.s0);
    const to = pointAtArcLength(slice.points, slice.cum, slice.s1);
    const along = segmentBearing(from, to);
    if (along != null) return along;
  }
  if (Number.isFinite(t.prev_lat) && Number.isFinite(t.prev_lon)) {
    return segmentBearing([t.prev_lat, t.prev_lon], [t.latitude, t.longitude]);
  }
  return null;
}

// WHY A RAILROAD TRAIN LEFT THE MAP, when the served data can say: "withheld" for a fix
// the position ladder stopped drawing for its age (section 3.4's step 5), else null. The
// vanishing-focus rescue speaks it (vanishingFocusMessage), because "left the feed" is
// false of a train the feed still carries and the status line is at that moment counting
// as "not shown, last seen over 10m ago": the page must not say one thing and speak
// another. The client cannot see which trips the count holds, so this asks of the
// departing row only what makes the rescue's words TRUE: it was the train's own fix
// (`reported`), on an age-gated system, now older than OBS_MAX_S at the envelope that
// dropped it (servedAge from that envelope's served_at), in a system whose block counts
// trains withheld (withheldTrains). A placed or estimated row is dated by its prediction,
// not its fix, so it keeps the general sentence.
function withheldFix(row, block, servedAt, now) {
  if (!row || row.provenance !== "reported" || UNDATED_SYSTEMS.has(row.system)) return null;
  if (!(withheldTrains(block) > 0)) return null;
  const age = observationAge(row, servedAt, now);
  return age != null && age > OBS_MAX_S ? "withheld" : null;
}

/* The words a position gets to say in a popup, or nothing for a fresh reported position: silence
   means current (memo D9), which is the rule every surface in this app follows.

   IT RETURNS WORDS AND NOT MARKUP SINCE MR5, which is the shape the vocabulary wanted: section 5
   puts a vehicle's facts in a label/value grid, so the Position row is a value like any other and
   the rule above is what decides whether that row exists. popupRowsHtml drops a row with no value,
   so the two rules compose rather than being written twice: this one says when a position has
   nothing to say, and the grid's says what a row with nothing to say looks like (nothing).

   MR5 (ruling Q1): EVERY SURFACE NOW, INCLUDING THE RAILROAD POPUP, which had been the one
   exception. That popup printed `position.compact` itself, unconditionally, from a line of its own
   in systems/railroad.js, and the two differences that made are the whole content of this ruling:
   its `placed` trains said "scheduled (no GPS)" where the contract says "scheduled position (no
   GPS)", and its FRESH GPS fixes said "live GPS" where every other surface in the app says nothing.
   An AGED fix still speaks, which is the contract working rather than a compromise: the silence is
   only ever about a current one.

   `.compact` STILL EXISTS AND NOW HAS NO READER, which is worth stating precisely rather than
   softly. It is one of the three forms section 3.2 of the freshness contract defines, it still
   differs from `.words` in exactly one family (`placed`), and positions.test.js still pins that
   difference. But the railroad popup was its ONLY caller, so after this ruling nothing in the app
   renders it. Deleting a form the contract defines is an amendment to that contract rather than a
   stage's tidying, so it stays, and pins.spec.js P5b carries its string as a waiver naming exactly
   this reason. P5b is how it was found: the coverage test reported "scheduled (no GPS)" as a
   rider-visible literal that no pin covered, on the commit that unified the call site.

   THE GUARD IS ON BOTH FIELDS. The ruling says an empty `.words` omits the row, and `positionQualifier`
   never returns one, so today that is belt to the brace `kind` already provides. It is written down
   because a future branch of that function could, and a row rendering `<br><span></span>` would be
   an empty line a rider cannot account for rather than an omission. */
function positionWords(position) {
  if (!position || !position.kind || !position.words) return "";
  return position.words;
}

// A position's clause in a marker's accessible name, under the same rule: its spoken
// form, or nothing where the popup says nothing. The A2 rule is that a name says what its
// popup renders, and this is the same answer the popup's line prints, said aloud.
function positionClause(position) {
  return position && position.kind ? position.spoken : null;
}

/* MR5 (ruling Q2): vehicleStaleLine WAS HERE and is gone, because the popup footer took its job.
   It rendered a vehicle popup's system age line and withheld itself where the position's words had
   already stated an age at least that old. popupFreshHtml keeps that rule exactly, keeps its
   reasoning (copied there rather than cited, so it cannot outlive this comment), and adds the two
   states the line never had. It was the function's only caller in either direction: nothing in the
   freshness contract names it, unlike positionQualifier's `.compact`, which is why this is deleted
   where that is recorded and kept. */

// ONE WRITE PER RENDER, AS ONE STRING (memo D11). The page's live region is atomic and
// polite, so two writes before a screen reader reads it are one sentence lost: the second
// replaces the first. systems/shared.js holds a poll's announcements until its render
// ends and speaks them through this, in the order they happened.
function composeAnnouncements(texts) {
  return (texts || []).filter((text) => typeof text === "string" && text.trim()).join(" ");
}

// Decide what a successful-but-EMPTY poll should do. Keeping the last-known
// markers protects against a TRANSIENT empty feed (a blip that would otherwise
// flicker every marker off and back on), but it must be bounded or a real lull
// (an overnight railroad gap) leaves ghost markers frozen forever. We bound it
// by TIME, not poll count: the poll cadence can change, so "N empty polls" is
// meaningless, whereas elapsed seconds is stable. `emptyRunStart` is the
// fetched_at of the FIRST empty poll in the current empty run (null when the
// previous poll was non-empty); `fetchedAt` is this poll's. Both are the
// server-recorded fetched_at, not the wall clock, so the decision is skew-free
// and consistent with staleness() above. Within FEED_STALE_AFTER_S of the run's
// start, keep the markers and warn "showing last known"; at or past that
// threshold, apply the empty dataset (the callers' unseen-marker sweeps clear
// the layer) and drop the now-false "showing last known" clause. Returns the
// decision plus the run start to store back (unchanged reset happens on the
// caller's non-empty path). A null fetched_at cannot be timed, so it holds
// last-known without starting or advancing a run.
function emptyFeedDecision(emptyRunStart, fetchedAt) {
  if (fetchedAt == null) {
    return { applyEmpty: false, error: "feed empty, showing last known", emptyRunStart };
  }
  const start = emptyRunStart ?? fetchedAt; // first empty poll of this run
  if (fetchedAt - start >= FEED_STALE_AFTER_S) {
    return { applyEmpty: true, error: "feed empty", emptyRunStart: start };
  }
  return { applyEmpty: false, error: "feed empty, showing last known", emptyRunStart: start };
}

// Per-source refresh gate (R2). refreshAll fires a refresh only for sources that
// are NOT already in flight, so a single slow source (bounded by its own
// AbortSignal.timeout) can never starve the others the way the old whole-cycle
// `refreshing` lock did. A source clears its own inFlight flag in refreshSource's
// finally, so this stays a pure function of the descriptor: given the row, is it
// eligible to be refreshed this tick. Pure and node-testable; the fetch/apply it
// gates stays in map.js (browser fetch + DOM).
function shouldRefresh(source) {
  return !source.inFlight;
}

function _segLen(aLat, aLon, bLat, bLon) {
  return Math.hypot((bLon - aLon) * _COS_LAT, bLat - aLat);
}

// Cumulative arc-length along a polyline: cum[0] = 0, cum[i] = cum[i-1] +
// segLen(points[i-1], points[i]). cum.length === points.length.
function polylineCumLengths(points) {
  const cum = [0];
  for (let i = 1; i < points.length; i++) {
    cum.push(cum[i - 1] + _segLen(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]));
  }
  return cum;
}

// [lat, lon] at arc-length s along the polyline, clamped to [0, total]. Binary
// search the segment containing s, then lerp the real coords within it.
function pointAtArcLength(points, cum, s) {
  const total = cum[cum.length - 1];
  if (!(total > 0) || s <= 0) return points[0].slice();
  if (s >= total) return points[points.length - 1].slice();
  let lo = 0, hi = cum.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (cum[mid] <= s) lo = mid;
    else hi = mid;
  }
  const seg = cum[hi] - cum[lo];
  const u = seg > 0 ? (s - cum[lo]) / seg : 0;
  const [aLat, aLon] = points[lo];
  const [bLat, bLon] = points[hi];
  return [aLat + (bLat - aLat) * u, aLon + (bLon - aLon) * u];
}

// Closest point on one polyline to P: { s, dist } in the same basis as cum, or
// null for a degenerate (<2-point) polyline.
function _projectOntoPolyline(points, cum, pLat, pLon) {
  if (points.length < 2) return null;
  let best = null;
  const px = pLon * _COS_LAT, py = pLat;
  for (let i = 1; i < points.length; i++) {
    const ax = points[i - 1][1] * _COS_LAT, ay = points[i - 1][0];
    const bx = points[i][1] * _COS_LAT, by = points[i][0];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy;
    const u = len2 > 0 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
    const dist = Math.hypot(px - (ax + dx * u), py - (ay + dy * u));
    if (best === null || dist < best.dist) best = { dist, s: cum[i - 1] + Math.sqrt(len2) * u };
  }
  return best;
}

// Project P onto a route's polylines (each { points, cum }); return
// { poly, s, dist } for the closest one within maxDist, else null. maxDist is
// parameterized (default = the subway constant) so a later increment can pass a
// looser railroad tolerance without touching callers.
function projectOntoRoute(routeGeom, pLat, pLon, maxDist = ROUTE_ACCEPT_DIST) {
  let best = null;
  for (let i = 0; i < routeGeom.length; i++) {
    const r = _projectOntoPolyline(routeGeom[i].points, routeGeom[i].cum, pLat, pLon);
    if (r && (best === null || r.dist < best.dist)) best = { poly: i, s: r.s, dist: r.dist };
  }
  return best && best.dist <= maxDist ? best : null;
}

// Slice a train's route polyline between its prev and next station. `geom` is the
// resolved [{points, cum}, ...] for the train's route (the CALLER looks it up, so
// this stays pure and serves both the subway and railroad route indexes); maxSlice
// / acceptDist default to the subway constants. Returns { points, cum, s0, s1 }
// when both stations project onto the SAME polyline within tolerance and the arc
// between them is plausible; null otherwise (trainLatLng then uses the straight line).
// s0/s1 are returned unordered (not min/max): the arc is walked in the sign of
// (s1 - s0), so a single stored shape serves both travel directions.
function computeRouteSlice(train, geom, { maxSlice = ROUTE_MAX_SLICE, acceptDist = ROUTE_ACCEPT_DIST } = {}) {
  if (train.prev_lat == null) return null;
  if (!geom) return null;
  const p0 = projectOntoRoute(geom, train.prev_lat, train.prev_lon, acceptDist);
  const p1 = projectOntoRoute(geom, train.latitude, train.longitude, acceptDist);
  if (!p0 || !p1 || p0.poly !== p1.poly) return null;
  if (Math.abs(p1.s - p0.s) > maxSlice) return null;
  const poly = geom[p0.poly];
  return { points: poly.points, cum: poly.cum, s0: p0.s, s1: p1.s };
}

// v2 train position: walk the route polyline from the previous-station offset to
// the next-station offset, parameterized by time. train._route ({ points, cum,
// s0, s1 }) is attached per poll by map.js when both stations projected cleanly
// onto the SAME polyline; absent otherwise, so this falls back to the v1 straight
// line. `now` is skew-corrected epoch seconds. `state` carries the monotonic-f
// clamp across calls: f may not decrease within a segment (so a growing next_time
// on a dwelling train can't drag the marker backward); it resets per segment.
// CAN THIS TRAIN BE INTERPOLATED AT ALL: a previous anchor, both times, and a segment
// that runs forward in time. trainLatLng's own guard, named so railroadAtItsStation asks
// exactly the question the glide answers: a train that cannot be interpolated is drawn
// at its own coordinates, which for a placement are its station's.
function glideAnchored(train) {
  const t = train || {};
  return t.prev_lat != null && t.prev_time != null && t.next_time != null && t.next_time > t.prev_time;
}

function trainLatLng(train, now, state = {}) {
  const { prev_lat, prev_lon, prev_time, next_time, latitude, longitude } = train;
  // Unusable timing: sit at the static next-station position (v1 behavior).
  if (!glideAnchored(train)) return [latitude, longitude];
  const segKey = `${prev_time}|${train.stop_id}`;
  if (state.segKey !== segKey) {
    state.segKey = segKey;
    state.lastF = 0;
  }
  const rawF = (now - prev_time) / (next_time - prev_time);
  const f = Math.min(1, Math.max(rawF, state.lastF));
  state.lastF = f;
  const r = train._route;
  if (r) return pointAtArcLength(r.points, r.cum, r.s0 + (r.s1 - r.s0) * f);
  return [prev_lat + (latitude - prev_lat) * f, prev_lon + (longitude - prev_lon) * f];
}

// The countdown DECISION, separated from its wording. A1 needs the same
// thresholds spoken rather than abbreviated ("4 minutes", not "4 min", which a
// screen reader reads as "four min"), and the one thing that must not happen is
// two functions rounding or bucketing time differently. So the rounding lives
// here once and both formatters below are thin wordings of this result.
// Returns {kind: "blank" | "now" | "min" | "hm", mins, hours, rem}.
function countdownParts(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return { kind: "blank" };
  if (seconds < 30) return { kind: "now" };
  const mins = Math.round(seconds / 60);
  // Hours tier for the long railroad branch-end horizons (e.g. 6000s -> "1 h 40
  // min"); only fires at 100+ minutes, which subway countdowns never reach.
  if (mins < 100) return { kind: "min", mins };
  return { kind: "hm", hours: Math.floor(mins / 60), rem: mins % 60 };
}

// Arrival countdown label from a seconds-until-arrival delta: "now" when due
// (or past), else rounded to whole minutes. The VISUAL wording, used by every
// popup; its output is unchanged by the countdownParts extraction above and the
// existing tests are what prove that.
function formatCountdown(seconds) {
  const p = countdownParts(seconds);
  if (p.kind === "blank") return "";
  if (p.kind === "now") return "now";
  if (p.kind === "min") return `${p.mins} min`;
  return `${p.hours} h ${p.rem} min`;
}

// The SPOKEN wording of the same decision, for the A1 station panel. Units are
// written out and pluralized, because this text is read aloud rather than
// scanned. Sharing countdownParts is what keeps "4 min" and "in 4 minutes" from
// ever disagreeing about which minute it is.
function spokenCountdown(seconds) {
  const p = countdownParts(seconds);
  if (p.kind === "blank") return "";
  if (p.kind === "now") return "now";
  const unit = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  if (p.kind === "min") return `in ${unit(p.mins, "minute")}`;
  if (!p.rem) return `in ${unit(p.hours, "hour")}`;
  return `in ${unit(p.hours, "hour")} ${unit(p.rem, "minute")}`;
}

// Arrivals buckets in a stable display order for a station popup. The backends
// send only the non-empty buckets, so this orders the ones that have trains and
// never fabricates empties. Returns [[name, arrivals], ...]. Any unexpected key
// is appended rather than dropped, so a backend change can never silently hide
// trains. Shared by the railroad and PATH orderings below, which differ only in
// their bucket-name lists.
function orderedBuckets(order, directions) {
  const present = directions || {};
  const known = order.filter((name) => (present[name] || []).length);
  const extra = Object.keys(present).filter(
    (name) => !order.includes(name) && (present[name] || []).length,
  );
  return [...known, ...extra].map((name) => [name, present[name]]);
}

// Railroad buckets: Inbound first (toward the NYC terminal, the common ask),
// then Outbound, then the residual "Trains" bucket (for trips whose direction
// the backend could neither read from direction_id nor infer from the MNR
// stop-progression heuristic).
const RAILROAD_BUCKET_ORDER = ["Inbound", "Outbound", "Trains"];

function orderedRailroadBuckets(directions) {
  return orderedBuckets(RAILROAD_BUCKET_ORDER, directions);
}

/* formatRailroadHead IS GONE, AND THIS IS WHERE IT WAS. It joined a railroad train's system,
   route id and route name into one line ("LIRR · Babylon Branch", "LIRR route 5", "MNR") for the
   one caller that wanted them joined: the popup head MR5 takes apart. Section 5 wants those parts
   separately, as a kicker and a title, and railroadHeadParts up in the vocabulary section is the
   same three cases returning them that way.

   ITS WORDS DID NOT CHANGE, only where they sit. The middot it joined with was a visual separator
   and the two parts are separated by being rows now, which is the argument railroadTrainName's
   comment already makes for not speaking it.

   A COMMENT RATHER THAN A FUNCTION WITH NO CALLER, which is this stage's third such disposal
   (vehicleStaleLine and stalePopupLine were the first two, in the footer's commit) under the same
   rule: a helper nothing calls is a second answer waiting for someone to call it. Its three cases
   are asserted in popupvocab.test.js against railroadHeadParts, joined back into the three strings
   it used to return, so the words it carried are still pinned somewhere. */

// Full railroad station arrivals popup HTML. Lives here (not map.js) so node can
// test the escaping and ordering. `now` is the skew-corrected clock, passed in
// for testability (map.js computes it from minClockOffset). `nameFor(routeId)`
// resolves a route's rider-facing name for this station's system (map.js closes
// over the (system|route_id) name map), returning null when unknown. Header is
// the station name plus a muted system tag; each present bucket renders its
// heading and one row per train: a route badge whose fill and ink are `paintFor(routeId)`'s
// (systems/railroad.js closes that over the (system|route_id) colour table the way it already
// closes `nameFor` over the name table), the route name where known, the train number when the
// feed carries one, and the countdown. Every feed-derived string is escaped.
//
// `paintFor` IS IN SLOT 4 BECAUSE THE OTHER TWO BOARDS PUT THEIR RESOLVER THERE (njtArrivalsHtml
// and pathArrivalsHtml), and ruling R1 is why it exists at all: this badge resolved the deleted
// `railroadColor` hash, so LIRR|1 and MNR|1 drew one brown for two published greens. It returns
// railBranchPaint's PAIR rather than a colour because the badge is 700-weight type on that fill and
// owes 4.5, and one published colour clears with neither ink (EE0034, four of Metro-North's six
// routes): the pair is the only resolver that can move such a fill, exactly as the tag's branch
// block does. The default is the rail neutral rather than a second one.
function railroadArrivalsHtml(station, body, now, paintFor = () => railBranchPaint(null), nameFor = () => null, markHtml = "") {
  // Every row qualified by its own served age, and the system line where the R1 age
  // line was (6.2; see arrivalQualifier and boardSystemLine).
  const board = boardFreshness("railroad", body, now);
  /* MR5: section 5's kicker, title and board. The agency moves from a muted tag beside the
     station's name to the kicker above it, and it is still the SERVED `system` field: "MNR"
     here, where this station's own panel row says railroadSystemLabel's "Metro-North". That
     divergence is recorded as an MR5 finding rather than reworded in passing. */
  const header =
    popupKickerHtml({ left: station.system ?? "" }) +
    popupTitleHtml({ markHtml, text: station.name ?? station.id }) +
    boardLineHtml(boardSystemLine(board, stationArrivalsRows(body)));
  const buckets = orderedRailroadBuckets(body.directions);
  if (!buckets.length) return `${header}<div class="arr-none">No trains</div>\n`;
  let html = header;
  for (const [dir, arrivals] of buckets) {
    html += popupDirHtml(dir);
    html += popupArrRowsHtml(
      arrivals.map((a) => {
        const route = a.route_id ?? "";
        const paint = paintFor(route);
        return {
          /* THE ROW'S MARK IS THE BOARD'S OWN CHIP, not the map's rail tag: a tag states a body and
             a heading, and an arrivals row has neither to state. Its two paints come from
             railBranchPaint through `paintFor`, so the fill can move where no ink clears, and
             popups.spec.js D6i measures the drawn result in both themes. (That sentence used to
             name pins.spec.js P4c, which was never true: P4c pins `contrast/marks`, the map's
             marker families, and has no badge in it.) */
          markHtml:
            `<span class="arr-badge" style="background:${paint.fill};color:${paint.ink}">` +
            `${esc(route || "?")}</span>`,
          label: (a.route_id ? nameFor(a.route_id) : null) || "",
          extraHtml:
            (a.train_num ? ` <span class="popup-sub">#${esc(a.train_num)}</span>` : "") +
            qualifierHtml(arrivalQualifier(a, board)),
          countdown: formatCountdown(a.arrival - now),
        };
      }),
    );
  }
  return html;
}

// ---- AirTrain JFK (static-only, no realtime feed) ----

// Parse an "HH:MM" band bound to minutes since midnight, accepting "24:00" (1440)
// as an end-of-day bound.
function hhmmToMinutes(hhmm) {
  const [h, m] = String(hhmm).split(":");
  return Number(h) * 60 + Number(m);
}

// Select the scheduled AirTrain headway band covering a minute-of-day, using
// HALF-OPEN [start, end) intervals so every minute maps to exactly one band (one
// band's end bound is the next band's start). `minutesSinceMidnight` is 0..1439.
// Returns the band (carrying headway_min) or null when NO band covers the minute.
// The null case is defensive on purpose: a future regenerated fixture could leave
// a gap, and returning null (so the caller can say "schedule unavailable") is safer
// than assuming the table always tiles the full day and guessing a nearest band.
function selectHeadwayBand(bands, minutesSinceMidnight) {
  for (const band of bands ?? []) {
    const start = hhmmToMinutes(band.start);
    const end = hhmmToMinutes(band.end);
    if (minutesSinceMidnight >= start && minutesSinceMidnight < end) return band;
  }
  return null;
}

// AirTrain JFK station popup HTML. WHY this is a plain static popup and NOT the
// live arrivals component (bindStationPopup / openStationArrivals / the 1s
// countdown tick): AirTrain has no realtime feed, so there is nothing to count
// down to, and a ticking "arriving in N min" would fabricate precision the data
// does not have. Instead we show the SCHEDULED headway band for the current time,
// clearly labeled "(scheduled)". `minutes` is minutes since NY midnight, computed
// by the CALLER and passed in (kept pure and testable with a plain numeric input).
// Every feed-derived string is escaped.
function airtrainStationPopupHtml(station, routes, minutes, markHtml = "") {
  const serving = (routes ?? []).filter((r) => (r.stations ?? []).includes(station.id));
  /* MR5: section 5's kicker and title, and the branches as its label/value grid. The one
     sentence this popup used to print as a muted line is now the kicker's "AirTrain JFK" and
     the line under the title, which is the same split the railroad's head took: the middot
     was joining two things that are a row apart now. */
  const header =
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.airtrain }) +
    popupTitleHtml({ markHtml, text: station.name ?? station.id }) +
    `<div class="popup-sub">scheduled service (no live tracking)</div>\n`;
  if (!serving.length) {
    return `${header}<div>No AirTrain branch serves this station.</div>\n`;
  }
  /* A BRANCH IS A LABEL AND ITS HEADWAY IS THE VALUE, which is what the grid is for. "(scheduled)"
     stays inside the value rather than becoming a muted span of its own: the words are the ones
     this popup has printed since phase 16, and a row's value is one string in this vocabulary.
     headway_min is a validated integer (AirTrainHeadwayBand.headway_min: int) and the grid escapes
     every value, so nothing here needs to escape anything itself. */
  return (
    header +
    popupRowsHtml(
      serving.map((route) => {
        const band = selectHeadwayBand(route.headways, minutes);
        return {
          k: route.name ?? route.id,
          v: band ? `every ~${band.headway_min} min (scheduled)` : "schedule unavailable",
        };
      }),
    )
  );
}

// ---- PATH (phase 13c: map layer over the 13a/13b endpoints) ----

// PATH buckets: "To New York" first (the dominant commute ask, mirroring the
// railroad's Inbound-first choice), then "To New Jersey", then the residual
// "Trains" bucket for trips the bridge feed served without a direction_id.
const PATH_BUCKET_ORDER = ["To New York", "To New Jersey", "Trains"];

function orderedPathBuckets(directions) {
  return orderedBuckets(PATH_BUCKET_ORDER, directions);
}

// Neutral slate for a PATH route the color table doesn't know; belongs to no
// real PATH route color, so a fallback is visually honest about being one.
const PATH_FALLBACK_COLOR = "#546e7a";

// /api/path-routes serves route_color verbatim from routes.txt: bare hex, no
// "#", possibly null. Validate before prefixing rather than trusting the feed,
// so a malformed value falls back instead of reaching a style attribute.
function pathColor(hex, fallback = PATH_FALLBACK_COLOR) {
  return /^[0-9a-fA-F]{6}$/.test(hex ?? "") ? `#${hex}` : fallback;
}

// Rider-facing head text for a PATH train popup: the route's rider-facing name
// ("Newark - World Trade Center") when known, else the route id, else just
// "PATH". Returns PLAIN text; the caller escapes it (the railroad precedent).
function formatPathHead(routeId, name) {
  if (name) return name;
  if (routeId) return `PATH route ${routeId}`;
  return "PATH";
}

// PATH train popup HTML. `name` is the rider-facing route name (null when
// unknown) and `color` a css color, both resolved by the caller from the
// /api/path-routes tables so this stays pure. Two deliberate omissions against
// the subway train popup: no trip id line, because PATH bridge trip ids are
// unstable across upstream refreshes and display-poor (the API contract says
// clients never show or key on them), and no alerts block, because PATH
// publishes no alerts feed. Every feed-derived string is escaped. `position` is the
// train's positionQualifier answer (6.3): PATH serves every train `placed`, so the line
// reads "scheduled position (no GPS)", and its age once its own trip update is past
// OBS_FRESH_S; before 6.3 the line was a constant that could not say either.
function pathTrainPopupHtml(train, name, color, position = null, surface = POPUP_SURFACE_FALLBACK, markHtml = "") {
  return (
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.path }) +
    popupTitleHtml({ markHtml, text: formatPathHead(train.route_id, name), color: readableInk(color, surface) }) +
    popupRowsHtml([
      { k: "Next stop", v: train.stop_name },
      { k: "Direction", v: train.direction },
      { k: "Position", v: positionWords(position) },
    ])
  );
}

// PATH station arrivals popup HTML, the railroad renderer's shape minus
// train_num (the bridge feed carries none). `colorFor(routeId)` resolves a
// route's css badge color and `nameFor(routeId)` its rider-facing name; map.js
// closes both over the /api/path-routes tables, keeping this pure and
// node-testable. An empty directions dict renders the shared "No trains"
// treatment. Every feed-derived string is escaped.
function pathArrivalsHtml(station, body, now, colorFor = () => PATH_FALLBACK_COLOR, nameFor = () => null, markHtml = "") {
  // PATH dates every trip itself, so two rows on one board can carry two different
  // ages, and only the old one is qualified (6.2).
  const board = boardFreshness("path", body, now);
  const header =
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.path }) +
    popupTitleHtml({ markHtml, text: station.name ?? station.id }) +
    boardLineHtml(boardSystemLine(board, stationArrivalsRows(body)));
  const buckets = orderedPathBuckets(body.directions);
  if (!buckets.length) return `${header}<div class="arr-none">No trains</div>\n`;
  let html = header;
  for (const [dir, arrivals] of buckets) {
    html += popupDirHtml(dir);
    html += popupArrRowsHtml(
      arrivals.map((a) => {
        const route = a.route_id ?? "";
        const color = colorFor(a.route_id);
        return {
          markHtml:
            `<span class="arr-badge" style="background:${color};color:${readableTextOn(color)}">` +
            `${esc(route || "?")}</span>`,
          label: (a.route_id ? nameFor(a.route_id) : null) || "",
          extraHtml: qualifierHtml(arrivalQualifier(a, board)),
          countdown: formatCountdown(a.arrival - now),
        };
      }),
    );
  }
  return html;
}

// ---- NYC Ferry (phase 14c: map layer over the 14a/14b endpoints) ----

// Neutral blue-gray for a boat whose route the color table doesn't know (a 14b
// join miss, kept on the map and labeled "Unassigned"): belongs to no real NYC
// Ferry route color, so a fallback reads as honestly being one. Distinct from
// PATH's slate so a stray unassigned boat isn't mistaken for a PATH marker.
const FERRY_FALLBACK_COLOR = "#78909c";

// Ferry arrivals bucket order: the /api/ferry-arrivals feed has NO direction_id,
// so buckets are ROUTE NAMES (a dynamic set, unlike the fixed direction lists the
// other systems use). Sort them alphabetically for a stable, predictable popup;
// only buckets that actually carry boats are returned (the backend sends only
// populated ones, and this filters defensively). Returns [[routeName, rows], ...].
function orderedFerryBuckets(routes) {
  const present = routes || {};
  return Object.keys(present)
    .filter((name) => (present[name] || []).length)
    .sort()
    .map((name) => [name, present[name]]);
}

// Pick the countdown a ferry arrivals ROW should show. Before the boat reaches
// the dock, count down to its ARRIVAL. Once it has arrived and is dwelling at
// the dock (arrival already passed but departure still ahead), or the dock is an
// origin with no arrival at all, count down to its DEPARTURE instead: at that
// point the rider cares when it LEAVES, not that it technically docked a moment
// ago. This is the dwell data (both times, from 14b) earning its passage.
// Returns { mode: "arriving" | "departing", seconds }, and never drops a row: a
// terminal dock with only an arrival keeps the arrival countdown even once past.
function ferryArrivalDisplay(row, now) {
  const arrival = row.arrival;
  const departure = row.departure;
  if (arrival != null && arrival - now >= 0) {
    return { mode: "arriving", seconds: arrival - now };
  }
  if (departure != null) {
    return { mode: "departing", seconds: departure - now };
  }
  return { mode: "arriving", seconds: arrival != null ? arrival - now : null };
}

// A docked boat's resting opacity: dimmed so it reads as parked at a dock rather
// than under way. It lives here, next to the other opacity rule, because it is now an
// input to markerOpacity rather than a css class of its own: one opacity authority
// per marker element, so the C2 staleness dimming compounds with it instead of
// overriding it. The .ferry-docked / .ferry-active classes remain as state markers.
const FERRY_DOCKED_OPACITY = 0.55;

// Map a boat's GTFS current_status to the icon variant. STOPPED_AT means the boat
// is sitting at a dock (render docked/dimmed); everything else (IN_TRANSIT_TO,
// INCOMING_AT, or a missing/unknown status) means under way (render active). The
// default is deliberately "active": a boat with GPS that is not explicitly
// STOPPED_AT should not be frozen-looking, and an unknown future enum value is
// safer shown moving than parked.
function ferryBoatIconState(status) {
  return status === "STOPPED_AT" ? "docked" : "active";
}

// Plain-words status for a boat popup, or null when the feed omits/uses an
// unknown status (the popup then shows no status line rather than asserting a
// guess). The three values 14b observed map to rider-facing phrases.
function ferryStatusText(status) {
  switch (status) {
    case "STOPPED_AT":
      return "At dock";
    case "INCOMING_AT":
      return "Arriving at dock";
    case "IN_TRANSIT_TO":
      return "Under way";
    default:
      return null;
  }
}

// GTFS-RT Position.speed is meters per second; boat popups show it in knots, the
// convention for vessels. 1 m/s = 1.94384 kn.
const MS_TO_KNOTS = 1.94384;
// Below this the reading is GPS jitter, not travel: a boat sitting at a dock still
// reports a few tenths of a knot of drift. 0.5 m/s is ~1 kn, comfortably above that
// noise and well below any real ferry cruising speed (10-25 kn).
const FERRY_SPEED_FLOOR_MS = 0.5;

// A boat's speed as an "N.N kn" string, or null when it should not be shown. Shown
// ONLY for an under-way boat (IN_TRANSIT_TO) moving above the jitter floor: a docked
// boat, or one whose reading is sub-floor drift, shows no speed rather than a
// misleading fraction of a knot. Pure and node-testable; the popup renders the line
// only when this returns a value.
function ferrySpeedKnots(status, speedMs) {
  if (status !== "IN_TRANSIT_TO") return null;
  if (typeof speedMs !== "number" || !Number.isFinite(speedMs) || speedMs < FERRY_SPEED_FLOOR_MS) {
    return null;
  }
  return `${(speedMs * MS_TO_KNOTS).toFixed(1)} kn`;
}

// Ferry BOAT popup HTML. `name` is the route long name (null when the boat did
// not join a route: 14b keeps it on the map, and here it reads "Unassigned" in
// the neutral fallback color) and `color` a css color, both resolved by the
// caller from the /api/ferry-routes tables so this stays pure and node-testable.
// Speed is shown in knots for an under-way boat above the jitter floor (H4; see
// ferrySpeedKnots): the GTFS-RT unit is meters per second, confirmed by the observed
// 0-13 m/s = 0-25 kn range matching NYC Ferry hull speeds. NO alerts block IN THIS
// FUNCTION: route-scoped ferry alerts are shown, but the caller (ferryBoatPopup)
// prepends them via routeAlertsBlock so this stays a pure HTML builder, exactly as
// the subway/bus popup HTML helpers keep their route-alert prepend in the caller.
// Every feed-derived string is escaped. `position` is the boat's positionQualifier
// answer (6.3): a fresh fix adds nothing, since the legend already says a boat is GPS,
// and one past OBS_FRESH_S adds "live GPS, as of 2m ago" (positionWords).
function ferryBoatPopupHtml(boat, name, color, position = null, surface = POPUP_SURFACE_FALLBACK, markHtml = "") {
  return (
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.ferry }) +
    popupTitleHtml({ markHtml, text: name || "Unassigned", color: readableInk(color, surface) }) +
    popupRowsHtml([
      { k: "Boat", v: boat.label },
      // Both of these are null where the feed has nothing to say (a docked boat has no speed,
      // an unknown status has no words), and the grid drops a row with no value: the popup
      // says what it knows and is silent about the rest, which is what it did with its
      // `<br>`-joined lines before section 5's grid.
      { k: "Status", v: ferryStatusText(boat.status) },
      { k: "Speed", v: ferrySpeedKnots(boat.status, boat.speed) },
      { k: "Position", v: positionWords(position) },
    ])
  );
}

// Ferry DOCK arrivals popup HTML. Buckets are route names (orderedFerryBuckets);
// each row is a countdown, shown as "departs …" when the boat is dwelling or the
// dock is an origin (ferryArrivalDisplay), else the plain arrival countdown. The
// bucket heading is colored by its route (all rows in a bucket share a route, so
// the color comes from the first row's route_id via colorFor). The station's
// `wheelchair` flag surfaces as a small accessibility marker in the header, the
// first such display in the app. An empty routes dict renders "No boats". Every
// feed-derived string is escaped; colorFor returns a validated css color.
function ferryArrivalsHtml(station, body, now, colorFor = () => FERRY_FALLBACK_COLOR, surface = POPUP_SURFACE_FALLBACK, markHtml = "") {
  /* MR5: THE DOCK'S ACCESSIBILITY GLYPH IS THE KICKER'S RIGHT-HAND SLOT, which is where section 5
     puts it ("right: route bullets / direction / accessibility"). It keeps its title attribute,
     which is the only place its WORDS exist: pins.spec.js reads them through the `labels` view for
     exactly that reason. */
  const access = station.wheelchair
    ? '<span class="popup-access" title="Wheelchair accessible">&#9855;</span>'
    : "";
  // Dock rows are dated by the TripUpdates clock, never the boat's (6.1), and each is
  // qualified by it here (6.2).
  const board = boardFreshness("ferry", body, now);
  const header =
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.ferry, rightHtml: access }) +
    popupTitleHtml({ markHtml, text: station.name ?? station.id }) +
    boardLineHtml(boardSystemLine(board, stationArrivalsRows(body)));
  const buckets = orderedFerryBuckets(body.routes);
  if (!buckets.length) return `${header}<div class="arr-none">No boats</div>\n`;
  let html = header;
  for (const [routeName, rows] of buckets) {
    const color = rows[0] && rows[0].route_id ? colorFor(rows[0].route_id) : FERRY_FALLBACK_COLOR;
    // The bucket is a ROUTE here rather than a direction, so it carries the route's colour,
    // walked against the popup's own surface (readableInk) like every other ink in a popup.
    html += popupDirHtml(routeName, readableInk(color, surface));
    html += popupArrRowsHtml(
      rows.map((row) => {
        const d = ferryArrivalDisplay(row, now);
        // "departs " STAYS ON THE COUNTDOWN, not in the label cell: it is half of one phrase
        // ("departs 1 min") and the number cell is the nowrap one, so the phrase cannot break.
        // The NJ Transit board does the same with the same prefix.
        return {
          extraHtml: qualifierHtml(arrivalQualifier(row, board)),
          countdown: (d.mode === "departing" ? "departs " : "") + formatCountdown(d.seconds),
        };
      }),
    );
  }
  return html;
}

// ---- NJ Transit Rail (phase 15c: map layer over the 15a/15b/15c endpoints) ----

// Neutral indigo-grey for an NJT route the colour table does not know. Every
// lookup in systems/njt.js lands here rather than on a blank, which is the
// amendment-a rule: a route with no line still colours a badge and a popup.
//
// PICKED AGAINST THE PUBLISHED TABLE RATHER THAN BY TASTE. The route most likely
// to reach this fallback is 17, the event-only Meadowlands Rail Line, which has no
// trips in an ordinary publication and therefore no entry on /api/njt-routes; its
// published route_color is C1AA72, a khaki, so a warm neutral would have been
// indistinguishable from the real colour of the very route it stands in for. None
// of the other eleven is close to this value either. It is also kept clear of
// PATH's #546e7a and the ferry's #78909c, both blue-greys: a fallback that looked
// like another mode's fallback would be worse than an obvious placeholder.
const NJT_FALLBACK_COLOR = "#4a4e69";

// /api/njt-routes serves route_color verbatim from routes.txt: bare hex, no "#",
// possibly null (the endpoint says so). Validated before prefixing for the same
// reason pathColor validates: a malformed value must fall back rather than reach a
// style attribute.
function njtColor(hex, fallback = NJT_FALLBACK_COLOR) {
  return /^[0-9a-fA-F]{6}$/.test(hex ?? "") ? `#${hex}` : fallback;
}

// The two lookups amendment a is about, as functions rather than as `.get(...) ??`
// spelled out at each call site. `table` is any Map-like; a missing route is the
// ORDINARY case here, not an error:
//
//   * route 17 has no geometry in any publication we have seen, so it is absent
//     from /api/njt-routes while remaining a real route id a trip can name, and
//   * both tables are EMPTY until loadNjtRoutes resolves, which is several
//     seconds after the first /api/njt-trains poll paints markers on a cold start.
//
// The colour falls back to the neutral above; the NAME falls back to null, and it
// is formatNjtHead / njtTrainName that turn null into "NJ Transit route 17". They
// are separate steps because the panel's chips want the raw name-or-nothing while
// the popups want a sentence.
//
// THE GUARD IS `table &&` AND NOTHING MORE, after a review round measured the two
// clauses the first draft also carried. `routeId != null` cannot change the answer,
// because Map.prototype.get(null) returns undefined and the `||` below already
// takes the fallback; `typeof table.get === "function"` was reachable from no call
// site in the app. Both survived deletion with the whole suite green, which under
// this project's rule (every guard is shown to fire) makes them decoration rather
// than defence. What remains is the one state that really happens: the tables are
// null-ish before loadNjtRoutes has ever run.
function njtRouteColor(routeId, table) {
  return (table ? table.get(routeId) : null) || NJT_FALLBACK_COLOR;
}

function njtRouteName(routeId, table) {
  return (table ? table.get(routeId) : null) || null;
}

// IS THIS TRAIN DRAWN EXACTLY ON ITS STATION? The whole layer turns on the answer,
// twice, so it is one predicate rather than two spellings of it.
//
// backend/feeds/njt.py places a train four ways. Cases 1, 2 and 4 (dwelling,
// approaching its first call, and inside the terminal grace) all emit the STOP'S
// OWN COORDINATES with null anchors; case 3, and only case 3, interpolates a
// position between two stops and carries prev_lat / prev_time / next_time. So a
// null prev_lat is exactly "drawn at stop_id", which is what the cross-link needs
// (a link naming a station the train is NOT at is the failure shared.js's own
// principle comment forbids) and the negation of what the glide needs.
//
// Read off prev_lat rather than off `status`: the coordinates and the anchors are
// emitted by the same branch, while `status` is a label beside them that a later
// decoder change could reword. railroadAtItsStation asks the railroad the same
// question and adds the one case this decoder never produces, a glide that has
// already reached its stop.
function njtAtItsStation(train) {
  const t = train || {};
  return t.prev_lat == null && t.stop_id != null;
}

// THE PAYLOAD'S latitude/longitude IS NOT WHAT THE GLIDE HELPERS EXPECT, and this is
// the one place that difference is reconciled.
//
// trainLatLng and computeRouteSlice were written against the subway and railroad
// decoders, which place a train AT ITS NEXT STATION and leave the interpolation to
// the client: latitude/longitude is the FAR END of the segment, prev_lat/prev_lon is
// the near end, and f walks between them. NJ Transit's decoder does the
// interpolation ON THE SERVER (feeds/njt.py case 3 calls _interpolate, and its
// docstring says why), so latitude/longitude is the train's CURRENT position,
// already f of the way along. Handing that straight to trainLatLng walks
// prev -> current-position by f a SECOND time and draws the train at f SQUARED.
// Measured, before this existed: a train the backend placed halfway between Newark
// Penn and New York Penn was drawn a quarter of the way along, 3.6 km short on a
// 14.5 km leg, AT THE INSTANT OF THE POLL rather than merely between polls. Across
// the segment the drawn fraction ran 0.010, 0.062, 0.250, 0.563, 0.810 for a true
// 0.10, 0.25, 0.50, 0.75, 0.90.
//
// The reconciliation is to put the NEXT STOP back where the helpers expect it, which
// the client can do without a payload change because /api/njt-stops already gives
// every stop's coordinates and the payload names the stop it is heading for.
// Returns a train object safe to hand to computeRouteSlice and trainLatLng, or NULL
// when it must not be handed to them at all: a train drawn at its own station has
// nothing to interpolate, and a train whose next stop is not in the table yet (the
// seconds before loadNjtStops resolves, or a stop the static load dropped) would be
// interpolating toward a point nobody knows. Both cases draw at the served position,
// which is right rather than merely safe: the server's position is correct at the
// instant it was polled.
function njtGlideTrain(train, stopCoords) {
  const t = train || {};
  if (njtAtItsStation(t) || t.prev_lat == null || t.prev_time == null || t.next_time == null) {
    return null;
  }
  const target = stopCoords && t.stop_id != null ? stopCoords.get(t.stop_id) : null;
  if (!target) return null;
  return { ...t, latitude: target[0], longitude: target[1] };
}

// THE MARKER KEY, and it is `id` rather than `trip_id` on purpose. NJ Transit's
// TripUpdates feed emits ADDED trips with an EMPTY trip_id (36 of them in the
// first capture that caught a disrupted evening), so keying a marker map by
// trip_id would collapse every added train in the system onto one marker: one
// dot standing for a dozen trains, the rest swept off the map as unseen on the
// next poll. models.NjtTrain guarantees `id` is never empty, falling back to
// "njt:<entity.id>" exactly where trip_id is blank, which is what makes it the
// key the backend intends a client to use.
function njtKey(train) {
  return (train || {}).id;
}

// Rider-facing head text for an NJT popup or accessible name: the route's own
// name ("Morris & Essex Line") when the route table knows it, else the route id,
// else the system. PLAIN text; the caller escapes it (the railroad precedent).
function formatNjtHead(routeId, name) {
  if (name) return name;
  if (routeId) return `NJ Transit route ${routeId}`;
  return "NJ Transit";
}

// Build the three per-route tables systems/njt.js reads, from one /api/njt-routes
// payload. Pure and node-testable: the polyline geometry that DRAWS and the
// geometry that GLIDES come out of the same pass over the same list, which is the
// property path.js states in prose and this makes structural (they cannot be
// built from different payloads because there is only one).
//
// `cumLengths` is injected so a node test can build the index without the
// arc-length helper's real arithmetic; systems/njt.js passes polylineCumLengths.
// A route whose polylines list is empty still gets a name and a colour entry: it
// has nothing to draw and nothing to glide along, and dropping it from the name
// table would make the popup say "NJ Transit route 9" for a route the payload
// named.
function njtRouteTables(routes, cumLengths = polylineCumLengths) {
  const names = new Map();
  const colors = new Map();
  const shortNames = new Map();
  const paints = new Map();
  const index = new Map();
  for (const route of routes || []) {
    const id = route.route;
    if (id == null) continue;
    if (route.name) names.set(id, route.name);
    colors.set(id, njtColor(route.color));
    /* MR3 ADDED THE LAST TWO. `paints` is the feed's RAW pair, hex with no "#" and null for a
       blank column, because railBranchPaint has to see what the feed actually published: it
       prefers route_text_color where that ink is legible and recomputes one where it is not,
       and njtColor has already folded a missing colour into a css fallback by the time
       `colors` is built. `shortNames` is route_short_name, which is the code the tag prints
       for this agency (the brief's section 6: NJ Transit publishes one and the railroads do
       not, so hand-tabling it would answer a question the feed answers).

       `colors` IS DOWN TO ONE READER, AND THAT IS RULING R1's DOING. It said "stays as it was and
       is still what the popup head and the arrivals badge read" when MR3 built the pair; the popup
       head moved to njtBranch in MR5 round 1 and R1 moved the station board's badge and the panel's
       chip, so the only thing left reading `colors` is the route LINE (systems/njt.js). That is the
       last residue of finding N6 on this layer: a route published with a blank colour would draw a
       #4a4e69 line beside a #6d6e71 tag. No live route does (all twelve publish one) and route 17,
       which publishes nothing, has no polylines at all, so nothing can draw it today. */
    if (route.short_name) shortNames.set(id, route.short_name);
    paints.set(id, { color: route.color ?? null, textColor: route.text_color ?? null });
    const polylines = route.polylines || [];
    if (polylines.length) {
      index.set(id, polylines.map((points) => ({ points, cum: cumLengths(points) })));
    }
  }
  return { names, colors, shortNames, paints, index };
}

// NJT train popup HTML. `name` is the rider-facing route name (null when the
// route table has none) and `color` a css colour, both resolved by the caller
// through the two fallbacks above so this stays pure.
//
// EVERY NJT TRAIN SAYS HOW ITS POSITION WAS DERIVED, and none of them says GPS: NJ
// Transit's vehicle positions feed is deliberately never fetched (the numbers are at
// the poller registry in pollers.py), so every position on this layer is computed
// from the TripUpdates times against 15a's stop coordinates. What 6.3 adds is WHICH
// computation, read from the served provenance through `position` (positionQualifier):
// a train at or approaching a stop is `placed` there, "scheduled position (no GPS)",
// and one feeds/njt.py case 3 interpolated between two stops is `estimated`, "estimated
// from a prediction". Before 6.3 this line was a constant and called all 60 in-transit
// trains of the committed capture scheduled positions.
//
// The delay line is printed only when the feed carries one AND it is not zero: a
// train running to schedule is the unremarkable case and "0 min late" is noise.
// Sign is respected, because NJ Transit does publish negative delays (running
// early) and rendering one as "late" would be a lie about the direction.
function njtTrainPopupHtml(train, name, color, position = null, surface = POPUP_SURFACE_FALLBACK, markHtml = "") {
  const t = train || {};
  return (
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.njt }) +
    popupTitleHtml({ markHtml, text: formatNjtHead(t.route_id, name), color: readableInk(color, surface) }) +
    popupRowsHtml([
      { k: "Train", v: t.train_num },
      // "To" IS THE LABEL AND THE HEADSIGN IS THE VALUE, which is the same two words this popup
      // printed as one line ("To New York") with the preposition doing the labelling.
      { k: "To", v: t.headsign },
      { k: "Next stop", v: t.stop_name },
      // njtDelayText prints nothing for a train running to schedule and nothing for a delay that
      // rounds to zero, so the grid drops the row: the unremarkable case says nothing at all.
      { k: "Delay", v: njtDelayText(t.delay) },
      { k: "Position", v: positionWords(position) },
    ])
  );
}

// "<br>4 min late" / "<br>2 min early" / "" . Seconds in, rider words out, so the
// popup and the accessible name cannot word the same delay differently. Rounded
// to the nearest minute because the feed's own precision does not survive the
// journey (the endpoint says absolute times stay authoritative), and a delay under
// half a minute rounds to zero and prints nothing.
function njtDelayText(delaySeconds) {
  if (delaySeconds == null) return "";
  // NaN needs no clause of its own and the first draft's was DEAD: Math.round of it
  // is NaN, and `!minutes` is true for NaN, so the zero-delay line below already
  // returns "". A review round measured that deleting the clause left every test
  // green, which is this project's definition of a guard that is not one.
  const minutes = Math.round(Math.abs(delaySeconds) / 60);
  if (!minutes) return "";
  return `${minutes} min ${delaySeconds < 0 ? "early" : "late"}`;
}

/* njtDelayLine WAS HERE, and it was njtDelayText wrapped in a `<br>` and escaped. MR5's grid
   escapes its own values and separates rows by being rows, so the wrapper had nothing left to do
   and njtTrainPopupHtml was its only caller. The rule it carried is in the grid: njtDelayText
   returns "" for a train running to schedule and for a delay that rounds to zero, and a row with
   no value is not printed, so the unremarkable case still says nothing at all. */

/* "Jamaica, LIRR, station" and "Grand Central, Metro-North, station". MR3 needed one: the rail
   station was a canvas circleMarker with no element and therefore no accessible name, and the
   paper square is an L.marker, so it has DOM and goes through labeledMarker like every other
   marker in this app. A named div is not a new surface a rider has to wade through, because
   labeledMarker owns keyboard:false: these are not tab stops, exactly as NJ Transit's three
   station squares have not been since 15c.

   THE SYSTEM WORD IS THE POINT, and njtStationName's comment already names the case this
   answers: at New York Penn Station an LIRR station and an NJ Transit one sit on the same
   pixel, and Jamaica has an AirTrain station beside the LIRR's. The agency is what tells a
   screen-reader user which platform they have landed on. railroadSystemLabel spells MNR out,
   because "Metro-North" is what a rider calls it and "MNR" is a feed key. */
function railroadStationName(station) {
  const s = station || {};
  return joinName([s.name || s.id || "Railroad", railroadSystemLabel(s.system), "station"]);
}

// "Newark Penn Station, NJ Transit, station". A builder rather than a literal at
// the call site for the reason airtrainStationName is one: the station markers are
// the only NJT markers a touch screen-reader user meets before any train has
// loaded, and the system word is what tells them which railroad's platform they
// have landed on at New York Penn Station, where an LIRR circle sits on the same
// pixel. NJT stop names are already rider-facing, so there is nothing to reword.
function njtStationName(station) {
  const s = station || {};
  return joinName([s.name || s.id || "NJ Transit", "NJ Transit", "station"]);
}

// "Morris & Essex Line, NJ Transit, train 6633, to Dover, next stop Summit, 4 min
// late, scheduled position, no GPS". The A2 accessible name, built from the same
// fallbacks, the same delay wording and (6.3) the same position answer the popup uses,
// so an in-transit train is "estimated from a prediction" in both.
function njtTrainName(train, routeName = null, position = null) {
  const t = train || {};
  return joinName([
    formatNjtHead(t.route_id, routeName),
    "NJ Transit",
    t.train_num ? `train ${t.train_num}` : null,
    t.headsign ? `to ${t.headsign}` : null,
    t.stop_name ? `next stop ${t.stop_name}` : null,
    njtDelayText(t.delay) || null,
    positionClause(position),
  ]);
}

// Is this failure the "this deployment does not run NJ Transit" answer?
//
// The backend serves it as a 503 whose detail names the two environment variables,
// and refreshSource surfaces that detail as the source's error. A deployment without
// NJT credentials is a supported state rather than a fault (15a's F1 settled that,
// for the monitor; this is the same decision on the rider's status line), so it must
// read as nothing rather than as red.
//
// MATCHED ON THE WORDS, NOT ON THE STATUS, and that is the whole care in it: 503 is
// also what a WARMING cache answers, and a warming NJT is worth surfacing. The
// phrase is the backend's own ("NJ Transit is not configured"), pinned by a test on
// both sides so a reworded detail cannot silently start reading as an outage.
const NOT_CONFIGURED_RE = /is not configured/i;

function isNotConfigured(detail) {
  return typeof detail === "string" && NOT_CONFIGURED_RE.test(detail);
}

// The dwell rule for an NJT departure row is the ferry's rule exactly: count down
// to the ARRIVAL while it is still ahead, and to the DEPARTURE once the train is
// standing at the platform or the stop is an origin with no arrival at all. An
// alias rather than a second copy, because two copies of one rule drift and the
// drift is invisible: both surfaces would still render a plausible countdown.
const njtArrivalDisplay = ferryArrivalDisplay;

// NJT station arrivals popup HTML. FLAT AND CHRONOLOGICAL, with no bucket
// headings at all, because /api/njt-arrivals is flat and chronological: every row
// carries its own route and headsign, and a departure board reads by time. The
// other systems' renderers bucket by direction because their endpoints do.
//
// `colorFor(routeId)` resolves a route's badge colour and `nameFor(routeId)` its
// rider-facing name; systems/njt.js closes both over the /api/njt-routes tables,
// keeping this pure. Every feed-derived string is escaped.
//
// THE HEADSIGN IS THE LINE'S DESTINATION AND IT IS WHAT A BOARD SHOWS, so it takes
// the one text slot between the badge and the countdown; the route NAME appears
// there only when the feed gives no headsign, and the badge always carries the route
// id. (An earlier draft of this comment described a two-slot row the function has
// never rendered; njtRowLabel is the authority and it returns one string.) A row with
// neither still renders its countdown rather than being dropped: the train is real
// and the time is the thing the rider came for.
function njtArrivalsHtml(station, body, now, colorFor = () => RAIL_NEUTRAL_COLOR, nameFor = () => null, markHtml = "") {
  // Every row dated by the TripUpdates header and qualified by it (6.2).
  const board = boardFreshness("njt", body, now);
  const header =
    popupKickerHtml({ left: POPUP_SYSTEM_WORDS.njt }) +
    popupTitleHtml({ markHtml, text: station.name ?? station.id }) +
    boardLineHtml(boardSystemLine(board, stationArrivalsRows(body)));
  const rows = njtOrderedArrivals(body.arrivals, now);
  if (!rows.length) return `${header}<div class="arr-none">No trains</div>\n`;
  return (
    header +
    popupArrRowsHtml(
      rows.map((row) => {
        const color = colorFor(row.route_id);
        const display = njtArrivalDisplay(row, now);
        return {
          markHtml:
            `<span class="arr-badge" style="background:${color};color:${readableTextOn(color)}">` +
            `${esc(row.route_id || "?")}</span>`,
          // njtRowLabel returns PLAIN TEXT since MR5: the grid escapes its own label, and a
          // helper that escaped first would print "&amp;amp;" for a headsign with an ampersand.
          label: njtRowLabel(row, nameFor),
          extraHtml:
            (row.train_num ? ` <span class="popup-sub">${esc(row.train_num)}</span>` : "") +
            qualifierHtml(arrivalQualifier(row, board)),
          countdown: (display.mode === "departing" ? "departs " : "") + formatCountdown(display.seconds),
        };
      }),
    )
  );
}

// The one text a row shows between its badge and its countdown: the destination
// where the feed gives one, else the route's name, else nothing. Leading space
// included so the caller concatenates rather than deciding about spacing.
// THE ROWS ARE ORDERED BY THE NUMBER THE RIDER READS, which is not the order the
// backend serves them in, and the gap between the two is real rather than
// theoretical. feeds/njt._trim_njt_arrivals sorts on max(arrival, departure), so a
// train dwelling at the platform does not sort by an arrival already in the past;
// the countdown, though, is njtArrivalDisplay's, which counts to the ARRIVAL while
// it is still ahead. Two trains at one station with different dwell lengths make the
// two keys disagree, and a board whose numbers descend out of order is worse than
// one that reorders two rows: measured, a Dover row at 3 min printed above a Trenton
// row at 2 min, both in the order the endpoint served them.
//
// Sorted HERE rather than in the backend because the key is the display's, and the
// display's key depends on `now`, which the backend does not have at serve time. A
// row whose countdown is unknowable sorts last rather than first, where a NaN would
// otherwise put it.
function njtOrderedArrivals(rows, now) {
  const present = (rows || []).map((row, index) => ({ row, index }));
  return present
    .sort((a, b) => {
      const sa = njtArrivalDisplay(a.row, now).seconds;
      const sb = njtArrivalDisplay(b.row, now).seconds;
      const ka = sa == null || Number.isNaN(sa) ? Infinity : sa;
      const kb = sb == null || Number.isNaN(sb) ? Infinity : sb;
      // Stable on a tie, so two trains due the same minute keep the backend's order
      // rather than an order the sort happened to produce.
      return ka === kb ? a.index - b.index : ka - kb;
    })
    .map((entry) => entry.row);
}

function njtRowLabel(row, nameFor = () => null) {
  const r = row || {};
  const headsign = r.headsign || null;
  const routeName = r.route_id ? nameFor(r.route_id) : null;
  return headsign || routeName || "";
}

// ---- Service alerts in the station popups (phase 12b) ----

// Alerts staleness threshold. The alerts feed polls every 60s (vs 15s for the vehicle
// feeds) and its content changes slowly: a service alert persists for hours, so a
// slightly old alert index is far less misleading than slightly old vehicle
// positions. The honesty bar is therefore higher than the 90s feed bar. The alerts
// loop swallows failures by design (it never surfaces an error or blocks arrivals),
// so this marker is the one honest signal that the index may have stopped updating.
//
// WHAT THE 300s IS MEASURED AGAINST changed in C1, so read it as five missed BACKEND
// polls, not five missed client fetches. The comment here used to say "five missed
// polls" while the gate keyed on served_at, i.e. on the client's own fetches; it now
// keys on the backend's fetched_at, which advances only on a poll that decoded. Both
// cadences are 60s (ALERT_POLL_INTERVAL_S here and in pollers.py), so the count is
// unchanged, but the quantity is now the age of the DATA and includes any time the
// backend spent failing or timing out (up to REFRESH_DEADLINE_S per poll).
//
// THE PARTIAL CASE IS COVERED AS OF C2, and this is where the limit used to be
// documented. It read: fetched_at is poll-level, a poll where four of five feeds
// decode is a SUCCESS that advances it, so one system down for hours never tripped
// this marker even after the backend's retention cap had dropped that system's
// alerts entirely. That limit existed because /api/alerts carried nothing per
// system for the client to key on. It now carries a `systems` block, so the basis
// is the WORST (oldest) system's fetched_at rather than the envelope's: one down
// alert system trips the marker at the same threshold. See alertsFreshnessBasis.
const ALERTS_STALE_AFTER_S = THRESHOLD_OVERRIDES.alerts ?? PRODUCTION_ALERTS_STALE_AFTER_S;

// True when the alert DATA is older than the threshold, judged from the payload's
// fetched_at: the backend's last poll that actually decoded. `now` is the skew-
// corrected client clock, i.e. already on the same server-time axis as fetched_at.
// A null fetchedAt (no successful fetch yet, e.g. during boot) is NOT stale: the app
// simply shows no alerts, not a false "out of date". Pure and node-testable; the
// banner and popup alert blocks gate their "alerts may be out of date" marker on this.
//
// C1 CHANGED THE SIGNAL FROM served_at TO fetched_at, correcting the R1 choice.
// served_at is stamped at response build, so the served_at of a stale cache is fresh
// BY CONSTRUCTION: while the alert feeds were down the backend kept 200ing its frozen
// index with an ever-advancing served_at, which reset this gate on every poll. The
// marker therefore could not fire during the exact outage it exists to hedge. It was
// measuring DELIVERY (did a response arrive) when the honest question is about DATA
// (has the index been refreshed). fetched_at only advances on a poll that decoded,
// so a 200 whose fetched_at has not moved is precisely the outage signature.
// sinceAt is the fallback age basis for the case where the client has NEVER received a
// fetched_at: the instant it first tried. Without it, a null fetchedAt returned the
// healthy answer forever, so a backend whose alert index never filled (every feed down
// since boot, so /api/alerts serves an error and loadAlerts swallows it) showed riders
// a confident, alert-free map with no hedge, indefinitely. That is the same
// never-defaulted silence this whole change is about, just at the other end of the
// wire. A boot grace period is still right, so the null case ages against sinceAt on
// the same threshold rather than disclosing immediately; pass null for sinceAt to get
// the old unbounded-grace behavior.
// The alerts freshness basis extracted from an /api/alerts body: the backend's
// fetched_at, or null when a body omits it entirely. THE POINT OF THIS BEING A
// FUNCTION is that it makes the field CHOICE testable. A node test that hands
// alertsStale a fetched_at proves only arithmetic, because the test picked the field
// itself; a revert to served_at would sail past it. This is where the choice lives,
// so this is what a test has to pin.
//
// C2 MADE IT THE WORST SYSTEM'S fetched_at when the body carries a per-system block.
// The envelope's own fetched_at advances on any poll where at least one alert feed
// decoded, so it hid a partial outage completely (the F1 finding); the oldest system
// that HAS decoded is the honest basis, because the alert set a rider is looking at
// is only as current as its least-current contributor. Falls back to the envelope
// fetched_at when there is no systems block at all, and returns null only when
// nothing at all has decoded.
//
// A SYSTEM THAT HAS NEVER DECODED IS SKIPPED, NOT PROPAGATED AS NULL. REVIEW FIX:
// this first returned null as soon as any one system reported a non-numeric
// fetched_at, which threw away four known-old timestamps because a fifth was
// missing, and null re-bases the whole hedge on the client's first-attempt time.
// That was wrong in BOTH directions: a freshly loaded tab showed no hedge for the
// full threshold during a total freeze that the envelope timestamp used to disclose
// at once, and a tab open for hours latched the hedge permanently on a backend where
// four of five feeds were current. The backend really does serve that shape (a feed
// that fails its first poll of a process keeps fresh_at null while the others
// advance), so it is the common case, not a corner.
//
// KNOWN LIMIT, stated rather than papered over: a system that has never decoded
// contributes no age, so its alerts being entirely absent does not by itself trip
// the marker. Latching the marker forever on that signal is the behavior described
// above, and it contradicts the rule the status line applies to feeds (a system is
// not called out until its age crosses the threshold). It stays visible to an
// operator through degraded_systems on /api/status.
function alertsFreshnessBasis(body) {
  const systems = body == null ? null : body.systems;
  if (systems != null && typeof systems === "object") {
    const decoded = Object.values(systems)
      .map((system) => (system ?? {}).fetched_at)
      .filter((value) => typeof value === "number");
    if (decoded.length) return Math.min(...decoded);
    if (Object.keys(systems).length) return null; // a block, but nothing decoded yet
  }
  const fetchedAt = body == null ? null : body.fetched_at;
  return typeof fetchedAt === "number" ? fetchedAt : null;
}

function alertsStale(fetchedAt, now, sinceAt = null) {
  if (fetchedAt == null) {
    if (sinceAt == null) return false;
    return now - sinceAt >= ALERTS_STALE_AFTER_S;
  }
  return now - fetchedAt >= ALERTS_STALE_AFTER_S;
}

// Index the active-alerts list into two lookups, each keyed by "system|id": one by
// stop selector, one by route selector. WHY the key embeds the system: numeric ids
// collide ACROSS systems (LIRR route "1" vs subway route "1" vs MNR route "1"), so a
// join scoped only by id would leak alerts between modes. Every lookup below is
// therefore system-scoped.
function indexAlerts(alerts) {
  const byStop = new Map(); // "system|stop_id" -> [alert, ...]
  const byRoute = new Map(); // "system|route_id" -> [alert, ...]
  const push = (map, key, alert) => {
    const list = map.get(key);
    if (list) list.push(alert);
    else map.set(key, [alert]);
  };
  for (const alert of alerts ?? []) {
    for (const stop of alert.stops ?? []) push(byStop, `${alert.system}|${stop}`, alert);
    for (const route of alert.routes ?? []) push(byRoute, `${alert.system}|${route}`, alert);
  }
  return { byStop, byRoute };
}

// Shared deterministic order for an alerts list: open-ended (no end) first, then by
// starts_at (earliest first, a null start sorts first), then id. Reused by the
// station, route, and banner matchers so the ordering is identical everywhere.
function compareAlerts(a, b) {
  const aOpen = a.ends_at == null ? 0 : 1;
  const bOpen = b.ends_at == null ? 0 : 1;
  if (aOpen !== bOpen) return aOpen - bOpen;
  const aStart = a.starts_at ?? -Infinity;
  const bStart = b.starts_at ?? -Infinity;
  if (aStart !== bStart) return aStart - bStart;
  return String(a.id).localeCompare(String(b.id));
}

// Alerts affecting one station popup, deduped and sorted. An alert applies when
// alert.system === system AND either (a) the station's id is in alert.stops, or
// (b) alert.routes intersects `routeIds`, the routes serving this station. Everything
// is scoped by `system`, so a numeric id shared across modes never leaks.
//
// `routeIds` is the caller's union of the STATIC routes-per-station index (every
// route serving the stop, from stop_times, H5) and the routes present in the CURRENT
// arrivals. The static side closes the gap the old arrivals-only match left open: a
// route that serves the station but has no imminent train there (a suspended route, a
// long late-night headway, a between-trains moment) still surfaces its route-scoped
// alert, instead of relying on the stop-level selectors (a) to enumerate it.
//
// Deterministic sort so the block is stable across refreshes: open-ended alerts (no
// end) first, then by starts_at (earliest first, a null start sorts first), then id.
function matchStationAlerts(index, system, stationId, routeIds) {
  const matched = new Map(); // id -> alert; an alert matching by BOTH stop and route appears once
  for (const alert of index.byStop.get(`${system}|${stationId}`) ?? []) matched.set(alert.id, alert);
  for (const routeId of routeIds ?? []) {
    for (const alert of index.byRoute.get(`${system}|${routeId}`) ?? []) matched.set(alert.id, alert);
  }
  return [...matched.values()].sort(compareAlerts);
}

// Alerts for a route surface (a bus, subway train, or railroad train popup), from
// the SAME byRoute lookup, scoped by system so a numeric route id shared across
// modes never leaks. A null or missing route_id matches nothing. Deduped (an alert
// naming the route more than once appears once) and sorted like the station matcher.
function matchRouteAlerts(index, system, routeId) {
  if (!routeId) return [];
  const matched = new Map();
  for (const alert of index.byRoute.get(`${system}|${routeId}`) ?? []) matched.set(alert.id, alert);
  return [...matched.values()].sort(compareAlerts);
}

// Agency-wide alerts for the banner: those that name NO route and NO stop, across
// ALL systems, sorted the same way. A route-scoped or stop-scoped alert is excluded
// (it belongs on its route/station surface, not the banner), so nothing is ever
// double-shown. Takes the raw alerts list, since selector-less alerts appear in
// neither byStop nor byRoute.
function bannerAlerts(alerts) {
  return (alerts ?? [])
    .filter((a) => !(a.routes ?? []).length && !(a.stops ?? []).length)
    .sort(compareAlerts);
}

/* ---------------- The station alert join, once, for every surface ----------------

   F11 is what this section exists for. The map popup consulted the alert store and
   the station PANEL did not, so a station suspension was on one surface and absent
   from the other; on a phone the open panel makes the map inert, so the popup is not
   an equivalent source and the panel is the only text surface a rider has. The fix
   is not a second matcher for the panel. It is ONE matcher that both call, which is
   why the join moved here (requireable, node-testable, no DOM) out of the popup
   helper in systems/shared.js (browser-only, reached only through a rendered popup).

   Nothing about WHICH alerts match changed when it moved. The route-id union below
   is the same two-source union stationAlertsBlock has performed since H5; what is
   new is that its arrivals side now reads every body shape this app serves, which is
   the NJ Transit half of the finding. */

// Every arrivals ROW in a station body, whatever shape the endpoint serves it in.
//
// THREE SHAPES EXIST, and they are the endpoints' own rather than anything this
// function chose:
//   directions   subway, the railroads, PATH   { "Northbound": [row, ...], ... }
//   routes       ferry                          { "East River": [row, ...], ... }
//   arrivals     NJ Transit                     [row, ...], flat, no direction at all
// Before F11 only `directions` was read here. That is why ferry.js carried a
// hand-written copy of the join (passing the dock's static routes and skipping the
// arrivals side entirely) and why NJ Transit had no join at all: 15c's ledger
// recorded the gap as "teaching that helper the flat shape changes a function four
// other systems depend on", and deferred it. Reading all three is what lets both of
// them call the one function instead.
//
// DEFENSIVE ABOUT THE KEY IT READS, because `routes` names two different things in
// this codebase: a STATION's routes is an array of route ids (the H5 index), while a
// ferry BODY's routes is an object of buckets. An array here is not a bucket map and
// yields no rows rather than iterating a row object as if it were a list.
function stationArrivalsRows(body) {
  const payload = body ?? {};
  if (Array.isArray(payload.arrivals)) return payload.arrivals;
  const buckets = payload.directions ?? payload.routes;
  if (buckets == null || typeof buckets !== "object" || Array.isArray(buckets)) return [];
  const rows = [];
  for (const bucket of Object.values(buckets)) {
    if (Array.isArray(bucket)) rows.push(...bucket);
  }
  return rows;
}

// The routes a station's alerts are matched against: the UNION of the static
// routes-per-station index (station.routes, which the backend derives from
// stop_times, H5) and the routes present in the station's CURRENT arrivals.
//
// WHY BOTH, carried over verbatim from stationAlertsBlock because the reasoning did
// not change with the move: the static list is the complete, always-present set, so
// a route-scoped alert reaches the station even with no imminent train; the arrivals
// ids are folded in too so a station whose static routes failed to load still shows
// alerts for routes with a live train, and so a brand-new route running before the
// next static refresh is covered. Either source alone is a strict subset of the
// intent.
function stationAlertRouteIds(station, body) {
  const routeIds = new Set((station ?? {}).routes ?? []);
  for (const row of stationArrivalsRows(body)) {
    if (row && row.route_id) routeIds.add(row.route_id);
  }
  return routeIds;
}

// THE ONE STATION-ALERT MATCHER. The map popup (systems/shared.js stationAlertsBlock,
// which renders it as HTML) and the station panel (stations.js, which renders it as
// elements) both call this and neither owns a second copy of the rule. The two
// surfaces differ in how they PAINT the answer and must never differ in what the
// answer is, which is precisely the disagreement F11 recorded.
//
// A null system yields no alerts rather than matching everything: PATH and AirTrain
// publish no alerts feed at all, so there is nothing to join for them and an empty
// list is the honest answer. See stationAlertSystem.
function stationAlerts(index, system, station, body) {
  if (!system) return [];
  return matchStationAlerts(index, system, (station ?? {}).id, stationAlertRouteIds(station, body));
}

// The alert-feed key a station registry entry joins against, or null when its mode
// publishes no alerts feed.
//
// NOT THE ENTRY'S OWN `kind`, which is why this is a function and not a field read.
// The registry keys stations by MODE ("railroad"), while the alert index keys by FEED
// ("LIRR", "MNR"): the two railroads publish separate alert feeds and share one map
// layer, so the mode is ambiguous exactly where the join has to be exact.
//
// PATH AND AIRTRAIN RETURN NULL ON PURPOSE. feeds/alerts.py's ALERT_FEED_URLS has an
// entry for subway, bus, LIRR, MNR, ferry and njt, and none for either of them. A
// station surface that rendered an alerts area for a mode with no feed behind it
// would promise a rider that silence means no alerts, when it means no data.
const STATION_ALERT_SYSTEMS = { subway: "subway", ferry: "ferry", njt: "njt" };

// OWN KEYS ONLY, on both lookups here and in alertSourceNote. These keys come off a
// parsed payload (a registry entry's kind and system, and the alert body's per-system
// block), and a plain bracket read finds inherited properties: a `kind` of
// "constructor" would answer with Object's constructor rather than null. Nothing in
// the app produces such a key, and the point of a total function is that it does not
// depend on that staying true.
function stationAlertSystem(entry) {
  if (!entry) return null;
  if (entry.kind === "railroad") return entry.system ?? null;
  return Object.hasOwn(STATION_ALERT_SYSTEMS, entry.kind) ? STATION_ALERT_SYSTEMS[entry.kind] : null;
}

// What a station's alert SOURCE is currently doing, as a line to print, or "" when the
// set can honestly be shown as current. Two different admissions, because they are two
// different failures and a rider acts on them differently:
//
//   RETAINED  this system's alert feed is down and the backend is serving the alerts
//             it last decoded (feeds/alerts.py merge_alert_generations). The set is
//             not wrong, it is OLD, and the age is the part worth printing.
//   STALE     the feed's last decode is further back than ALERTS_STALE_AFTER_S. Same
//             wording as the popup marker, so the two surfaces read identically.
//
// SCOPED TO ONE SYSTEM, which is the difference from the popup marker and is
// deliberate. staleAlertsMarker ages against alertsFreshnessBasis, the MINIMUM across
// every alert system, because the banner it was written for is agency-wide: the set a
// rider sees there is only as current as its least-current contributor. A station
// panel shows ONE system, so the honest basis is that system's own last decode, and
// the envelope minimum would hedge a current NJ Transit board because the ferry feed
// is frozen. `basis` is that envelope-wide value, used only when this system has no
// block of its own (an /api/alerts body that predates the per-system block).
//
// A SYSTEM THAT HAS NEVER DECODED IS HEDGED HERE, and alertsFreshnessBasis skips it.
// That difference is also deliberate and it is the same reasoning: skipping is right
// when four other systems' timestamps are still describing the set, and wrong when
// the never-decoded system IS the set. A null fetchedAt therefore ages against
// sinceAt, the client's first attempt, on the same threshold.
const ALERTS_STALE_NOTE = "alerts may be out of date";
const ALERTS_RETAINED_NOTE = "alerts held from";

function alertSourceNote(systems, system, basis, now, sinceAt = null) {
  if (!system) return "";
  const table = systems ?? {};
  const block = Object.hasOwn(table, system) ? table[system] : null;
  if (block && block.retainedSince != null) {
    return `${ALERTS_RETAINED_NOTE} ${humanizeAge(now - block.retainedSince)} ago`;
  }
  const fetchedAt = block ? block.fetchedAt : basis;
  return alertsStale(fetchedAt, now, sinceAt) ? ALERTS_STALE_NOTE : "";
}

// A small deterministic string hash (FNV-1a, 32-bit, hex). Used only to keep the
// banner's dedup key bounded when it folds in alert TEXT; nothing security-relevant
// rides on it. Written out rather than pulled in so the frontend stays dependency
// free, and >>> 0 keeps every step unsigned (JS bitwise ops are signed 32-bit).
function hashString(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16);
}

// The banner's render-dedup key: the stale flag, then one line per shown alert
// carrying its system, id, and a hash of its header TEXT.
//
// C1 ADDED THE CONTENT HASH. The key used to be the shown ids alone, so an alert
// whose WORDING changed under the same id compared equal and the banner was left
// untouched, showing the superseded text indefinitely. That is a real upstream
// pattern: the MTA revises an ongoing incident's description in place rather than
// issuing a new id. Hashing rather than embedding the full text keeps the key short;
// the tradeoff is that a 32-bit collision between two wordings of the SAME id would
// still skip the re-render, which is a far smaller exposure than never re-rendering.
function bannerRenderKey(shown, stale) {
  return (
    (stale ? "S|" : "F|") +
    (shown ?? [])
      .map((a) => `${a.system}|${a.id}|${hashString(String(a.header ?? ""))}`)
      .join("\n")
  );
}

// Compact alerts block for a station popup: one escaped header line per alert, or ""
// when there is nothing to show (so the caller renders NO container at all). Header
// text only in this phase (description/effect omitted); the text is kept verbatim
// and escaped, so bracketed route tokens like [Q] render as plain text, no
// substitution. Alerts with no header contribute nothing.
function alertsBlockHtml(alerts) {
  const rows = shownAlerts(alerts).map((a) => `<div class="alert-row">${esc(a.header)}</div>`);
  if (!rows.length) return "";
  return `<div class="alert-block">${rows.join("\n")}</div>\n`;
}

// The alerts a surface actually SHOWS: those with a header to show. An alert with no
// header contributes nothing a rider can read, and the feeds really do publish them
// (header_text is optional in the GTFS-RT alert message).
//
// SHARED SO THE TWO RENDERERS CANNOT DIVERGE (F11). alertsBlockHtml dropped these and
// the panel's element renderer did not, which would have put a blank bullet on one
// surface and nothing on the other, for the same alert. Worse, the panel would have
// ANNOUNCED it: the change guard counts identities, so a headerless alert arriving
// would have said "new service alert for this station" over an empty list. Both
// renderers and the announcement now count the same set.
function shownAlerts(alerts) {
  return (alerts ?? []).filter((a) => a && a.header);
}

// ---- Static-loader retry (phase 12d) ----

// Retry fn until it resolves truthy, with doubling backoff from baseMs capped at
// capMs. A falsy resolution or a thrown error schedules the next attempt. WHY
// forever, with no attempt cap: the wrapped requests are cheap (the backend caches
// static payloads and serves 503/[] instantly while warming), and a map that never
// fills in is strictly worse than a slow retry hum in a background tab. WHY no
// jitter: jitter exists to de-synchronize a fleet of clients hammering a shared
// origin; here a handful of browsers each retry a cached endpoint every 30s at
// worst, so synchronized arrivals cost nothing and determinism keeps tests exact.
// `sleep` is injected so node tests resolve instantly and can assert the exact
// backoff sequence; the browser caller uses the default setTimeout sleep.
async function retryUntil(fn, { baseMs, capMs, sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
  let wait = baseMs;
  for (;;) {
    let ok = false;
    try {
      ok = await fn();
    } catch {
      // thrown = falsy: a fetch/parse error is just another "not yet" signal
    }
    if (ok) return;
    await sleep(wait);
    wait = Math.min(wait * 2, capMs);
  }
}

/* ==================================================================
   A1: the accessible station surface
   ==================================================================

   Pure logic for frontend/stations.js: a searchable list of every system's
   stations and a text rendering of one station's live arrivals. The rendering
   half deliberately consumes the SAME bucket ordering and countdown decision the
   map popups use, so the two surfaces cannot drift into describing the same feed
   differently. Everything here is pure and node-tested; the DOM lives in
   stations.js.

   This is also the architectural parent of the future terminal boards, so
   nothing below knows what a terminal is. */

// How many result rows the panel shows before it stops and says how many it is
// holding back. Long enough that a real search is rarely truncated, short enough
// that a screen reader is never handed hundreds of rows to walk.
const STATION_RESULT_CAP = 50;

// Fold a name to its comparison form: decomposed, diacritics stripped,
// lowercased. Station names carry accents in the wild, and a rider typing
// unaccented ASCII must still find them, which is why this is not just
// toLowerCase.
function foldStationName(name) {
  return String(name ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

// Split a query into folded tokens. Tokenizing (rather than one substring test on
// the whole query) is what makes "grand cen" match "Grand Central": every token
// must appear somewhere in the name, in any order.
function stationQueryTokens(query) {
  return foldStationName(query).split(/\s+/).filter(Boolean);
}

function stationMatchesTokens(foldedName, tokens) {
  return tokens.every((token) => foldedName.includes(token));
}

// Search the union of every system's stations.
//
// Returns {prompt, rows, total, hidden}. `prompt` true means the query was empty
// and the caller should show its "type to search" line rather than a list: 900
// rows is not a useful answer to no question, and it is a hostile one to walk
// with a keyboard.
//
// ORDERING is prefix-matches-first, then by name, then by system, so typing
// "grand" puts "Grand Central" above "East Grand Street" and the order is total
// (never dependent on the input order of the stop tables, which load
// asynchronously and in a race). `rows` is capped; `hidden` is how many matches
// were withheld, so the caller can say so honestly instead of silently
// truncating.
function searchStations(stations, query, cap = STATION_RESULT_CAP) {
  const tokens = stationQueryTokens(query);
  if (!tokens.length) return { prompt: true, rows: [], total: 0, hidden: 0 };
  const matched = [];
  for (const station of stations || []) {
    const folded = foldStationName(station.name ?? station.id);
    if (stationMatchesTokens(folded, tokens)) matched.push({ station, folded });
  }
  const first = tokens[0];
  matched.sort((a, b) => {
    const ap = a.folded.startsWith(first) ? 0 : 1;
    const bp = b.folded.startsWith(first) ? 0 : 1;
    if (ap !== bp) return ap - bp;
    if (a.folded !== b.folded) return a.folded < b.folded ? -1 : 1;
    const asys = a.station.systemLabel ?? "";
    const bsys = b.station.systemLabel ?? "";
    return asys < bsys ? -1 : asys > bsys ? 1 : 0;
  });
  return {
    prompt: false,
    rows: matched.slice(0, cap).map((m) => m.station),
    total: matched.length,
    hidden: Math.max(0, matched.length - cap),
  };
}

// The overflow line's text, or "" when nothing was withheld. Worded as an
// instruction rather than a count alone, because the useful thing to tell someone
// who cannot see the list is what to DO about it.
function stationOverflowLine(hidden) {
  if (!hidden) return "";
  return `${hidden} more ${hidden === 1 ? "station" : "stations"} match; keep typing to narrow`;
}

// ---- Arrivals, shaped once and rendered twice ----

// Turn one arrivals payload into the structure both the popup markup and the
// panel text are built from: {systemLine, buckets: [{name, rows: [...]}]}.
//
// `kind` selects the bucket source and ordering, and the four cases are exactly
// the four the popups already implement: subway and railroad and PATH bucket
// body.directions (subway by compass, the others by their own orders), ferry
// buckets body.routes by name. The orderings come from the SAME helpers the
// popups call, so a change to either is a change to both.
//
// Each row carries what a rider needs and nothing derived from the clock except
// `seconds` and `qualifier`: routeId, routeName (resolved by the caller), trainNum,
// mode ("arriving" or "departing", ferry and NJT only), seconds-until, and since 6.2
// the row's qualifier words and their kind (arrivalQualifier), which changes only at
// the threshold, never as the age counts on. Keeping the clock out of the identity
// fields is what lets announcementWorthy below tell a real change from a tick.
const SUBWAY_BUCKET_ORDER = ["Northbound", "Southbound"];

function shapeStationArrivals(kind, body, now, opts = {}) {
  const nameFor = opts.nameFor || (() => null);
  const payload = body || {};
  let raw;
  if (kind === "ferry") {
    raw = orderedFerryBuckets(payload.routes);
  } else if (kind === "railroad") {
    raw = orderedRailroadBuckets(payload.directions);
  } else if (kind === "path") {
    raw = orderedPathBuckets(payload.directions);
  } else if (kind === "njt") {
    // ONE BUCKET, because /api/njt-arrivals is a flat chronological list with no
    // direction on it at all. The panel renders a bucket heading per bucket, so
    // the single name is what a rider reads above the list, and "Departures" is
    // what NJ Transit calls that board. An empty list yields NO bucket rather
    // than an empty one, which is how every other kind reaches the panel's
    // "No trains." line.
    // Ordered by the displayed countdown, the same way the popup renders them, so
    // the two surfaces cannot list one station's board in two different orders.
    raw = (payload.arrivals || []).length
      ? [["Departures", njtOrderedArrivals(payload.arrivals, now)]]
      : [];
  } else {
    raw = orderedBuckets(SUBWAY_BUCKET_ORDER, payload.directions);
  }
  // THE SAME BOARD FRESHNESS THE POPUP READS, so the two surfaces qualify a row from
  // one computation and cannot word or threshold it differently (6.2).
  const board = boardFreshness(kind, payload, now);
  const buckets = raw.map(([name, rows]) => ({
    name,
    rows: (rows || []).map((row) => {
      // NJT rows carry the same arrival/departure pair the ferry's do, so they take
      // the same dwell rule (njtArrivalDisplay is that rule under its NJT name).
      const display =
        kind === "ferry" || kind === "njt" ? ferryArrivalDisplay(row, now) : null;
      const seconds = display ? display.seconds : row.arrival - now;
      const qualifier = arrivalQualifier(row, board);
      return {
        routeId: row.route_id ?? null,
        routeName: row.route_id ? nameFor(row.route_id) : null,
        trainNum: row.train_num ?? null,
        // The destination, where the endpoint publishes one. Only NjtArrival does
        // (checked across every arrivals model), so this is null on every other
        // kind and the sentence below is unchanged for them; it is read generically
        // rather than gated on kind because a future endpoint that starts carrying
        // a headsign should reach the rider, not a branch nobody remembered.
        headsign: row.headsign ?? null,
        mode: display ? display.mode : "arriving",
        seconds,
        // The ABSOLUTE instant this row is about, kept alongside the countdown
        // rather than derived from it later. Two consumers need it and both would
        // otherwise reconstruct it as now + seconds: the clock label in the
        // sentence, and announcementWorthy, which can only tell a real change
        // from a tick by comparing absolute times.
        at: seconds == null || Number.isNaN(seconds) ? null : now + seconds,
        qualifier: qualifier.words,
        qualifierKind: qualifier.kind,
      };
    }),
  }));
  return {
    // THE BOARD'S SYSTEM LINE, which replaced ageSeconds (6.2). ageSeconds was
    // now - fetched_at, the age of our POLL, and it was the whole of the panel's age
    // judgement: silent while a provider served old content to a poll that kept
    // succeeding, which is F03. Rows now carry their own qualifiers, and this line
    // speaks only for what they cannot (boardSystemLine). Null when it has nothing.
    systemLine: boardSystemLine(board, stationArrivalsRows(payload)),
    buckets,
  };
}

// The wall-clock label for an arrival instant, in NEW YORK time regardless of
// where the browser is: the rider is asking about a New York train, and a
// countdown plus a clock time in a different zone is worse than no clock time.
// The zone is a parameter so the node tests can pin an exact string instead of
// asserting against the runner's locale.
function clockTimeLabel(epochSeconds, timeZone = "America/New_York") {
  if (epochSeconds == null || Number.isNaN(epochSeconds)) return "";
  return new Date(epochSeconds * 1000).toLocaleTimeString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  });
}

// One arrival as a sentence a screen reader can read straight through.
//
// The shape is "<route> <noun> <countdown>, <clock> <what the clock is>":
// "Babylon train in 4 minutes, 8:12 AM arrival". Route NAME is preferred over
// route id because "Babylon" is what a rider calls it and "5" is not; the id is
// the fallback. The noun and verb carry the mode, so a ferry dwelling at its dock
// says "departs" rather than implying it is still inbound.
//
// THE CLOCK LABEL SAYS WHICH INSTANT IT IS, and for most systems that is the
// ARRIVAL, not the departure. The phase spec's example sentence reads "8:12
// departure", but the subway, railroad and PATH arrivals endpoints carry an
// `arrival` field and no departure, so calling it a departure would be a small
// lie of exactly the kind this codebase spends its comments avoiding. Ferry rows
// DO carry a departure and say so when that is the instant being counted down.
//
// The train number, where the feed has one, goes last as an aside: it identifies
// the train for someone who cares and is noise for everyone else.
function arrivalSentence(row, noun = "train", timeZone = "America/New_York") {
  const label = row.routeName || row.routeId || "";
  const countdown = spokenCountdown(row.seconds);
  const departing = row.mode === "departing";
  // The destination rides between the label and the noun ("Morris & Essex Line to
  // Dover train in 4 minutes"), which is the order a departure board reads in and
  // the order the popup renders. Absent on every kind but NJT, so every other
  // sentence is byte-identical to what it was.
  const destination = row.headsign ? `to ${row.headsign}` : "";
  const parts = [label, destination, noun, departing ? "departs" : "", countdown].filter(Boolean);
  let sentence = parts.join(" ");
  const clock = clockTimeLabel(row.at, timeZone);
  if (clock) sentence += `, ${clock} ${departing ? "departure" : "arrival"}`;
  // THE QUALIFIER BESIDE THE TIME IT QUALIFIES (6.2), before the train number's aside,
  // so "as of 10m ago" is heard about the prediction rather than about the train's
  // identity. Absent on a fresh row, so every fresh sentence is exactly what it was.
  if (row.qualifier) sentence += `, ${row.qualifier}`;
  return row.trainNum ? `${sentence}, train ${row.trainNum}` : sentence;
}

// ---- Live region discipline ----

// How far a bucket's next arrival must move before it is worth interrupting
// someone to say so. Under a minute is prediction jitter: the feeds revise
// arrival estimates by a few seconds on every poll, and announcing that is how a
// live region becomes something a rider turns off.
const ANNOUNCE_LEAD_SHIFT_S = 60;

// A bucket's comparable signature: which routes are present (as a SORTED
// multiset, so a reordered payload is not a change) and when the next one
// arrives. Sorted rather than sequential is deliberate: the backends do not
// promise a stable row order, and announcing on a reshuffle of the same trains
// would be noise.
function arrivalsSignature(shaped) {
  const buckets = {};
  for (const bucket of (shaped && shaped.buckets) || []) {
    // THE IDENTITY IS WHAT THE RIDER CAN SEE, which is route plus train number
    // where the feed carries one. That choice decides the swapped-lead case: a
    // railroad train 8412 replaced by 8414 at nearly the same minute changes the
    // rendered sentence, so it is news and this key changes with it. The same
    // swap on the subway, where no train number exists and every "1" train reads
    // identically, changes nothing a rider could perceive, so the key is stable
    // and the live region stays quiet. Announcing an invisible identity change
    // would be indistinguishable from noise to the person listening.
    const routes = bucket.rows
      .map((r) => `${r.routeId ?? "?"}|${r.trainNum ?? ""}`)
      .sort();
    // Lead arrival as the ABSOLUTE instant the shaped row already carries.
    // Comparing absolute times is what makes a tick a non-event: the same train
    // an hour from now is the same instant on every tick, while `seconds` counts
    // down by one each time.
    const leads = bucket.rows.map((r) => r.at).filter((t) => t != null);
    // WHICH QUALIFIERS THE BUCKET CARRIES, as a sorted set of kinds, never their words
    // (6.2). "aged" stays "aged" while its age counts up, so neither the tick nor the
    // refresh re-announces it. A SET rather than one kind per row, because on a board
    // whose provider dates each trip (LIRR, PATH) rows cross the threshold one at a
    // time all day, and announcing each crossing would be the chatter this function
    // exists to prevent. THE FRESH KIND ("") IS A MEMBER, on purpose: it is what tells
    // "some countdown here is still current" from "none is", so the bucket is spoken
    // when its LAST current row goes old, which is the moment a rider who heard a
    // current countdown needs to hear otherwise (a stalling provider shows up exactly
    // there, because the current row is usually the lead train). The cost, measured in
    // review: a bucket whose only current row flips back and forth on its provider's
    // own refresh cadence is spoken on each flip.
    const kinds = [...new Set(bucket.rows.map((r) => r.qualifierKind || ""))].sort();
    buckets[bucket.name] = { routes, lead: leads.length ? Math.min(...leads) : null, kinds };
  }
  return buckets;
}

// Should the live region speak?
//
// Announce when the ARRIVALS changed in a way a rider would care about:
//   1. a bucket appeared or vanished (a direction started or stopped running),
//   2. a bucket's set of routes changed (a train appeared, vanished, or the next
//      one is on a different line),
//   3. a bucket's next arrival moved by more than ANNOUNCE_LEAD_SHIFT_S (the
//      wait got materially longer or shorter),
//   4. (6.2) a bucket's set of qualifier KINDS changed, or the board's system line
//      appeared or went: a countdown the rider heard as current is now known to be
//      old, or is current again. Announced once, because a kind does not change as
//      its age counts up.
//
// Stay silent on everything else, and the case that matters most is the
// countdown tick: none of clauses 1 to 3 reads the clock, so a second passing can
// never trip them. Without that, a screen reader narrates "4 minutes... 3
// minutes..." forever, which is hostile enough that the rider disables the feature
// and loses the arrivals with it. CLAUSE 4 IS THE EXCEPTION, and deliberately: a
// row's kind flips from "" to "aged" once, when its age crosses the threshold, and an
// empty board's system line appears the same way, on time alone. What keeps a TICK
// from speaking that flip is stations.js (speakPanel drops the arrivals half on a
// tick, and announceArrivals advances nothing on one), so the next non-tick render
// speaks it once (A1v4). A caller outside that tick-guarded path must not assume a
// second passing cannot trip this function.
//
// `prev` and `next` are shaped payloads. A first render (no prev) announces once,
// because the arrivals appearing IS the news.
function announcementWorthy(prev, next) {
  if (!next) return false;
  if (!prev) return true;
  if ((prev.systemLine == null) !== (next.systemLine == null)) return true; // clause 4
  const before = arrivalsSignature(prev);
  const after = arrivalsSignature(next);
  const names = new Set([...Object.keys(before), ...Object.keys(after)]);
  for (const name of names) {
    const a = before[name];
    const b = after[name];
    if (!a || !b) return true; // clause 1: a bucket came or went
    if (a.routes.length !== b.routes.length) return true; // clause 2
    for (let i = 0; i < a.routes.length; i++) {
      if (a.routes[i] !== b.routes[i]) return true; // clause 2
    }
    if ((a.kinds || []).join("|") !== (b.kinds || []).join("|")) return true; // clause 4
    if (a.lead == null !== (b.lead == null)) return true;
    if (a.lead != null && b.lead != null && Math.abs(b.lead - a.lead) > ANNOUNCE_LEAD_SHIFT_S) {
      return true; // clause 3
    }
  }
  return false;
}

/* ---------------- A2: what a marker is called ---------------- */

// THE NAMES ON THE MAP. Every one of these builds from the SAME fields its system's
// popup renders, so a marker and its popup can never describe different trains. They
// are pure and take their lookups by injection, which is what lets the node tests pin
// the wording instead of asserting against a live map.
//
// They are also deliberately plain sentences rather than the popup's shorthand. A
// popup can afford "Next stop:" as a label above a value because it is laid out in
// two dimensions; a name is read as one line of speech, so it has to be a sentence a
// person would say. The rule for every builder below: name the vehicle, say where it
// is going or what it is doing, and stop. No trip ids, no coordinates, no counts.
//
// NOTHING HERE READS THE CLOCK OR THE FRESHNESS INDEX. A stale SYSTEM's marker is
// already dimmed and its popup already carries the age line, and folding a system's
// "as of 4m ago" into the name would make every label change on a timer, which is the
// announcement problem A1 solved and has no business coming back through the marker
// layer. 6.3 ADDS ONE CLAUSE THAT IS HANDED IN, NOT READ: `position`, the answer
// positionQualifier gave for the row's OWN observation, whose spoken form says how the
// position was obtained and, once that observation is past OBS_FRESH_S, how old it is
// ("live GPS, as of 5m ago"). Section 3.2 says a marker must carry that word, and the
// name is the marker a screen reader reaches. It is written when a poll re-applies the
// name AND by the animation tick's stale sweep, which is the site that wakes when one
// observation crosses OBS_FRESH_S between polls (systems/shared.js calls
// applyStaleTreatment on observationCrossed, and every sweep re-names): a marker that
// dims for its own age has to say why at the same moment, or the name and the pixels
// disagree until the next poll. A marker is not a live region, so neither write
// announces anything, which is what makes the tick-driven one safe. REVIEW FIX: this
// paragraph said "never by a timer", which was false of the very commit that added the
// clause. Its words are the popup's own, said aloud, which is the A2 rule.

// Join the parts of a name, dropping the empty ones, so a missing field leaves no
// double comma and no dangling "to".
function joinName(parts) {
  return parts.filter((part) => part != null && part !== "").join(", ");
}

// "1 train, next stop Times Sq-42 St, Northbound, scheduled position, no GPS". route_id
// is the same bullet the icon shows; an unknown route says so rather than reading the
// literal "?" glyph. Every subway train is placed from its trip update, and since 6.3 the
// name says so, as the popup does.
function subwayTrainName(train, position = null) {
  const t = train || {};
  const route = t.route_id ? `${t.route_id} train` : "Subway train";
  const stop = t.stop_name || t.stop_id;
  return joinName([route, stop ? `next stop ${stop}` : null, t.direction || null, positionClause(position)]);
}

// "MNR" is what the feed calls it and what the popup prints; "Metro-North" is what a
// rider calls it, and what the A1 station panel already says. A name that is going to
// be SPOKEN uses the rider's word, because an initialism is read letter by letter.
function railroadSystemLabel(system) {
  return system === "MNR" ? "Metro-North" : system || "Railroad";
}

// "Metro-North Hudson, train 8801, next stop Grand Central, scheduled position, no
// GPS". Built from the same FIELDS as the popup head, but NOT from formatRailroadHead
// itself: that helper joins with a middot, which is a visual separator doing a job
// that punctuation cannot do in speech (a screen reader reads it as noise, or as the
// words "middle dot"). Same facts, spoken shape. The position clause is here for the
// same reason it is in the popup: it tells a rider how much to trust the position they
// are being told about. It is `position.spoken`, the popup's compact line said aloud,
// and unlike every other system's name it is said for EVERY position, "live GPS"
// included, because the railroad popup has always said it. The next stop is said when
// the row names one, exactly when the popup prints it: a train drawn from a prediction
// names the stop it is at or heading for, and a GPS fix names none.
function railroadTrainName(train, routeName = null, position = null) {
  const t = train || {};
  const system = railroadSystemLabel(t.system);
  const head = routeName ? `${system} ${routeName}` : t.route_id ? `${system} route ${t.route_id}` : system;
  return joinName([
    head,
    t.train_num ? `train ${t.train_num}` : null,
    t.stop_name ? `next stop ${t.stop_name}` : null,
    t.direction || null,
    position ? position.spoken : null,
  ]);
}

// "Newark - World Trade Center, PATH, next stop Grove St, to Newark, scheduled position,
// no GPS". PATH serves every train `placed`, which the popup states and the name
// repeats, from the served provenance rather than as a constant, so a train whose own
// trip update is old says so in both.
function pathTrainName(train, routeName = null, position = null) {
  const t = train || {};
  return joinName([
    formatPathHead(t.route_id, routeName),
    "PATH",
    t.stop_name ? `next stop ${t.stop_name}` : null,
    t.direction || null,
    positionClause(position),
  ]);
}

// "East River, NYC Ferry, boat H201, at dock". ferryStatusText is the popup's own
// wording. The boat label is the rider-visible hull name, not the feed's vehicle id. A
// fresh fix adds nothing, and one past OBS_FRESH_S adds "live GPS, as of 2m ago", as the
// popup does.
function ferryBoatName(boat, routeName = null, position = null) {
  const b = boat || {};
  // ferryStatusText returns null for a status the feed did not give or we do not
  // recognise, and the popup omits its line entirely in that case. The name does the
  // same: saying "under way" about a boat whose status is unknown would be inventing
  // the one fact a rider is actually asking about.
  const status = ferryStatusText(b.status);
  return joinName([
    routeName || "Unassigned route",
    "NYC Ferry",
    b.label ? `boat ${b.label}` : null,
    status ? status.toLowerCase() : null,
    positionClause(position),
  ]);
}

// "M15 bus, heading east" or "M15 bus, heading unknown". The bearing is spoken as a
// COMPASS POINT, not as degrees: the marker's whole visual job is the arrow, and "142
// degrees" is a number a rider has to convert while standing at a stop. A bus's own old
// fix adds its age, as the popup does.
function busName(bus, position = null) {
  const b = bus || {};
  const route = b.route_id ? `${b.route_id} bus` : "Bus";
  return joinName([route, `heading ${compassPoint(b.bearing)}`, positionClause(position)]);
}

const COMPASS_POINTS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"];

// Degrees to one of eight compass points. Wraps, so 350 and -10 both read "north".
function compassPoint(bearing) {
  if (bearing == null || Number.isNaN(Number(bearing))) return "unknown";
  const step = 360 / COMPASS_POINTS.length;
  const index = Math.round(Number(bearing) / step);
  return COMPASS_POINTS[((index % COMPASS_POINTS.length) + COMPASS_POINTS.length) % COMPASS_POINTS.length];
}

// "Federal Circle, AirTrain JFK station". AirTrain stations are the one STATION with a
// DOM element to name (every other system's stations are circleMarkers drawn on a
// shared canvas, which has no element and therefore no place to put a name; those
// stations are reachable as text through the A1 panel instead).
function airtrainStationName(station) {
  const s = station || {};
  return joinName([s.name || "AirTrain station", "AirTrain JFK station"]);
}

/* ---------------- A2: when the page itself should speak ---------------- */

// The page-level equivalent of announcementWorthy, and it follows the same rule A1
// settled: judge a TRANSITION in underlying state, never a rendered string. The status
// line contains a clock and rewrites itself every fifteen seconds by construction, so
// anything comparing its text would announce forever.
//
// THE UNIT OF JUDGEMENT IS SET MEMBERSHIP, not a count and not a string. The identity
// is "<sourceKey>|<systemName>", exactly the key the C2 freshness index already uses,
// so what a rider hears is derived from the same numbers the status line and the
// marker dimming read. Counting would be wrong in a way that shows up precisely during
// a spreading incident: LIRR going stale while MNR recovers leaves the count at one
// and says nothing, when two things a rider cares about just changed.

// Which (source, system) identities are degraded right now. Accepts the freshness
// index as a Map or a plain object so callers and tests can pass either.
function degradedIdentities(freshnessIndex) {
  const entries =
    freshnessIndex instanceof Map
      ? [...freshnessIndex.entries()]
      : Object.entries(freshnessIndex || {});
  return entries
    .filter(([, entry]) => entry && (staleAge(entry.age) || neverDecoded(entry)))
    .map(([key]) => key)
    .sort();
}

// A system with NO age at all, which its own source reports as down. This is not a
// healthy system and it is not merely a stale one: it has never produced data.
//
// THE REVIEW FOUND THIS BY REPRODUCTION, and the failure was the worst shape available.
// A backend restart while a feed is still failing republishes that system with
// fetched_at null (the previous value it would have carried forward is gone with the
// process). A null age is not >= the staleness threshold, so the system silently LEFT
// the degraded set, and the page announced "Live data current again" at the exact
// moment its trains disappeared from the map. It then never re-entered the set, so the
// one surface that exists to say otherwise stayed quiet for as long as the outage
// lasted. A rider was told a dead system was fine, once, and never corrected.
//
// The status line never had this bug: staleness() has always separated a `blind` set
// (no age AND not ok) from the stale one. This makes the spoken judgment read the same
// two fields the visible one does, which is the invariant that matters: the page must
// not say one thing and speak another.
function neverDecoded(entry) {
  return entry.age == null && entry.ok === false;
}

// The rider-facing word for a source, and the word to use when a source's system is
// only the source over again. Railroads take no qualifier in front of a real system
// name, because "LIRR" and "Metro-North" are already what a rider calls them and
// "Railroad LIRR" is the kind of phrase only a schema would produce. But they still
// need a WHOLE word for the case below, and getting that wrong is not hypothetical:
// the first draft announced "Live data delayed for railroads", lowercase and plural,
// straight out of the payload key.
//
// AND THE RAILROAD SYSTEM NAME GOES THROUGH railroadSystemLabel, which the review found
// missing here. Every other spoken surface says "Metro-North"; this one said "MNR",
// straight out of the feed, into the only region a screen reader reads aloud. It is the
// same defect railroadSystemLabel was written to prevent, one surface later. "LIRR" is
// unchanged by that helper, because LIRR is what a rider calls it.
const SOURCE_WORDS = {
  buses: { qualifier: "Bus", whole: "Bus" },
  subways: { qualifier: "Subway", whole: "Subway" },
  railroads: { qualifier: null, whole: "Railroad", spoken: railroadSystemLabel },
  path: { qualifier: "PATH", whole: "PATH" },
  ferry: { qualifier: "Ferry", whole: "Ferry" },
  // NJ Transit is one system named after its source, so describeIdentity returns
  // `whole` and the qualifier is never reached. It is still filled in rather than
  // left null: an entry missing here falls through to the raw payload key, which
  // is how "Live data delayed for railroads" reached the live region once already.
  njt: { qualifier: "NJ Transit", whole: "NJ Transit" },
};

function describeIdentity(identity) {
  const [source, system] = String(identity).split("|");
  const words = SOURCE_WORDS[source] || { qualifier: source, whole: source };
  // A source with no per-system block synthesizes ONE system named after itself
  // (ingestSystems), so "buses|buses" is just the buses and naming it twice would be
  // noise. The railroads payload does this whenever its systems block is absent, which
  // is the path that produced the defect above.
  if (system === source || !system) return words.whole;
  const spoken = words.spoken ? words.spoken(system) : system;
  return words.qualifier ? `${words.qualifier} ${spoken}` : spoken;
}

// Join names the way a person would say them.
function sentenceList(names) {
  if (names.length <= 1) return names[0] || "";
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names.slice(0, -1).join(", ")}, and ${names[names.length - 1]}`;
}

// The status announcement, or null for silence. `prev` of null is the FIRST
// OBSERVATION: it seeds state and says nothing, because a page load must not read its
// own condition aloud before the rider has asked for anything.
//
// Silent by construction on: age ticks (an identity already in the set stays in it as
// it gets older), re-renders, and any change that alters the formatted line without
// altering membership. Recovery is worth one sentence, because a rider who was told
// the data was delayed is owed the news that it is not.
function statusAnnouncement(prev, next) {
  if (!next) return null;
  if (!prev) return null;
  const before = new Set(prev);
  const after = new Set(next);
  const entered = next.filter((key) => !before.has(key));
  const left = prev.filter((key) => !after.has(key));
  if (!entered.length && !left.length) return null;
  const clauses = [];
  if (entered.length) clauses.push(`Live data delayed for ${sentenceList(entered.map(describeIdentity))}`);
  if (left.length) clauses.push(`Live data current again for ${sentenceList(left.map(describeIdentity))}`);
  return `${clauses.join(". ")}.`;
}

// The banner's announcement. The identity of an alert is its id AND a hash of its
// wording, which is the same content-hash approach the C1 banner dedup fix
// established after an alert whose text was revised in place under an unchanged id
// left the banner showing superseded wording indefinitely. So a reworded alert is a
// new identity here too, and is announced once.
//
// Deliberately NOT sensitive to: ordering (the set is compared, and the render key is
// built from a sorted list), a refresh carrying identical alerts, and the staleness
// marker. That last one matters: the "may be out of date" flag is visual honesty
// about the feed, not news about the transit system, and folding it in would announce
// every time the alerts feed crossed its threshold with nothing having happened.
function alertIdentities(alerts) {
  return (alerts || [])
    .map((a) => `${a.system}|${a.id}|${hashString(String(a.header ?? ""))}`)
    .sort();
}

function bannerAnnouncement(prev, next) {
  if (!next) return null;
  if (!prev) return null; // first observation seeds silently
  const before = new Set(prev);
  const appeared = next.filter((key) => !before.has(key));
  // An alert CLEARING is not announced: the rider is not told about the absence of an
  // emergency, and the strip disappearing is the signal. Only new or revised alerts
  // are worth interrupting for.
  if (!appeared.length) return null;
  // A SUMMARY, NEVER THE BODY. The banner and the alerts block carry the wording; a
  // live region that read a full service alert aloud would be unusable during exactly
  // the incident it exists for.
  return appeared.length === 1
    ? "New service alert."
    : `${appeared.length} new service alerts.`;
}

// The PANEL's equivalent, for the alert set of the SELECTED station (F11). Same
// identity keys, same summary-not-body rule, and one deliberate difference: this one
// also speaks when an alert CLEARS.
//
// WHY THE TWO DIFFER, since a reader will otherwise read it as an oversight in one of
// them. bannerAnnouncement stays silent on a clear because the banner is a persistent
// strip over the map whose disappearance is itself the signal, and because nobody
// needs to be interrupted to be told an emergency is over. The panel's block has
// neither property: it lives INSIDE a detail subtree that renderStationDetail
// replaces wholesale every second, so there is no element whose removal a rider
// perceives, and on a phone the open panel makes the map inert, so this is the only
// surface saying anything at all. A rider who was told this station has a suspension
// changed their plan on that basis and is owed the retraction.
//
// A SUMMARY, NEVER THE BODY, exactly as the banner: the block carries the wording and
// a live region that read a full service alert aloud would be unusable during the
// incident it exists for. The FIRST observation seeds silently, so selecting a
// station that already has alerts renders them without an interruption; the arrivals
// announcement names the station on selection and this rides alongside it.
function stationAlertAnnouncement(prev, next) {
  if (next == null || prev == null) return null;
  const before = new Set(prev);
  const appeared = next.filter((key) => !before.has(key));
  if (appeared.length) {
    return appeared.length === 1
      ? "New service alert for this station."
      : `${appeared.length} new service alerts for this station.`;
  }
  const after = new Set(next);
  const cleared = prev.filter((key) => !after.has(key));
  if (!cleared.length) return null;
  return next.length
    ? "A service alert for this station has cleared."
    : "Service alerts for this station have cleared.";
}

/* ---------------- A2: motion ---------------- */

// THE ONE MOTION GATE. Returns false when the rider has asked their system for
// reduced motion.
//
// THE PRINCIPLE, and it is the whole reason this is a gate and not a feature flag:
// reduced motion changes HOW a position updates, never WHAT is shown. A gliding train
// and a stepping train are at the same place at the same time; one interpolates
// between polls and the other jumps when the truth arrives. Nothing here may hide a
// marker, drop a poll, freeze data, or change any text. If a change would make the
// map say something different rather than move differently, it does not belong behind
// this gate.
//
// matchMedia is injected so the node tests can drive both answers, and so the callers
// that need to REACT to a change (see motionPreferenceListener) share one definition
// of the query with the callers that only read it once.
const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function motionAllowed(mql = null) {
  const query = mql || (typeof matchMedia === "function" ? matchMedia(REDUCED_MOTION_QUERY) : null);
  if (!query) return true; // no matchMedia (node, ancient browser): animate as before
  return !query.matches;
}

// Watch the preference for CHANGES, so a rider who turns reduced motion on does not
// have to reload to be believed. Returns an unsubscribe function.
//
// WHAT THIS CANNOT REACH, and it is stated here rather than discovered later: Leaflet
// reads its zoomAnimation, fadeAnimation and markerZoomAnimation options ONCE, when the
// map is constructed, and offers no supported way to change them afterwards. So a
// mid-session flip takes effect immediately for everything this app owns (the marker
// glide, the css transitions, the panel) and only at the next page load for Leaflet's
// own zoom and pan animations. Poking at map.options after construction would leave the
// handlers Leaflet already installed running against a lie, which is a worse failure
// than the honest limitation. The README says the same thing in a rider's words, since
// the person affected is a user rather than a maintainer.
//
// addEventListener is guarded because MediaQueryList only grew it in Safari 14; the
// older addListener is deliberately NOT used as a fallback, because a browser that old
// predates the app's other requirements anyway and a silent no-op is better than a
// deprecated path nobody tests.
function watchMotionPreference(onChange, mql = null) {
  const query = mql || (typeof matchMedia === "function" ? matchMedia(REDUCED_MOTION_QUERY) : null);
  if (!query || typeof query.addEventListener !== "function") return () => {};
  const handler = () => onChange(!query.matches);
  query.addEventListener("change", handler);
  return () => query.removeEventListener("change", handler);
}

/* A4: VANISHING FOCUS, the decision half.

   WHAT VANISHES AND WHY IT IS THE POPUP. A rider's focus can never be inside a marker
   element: the factory builds every marker with keyboard:false, so there is no tabindex
   and no tab stop, and A2 pinned that as the marker exclusion policy. Popups, though,
   live in Leaflet's popupPane as a SEPARATE subtree, and every popup contains at least
   Leaflet's own close button. So the thing that can be destroyed under a rider's fingers
   is the popup, and the same is true of the alert banner's dismiss button.

   THE PREDICATE IS THE ONE THE POPUP-REFRESH FIX PROVED OUT: did the subtree that is
   about to be destroyed contain document.activeElement? Not "is a popup open", not "did
   a vehicle leave" - the question is only ever whether the rider was holding something
   that is going away. That is what makes this silent in the common case: a layer toggle
   destroys every marker in a group, but the rider's focus is on the checkbox they just
   activated, so the predicate is false and nothing is said. The announcement is earned by
   a TRANSITION in the rider's own state, which is the same worthiness rule the live
   regions have followed since A1.

   Kept pure and here so node can test it without a DOM: the caller passes the subtree and
   the currently focused element, and gets back the decision plus the wording. */
function vanishingFocusPlan(subtree, active, { label = null, kind = "vehicle", reason = null } = {}) {
  if (!subtree || !active) return { rescue: false, message: null };
  const inside = subtree === active || (typeof subtree.contains === "function" && subtree.contains(active));
  if (!inside) return { rescue: false, message: null };
  return { rescue: true, message: vanishingFocusMessage(kind, label, reason) };
}

/* The wording. The decisions block gave "The train you were following left the feed" and
   "Alerts cleared"; the vehicle half is built from the marker's OWN accessible name
   rather than from a hardcoded noun, because this app carries buses, boats and PATH
   trains as well as subway trains and a fixed "train" would be false for most of them.
   The name's leading clause is exactly the vehicle identity ("1 train", "M15 bus",
   "Rockaway ferry"), because that is how buildMarkerName composes it.

   Both sentences name where focus went. That is not decoration: a rider who was reading a
   popup and is silently moved somewhere else has lost their place, and "focus moved to
   the map" is the one piece of orientation that makes the move recoverable.

   6.3 ADDS ONE REASON, "withheld" (withheldFix): a railroad fix the position ladder
   stopped drawing for its age is still in the feed, so "left the feed" would be false of
   it while the status line counts it as not shown. The sentence borrows the status line's
   account of the same train ("shown", and "last seen over 10m ago" with OBS_MAX_S through
   humanizeAge), so the two surfaces cannot word one fact two ways. */
function vanishingFocusMessage(kind, label, reason = null) {
  if (kind === "alerts") return "Alerts cleared. Focus moved to the map.";
  const lead = typeof label === "string" && label.trim() ? label.split(",")[0].trim() : null;
  const subject = lead ? `The ${lead} you were following` : "The vehicle you were following";
  if (reason === "withheld") {
    return `${subject} is no longer shown, last seen over ${humanizeAge(OBS_MAX_S)} ago. Focus moved to the map.`;
  }
  return `${subject} left the feed. Focus moved to the map.`;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    vanishingFocusPlan,
    vanishingFocusMessage,
    esc, routeColor, lineColor, staleness, emptyFeedDecision, noteClockOffset,
    formatCountdown, trainLatLng, polylineCumLengths, pointAtArcLength, projectOntoRoute,
    computeRouteSlice, orderedRailroadBuckets,
    railroadArrivalsHtml, ROUTE_ACCEPT_DIST, ROUTE_MAX_SLICE,
    indexAlerts, matchStationAlerts, matchRouteAlerts, bannerAlerts, alertsBlockHtml,
    hashString, bannerRenderKey,
    RAILROAD_ROUTE_MAX_SLICE, RAILROAD_ROUTE_ACCEPT_DIST, RAILROAD_BUCKET_ORDER,
    LINE_COLORS, FEED_STALE_AFTER_S, FETCH_DEADLINE_MS, staticPayloadHasField, shouldRefresh,
    // MR2: the subway's drawing decisions, pure so the map and the Key read one answer.
    YELLOW_TRUNK_ROUTES, isYellowTrunk, trunkDrawOrder,
    RIBBON_CASING_WEIGHT, RIBBON_CASING_OPACITY, RIBBON_LINE_WEIGHT, RIBBON_LINE_OPACITY,
    FOCUS_DIM_LINE, FOCUS_DIM_CASING, FOCUS_DIM_TRAIN, focusOpacity, isOffFocus,
    routeFocusAnnouncement, routeFocusLabel,
    // MR2 round 2: the key derived from the data, and focus as membership.
    SUBWAY_KEY_ALIASES, compareRouteIds, subwayRouteUniverse, bulletRouteIds, drawnRouteIds,
    ribbonRouteSet, focusRoutesForBullet, bulletTrackSet, bulletDrawsSomething, bulletTitle, subwayKeyModel,
    STATION_LOCAL_RADIUS, STATION_TRANSFER_RADIUS, STATION_TRANSFER_WEIGHT,
    isTransferStation, stationTrunks, stationMarkStyle, stationLabelClass,
    LABEL_HUB_ZOOM, LABEL_ALL_ZOOM, LABEL_NO_HUB_ZOOM, labelZoomBand, stationLabelShown,
    namesToggleAnnouncement, namesToggleTitle,
    // A3: one luminance path for the whole app.
    parseColor, relativeLuminance, contrastRatio, readableTextOn, readableInk, statusLineText,
    POPUP_SURFACE_FALLBACK,
    statusNoteText, FEEDS, feedDotState, feedTooltip, feedStripModel, themeChoice, nextTheme,
    // MR5 (Q2): the state's own words, and the popup footer that says them the strip's way.
    feedStateWords, popupFreshHtml,
    // MR5: the popup's vocabulary, and the subway plate that became a builder so a title
    // could draw the map's own mark.
    POPUP_SYSTEM_WORDS, POPUP_MARK_TITLE, POPUP_MARK_ROW, popupMarkHtml, popupKickerHtml,
    popupTitleHtml, popupRowsHtml, popupDirHtml, popupArrRowsHtml, railroadHeadParts,
    subwayPlateSvg,
    MOBILE_MAX_WIDTH_PX, MOBILE_QUERY, narrowViewport,
    INK_LIGHT, INK_DARK,
    humanizeAge, alertsStale, alertsFreshnessBasis, ALERTS_STALE_AFTER_S,
    ingestSystems, systemAges, systemStaleAts, staleAge, markerOpacity, glideClock,
    // 6.2: the door reads the content clock, and the envelope's clocks enter with it.
    ingestEnvelope, contentClock, systemLag,
    // 6.2: the boards, each row qualified by its own age.
    UNDATED_SYSTEMS, boardSystem, servedAge, boardFreshness, arrivalQualifier,
    boardSystemLine, boardLineHtml, qualifierHtml,
    // 6.3: a vehicle's position, qualified by its own observation, and its rendering.
    OBS_MAX_S, observationAge, observationStaleAt, positionQualifier,
    POSITION_STEP_KEYS, positionSteps, positionBoard, markerAge, glideDeadline, glideAnchored,
    drawnFromPrediction, railroadHollow, railroadAtItsStation, withheldFix, positionWords, positionClause,
    // MR3: the commuter rail grammar. Pure, so the state table can be asked one row at a
    // time and the tag's markup read as a string rather than off a screenshot.
    RAIL_BRANCH_CODES, RAIL_NEUTRAL_COLOR, RAIL_AGENCY, railBranchCode, railBranchColor, railBranchInk, railBranchPaint, RAIL_INK_TARGET,
    RAIL_TAG_HEIGHT, railTagGeometry, railTagState,
    segmentBearing, railTrainBearing, RAIL_HEX,
    railTagSvg, railTagChevronPath, railStationSvg, RAIL_STATION_BOX, RAIL_STATION_SQUARE,
    // MR4: the other four families' marks, pure so each state can be asked in node.
    busMarkColor, busMarkColorAt, busMarkHue, BUS_MARK_SATURATION,
    BUS_MARK_LIGHTNESS, BUS_MARK_LIGHTNESS_DARK, BUS_MARK_LIGHTNESS_TOKEN,
    pathDiamondSvg, PATH_DIAMOND_BOX, PATH_DIAMOND_PATH,
    ferryHullSvg, FERRY_HULL_BOX, FERRY_HULL_PATH,
    ferryDockStyle, FERRY_DOCK_COLOR, FERRY_DOCK_RADIUS, FERRY_DOCK_STROKE,
    airtrainLineStyle, AIRTRAIN_LINE_DASH, AIRTRAIN_LINE_WEIGHT,
    busMarkSvg, busHasHeading, BUS_MARK_BOX, BUS_ARROW_PATH, BUS_DOT_R,
    railLabelBand, RAIL_LABEL_ZOOM, ferryLabelBand, FERRY_LABEL_ZOOM,
    railroadStationName, railFamilyClass,
    AGE_UNKNOWN, observationDimAge, observationGated, OBSERVATION_GATED,
    composeAnnouncements, withheldTrains, withheldClause,
    thresholdOverrides, CONTRACT_FLAG_PARAM,
    STALE_MARKER_OPACITY, FERRY_DOCKED_OPACITY,
    selectHeadwayBand, airtrainStationPopupHtml, retryUntil,
    PATH_BUCKET_ORDER, PATH_FALLBACK_COLOR, orderedPathBuckets, pathColor,
    formatPathHead, pathTrainPopupHtml, pathArrivalsHtml,
    PATH_ROUTE_MAX_SLICE, PATH_ROUTE_ACCEPT_DIST, computePathRouteSlice,
    FERRY_FALLBACK_COLOR, orderedFerryBuckets, ferryArrivalDisplay, ferryBoatIconState,
    ferryStatusText, ferrySpeedKnots, ferryBoatPopupHtml, ferryArrivalsHtml,
    // 15c: NJ Transit Rail.
    NJT_FALLBACK_COLOR, njtColor, njtRouteColor, njtRouteName, njtKey, formatNjtHead,
    njtRouteTables, njtTrainPopupHtml, njtDelayText, njtTrainName,
    njtStationName,
    njtArrivalDisplay, njtArrivalsHtml, njtRowLabel, njtAtItsStation, njtGlideTrain,
    njtOrderedArrivals, isNotConfigured,
    // A1: the accessible station surface.
    countdownParts, spokenCountdown, STATION_RESULT_CAP, SUBWAY_BUCKET_ORDER,
    foldStationName, stationQueryTokens, stationMatchesTokens, searchStations,
    stationOverflowLine, shapeStationArrivals, arrivalSentence, clockTimeLabel,
    ANNOUNCE_LEAD_SHIFT_S, arrivalsSignature, announcementWorthy,
    // A2: map semantics and the interaction floor.
    joinName, subwayTrainName, railroadTrainName, pathTrainName, ferryBoatName,
    busName, compassPoint, airtrainStationName, COMPASS_POINTS, railroadSystemLabel,
    degradedIdentities, neverDecoded, describeIdentity, sentenceList, statusAnnouncement,
    alertIdentities, bannerAnnouncement, shownAlerts,
    // F11: the station alert join, shared by the map popup and the station panel.
    stationArrivalsRows, stationAlertRouteIds, stationAlerts, stationAlertSystem,
    alertSourceNote, stationAlertAnnouncement,
    ALERTS_STALE_NOTE, ALERTS_RETAINED_NOTE, STATION_ALERT_SYSTEMS,
    motionAllowed, watchMotionPreference, REDUCED_MOTION_QUERY,
    // A4: the popup-clearing geometry.
    boxesOverlap, shiftBox, popupClearingShift, POPUP_CLEAR_GAP,
    clampedAutoPanPadding, popupAutoPanWant, POPUP_AUTOPAN_GAP, POPUP_AUTOPAN_WANT,
    POPUP_OPTIONS,
  };
}
