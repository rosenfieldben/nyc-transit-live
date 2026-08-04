// The accessible station surface (A1): a searchable list of every station the app
// knows, and one station's live arrivals rendered as TEXT with real semantics.
//
// WHY THIS EXISTS AT ALL. The map is a canvas. A canvas has no accessible
// structure to annotate, so the fix is not ARIA on the map, it is an equivalent
// non-map surface that carries the same information in elements a screen reader
// and a keyboard already understand. Everything a rider can learn by clicking a
// station dot is reachable here without a pointer and without sight.
//
// WHAT IS DELIBERATELY NATIVE. Result rows are real <button> elements inside a
// <ul>. No role="listbox", no aria-activedescendant, no roving tabindex. Those
// patterns require reimplementing focus, selection, and typeahead correctly in
// every screen reader, and getting any of it slightly wrong is worse than the
// plain elements that already work everywhere.
//
// THIS FILE OWNS THE DOM. Its pure logic (search, arrivals shaping, the spoken
// sentence, the announcement guard) lives in helpers.js and is node-tested there.
// It runs after systems/*.js so the station registry exists to read, and before
// map.js, which wires the toggle and the refresh tick.
//
// Nothing here knows what a terminal is: the future terminal boards are meant to
// be a preset view of this same surface, so a special case for them here would be
// the wrong shape twice.

const stationsPanel = document.getElementById("stations-panel");
const stationsToggle = document.getElementById("stations-toggle");
const stationsSearch = document.getElementById("stations-search");
const stationsResults = document.getElementById("stations-results");
const stationsStatus = document.getElementById("stations-status");
const stationsDetail = document.getElementById("stations-detail");
const stationsAnnounce = document.getElementById("stations-announce");

// The station whose detail is showing, plus the machinery that keeps it live.
// This mirrors the popup lifecycle in systems/shared.js on purpose: one sequence
// counter invalidating superseded fetches, one interval repainting the countdowns,
// and a background refresh that keeps the last-known rows rather than blanking
// them. Two surfaces reading the same endpoints must not disagree about what a
// failed poll means.
let panelStation = null;
let panelBody = null;
let panelSeq = 0;
let panelTimer = null;
// The last SHAPED payload announced. The announcement guard compares payloads, never
// rendered text, and it advances only when the live region was actually written. See
// announcementWorthy in helpers.js and announceUnlessTick below.
let panelAnnounced = null;
// The first-load failure for the SELECTED station, retained rather than left to the
// rendered DOM. Round 2 of the review caught why it has to be state: reopening the
// panel re-renders from scratch, and with the error living only in the DOM the
// re-render replaced a truthful "Arrivals cache is warming up" with a "Loading
// arrivals..." that never resolved while the backend stayed down. Cleared on a new
// selection and on any successful load; a failed BACKGROUND refresh deliberately
// leaves it alone, because that path keeps the last-known rows instead.
let panelError = null;

/* ---------------- open, close, and where focus goes ---------------- */

function stationsPanelOpen() {
  return stationsPanel != null && !stationsPanel.hidden;
}

/* A4: WHILE THE OVERLAY IS UP, THE PAGE BEHIND IT IS INERT.

   THE WART THIS CLOSES. Under 700px the panel covers the viewport, and A3 measured what
   that cost a keyboard rider: 14 of 17 tab stops sat behind an opaque overlay, reachable
   by Tab and invisible to the eye. A3 recorded it rather than fixing it, and A6n pinned
   it as a known wart, because the obvious fix is a focus trap and a trap with no reliable
   exit is exactly how the untappable overlay got created in the first place.

   `inert` is the platform primitive built for this and it is NOT a trap: it makes a
   subtree unfocusable AND removes it from the accessibility tree, so the controls behind
   the overlay stop being reachable and stop being announced, while Tab still cycles
   freely within the panel and Escape still leaves. Nothing has to be un-trapped on the
   way out; the attribute is simply removed.

   TWO EXEMPTIONS, and the second one is the sharp edge.

   #stations-panel itself, obviously: it is the thing that is on top.

   #page-announce, because inert removes a subtree from the accessibility tree and an
   inert live region is a SILENT live region. That region is where A4's vanishing-focus
   announcements are spoken, and the overlay is exactly the state a rider is most likely
   to be in when a marker they were reading disappears underneath it. Inerting it would
   have made this phase's other deliverable mute in this phase's own new state, silently,
   with every spec still green. It is visually hidden and holds nothing focusable, so
   exempting it costs nothing and hides nothing.

   #stations-skip is deliberately NOT exempt. It is a body child, so it goes inert with
   everything else, and that is correct: the link exists to get a rider INTO the panel and
   the panel is already open and holding focus. */
