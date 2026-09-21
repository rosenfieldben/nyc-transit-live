// Handcrafted API fixtures for the e2e smoke suite. Field names and shapes match
// backend/models.py and the endpoint docstrings exactly (Vehicle, Train,
// RailroadTrain, SubwayStop/Route, RailroadStop/Route, StationArrivals with
// Arrival, RailroadStationArrivals with RailroadArrival). Each accessor returns a
// FRESH object so a test mutating a response cannot bleed into another.
//
// Times are epoch SECONDS relative to a frozen clock (FROZEN_MS), which the tests
// install via page.clock. With fetched_at == FROZEN_S the frontend's clock-skew
// offset is 0, so a countdown reads exactly (arrival - FROZEN_S): +90s renders
// "2 min" and, one second later, "1 min" (the boundary the tick test relies on).
//
// THE CONTRACT 6.1 FIELDS, which the backend has served since that step and which these
// fixtures did not describe until the boards started reading them. Every arrivals row
// carries observed_at, the provider's own clock for that prediction (whichever clock
// section 3.3 of docs/design/freshness-contract.md names for its system), and a
// provenance; every arrivals envelope carries feed_timestamp, served_at and systems,
// each the way its own endpoint serves them; every per-system block carries its own
// feed_timestamp. The stock values are FRESH, a few seconds behind FROZEN_S, so a board
// rendered at FROZEN has nothing to qualify. frontend/boards.test.js pins exactly that.
//
// AND SINCE 6.3 EVERY VEHICLE ROW CARRIES THE SAME PAIR, because the map now reads it:
// a marker's words, its dimming and its glide freeze come from its row's own provenance
// and observed_at (positionQualifier), so a row without them would read "age unknown"
// and every existing spec would be asserting about a different map. Each row carries the
// provenance its system's decoder serves (feeds/*.py): buses and ferry boats `reported`,
// subway and PATH trains `placed`, NJ Transit's in-transit train `estimated` and its
// others `placed`, Metro-North's GPS train `reported`, LIRR's train with no vehicle
// `placed`. Its observed_at is FRESH: 5 s behind the poll that serves it, the envelope's
// own content convention, or null for Metro-North, which dates nothing. A ROW'S CLOCK
// FOLLOWS ITS POLL: every builder that takes a fetchedAt stamps its rows from it
// (stampObserved), so a spec that serves a later poll serves observations as young as
// that poll, and a spec that means an OLD observation sets one on the built body, where
// nothing stamps over it. The ferry keeps the backend's own pair, observed_at equal to
// updated_at, both of them the boat's vehicle.timestamp.

const FROZEN_MS = Date.UTC(2026, 6, 2, 12, 0, 0); // 2026-07-02T12:00:00Z
const FROZEN_S = FROZEN_MS / 1000;

// How old a fixture row's own observation is at the poll that serves it: fresh, and the
// same five seconds the envelope's feed_timestamp trails its fetched_at by.
const OBSERVED_LAG_S = 5;

// Rows with their observation stamped from the poll that serves them. A row whose
// observed_at is null keeps it: that is a provider that dates nothing (Metro-North), not
// a stamp waiting to be filled.
const stampObserved = (rows, fetchedAt) =>
  (rows || []).map((row) => (row.observed_at === null ? row : { ...row, observed_at: fetchedAt - OBSERVED_LAG_S }));

// A successful feed envelope: content 5s old at poll time (fresh). served_at (R1) is
// the response build time; it defaults to fetchedAt (served the instant it was
// polled, so zero server cache age), and a stale-serve stub passes served_at AHEAD of
// fetched_at to model a backend still serving a poll that is already N seconds old.
const envelope = (data, fetchedAt = FROZEN_S, servedAt = fetchedAt) => ({
  fetched_at: fetchedAt,
  feed_timestamp: fetchedAt - 5,
  served_at: servedAt,
  data: stampObserved(data, fetchedAt),
});

// ---- Per-system freshness blocks (C2) ----
//
// One entry of an aggregate envelope's `systems` map, matching models.SystemFreshness:
// this system's own last decode, its own content time (6.1), whether its last poll
// succeeded, since when its data has been carried forward, and (subway only) which
// routes its served data covers. Defaults describe a HEALTHY system, so a partial-outage
// fixture states only the system that is down.
//
// feed_timestamp defaults to 5s behind this system's own fetched_at, the envelope's
// convention, and it FREEZES with fetched_at for a system whose fetched_at is frozen,
// which is what pollers._system_freshness does: a failed system keeps the content time
// it last reported. Pass `feedTimestamp: null` for a system with no content clock at
// all, which is Metro-North's standing answer.
//
// `positions` (6.3) is the position ladder's five counts, which the backend serves on
// the railroad blocks and as null on every other (models.PositionSteps): pass
// positionSteps({...}) for a railroad system.
const systemBlock = (
  fetchedAt,
  {
    ok = true,
    retainedSince = null,
    routes = null,
    feedTimestamp = fetchedAt == null ? null : fetchedAt - 5,
    positions = null,
  } = {},
) => ({
  fetched_at: fetchedAt,
  feed_timestamp: feedTimestamp,
  ok,
  retained_since: retainedSince,
  routes,
  positions,
});

// One railroad system's position-ladder counts, all zero unless given: reported,
// estimated, qualified, placed and suppressed (section 3.4's steps 1 to 5).
const positionSteps = (over = {}) => ({
  reported: 0,
  estimated: 0,
  qualified: 0,
  placed: 0,
  suppressed: 0,
  ...over,
});

