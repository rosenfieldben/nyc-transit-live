// Shared map infrastructure for the ordered-script frontend: the Leaflet map
// and every layer group, the toggle wiring, the status line, the reusable station
// popup machinery (used by subway, railroad and PATH stations), the service-alert
// index and banner, and the shared train-animation loop. Loaded as a plain
// <script> right after helpers.js and before the per-system files, so its
// top-level const/let bindings are in the shared global scope they all read (the
// same buildless model helpers.js -> map.js already uses; no bundler).

/* ---------------- A2: motion ---------------- */

// THE PRINCIPLE, and every gate below serves it: REDUCED MOTION CHANGES HOW A POSITION
// UPDATES, NEVER WHAT IS SHOWN. A gliding train and a stepping train are at the same
// place at the same time; one interpolates between polls and the other jumps when the
// truth arrives. Nothing behind this gate may hide a marker, skip a poll, freeze data,
// or change a single word of text. If a change would make the map SAY something
// different rather than MOVE differently, it does not belong here.
//
// Read once here for Leaflet, because Leaflet reads these options at construction and
// has no supported way to change them afterwards; watchMotionPreference in helpers.js
// carries the same limitation in full, and the README states it for riders.
const motionAtLoad = motionAllowed();

const map = L.map("map", {
  zoomAnimation: motionAtLoad,
  fadeAnimation: motionAtLoad,
  markerZoomAnimation: motionAtLoad,
}).setView([40.7128, -74.006], 12);

// Everything this app owns follows the preference LIVE. One class on the root element
// drives every css transition (see the reduced-motion rules in style.css), and one flag
// drives the glide in animateTrains, so a rider who turns the preference on mid-session
// is believed immediately rather than at their next reload.
let motionOn = motionAtLoad;

function applyMotionPreference(allowed) {
  motionOn = allowed;
  document.documentElement.classList.toggle("reduced-motion", !allowed);
}

// THE PAN IS NOT A CONSTRUCTOR OPTION, and that is exactly why the first version of
// this gate missed it. Leaflet has no map-level switch for pan animation the way it has
// for zoom and fade: panBy animates unless a caller passes animate:false, and the
// callers that matter are inside Leaflet. Popup._adjustPan calls map.panBy with no
// options whenever an opening popup would overflow the viewport, which the A2
// cross-link triggers on purpose, so opening a popup near an edge slid the entire map
// (tiles, every marker, every route line) for ~280ms. The review measured it as
// IDENTICAL with the preference on and off: 13 distinct map centres either way. That is
// full-field motion, and it is a larger motion source than the train glide the gate
// already stops.
//
// Wrapping panBy is the narrowest place that covers every caller, ours and Leaflet's
// own, including panTo and the animated branch of setView, which both route through it.
// It changes only HOW the map arrives at a position, never WHICH position: an
// unanimated pan lands on exactly the same centre.
//
// A4 ROUND 1: ANIMATE THE JOURNEY, NEVER THE ADJUSTMENT.
//
// This is the principle the map's motion now follows, and it splits the pans into two kinds
// that had been treated as one:
//
//   A JOURNEY is navigation the rider chose. Picking a station in the panel pans the map to
//   it (syncMapToStation's panTo), and the motion there carries CONTINUITY: it shows the
//   rider that this new place is that old place, moved. Journeys keep their preference gate,
//   animated unless the rider asked for reduced motion. A5g and A5h pin that pair.
//
//   AN ADJUSTMENT is the app correcting its own fit. Leaflet's _adjustPan nudging an opening
//   popup back inside the viewport is one, and so is this phase's move of a popup out from
//   under the legend. Nobody asked for it, it carries no continuity, and its ENDPOINT MUST BE
//   KNOWABLE: the app's own occlusion logic reads where the popup came to rest, and it cannot
//   read a position that is still moving. Adjustments are instant for everyone.
//
// So Leaflet's autoPan is unanimated regardless of preference, which is a rider-visible
// change from A2 and is deliberate. Measured at 1280 with the placed railroad popup:
//
//     real clock   popup settles at x 1001..1276, overlapping the legend at 1030
//     fixed clock  popup never lands at all, x 1288..1563, off the map's right edge
//
// The second row is the one that makes this structural rather than aesthetic: PosAnimation
// drives itself off `+new Date()`, so under a fixed clock (which the accessibility gate
// needs for deterministic ages) the animation never completes and the map is left mid-slide
// forever. Instant is the only setting under which the popup has a position at all, for the
// app's occlusion logic first and for any test second.
let leafletAutoPanning = false;
map.on("autopanstart", () => {
  leafletAutoPanning = true;
});
const leafletPanBy = map.panBy.bind(map);
map.panBy = (offset, options) => {
  const instant = !motionOn || leafletAutoPanning;
  leafletAutoPanning = false; // consumed: autopanstart fires immediately before its own panBy
  return leafletPanBy(offset, instant ? { ...(options || {}), animate: false } : options);
};

applyMotionPreference(motionAtLoad);
watchMotionPreference(applyMotionPreference);

/* ----- A3: the legend disclosure, and the one place the breakpoint is read ----------
   The legend collapses under 700px and is always open above it. Both facts live here
   rather than being split between a CSS rule and a click handler, because the two would
   drift: a rider who rotates a phone into landscape crosses the breakpoint without any
   click, and the version of this that only listened for clicks left the legend hidden
   on a screen with room for it.

   ARIA AND THE ATTRIBUTE ARE SET TOGETHER, always, so what a screen reader is told and
   what is drawn cannot disagree. Above the breakpoint the button is display:none, and
   aria-expanded is reported as true, because the legend genuinely is expanded there.

   FOCUS STAYS ON THE BUTTON. The panel expands in place, so there is nowhere to send
   focus and nothing is destroyed; this is deliberately NOT the popup or banner case
   where a control is replaced. */
const legendToggleEl = document.getElementById("legend-toggle");
const legendEl = document.getElementById("legend");
let legendOpen = false;

function applyLegendDisclosure() {
  if (!legendToggleEl || !legendEl) return;
  const narrow = narrowViewport();
  const open = narrow ? legendOpen : true;
  legendEl.hidden = !open;
  legendToggleEl.setAttribute("aria-expanded", String(open));
}

if (legendToggleEl) {
  legendToggleEl.addEventListener("click", () => {
    legendOpen = !legendOpen;
    applyLegendDisclosure();
  });
}
applyLegendDisclosure();
if (typeof matchMedia === "function") {
  const mql = matchMedia(MOBILE_QUERY);
  if (mql.addEventListener) mql.addEventListener("change", applyLegendDisclosure);
}

// Station dots get their own canvas pane sandwiched between the route lines
// (overlayPane, 400) and the train/bus markers (markerPane, 600), so the
// station canvas — not the route-line canvas it overlaps — receives clicks.
map.createPane("stationPane");
map.getPane("stationPane").style.zIndex = 450;

L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

// Buses and subways live in separate layer groups so they toggle independently.
// Route lines are vectors (canvas), which Leaflet draws beneath marker panes.
const busLayer = L.layerGroup().addTo(map);
const subwayLayer = L.layerGroup().addTo(map);
const routeLinesLayer = L.layerGroup().addTo(map);
const busRouteLayer = L.layerGroup().addTo(map); // the one clicked bus route
const stationLayer = L.layerGroup().addTo(map);
const railroadLayer = L.layerGroup().addTo(map); // LIRR + MNR GPS markers
const railroadRouteLinesLayer = L.layerGroup().addTo(map); // LIRR + MNR route geometry
const railroadStationLayer = L.layerGroup().addTo(map); // LIRR + MNR clickable stations
// AirTrain JFK is static-only (no realtime feed exists). Its own layers so it
// toggles independently of the railroad group.
const airtrainRouteLinesLayer = L.layerGroup().addTo(map); // 3 branch guideways
const airtrainStationLayer = L.layerGroup().addTo(map); // 10 clickable stations
// PATH gets its own three groups (mirroring the railroad trio) so the whole
// system toggles as one.
const pathRouteLines = L.layerGroup().addTo(map); // route geometry, both directions per route
const pathStations = L.layerGroup().addTo(map); // 13 clickable parent stations
const pathTrains = L.layerGroup().addTo(map); // trains gliding between (or placed at) stations
// NYC Ferry gets its own three groups (the same trio shape) so the whole system
// toggles as one: route geometry, clickable docks, and live GPS boats.
const ferryRouteLines = L.layerGroup().addTo(map); // route geometry, modal polyline per direction
const ferryDocks = L.layerGroup().addTo(map); // clickable landing docks
const ferryBoats = L.layerGroup().addTo(map); // live GPS boat markers

/* A4: LEAFLET'S POPUP CLOSE BUTTON IS AN ANCHOR TO NOWHERE, and page-wide scanning is
   what finally saw it. Leaflet renders
     <a class="leaflet-popup-close-button" role="button" aria-label="Close popup" href="#close">
   and axe reports it as a skip-link violation, "No skip link target", because an anchor
   with a fragment href is a link to an element that has to exist and `#close` never does.
   No scoped scan could ever have caught it: popups were outside every root A1 through A3
   included.

   The role and the name are already right, so the defect is only the vestigial href, which
   Leaflet carries to make the anchor focusable and clickable. Removing it costs the focus
   stop, so tabindex replaces it, and it costs keyboard activation, because a browser
   synthesises a click from Enter for LINKS and not for role=button anchors: that is what
   the keydown handler restores. Space is included because a rider who has been told this
   is a button will try it.

   Done on popupopen rather than by replacing the node, because Leaflet keeps its own
   reference to the button and rebuilds the popup element lazily; the guard flag is because
   Leaflet reuses that element across opens and a second listener would close the popup on
   one press and then try again on nothing. */
