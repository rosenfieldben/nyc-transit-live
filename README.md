# NYC Transit Live

A live map of NYC subways, buses, and commuter rail (LIRR + Metro-North), built
on the MTA's public real-time feeds.
Buses report true GPS positions and move on the map; subways are placed at their
next station using real-time arrival data joined against the static schedule,
then glide between stations as time passes, following the actual route geometry.
Each train's previous and next station are projected onto the route shape, and
the marker walks the arc between the two offsets, parameterized by time; a train
that does not project cleanly onto its route shape falls back to a straight line
between the two stations. The feeds usually prune the just-departed stop, so the
backend carries each train's previous-poll station forward across polls as that
anchor, letting trains glide even when the feed omits where they came from.

## How it works

A small FastAPI backend polls the MTA's GTFS-Realtime feeds every ~20 seconds,
decodes the protobuf, and exposes clean JSON. A Leaflet frontend polls that JSON
and draws/moves markers. The backend does the polling once and serves many
browser clients, so the MTA endpoints aren't hit on every page refresh.

A visitor who lands during a backend cold start still gets a full map without
reloading: the static loaders (route lines, station dots, AirTrain) retry with
doubling backoff (1s up to 30s) until they populate, matching the backend's
warmup semantics (a warming group 503s; a failed group serves an empty payload
under no-cache while its server-side retry heals it, so an empty 200 means "ask
again later", never success). Each loader stops for good once it has populated.

Clicking a subway station marker shows the upcoming trains in each direction
with live countdowns, and any active service alerts affecting that station in a
quiet block above the countdowns (the railroad station popups do the same). The
alerts come from the `/api/alerts` store; an alert applies to a station when it
selects that station's stop id, or a route currently arriving there, within the
same system (numeric ids collide across modes, so the join is system-scoped).
Clicking a bus, a subway train, or a railroad train shows the alerts for that
vehicle's route the same way, and agency-wide alerts (which name no route and no
stop) appear in a dismissible banner over the map rather than in any one popup.
Alerts are decorative: a failed or stale alerts fetch never blocks the arrivals.
Route-line severity styling is deferred: the MTA stamps `UNKNOWN_EFFECT` on live
alerts, so a real severity signal needs a future backend phase to decode the
Mercury extension.
The same subway poll that places trains also builds a per-station arrivals index
in memory (the stops a train placement discards are exactly those arrival times),
so a click is served from memory without hitting the MTA. The endpoints involved:

- `GET /api/subway-stops` — station markers `[{id, name, lat, lon}]`, static
  for the session (cached by the browser).
- `GET /api/subway-arrivals/{station_id}` — `{fetched_at, station_id,
  station_name, directions: {Northbound, Southbound}}` from the in-memory index,
  refreshed each poll; the frontend ticks the countdowns down between polls.

The LIRR and Metro-North get the same treatment, built during the railroad poll
into a per-system in-memory index (`railroad_stops` and `railroad_arrivals`
namespaces are independent, so the arrivals endpoint is keyed by system):

- `GET /api/railroad-stops`: station markers `[{system, id, name, lat, lon}]`,
  static for the session; a system whose static GTFS did not load contributes
  nothing (empty list, not an error, when none loaded).
- `GET /api/railroad-arrivals/{system}/{stop_id}`: `{fetched_at, system,
  stop_id, stop_name, directions}` for `system` in `{LIRR, MNR}`. The direction
  buckets are asymmetric: LIRR reads `Outbound`/`Inbound` straight from the
  realtime `direction_id`. Metro-North omits `direction_id`, so its direction is
  INFERRED per trip from whether its stop sequence moves toward or away from an
  NYC anchor (Grand Central): a heuristic from stop progression, not feed data.
  `Trains` is the residual bucket for trips whose direction could be neither read
  nor inferred (a near-tie or a single-resolvable-stop stub). `directions` carries
  only the buckets that have upcoming trains, so a station shows some subset of
  those keys (an empty object means nothing upcoming). Unlike the marker layer,
  this index INCLUDES the GPS-tracked trains: a positioned train still stops at
  stations, so omitting it would hide exactly the best-tracked trains. Each
  railroad arrival also carries a `train_num` (the rider-facing train number, null
  when no vehicle entity joins), and `/api/railroad-routes` supplies each route's
  rider-facing name (e.g. "Babylon Branch") for the popups.