// The subways envelope with per-system blocks for the two feed groups the fixtures'
// trains belong to: route "1" rides the 1-7+S group, route "A" the ACE group.
// `aceAt` is ACE's own last decode (pass an older stamp to model a down ACE), and
// `aceOk` / `aceRetainedSince` complete that picture. `fetchedAt` is the ENVELOPE's,
// which keeps advancing on a partial outage, which is the whole reason the per-system
// blocks exist.
const subwaysWithSystems = ({
  data,
  fetchedAt = FROZEN_S,
  servedAt = fetchedAt,
  aceAt = fetchedAt,
  aceOk = true,
  aceRetainedSince = null,
  aceRoutes = ["A"],
  // ACE's own content time (6.1). Pass an old one with a CURRENT aceAt to model F03's
  // state: a poll that keeps succeeding over content that stopped moving.
  aceContentAt = aceAt - 5,
} = {}) => ({
  fetched_at: fetchedAt,
  // The envelope's content clock is the oldest header among the groups that decoded,
  // which is how feeds/subway.py folds them; a failed ACE did not decode this poll.
  feed_timestamp: aceOk ? Math.min(fetchedAt - 5, aceContentAt) : fetchedAt - 5,
  served_at: servedAt,
  data: stampObserved(data ?? subways().data, fetchedAt),
  systems: {
    "1-7+S": systemBlock(fetchedAt, { routes: ["1"] }),
    ACE: systemBlock(aceAt, {
      ok: aceOk,
      retainedSince: aceRetainedSince,
      routes: aceRoutes,
      feedTimestamp: aceContentAt,
    }),
  },
});

// The railroads envelope with per-system blocks. LIRR stays fresh at the envelope's
// own stamp; MNR's is passed in, so a test can freeze MNR while LIRR advances.
//
// Each block carries its position-ladder counts (6.3), as the backend serves them for
// this fixture's world: LIRR's one train has no vehicle, so the ladder counts nothing
// for it, and Metro-North's GPS train is one `reported`. Pass `lirrPositions` to put a
// suppressed count on the status line. `lirrAt` / `lirrOk` / `lirrRetainedSince` are
// MNR's three for LIRR, so a spec can take LIRR down instead; its rows keep whatever
// provenance and observed_at the spec gives them after the build (a retained row keeps
// the clock it was observed at, which stampObserved would otherwise move to this poll).
const railroadsWithSystems = ({
  data,
  fetchedAt = FROZEN_S,
  servedAt = fetchedAt,
  mnrAt = fetchedAt,
  mnrOk = true,
  mnrRetainedSince = null,
  lirrAt = fetchedAt,
  lirrOk = true,
  lirrRetainedSince = null,
  lirrPositions = positionSteps(),
  mnrPositions = positionSteps({ reported: 1 }),
} = {}) => ({
  fetched_at: fetchedAt,
  feed_timestamp: fetchedAt - 5,
  served_at: servedAt,
  data: stampObserved(data ?? railroads().data, fetchedAt),
  systems: {
    LIRR: systemBlock(lirrAt, { ok: lirrOk, retainedSince: lirrRetainedSince, positions: lirrPositions }),
    // NULL, NOT MISSING: Metro-North's header is a lagging copy, so the backend never
    // publishes it as MNR's content time (feeds.RAILROAD_FRESHNESS_SYSTEMS).
    MNR: systemBlock(mnrAt, {
      ok: mnrOk,
      retainedSince: mnrRetainedSince,
      feedTimestamp: null,
      positions: mnrPositions,
    }),
  },
});

// The alerts envelope with per-system blocks for every alert system. All fresh at
// `fetchedAt` except the one named by `frozen`, which is the partial-outage case the
// C1 marker could not express (a poll where all but one decode is a SUCCESS, so the
// envelope's own fetched_at keeps advancing). `frozen` may also be given a
// retained_since, which is what the backend records for a system whose alerts are
// being carried forward from an earlier poll while its feed is down.
//
// "njt" JOINED THE LIST WITH F11, and it was missing rather than excluded: 15c added
// the NJ Transit alerts feed to ALERT_FEED_URLS in feeds/alerts.py, and this fixture
// still described the five that predated it. Nothing downstream moved when it was
// added, because every system here shares one fetched_at and alertsFreshnessBasis
// takes the minimum.
const ALERT_SYSTEMS = ["subway", "bus", "LIRR", "MNR", "ferry", "njt"];

const alertsWithSystems = ({
  alerts: list = [],
  fetchedAt = FROZEN_S,
  servedAt = fetchedAt,
  frozen = null,
  frozenAt = fetchedAt,
  retainedSince = frozenAt,
} = {}) => ({
  fetched_at: fetchedAt,
  served_at: servedAt,
  alerts: list,
  systems: Object.fromEntries(
    ALERT_SYSTEMS.map((system) => [
      system,
      system === frozen
        ? systemBlock(frozenAt, { ok: false, retainedSince })
        : systemBlock(fetchedAt),
    ]),
  ),
});

const buses = () =>
  envelope([
    {
      id: "MTA NYCT_101", route_id: "M15", latitude: 40.72, longitude: -73.98, bearing: 90.0,
      observed_at: FROZEN_S - 5, provenance: "reported",
    },
    {
      id: "MTA NYCT_102", route_id: "B46", latitude: 40.68, longitude: -73.94, bearing: null,
      observed_at: FROZEN_S - 5, provenance: "reported",
    },
  ]);

const subways = () =>
  envelope([
    {
      trip_id: "sub-1",
      route_id: "1",
      latitude: 40.75,
      longitude: -73.99,
      stop_id: "127N",
      stop_name: "Times Sq-42 St",
      direction: "Northbound",
      prev_lat: 40.74,
      prev_lon: -73.99,
      prev_time: FROZEN_S - 60,
      next_time: FROZEN_S + 60,
      observed_at: FROZEN_S - 5,
      provenance: "placed",
    },
    {
      trip_id: "sub-2",
      route_id: "A",
      latitude: 40.71,
      longitude: -74.01,
      stop_id: "A31S",
      stop_name: "Canal St",
      direction: "Southbound",
      prev_lat: 40.72,
      prev_lon: -74.0,
      prev_time: FROZEN_S - 30,
      next_time: FROZEN_S + 90,
      observed_at: FROZEN_S - 5,
      provenance: "placed",
    },
  ]);