const INERT_EXEMPT = new Set(["stations-panel", "page-announce", "app-title"]);

/* WALKED, NOT FLATTENED, and A4 had to learn that the hard way inside its own phase.

   The first version of this iterated document.body.children, which was correct only while
   the panel happened to BE a body child. Deliverable 4 then wrapped the map and the panel
   in <main> for the landmark rules, and that one structural change turned this loop into a
   defect: <main> is a body child, it is not the panel, so it was inerted, and the panel
   inside it went inert with it. The overlay would have made itself unusable.

   So the sweep walks from the panel up to the body and inerts SIBLINGS at each level,
   which is the standard "everything except this subtree" shape and is indifferent to how
   deeply the panel is nested. #map is a sibling of the panel inside <main> and is inerted
   there; <main> itself is on the path and is left alone, which is also what keeps the
   document's one landmark reachable while the overlay is up.

   THE EXEMPTIONS, all three non-interactive and all three needed in the accessibility tree
   rather than out of it. #stations-panel is the overlay. #page-announce is where the
   vanishing-focus announcements are spoken, and an inert live region is a silent one.
   #app-title is the page's h1: inerting it removes the document's only level-one heading
   from the a11y tree, which axe reports as page-has-heading-one and which would leave a
   screen-reader rider in the overlay with no page title at all. */
function setBackgroundInert(on) {
  if (!stationsPanel || !document.body) return;
  let node = stationsPanel;
  while (node && node !== document.body && node.parentElement) {
    const parent = node.parentElement;
    for (const sibling of parent.children) {
      // Scripts are not rendered and cannot hold focus; skipping them keeps the attribute
      // off elements where it would only be noise in the DOM.
      if (sibling === node || sibling.tagName === "SCRIPT" || INERT_EXEMPT.has(sibling.id)) continue;
      sibling.inert = on;
    }
    node = parent;
  }
}

// The state this derives from is the same state the layout derives from: an overlay is
// an open panel at a width where the panel covers the page. Above the breakpoint the
// panel is a drawer beside the map, nothing is covered, and nothing is inerted.
function applyOverlayInertness() {
  setBackgroundInert(stationsPanelOpen() && narrowViewport());
}

// Opening moves focus INTO the panel, to the search input, because the reason
// someone opened it is to search. Announcing the panel and leaving focus behind
// on the toggle would make the next Tab land somewhere unrelated.
function openStationsPanel({ focusSearch = true } = {}) {
  if (!stationsPanel) return;
  stationsPanel.hidden = false;
  if (stationsToggle) stationsToggle.setAttribute("aria-expanded", "true");
  applyDockedLayout(); // the map gives up the column while the panel is using it
  applyOverlayInertness(); // and at overlay widths the page behind it stops being reachable
  if (focusSearch && stationsSearch) stationsSearch.focus();
  renderStationResults();
  resumePanelArrivals();
}

// REOPENING MUST NOT PRESENT THE OLD ARRIVALS AS CURRENT, and before this it did.
// Closing stops the tick but leaves the rendered detail sitting in the DOM, so a
// rider who closed the panel, did something else for ten minutes, and reopened it
// read "1 train in 2 minutes, 8:01 AM arrival" for a train that had left eight
// minutes earlier, with no staleness line anywhere, because the text was computed
// when it was true and nothing recomputed it. The countdown and the clock time even
// agreed with each other, so there was no way to tell from the text that it was old.
//
// Three parts, in order. The re-render recomputes everything against the current
// clock, which is what makes the "as of 10m ago" line appear and moves the departed
// train to "now", both from the helpers that already existed. The tick restarts so
// the text keeps moving. The refresh fetches live data instead of leaving the rider
// on retained rows until map.js next comes around, and it is a refresh rather than a
// first load so a failed poll keeps those rows (already marked old by the re-render)
// rather than replacing them with an error.
function resumePanelArrivals() {
  if (!panelStation) return;
  // No `error` argument: renderStationDetail defaults to the retained panelError, so
  // a station that failed to load reopens still saying why, rather than reverting to
  // a loading line for a fetch that already came back.
  renderStationDetail();
  if (!panelStation.arrivalsUrl) return;
  // Only rows that count down need a tick. Arming it with nothing to count would
  // repaint an error or a loading line once a second to no purpose.
  if (panelBody) startPanelTick();
  fetchPanelArrivals({ refresh: true });
}