AirTrain JFK is the exception: the Port Authority publishes no real-time feed for
it, so this layer is scheduled reference data by design, not a degraded live mode.
It ships as one committed fixture and never shows train positions or a live
countdown.

- `GET /api/airtrain`: the whole static dataset `{stations, routes}` in one
  response; each route carries its ordered guideway `polyline`, the `stations` it
  serves, and non-overlapping scheduled `headways`. The frontend draws it as its
  own toggleable layer, and a station popup shows each serving branch's scheduled
  headway for the current New York time, labeled "(scheduled)".

Service alerts are polled on their own slower loop and served from an in-memory
index (there is no map surface for them yet; that is a later phase):

- `GET /api/alerts`: `{fetched_at, alerts: [...]}`, one entry per alert active now
  across the keyless subway/bus/LIRR/MNR alert feeds: `{id, system, header,
  description, effect, cause, routes, stops, starts_at, ends_at}`. `routes`/`stops`
  are the deduped selectors from the alert's informed_entity list (subway stop
  selectors are parent-station ids, the same id space as `/api/subway-stops`);
  `ends_at` is null for an open-ended alert. Only alerts active NOW are included;
  not-yet-active planned work is held back and counted in `/api/status`.

```
nyc-transit-live/
├── backend/
│   ├── main.py              # FastAPI app + JSON endpoints, serves the frontend
│   ├── feeds.py             # fetch + decode GTFS-RT protobuf (buses + subways + railroads)
│   ├── static_data.py       # load stop coords / route shapes from static GTFS
│   ├── bus_static.py        # background-built on-disk index of bus route shapes
│   ├── airtrain_static.py   # load the committed AirTrain JFK fixture (no network)
│   ├── scripts/             # one-off generators (gen_airtrain_fixture.py)
│   ├── tests/               # pytest suite (run from backend/)
│   ├── requirements.txt     # lower-bound deps for local dev
│   ├── requirements.lock    # pinned deps installed by Railway and CI
│   └── requirements-dev.txt # the lock + test-only extras
├── frontend/
│   ├── index.html
│   ├── map.js               # Leaflet map, polls backend, draws markers
│   ├── helpers.js           # pure helpers shared with map.js (node-testable)
│   ├── helpers.test.js      # node --test suite for the helpers
│   └── style.css
├── tests/e2e/               # hermetic Playwright smoke suite (dev/test only)
│   ├── smoke.spec.js        # the scenarios; all network intercepted
│   ├── mock.js              # /api/* fixtures + vendored Leaflet interception
│   ├── serve.js             # tiny static server for frontend/ (no backend)
│   ├── playwright.config.js # chromium only, starts the static server
│   └── fixtures/            # handcrafted JSON payloads + vendored leaflet dist
├── data/
│   ├── airtrain_jfk.json    # committed AirTrain JFK fixture (geometry + scheduled headways)
│   ├── gtfs_static/         # downloaded static subway GTFS (gitignored)
│   └── cache/bus_routes/    # background-built bus route index (gitignored)
├── .github/workflows/ci.yml # backend pytest + frontend node tests + e2e smoke
├── package.json             # dev-only: @playwright/test (the app is buildless)
├── railway.json             # Railway start command + healthcheck
├── nixpacks.toml            # pins Python 3.12 for the Railway build
├── requirements.txt         # root pointer -> backend/requirements.lock
└── .env                     # BUS_TIME_API_KEY (gitignored)
```

## Setup

1. **Get a Bus Time API key.** Register (free) at the MTA developer site for an
   MTA Bus Time key. The subway feeds do *not* require a key; the bus feed does.
