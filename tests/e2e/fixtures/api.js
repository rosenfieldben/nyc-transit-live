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

const FROZEN_MS = Date.UTC(2026, 6, 2, 12, 0, 0); // 2026-07-02T12:00:00Z
const FROZEN_S = FROZEN_MS / 1000;

// A successful feed envelope: content 5s old at poll time (fresh).
const envelope = (data, fetchedAt = FROZEN_S) => ({
  fetched_at: fetchedAt,
  feed_timestamp: fetchedAt - 5,
  data,
});

const buses = () =>
  envelope([
    { id: "MTA NYCT_101", route_id: "M15", latitude: 40.72, longitude: -73.98, bearing: 90.0 },
    { id: "MTA NYCT_102", route_id: "B46", latitude: 40.68, longitude: -73.94, bearing: null },
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
    },
  ]);

// One GPS train (real position, null station/anchor fields) and one placed train
// (at its next station, anchors filled), exactly as the two decode paths emit.
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
      prev_lat: 40.69,
      prev_lon: -73.79,
      prev_time: FROZEN_S - 120,
      next_time: FROZEN_S + 180,
    },
  ]);

const subwayStops = () => [
  { id: "127", name: "Times Sq-42 St", lat: 40.7554, lon: -73.9874 },
  { id: "A31", name: "Canal St", lat: 40.7227, lon: -74.0057 },
];

const subwayRoutes = () => [
  { route: "1", polylines: [[[40.74, -73.99], [40.75, -73.99], [40.76, -73.98]]] },
  { route: "A", polylines: [[[40.71, -74.01], [40.72, -74.0]]] },
];

const railroadStops = () => [
  { system: "LIRR", id: "12", name: "Jamaica", lat: 40.7005, lon: -73.8095 },
  { system: "MNR", id: "1", name: "Grand Central", lat: 40.7527, lon: -73.9772 },
];

const railroadRoutes = () => [
  { system: "LIRR", route: "1", name: "Babylon Branch", polylines: [[[40.7, -73.8], [40.69, -73.6]]] },
  { system: "MNR", route: "1", name: "Hudson", polylines: [[[40.9, -73.78], [41.0, -73.86]]] },
];

// Subway station arrivals. The first Northbound arrival is at +90s so the popup
// reads "2 min" on open and "1 min" one second later (the countdown-tick test).
const subwayArrivals = () => ({
  fetched_at: FROZEN_S,
  station_id: "127",
  station_name: "Times Sq-42 St",
  directions: {
    Northbound: [
      { route_id: "1", trip_id: "sub-1", arrival: FROZEN_S + 90 },
      { route_id: "2", trip_id: "sub-3", arrival: FROZEN_S + 300 },
    ],
    Southbound: [{ route_id: "1", trip_id: "sub-2", arrival: FROZEN_S + 180 }],
  },
});