/* A4: A POPUP MUST NOT OPEN UNDERNEATH THE PAGE'S OWN CHROME, and page-wide scanning is
   what proved it was happening. axe reported the popup's content as undecidable,
   "background color could not be determined because it is overlapped by another element".
   Measured at 375x667: the popup occupied x 255..345 y 19..70 while #panel occupied
   x 140..365 y 10..285, and document.elementFromPoint at all nine sample points across the
   popup returned #stations-toggle or #legend-toggle. Not partially covered: entirely
   covered. A rider who opened a popup near the top of a phone screen saw the legend.

   THE STACKING FIX DOES NOT WORK, and it is worth recording why so nobody retries it.
   Leaflet's popupPane is z-index 700 and #panel is 1000, so raising the pane looks like a
   one-line answer. It is not: .leaflet-map-pane is itself positioned with z-index 400, which
   makes it a stacking context, so every pane inside it is capped at 400 relative to anything
   outside. Measured: the pane's computed z-index really was 1001 and the hit test still
   returned the legend's controls. Moving the popup to a pane outside the map pane would
   escape the cap and break worse, because a pane outside .leaflet-map-pane does not receive
   the map's transform and the popup would stop following the map.

   SO THE MAP PANS, which is what Leaflet already does when a popup would fall off the
   viewport edge; this extends the same idea to edges Leaflet cannot see.

   THE FIRST VERSION WAS WRONG IN THREE WAYS AND THE ADVERSARIAL ROUND MEASURED ALL THREE.
   It is rewritten rather than adjusted, because each error came from deciding something in
   advance that only measurement can answer.

   1. IT READ THE POPUP TOO EARLY. The old comment claimed popupopen "reads a settled
      position". False for the popups that matter: a station popup is bound with empty
      content and filled by a fetch, so at popupopen it is 29px tall, the correction is
      computed on that, and the arrivals then land and grow it back over the legend.
      Measured at 375 on the panel's own sync path: popupopen saw h=29, settled measured
      top 169.65 bottom 321 against #panel bottom 284.5, still overlapping. So the trigger
      is a ResizeObserver on the popup element as well as popupopen, and the correction
      runs again whenever the content changes size.

   2. IT ONLY EVER MOVED DOWN. Down is right at 375, where #panel is 275px of a 667px
      viewport. At 1280 the legend spans y 10..710 of a 720px map, so nothing fits below it
      and the guard bailed every time: the mechanism never fired at the width where the
      popup is largest. Measured on a real station popup at desktop, six of nine sample
      points across it returned legend-row. The clear space at desktop is to the LEFT of
      x=1030, not below. So the direction is no longer chosen in advance: all four are
      costed and the cheapest one that actually clears everything wins.

   3. IT KNEW ABOUT ONE OBSTACLE. The old guard's only other constraint was the map's
      height, so a downward move could push a tall popup under the alert banner, which is
      z-index 1001 and paints over the popup pane exactly as the legend does. Measured with
      a 300px popup at 375: pan down 368.5, popup bottom 620, banner top 595.4, the bottom
      25px covered and not hit-testable. The banner is now an obstacle like any other.

   UNANIMATED, AND THAT IS A DECISION RATHER THAN A DEFAULT. Everything else on this map
   goes through the A2 panBy wrapper, which animates unless the rider asked for reduced
   motion. This one does not, because it is not a journey: it is a correction of where the
   popup already landed, and animating a correction shows the rider the wrong position
   first and then slides the whole field away from it. */

// The chrome that paints over the popup pane. Both are siblings of #map with a z-index
// above .leaflet-map-pane's stacking context, which is the property that makes them
// obstacles rather than just neighbours. Listed rather than derived, so adding a third
// overlay is a deliberate edit here and not a silent regression.
const POPUP_OBSTACLE_IDS = ["panel", "alert-banner"];

function popupObstacles() {
  return POPUP_OBSTACLE_IDS.map((id) => document.getElementById(id))
    .filter((el) => el && !el.hidden)
    .map((el) => el.getBoundingClientRect())
    // A zero-size box is the dismissed banner, which reserves no space and blocks nothing.
    .filter((box) => box.width > 0 && box.height > 0);
}

function panPopupClearOfChrome(popup) {
  const root = popup && popup.getElement ? popup.getElement() : null;
  const container = document.getElementById("map");
  if (!root || !container) return false;
  const shift = popupClearingShift(root.getBoundingClientRect(), popupObstacles(), container.getBoundingClientRect());
  if (!shift) return false;
  // panBy moves the VIEW, so the content moves the other way: to move the popup left by d
  // the map pans right by d. Measured rather than assumed: panBy([200, 0]) moved a popup
  // from x 234 to x 34.
  map.panBy([-shift.dx, -shift.dy], { animate: false });
  return true;
}

/* THE TRIGGER, WHICH IS THE HALF THE FIRST VERSION GOT WRONG. popupopen alone is too early
   for any popup whose content arrives later, and every station popup is one. A
   ResizeObserver on the popup element catches both moments with one mechanism: it fires
   once when the element is first laid out and again when the fetched content changes its
   size. It is disconnected on popupclose so a closed popup's corpse cannot keep panning
   the map during its fade.

   AND IT ONLY EVER CORRECTS A POSITION IT SET ITSELF, which round 2 had to teach it. The
   observer outlives the opening, so a background arrivals refresh that changes the popup's
   height re-ran the correction on a map the RIDER had since moved, and threw their position
   away. Measured at 375: the rider dragged the map to centre lat 40.65134, a refresh grew
   the popup, and the map jumped to 40.72996.

   That is the same principle as the pan animation split, one level up. An adjustment is the
   app correcting its own fit; the moment the rider takes over, the position is theirs and
   the app has no business tidying it. So the last centre this code set is remembered, and if
   the map is not still there when the observer fires, the correction stands down for good.
   Remembered as the centre rather than as a "did the rider drag" flag, because that catches
   every way the map can move without asking us, including keyboard panning and zoom, and it
   needs no Leaflet internals to do it. */
let popupClearObserver = null;
let popupClearCentre = null;

const sameCentre = (a, b) => !!a && !!b && a.lat === b.lat && a.lng === b.lng;

map.on("popupopen", (event) => {
  const root = event.popup && event.popup.getElement ? event.popup.getElement() : null;
  if (popupClearObserver) popupClearObserver.disconnect();
  panPopupClearOfChrome(event.popup);
  popupClearCentre = map.getCenter();
  if (!root || typeof ResizeObserver !== "function") return;
  popupClearObserver = new ResizeObserver(() => {
    // Guarded on the popup still being the open one: Leaflet keeps the element alive
    // through the close fade, and a resize during that fade must not move the map.
    if (map._popup !== event.popup || !map.hasLayer(event.popup)) return;
    if (!sameCentre(map.getCenter(), popupClearCentre)) return; // the rider owns it now
    panPopupClearOfChrome(event.popup);
    popupClearCentre = map.getCenter();
  });
  popupClearObserver.observe(root);
});

map.on("popupclose", () => {
  popupClearCentre = null;
  if (!popupClearObserver) return;
  popupClearObserver.disconnect();
  popupClearObserver = null;
});

/* A4 ROUND 1: CLOSING A POPUP THE RIDER IS STANDING IN HAS A FOCUS CONTRACT, and until the
   adversarial round it did not. Measured at 1280 with focus on the close button:

     Escape          -> {"active":"BODY","announced":""}
     the close button-> {"active":"BODY","announced":""}
     next Tab        -> #stations-skip, so the rider restarted at the top of the page

   Two reviewers found it independently, and the shape of the miss is worth recording. A4
   built the vanishing-focus door for exactly this outcome, then A4's own Escape ladder
   created a new path to it, and escape.spec.js asserted only which surface was open, never
   where the rider ended up. A spec suite that checks state and not focus will pass over a
   stranding every time.

   QUIETLY, WHICH IS THE DIFFERENCE FROM THE VANISHING DOOR. That door announces because the
   rider did not ask for anything and the thing they held disappeared. Here the rider pressed
   Escape or the close button: the move is the expected consequence of their own action, and
   narrating it every time would be noise. So focus lands on the map container with nothing
   said, which is the same contract A1 gave the panel.

   ONE HELPER, TWO CALL SITES, and the split is honest: the ladder owns the key and the
   button owns the click, but the DECISION about focus lives in one place. The plan is taken
   BEFORE the popup is destroyed, because afterwards activeElement is already BODY and the
   question cannot be asked, which is the lesson the banner rebuild taught in deliverable 2. */
function closePopupReturningFocus(popup) {
  if (!popup) return false;
  const el = popup.getElement ? popup.getElement() : null;
  const held = !!(el && document.activeElement && el.contains(document.activeElement));
  map.closePopup(popup);
  if (!held) return true;
  const container = document.getElementById("map");
  if (container) container.focus();
  return true;
}

