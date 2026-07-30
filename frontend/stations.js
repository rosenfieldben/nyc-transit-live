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
// The last SHAPED payload announced, and the shape currently rendered. The
// announcement guard compares payloads, never rendered text, so a countdown tick
// cannot reach the live region. See announcementWorthy in helpers.js.
let panelAnnounced = null;

/* ---------------- open, close, and where focus goes ---------------- */

function stationsPanelOpen() {
  return stationsPanel != null && !stationsPanel.hidden;
}

// Opening moves focus INTO the panel, to the search input, because the reason
// someone opened it is to search. Announcing the panel and leaving focus behind
// on the toggle would make the next Tab land somewhere unrelated.
function openStationsPanel({ focusSearch = true } = {}) {
  if (!stationsPanel) return;
  stationsPanel.hidden = false;
  if (stationsToggle) stationsToggle.setAttribute("aria-expanded", "true");
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
  renderStationDetail();
  if (!panelStation.arrivalsUrl) return;
  startPanelTick();
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
  const focusWasInside = stationsPanel.contains(document.activeElement);
  if (focusWasInside && stationsToggle) stationsToggle.focus();
  stationsPanel.hidden = true;
  if (stationsToggle) stationsToggle.setAttribute("aria-expanded", "false");
  stopPanelArrivals();
}

function toggleStationsPanel() {
  if (stationsPanelOpen()) closeStationsPanel();
  else openStationsPanel();
}

// Escape anywhere inside closes and returns focus. Bound on the panel rather than
// the document so it cannot swallow Escape from the rest of the page (the Leaflet
// popups use it too), and nothing here traps: Tab always leaves the panel.
if (stationsPanel) {
  stationsPanel.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.stopPropagation();
    closeStationsPanel();
  });
}

if (stationsToggle) stationsToggle.addEventListener("click", toggleStationsPanel);
if (stationsSearch) stationsSearch.addEventListener("input", renderStationResults);

/* ---------------- the result list ---------------- */

// The chip background and text color for one route, from the SAME color
// authorities the map markers and popups use, switched by system because the
// systems genuinely differ: subway route colors come from a fixed table with a
// known set of light backgrounds needing dark text, railroad has its own palette
// (its route ids collide with the subway's), and PATH and ferry colors are served
// per route by the backend and validated before use.
function stationChipStyle(entry, routeId) {
  if (entry.kind === "railroad") return { bg: railroadColor(routeId), fg: "#fff" };
  if (entry.kind === "path" || entry.kind === "ferry") {
    const colorFor = entry.colorFor || (() => null);
    return { bg: colorFor(routeId) || "#546e7a", fg: "#fff" };
  }
  if (entry.kind === "airtrain") return { bg: "#b5179e", fg: "#fff" };
  return { bg: lineColor(routeId), fg: DARK_TEXT_LINES.has(String(routeId)[0]) ? "#1a1a1a" : "#fff" };
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
  panelSeq++;
  renderStationDetail();
  if (entry.arrivalsUrl) fetchPanelArrivals();
  syncMapToStation(entry);
}

function stopPanelArrivals() {
  panelSeq++; // invalidate any fetch in flight for the station we are leaving
  clearInterval(panelTimer);
  panelTimer = null;
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
  if (!refresh) {
    clearInterval(panelTimer);
    panelTimer = null;
  }
  let body;
  try {
    const res = await fetch(entry.arrivalsUrl, { signal: AbortSignal.timeout(FETCH_DEADLINE_MS) });
    if (seq !== panelSeq) return;
    if (!res.ok) {
      // A warming backend answers 503 with a detail line; show it rather than an
      // invented message, the same honesty the popups earned.
      if (!refresh) {
        const err = await res.json().catch(() => null);
        renderStationDetail({
          error: err && err.detail ? err.detail : `Arrivals unavailable (HTTP ${res.status})`,
        });
      }
      return;
    }
    body = await res.json();
  } catch {
    if (seq !== panelSeq) return;
    if (!refresh) renderStationDetail({ error: "Arrivals unavailable (network error)" });
    return;
  }
  if (seq !== panelSeq) return;
  panelBody = body;
  renderStationDetail();
  startPanelTick();
}