// Railroad (MNR) station arrivals. MNR omits direction_id, so the backend INFERS
// Inbound/Outbound from the stop progression; both are directional buckets here
// (RailroadArrival carries train_num, which subway arrivals do not).
const railroadArrivals = () => ({
  fetched_at: FROZEN_S,
  system: "MNR",
  stop_id: "1",
  stop_name: "Grand Central",
  directions: {
    Inbound: [{ route_id: "1", trip_id: "mnr-3117769", arrival: FROZEN_S + 240, train_num: "795" }],
    Outbound: [{ route_id: "1", trip_id: "mnr-3117770", arrival: FROZEN_S + 360, train_num: "812" }],
  },
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
const pathStops = () => [
  { id: "26734", name: "World Trade Center", lat: 40.71271, lon: -74.01193 },
  { id: "26733", name: "Newark", lat: 40.73454, lon: -74.16375 },
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
  trains: [
    {
      id: "p-1", route_id: "862",
      latitude: 40.71271, longitude: -74.01193, stop_id: "26734",
      stop_name: "World Trade Center", direction: "To New York",
      prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 120,
    },
    {
      id: "p-2", route_id: "862",
      latitude: 40.73454, longitude: -74.16375, stop_id: "26733",
      stop_name: "Newark", direction: "To New York",
      prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 15,
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
  trains: [
    {
      id: "p-1", route_id: "862",
      latitude: 40.71271, longitude: -74.01193, stop_id: "26734",
      stop_name: "World Trade Center", direction: "To New York",
      prev_lat: null, prev_lon: null, prev_time: null, next_time: FROZEN_S + 120,
    },
    {
      id: "p-2", route_id: "862",
      latitude: 40.71271, longitude: -74.01193, stop_id: "26734",
      stop_name: "World Trade Center", direction: "To New York",
      prev_lat: 40.73454, prev_lon: -74.16375, prev_time: FROZEN_S,
      next_time: FROZEN_S + 60,
    },
  ],
});

// PATH station arrivals for WTC: both directional buckets present. The first
// To New York arrival is at +90s, the same countdown-tick boundary the subway
// fixture uses ("2 min" on open, "1 min" one second later).
const pathArrivals = () => ({
  fetched_at: FROZEN_S,
  stop_id: "26734",
  stop_name: "World Trade Center",
  directions: {
    // Rows are {route_id, arrival} only: the bridge hash reaches no payload
    // (PathArrival dropped trip_id in the 13d cleanup).
    "To New Jersey": [{ route_id: "862", arrival: FROZEN_S + 300 }],
    "To New York": [{ route_id: "859", arrival: FROZEN_S + 90 }],
  },
});

// NYC Ferry static layer (14a). Two docks: Wall St/Pier 11 (accessible, first so
// ferryDocks.getLayers()[0] is a deterministic click target) and South
// Williamsburg (not accessible, so the wheelchair-marker branch is exercised both
// ways). Two routes, each with one modal polyline.
const ferryStops = () => [
  { id: "18", name: "Wall St/Pier 11", lat: 40.70355, lon: -74.00512, wheelchair: true },
  { id: "2", name: "South Williamsburg", lat: 40.70951, lon: -73.96769, wheelchair: false },
];

const ferryRoutes = () => [
  { id: "ER", name: "East River", color: "00839c", text_color: "ffffff",
    shape: [[[40.70951, -73.96769], [40.70355, -74.00512]]] },
  { id: "SB", name: "South Brooklyn", color: "ffd100", text_color: "000000",
    shape: [[[40.70355, -74.00512], [40.68, -74.02]]] },
];

// A ferry realtime envelope: the `boats` key (not `data`), 5s-fresh like envelope().
const ferryEnvelope = (boats, fetchedAt = FROZEN_S) => ({
  fetched_at: fetchedAt,
  feed_timestamp: fetchedAt - 5,
  boats,
});

// Three boats spanning the render states: an under-way route boat (active), a
// STOPPED_AT boat (docked/dimmed), and a null-route boat (Unassigned, neutral).
// No bearing field (14b omits it). Stable ids so the next poll keys by id.
const ferry = () =>
  ferryEnvelope([
    { id: "H1", label: "H201", trip_id: "t-er-1", route_id: "ER",
      latitude: 40.706, longitude: -73.99, speed: 6.5, status: "IN_TRANSIT_TO", updated_at: FROZEN_S - 3 },
    { id: "H2", label: "H202", trip_id: "t-sb-1", route_id: "SB",
      latitude: 40.70355, longitude: -74.00512, speed: 0.0, status: "STOPPED_AT", updated_at: FROZEN_S - 2 },
    { id: "H3", label: "H099", trip_id: "t-x-1", route_id: null,
      latitude: 40.69, longitude: -73.98, speed: 4.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S - 4 },
  ]);

// The NEXT poll: H1 moved to a new position (same id -> the same marker moves,
// id-keyed diffing); H2 and H3 are unchanged.
const ferryMoved = () =>
  ferryEnvelope(
    [
      { id: "H1", label: "H201", trip_id: "t-er-1", route_id: "ER",
        latitude: 40.708, longitude: -73.985, speed: 7.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S + 12 },
      { id: "H2", label: "H202", trip_id: "t-sb-1", route_id: "SB",
        latitude: 40.70355, longitude: -74.00512, speed: 0.0, status: "STOPPED_AT", updated_at: FROZEN_S + 10 },
      { id: "H3", label: "H099", trip_id: "t-x-1", route_id: null,
        latitude: 40.69, longitude: -73.98, speed: 4.0, status: "IN_TRANSIT_TO", updated_at: FROZEN_S + 11 },
    ],
    FROZEN_S + 15,
  );

// Ferry dock arrivals for Wall St/Pier 11: two route-name buckets. East River is a
// normal arriving boat (+90s -> "2 min"); South Brooklyn is a DWELLING boat
// (arrival 30s past, departure +90s ahead), so its row renders "departs 2 min".
const ferryArrivals = () => ({
  fetched_at: FROZEN_S,
  stop_id: "18",
  stop_name: "Wall St/Pier 11",
  routes: {
    "East River": [{ route_id: "ER", trip_id: "t-er-1", arrival: FROZEN_S + 90, departure: FROZEN_S + 120 }],
    "South Brooklyn": [{ route_id: "SB", trip_id: "t-sb-1", arrival: FROZEN_S - 30, departure: FROZEN_S + 90 }],
  },
});

// Service alerts default to an EMPTY list so every existing scenario's popup
// expectations are untouched by the new /api/alerts fetch. The alerts scenario
// overrides this per-test with a fixture that matches the station under test.
const alerts = () => ({ fetched_at: FROZEN_S, alerts: [] });

module.exports = {
  FROZEN_MS,
  FROZEN_S,
  envelope,
  buses,
  subways,
  railroads,
  subwayStops,
  subwayRoutes,
  railroadStops,
  railroadRoutes,
  subwayArrivals,
  railroadArrivals,
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
  ferryArrivals,
  alerts,
};