map.on("popupopen", (event) => {
  const root = event.popup && event.popup.getElement ? event.popup.getElement() : null;
  const button = root ? root.querySelector(".leaflet-popup-close-button") : null;
  if (!button || button.dataset.a11yCloseFixed) return;
  button.dataset.a11yCloseFixed = "1";
  button.removeAttribute("href");
  button.setAttribute("tabindex", "0");
  button.addEventListener("keydown", (key) => {
    if (key.key !== "Enter" && key.key !== " ") return;
    key.preventDefault();
    button.click();
  });
  // CAPTURE AND stopImmediatePropagation, because Leaflet binds its own close handler to
  // this same button and a plain stopPropagation does not stop a sibling listener on the
  // target. Leaflet's handler is map.closePopup(popup) and nothing else, so replacing it
  // costs no cleanup; what it buys is that every close path, mouse and keyboard alike,
  // goes through the one helper that knows about focus.
  button.addEventListener(
    "click",
    // Named `press` rather than `event`: the outer parameter is the popupopen event and the
    // popup it carries is the whole point of this handler, so shadowing it silently turned
    // event.popup into undefined and the close button stopped closing anything. A9j caught
    // it in the same run it was written.
    (press) => {
      press.preventDefault();
      press.stopImmediatePropagation();
      // THE POPUP THAT OWNS THIS BUTTON, not map._popup. They are the same in this app
      // today, because Leaflet auto-closes the previous popup when a new one opens, so a
      // second live popup is not reachable. It is still wrong to ask the map which popup is
      // current when the button already knows: the handler is bound per popup and closing
      // "whichever is current" is a bug waiting for the first feature that opens two.
      closePopupReturningFocus(event.popup);
    },
    true,
  );
});


function bindToggle(checkboxId, layers) {
  const box = document.getElementById(checkboxId);
  const sync = () => {
    for (const layer of layers) {
      if (box.checked) map.addLayer(layer);
      else map.removeLayer(layer);
    }
  };
  box.addEventListener("change", sync);
  sync(); // some browsers restore checkbox state across reloads without firing change
}
bindToggle("toggle-buses", [busLayer, busRouteLayer]);
bindToggle("toggle-subways", [subwayLayer, routeLinesLayer]);
bindToggle("toggle-stations", [stationLayer]);
bindToggle("toggle-railroads", [railroadLayer, railroadRouteLinesLayer, railroadStationLayer]);
bindToggle("toggle-airtrain", [airtrainRouteLinesLayer, airtrainStationLayer]);
bindToggle("toggle-path", [pathRouteLines, pathStations, pathTrains]);
bindToggle("toggle-ferries", [ferryRouteLines, ferryDocks, ferryBoats]);

const statusEl = document.getElementById("status");

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

/* ---------------- A2: the page's one live region ---------------- */

// THE PAGE DOOR. The status line and the alert banner are the two surfaces outside the
// station panel that can have something to say, and BOTH must speak through here.
//
// WHY ONE DOOR AND NOT A GUARD AT EACH SURFACE. This is the same reasoning as the
// marker factory owning keyboard:false, and as A1's announceUnlessTick before it:
// three guarded copies drift, and the one that drifts is the one nobody notices,
// because a live region failing is silent by definition. A1 learned this the
// expensive way when a copied tick guard was missing from one branch and a leaked
// timer walked straight through the gap. One function, one element, no other writer.
//
// POLITENESS IS DELIBERATE. aria-live="polite" for both: alerts are decorative by this
// project's philosophy (they never gate the arrivals a rider came for) and the status
// line is ambient. Nothing on this page is worth cutting off whatever a rider is
// currently reading, so nothing here is assertive.
//
// A NOTE FOR A FUTURE PATH THAT DOES NOT EXIST YET: if either surface is ever hidden
// and later shown again, re-announcing the CURRENT state on return is correct, not
// duplicate. The A1 panel settled the same question: while a region is out of the
// accessibility tree it cannot have been heard, so returning is a first observation
// again rather than a repeat. Today nothing hides these two, which is exactly why the
// rule belongs in writing before something does.
const pageAnnounceEl = document.getElementById("page-announce");

function announcePage(text) {
  if (!pageAnnounceEl || !text) return false;
  pageAnnounceEl.textContent = text;
  return true;
}

// What the page last knew, so worthiness is judged against the previous OBSERVATION
// rather than against whatever happens to be on screen. Null means nothing has been
// observed yet, which is what makes the first poll silent.
let announcedDegraded = null;
let announcedAlerts = null;

// Called from the poll tail with the freshness index this tick produced. Pure decision
// in helpers.js; this is only the plumbing that remembers and speaks.
function announceStatusTransition(freshnessIndex) {
  const next = degradedIdentities(freshnessIndex);
  const text = statusAnnouncement(announcedDegraded, next);
  announcedDegraded = next;
  if (text) announcePage(text);
  return text;
}

// Called wherever the banner's shown set is computed, with the SAME list the banner
// renders, so the spoken and the drawn banner cannot disagree about what is showing.
function announceAlertTransition(shown) {
  const next = alertIdentities(shown);
  const text = bannerAnnouncement(announcedAlerts, next);
  announcedAlerts = next;
  if (text) announcePage(text);
  return text;
}


/* ---------------- Per-system freshness (C2) ---------------- */

// "<sourceKey>|<systemName>" -> that system's current age in seconds (null when it
// has never decoded), plus the subset of those keys that are stale right now. The
// index is rebuilt from the `sources` descriptors, which is the ONE place the
// per-system blocks are ingested (refreshSource), so every rendering surface below
// reads the same numbers the status line does.
//
// Rebuilt on a clock, not only on a poll: a system crosses FEED_STALE_AFTER_S by
// time passing, not by a response arriving, so the animation tick refreshes it too
// (see animateTrains). The stale SET is compared as a string signature and the
// marker sweep runs only when it changes, so the common case (nothing stale, or
// nothing newly stale) costs one small string compare per tick rather than an
// opacity write per marker.
let systemFreshnessIndex = new Map(); // "<source>|<system>" -> { age, staleAt }
let staleSystemSignature = "";

// Rebuild the index. Deliberately does NOT touch the stale-set signature: that is
// change-detection state owned by the animation tick, and consuming a transition
// here (this runs mid-poll, per source) would make the tick miss the sweep. REVIEW
// FIX: it used to do both, so a fast source's poll could swallow a slow source's
// transition and leave those markers undimmed until the tail of refreshAll.
function refreshSystemFreshness() {
  const now = Date.now() / 1000; // RAW clock: systemAges applies the skew correction
  const index = new Map();
  for (const [sourceKey, source] of Object.entries(sources)) {
    // A source with no payload yet has nothing to say about any system. Skipping it
    // also keeps the synthesized-name mismatch in sourceSystems' boot fallback (it
    // names by label, this indexes by key) out of the index entirely.
    if (!source.systems) continue;
    const ages = systemAges(source, now);
    const staleAts = systemStaleAts(source);
    // `ok` travels WITH the age, because an age alone cannot tell "current" from
    // "never decoded anything". The status line has always known this (its `blind` set
    // is exactly ages[name] == null && !ok), and the review found the announcement
    // path did not, which let a dead system be announced as recovered.
    const blocks = sourceSystems(source);
    for (const name of Object.keys(ages)) {
      index.set(`${sourceKey}|${name}`, {
        age: ages[name],
        staleAt: staleAts[name],
        ok: (blocks[name] || {}).ok !== false,
      });
    }
  }
  systemFreshnessIndex = index;
}

// True when the set of stale systems CHANGED since the last call. Separate from the
// rebuild above so exactly one caller (the animation tick) owns the transition.
function staleSetChanged() {
  const stale = [];
  for (const [key, entry] of systemFreshnessIndex) if (staleAge(entry.age)) stale.push(key);
  const signature = stale.sort().join(",");
  const changed = signature !== staleSystemSignature;
  staleSystemSignature = signature;
  return changed;
}

// One system's freshness, for a marker's dimming / popup line / glide freeze.
//
// AN UNKNOWN SYSTEM FALLS BACK TO THE SOURCE'S WORST, which is the fail-safe
// direction: over-dimming says "some of this may be old", which is true, while
// under-dimming would present retained data as live, the exact defect the retention
// gate exists to prevent. REVIEW FIX, and it is not hypothetical: models.py lets an
// aggregate envelope serve `systems: null` (a rollback, or a browser holding the new
// frontend against the old backend), and ingestSystems then synthesizes ONE system
// named after the source while the railroad layer looks its systems up by
// train.system. Every railroad marker missed, so none of them ever dimmed while the
// status line said the whole source was stale.
function systemFreshnessOf(sourceKey, systemName) {
  const entry =
    systemName == null ? null : systemFreshnessIndex.get(`${sourceKey}|${systemName}`);
  return entry ?? worstSystemFreshness(sourceKey);
}

function systemAgeOf(sourceKey, systemName) {
  return systemFreshnessOf(sourceKey, systemName).age;
}

function systemStaleAtOf(sourceKey, systemName) {
  return systemFreshnessOf(sourceKey, systemName).staleAt;
}

// The worst of a source's systems: the largest age and the EARLIEST freeze deadline,
// both being the pessimistic answer.
function worstSystemFreshness(sourceKey) {
  const worst = { age: null, staleAt: null };
  for (const [key, entry] of systemFreshnessIndex) {
    if (!key.startsWith(`${sourceKey}|`)) continue;
    if (entry.age != null && (worst.age == null || entry.age > worst.age)) worst.age = entry.age;
    if (entry.staleAt != null && (worst.staleAt == null || entry.staleAt < worst.staleAt)) {
      worst.staleAt = entry.staleAt;
    }
  }
  return worst;
}

// Each system file registers a sweep that re-dims its own markers; applyStaleTreatment
// runs them all when the stale set changes or a poll lands. Declared here (before the
// system files load) so their top-level pushes land in an existing array.
const staleTreatments = [];

function applyStaleTreatment() {
  for (const sweep of staleTreatments) sweep();
}