2. **Add your key.** Copy `.env.example` to `.env` and paste your key in.
3. **Install backend deps.**
   ```bash
   cd backend
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
   Deploys (Railway, via the root `requirements.txt`) install the pinned
   `backend/requirements.lock` instead; regeneration instructions are in the
   lock file's header.
4. **Run it.**
   ```bash
   uvicorn main:app --reload
   ```
   Then open http://localhost:8000.
5. **Run the tests** (optional).
   ```bash
   pip install -r requirements-dev.txt   # from backend/
   pytest
   node --test "frontend/*.test.js"      # from the repo root
   ```

### End-to-end smoke suite (Playwright)

A small hermetic Playwright suite exercises the real frontend in chromium: map
boot, the empty-feed grace behavior, a failed poll, the station arrivals popup,
the click-supersession race, layer toggles, and the bus route line. Run it from
the repo root:

```bash
npm ci                          # dev-only deps (the app itself has no build step)
npx playwright install chromium # one-time browser download
npx playwright test --config tests/e2e/playwright.config.js
```

It is **hermetic by design**: the config starts a tiny static server for
`frontend/` (the Python backend is never launched), and every request is
intercepted in the browser. All `/api/*` calls are answered from the handcrafted
fixtures in `tests/e2e/fixtures/`, and the two unpkg Leaflet URLs are fulfilled
from byte-identical vendored copies of `leaflet@1.9.4` under
`tests/e2e/fixtures/vendor/` (serving the exact bytes keeps the SRI `integrity`
attributes in `index.html` valid). Nothing leaves the machine, so CI needs no
network at test time. Time is frozen with Playwright's clock control, so the
arrival countdowns and the staleness window are deterministic (no sleeps).

## Data sources

- **Buses** — MTA Bus Time `VehiclePositions` feed (requires key). Real lat/lon.
- **Subways** — MTA keyless GTFS-RT feeds, grouped by line (ACE, BDFM, numbered
  lines, etc.). These carry trip/arrival updates, not GPS, so trains are shown
  at their next station.
- **Commuter rail** — MTA keyless GTFS-RT feeds for the LIRR and Metro-North.
  These do report real GPS, so phase 1 shows the trains that carry a vehicle
  position at their true lat/lon; trains without a position are omitted (station
  placement from the trip updates is a planned phase 2).
- **Static GTFS** — stop coordinates and route shapes, downloaded into
  `data/gtfs_static/` and loaded into memory by background warmup tasks (subway
  and railroad, each independent), off the startup critical path. A group's load
  retries automatically on failure, so a degraded network at boot self-heals
  rather than stranding the map until the next deploy.
- **AirTrain JFK**: 511NY open-data static GTFS, with no real-time feed. Committed
  once as `data/airtrain_jfk.json` and never fetched at runtime, so this layer is
  scheduled reference data, not a live mode. See the regeneration note below.

All feeds are free to use. Data is GTFS-Realtime (protobuf), decoded server-side.

### AirTrain JFK (scheduled reference data, no live feed)

The Port Authority publishes no GTFS-Realtime for AirTrain JFK, so this layer is
scheduled reference data by design, not a degraded live mode. It ships as one
committed fixture and the UI never fakes a countdown: station popups show the
scheduled headway for the current New York time, labeled "(scheduled)".

Regenerate only if 511NY refreshes the source feed. Its `calendar.txt` expired
2021-12-31, so the feed is stale as a schedule authority and the geometry and
headways rarely change; regeneration matters only when 511NY publishes a new zip.
To regenerate:

```bash
python backend/scripts/gen_airtrain_fixture.py   # downloads the 511NY zip, writes data/airtrain_jfk.json
```

The script prints a per-route headway table. Eyeball it against the Port
Authority's published AirTrain frequencies before committing, and do not silently
adjust a mismatch. A backend test asserts the committed fixture has exactly 10
stations and 3 routes, so a regeneration that drifts those counts fails loudly in
CI. Overlapping frequency bands (an all-day base under narrower daytime bands) are
reconciled as base-plus-override, where the most frequent covering band wins rather
than being summed as concurrent patterns; see the `reconcile_bands` comment in
`backend/scripts/gen_airtrain_fixture.py`.

## Scaling

The deploy must run a **single uvicorn worker** (the default; no `--workers`
flag). `bus_static` keeps its index status and partial flag as per-process
state: with multiple workers, each would download and build the bus route
index independently, and a worker whose build partially failed would 404
routes that another worker indexed fine. Route geometry itself is read from
the shared on-disk cache, so data wouldn't corrupt — but going multi-worker
would need a file lock around the index build (so one worker builds while
the others wait) and workers re-reading the manifest instead of trusting
their own build result. The static-GTFS warmups (subway, railroad) keep their
loaded tables and their loading/ready/failed status in per-process memory too,
so the same single-worker assumption applies; the on-disk zips are shared and
downloaded last-writer-wins.

## Monitoring

`GET /api/status` returns an always-200 operational snapshot: per-feed cache
freshness — both `age_s` (since this server last polled) and `feed_age_s` (how
stale the feed's own content was at poll time) — the last recorded poll error
if any, the bus route index state, the static subway GTFS age, and each static
group's warmup state (`subway_static` / `railroad_static`: loading, ready, or
failed-and-retrying). The `alerts` entry reports the alert poll's `age_s`, its
last error if any, the `active` alert count in the index, and `suppressed_planned`
(not-yet-active planned work the last poll held back), so upcoming service work is
visible even though it is excluded from `/api/alerts`.

`GET /healthz` is the readiness probe (Railway's healthcheck points here). It
returns 503 when the app can't serve fresh data: no feed is fresh, the bus route
index build has failed, or the subway static load has failed (and is retrying).
It stays healthy as long as **at least one** feed is fresh, so a misconfigured
key (which only stops the bus feed) doesn't take down an otherwise-working subway
map. A still-**loading** static group or bus index during cold start does not
flap it (the load runs in the background, off the healthcheck critical path);
only the failed states, which retry, degrade it until a retry succeeds. Railroad
static failure is deliberately lenient (that system degrades to GPS-only) rather
than a healthz reason.

While a static group is still loading, its decorative endpoints
(`/api/subway-stops`, `/api/subway-routes`, `/api/railroad-stops`,
`/api/railroad-routes`) return 503 rather than an empty list, so a browser never
caches an empty payload for the hour-long `max-age` during a cold start; a failed
group serves `[]` with `no-cache` so a later retry is picked up.

The feed envelopes (`/api/buses`, `/api/subways`, `/api/railroads`) carry
`fetched_at` (this server's poll time) and `feed_timestamp` (the feed's own
content time: oldest across the subway feeds for `/api/subways`). For
`/api/railroads`, `feed_timestamp` reflects LIRR's feed-generation time; MNR
publishes a lagging shared header clock that does not track publish time (it is
copied onto every vehicle too, while the GPS positions are live), so it is not
used as a freshness signal. The frontend judges staleness from the difference of
those two server-side values, so the browser clock never causes false "stale"
warnings.

## Build phases

- [x] **1. Backend proves data flows** — `/api/buses` returns live JSON.
- [x] **2. Minimal map** — Leaflet map plots buses, polling every 15s.
- [x] **3. Readable markers** — bearing rotation, route colors, popups, failure handling.
- [x] **4. Subways** — `/api/subways`, trains placed at next station via static GTFS.
- [x] **5. Route lines** — draw `shapes.txt` route geometry under the markers.
- [x] **6. Train motion (v1)** — trains glide between stations via straight-line
  interpolation, animated client-side between polls; the previous-station anchor
  is carried forward across polls so trains glide even when the feed prunes the
  just-departed stop.
- [x] **7. Train motion (v2)**: trains follow the actual route geometry between
  stations. Each train's previous and next station are projected onto the route
  shape and the marker walks the arc between the two offsets, parameterized by
  time, with a monotonic clamp so a dwelling train cannot slide backward. A train
  that does not project cleanly onto its route shape (off-shape stations, an
  implausibly long slice, or an unindexed route) falls back to the v1 straight
  line.
- [x] **8. Commuter rail (GPS)**: `/api/railroads` serves the LIRR and
  Metro-North trains that report a vehicle position, drawn as a toggleable layer
  of square markers at their real lat/lon.
- [x] **9. Commuter rail (station placement)**: the position-less railroad trains
  the GPS slice omits are placed at their next station from the trip updates (the
  way subways are placed), joining the static railroad GTFS for coordinates and
  taking direction from the realtime direction_id, or, for a trip that omits it
  (MNR), from the same stop-progression inference the arrivals use (a heuristic,
  null when neither applies). They render as hollow squares (a scheduled estimate)
  vs the filled GPS squares. Static placement only; the time anchors (next_time /
  prev_*) are filled but motion is the next increment.
- [x] **10. Commuter rail (gliding)**: the schedule-placed LIRR + Metro-North
  trains glide between stations along the route shape, the way subway v2 does.
  Route geometry is built per route from the static trips/shapes and associated
  to a train by route_id plus coordinate projection, never the realtime trip_id
  (which MNR does not join to its static schedule), so one approach serves both
  systems. GPS trains keep moving by their reported position; only the placed
  trains glide.
- [x] **11. Commuter rail (station arrivals)**: clickable LIRR + Metro-North
  station markers with live countdowns, the way subway stations work. The
  railroad poll builds a per-system in-memory arrivals index (`/api/railroad-stops`
  and `/api/railroad-arrivals/{system}/{stop_id}`); the popup renders whichever
  direction buckets a station carries, labeled with the rider-facing route name
  (e.g. "Babylon Branch") from routes.txt. LIRR reads Inbound/Outbound from the
  realtime direction_id; Metro-North omits it, so its direction is inferred per
  trip from the stop progression toward an NYC anchor, with a residual Trains
  bucket for the ambiguous cases. GPS-tracked trains are included in arrivals even
  though the marker layer draws them from their live position.
- [x] **12. AirTrain JFK (static layer)**: a scheduled-reference-only layer for
  AirTrain JFK (no realtime feed exists), served from a committed fixture via
  `/api/airtrain` and drawn as its own toggleable layer with scheduled headways.
  See the AirTrain JFK section above.
- [x] **12a. Service alerts (backend)**: the backend polls the keyless
  subway/bus/LIRR/MNR GTFS-RT alert feeds on a slower 60s loop, keeps an in-memory
  index of alerts active now (not-yet-active planned work is held back and counted
  for `/api/status`), and serves them from `/api/alerts`. `/api/status` reports the
  alert feed's health; `/healthz` ignores it (decorative). Map surfaces are 12b/12c.
- [x] **12b. Service alerts in station popups (frontend)**: the frontend polls
  `/api/alerts` on its own 60s loop and shows the alerts affecting a clicked station
  in a quiet block above the arrival countdowns, in both the subway and railroad
  popups. An alert applies when it selects the station's stop id, or a route in its
  current arrivals, within the same system (the match is system-scoped because
  numeric route/stop ids collide across modes). Header text only; alerts are
  decorative, so a failed fetch keeps the last-known set silently and never blocks
  arrivals. Map banner and systemwide/bus alerts are 12c.
- [x] **12c. Service alerts on vehicles + systemwide banner (frontend)**: the same
  alerts store now feeds the bus, subway-train, and railroad-train popups (matched
  by the vehicle's route, system-scoped), and agency-wide alerts (no route and no
  stop selectors) surface in a dismissible banner over the map. Dismissal is per
  alert id for the session, so clearing a standing incident does not suppress the
  next, distinct one. Route-line severity styling stays deferred until a backend
  phase decodes the MTA Mercury extension (live alerts all report `UNKNOWN_EFFECT`).
- [x] **12d. Static loaders retry until they populate (frontend)**: the five static
  loaders (subway routes/stations, railroad routes/stations, AirTrain) retry with
  doubling backoff (1s capped at 30s) until they have populated their layer, so a
  visitor who lands during a backend cold start gets a map that fills in on its
  own once the static GTFS warms. An empty 200 counts as failure, matching the
  backend's failed-warmup no-cache semantics; a non-empty railroad payload counts
  as success even if one system is missing, because the backend's lenient
  per-system warmup settles that state and frontend retries cannot improve it.
  Live-data polling already self-healed and is untouched.

## Notes

- The MTA's logos, official map, and route symbols require a license. Use your
  own colors and markers rather than official MTA branding.
- Cache the static GTFS in memory on startup — it's large; don't reload per request.
- Phase 4 (subways) is the hard part: joining realtime `trip_id`s to physical
  stations involves fiddly matching against the static schedule, and the subway
  feeds use NYC-specific protobuf extensions. Expect to iterate.

## License

Personal project. MTA data is subject to the MTA's terms and conditions.