// One GPS train (real position, null station/anchor fields) and one placed train (at
// its next station), exactly as the two decode paths emit.
//
// THE PLACED TRAIN CARRIES NO ANCHORS, since 6.3. That is what the placement decode
// emits on a train's first poll (none of the 56 placed rows in the committed LIRR golden
// has one; the poller carries the previous stop forward from the second poll on), and it
// is what draws the train ON its station, which is what the cross-link specs need now
// that the link asks railroadAtItsStation rather than stop_id. A spec about the glide
// gives it anchors itself (C2a). Metro-North's train is undated, as all of Metro-North
// is; LIRR's is fresh at the poll that serves it (stampObserved).
const railroads = () =>
  envelope([
    {
      system: "MNR",
      trip_id: "mnr-gps-1",
      route_id: "1",
      latitude: 40.9,
      longitude: -73.78,
      bearing: 210.0,
      train_num: "1797",
      stop_id: null,
      stop_name: null,
      direction: null,
      prev_lat: null,
      prev_lon: null,
      prev_time: null,
      next_time: null,
      observed_at: null,
      provenance: "reported",
    },
    {
      system: "LIRR",
      trip_id: "lirr-placed-1",
      route_id: "1",
      latitude: 40.7005,
      longitude: -73.8095,
      bearing: null,
      train_num: "521",
      stop_id: "12",
      stop_name: "Jamaica",
      direction: "Outbound",
      prev_lat: null,
      prev_lon: null,
      prev_time: null,
      next_time: FROZEN_S + 180,
      observed_at: FROZEN_S - 5,
      provenance: "placed",
    },
  ]);

// routes: the H5 routes-per-station index. Times Sq serves 1/2/3; the arrivals
// fixture only has imminent 1 and 2 trains, so route "3" is present ONLY in this
// static list, which is exactly the "serves the station but no imminent train" case
// the routes-per-station join closes (test 27).
const subwayStops = () => [
  { id: "127", name: "Times Sq-42 St", lat: 40.7554, lon: -73.9874, routes: ["1", "2", "3"] },
  { id: "A31", name: "Canal St", lat: 40.7227, lon: -74.0057, routes: ["A", "C", "E"] },
];

const subwayRoutes = () => [
  { route: "1", polylines: [[[40.74, -73.99], [40.75, -73.99], [40.76, -73.98]]] },
  { route: "A", polylines: [[[40.71, -74.01], [40.72, -74.0]]] },
];

/* `routes` IS SERVED AND WAS MISSING HERE, which ruling R3 is what found. backend/models.py's
   RailroadStop carries `routes: list[str]` and backend/routes/railroad.py fills it from the static
   archive, and this fixture never had the field: so the panel's railroad chips and (from R3) the
   popup kicker's branch tags had nothing to draw in any hermetic world, and a pin of either would
   have been a pin of an empty span. That is trap T4 in pins.spec.js's own words, "every emptiness
   that used to be loud becomes silent the moment it is written into the golden". One route each,
   which is what railroadRoutes below carries for them. */
const railroadStops = () => [
  { system: "LIRR", id: "12", name: "Jamaica", lat: 40.7005, lon: -73.8095, routes: ["1"] },
  { system: "MNR", id: "1", name: "Grand Central", lat: 40.7527, lon: -73.9772, routes: ["1"] },
];

/* THE TWO COLOUR FIELDS, as claude/railroad-route-colors added them and MR3 draws them: the
   real values the live feeds publish for these two routes, hex with no "#", each with the
   route_text_color the agency publishes beside it. Both of those inks are ILLEGIBLE on their
   own colour (white on Babylon's green reads 3.71 and on Hudson's green 3.65), which is not a
   quirk of this fixture but what the feeds actually serve, so these two rows exercise
   railBranchPaint's recompute-the-ink branch on the live page rather than only in node. */
const railroadRoutes = () => [
  {
    system: "LIRR", route: "1", name: "Babylon Branch", color: "00985F", text_color: "FFFFFF",
    polylines: [[[40.7, -73.8], [40.69, -73.6]]],
  },
  {
    system: "MNR", route: "1", name: "Hudson", color: "009B3A", text_color: "FFFFFF",
    polylines: [[[40.9, -73.78], [41.0, -73.86]]],
  },
];

// A served prediction's contract pair (6.1): the provider's clock for it, and how it
// was derived. Every arrivals row the backend emits is `reported`.
const reported = (observedAt) => ({ observed_at: observedAt, provenance: "reported" });

// Subway station arrivals. The first Northbound arrival is at +90s so the popup
// reads "2 min" on open and "1 min" one second later (the countdown-tick test).
// Every row is dated by its feed GROUP's header, since no subway trip update dates
// itself; one group (1-7+S) contributes here, and its block is the board's `systems`.
const subwayArrivals = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  station_id: "127",
  station_name: "Times Sq-42 St",
  directions: {
    Northbound: [
      { route_id: "1", trip_id: "sub-1", arrival: FROZEN_S + 90, ...reported(FROZEN_S - 5) },
      { route_id: "2", trip_id: "sub-3", arrival: FROZEN_S + 300, ...reported(FROZEN_S - 5) },
    ],
    Southbound: [
      { route_id: "1", trip_id: "sub-2", arrival: FROZEN_S + 180, ...reported(FROZEN_S - 5) },
    ],
  },
  served_at: FROZEN_S,
  systems: { "1-7+S": systemBlock(FROZEN_S, { routes: ["1", "2"] }) },
});