// setOpacity rather than a css class on the element: Leaflet stores it in the
// marker's options and re-applies it when the layer is re-added, so a marker dimmed
// while its layer is toggled off comes back still dimmed. getElement() is null in
// that state, which a class-based approach would have to guard.
// `base` is the marker's own resting opacity, which staleness compounds with rather
// than replaces (only the ferry layer has one; see markerOpacity).
function dimMarker(marker, age, base = 1) {
  marker.setOpacity(markerOpacity(age, base));
}

/* ---------------- A2: the one place a map marker is born ---------------- */

// EVERY L.marker ON THIS MAP COMES FROM HERE, and new systems (Amtrak, NJ Transit)
// must use it too. It is a seam, not a convenience.
//
// WHY A FACTORY RATHER THAN AN OPTION COPIED SIX TIMES. Leaflet gates two separate
// things on ONE option: `keyboard && (tabIndex = "0", role = "button")`. So the tab
// stop and the role are inseparable, and every marker in this app arrived
// tabbable, role="button", and nameless: a keyboard rider tabbed through every
// vehicle on the map before reaching a single control, hearing "button" each time
// and nothing else. The tab-order policy is that markers are NOT the keyboard path
// (the A1 station panel is, reachable by the skip link), so `keyboard: false` is
// correct everywhere. Written as six copied lines it would be six chances to forget,
// and the seventh system would forget; A1's announceUnlessTick exists for exactly
// this reason and this is the same lesson applied to the map. Sweeping the DOM after
// each poll to fix up markers would be worse still: a compensator running after the
// mistake instead of a design that cannot make it.
//
// The role has to be put BACK by hand, because keyboard:false took it away with the
// tab stop. role="img" with an aria-label is what a marker actually is: a graphic
// that means something. Touch screen-reader users, who navigate by pointer and not
// by Tab, still find and hear it.
/* A4: THE VANISHING-FOCUS DOOR, one place, on the popup path.

   WHERE IT LIVES. labeledMarker is the single birth seam for every marker in this app,
   enforced by markers.test.js, so a `remove` hook registered here is the symmetric death
   seam and covers every destruction path at once: the five per-system departure sweeps
   (group.removeLayer), a layer toggle (map.removeLayer on the group, which fires `remove`
   on every child), clearLayers, and a bare marker.remove(). That coverage is the reason
   for the placement, and it is pinned by mutation: deleting this hook fails A8a and A8c,
   which travel two different destruction paths.

   WHAT IS *NOT* LOAD-BEARING, corrected after mutation testing said so. The first version
   of this comment claimed the hook must run BEFORE Leaflet's own `remove: this.closePopup`
   (which callers install later, via bindPopup) so that it could see the popup still open.
   Measured: forcing marker.closePopup() to run first, at the top of this handler, leaves
   every spec green. The predicate does not need the popup to be OPEN, only for its ELEMENT
   to still contain the focused node, and a closing Leaflet popup lingers in the DOM for
   its fade. So the ordering is real but incidental, and saying otherwise would have left a
   future reader defending an invariant nothing depends on.

   WHAT IT DOES NOT TRY TO DO. It does not ask why the marker is going away. Measured at
   `remove` time, a departed vehicle and a hidden layer are byte-identical in every piece
   of Leaflet state (the group still has the marker, the map still has the group, the
   registry still has the key), so any attempt to tell them apart here would be guesswork.
   It does not need to: the predicate is about the RIDER, not the cause. A layer toggle
   moves focus to the checkbox the rider just activated, so the predicate is false and the
   door stays silent; a vehicle ageing out of the feed while its popup is open leaves focus
   inside the doomed subtree, and that is the case worth rescuing. */
function planVanishingFocus(subtree, { label = null, kind = "vehicle" } = {}) {
  return vanishingFocusPlan(subtree, document.activeElement, { label, kind });
}

// SPLIT FROM THE PLAN, and the split is not cosmetic. The banner is rebuilt by replacing
// its children, so by the time the rescue runs the focused button is already gone and
// document.activeElement has fallen to the body: a predicate evaluated at that moment asks
// "is the body inside the banner", which is false, and the rescue silently declines to
// fire on precisely the case it exists for. Caught by A8d failing with active=BODY. So
// callers whose destruction happens AFTER the decision plan first and apply second; the
// marker door, whose hook runs BEFORE Leaflet tears anything down, can do both at once.
function applyVanishingFocus(plan) {
  if (!plan || !plan.rescue) return false;
  // The map container is the stable ancestor and is already a labeled tab stop (Leaflet
  // writes tabindex=0 on it; index.html gives it the name). It cannot itself vanish, which
  // is the whole reason it is the destination rather than, say, the nearest sibling.
  const container = document.getElementById("map");
  if (container) container.focus();
  announcePage(plan.message);
  return true;
}

function rescueVanishingFocus(subtree, options = {}) {
  return applyVanishingFocus(planVanishingFocus(subtree, options));
}

function labeledMarker(latlng, options, name) {
  const marker = L.marker(latlng, { ...options, keyboard: false });
  // Relabel whenever Leaflet builds the element again. Toggling a layer off and on
  // DESTROYS the icon element and creates a fresh one, which restores tabindex and
  // role from marker options but loses anything we wrote as an attribute; verified
  // in the step-1 inventory. Without this, every marker on a re-shown layer would be
  // silently anonymous again, and nothing else would notice for a static system like
  // AirTrain that has no poll to re-apply the name.
  marker.on("add", () => applyMarkerName(marker));
  // A4: and the symmetric door. Registered BEFORE the caller's bindPopup so it runs
  // before Leaflet's own closePopup and can still see what the rider was holding.
  marker.on("remove", () => {
    const popup = typeof marker.getPopup === "function" ? marker.getPopup() : null;
    const el = popup && typeof popup.getElement === "function" ? popup.getElement() : null;
    rescueVanishingFocus(el, { label: marker._a11yName, kind: "vehicle" });
  });
  // RELABEL AFTER A RE-SKIN TOO, so the name survives no matter what order a caller
  // does things in. Today setIcon happens to reuse the same element and attributes
  // happen to survive, but that is a Leaflet implementation detail (Icon._setIconStyles
  // reassigns className wholesale, and DivIcon.createIcon reuses the div it is handed)
  // and two systems already call setIcon AFTER setMarkerName. Making survival depend
  // on call-site ordering is the copied-guard liability again, one level down: wrap it
  // once here and no system can get the order wrong.
  const setIcon = marker.setIcon.bind(marker);
  marker.setIcon = (icon) => {
    const result = setIcon(icon);
    applyMarkerName(marker);
    return result;
  };
  setMarkerName(marker, name);
  return marker;
}

// Set or refresh a marker's accessible name. SAFE AND EXPECTED TO BE CALLED EVERY
// POLL: the name is remembered on the marker so a rebuilt element can be relabeled
// from the last known value, and re-applying an unchanged name costs one attribute
// write and announces nothing (a marker is not a live region).
function setMarkerName(marker, name) {
  marker._a11yName = name;
  applyMarkerName(marker);
}

function applyMarkerName(marker) {
  const el = marker.getElement();
  if (!el || !marker._a11yName) return; // not on the map yet, or on a hidden layer
  el.setAttribute("role", "img");
  el.setAttribute("aria-label", marker._a11yName);
  // The inner svg is decoration: it repeats what the label already says, and left
  // exposed it reads as a second, nameless graphic inside the first.
  const svg = el.querySelector("svg");
  if (svg) svg.setAttribute("aria-hidden", "true");
}

/* ---------------- A2: reaching a station a vehicle is sitting on ---------------- */

// THE PRINCIPLE, AND IT LIVES HERE ONCE. Both systems that need it cite this comment
// rather than restating it.
//
// A vehicle marker sits in markerPane (z 600); station dots are drawn on a canvas in
// stationPane (z 450). A vehicle parked on its station therefore swallows every click
// meant for the station, and the arrivals a rider actually came for become unreachable
// at that pixel. Measured in the step-1 inventory: clicking a station with a train on
// it opens the TRAIN popup, and the station popup never fires.
//
// There are two honest resolutions, and where the position came from decides which
// ones are available.
//
// DERIVED POSITIONS MAY OFFSET. A subway train is placed at its stop by stop_id and a
// PATH train is interpolated along its route: neither position is a measurement, so
// drawing the marker a few pixels above the point costs nothing true. PATH set this
// precedent (iconAnchor [8, 20], path.js) and the subway follows it. Nudging a
// computation lies to no one.
//
// MEASURED POSITIONS MUST NOT OFFSET. Moving a GPS marker would make the map say the
// vehicle is somewhere it is not, which is the one thing this project does not do.
//
// A CROSS-LINK IS HONEST FOR EITHER, because it moves nothing at all: it adds a way to
// reach the station without touching where anything is drawn. So offsetting is the
// narrower permission and linking is the general one.
//
// PLACED RAILROAD TRAINS ARE DERIVED, AND TAKE THE CROSS-LINK ANYWAY. isPlacedRailroad
// means the train carries a stop_id and is drawn at its station's coordinates from the
// schedule; a railroad train with real GPS carries no stop_id at all. So a placed train
// would qualify for the offset by the rule above. It gets the link instead, as the
// deliberate conservative choice: the link never moves a marker, and the subway and
// PATH offsets are tuned to grid geometry those two systems share and the commuter
// railroads do not. Recorded because an earlier version of this comment had it
// backwards, calling placed trains measured, and a reader who inherited that would
// draw the wrong conclusion about every system here.
//
// THE LINK IS NOT A REPLACEMENT FOR THE PANE ORDERING. Vehicles still paint above
// stations, because that is the right visual layering; the link exists so the station
// under a vehicle is still reachable, not so the layering can be ignored.
//
// AND IT IS NEVER A GUESS. "At" is read from a field the payload already carries, per
// system, never from distance math. A cross-link pointing at the wrong station is
// worse than no cross-link at all: a rider who follows it gets confidently incorrect
// arrivals, and nothing on screen tells them so. A vehicle that does not name a
// station gets no link.
const CROSSLINK_CLASS = "popup-crosslink";

