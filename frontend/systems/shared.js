// Shared map infrastructure for the ordered-script frontend: the Leaflet map
// and every layer group, the toggle wiring, the status line, the reusable station
// popup machinery (used by subway, railroad and PATH stations), the service-alert
// index and banner, and the shared train-animation loop. Loaded as a plain
// <script> right after helpers.js and before the per-system files, so its
// top-level const/let bindings are in the shared global scope they all read (the
// same buildless model helpers.js -> map.js already uses; no bundler).

const map = L.map("map").setView([40.7128, -74.006], 12);

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
    for (const name of Object.keys(ages)) {
      index.set(`${sourceKey}|${name}`, { age: ages[name], staleAt: staleAts[name] });
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
function labeledMarker(latlng, options, name) {
  const marker = L.marker(latlng, { ...options, keyboard: false });
  // Relabel whenever Leaflet builds the element again. Toggling a layer off and on
  // DESTROYS the icon element and creates a fresh one, which restores tabindex and
  // role from marker options but loses anything we wrote as an attribute; verified
  // in the step-1 inventory. Without this, every marker on a re-shown layer would be
  // silently anonymous again, and nothing else would notice for a static system like
  // AirTrain that has no poll to re-apply the name.
  marker.on("add", () => applyMarkerName(marker));
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
    el.replaceChildren(); // nothing to show and alerts are current: no banner strip
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
  el.innerHTML =
    `<div class="alert-banner-strip">` +
    `<div class="alert-banner-rows">${rows}${staleRow}</div>` +
    dismiss +
    `</div>`;
  const dismissBtn = el.querySelector("#alert-banner-dismiss");
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