// Railroad (MNR) station arrivals. MNR omits direction_id, so the backend INFERS
// Inbound/Outbound from the stop progression; both are directional buckets here
// (RailroadArrival carries train_num, which subway arrivals do not).
//
// UNDATED, AND THAT IS THE POLICY RATHER THAN A GAP: Metro-North dates none of its
// predictions, so every row's observed_at is null and so is the envelope's content
// clock, while its system block still carries the poll time.
const railroadArrivals = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: null,
  system: "MNR",
  stop_id: "1",
  stop_name: "Grand Central",
  directions: {
    Inbound: [
      { route_id: "1", trip_id: "mnr-3117769", arrival: FROZEN_S + 240, train_num: "795", ...reported(null) },
    ],
    Outbound: [
      { route_id: "1", trip_id: "mnr-3117770", arrival: FROZEN_S + 360, train_num: "812", ...reported(null) },
    ],
  },
  served_at: FROZEN_S,
  systems: { MNR: systemBlock(FROZEN_S, { feedTimestamp: null }) },
});

// Railroad (LIRR) station arrivals for Jamaica. LIRR is the one provider that dates
// each prediction itself (trip_update.timestamp), so the two rows carry two different
// clocks, both fresh, while the envelope's content clock is the LIRR feed header.
const railroadArrivalsLirr = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  system: "LIRR",
  stop_id: "12",
  stop_name: "Jamaica",
  directions: {
    Inbound: [
      { route_id: "1", trip_id: "lirr-8412", arrival: FROZEN_S + 240, train_num: "8412", ...reported(FROZEN_S - 20) },
    ],
    Outbound: [
      { route_id: "1", trip_id: "lirr-8413", arrival: FROZEN_S + 420, train_num: "8413", ...reported(FROZEN_S - 40) },
    ],
  },
  served_at: FROZEN_S,
  systems: { LIRR: systemBlock(FROZEN_S) },
});

// ---- NJ Transit Rail (15c) ----
//
// Three stations, two drawable routes, and four trains, chosen so the layer's two
// awkward cases are both on the page rather than described in a comment:
//
//   * ROUTE 17 HAS NO LINE. Hoboken lists it among the routes serving the station
//     and two trains run on it, and it never appears on /api/njt-routes. In the real
//     feed that is the event-only Meadowlands Rail Line, which has no trips at all
//     in an ordinary publication; the shape modeled here is the one a CLIENT can
//     actually meet, a route with trips whose geometry the publication does not
//     draw, because a route with no trips also has no trains and no station listing
//     it. Either way the client's question is the same: a route id it has no entry
//     for. Every name and colour lookup must fall back rather than blank.
//   * TWO ADDED TRIPS SHARE AN EMPTY trip_id. NJ Transit publishes them that way,
//     which is why the marker map is keyed on the backend-minted `id`. Two entries
//     here means the ADDED-trips coverage is a count assertion rather than a claim.
const njtStops = () => [
  { id: "109", name: "New York Penn Station", lat: 40.750568, lon: -73.993519, routes: ["9", "2"] },
  { id: "112", name: "Newark Penn Station", lat: 40.734924, lon: -74.164581, routes: ["9"] },
  // HOBOKEN'S ID COLLIDES WITH LIRR JAMAICA'S ON PURPOSE. NJT ids are bare integers
  // 1..176 and the railroad's are bare integers too; the contract tier has measured
  // 21 of 24 ferry dock ids colliding with Metro-North station ids, so this is the
  // real shape rather than an invented one. It is here so the system-qualified
  // registry key is FALSIFIABLE: crossLinkHtml finds the first row whose key
  // matches, the railroad stations register before the NJT ones, and a bare-id key
  // therefore sends a rider standing at Hoboken to Jamaica, Queens.
  { id: "12", name: "Hoboken", lat: 40.734984, lon: -74.027683, routes: ["2", "17"] },
];

// text_color is null on every route, exactly as the feed publishes it (empty on all
// twelve as of the 2026-08-05 probe), so the client has to compute its own ink.
// Route 2 carries TWO polylines, which is what the backend's dedup leaves on a
// branching line: the Hoboken leg and the New York Penn leg both reach a terminus
// the other does not.
/* short_name IS THE FEED'S route_short_name and the endpoint began serving it on
   claude/mr3-rail, because the map's commuter rail tag prints a short branch code and the
   brief's section 6 says NJ Transit's comes from that column rather than from a hand-written
   table. text_color stays null on both, which is what the live feed publishes for all twelve
   routes: the tag computes its own ink there, and that asymmetry with the railroads (which
   publish one on every route) is exactly what railBranchPaint exists to carry. */
const njtRoutes = () => [
  {
    route: "9", name: "Northeast Corridor", short_name: "NEC", color: "DD3439", text_color: null,
    polylines: [[[40.734924, -74.164581], [40.7425, -74.07], [40.750568, -73.993519]]],
  },
  {
    route: "2", name: "Montclair-Boonton Line", short_name: "MNBTN", color: "E66859", text_color: null,
    polylines: [
      [[40.734984, -74.027683], [40.79, -74.15], [40.86, -74.22]],
      [[40.750568, -73.993519], [40.79, -74.15], [40.86, -74.22]],
    ],
  },
];

// The NJT envelope, with the single-entry `systems` block keyed "njt" that
// models.NjtFeed serves. `at` is NJT's own last decode, so a test can freeze NJ
// Transit while the envelope's fetched_at keeps advancing, which is the partial
// outage the whole C2 apparatus exists for.
const njtWithSystems = ({
  trains,
  fetchedAt = FROZEN_S,
  servedAt = fetchedAt,
  at = fetchedAt,
  ok = true,
  retainedSince = null,
} = {}) => ({
  fetched_at: fetchedAt,
  feed_timestamp: fetchedAt - 5,
  served_at: servedAt,
  trains: stampObserved(trains ?? njt().trains, fetchedAt),
  systems: { njt: systemBlock(at, { ok, retainedSince }) },
});