// The link's markup, or "" when this vehicle names no station. `stationKey` must be a
// SYSTEM-QUALIFIED registry key: station ids collide across systems (see the
// stationRegistry comment above, where the contract tier measured 21 of 24 ferry dock
// ids colliding with Metro-North station ids), so a bare id could resolve to a station
// in an entirely different system.
function crossLinkHtml(stationKey) {
  const entry = stationKey ? stationRegistry.find((row) => row.key === stationKey) : null;
  // No registry entry means the station layer has not loaded yet, or this id is not a
  // station we know. Either way there is nothing to link to, and inventing a
  // destination is the failure this whole comment is about.
  if (!entry) return "";
  // A real button, not a styled span: it is keyboard reachable, it activates on Enter
  // and Space without any handler of ours, and it announces as a button. The station
  // name is IN the accessible name, so "Also here" is never announced on its own.
  return (
    `<button type="button" class="${CROSSLINK_CLASS}" data-station-key="${esc(entry.key)}">` +
    `Also here: ${esc(entry.name)}</button>`
  );
}

// One delegated handler for every cross-link on the map, bound once. Delegation rather
// than per-popup wiring because popup content is regenerated from a function on every
// open and every update, so any listener attached to the rendered nodes would be
// discarded and re-attached constantly.
//
// BOUND IN THE CAPTURE PHASE, and it does not work otherwise. Leaflet calls
// disableClickPropagation on every popup container, which stops click events inside a
// popup from bubbling out (so a click on a popup does not also reach the map beneath
// it). A bubble-phase listener on document therefore never sees a cross-link press at
// all: the first draft used one and the button did nothing, from mouse or keyboard.
// Capture runs downward from document before the container's own listener, so it
// arrives first and is unaffected. Enter and Space on a <button> both synthesize a
// click, so this one listener is the keyboard path too.
document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest ? event.target.closest(`.${CROSSLINK_CLASS}`) : null;
    if (!button) return;
    event.preventDefault();
    openStationFromCrossLink(button.getAttribute("data-station-key"));
  },
  true,
);

// Open the linked station's popup and MOVE FOCUS INTO IT. This is the one place where
// moving focus is correct rather than rude: the rider activated a link asking to go
// somewhere, so leaving focus behind on a button whose popup just closed would strand
// them exactly as A1's closing paths would have. Opening a Leaflet popup replaces the
// popup pane's contents, so the button the rider pressed no longer exists by the time
// this returns.
function openStationFromCrossLink(stationKey) {
  const entry = stationRegistry.find((row) => row.key === stationKey);
  if (!entry || !entry.marker) return false;
  entry.marker.openPopup();
  // Ask the POPUP for its element rather than querying the document for the first
  // ".leaflet-popup-content". Leaflet fades a closing popup out, so for the length of
  // that animation the old popup is still in the DOM and a document query returns the
  // one we just closed: the first draft focused the train's dying popup and the
  // station's never received focus at all.
  const popup = entry.marker.getPopup();
  const root = popup && popup.getElement ? popup.getElement() : null;
  const content = root ? root.querySelector(".leaflet-popup-content") : null;
  if (!content) return false;
  // tabindex -1 makes it programmatically focusable without adding a tab stop: the
  // rider lands here, and Tab from here continues into the popup's own controls.
  //
  // ESCAPE NOW CLOSES THE POPUP FROM HERE, and this comment is kept rather than deleted
  // because the reasoning that deferred it is the reasoning that A4 finally acted on.
  //
  // A2 recorded the gap and declined to fix it here: Leaflet binds Escape on the MAP
  // container, focus inside a popup is outside it, so the key never reached any handler
  // (measured then: popup still open, focus unmoved). The finding's stronger claim, that
  // there was no way out, was false even then, because one Tab from this landing point
  // reaches the popup's own close button. What A2 said was that a keyboard-dismiss binding
  // is a MAP-WIDE decision and does not belong in the cross-link's landing path.
  //
  // A4 is where that map-wide decision got made. The Escape ladder in map.js is one
  // document-level handler that closes the topmost transient surface (an open popup, then
  // the station panel) from anywhere on the page, so this landing point inherits it
  // without knowing anything about keys. A9d asserts the ladder behaves identically from
  // inside a popup and from inside the panel, which is exactly the asymmetry A2 measured.
  content.setAttribute("tabindex", "-1");
  content.focus();
  return true;
}

// A2 FOLLOWUP, RESOLVED IN A4: WHERE FOCUS GOES WHEN A CONTROL IS DESTROYED WITH NO
// SUCCESSOR. Everything below restores focus to a live replacement, which was the case A2
// could answer honestly. The two cases with no replacement at all, both measured then
// landing the rider on document.body, are the ones A4's vanishing-focus door now catches:
// focus moves to the map container and the page live region says so once. See
// rescueVanishingFocus above and tests/e2e/vanish.spec.js. The original statement of both
// cases is kept below because it is the measurement that justified the fix:
//
//   1. A VEHICLE LEAVES THE FEED while its popup is open and focused. Measured on a
//      railroad train removed from one poll's payload: railroads.size 2 -> 1,
//      marker gone, zero .leaflet-popup nodes, document.activeElement === document.body.
//      Every system's apply* loop removes departed vehicles the same way, so the same
//      path exists five times over. It predates this phase; A2 neither introduced it nor
//      closes it.
//   2. THE LAST AGENCY-WIDE ALERT CLEARS while the rider is on the banner's dismiss
//      button, including when the rider is the one who dismissed it.
//
// It is one question, not two, and it is a product question rather than a mechanical
// one: silently moving focus to a landmark is a WCAG 3.2.2 change of context the rider
// did not ask for, and moving it WITH an announcement means deciding what the page says
// when a train a rider was reading about stops existing. The door for saying it already
// exists (announcePage). Fixing it badly is worse than the strand, and this phase has no
// review round left to cover a five-call-site change, so it is filed rather than guessed
// at. Until then a rider who lands on the body reaches the skip link with one Tab.
//
// UPDATING AN OPEN POPUP DESTROYS WHATEVER HAS FOCUS INSIDE IT, so every update goes
// through here. This is the THIRD Leaflet behaviour of the same family as the two above,
// and the review found it by reproduction: a rider who tabbed to a cross-link and waited
// one poll had document.activeElement drop to BODY while the button was still on screen,
// and their Enter did nothing. That is precisely the stranding the A1 focus contract
// exists to prevent, reintroduced through a feature built FOR keyboard riders.
//
// Popup content here is bound as a FUNCTION, so a refresh re-renders it wholesale and
// the old nodes are discarded. Nothing warns; focus simply lands on the body.
//
// Restores the same control when the re-rendered popup still has one, and falls back to
// the popup's content container so the rider is at worst still inside the popup they
// were reading rather than at the top of the document.
//
// THE CONTROL IS IDENTIFIED BY ITS ROLE IN THE POPUP, NOT BY WHAT IT POINTS AT, and
// round 3 of the review caught the difference by reproduction. The first version matched
// the cross-link on its data-station-key, with a comment claiming the key "survives the
// re-render because the popup describes the same vehicle". The key describes the
// STATION: railroad.js builds it from the train's current stop_id, so a train advancing
// one stop changes it by design. The rider tabbed to "Also here: Jamaica", the poll
// rendered "Also here: Hicksville", the key no longer matched, and focus was dumped on
// the inert content div with a live button on screen. Worse, it stuck: the content div
// survives later updates, so the guard below returned early on every subsequent poll and
// focus never came back. A vehicle popup has at most one cross-link (one call site, in
// railroad.js), so asking for that one is both simpler and correct.
//
// A DISSENT ON THE RECORD, because the review panel that raised this also REFUTED it and
// the disagreement is a judgment call rather than a fact. The skeptic reproduced
// everything above and then argued two things. First, that the severity is lower than
// round 1's: true, and worth stating plainly. Focus lands inside the popup, not on the
// body, so the rider is one Tab from the button rather than at the top of the document.
// Second, that this fix is worse than the defect, because restoring focus to a relabeled
// button means a rider who chose "Jamaica" and waited can press Enter and arrive at
// Hicksville without having chosen it.
//
// The fix ships anyway, for three reasons. The alternative leaves a rider holding a
// live-looking control whose Enter does nothing, which is the exact symptom this phase
// has now fixed twice and the reason the helper exists at all. The principle the skeptic
// cited (a cross-link pointing at the wrong station is worse than no cross-link) is
// about a link that names a station the vehicle is NOT at; this one names, correctly,
// where the train now is. And a popup held open across polls is live by design: its next
// stop, its countdowns and its staleness line all change underneath the rider already,
// so a control that changes with them is consistent rather than treacherous, and it
// announces its new name at the moment focus reaches it.
//
// The residual risk the skeptic names is real: a rider not listening when focus is
// restored can act on a changed destination. It is bounded by the station popup that
// opens naming itself. A3g pins the announcing half deliberately, asserting the button
// reads "Hicksville" before asserting Enter goes there.
function updatePopupKeepingFocus(marker) {
  const popup = marker.getPopup && marker.getPopup();
  if (!popup) return;
  const before = popup.getElement ? popup.getElement() : null;
  const active = document.activeElement;
  const hadFocus = !!(before && active && before.contains(active));
  const hadCrossLink = !!(hadFocus && active.classList && active.classList.contains(CROSSLINK_CLASS));

  popup.update();

  if (!hadFocus) return; // focus was elsewhere: moving it now would be the rude case
  const after = popup.getElement ? popup.getElement() : null;
  if (!after) return;
  // AND ONLY RESTORE WHAT WAS ACTUALLY LOST. Round 1 of the review shipped this helper
  // without this line and five independent lenses caught the same regression: update()
  // reassigns the CONTENT node's innerHTML and nothing else, so the popup's own close
  // button is a sibling that survives untouched. Restoring unconditionally therefore
  // yanked focus off a live control and dropped it on an inert div, on every vehicle
  // popup, every fifteen seconds, and the rider's Enter stopped closing the popup. The
  // element that still holds focus is by definition not stranded, so leave it alone.
  if (after.contains(active)) return;
  const content = after.querySelector(".leaflet-popup-content");
  // The station this now points at may differ from the one the rider tabbed to, and
  // that is the honest outcome: the button carries the station name in its accessible
  // name, so a restored focus announces the new destination before the rider can act on
  // it. A vehicle that has stopped naming a station renders no cross-link at all, and
  // then the content container below is the right landing place.
  const sameControl = hadCrossLink ? after.querySelector(`.${CROSSLINK_CLASS}`) : null;
  const destination = sameControl || content;
  if (!destination) return;
  // The content container is not naturally focusable; -1 makes it a landing place
  // without adding a tab stop, exactly as the cross-link handler does.
  if (destination === content) content.setAttribute("tabindex", "-1");
  destination.focus();
}