// Closing returns focus to the toggle, which is the element that opened it and
// the one a rider expects to find themselves on.
//
// THE FOCUS-STRANDING GUARD is the `contains` check. Hiding a subtree that holds
// the focused element drops focus onto document.body, where the next Tab restarts
// from the top of the page and a screen reader announces nothing at all. So focus
// is moved out BEFORE the panel is hidden, and only when it was actually inside:
// closing a panel the rider was not in must not yank their focus somewhere else.
function closeStationsPanel() {
  if (!stationsPanel) return;
  // A4: UN-INERT BEFORE RESTORING FOCUS, and this order is the whole of the fix rather
  // than a tidiness preference. #stations-toggle is outside the panel, so while the
  // overlay is up it is inert, and .focus() on an element inside an inert subtree is a
  // no-op: the call succeeds, nothing moves, and the rider is left on the body with the
  // panel closing around them. Released unconditionally rather than through
  // applyOverlayInertness, because the panel is still open on this line and that helper
  // would correctly compute "still an overlay" and change nothing.
  setBackgroundInert(false);
  const focusWasInside = stationsPanel.contains(document.activeElement);
  if (focusWasInside && stationsToggle) stationsToggle.focus();
  stationsPanel.hidden = true;
  if (stationsToggle) stationsToggle.setAttribute("aria-expanded", "false");
  applyDockedLayout(); // and takes it back the moment the panel stops using it
  stopPanelArrivals();
}

function toggleStationsPanel() {
  if (stationsPanelOpen()) closeStationsPanel();
  else openStationsPanel();
}

// A3: the panel's own close button, which under 700px is the only pointer route out of
// a full-viewport overlay. Routed through closeStationsPanel so it shares the focus
// contract with Escape and the toggle: one door, and focus returns to the opener.
const stationsClose = document.getElementById("stations-close");
if (stationsClose) stationsClose.addEventListener("click", closeStationsPanel);

// Escape anywhere inside closes and returns focus. Bound on the panel rather than
// the document so it cannot swallow Escape from the rest of the page (the Leaflet
// popups use it too).
//
// TAB IS NOT TRAPPED, and A3 has to be honest about what that means now. Above the
// breakpoint the panel is a drawer beside the map and tabbing out of it reaches things
// the rider can see. Under 700px the panel COVERS the page, so tabbing past its last
// control reaches elements that are behind an opaque overlay: reachable by keyboard,
// invisible to the eye. That is a real wart and it is deliberately not solved by
// trapping focus, because a trap with no reliable exit is how the defect above was
// created in the first place.
//
// WHERE THE EXIT ACTUALLY IS, corrected after round 2 of the review caught this comment
// claiming the close button was "the last stop inside the panel". It is the FIRST.
//
// Round 3 then caught the correction: it named the wrong element AND described only one
// of the two states the panel is ever in. Twice wrong in the same paragraph is worth
// leaving on the record, because it is the argument for A6n existing at all. Measured at
// 375, both states:
//
//   focusable order   [#stations-close, #stations-search, one button per result row]
//   empty query       search -> #stations-toggle (outside, and covered by the overlay)
//   with result rows  search -> the first row (still inside), rows -> #stations-toggle
//
// So the first stop outside the panel is always #stations-toggle, which the overlay
// covers; #legend-toggle, which the earlier draft named, is the stop after it. And with
// rows rendered, which is the state a rider who has typed anything is in, forward-tab
// does not leave immediately at all. The keyboard exits are Escape, and one Shift+Tab
// from the search input the panel opens on. A6n pins all of that in both states rather
// than trusting this paragraph, since this paragraph is what keeps being wrong; A6i pins
// the pointer rider's exit.
//
// First was chosen for the screen reader, and it is kept: the exit is announced
// immediately after the region's name, and it stays in one place instead of moving down
// the panel as result rows appear.
// A4 MOVED THE ESCAPE HANDLING OUT OF THIS FILE. The panel used to bind its own Escape
// here, which meant the answer to "what does Escape close" depended on which file you
// read: this one closed the panel whenever focus was inside it, and Leaflet closed a popup
// whenever focus was the map container, so a rider with both open got different results
// from the same key depending on where they were standing. The ladder in map.js now owns
// the ordering in one place (popup, then panel, never the banner) and calls
// closeStationsPanel, so this file keeps the focus contract and gives up the key.

if (stationsToggle) stationsToggle.addEventListener("click", toggleStationsPanel);