const njt = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  served_at: FROZEN_S,
  trains: [
    // In transit on route 9 with both anchors, so it glides Newark -> New York along
    // the route polyline rather than the straight chord.
    //
    // ITS latitude/longitude IS THE INTERPOLATED CURRENT POSITION, not station 109's
    // coordinates, and the difference is the whole point of writing it out. This is
    // what backend/feeds/njt.py case 3 emits (it calls _interpolate and puts the
    // result here), and the first draft of this fixture hand-wrote 109's coordinates
    // instead, which is a payload that decoder cannot produce for an in-transit
    // train. That made the fixture agree with a client bug: the glide helpers expect
    // the NEXT STATION in these fields, so a fixture that supplied one hid a marker
    // being drawn at f squared of its segment. 40.741182 / -74.096156 is exactly 0.4
    // of the way from 112 to 109, matching prev_time and next_time below (120s of a
    // 300s leg elapsed at FROZEN_S).
    //
    // SERVED `estimated`, the one NJ Transit branch that is not `placed` (feeds/njt.py
    // case 3 interpolated it between two stops), so since 6.3 its popup and name say
    // "estimated from a prediction" where they said "scheduled position".
    {
      id: "NJ_3800", trip_id: "NJ_3800", route_id: "9", headsign: "New York",
      train_num: "3800", latitude: 40.741182, longitude: -74.096156,
      status: "in-transit", stop_id: "109", stop_name: "New York Penn Station",
      delay: 250,
      prev_lat: 40.734924, prev_lon: -74.164581, prev_time: FROZEN_S - 120,
      next_time: FROZEN_S + 180,
      observed_at: FROZEN_S - 5, provenance: "estimated",
    },
    // Dwelling at Hoboken: null anchors, so it sits placed on the station square
    // (trainLatLng's own fallback) and its popup carries the cross-link.
    {
      id: "NJ_6633", trip_id: "NJ_6633", route_id: "2", headsign: "Hackettstown",
      train_num: "6633", latitude: 40.734984, longitude: -74.027683,
      status: "at-station", stop_id: "12", stop_name: "Hoboken", delay: 0,
      prev_lat: null, prev_lon: null, prev_time: null, next_time: null,
      observed_at: FROZEN_S - 5, provenance: "placed",
    },
    // The two ADDED trips. Empty trip_id on both, distinct backend ids, and a route
    // that /api/njt-routes never serves.
    {
      id: "njt:9001", trip_id: "", route_id: "17", headsign: "Meadowlands",
      train_num: "9001", latitude: 40.7401, longitude: -74.0701,
      status: "approaching", stop_id: null, stop_name: null, delay: null,
      prev_lat: null, prev_lon: null, prev_time: null, next_time: null,
      observed_at: FROZEN_S - 5, provenance: "placed",
    },
    {
      id: "njt:9002", trip_id: "", route_id: "17", headsign: "Hoboken",
      train_num: "9002", latitude: 40.7402, longitude: -74.0702,
      status: "approaching", stop_id: null, stop_name: null, delay: null,
      prev_lat: null, prev_lon: null, prev_time: null, next_time: null,
      observed_at: FROZEN_S - 5, provenance: "placed",
    },
  ],
});

// NJT station departures for New York Penn Station: FLAT and chronological, with no
// direction buckets, which is what /api/njt-arrivals serves. The first row is at
// +90s, the same countdown boundary the subway and PATH fixtures use.
// Every row is dated by the TripUpdates header, the only clock NJ Transit sends.
const njtArrivals = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  stop_id: "109",
  stop_name: "New York Penn Station",
  arrivals: [
    {
      train_num: "3800", route_id: "9", headsign: "Trenton",
      arrival: FROZEN_S + 90, departure: FROZEN_S + 120, delay: 250, trip_id: "NJ_3800",
      ...reported(FROZEN_S - 5),
    },
    {
      train_num: "6634", route_id: "2", headsign: "Dover",
      arrival: FROZEN_S + 300, departure: FROZEN_S + 330, delay: null, trip_id: "NJ_6634",
      ...reported(FROZEN_S - 5),
    },
    // A ROUTE-LESS ROW, which models.NjtArrival declares (route_id: str | None) and
    // feeds/njt.py really produces: an ADDED trip whose TripDescriptor omits
    // route_id and joins no static trip has none to recover. Present here because
    // the badge's own fallback was otherwise exercised by nothing at any tier, and a
    // board that printed the literal "null" in a route badge is what that guard is
    // for.
    {
      train_num: null, route_id: null, headsign: "Bay Head",
      arrival: FROZEN_S + 480, departure: FROZEN_S + 500, delay: null, trip_id: "",
      ...reported(FROZEN_S - 5),
    },
  ],
  served_at: FROZEN_S,
  systems: { njt: systemBlock(FROZEN_S) },
});

const busRoute = () => ({
  route: "M15",
  directions: [[[40.72, -73.98], [40.73, -73.98], [40.74, -73.97]]],
});

// AirTrain JFK static layer (no realtime feed). Matches AirTrainData: {stations,
// routes}, each route with an ordered polyline, the station ids it serves, and
// non-overlapping scheduled headway bands. At the frozen clock (12:00Z is 08:00
// America/New_York in July), the 06:00-11:00 band applies, so popups read "7 min".
const AIRTRAIN_BANDS = [
  { start: "00:00", end: "06:00", headway_min: 15 },
  { start: "06:00", end: "11:00", headway_min: 7 },
  { start: "11:00", end: "22:00", headway_min: 4 },
  { start: "22:00", end: "24:00", headway_min: 7 },
];

