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
};