// One tick a second repaints the countdowns. It repaints TEXT only; whether the live
// region speaks is decided by the payload comparison in renderStationDetail, never by
// this timer firing. Cleared first so two callers cannot leave two intervals running.
function startPanelTick() {
  clearInterval(panelTimer);
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
    announcePanelState(`${entry.name}, ${entry.systemLabel}. ${note} No AirTrain branch serves this station.`);
    return;
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
  announcePanelState(`${entry.name}, ${entry.systemLabel}. ${note} ${spoken.join(". ")}`);
}

// Render the detail area for the selected station.
//
// `tick` marks a repaint driven by the one-second timer rather than by new data.
// It changes nothing about what is drawn; it exists so the announcement decision
// can be skipped outright on a tick, which makes the live-region rule impossible
// to violate by accident rather than merely unlikely.
function renderStationDetail({ error = null, tick = false } = {}) {
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
    renderScheduledDetail(entry, heading);
    return;
  }

  stationsDetail.appendChild(heading);

  if (error) {
    const problem = document.createElement("p");
    problem.className = "station-detail-note";
    problem.textContent = error;
    stationsDetail.appendChild(problem);
    announcePanelState(`${entry.name}, ${entry.systemLabel}. ${error}`);
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

  if (!tick) maybeAnnounce(shaped, staleLine);
}

// THE LIVE REGION, and the one hard interaction rule of this phase. The detail
// area repaints every second; the announcement fires only when the ARRIVALS
// changed in a way a rider would care about. announcementWorthy is the guard and
// helpers.js documents its three clauses; what matters here is that the tick path
// never reaches this function at all, so "4 minutes... 3 minutes..." forever is
// structurally impossible rather than merely guarded against.
function maybeAnnounce(shaped, staleLine = null) {
  if (!stationsAnnounce || !panelStation) return;
  if (!announcementWorthy(panelAnnounced, shaped)) return;
  panelAnnounced = shaped;
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
  stationsAnnounce.textContent = lines.join(". ");
}

// The live region for the two states that have NO arrivals payload to compare: a
// first-load failure, and the feedless scheduled view. Both render visible text, and
// both used to render it in total silence, which meant the only station kinds that
// spoke were the ones with a working feed. That is exactly backwards: a rider who
// cannot see the panel is the one who most needs to be told that the arrivals did not
// arrive, or that this system publishes a schedule instead of live times.
//
// Deduplicated on the text itself, so a repaint of an unchanged state cannot re-speak
// the same sentence, and it clears panelAnnounced so that whatever payload lands next
// counts as news: recovering from an error IS something to say.
function announcePanelState(text) {
  if (!stationsAnnounce) return;
  if (stationsAnnounce.textContent === text) return;
  panelAnnounced = null;
  stationsAnnounce.textContent = text;
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

function applyStationsDocking() {
  if (!stationsPanel || typeof matchMedia !== "function") return;
  const docked = matchMedia(STATIONS_DOCK_QUERY).matches;
  document.body.classList.toggle("stations-docked", docked);
  if (docked && !stationsPanelOpen()) openStationsPanel({ focusSearch: false });
  // THE MAP MUST LEARN ITS NEW WIDTH. Docking narrows #map, and Leaflet caches its
  // container size, so without this the map stays sized for a viewport it no
  // longer has: tiles short of the right edge, and clicks landing on the wrong
  // coordinates. invalidateSize is Leaflet's sanctioned API for exactly this, and
  // it is called ONLY when the dock state is applied: once at load, and then on a
  // matchMedia change, which fires when the query flips rather than on every resize
  // frame. Never on the one-second tick. The phase's "no map layout changes"
  // constraint is about not restyling the map, which this does not do.
  if (typeof map !== "undefined" && map) map.invalidateSize();
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
