// Subway layer: train markers, route lines, station arrivals, and the per-poll
// apply. A plain <script> after systems/shared.js in the shared global scope.

/* ---------------- Subways ---------------- */

function trainIcon(train) {
  const route = train.route_id ?? "";
  const label = /^[A-Za-z0-9]{1,3}$/.test(route) ? route : "?";
  const color = lineColor(route);
  const textColor = DARK_TEXT_LINES.has(route[0]) ? "#1a1a1a" : "#ffffff";
  const html = `<svg viewBox="0 0 18 18">
      <rect x="1.5" y="1.5" width="15" height="15" rx="3" fill="${color}" stroke="#fff" stroke-width="1.5"/>
      <text x="9" y="9.5" text-anchor="middle" dominant-baseline="central"
            font-size="${label.length > 1 ? 7 : 9}" font-weight="700"
            font-family="system-ui, sans-serif" fill="${textColor}">${esc(label)}</text>
    </svg>`;
  return L.divIcon({ className: "train-marker", html, iconSize: [18, 18], iconAnchor: [9, 9] });
}

function trainPopup(record) {
  const t = record.latest;
  return (
    routeAlertsBlock("subway", t.route_id) +
    `<b style="color:${lineColor(t.route_id)}">${esc(t.route_id ?? "?")} train</b>` +
    `<br>Next stop: ${esc(t.stop_name ?? t.stop_id ?? "unknown")}` +
    (t.direction ? `<br>${esc(t.direction)}` : "") +
    `<br><span class="popup-sub">Trip ${esc(t.trip_id ?? "?")}</span>` +
    // C2: how old this train's own feed group is, when that group has gone stale.
    // A dimmed marker says "not current"; this says how far from current.
    stalePopupLine(subwaySystemAge(t))
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
  const groups = subwayRouteCoverage ? (subwayGroupsByRoute.get(train.route_id) ?? []) : [];
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

// Re-dim every subway marker from its own group's current age (C2).
staleTreatments.push(() => {
  for (const record of trains.values()) dimMarker(record.marker, subwaySystemAge(record.latest));
});

// Static route geometry, fetched once at startup (not polled). Canvas
// renderer keeps ~22k points cheap; lines are decorative, so failures are
// silent and the map just shows markers without them.
const lineRenderer = L.canvas({ padding: 0.3 });
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
    for (const points of route.polylines) {
      L.polyline(points, {
        color: lineColor(route.route),
        weight: 2.5,
        opacity: 0.5,
        interactive: false,
        renderer: lineRenderer,
      }).addTo(routeLinesLayer);
    }
  }
  return true;
}


function subwayArrivalsHtml(station, body) {
  // Skew-corrected now, reusing the staleness baseline from helpers.js.
  const now = Date.now() / 1000 - (minClockOffset ?? 0);
  // "as of Xm ago" when a failed refresh has left these rows stale (R1); empty
  // while fresh, so a live popup is unchanged.
  let html = `<b>${esc(station.name ?? station.id)}</b>` + feedAgeLine(body.fetched_at, now);
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
        const textColor = DARK_TEXT_LINES.has(route[0]) ? "#1a1a1a" : "#fff";
        const badge =
          `<span class="arr-badge" style="background:${lineColor(route)};color:${textColor}">` +
          `${esc(route || "?")}</span>`;
        return `${badge} ${esc(formatCountdown(a.arrival - now))}`;
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
  for (const station of stations) {
    const marker = L.circleMarker([station.lat, station.lon], {
      radius: 4,
      color: "#333",
      weight: 1.5,
      fillColor: "#fff",
      fillOpacity: 1,
      renderer: stationRenderer,
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
      // Placed through the freeze clock, not the raw one: a retained group's trains
      // must not advance on a poll that only re-served them (C2).
      const fresh = subwaySystemFreshness(train);
      record.marker.setLatLng(trainLatLng(train, glideClock(now, fresh.staleAt), record.fState));
      dimMarker(record.marker, fresh.age);
      if (record.routeId !== train.route_id) {
        record.marker.setIcon(trainIcon(train));
        record.routeId = train.route_id;
      }
      if (record.marker.isPopupOpen()) record.marker.getPopup().update();
    } else {
      const newRecord = { routeId: train.route_id, latest: train, fState: {} };
      newRecord._segId = `${train.route_id}|${train.prev_time}|${train.stop_id}`;
      train._route = computeRouteSlice(train, routeIndex.get(train.route_id));
      const fresh = subwaySystemFreshness(train);
      newRecord.marker = L.marker(
        trainLatLng(train, glideClock(now, fresh.staleAt), newRecord.fState),
        {
          icon: trainIcon(train),
          // Dimmed AT CREATION, not by the sweep afterwards: a retained train first
          // seen during an outage (a reload mid-outage) must be dim on the very
          // first frame it exists, never full-opacity for a beat. This is the
          // invariant the "C2b" e2e spec pins, and the reason the retention flag and
          // this rendering ship in one commit.
          opacity: markerOpacity(fresh.age),
        },
      )
        .bindPopup(() => trainPopup(newRecord))
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
}