const airtrain = () => ({
  stations: [
    { id: "A", name: "Terminal Alpha", lat: 40.645, lon: -73.785 },
    { id: "B", name: "Federal Circle", lat: 40.66, lon: -73.803 },
    { id: "C", name: "Jamaica", lat: 40.7, lon: -73.808 },
  ],
  routes: [
    // Federal Circle (B) is served by BOTH branches; Jamaica (C) only by R1.
    { id: "R1", name: "Jamaica", polyline: [[40.7, -73.808], [40.66, -73.803], [40.645, -73.785]], stations: ["C", "B", "A"], headways: AIRTRAIN_BANDS },
    { id: "R2", name: "Howard Beach", polyline: [[40.66, -73.803], [40.645, -73.785]], stations: ["B", "A"], headways: AIRTRAIN_BANDS },
  ],
});

// PATH static layer (13a shapes). Two parent stations (WTC first, so
// pathStations.getLayers()[0] is a deterministic click target) and two routes,
// each with the modal polyline per direction (so 4 polylines total).
// `routes` for the same reason as railroadStops above (backend/models.py PathStop carries it and
// backend/routes/path.py fills it): World Trade Center is served by both routes this fixture
// publishes, Newark by the one that reaches it.
const pathStops = () => [
  { id: "26734", name: "World Trade Center", lat: 40.71271, lon: -74.01193, routes: ["862", "859"] },
  { id: "26733", name: "Newark", lat: 40.73454, lon: -74.16375, routes: ["862"] },
];

const pathRoutes = () => [
  {
    id: "862", name: "Newark - World Trade Center", color: "d93a30", text_color: "ffffff",
    shape: [
      [[40.73454, -74.16375], [40.7334, -74.0629], [40.71271, -74.01193]],
      [[40.71271, -74.01193], [40.7334, -74.0629], [40.73454, -74.16375]],
    ],
  },
  {
    id: "859", name: "Hoboken - 33rd", color: "4d92fb", text_color: "ffffff",
    shape: [
      [[40.73573, -74.02944], [40.74913, -73.98816]],
      [[40.74913, -73.98816], [40.73573, -74.02944]],
    ],
  },
];

// The PATH realtime feed, SERVED shape (13d): envelope key is `trains` (not
// `data`), and every train carries the backend-minted stable `id`; the
// bridge's unstable trip hash never reaches this payload (matcher contract),
// so the e2e stubs stopped modeling disjoint raw ids when the backend took
// identity over. Anchors are null in the steady state (trains sit placed).
const path = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  served_at: FROZEN_S,
  trains: [
    // PATH dates every train by its own trip update (section 3.3) and places it at its
    // stop, so each row is `placed` with that trip update's clock.
    {
      id: "p-1", route_id: "862",
      latitude: 40.71271, longitude: -74.01193, stop_id: "26734",
      stop_name: "World Trade Center", direction: "To New York",
      prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 120,
      observed_at: FROZEN_S - 5, provenance: "placed",
    },
    {
      id: "p-2", route_id: "862",
      latitude: 40.73454, longitude: -74.16375, stop_id: "26733",
      stop_name: "Newark", direction: "To New York",
      prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 15,
      observed_at: FROZEN_S - 5, provenance: "placed",
    },
  ],
});

// The NEXT poll after path(): p-2 advanced Newark -> World Trade Center and
// gained the glide anchor pair (prev = Newark, prev_time = its predicted
// arrival there), exactly what the matcher's branch 2 emits; p-1 is
// unchanged. next_time is FROZEN_S + 60 so the fake clock lands the glide
// midpoint (f = 0.5) at +30s, where the 862 polyline position (lat ~40.734,
// still on the long first segment) is far from the straight chord's midpoint
// (lat ~40.723): the e2e can therefore assert route-following, not just
// movement.
const pathAdvanced = () => ({
  fetched_at: FROZEN_S + 15,
  feed_timestamp: FROZEN_S + 10,
  served_at: FROZEN_S + 15,
  trains: [
    {
      id: "p-1", route_id: "862",
      latitude: 40.71271, longitude: -74.01193, stop_id: "26734",
      stop_name: "World Trade Center", direction: "To New York",
      prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 120,
      observed_at: FROZEN_S + 10, provenance: "placed",
    },
    {
      id: "p-2", route_id: "862",
      latitude: 40.71271, longitude: -74.01193, stop_id: "26734",
      stop_name: "World Trade Center", direction: "To New York",
      prev_lat: 40.73454, prev_lon: -74.16375, prev_time: FROZEN_S,
      next_time: FROZEN_S + 60,
      observed_at: FROZEN_S + 10, provenance: "placed",
    },
  ],
});

// PATH station arrivals for WTC: both directional buckets present. The first
// To New York arrival is at +90s, the same countdown-tick boundary the subway
// fixture uses ("2 min" on open, "1 min" one second later).
//
// Each row carries its OWN trip's clock (PATH dates every trip update), so the two
// differ; the envelope's content clock is the oldest of them, and PATH has no systems.
const pathArrivals = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 38,
  stop_id: "26734",
  stop_name: "World Trade Center",
  directions: {
    // Rows are {route_id, arrival} plus the contract pair: the bridge hash reaches no
    // payload (PathArrival dropped trip_id in the 13d cleanup).
    "To New Jersey": [{ route_id: "862", arrival: FROZEN_S + 300, ...reported(FROZEN_S - 38) }],
    "To New York": [{ route_id: "859", arrival: FROZEN_S + 90, ...reported(FROZEN_S - 18) }],
  },
  served_at: FROZEN_S,
  systems: null,
});