/* ----- Station popups + live arrivals, shared by subway, railroad and PATH ----- */

// Canvas-rendered so ~470 circle markers stay cheap and hit-testable; on its
// own pane (above the route-line canvas) so station clicks land here.
//
// A2 FOOTNOTE, because it is the reason station dots have no accessible name: a
// canvas-rendered circleMarker produces NO DOM element at all, so there is nothing to
// put a role or a label on. Naming ~470 station dots would mean abandoning the canvas
// renderer, which is the canvas work this phase deliberately does not do. Stations are
// reachable as named, keyboard-navigable text through the A1 station panel instead,
// which is the surface built for exactly that. AirTrain stations are the exception:
// they are L.marker with a divIcon (they need a shape a circle cannot draw), so they
// have an element and they get a name like any other marker.
const stationRenderer = L.canvas({ padding: 0.5, pane: "stationPane" });

// Shared popup machinery for BOTH station kinds (subway + railroad). One popup
// is open at a time (Leaflet closes others). A request token guards against a
// slow fetch landing after the user clicked a different station (of either
// kind, since the token is shared), and a 1s timer ticks countdowns down from
// absolute arrival timestamps without re-fetching. The last good arrivals
// payload lives on openStation so the tick and the 15s refresh share one source
// of truth (no captured-body closure that a later call could leave firing over
// newer state). openStation carries the station, its marker, the fetched body,
// the arrivals fetch `url`, and a kind-specific `render(station, body)`; the
// fetch/guard/timer skeleton below is otherwise kind-agnostic.
/* ---------------- The station registry (A1) ---------------- */

// EVERY STATION THE APP HAS LOADED, IN ONE PLACE. It did not exist before A1:
// each loader fetched its stops, built markers, and dropped the records on the
// floor, so the station panel had nothing to search. The loaders now register
// what they loaded as they build each marker, which keeps the arrivals URL and
// the marker written once per system rather than once per surface.
//
// Entries are appended as the loaders resolve, which they do asynchronously and
// in a race, so the panel must tolerate a partial registry and re-read it rather
// than snapshot it. searchStations sorts totally, so the display order never
// depends on which loader won.
//
// `key` is system-qualified because station ids collide ACROSS systems: the
// railroad and ferry id spaces are both bare integers, and the contract tier
// measured 21 of 24 ferry dock ids colliding with Metro-North station ids. A bare
// id would silently merge two different places.
const stationRegistry = [];

// kind drives the arrivals shaping and the vehicle noun; systemLabel is what the
// rider sees. AirTrain has no live arrivals at all, so it registers with a null
// arrivalsUrl and the panel renders its scheduled headways instead: system-shape
// honesty, not a special case, and the same branch a future system with no
// realtime feed would take.
function registerStation(entry) {
  stationRegistry.push(entry);
  // TELL THE PANEL, because it may already be on screen showing "Loading
  // stations..." from before this loader resolved. The docked-open desktop default
  // renders the panel at load time, when the registry is usually still empty, so
  // without this notification the list stayed on its loading line until the rider
  // typed something. Late-bound by name rather than wired at definition time,
  // because stations.js loads AFTER this file; the loaders all run later still, so
  // the function exists by the time any of them call it.
  if (typeof stationsRegistryChanged === "function") stationsRegistryChanged();
}

let stationSeq = 0;
let stationTimer = null;
let openStation = null; // { station, marker, body, url, render } while open

// Repaint the open popup from openStation.body. Reading the shared body (rather
// than a value captured per fetch) is what stops a stale tick from overwriting
// newer content: there is only ever one body to draw, the current one.
function renderStation() {
  if (!openStation || !openStation.body) return;
  const { station, marker, body, render } = openStation;
  if (marker.isPopupOpen()) marker.setPopupContent(render(station, body));
}


function stationError(station, message) {
  return (
    `<b>${esc(station.name ?? station.id)}</b>` +
    `<br><span class="popup-sub">${esc(message)}</span>`
  );
}