/* A3: THE SKIP LINK HAS TO OPEN WHAT IT SKIPS TO.
   It was a bare <a href="#stations-panel"> with no script at all, which works only when
   the panel happens to be open already. At desktop widths A1 docks it open, so the link
   behaved correctly and nothing noticed; under the mobile breakpoint the panel starts
   closed, and the link then pointed at a hidden element.

   Measured at 375 before this: Tab reached the link, Enter did nothing observable, and
   the next Tab landed on #stations-toggle. So the first tab stop on a phone promised to
   skip TO the stations list and delivered the button that opens it, which is exactly
   where the rider would have arrived with one more Tab and no link at all. axe says the
   same thing in its own words, reporting skip-link as undecidable at this width with
   "Skip link target should become visible on activation"; that report is what put this
   under the light, and it is the reason the mobile scan was worth adding.

   preventDefault because the fragment navigation is now redundant and would fight the
   focus call. openStationsPanel already focuses the search box, so this reuses the
   panel's own opening path rather than reimplementing it: one door, as everywhere else
   in this codebase. When the panel is ALREADY open the anchor's native behaviour is
   correct and is left alone, which keeps the desktop path exactly as A1 shipped it. */
const stationsSkip = document.getElementById("stations-skip");
if (stationsSkip) {
  stationsSkip.addEventListener("click", (event) => {
    if (!stationsPanel || !stationsPanel.hidden) return; // already open: the anchor is right
    event.preventDefault();
    openStationsPanel();
  });
}
if (stationsSearch) stationsSearch.addEventListener("input", renderStationResults);

/* ---------------- the result list ---------------- */

// The chip background and text color for one route, from the SAME color
// authorities the map markers and popups use, switched by system because the
// systems genuinely differ: subway route colors come from a fixed table with a
// known set of light backgrounds needing dark text, railroad has its own palette
// (its route ids collide with the subway's), and PATH and ferry colors are served
// per route by the backend and validated before use.
function stationChipStyle(entry, routeId) {
  if (entry.kind === "railroad") {
    const bg = railroadColor(routeId);
    return { bg, fg: readableTextOn(bg) };
  }
  if (entry.kind === "path" || entry.kind === "ferry") {
    const colorFor = entry.colorFor || (() => null);
    const bg = colorFor(routeId) || "#546e7a";
    return { bg, fg: readableTextOn(bg) };
  }
  if (entry.kind === "airtrain") return { bg: "#b5179e", fg: readableTextOn("#b5179e") };
  const bg = lineColor(routeId);
  return { bg, fg: readableTextOn(bg) };
}

// One result row: a real button carrying the station name, its system, its route
// chips, and the accessibility indicator where the data says so.
//
// THE INDICATOR IS NEVER THE ICON ALONE. The wheelchair glyph is decorative
// (aria-hidden) and a visually-hidden span carries the words, so the row reads as
// "Wall St, Ferry, wheelchair accessible" rather than announcing a symbol name or
// nothing at all. Route chips get the same treatment: the chip is visual, the
// route is spoken as part of the row's text.
function stationRowButton(entry) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "station-row";
  button.dataset.stationKey = entry.key;

  const name = document.createElement("span");
  name.className = "station-row-name";
  name.textContent = entry.name;
  button.appendChild(name);

  const system = document.createElement("span");
  system.className = "station-row-system";
  system.textContent = entry.systemLabel;
  button.appendChild(system);

  if (entry.routes && entry.routes.length) {
    const chips = document.createElement("span");
    chips.className = "station-row-chips";
    for (const routeId of entry.routes) {
      const { bg, fg } = stationChipStyle(entry, routeId);
      const chip = document.createElement("span");
      chip.className = "station-chip";
      chip.style.background = bg;
      chip.style.color = fg;
      chip.textContent = routeId;
      chips.appendChild(chip);
    }
    button.appendChild(chips);
  }

  if (entry.wheelchair) {
    const glyph = document.createElement("span");
    glyph.className = "station-row-access";
    glyph.setAttribute("aria-hidden", "true");
    glyph.textContent = "♿";
    button.appendChild(glyph);
    const label = document.createElement("span");
    label.className = "visually-hidden";
    label.textContent = " wheelchair accessible";
    button.appendChild(label);
  }

  button.addEventListener("click", () => selectStation(entry.key));
  item.appendChild(button);
  return item;
}

// Repaint the results from the current query. Reads the registry fresh every time
// rather than snapshotting it, because the loaders resolve asynchronously and a
// snapshot taken at load would permanently miss whichever system was still in
// flight.
function renderStationResults() {
  if (!stationsResults || !stationsStatus) return;
  const query = stationsSearch ? stationsSearch.value : "";
  const result = searchStations(stationRegistry, query);
  stationsResults.replaceChildren();

  if (result.prompt) {
    // An empty query is a question nobody asked. Nine hundred rows is a hostile
    // answer to it, especially to someone walking them one Tab at a time.
    stationsStatus.textContent = stationRegistry.length
      ? "Type a station name to search."
      : "Loading stations…";
    return;
  }
  if (!result.total) {
    stationsStatus.textContent = "No stations match that search.";
    return;
  }
  for (const entry of result.rows) stationsResults.appendChild(stationRowButton(entry));
  // The count is stated for everyone, and the overflow line says what to DO about
  // it rather than silently truncating.
  const shown = `${result.rows.length} of ${result.total} ${result.total === 1 ? "station" : "stations"}`;
  const overflow = stationOverflowLine(result.hidden);
  stationsStatus.textContent = overflow ? `${shown}. ${overflow}.` : shown;
}