// NYC Ferry static layer (14a). Two docks: Wall St/Pier 11 (accessible, first so
// ferryDocks.getLayers()[0] is a deterministic click target) and South
// Williamsburg (not accessible, so the wheelchair-marker branch is exercised both
// ways). Two routes, each with one modal polyline.
// routes: the H5 routes-per-station index the backend derives from stop_times.
// Wall St/Pier 11 is served by ER, SB, and SV (Soundview); South Williamsburg by
// ER only. SV has NO boat and NO arrival in these fixtures on purpose: it appears
// ONLY in this static list, so a route-scoped SV alert rendering at the dock proves
// the join reads the static index and not the arrivals (test 26). ER and SB also
// appear in the dock's arrivals; SV is the one that isolates the static path.
const ferryStops = () => [
  { id: "18", name: "Wall St/Pier 11", lat: 40.70355, lon: -74.00512, wheelchair: true,
    routes: ["ER", "SB", "SV"] },
  { id: "2", name: "South Williamsburg", lat: 40.70951, lon: -73.96769, wheelchair: false,
    routes: ["ER"] },
];

const ferryRoutes = () => [
  { id: "ER", name: "East River", color: "00839c", text_color: "ffffff",
    shape: [[[40.70951, -73.96769], [40.70355, -74.00512]]] },
  { id: "SB", name: "South Brooklyn", color: "ffd100", text_color: "000000",
    shape: [[[40.70355, -74.00512], [40.68, -74.02]]] },
];

// A ferry realtime envelope: the `boats` key (not `data`), 5s-fresh like envelope().
const ferryEnvelope = (boats, fetchedAt = FROZEN_S, servedAt = fetchedAt) => ({
  fetched_at: fetchedAt,
  feed_timestamp: fetchedAt - 5,
  served_at: servedAt,
  boats,
});

// Three boats spanning the render states: an under-way route boat (active), a
// STOPPED_AT boat (docked/dimmed), and a null-route boat (Unassigned, neutral).
// No bearing field (14b omits it). Stable ids so the next poll keys by id.
// Each boat's observed_at is its updated_at: the backend serves one vehicle.timestamp
// under both names (6.1), and a GPS boat is `reported`.
const boat = (row) => ({ ...row, observed_at: row.updated_at, provenance: "reported" });

const ferry = () =>
  ferryEnvelope([
    boat({ id: "H1", label: "H201", trip_id: "t-er-1", route_id: "ER",
      latitude: 40.706, longitude: -73.99, speed: 6.5, status: "IN_TRANSIT_TO", updated_at: FROZEN_S - 3 }),
    boat({ id: "H2", label: "H202", trip_id: "t-sb-1", route_id: "SB",
      latitude: 40.70355, longitude: -74.00512, speed: 0.0, status: "STOPPED_AT", updated_at: FROZEN_S - 2 }),
    boat({ id: "H3", label: "H099", trip_id: "t-x-1", route_id: null,
      latitude: 40.69, longitude: -73.98, speed: 4.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S - 4 }),
  ]);

// The NEXT poll: H1 moved to a new position (same id -> the same marker moves,
// id-keyed diffing); H2 and H3 are unchanged.
const ferryMoved = () =>
  ferryEnvelope(
    [
      boat({ id: "H1", label: "H201", trip_id: "t-er-1", route_id: "ER",
        latitude: 40.708, longitude: -73.985, speed: 7.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S + 12 }),
      boat({ id: "H2", label: "H202", trip_id: "t-sb-1", route_id: "SB",
        latitude: 40.70355, longitude: -74.00512, speed: 0.0, status: "STOPPED_AT", updated_at: FROZEN_S + 10 }),
      boat({ id: "H3", label: "H099", trip_id: "t-x-1", route_id: null,
        latitude: 40.69, longitude: -73.98, speed: 4.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S + 11 }),
    ],
    FROZEN_S + 15,
  );

// A later poll where H1 has DOCKED (IN_TRANSIT_TO -> STOPPED_AT at Wall St/Pier 11):
// exercises the cross-poll re-icon branch (ferry-active -> ferry-docked, keyed on the
// changed icon state) and, with a boat popup held open, the record.latest refresh
// (getPopup().update()). H2/H3 are unchanged so only H1 re-icons.
const ferryDocked = () =>
  ferryEnvelope(
    [
      boat({ id: "H1", label: "H201", trip_id: "t-er-1", route_id: "ER",
        latitude: 40.70355, longitude: -74.00512, speed: 0.0, status: "STOPPED_AT", updated_at: FROZEN_S + 12 }),
      boat({ id: "H2", label: "H202", trip_id: "t-sb-1", route_id: "SB",
        latitude: 40.70355, longitude: -74.00512, speed: 0.0, status: "STOPPED_AT", updated_at: FROZEN_S + 10 }),
      boat({ id: "H3", label: "H099", trip_id: "t-x-1", route_id: null,
        latitude: 40.69, longitude: -73.98, speed: 4.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S + 11 }),
    ],
    FROZEN_S + 15,
  );

// Ferry dock arrivals for Wall St/Pier 11: two route-name buckets. East River is a
// normal arriving boat (+90s -> "2 min"); South Brooklyn is a DWELLING boat
// (arrival 30s past, departure +90s ahead), so its row renders "departs 2 min".
// Dock rows are dated by the TripUpdates header, never by the boat clock.
const ferryArrivals = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  stop_id: "18",
  stop_name: "Wall St/Pier 11",
  routes: {
    "East River": [
      { route_id: "ER", trip_id: "t-er-1", arrival: FROZEN_S + 90, departure: FROZEN_S + 120, ...reported(FROZEN_S - 5) },
    ],
    "South Brooklyn": [
      { route_id: "SB", trip_id: "t-sb-1", arrival: FROZEN_S - 30, departure: FROZEN_S + 90, ...reported(FROZEN_S - 5) },
    ],
  },
  served_at: FROZEN_S,
  systems: null,
});