// refresh=false is a fresh popup open (show a Loading state, surface errors).
// refresh=true is the 15s background refresh of an already-open popup: keep the
// current arrivals ticking, swap in new data when it lands, and stay quiet on a
// failed poll rather than blanking good data with a Loading or error message.
// Reads the current openStation descriptor for the url/render, so it is the same
// skeleton for either station kind.
async function openStationArrivals({ refresh = false } = {}) {
  const open = openStation;
  if (!open) return;
  const { station, marker, url } = open;
  const seq = ++stationSeq;
  if (!refresh) {
    // Stop the previous tick up front so it cannot fire during this fetch.
    clearInterval(stationTimer);
    stationTimer = null;
    marker.setPopupContent(`<b>${esc(station.name ?? station.id)}</b><br>Loading arrivals…`);
  }
  let body;
  try {
    // AbortSignal.timeout bounds this arrivals fetch (R2); an abort rejects into the
    // catch below, which on a background refresh keeps the last-known arrivals
    // ticking rather than wedging the popup on "Loading…". This whole-fetch deadline
    // is ORTHOGONAL to the stationSeq guard: seq discards a fetch superseded by a
    // new click or a popup close (a user-scoped supersession), while the timeout cuts
    // off a fetch that simply never lands. A timeout is not a seq bump.
    const res = await fetch(url, { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (seq !== stationSeq) return; // superseded by another station click or a close
    if (!res.ok) {
      if (!refresh) {
        const err = await res.json().catch(() => null);
        marker.setPopupContent(
          stationError(station, err?.detail ?? `Arrivals unavailable (HTTP ${res.status})`),
        );
      }
      return; // a failed background refresh keeps the last-known arrivals ticking
    }
    body = await res.json();
  } catch {
    if (seq !== stationSeq) return;
    if (!refresh) {
      marker.setPopupContent(stationError(station, "Arrivals unavailable (network error)"));
    }
    return;
  }
  if (seq !== stationSeq) return;
  // NOTE: the skew baseline is NOT calibrated here. The arrivals endpoints carry
  // no served_at (only the five vehicle feeds do, R1), and calibrating off their
  // fetched_at was the audit poison this PR removes. The 15s vehicle-feed poll keeps
  // minClockOffset fresh. BOUNDED BOOT RACE: a client whose wall clock is materially
  // wrong that opens a station popup in the sub-second window before the first
  // vehicle poll resolves sees an uncalibrated countdown (and possibly a false age
  // line); it self-corrects on the very next 1s tick once a poll lands. The complete
  // fix (served_at on the arrivals endpoints, or gating the countdown on a settled
  // baseline) is deferred to R3's cold-start work.
  if (openStation === open) openStation.body = body;
  renderStation();
  if (!marker.isPopupOpen()) return;
  // (Re)start the single tick now that fresh data is in place.
  clearInterval(stationTimer);
  stationTimer = setInterval(renderStation, 1000);
}

// Wire one station circleMarker to the shared popup lifecycle. makeDescriptor(marker)
// builds the openStation descriptor (kind-specific url + render); the seq bump,
// timer teardown, and one-popup-at-a-time invalidation are identical for both
// kinds, so they live here once.
function bindStationPopup(marker, makeDescriptor) {
  return marker
    .bindPopup("", { minWidth: 170 })
    .on("popupopen", function () {
      openStation = makeDescriptor(this);
      openStationArrivals();
    })
    .on("popupclose", function () {
      stationSeq++; // invalidate any in-flight arrivals fetch for this popup
      clearInterval(stationTimer);
      stationTimer = null;
      if (openStation?.marker === this) openStation = null;
    });
}

/* ---------------- Service alerts (station popups) ---------------- */

// Active alerts indexed by (system, stop) and (system, route), rebuilt each poll.
// Starts empty, so a popup opened before the first fetch simply shows no alerts.
let alertsIndex = indexAlerts([]);

// Alerts freshness: the fetched_at the backend last reported, and the last banner
// set. The alerts loop swallows failures (below), so without an explicit freshness
// signal the banner and popups silently imply the alert set is current when the feed
// may have stopped updating. alertsStale() gates the honesty marker on
// alertsFetchedAt; lastBannerAlerts lets tickAlertBanner re-render the marker while
// polls are failing (loadAlerts only re-renders on success).
//
// C1: this tracks fetched_at, not served_at. fetched_at advances only on a backend
// poll that DECODED, so it stops moving during an alert-feed outage; served_at is
// stamped at response build and so stayed fresh forever behind a frozen index,
// which is why the R1 marker could never fire for that outage. See alertsStale().
let alertsFetchedAt = null;
let lastBannerAlerts = [];

// When this client first ASKED for alerts. It is the age basis while alertsFetchedAt
// is still null, so a backend that has never filled its index (every feed down since
// boot, so /api/alerts errors and loadAlerts swallows it) discloses after the same
// threshold instead of showing a confident alert-free map forever. Stamped once, at
// the first attempt, not per attempt, or it would reset the grace period every poll.
let alertsFirstAttemptAt = null;

// The skew-corrected client clock, matching the arrivals-countdown basis.
function alertsClockNow() {
  return Date.now() / 1000 - (minClockOffset ?? 0);
}

// The muted "alerts may be out of date" marker, or "" when the alerts feed is fresh.
// Honesty, not alarm: shared by the banner and every popup alert block so a stale
// alerts feed is disclosed everywhere the alert set is shown (or implied absent).
function staleAlertsMarker() {
  return alertsStale(alertsFetchedAt, alertsClockNow(), alertsFirstAttemptAt)
    ? `<div class="alert-stale">alerts may be out of date</div>`
    : "";
}

// Poll /api/alerts on the alerts cadence. WHY a failed or non-200 fetch is swallowed
// and keeps the last-known index: alerts are a decorative overlay, so their
// staleness or absence must never surface an error or delay the arrivals a rider
// clicked for. There is no user-facing alerts ERROR state, by design; the freshness
// marker (R1) is the one honest hedge that the index may have stopped updating.
async function loadAlerts() {
  // Stamped BEFORE the fetch and only once, so the never-filled grace period is
  // measured from the client's first attempt and cannot be reset by later attempts.
  if (alertsFirstAttemptAt == null) alertsFirstAttemptAt = alertsClockNow();
  try {
    // AbortSignal.timeout bounds the alerts fetch (R2). A timeout aborts into the
    // catch below and is swallowed like every other alerts failure: alerts are a
    // decorative overlay, so a wedged fetch must keep the last-known index silently,
    // never surface an error or block the arrivals a rider clicked for.
    const res = await fetch("/api/alerts", { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (!res.ok) return; // keep the last-known index + banner silently
    const body = await res.json();
    const list = body.alerts ?? [];
    alertsIndex = indexAlerts(list);
    // Record the BACKEND'S last successful poll, not this fetch's arrival. A 200
    // whose fetched_at has not advanced since the previous poll means the backend is
    // serving an index it could not refresh, and that must age the marker rather
    // than reset it. As of C2 the basis is the WORST per-system fetched_at, so a
    // partial outage ages it too (see alertsFreshnessBasis).
    //
    // NO FALLBACK TO THE CLIENT CLOCK. This used to read `?? alertsClockNow()` for a
    // backend that omitted fetched_at entirely, which C2 turned into a bug: null now
    // ALSO means "a system has never decoded", and mapping that to now would have
    // reported a permanently missing alert system as perfectly fresh. Null instead
    // ages against the client's first-attempt time (alertsStale's sinceAt branch),
    // which is the same honesty rule the never-filled-index case already used.
    //
    // minClockOffset is deliberately NOT calibrated from this response: it is a
    // global shared with the arrivals countdowns and the status line, so feeding it
    // from here would change non-alert surfaces. It stays calibrated by served_at on
    // the feed responses, exactly as R1 arranged; this line only consumes the axis.
    alertsFetchedAt = alertsFreshnessBasis(body);
    lastBannerAlerts = bannerAlerts(list);
    // The banner re-renders every poll (unlike popups, which render on open), so a
    // resolved agency-wide alert disappears on the next poll and a new one appears.
    renderAlertBanner(lastBannerAlerts);
  } catch {
    // network error: keep the last-known index + banner, no user-facing error
  }
}

// Re-render the banner from the last-known alerts so the "may be out of date" marker
// appears (or clears) as time crosses ALERTS_STALE_AFTER_S even while the alerts poll
// is FAILING (loadAlerts only re-renders on success). The stale flag is folded into
// the banner's dedup key, so this is a no-op until the flag actually flips. Driven by
// the 15s refreshAll tick in map.js.
function tickAlertBanner() {
  renderAlertBanner(lastBannerAlerts);
}

// The alerts block for a station popup: match the current index (read fresh as a
// global, so a popup re-render picks up whatever the store holds now) against the
// station, scoped by system, plus every route that serves the station. Returns ""
// when nothing matches, so no empty container is rendered.
//
// The served-routes set is the UNION of two sources (H5): the static
// routes-per-station index the backend now derives from stop_times (station.routes),
// and the route ids present in the station's CURRENT arrivals. The static list is
// the complete, always-present set, so a route-scoped alert reaches the station even
// with no imminent train; the arrivals ids are folded in too so a station whose
// static routes failed to load still shows alerts for routes with a live train (and
// so a brand-new route running before the next static refresh is covered). Either
// source alone is a strict subset of the intent, so both feed matchStationAlerts.
function stationAlertsBlock(system, station, body) {
  const routeIds = new Set(station.routes ?? []);
  for (const arrivals of Object.values(body?.directions ?? {})) {
    for (const arr of arrivals ?? []) if (arr.route_id) routeIds.add(arr.route_id);
  }
  // Append the alerts-freshness marker (R1): if the alerts feed itself is stale it
  // shows even when this station currently matches no alerts (the block is ""), so a
  // rider is never shown an empty-looking station while the alert feed is down.
  return alertsBlockHtml(matchStationAlerts(alertsIndex, system, station.id, routeIds)) +
    staleAlertsMarker();
}

// The alerts block for a route surface (bus / subway train / railroad train popup):
// match the current index against the popup's system and route. WHY read alertsIndex
// fresh each call: these popups are bound as functions and render at OPEN time (and
// on the marker poll's popup.update()), so they show the store as of open/refresh,
// not a live stream. A newly-arrived alert appears the next time the popup opens or
// updates, the same contract the arrivals popups follow.
function routeAlertsBlock(system, routeId) {
  return alertsBlockHtml(matchRouteAlerts(alertsIndex, system, routeId)) + staleAlertsMarker();
}

// Agency-wide (selector-less) alerts get a dismissible banner over the map instead
// of a popup, since they belong to no single route or station. WHY dismissal is per
// alert and in-memory for the session: dismissing hides the currently-shown alerts,
// a later poll re-showing the SAME ones keeps them hidden, but a NEW one (never
// dismissed) reopens the banner. So a rider can clear a standing incident without
// losing the next, distinct one, and a page reload starts fresh. The key is scoped by
// system like every other alert join, so a bare id reused across two feeds cannot make
// dismissing one hide an unrelated agency-wide alert.
//
// THE DISMISSAL KEY INCLUDES THE HEADER TEXT, for the same reason the render key does
// and with more at stake. The MTA revises an ongoing incident in place under one id,
// so an id-only dismissal meant that once a rider cleared "Delays on the 4 line", the
// SAME id later reading "All subway service suspended" stayed suppressed for the rest
// of the session. Agency-wide alerts have no route or stop selectors, so the banner is
// their only surface: there is no popup that would have shown it instead. Rewording
// therefore un-dismisses, which is the safe direction to err in. A rider who dismisses
// an unchanged alert still keeps it hidden, because an unchanged alert hashes the same.
const dismissedAlertIds = new Set();
const alertKey = (a) => `${a.system}|${a.id}|${hashString(String(a.header ?? ""))}`;

// Signature of the last-rendered banner, so an unchanged banner is NOT rebuilt every
// 60s poll: reassigning innerHTML would drop any text the rider has selected and
// re-parse identical markup for no visual change.
let lastBannerKey = null;

// A3 review: THE PANEL HAS TO KNOW HOW TALL THIS IS, because under 700px the two share
// the bottom of the screen. The banner moved to the bottom so it would stop covering the
// Stations toggle; the legend panel grows down from the top; and with the legend expanded
// they met. Measured at 375x667 with one agency-wide alert: #panel ran to y=657 while the
// banner occupied y 595..645.
//
// WHAT THE OVERLAP ACTUALLY COST, since the first reading of it was wrong. The banner is
// z-index 1001 and the panel 1000, and elementFromPoint across the alert row returned the
// row at every sample, so no alert text was ever hidden. Two things were: axe stopped
// being able to decide the row's contrast at all ("background color could not be
// determined because it partially overlaps other elements"), and the panel's last 62px
// sat behind the banner with no way to bring them out, because the panel scrolls its own
// overflow and its end is exactly what lands there. On a phone during a systemwide
// incident that is the status line, which is the surface that says whether the data a
// rider is looking at is current.
//
// PUBLISHED RATHER THAN GUESSED because the height varies with the number of alerts and
// with how the header wraps at a given width: any fixed reservation in the stylesheet
// would be right for one alert and wrong for two. style.css subtracts it from the panel's
// mobile max-height, so the panel now stops above the banner and scrolls instead.
function publishBannerHeight(el) {
  const px = el.childElementCount ? el.getBoundingClientRect().height : 0;
  document.documentElement.style.setProperty("--alert-banner-height", `${px}px`);
}

// The banner's height changes with the viewport even when its CONTENT has not changed: the
// same header wraps to one line at 1280 and two at 320, and renderAlertBanner returns early
// on an unchanged key so it would never republish. Rotating a phone would then leave the
// panel sized against the old height. Cheap enough to run raw (one rect read plus one
// custom property write), and there is no work to debounce.
window.addEventListener("resize", () => {
  const el = document.getElementById("alert-banner");
  if (el) publishBannerHeight(el);
});

// A4: what the banner is about to destroy, captured while it still exists.
//
// The banner is REBUILT IN PLACE rather than removed, so the element the rider is holding
// is a descendant that will not survive, while #alert-banner itself does. Returning the
// container is therefore right for the predicate (it is the subtree that contains the
// doomed control) and returning the focused element itself would be wrong the moment
// Leaflet or a future rebuild reuses a node.
function bannerFocusVictim(el) {
  if (!el || !document.activeElement) return null;
  return el.contains(document.activeElement) ? el : null;
}

function renderAlertBanner(alerts) {
  const el = document.getElementById("alert-banner");
  const shown = alerts.filter((a) => a.header && !dismissedAlertIds.has(alertKey(a)));
  // R1: the banner also carries the alerts-freshness marker, so a stale alerts feed
  // is disclosed even when there are no agency-wide alerts to show. The stale flag is
  // folded into the dedup key, or the marker would never paint/clear on an unchanged
  // alert set; C1 folded in a hash of each alert's HEADER TEXT too, so a revision of
  // an ongoing incident under the same id re-renders instead of leaving stale wording
  // on screen. See bannerRenderKey().
  const stale = alertsStale(alertsFetchedAt, alertsClockNow(), alertsFirstAttemptAt);
  const key = bannerRenderKey(shown, stale);
  if (key === lastBannerKey) return; // unchanged since the last render: leave the DOM alone
  lastBannerKey = key;
  // Speak from the SAME `shown` list the rows below are built from, so the spoken and
  // the drawn banner cannot disagree about what is showing. Placed after the dedup
  // return, which costs nothing: an unchanged key means unchanged alert identities, so
  // the announcement would have been silent anyway. The stale flag IS in the key but
  // NOT in the identities, so the freshness marker appearing re-renders the strip and
  // says nothing, which is the intent: that marker is honesty about the feed, not news
  // about the transit system.
  announceAlertTransition(shown);
  if (!shown.length && !stale) {
    // A4: THE UNMOUNT PATH, which A2's own FOLLOWUP named and left open. The rider may be
    // holding the dismiss button that is about to stop existing, and measured, this branch
    // dropped them on document.body in silence. Read BEFORE the children go, because
    // afterwards there is nothing left to ask.
    const plan = planVanishingFocus(bannerFocusVictim(el), { kind: "alerts" });
    el.replaceChildren(); // nothing to show and alerts are current: no banner strip
    publishBannerHeight(el);
    applyVanishingFocus(plan);
    return;
  }
  const rows = shown.map((a) => `<div class="alert-banner-row">${esc(a.header)}</div>`).join("");
  const staleRow = stale
    ? `<div class="alert-banner-row alert-stale">alerts may be out of date</div>`
    : "";
  // The dismiss button only appears when there ARE dismissible alerts; it clears the
  // shown alerts but never the freshness marker (dismissing incidents must not hide
  // the honesty hedge that the feed is down).
  const dismiss = shown.length
    ? `<button type="button" id="alert-banner-dismiss" title="Dismiss">&times;</button>`
    : "";
  // THE SAME FAMILY AS THE POPUP REFRESH, on the page's other rebuilt-in-place surface.
  // Reassigning innerHTML destroys the dismiss button, so a rider parked on it was
  // dropped to document.body the moment an ongoing incident was reworded under its own
  // id, which is precisely the case the header hash in the render key exists to catch.
  // Measured before the fix: BUTTON#alert-banner-dismiss -> BODY.
  //
  // Restores only to a LIVE successor, like updatePopupKeepingFocus. When the rebuild
  // has no dismiss button the banner itself is gone, and where focus belongs then is an
  // open question this phase does not answer; see the A2 FOLLOWUP filed above
  // updatePopupKeepingFocus.
  const hadFocus = !!(document.activeElement && el.contains(document.activeElement));
  // Captured before the rebuild for the same reason as the unmount branch: once innerHTML
  // is reassigned the old subtree is gone and cannot be asked whether it held focus.
  const rebuildPlan = planVanishingFocus(bannerFocusVictim(el), { kind: "alerts" });
  el.innerHTML =
    `<div class="alert-banner-strip">` +
    `<div class="alert-banner-rows">${rows}${staleRow}</div>` +
    dismiss +
    `</div>`;
  const dismissBtn = el.querySelector("#alert-banner-dismiss");
  if (hadFocus && dismissBtn) dismissBtn.focus();
  // A4: AND THE REBUILD THAT HAS NO SUCCESSOR TO RESTORE TO. When the alert set empties
  // on a poll whose feed is also stale, the strip is rebuilt carrying only the "alerts may
  // be out of date" row and no dismiss button, so the branch above finds nothing to focus
  // and silently gives up. Measured, that is the most confusing variant of the defect: the
  // banner is still visibly on screen with nothing focusable inside it and the rider is on
  // the body. The dismiss button also cannot survive its own click, since dismissing
  // empties `shown`, so every dismissal lands on this path or the unmount above.
  if (hadFocus && !dismissBtn) applyVanishingFocus(rebuildPlan);
  publishBannerHeight(el);
  if (dismissBtn) {
    dismissBtn.addEventListener("click", () => {
      for (const alert of shown) dismissedAlertIds.add(alertKey(alert));
      renderAlertBanner(alerts); // re-render: dismissed ids drop out (marker, if any, stays)
    });
  }
}


// Glide trains between polls: recompute every marker's interpolated position
// from the current skew-corrected time. Throttled to ~10 fps (trains are slow
// and there can be a few hundred markers), and skipped entirely while the
// subway layer is hidden. rAF keeps rescheduling so it resumes on re-toggle.
const TRAIN_TICK_MS = 100;
let lastTrainTick = 0;

function animateTrains(ts) {
  // Glides subway trains, placed railroad trains, and PATH trains between
  // polls. GPS railroad trains are not animated here: they move by their
  // reported position in applyRailroads. Anchorless PATH trains cost one
  // trainLatLng fallback each and stay put, so no per-record gate is needed.
  // Each layer is gated on its own visibility; rAF keeps rescheduling so
  // animation resumes on re-toggle.
  if (ts - lastTrainTick >= TRAIN_TICK_MS) {
    lastTrainTick = ts;
    // A system goes stale by time passing, not by a response arriving, so the
    // freshness index is rebuilt here as well as on each poll: crossing the
    // threshold mid-interval must dim the markers and freeze the glide without
    // waiting up to 15s for the next poll. The sweep runs only when the stale set
    // actually changes (C2).
    refreshSystemFreshness();
    if (staleSetChanged()) {
      applyStaleTreatment();
      // AND SAY SO. A system goes stale by time passing, not only by a poll landing,
      // so the tick is where a mid-interval crossing is detected; announcing only from
      // the poll tail would leave a rider up to fifteen seconds behind the dimming
      // they cannot see. announceStatusTransition compares against what was last
      // announced, so being called from here AND from the poll tail is harmless: the
      // second call finds an unchanged set and says nothing.
      announceStatusTransition(systemFreshnessIndex);
    }
    const now = Date.now() / 1000 - (minClockOffset ?? 0);
    // REDUCED MOTION STOPS THE GLIDE, AND NOTHING ELSE. The freshness rebuild, the
    // dimming sweep and the announcement above all still run: those are data honesty,
    // not motion, and suppressing them would be the gate changing WHAT is shown rather
    // than HOW it moves. What is skipped is only the per-frame interpolation, so every
    // marker sits where its last poll said it was and jumps to the new truth when the
    // next one lands. Same positions, same data, no tweening.
    if (!motionOn) {
      requestAnimationFrame(animateTrains);
      return;
    }
    // glideClock pins a marker at its system's freeze deadline instead of
    // dead-reckoning it forward on a feed that is not being refreshed. A system with
    // no deadline gets `now` back unchanged, so healthy gliding is untouched.
    if (map.hasLayer(subwayLayer)) {
      for (const record of trains.values()) {
        const at = glideClock(now, subwaySystemStaleAt(record.latest));
        record.marker.setLatLng(trainLatLng(record.latest, at, record.fState));
      }
    }
    if (map.hasLayer(railroadLayer)) {
      for (const record of railroads.values()) {
        if (record.placed) {
          const at = glideClock(now, systemStaleAtOf("railroads", record.latest.system));
          record.marker.setLatLng(trainLatLng(record.latest, at, record.fState));
        }
      }
    }
    if (map.hasLayer(pathTrains)) {
      // PATH is single-feed, so its system is the synthesized one named after the
      // source: it flows through the SAME freeze rule as the aggregates rather than
      // being exempted by having no systems block (see ingestSystems).
      const at = glideClock(now, systemStaleAtOf("path", "path"));
      for (const record of pathTrainRecords.values()) {
        record.marker.setLatLng(trainLatLng(record.latest, at, record.fState));
      }
    }
  }
  requestAnimationFrame(animateTrains);
}