// Called by registerStation as each loader resolves. Repaints the list so a panel
// that rendered before the stations arrived stops saying "Loading stations...".
//
// IT REFUSES TO REPAINT UNDER A FOCUSED ROW. Rebuilding the list replaces the
// button elements, and replacing the element that currently has focus drops focus
// to the body, which is the same stranding this phase spends a spec on. A rider
// tabbing through results while a slow loader resolves would be thrown out of the
// list. So the repaint is skipped whenever focus is inside the results, and the
// next keystroke in the search box picks the new stations up.
function stationsRegistryChanged() {
  if (!stationsPanelOpen() || !stationsResults) return;
  if (stationsResults.contains(document.activeElement)) return;
  renderStationResults();
}

/* ---------------- one station's arrivals, as text ---------------- */

function findStationEntry(key) {
  return stationRegistry.find((entry) => entry.key === key) || null;
}

// Selecting a station: render its detail, start its live updates, and sync the
// map. The map sync exists so a sighted keyboard user sees one application rather
// than two, and it must never take focus (see syncMapToStation).
function selectStation(key) {
  const entry = findStationEntry(key);
  if (!entry) return;
  panelStation = entry;
  panelBody = null;
  panelAnnounced = null;
  panelError = null;
  panelSeq++;
  // STOP THE OLD STATION'S TICK HERE, not in the fetch. Round 2 of the review found
  // that leaving it to fetchPanelArrivals leaks the interval whenever the new station
  // has no feed to fetch: selecting AirTrain after a subway station left the subway's
  // one-second timer running against the AirTrain detail, repainting it forever and
  // driving the live region across a scheduled-headway band boundary. A station
  // switch stops the clock unconditionally; whoever has something to count restarts it.
  stopPanelTick();
  renderStationDetail();
  if (entry.arrivalsUrl) fetchPanelArrivals();
  syncMapToStation(entry);
}

// The countdown clock, stopped and started in exactly one place each so there is one
// answer to "is a tick running" rather than one per caller.
function stopPanelTick() {
  clearInterval(panelTimer);
  panelTimer = null;
}

function stopPanelArrivals() {
  panelSeq++; // invalidate any fetch in flight for the station we are leaving
  stopPanelTick();
}