// Service alerts default to an EMPTY list so every existing scenario's popup
// expectations are untouched by the new /api/alerts fetch. The alerts scenario
// overrides this per-test with a fixture that matches the station under test.
const alerts = () => ({ fetched_at: FROZEN_S, served_at: FROZEN_S, alerts: [] });

// Two ferry service alerts for the ferry alert-join test: one STOP-scoped (dock
// "18", Wall St/Pier 11) and one ROUTE-scoped (route "ER", East River). Dock 18 is
// served by route ER (its ferryStops routes list), so its popup renders BOTH alerts
// (H5 union); the route one also appears on an ER boat's popup. Selectors use the
// same ferry stop/route id space as ferryStops()/ferryRoutes() above and ferry().
const ferryAlerts = () => ({
  fetched_at: FROZEN_S,
  alerts: [
    { id: "ferry-stop", system: "ferry", header: "Wall St/Pier 11 landing closed", description: null,
      effect: "NO_SERVICE", cause: "MAINTENANCE", routes: [], stops: ["18"],
      starts_at: FROZEN_S - 600, ends_at: null },
    { id: "ferry-route", system: "ferry", header: "East River route reroute", description: null,
      effect: "DETOUR", cause: "CONSTRUCTION", routes: ["ER"], stops: [],
      starts_at: FROZEN_S - 600, ends_at: null },
    // Scoped to SV (Soundview), which serves dock 18 in its static routes list but
    // has no boat/arrival in these fixtures: this alert can reach the dock ONLY
    // through the routes-per-station index, isolating it from any arrivals match.
    { id: "ferry-route-sv", system: "ferry", header: "Soundview route suspended", description: null,
      effect: "NO_SERVICE", cause: "MAINTENANCE", routes: ["SV"], stops: [],
      starts_at: FROZEN_S - 600, ends_at: null },
  ],
});

// F11: station-scoped and route-scoped alerts, for the specs that assert the station
// PANEL and the map POPUP say the same thing about the same station. Selectors are in
// each system's own id space and line up with the station fixtures above.
//
// "sub-route-3" IS THE ACCEPTANCE CASE. Route 3 serves Times Sq (127) in its
// routes-per-station list and has NO imminent train in subwayArrivals, which only
// carries 1s and 2s. It can therefore reach the station only through the static side
// of the union, and F11's acceptance is that BOTH surfaces show it anyway.
//
// "njt-route-9" IS THE FLAT-SHAPE CASE. Route 9 does NOT serve Hoboken (id 12) in its
// static routes list (2 and 17), so it reaches that station only through a route id
// read out of a FLAT arrivals board, which is the shape the join could not read
// before F11. njtArrivalsHoboken serves exactly that board.
const stationAlertList = () => [
  { id: "sub-stop", system: "subway", header: "Times Sq-42 St is closed", description: null,
    effect: "NO_SERVICE", cause: "MAINTENANCE", routes: [], stops: ["127"],
    starts_at: FROZEN_S - 600, ends_at: null },
  { id: "sub-route-3", system: "subway", header: "[3] suspended overnight", description: null,
    effect: "NO_SERVICE", cause: "MAINTENANCE", routes: ["3"], stops: [],
    starts_at: FROZEN_S - 600, ends_at: null },
  { id: "sub-route-Z", system: "subway", header: "[Z] does not serve Times Sq", description: null,
    effect: "DETOUR", cause: "CONSTRUCTION", routes: ["Z"], stops: [],
    starts_at: FROZEN_S - 600, ends_at: null },
  { id: "njt-stop", system: "njt", header: "New York Penn Station platforms closed", description: null,
    effect: "NO_SERVICE", cause: "MAINTENANCE", routes: [], stops: ["109"],
    starts_at: FROZEN_S - 600, ends_at: null },
  { id: "njt-route-9", system: "njt", header: "[9] Northeast Corridor suspended", description: null,
    effect: "NO_SERVICE", cause: "MAINTENANCE", routes: ["9"], stops: [],
    starts_at: FROZEN_S - 600, ends_at: null },
];

// A flat NJ Transit board for HOBOKEN carrying a route 9 train. Hoboken's static
// routes are 2 and 17, so route 9 is present here and nowhere else: an alert scoped
// to it reaches this station only if the join reads the flat arrivals list.
const njtArrivalsHoboken = () => ({
  fetched_at: FROZEN_S,
  feed_timestamp: FROZEN_S - 5,
  stop_id: "12",
  stop_name: "Hoboken",
  arrivals: [
    {
      train_num: "3901", route_id: "9", headsign: "New York Penn Station",
      arrival: FROZEN_S + 120, departure: FROZEN_S + 150, delay: null, trip_id: "NJ_3901",
      ...reported(FROZEN_S - 5),
    },
  ],
  served_at: FROZEN_S,
  systems: { njt: systemBlock(FROZEN_S) },
});

module.exports = {
  FROZEN_MS,
  FROZEN_S,
  envelope,
  systemBlock,
  positionSteps,
  subwaysWithSystems,
  railroadsWithSystems,
  alertsWithSystems,
  ALERT_SYSTEMS,
  buses,
  subways,
  railroads,
  subwayStops,
  subwayRoutes,
  railroadStops,
  railroadRoutes,
  subwayArrivals,
  railroadArrivals,
  railroadArrivalsLirr,
  reported,
  busRoute,
  airtrain,
  pathStops,
  pathRoutes,
  path,
  pathAdvanced,
  pathArrivals,
  ferryStops,
  ferryRoutes,
  ferryEnvelope,
  ferry,
  ferryMoved,
  ferryDocked,
  ferryArrivals,
  njtStops,
  njtRoutes,
  njtWithSystems,
  njt,
  njtArrivals,
  alerts,
  ferryAlerts,
  stationAlertList,
  njtArrivalsHoboken,
};