// Fetch this station's arrivals. The same shape as openStationArrivals in
// systems/shared.js, and deliberately so: a whole-fetch deadline bounds it, the
// sequence guard discards a response superseded by another selection or a close,
// and a failed BACKGROUND refresh keeps the last-known rows ticking instead of
// blanking good data. A first load surfaces its error, because there is nothing
// to keep.
async function fetchPanelArrivals({ refresh = false } = {}) {
  const entry = panelStation;
  if (!entry || !entry.arrivalsUrl) return;
  const seq = ++panelSeq;
  if (!refresh) stopPanelTick();
  let body;
  try {
    const res = await fetch(entry.arrivalsUrl, { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (seq !== panelSeq) return;
    if (!res.ok) {
      // A warming backend answers 503 with a detail line; show it rather than an
      // invented message, the same honesty the popups earned. Recorded in panelError
      // rather than only drawn, so closing and reopening the panel cannot lose it.
      if (!refresh) {
        const err = await res.json().catch(() => null);
        panelError = err && err.detail ? err.detail : `Arrivals unavailable (HTTP ${res.status})`;
        renderStationDetail();
      }
      return;
    }
    body = await res.json();
  } catch {
    if (seq !== panelSeq) return;
    if (!refresh) {
      panelError = "Arrivals unavailable (network error)";
      renderStationDetail();
    }
    return;
  }
  if (seq !== panelSeq) return;
  panelBody = body;
  panelError = null; // data arrived; whatever went wrong before is over
  renderStationDetail();
  startPanelTick();
}

// One tick a second repaints the countdowns. It repaints TEXT only; whether the live
// region speaks is decided by the payload comparison in renderStationDetail, never by
// this timer firing. Cleared first so two callers cannot leave two intervals running.
function startPanelTick() {
  stopPanelTick();
  panelTimer = setInterval(() => renderStationDetail({ tick: true }), 1000);
}

// The background refresh, called from map.js on the same cadence that refreshes an
// open popup, so the two surfaces never show data of different ages.
function refreshPanelArrivals() {
  if (panelStation && panelStation.arrivalsUrl && stationsPanelOpen()) {
    fetchPanelArrivals({ refresh: true });
  }
}

function panelClockNow() {
  return Date.now() / 1000 - (minClockOffset ?? 0);
}

// AirTrain's detail view: the SCHEDULED headway bands, labeled as scheduled.
// AirTrain publishes no realtime feed, so a countdown here would fabricate
// precision the data does not have. This is the branch any feedless system takes.
// RETURNS THE SPOKEN TEXT RATHER THAN SPEAKING IT. Round 2 of the review found the
// previous shape breaking this phase's one hard rule: this function called the live
// region directly and ignored `tick`, so a leaked countdown timer wrote the region as
// the scheduled text crossed a headway band. Announcing is the caller's job now, and
// every caller reaches the region through announceUnlessTick, the single door where
// the rule is enforced.
function renderScheduledDetail(entry, heading) {
  const note = "Scheduled service. AirTrain JFK publishes no live tracking.";
  const noteEl = document.createElement("p");
  noteEl.className = "station-detail-note";
  noteEl.textContent = note;
  stationsDetail.append(heading, noteEl);
  const serving = (entry.airtrainRoutes || []).filter((route) =>
    (route.stations || []).includes(entry.id),
  );
  if (!serving.length) {
    const none = document.createElement("p");
    none.textContent = "No AirTrain branch serves this station.";
    stationsDetail.appendChild(none);
    return `${entry.name}, ${entry.systemLabel}. ${note} No AirTrain branch serves this station.`;
  }
  const list = document.createElement("ul");
  list.className = "station-arrivals";
  const spoken = [];
  for (const route of serving) {
    const band = selectHeadwayBand(route.headways, nyMinutesSinceMidnight());
    const text = band
      ? `${route.name || route.id}: a train about every ${band.headway_min} minutes, scheduled`
      : `${route.name || route.id}: schedule unavailable`;
    const row = document.createElement("li");
    row.textContent = text;
    list.appendChild(row);
    spoken.push(text);
  }
  stationsDetail.appendChild(list);
  return `${entry.name}, ${entry.systemLabel}. ${note} ${spoken.join(". ")}`;
}

// Render the detail area for the selected station.
//
// `tick` marks a repaint driven by the one-second timer rather than by new data.
// It changes nothing about what is drawn; it exists so the announcement decision
// can be skipped outright on a tick, which makes the live-region rule impossible
// to violate by accident rather than merely unlikely.
function renderStationDetail({ error = panelError, tick = false } = {}) {
  if (!stationsDetail) return;
  const entry = panelStation;
  stationsDetail.replaceChildren();
  if (!entry) return;

  const heading = document.createElement("h3");
  heading.textContent = `${entry.name} (${entry.systemLabel})`;
  if (entry.wheelchair) {
    const label = document.createElement("span");
    label.className = "visually-hidden";
    label.textContent = ", wheelchair accessible";
    heading.appendChild(label);
  }

  if (!entry.arrivalsUrl) {
    const spoken = renderScheduledDetail(entry, heading);
    announceState(spoken, tick);
    return;
  }

  stationsDetail.appendChild(heading);

  if (error) {
    const problem = document.createElement("p");
    problem.className = "station-detail-note";
    problem.textContent = error;
    stationsDetail.appendChild(problem);
    announceState(`${entry.name}, ${entry.systemLabel}. ${error}`, tick);
    return;
  }
  if (!panelBody) {
    const loading = document.createElement("p");
    loading.className = "station-detail-note";
    loading.textContent = "Loading arrivals…";
    stationsDetail.appendChild(loading);
    return;
  }

  const now = panelClockNow();
  const shaped = shapeStationArrivals(entry.kind, panelBody, now, {
    nameFor: entry.nameFor || (() => null),
  });

  // The same "as of Xm ago" honesty the popups render, from the same threshold and
  // the same wording, so a stale feed reads identically on both surfaces.
  let staleLine = null;
  if (staleAge(shaped.ageSeconds)) {
    staleLine = `as of ${humanizeAge(shaped.ageSeconds)} ago`;
    const stale = document.createElement("p");
    stale.className = "station-detail-stale";
    stale.textContent = staleLine;
    stationsDetail.appendChild(stale);
  }

  if (!shaped.buckets.length) {
    const none = document.createElement("p");
    none.textContent = entry.noun === "boat" ? "No boats." : "No trains.";
    stationsDetail.appendChild(none);
  }

  for (const bucket of shaped.buckets) {
    const bucketHeading = document.createElement("h4");
    bucketHeading.textContent = bucket.name;
    stationsDetail.appendChild(bucketHeading);
    const list = document.createElement("ul");
    list.className = "station-arrivals";
    for (const row of bucket.rows) {
      const item = document.createElement("li");
      item.textContent = arrivalSentence(row, entry.noun);
      list.appendChild(item);
    }
    stationsDetail.appendChild(list);
  }

  announceArrivals(shaped, staleLine, tick);
}

// THE ONE DOOR TO THE LIVE REGION. Every write in this file goes through here, and
// the phase's one hard rule lives in this single line: a tick renders text and says
// nothing. Round 2 of the review earned this shape. The rule had been copied to each
// call site, one copy was missing, and a leaked timer walked straight through the gap;
// with one door, a fourth writer added later cannot forget the guard, because there is
// no other way to reach the region.
//
// Returns whether it spoke, so callers can keep their bookkeeping in step with what
// the rider actually heard rather than with what was merely rendered.
function announceUnlessTick(tick, text) {
  if (tick || !text || !stationsAnnounce) return false;
  stationsAnnounce.textContent = text;
  return true;
}

// The two states with NO arrivals payload to compare: a first-load failure, and the
// feedless scheduled view. Both render visible text, and both used to render it in
// total silence, which meant the only station kinds that spoke were the ones with a
// working feed. That is backwards: a rider who cannot see the panel is the one who
// most needs to be told that the arrivals did not arrive, or that this system
// publishes a schedule instead of live times.
//
// NO TEXT-EQUALITY DEDUP. There was one, comparing the region's own textContent
// against the new text, and it was removed on review: reading state back out of the
// DOM to decide whether to write to the DOM hides whatever work it is really doing.
// The paths that reach here are a station selection, a first-load failure, and a panel
// reopen, all of them one-shot or rider-initiated, so there is nothing to suppress.
// Re-announcing on a reopen is correct rather than duplicate: the region was gone from
// the accessibility tree while the panel was closed. The repeat that WOULD matter, a
// background refresh carrying unchanged data, never reaches this function at all; it
// goes through announceArrivals, where announcementWorthy is the guard, and A1r pins
// that it stays silent across two full refresh cycles.
function announceState(text, tick) {
  if (!announceUnlessTick(tick, text)) return;
  // Whatever payload lands next counts as news: recovering from an error, or moving
  // from a schedule to live times, IS something to say.
  panelAnnounced = null;
}

// The arrivals announcement. The detail area repaints every second; this fires only
// when the ARRIVALS changed in a way a rider would care about. announcementWorthy is
// the guard and helpers.js documents its three clauses. panelAnnounced advances only
// when the door actually opened, so a tick can neither speak nor quietly consume the
// change that the next real render owes the rider.
function announceArrivals(shaped, staleLine, tick) {
  if (!panelStation) return;
  if (!announcementWorthy(panelAnnounced, shaped)) return;
  const lines = [`${panelStation.name}, ${panelStation.systemLabel}`];
  // THE STALENESS TRAVELS WITH THE SPOKEN TEXT, not just the visible text. The
  // countdowns are read aloud whether or not the feed behind them is current, so a
  // rider listening to a stale payload would otherwise hear confident times with the
  // one caveat that qualifies them left on screen where they cannot see it.
  if (staleLine) lines.push(staleLine);
  for (const bucket of shaped.buckets) {
    const sentences = bucket.rows.map((row) => arrivalSentence(row, panelStation.noun));
    lines.push(`${bucket.name}: ${sentences.join(". ")}`);
  }
  if (!shaped.buckets.length) {
    lines.push(panelStation.noun === "boat" ? "No boats." : "No trains.");
  }
  if (!announceUnlessTick(tick, lines.join(". "))) return;
  panelAnnounced = shaped;
}

/* ---------------- the docked layout ---------------- */

// Wide viewports get the panel DOCKED and already open, per the placement
// decision: there is room for the list and the map at once, so hiding the list
// behind a button would be hiding it for no reason. Narrow viewports load closed,
// with the map greeting the rider and the panel one tap (or one skip link) away.
//
// OPENED WITHOUT MOVING FOCUS. openStationsPanel normally focuses the search
// input, which is right when a rider asked for the panel and wrong when the
// layout merely has room for it: stealing focus on page load is disorienting for
// a screen reader user and breaks the skip link's whole purpose.
const STATIONS_DOCK_QUERY = "(min-width: 1100px)";

// A3 review: THE CLASS MEANS "the panel is docked AND OPEN", not "the viewport is wide",
// and the difference was a defect. body.stations-docked drives two rules that reserve
// the panel's column: #map is offset and narrowed by 360px, and the alert banner is
// pushed clear of it. Both were keyed on the media query alone, so closing the panel at
// a wide width left the column reserved for something that was no longer there.
// Measured at 1280 before this change: with the panel closed, #map still reported
// {left: 360, width: 920} and elementFromPoint at (180, 400) returned BODY, a 360px
// strip of nothing beside a map squeezed out of it, with the banner still indented to
// 414px. Riders who close the panel are exactly the ones who wanted the map bigger.
//
// One function owns the class so the two callers cannot disagree about what it means:
// the breakpoint handler below and the panel's own open and close.
function applyDockedLayout() {
  const docked = typeof matchMedia === "function" && matchMedia(STATIONS_DOCK_QUERY).matches;
  const reserve = docked && stationsPanelOpen();
  if (document.body.classList.contains("stations-docked") === reserve) return;
  document.body.classList.toggle("stations-docked", reserve);
  // THE MAP MUST LEARN ITS NEW WIDTH. Docking narrows #map, and Leaflet caches its
  // container size, so without this the map stays sized for a viewport it no
  // longer has: tiles short of the right edge, and clicks landing on the wrong
  // coordinates. invalidateSize is Leaflet's sanctioned API for exactly this, and the
  // early return above is what keeps it to the moments that need it: the class actually
  // changing, which is a breakpoint crossing or the panel opening or closing at a wide
  // width. Never on a resize frame, never on the one-second tick. The phase's "no map
  // layout changes" constraint is about not restyling the map, which this does not do.
  if (typeof map !== "undefined" && map) map.invalidateSize();
}

function applyStationsDocking() {
  if (!stationsPanel || typeof matchMedia !== "function") return;
  // Opening comes first: it calls applyDockedLayout itself, so the class lands with the
  // panel rather than a frame before it. The call below then covers the other
  // direction, where the viewport left the docked range and the class has to come off.
  if (matchMedia(STATIONS_DOCK_QUERY).matches && !stationsPanelOpen()) {
    openStationsPanel({ focusSearch: false });
  }
  applyDockedLayout();
  applyOverlayInertness();
}

// A4: the overlay threshold is 700, not the 1100 dock threshold, so it needs its own
// listener. The path that matters is the unprompted one A6j already covers: a tablet
// docked at 1280 with the panel open, narrowed to a phone, becomes an overlay without
// the rider touching anything, and the page behind it has to go inert on the way down
// and come back on the way up.
if (typeof matchMedia === "function" && typeof MOBILE_QUERY === "string") {
  const overlayQuery = matchMedia(MOBILE_QUERY);
  if (typeof overlayQuery.addEventListener === "function") {
    overlayQuery.addEventListener("change", applyOverlayInertness);
  }
}

applyStationsDocking();
if (typeof matchMedia === "function") {
  const dockQuery = matchMedia(STATIONS_DOCK_QUERY);
  // addEventListener rather than the deprecated addListener, and only re-docking
  // on a real breakpoint crossing rather than on every resize frame.
  if (typeof dockQuery.addEventListener === "function") {
    dockQuery.addEventListener("change", applyStationsDocking);
  }
}

/* ---------------- map sync ---------------- */

// Pan the map to the selected station and open its popup, so a sighted keyboard
// user sees one application rather than two. Everything the panel offers works
// with this doing nothing at all: it is a convenience for a specific audience,
// not part of the accessible path.
//
// IT MUST NOT TAKE FOCUS. This is the only interaction with two focus
// authorities: Leaflet moves focus to a popup when it opens it, which would drag
// the rider out of the panel mid-search and leave Tab resuming from the map. So
// the focused element is captured before the transition and restored after, and
// the popup is opened without Leaflet's own autopan focus behavior.
function syncMapToStation(entry) {
  if (!entry.marker || typeof map === "undefined" || !map) return;
  const focused = document.activeElement;
  if (entry.layer && !map.hasLayer(entry.layer)) {
    // The rider hid this system's markers. Panning to an invisible dot and opening
    // nothing would be a silent no-op, so pan and skip the popup rather than
    // re-enabling a layer they deliberately turned off.
    map.panTo([entry.lat, entry.lon]);
  } else {
    map.panTo([entry.lat, entry.lon]);
    entry.marker.openPopup();
  }
  // Restore focus unconditionally rather than only when Leaflet moved it: cheaper
  // than detecting the several paths that can move it, and a no-op when it did not.
  if (focused && typeof focused.focus === "function" && document.activeElement !== focused) {
    focused.focus();
  }
}
